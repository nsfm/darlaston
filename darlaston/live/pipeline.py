"""LivePipeline — frames in, LiveSignals out.

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

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from ..camera.buffers import Frame
from .cell import LatestFrame
from .focus import (DEFAULTS, FocusTrace, Illumination, Metric, Prefilter,
                    Region, measure, region_rect)


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
    xy_offset: tuple[float, float] | None = None
    xy_confidence: float = 0.0
    peaking: np.ndarray | None = None
    #: Normalised (x, y, w, h) of the region the metric was taken from, so the
    #: view can show what is actually being measured.
    focus_rect: tuple[float, float, float, float] | None = None
    stats: dict = field(default_factory=dict)


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
        self._custom: tuple[float, float, float, float] | None = None
        self._prev_small: np.ndarray | None = None
        self._hann: np.ndarray | None = None
        self._analysed = 0
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

    def reset_focus_peak(self) -> None:
        with self._lock:
            self._trace.reset_peak()

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

        data = frame.data
        gray = (cv2.cvtColor(data, cv2.COLOR_BGR2GRAY) if data.ndim == 3
                else data)

        hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        total = float(gray.size)
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
        if data.ndim == 3:
            per = tuple(float((data[:, :, i] >= 255).mean()) for i in (2, 1, 0))
            clipped = per[1]           # green
        else:
            per = (0.0, float(hist[255] / total), 0.0)
            clipped = per[1]

        rect = region_rect(gray.shape, region, custom)
        score = measure(gray, metric, prefilter, rect)
        trace.push(score)
        gh, gw = gray.shape[:2]
        norm_rect = (rect[0] / gw, rect[1] / gh, rect[2] / gw, rect[3] / gh)

        small = cv2.resize(gray, (512, 512), interpolation=cv2.INTER_AREA)
        small_f = small.astype(np.float32)
        offset, confidence = self._track(small_f, gray.shape)

        peak_map = None
        if peaking_on and self._analysed % self._peaking_divisor == 0:
            peak_map = self._peaking(gray)

        self._analysed += 1
        now = time.perf_counter()
        dt = now - self._t_last
        self._t_last = now
        if dt > 0:
            self._rate = 0.9 * self._rate + 0.1 * (1.0 / dt)

        delivered, dropped = self._cell.stats
        self._emit(LiveSignals(
            seq=frame.seq,
            timestamp=frame.timestamp,
            preview=data.copy(),          # explicit: the UI outlives the pool
            histogram=hist,
            clipped_fraction=clipped,
            black_fraction=black,
            channel_clipped=per,
            focus_metric=score,
            focus_fraction_of_peak=trace.fraction_of_peak,
            focus_trace=trace.normalised,
            xy_offset=offset,
            xy_confidence=confidence,
            peaking=peak_map,
            focus_rect=norm_rect,
            stats={"analysed_fps": self._rate, "delivered": delivered,
                   "dropped": dropped, "exposure_us": frame.exposure_us,
                   "gain_pct": frame.gain_pct},
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
        (dx, dy), response = cv2.phaseCorrelate(prev, small, self._hann)
        sx = full_shape[1] / small.shape[1]
        sy = full_shape[0] / small.shape[0]
        return (dx * sx, dy * sy), float(response)

    @staticmethod
    def _peaking(gray: np.ndarray) -> np.ndarray:
        """Per-pixel sharpness field.

        Tenengrad plus Gaussian pooling, after focus-stack's task_focusmeasure
        (MIT). The blur is the load-bearing step nobody else does -- it turns a
        sparse edge response into a smooth field that can be colour-mapped
        rather than a speckle mask.
        """
        f = gray.astype(np.float32)
        gx = cv2.Sobel(f, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(f, cv2.CV_32F, 0, 1)
        mag = gx * gx + gy * gy
        mag = cv2.GaussianBlur(mag, (0, 0), 4.0)
        return cv2.sqrt(mag)
