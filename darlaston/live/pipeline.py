"""LivePipeline -- frames in, LiveSignals out.

This module is the boundary described in ARCHITECTURE.md 4, and the rules are
not stylistic:

  * nothing here imports Qt
  * nothing here imports from ui/
  * no UI concept -- widgets, colours, layout, preferences -- appears here
  * the UI reaches into nothing but the signal stream

Held, swapping these guts for a compiled implementation later is a local change
behind a stable interface. Unheld, the pipeline smears across the widgets and
that door closes quietly.

Measured on a 16-core machine: a full loop including per-pixel peaking runs at
~34 fps, of which 0.0017 ms is the Python interpreter. The reason to keep the
boundary is not present-day speed; it is optionality.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from ..camera.buffers import Frame
from . import balance
from .cell import LatestFrame
from .coverage import FocusCoverage
from .focus import (DEFAULTS, FocusTrace, Illumination, Metric, Prefilter,
                    Region, measure, region_rect)
from .profile import Meter
from .tracker import StageTracker
from .turret import TurretDetector, TurretEvent, model_signatures

_log = logging.getLogger(__name__)


#: How many frames pass between instrument repaints. Shared with the UI's
#: own throttle rather than written twice, because the two have to agree:
#: computing the levels less often than they are drawn shows a stale
#: histogram, and more often is work nobody ever sees.
INSTRUMENT_DIVISOR = 3
#: Width the stage tracker correlates at, in pixels. The 2736 preview
#: divided by four lands here, and that grid is the one the tracker's own
#: measurements were taken on; larger previews are reduced further to meet
#: it rather than handing phase correlation more pixels than it can use.
#: Frames between turret-watch feeds. A human turning a turret takes
#: about a second and the detector integrates over it; ten samples a
#: second decide as well as thirty, at a third of the cost. Unlike the
#: tracker, nothing here integrates displacement, so a divisor loses
#: nothing silently -- the distinction the tracker's own do-not-divide
#: rule turns on.
TURRET_DIVISOR = 3

#: The stage tracker's correlation grid, as a target width. An eighth
#: of the 1824 preview. It was a quarter (456 wide) until the frame
#: budget was measured on a weak machine, where the correlation was the
#: largest single stage in the table; a quarter of the pixels takes the
#: FFT to well under half the cost (3.8 to 1.6 ms on the reference box,
#: and the DFT-friendly crop that was supposed to help instead lost to
#: its own copy at this size). Accuracy, measured through the real
#: pipeline at every speed: worst error moved from -0.4% of travel to
#: -0.7% at a crawl and under -0.45% everywhere else, still zero
#: gating -- and the relocalizer now abolishes exactly the accumulated
#: drift that difference produces, which is the backstop that makes the
#: trade safe. The frames the cut buys back feed the tracker itself.
TRACK_WIDTH = 228


@dataclass(frozen=True)
class LiveSignals:
    """Everything the UI is allowed to know about a frame."""

    seq: int
    timestamp: float
    preview: np.ndarray                      # owned copy, safe to keep
    histogram: np.ndarray
    clipped_fraction: float
    focus_metric: float
    focus_fraction_of_peak: float
    focus_trace: np.ndarray
    #: Per-channel fraction of pixels at 255 in the preview. Reported
    #: separately because they are not comparable: the ISP must boost blue
    #: about 3x to neutralise this sensor, so blue saturates roughly 4.7x
    #: earlier in raw terms than green does.
    channel_clipped: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Counts recomputations of `histogram`, not frames. The levels cost
    #: enough that they are taken every INSTRUMENT_DIVISOR frames and the
    #: same array is handed out in between, so a consumer acting on every
    #: frame acts several times on one measurement. Harmless for a display,
    #: which redraws the same thing; not harmless for a control loop, which
    #: then applies several steps' worth of correction per observation.
    #: That is dead time, and it oscillates the way dead time does:
    #: measured at 1448x exposure swing against 1.00x when the loop acts
    #: once per reading.
    levels_seq: int = 0
    #: Does this frame look like empty slide? Cheap to compute here and it
    #: lets the app notice a chance to bank a flat field without asking.
    looks_blank: bool = False
    #: Has the view been still for a moment? A raw grab freezes the preview
    #: for over a second, so it must only happen when nobody is moving.
    settled: bool = False
    xy_offset: tuple[float, float] | None = None
    xy_confidence: float = 0.0
    #: Integrated view position over the slide, in preview pixels, origin
    #: where tracking began. None until the tracker first locks. Navigation
    #: quality only: drift accumulates, blank glass is a gap -- the mosaic
    #: path uses this as a constraint for registration, never as registration.
    stage_pos: tuple[float, float] | None = None
    #: Did this frame's offset actually integrate? False over featureless
    #: ground, where the position is held rather than trusted.
    stage_tracking: bool = False
    #: Running count of measured-but-rejected tracker steps. The window
    #: turns a rising count into the "moving too fast" advisory.
    track_gated: int = 0
    #: The tracker's own downsample of this frame, grey uint8, shipped
    #: so the relocalizer's probe starts from work already paid for --
    #: its single-step thumbnail resize of the full preview measured
    #: 6.4 ms against 0.3 from here. A reference, not a copy: written
    #: once per frame by the analysis thread and read within the frame.
    track_small: np.ndarray | None = None
    #: Which tracking origin `stage_pos` was measured under. Bumped by
    #: every reset. A consumer that clears on reset must refuse frames
    #: stamped with an older generation: they were measured before the
    #: reset landed and their positions describe an origin that no longer
    #: exists -- delivered late, they repaint cleared ground.
    track_gen: int = 0
    peaking: np.ndarray | None = None
    #: Normalised (x, y, w, h) of the region the metric was taken from, so the
    #: view can show what is actually being measured.
    focus_rect: tuple[float, float, float, float] | None = None
    #: Fraction of the structured area that has been through focus, and a mask
    #: of what has not. None unless a sweep is running.
    coverage: float | None = None
    coverage_remaining: np.ndarray | None = None
    #: Complete means covered *and* no longer finding new structure.
    coverage_complete: bool = False
    #: The pooled sharpness field itself, at reduced scale, when a sweep is
    #: running. The Z-stack trigger reads focus motion from it: racking the
    #: fine focus changes the image without translating it, so the xy-based
    #: stillness flag cannot see a pause in racking -- but the sharpness
    #: field reshapes as the focal plane moves, and stops reshaping when the
    #: hand stops.
    sharpness_field: np.ndarray | None = None
    #: A proposal that the objective changed, or None. Never a decision:
    #: a silent misdetection would poison every calibration lookup, so the
    #: UI asks and the operator confirms.
    turret_event: TurretEvent | None = None
    stats: dict = field(default_factory=dict)
    #: Per-feature milliseconds for the frame loop, smoothed.
    costs: dict = field(default_factory=dict)


class LivePipeline:
    """Consumes frames from a cell, emits LiveSignals to a plain callback.

    Two consumers, deliberately at different rates: the tracker and metric are
    cheap and need every frame, while peaking costs ~20 ms and is
    indistinguishable to the eye at half rate. Binding them together would let
    the expensive one set the pace for everything.
    """

    def __init__(self, on_signals: Callable[[LiveSignals], None],
                 illumination: Illumination = Illumination.BRIGHTFIELD,
                 peaking_divisor: int = 2) -> None:
        self._emit = on_signals
        self._cell: LatestFrame[Frame] = LatestFrame()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()

        self._illumination = illumination
        self._metric, self._prefilter = DEFAULTS[illumination]
        self._trace = FocusTrace()
        self._peaking_divisor = max(1, peaking_divisor)
        self._peaking_enabled = False
        self._region = Region.CENTRE
        self._blank = None
        #: Whether anything wants the blankness verdict. See `_analyse`.
        self._blank_watch = False
        self._still_for = 0
        #: The working white balance, and its lookup. Gains rather than a
        #: mode: "off" is unity, so nothing downstream has to special-case
        #: the absence of a correction. See live/balance.py for why this is
        #: a different thing from the one calib/ measures.
        self._wb = balance.UNITY
        self._wb_lut = None
        #: Where a balance would be taken from, and what that patch reads
        #: *before* any correction. Sampled on the way past every frame,
        #: because by the time somebody presses the button the uncorrected
        #: frame is gone -- and reading the corrected one instead is what
        #: made the control need several presses to converge.
        self._wb_rect = (0.40, 0.40, 0.20, 0.20)
        self._wb_sample: tuple[float, float, float] | None = None
        self._coverage = FocusCoverage()
        self._sweeping = False
        self._custom: tuple[float, float, float, float] | None = None
        self._xy = StageTracker()
        self._confidence = 0.0
        self._turret_det = TurretDetector()
        # An opaque handle, set by the UI. The live pipeline deliberately
        # does not import the session model -- it only passes this back to
        # the detector, which duck-types it.
        self._turret = None
        self._signatures = None
        self._learned = None
        #: The frame the stage position is currently measured against, the
        #: shift last measured from it, and the candidate that replaces it
        #: when the tracker asks for a fresh anchor.
        #: Bumped whenever the origin is reset. Owned by the lock; the
        #: keyframe fields below are owned by the analysis thread alone,
        #: which is what keeps them out of a race.
        self._track_gen = 0
        self._track_seen = 0
        #: Whether anything consumes the tracked position. The window
        #: says so; blind, the correlation is skipped entirely. A plain
        #: bool written by the interface thread and read by the analysis
        #: thread, which is safe for a flag that only gates work.
        self._track_wanted = True
        #: A relocalizer refix landed: the keyframe belongs to ground
        #: from before the crossing and must go. Set under the lock by
        #: the interface thread, consumed under it by analysis.
        self._refix_pending = False
        self._key: np.ndarray | None = None
        self._key_offset: tuple[float, float] = (0.0, 0.0)
        self._key_pending: np.ndarray | None = None
        self._hann: np.ndarray | None = None
        #: Channel destinations, reused across frames. Allocating fresh
        #: 2.2 MP planes per frame cost 3217 minor page faults -- the kernel
        #: mapping and zeroing megabytes thirty times a second -- and that,
        #: not the deinterleave, was nearly the whole of this stage. Safe to
        #: reuse because the planes never leave `_analyse`: the preview is an
        #: explicit copy, the histogram is its own array, and the tracker,
        #: turret and peaking all resize into new buffers of their own.
        #: Three named buffers rather than a split's list, because green is
        #: wanted every frame and blue and red only on histogram frames.
        self._gray_buf: np.ndarray | None = None
        self._blue_buf: np.ndarray | None = None
        self._red_buf: np.ndarray | None = None
        self._levels_seq = 0
        #: Last (histogram, per-channel clipping), reused on the frames
        #: between instrument repaints.
        self._levels: tuple | None = None
        self._analysed = 0
        #: Per-feature frame costs, always running. live/profile.py
        #: explains why this is not behind a flag.
        self.meter = Meter()
        self._tick = threading.Event()
        self._t_last = time.perf_counter()
        self._rate = 0.0

    # ---- the producer side ----------------------------------------------

    def submit(self, frame: Frame) -> None:
        """Called from the camera's thread. Must do nothing but hand off."""
        self._cell.put(frame)

    # ---- configuration ---------------------------------------------------

    def set_illumination(self, illumination: Illumination) -> None:
        with self._lock:
            self._illumination = illumination
            self._metric, self._prefilter = DEFAULTS[illumination]
            self._trace = FocusTrace()

    def set_metric(self, metric: Metric, prefilter: Prefilter) -> None:
        with self._lock:
            self._metric, self._prefilter = metric, prefilter
            self._trace = FocusTrace()

    def set_focus_region(self, region: Region,
                        custom: tuple[float, float, float, float] | None = None
                        ) -> None:
        with self._lock:
            self._region = region
            if custom is not None:
                self._custom = custom
            # The absolute score is not comparable between regions, so a peak
            # remembered from the old one would be meaningless.
            self._trace = FocusTrace()

    def set_blank_watch(self, on: bool) -> None:
        """Compute the blankness verdict, for as long as it is wanted.

        Pushed every frame by the window rather than tracked through the
        stack session's several beginnings and ends, so it cannot drift
        out of step with the thing it describes. The one frame of lag that
        costs is harmless: the trigger it feeds needs several settled
        frames before it fires at all.
        """
        self._blank_watch = bool(on)

    def set_peaking(self, enabled: bool) -> None:
        with self._lock:
            self._peaking_enabled = enabled

    def set_white_balance(self, gains) -> None:
        """The gains applied to every preview from the next frame on."""
        with self._lock:
            self._wb = balance.sane(gains)
            self._wb_lut = balance.lut(self._wb)

    @property
    def white_balance(self) -> tuple[float, float, float]:
        return self._wb

    def set_balance_rect(self, rect) -> None:
        """Where to sample for the next pick, normalised to the frame."""
        with self._lock:
            self._wb_rect = tuple(rect)

    @property
    def balance_sample(self) -> tuple[float, float, float] | None:
        """Mean B, G, R of that rect from the *uncorrected* frame."""
        return self._wb_sample

    def start_sweep(self) -> None:
        """Begin accumulating coverage, on a fresh accumulator.

        A *new* object rather than `reset()` on the existing one. The
        analysis thread takes its reference under the lock and then calls
        `update` outside it -- which is right, because that call is the
        expensive part and holding the lock across it would stall the
        interface. But it means resetting in place tears the accumulator's
        state out from under a frame that is already using it: `_peak`
        becomes None between two lines that both expect an array, and the
        blanket handler in the worker swallows the TypeError. The window
        opens exactly when somebody presses "start sweep", which is the
        one moment it is guaranteed to be pressed.

        Replacing the object closes it. A frame in flight finishes against
        the accumulator it started with, which is then discarded; the next
        frame picks up this one.
        """
        with self._lock:
            self._coverage = FocusCoverage()
            self._sweeping = True

    def stop_sweep(self) -> None:
        with self._lock:
            self._sweeping = False

    @property
    def sweeping(self) -> bool:
        return self._sweeping

    def reset_focus_peak(self) -> None:
        with self._lock:
            self._trace.reset_peak()

    def set_turret(self, turret, rotation_sign: int = 1,
                   signatures=None, learned=None) -> None:
        """Tell the detector what positions exist. None disables detection.

        `signatures` is the expected normalised brightness per position --
        learned where the operator has confirmed one, modelled where they
        have not.
        """
        with self._lock:
            self._turret = turret
            self._signatures = signatures
            self._learned = learned
            if rotation_sign != self._turret_det._sign:
                self._turret_det = TurretDetector(rotation_sign=rotation_sign)

    def set_track_wanted(self, wanted: bool) -> None:
        """Run the tracker, or let it sleep. The window computes who is
        listening: the map on screen, a mosaic, a sweep, a timelapse.
        Waking is the caller's moment to reset the origin -- travel while
        blind was never integrated, so old positions are lies."""
        self._track_wanted = bool(wanted)

    def correct_tracking(self, gen: int, delta=None, refix=None) -> None:
        """A correction from the relocalizer, measured on the interface
        thread against the map's own bank. Generation-guarded like every
        cross-thread opinion about position: a correction computed under
        an origin that has since been reset describes nothing.

        `delta` nudges -- drift measured over trodden ground, position
        and anchor moving together so the keyframe stays honest.
        `refix` plants the position absolutely after a blank crossing;
        the keyframe belongs to ground from before the gap, so the
        analysis thread drops it at the next pass, the same pattern the
        reset uses."""
        with self._lock:
            if gen != self._track_gen:
                return
            if refix is not None:
                self._xy.refix(refix)
                self._refix_pending = True
            elif delta is not None:
                self._xy.nudge(delta)

    def reset_tracking(self) -> None:
        """New origin. Required when the objective changes -- magnification
        changes the pixels-per-micron scale and old positions become lies.

        The keyframe goes with it. Resetting only the tracker left the
        correlation reference on a frame from before the change, so the
        first shift measured after "new origin" was the travel since that
        older frame -- and the origin was planted up to a keyframe interval
        away from the frame the operator pressed the button on. Worse after
        an objective change, where the two frames are at different
        magnifications and the shift between them means nothing at all.

        Which is exactly the bug this used to reintroduce by hand. The
        keyframe state is read and written by `_track` without the lock,
        and `phaseCorrelate` holds that window open for milliseconds of
        every frame, so clearing it from here raced: a press landing
        mid-correlation cleared the reference and then had the in-flight
        measurement, taken against the reference that no longer existed,
        handed to the freshly reset tracker. The origin went back to being
        planted up to a keyframe away from where the operator pressed.

        So this no longer touches that state at all. It bumps a
        generation, and the analysis thread -- the only thread that reads
        or writes the keyframe -- notices and clears it. A measurement
        that spans a bump is discarded rather than believed.

        Returns the new generation, so whoever cleared can also refuse
        frames still in flight from before the reset -- the slide map
        was repainting the current view at its stale position from
        exactly one such frame, which is why "clear" took two presses.
        """
        with self._lock:
            self._xy.reset()
            self._track_gen += 1
            return self._track_gen

    # ---- the hold-still guard --------------------------------------------
    #
    # A full-resolution pull freezes the preview for over a second, and any
    # cranking during it smears the frame invisibly. The tracker's previous
    # frame survives the gap, so the first analysis after the stream resumes
    # correlates straight across it -- displacement across the gap *is* the
    # motion during the shot.

    #: The guard needs a better lock than navigation does. Measured on the
    #: mock: structured scenes correlate at 0.85+, featureless ones under 0.3
    #: -- and below that line the integrated position is a noise walk that
    #: would accuse a still stage of moving.
    GUARD_CONFIDENCE = 0.5

    def stage_position(self) -> tuple[float, float] | None:
        """The tracker's current position, lock or no lock. Mosaic tiles want
        a position even when it is only navigation-grade -- it is a seed for
        registration, and a rough seed beats none."""
        with self._lock:
            return self._xy.position

    def guard_begin(self) -> dict | None:
        """Snapshot for a capture about to start. None when the tracker has no
        quality lock on the current scene (a blank field, say) -- a guard that
        cannot measure must stay silent rather than guess."""
        with self._lock:
            if (not self._xy.tracking or self._xy.position is None
                    or self._confidence < self.GUARD_CONFIDENCE):
                return None
            return {"pos": self._xy.position, "analysed": self._analysed,
                    "gated": self._xy.gated}

    def guard_measure(self, token: dict, timeout: float = 3.0) -> float | None:
        """How far did the view move since guard_begin, in preview pixels?

        Waits for two fresh analyses so the measurement spans the capture gap.
        Returns None when it cannot say (no frames arrived, or the scene lost
        lock for reasons that are not motion), and math.inf when the view
        changed by more than phase correlation can measure -- which for a
        capture is the strongest possible yes.
        """
        deadline = time.perf_counter() + timeout
        target = token["analysed"] + 2
        while self._analysed < target:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None
            self._tick.clear()
            self._tick.wait(min(0.25, remaining))
        with self._lock:
            if self._xy.gated > token["gated"]:
                return math.inf
            if not self._xy.tracking or self._xy.position is None:
                return None
            bx, by = token["pos"]
            x, y = self._xy.position
            return math.hypot(x - bx, y - by)

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        # A fresh cell if the last one was closed. `stop` closes it, and a
        # closed cell hands back None *immediately* rather than waiting out
        # the timeout -- so restarting on the old one left the loop
        # spinning at full speed, pegging a core and delivering nothing,
        # with no error anywhere to say so. Nothing restarts a pipeline
        # today; this is the kind of thing that becomes reachable later and
        # is then very hard to see.
        if self._cell.closed:
            self._cell = LatestFrame()
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="live-analysis")
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        self._cell.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ---- the consumer side ----------------------------------------------

    def _loop(self) -> None:
        while self._running.is_set():
            frame = self._cell.take(timeout=0.25)
            if frame is None:
                continue
            # Ownership is ours; release it before the next iteration no matter
            # what happens in between.
            with frame:
                try:
                    self._analyse(frame)
                except Exception:
                    # A bad frame must never kill the live view -- but a
                    # frame that fails every time is a real fault, and this
                    # is the only record of it.
                    _log.exception("analysis raised on a frame")

    def _analyse(self, frame: Frame) -> None:
        with self._lock:
            metric, prefilter = self._metric, self._prefilter
            peaking_on = self._peaking_enabled
            trace = self._trace
            region, custom = self._region, self._custom
            sweeping = self._sweeping
            coverage_acc = self._coverage
            wb_lut = self._wb_lut
            wb_rect = self._wb_rect

        data = frame.data
        mark = time.perf_counter()
        # Green rather than luminance, for the same reason the clipping
        # warning uses it: the ISP boosts blue about three times to neutralise
        # this sensor, so blue pins in the preview at roughly a fifth of full
        # scale. Luminance carries that in, which makes the focus score sag
        # from preview clipping that is not sensor clipping at all. Green
        # saturates within a few percent of the sensor's own ceiling, and it
        # carries most of the detail regardless.
        #
        # One plane, not three. The full split ran every frame, but blue
        # and red are only read on the frames the histogram is computed
        # -- one in three -- so two planes in two of three frames were
        # extracted into reused buffers for nobody. Green is pulled
        # alone here and the others only when the levels below want
        # them. (Clipping by histogram rather than by boolean masks is
        # older history: three `(chan >= 255).mean()` calls materialised
        # 2.2 MP temporaries at 10.9 ms a frame.)
        if data.ndim == 3:
            shape = data.shape[:2]
            if self._gray_buf is None or self._gray_buf.shape != shape:
                self._gray_buf = np.empty(shape, np.uint8)
                self._blue_buf = np.empty(shape, np.uint8)
                self._red_buf = np.empty(shape, np.uint8)
            gray = cv2.extractChannel(data, 1, self._gray_buf)
            colour = True
        else:
            gray = data
            colour = False

        # The levels are computed at the rate they are *looked at*, not at
        # frame rate. The exposure histogram repaints at a third of the
        # frame rate because it is read as a trend, and unlike the focus
        # trace nothing accumulates here between repaints -- the widget
        # simply takes the newest array -- so two of every three of these
        # were being thrown away unread. They are the most thread-hungry
        # work in the loop, which makes them the wrong thing to do at full
        # rate on a machine that has been told to use four threads.
        #
        # Subsampling the pixels was the obvious alternative and is unsafe:
        # a one-pixel-tall clipped streak, which is a slide edge or a dust
        # line, disappears entirely from a row-strided histogram and takes
        # the clipping warning with it. Sampling every pixel less often
        # still sees it, one frame late.
        if self._levels is None or self._analysed % INSTRUMENT_DIVISOR == 0:
            total = float(gray.size)
            if colour:
                blue = cv2.extractChannel(data, 0, self._blue_buf)
                red = cv2.extractChannel(data, 2, self._red_buf)
                hists = [cv2.calcHist([p], [0], None, [256],
                                      [0, 256]).ravel()
                         for p in (blue, gray, red)]
                hist = hists[1]
                per = (float(hists[2][255] / total),
                       float(hists[1][255] / total),
                       float(hists[0][255] / total))
            else:
                hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
                per = (0.0, float(hist[255] / total), 0.0)
            self._levels = (hist, per)
            self._levels_seq += 1
        hist, per = self._levels

        # Clipping is judged on green, not on luminance or on any channel.
        #
        # Measured against a raw frame of the same scene: a naive
        # preview == 255 test reported 22.26% of pixels clipped where the raw
        # had 0.69%. Blue accounted for essentially all of it -- 62% of blue
        # sites were blown in the preview and *none* of them were at raw
        # saturation, because neutralising this sensor needs about 3x blue
        # gain. Green saturates at raw ~3584, 88% of full scale, so it is
        # honest to within about 12% and carries most of the luminance anyway.
        clipped = per[1]               # green

        mark = self.meter.since("decode + histogram", mark)

        rect = region_rect(gray.shape, region, custom)
        score = measure(gray, metric, prefilter, rect)
        trace.push(score)
        gh, gw = gray.shape[:2]
        norm_rect = (rect[0] / gw, rect[1] / gh, rect[2] / gw, rect[3] / gh)

        mark = self.meter.since("focus metric", mark)

        # Exactly a quarter, because that is the only cheap INTER_AREA.
        # OpenCV has a fast box-average path only when the scale factor is a
        # whole number; 1824/512 is 3.5625, which falls into the generic
        # gather path and costs 2.0 ms. An exact quarter costs 0.73 ms for a
        # true 4x4 average, and is aspect-correct as a bonus, which the
        # square 512 was not. Shrinking the grid further is a trap: 256
        # square is 7.125x and measured *worse* than what it replaced.
        # A *size*, not a fraction. Dividing the preview by four gives
        # 684 wide at the 2736 mode, which is the grid the note above was
        # measured on, and 1360 at the 5440 mode -- four times the pixels
        # into a phase correlation that gains nothing from them. Measured
        # at 5440: 38.0 ms of a 143.9 ms frame, on a camera whose frame
        # period there is 83 ms. The correlation does not want more
        # picture because the preview has more picture.
        #
        # Still an integer factor, so it stays the true NxN average the
        # note calls for, and never coarser than four, so no mode is
        # tracked on less than it is today.
        step = max(4, int(round(gray.shape[1] / TRACK_WIDTH)))
        # The turret watch reads this downsample too, so it outlives the
        # tracker sleeping; only when neither wants it is it skipped.
        small = (cv2.resize(gray, (gray.shape[1] // step,
                                   gray.shape[0] // step),
                            interpolation=cv2.INTER_AREA)
                 if self._track_wanted or self._turret is not None else None)
        if not self._track_wanted:
            # Nobody is consuming the position: no map on screen, no
            # mosaic, no sweep, no timelapse. The correlation is one of
            # the larger stages in the table and is not run for an
            # audience of zero. Blind means blind: the position is
            # withheld and stillness is never claimed, so nothing
            # downstream -- the opportunist banking flats, the capture
            # guard -- mistakes sleep for a measurement. The window
            # resets the origin when it wakes the tracker, because
            # whatever moved while blind was never integrated and every
            # old position is a lie about the new resting place.
            offset, confidence = None, 0.0
            stage_pos, stage_tracking = None, False
            settled = False
            self._still_for = 0
            self.meter.skip("stage tracking")
            mark = time.perf_counter()
        else:
            with self._lock:
                gen = self._track_gen
                refixed = self._refix_pending
                self._refix_pending = False
            if gen != self._track_seen:
                # An origin was reset since the last frame. The correlation
                # reference belongs to a scale or a position that no longer
                # means anything, so it goes rather than being measured from.
                self._track_seen = gen
                self._drop_key()
            elif refixed:
                # The relocalizer planted a new position; the keyframe is
                # of ground from before the crossing it just bridged.
                self._drop_key()

            key_offset, offset, confidence = self._track(small, gray.shape)

            with self._lock:
                if self._track_gen != gen:
                    # The reset landed while this frame was correlating. The
                    # measurement is against an origin that no longer exists,
                    # and handing it to the tracker is the whole bug.
                    self._track_seen = self._track_gen
                    self._drop_key()
                    key_offset, offset, confidence = None, None, 0.0
                stage_pos, stage_tracking, rekey = self._xy.anchor(
                    key_offset, confidence, gray.shape)
                self._confidence = confidence
            if rekey or key_offset is None:
                self._rekey()

            # Stillness, from the tracker we already run. Two pixels of
            # drift is hand tremor on a manual stage, not movement.
            moving = offset is not None and (abs(offset[0]) > 2
                                             or abs(offset[1]) > 2)
            self._still_for = 0 if moving else self._still_for + 1
            settled = self._still_for >= 8

            mark = self.meter.since("stage tracking", mark)

        # Turret watch, from the downsample the tracker already made.
        #
        # This was believed to be "a 256-square resize and a mean", and the
        # mean is indeed free -- but the resize was 3.88 ms of a 4.01 ms
        # stage, because it fell into exactly the trap documented above:
        # 1824/256 is 7.125, so INTER_AREA takes its generic gather path and
        # one downscale cost more than the whole-frame work it was meant to
        # avoid. Reducing the existing quarter-size frame instead costs
        # 0.02 ms, and is the same box average applied in two passes.
        # Measured over 30 rotations on real frames, 29 give a bit-identical
        # proposal; the thirtieth agrees on which objective and differs only
        # in confidence, on a frame already clipped to white.
        #
        # The log-polar step really does only run on the frame a rotation
        # finishes, and costs 1.37 ms when it does.
        turret_event = None
        fed_turret = (self._turret is not None and small is not None
                      and self._analysed % TURRET_DIVISOR == 0)
        if fed_turret:
            # Exposure times gain is what the brightness reading has to be
            # divided by; without it the level says more about the last
            # slider touched than about which objective is in place.
            #
            # At a divisor, which the tracker must never run at and this
            # may: a human turning a turret takes about a second, the
            # detector integrates its evidence over that second, and ten
            # samples of it decide as well as thirty. Nothing here
            # integrates displacement, so nothing is silently lost.
            turret_event = self._turret_det.feed(
                small, self._turret,
                exposure_gain=max(frame.exposure_us * frame.gain_pct, 1),
                signatures=self._signatures, learned=self._learned)

        if fed_turret:
            mark = self.meter.since("turret watch", mark)
        else:
            self.meter.skip("turret watch")
            mark = time.perf_counter()

        if self._blank is None:
            from .blank import BlankDetector
            self._blank = BlankDetector()
        # Only while somebody is asking. The one live consumer is the
        # stack trigger, which uses it to avoid firing a slice on empty
        # glass, and that runs only during a stack. The opportunist looked
        # like a second consumer and is not: its `observe` is advisory and
        # reads the stage offset alone, the auto-grab-on-blank having been
        # removed as unworkable a while ago, and its own banking measures
        # blankness on the raw frame rather than this.
        #
        # About 2 ms of a 23 ms frame, spent on every frame of every
        # session to answer a question nothing was asking outside a stack.
        blank = (self._blank.looks_blank(small) if self._blank_watch
                 else False)

        mark = self.meter.since("blank check", mark)

        # The sharpness field is the expensive part, so it is computed once
        # and shared: peaking draws it, coverage accumulates it.
        field = None
        if (peaking_on or sweeping) and self._analysed % self._peaking_divisor == 0:
            field = self._peaking(gray)

        if field is not None:
            mark = self.meter.since("sharpness field", mark)
        else:
            self.meter.skip("sharpness field")
            mark = time.perf_counter()

        peak_map = field if peaking_on else None
        coverage = coverage_remaining = None
        coverage_done = False
        if sweeping:
            # Coverage is measured over the same region as the focus metric,
            # so 'spot' gives fast feedback on one detail and 'full' answers
            # whether the whole tile is covered.
            # The field is at reduced scale; the region is in preview
            # coordinates. Scale it down rather than the field up.
            k = self.PEAKING_SHRINK
            rx, ry, rw, rh = (v // k for v in rect)
            rw, rh = max(1, rw), max(1, rh)
            if field is not None:
                coverage_acc.update(field[ry:ry + rh, rx:rx + rw])
            coverage = coverage_acc.fraction
            coverage_done = coverage_acc.complete
            if coverage_acc.active:
                coverage_remaining = coverage_acc.overlay((rh, rw))

        if sweeping:
            mark = self.meter.since("coverage", mark)
        else:
            self.meter.skip("coverage")
            mark = time.perf_counter()

        # What the balance box reads before anything is done to it. Three
        # means over a small rect, so the cost is not worth measuring, and
        # this is the only moment the uncorrected values still exist.
        self._wb_sample = None
        if data.ndim == 3:
            fh, fw = data.shape[:2]
            bx, by, bw, bh = wb_rect
            patch = data[int(by * fh):int((by + bh) * fh),
                         int(bx * fw):int((bx + bw) * fw)]
            if patch.size:
                # Three strided reductions over interleaved BGR, which is
                # the slow way through a 3-channel array: 0.526 ms against
                # 0.065 ms. `cv2.mean` returns B, G, R, alpha, which is
                # the order this was already building.
                self._wb_sample = tuple(cv2.mean(patch)[:3])

        self._analysed += 1
        self._tick.set()
        now = time.perf_counter()
        dt = now - self._t_last
        self._t_last = now
        if dt > 0:
            self._rate = 0.9 * self._rate + 0.1 * (1.0 / dt)

        delivered, dropped = self._cell.stats
        # The preview copy is charged to the UI, because that is
        # who it exists for: the widget outlives the buffer pool.
        self.meter.frame()
        self._emit(LiveSignals(
            seq=frame.seq,
            timestamp=frame.timestamp,
            # Balanced here, and *after* the histogram above deliberately.
            # The instruments report the sensor's own levels -- that is what
            # the preview LUT exists for, and correcting the frame first
            # would put the very cast back into the numbers that machinery
            # was written to take out of them. The copy is not extra: the
            # UI outlives the buffer pool either way, so this replaces a
            # copy rather than adding a pass.
            preview=balance.applied(data, wb_lut),
            histogram=hist,
            levels_seq=self._levels_seq,
            clipped_fraction=clipped,
            channel_clipped=per,
            looks_blank=blank,
            settled=settled,
            focus_metric=score,
            focus_fraction_of_peak=trace.fraction_of_peak,
            focus_trace=trace.normalised,
            xy_offset=offset,
            xy_confidence=confidence,
            stage_pos=stage_pos,
            stage_tracking=stage_tracking,
            track_gated=self._xy.gated,
            track_small=small,
            # After the sync above, `_track_seen` is the generation this
            # frame's position was measured under -- including the case
            # where a reset landed mid-correlation and the measurement
            # was discarded, which syncs to the new origin.
            track_gen=self._track_seen,
            peaking=peak_map,
            sharpness_field=field if sweeping else None,
            focus_rect=norm_rect,
            coverage=coverage,
            coverage_remaining=coverage_remaining,
            coverage_complete=coverage_done,
            turret_event=turret_event,
            stats={"analysed_fps": self._rate, "delivered": delivered,
                   "dropped": dropped, "exposure_us": frame.exposure_us,
                   # Measured-but-rejected tracker steps. Each one is
                   # travel discarded whole, so a climbing count during a
                   # fast crank is the dead-reckoning undershoot being
                   # committed live -- the number that turns "the map
                   # feels short" into a diagnosis.
                   "gated": self._xy.gated,
                   "gain_pct": frame.gain_pct},
            costs=self.meter.snapshot(),
        ))

    def _track(self, small: np.ndarray, full_shape) -> tuple[tuple[float, float] | None, float]:
        """Phase correlation against the previous frame.

        cv2's response is a genuine confidence, unlike skimage's `error`, which
        returns ~1.0 unconditionally under its own default (scikit-image#7078).
        But confidence alone cannot detect a *wrong* match -- see DISCOVERY.md
        10a -- so downstream consumers must also apply a position constraint.
        """
        h, w = small.shape[:2]
        if self._hann is None or self._hann.shape[:2] != (h, w):
            self._hann = cv2.createHanningWindow((w, h), cv2.CV_32F)
            self._key = self._key_pending = None
            self._key_offset = (0.0, 0.0)
        # OpenCV 5's phaseCorrelate windows its inputs IN PLACE, and the
        # current frame is kept as the next frame's `prev` -- which used to
        # need a defensive copy of both inputs on every call, or else each
        # frame correlated a twice-windowed past against a once-windowed
        # present, a standing bias in the stage tracking. Windowing here and
        # passing no window leaves phaseCorrelate nothing to write into, so
        # the hazard is removed rather than defended, `prev` is windowed once
        # instead of once per correlation, and the uint8 to float32
        # conversion fuses into the same multiply for free. Verified
        # bit-identical to the windowed call.
        #
        # Do NOT pass self._hann to phaseCorrelate as well: that windows
        # already-windowed data and puts the bias straight back.
        cur = cv2.multiply(small, self._hann, dtype=cv2.CV_32F)
        # Correlated against the *keyframe*, not the previous frame. See
        # StageTracker.anchor for the measurements: integrating consecutive
        # frames loses about half the travel of a slow hand, and does it
        # with the confidence sitting near 1.0.
        if self._key is None:
            self._key, self._key_offset = cur, (0.0, 0.0)
            return None, None, 0.0
        (dx, dy), response = cv2.phaseCorrelate(self._key, cur)
        sx = full_shape[1] / w
        sy = full_shape[0] / h
        key_offset = (dx * sx, dy * sy)
        # Frame-to-frame motion for free, as the change in the shift from a
        # fixed reference. A second correlation would cost another 2 ms per
        # frame to answer the same question slightly worse.
        motion = (key_offset[0] - self._key_offset[0],
                  key_offset[1] - self._key_offset[1])
        self._key_offset = key_offset
        self._key_pending = cur
        return key_offset, motion, float(response)

    def _drop_key(self) -> None:
        """Forget the correlation reference. Analysis thread only."""
        self._key = None
        self._key_pending = None
        self._key_offset = (0.0, 0.0)

    def _rekey(self) -> None:
        """Adopt the current frame as the new anchor point."""
        if self._key_pending is not None:
            self._key = self._key_pending
            self._key_offset = (0.0, 0.0)

    #: The sharpness field is computed at half the preview's linear size.
    #: Both its consumers are insensitive to that: the peaking overlay is
    #: Gaussian-pooled with sigma 4 and then rescaled to the widget anyway,
    #: and coverage asks a per-region question, not a per-pixel one. Measured
    #: at 16.6 ms full size against 4.4 ms here, and it makes every coverage
    #: pass a quarter of the work as well.
    PEAKING_SHRINK = 2

    @classmethod
    def _peaking(cls, gray: np.ndarray) -> np.ndarray:
        """Per-pixel sharpness field, at reduced scale.

        Tenengrad plus Gaussian pooling, after focus-stack's task_focusmeasure
        (MIT). The blur is the load-bearing step nobody else does -- it turns a
        sparse edge response into a smooth field that can be colour-mapped
        rather than a speckle mask.

        Sigma scales with the shrink so the pooled field has the same shape in
        image terms as it did at full size -- otherwise halving the resolution
        would quietly double the effective blur radius.
        """
        k = cls.PEAKING_SHRINK
        if k > 1:
            h, w = gray.shape[:2]
            gray = cv2.resize(gray, (w // k, h // k),
                              interpolation=cv2.INTER_AREA)
        f = gray.astype(np.float32)
        gx = cv2.Sobel(f, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(f, cv2.CV_32F, 0, 1)
        mag = gx * gx + gy * gy
        mag = cv2.GaussianBlur(mag, (0, 0), 4.0 / k)
        return cv2.sqrt(mag)
