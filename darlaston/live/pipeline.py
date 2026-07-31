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

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from ..camera.buffers import Frame
from .cell import LatestFrame
from .coverage import FocusCoverage
from .focus import (DEFAULTS, FocusTrace, Illumination, Metric, Prefilter,
                    Region, measure, region_rect)
from .profile import Meter
from .tracker import StageTracker
from .turret import TurretDetector, TurretEvent, model_signatures


@dataclass(frozen=True)
class LiveSignals:
    """Everything the UI is allowed to know about a frame."""

    seq: int
    timestamp: float
    preview: np.ndarray                      # owned copy, safe to keep
    histogram: np.ndarray
    clipped_fraction: float
    black_fraction: float
    focus_metric: float
    focus_fraction_of_peak: float
    focus_trace: np.ndarray
    #: Per-channel fraction of pixels at 255 in the preview. Reported
    #: separately because they are not comparable: the ISP must boost blue
    #: about 3x to neutralise this sensor, so blue saturates roughly 4.7x
    #: earlier in raw terms than green does.
    channel_clipped: tuple[float, float, float] = (0.0, 0.0, 0.0)
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
        self._still_for = 0
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
        self._prev_small: np.ndarray | None = None
        self._hann: np.ndarray | None = None
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

    def set_peaking(self, enabled: bool) -> None:
        with self._lock:
            self._peaking_enabled = enabled

    def start_sweep(self) -> None:
        """Begin accumulating coverage. Resets whatever was there."""
        with self._lock:
            self._coverage.reset()
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

    def reset_tracking(self) -> None:
        """New origin. Required when the objective changes -- magnification
        changes the pixels-per-micron scale and old positions become lies."""
        with self._lock:
            self._xy.reset()

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
                    # A bad frame must never kill the live view.
                    import traceback
                    traceback.print_exc()

    def _analyse(self, frame: Frame) -> None:
        with self._lock:
            metric, prefilter = self._metric, self._prefilter
            peaking_on = self._peaking_enabled
            trace = self._trace
            region, custom = self._region, self._custom
            sweeping = self._sweeping
            coverage_acc = self._coverage

        data = frame.data
        mark = time.perf_counter()
        # Green rather than luminance, for the same reason the clipping
        # warning uses it: the ISP boosts blue about three times to neutralise
        # this sensor, so blue pins in the preview at roughly a fifth of full
        # scale. Luminance carries that in, which makes the focus score sag
        # from preview clipping that is not sensor clipping at all. Green
        # saturates within a few percent of the sensor's own ceiling, and it
        # carries most of the detail regardless.
        # Split once, then everything downstream reads contiguous memory.
        #
        # This was the most expensive thing in the loop and it was pure waste.
        # Clipping used to be three full-frame `(chan >= 255).mean()` calls,
        # each materialising a 2.2 MP boolean temporary: 10.9 ms per frame
        # against a 33 ms budget. Counting the same pixels with calcHist is
        # the obvious fix, but calcHist on an interleaved channel strides and
        # costs 5.7 ms -- while splitting first and running it on contiguous
        # planes costs 3.4 ms *and* hands back the green plane the focus
        # metric needs anyway, replacing a separate copy.
        if data.ndim == 3:
            planes = cv2.split(data)                  # B, G, R
            gray = planes[1]
            hists = [cv2.calcHist([p], [0], None, [256], [0, 256]).ravel()
                     for p in planes]
            total = float(gray.size)
            per = (float(hists[2][255] / total), float(hists[1][255] / total),
                   float(hists[0][255] / total))
            hist = hists[1]
        else:
            gray = data
            total = float(gray.size)
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
            per = (0.0, float(hist[255] / total), 0.0)
        black = float(hist[0] / total)

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

        small = cv2.resize(gray, (512, 512), interpolation=cv2.INTER_AREA)
        small_f = small.astype(np.float32)
        offset, confidence = self._track(small_f, gray.shape)
        with self._lock:
            stage_pos, stage_tracking = self._xy.advance(
                offset, confidence, gray.shape)
            self._confidence = confidence

        # Stillness, from the tracker we already run. Two pixels of drift is
        # hand tremor on a manual stage, not movement.
        moving = offset is not None and (abs(offset[0]) > 2 or abs(offset[1]) > 2)
        self._still_for = 0 if moving else self._still_for + 1
        settled = self._still_for >= 8

        mark = self.meter.since("stage tracking", mark)

        # Turret watch. Cheap per frame -- a 256-square resize and a mean --
        # and the expensive log-polar step only runs on the one frame where a
        # rotation finishes.
        turret_event = None
        if self._turret is not None:
            # Exposure times gain is what the brightness reading has to be
            # divided by; without it the level says more about the last
            # slider touched than about which objective is in place.
            turret_event = self._turret_det.feed(
                gray, self._turret,
                exposure_gain=max(frame.exposure_us * frame.gain_pct, 1),
                signatures=self._signatures, learned=self._learned)

        if self._turret is not None:
            mark = self.meter.since("turret watch", mark)
        else:
            self.meter.skip("turret watch")
            mark = time.perf_counter()

        if self._blank is None:
            from .blank import BlankDetector
            self._blank = BlankDetector()
        blank = self._blank.looks_blank(small)

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
            preview=data.copy(),          # explicit: the UI outlives the pool
            histogram=hist,
            clipped_fraction=clipped,
            black_fraction=black,
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
            peaking=peak_map,
            sharpness_field=field if sweeping else None,
            focus_rect=norm_rect,
            coverage=coverage,
            coverage_remaining=coverage_remaining,
            coverage_complete=coverage_done,
            turret_event=turret_event,
            stats={"analysed_fps": self._rate, "delivered": delivered,
                   "dropped": dropped, "exposure_us": frame.exposure_us,
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
        prev, self._prev_small = self._prev_small, small
        if prev is None or prev.shape != small.shape:
            self._hann = cv2.createHanningWindow(
                (small.shape[1], small.shape[0]), cv2.CV_32F)
            return None, 0.0
        # OpenCV 5's phaseCorrelate windows its inputs IN PLACE. `small` is
        # stored above as the next frame's `prev`, so without the copies
        # every frame correlated a twice-windowed past against a
        # once-windowed present -- a standing bias in the stage tracking.
        (dx, dy), response = cv2.phaseCorrelate(prev.copy(), small.copy(),
                                                self._hann)
        sx = full_shape[1] / small.shape[1]
        sy = full_shape[0] / small.shape[0]
        return (dx * sx, dy * sy), float(response)

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
