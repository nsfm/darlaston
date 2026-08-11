"""The tracking-drift ritual's arithmetic: fit a readout time from
closed passes over the same ground.

A rolling shutter rescales every measured vertical shift by
1 + v·T/h (T the readout time, v the vertical speed, h the frame
height), so an up-and-down pass that truly closes -- the operator
re-centres the same feature, which is the ground truth this ritual is
built on -- ends with a residual tracked position equal to

    drift = -(T / h) · Σ (v · dy)          per pass, first order

Everything on the right is shipped in the live signals, so T falls out
of a least-squares fit over the passes. The sign of T carries the
readout direction, which is also how a camera mounted back-to-front
calibrates correctly without anybody saying so.

Nothing here imports Qt: the dialog wears this, and the tests hold it
without a window. See spike/docs/rolling-shutter.md for the derivation
and the probe evidence.
"""
from __future__ import annotations


class DriftCalibration:
    """Accumulates one ritual's evidence and fits `readout_us`.

    One instance per ritual. `begin_pass` at each re-centred start,
    `observe` every tracked frame, `mark` when the operator asserts
    they are back on the feature. After the measuring passes, `fit`;
    the verification pass then uses the same begin/observe/mark shape
    and its mark simply reports the residual drift.
    """

    #: A pass whose rectified travel is too small cannot testify: the
    #: fit divides by it. A third of a field of Σ|v·dy| at cranking
    #: speeds is far exceeded by any honest pass.
    MIN_EVIDENCE = 1e4

    def __init__(self, frame_h: int) -> None:
        self.h = float(frame_h)
        self._y0: float | None = None
        self._x = 0.0
        self._path = 0.0
        #: (drift_px, Σ v·dy) per completed pass.
        self.passes: list[tuple[float, float]] = []

    @property
    def running(self) -> bool:
        return self._y0 is not None

    @property
    def fields_travelled(self) -> float:
        """Path length this pass, in fields of frame height."""
        return self._path / self.h

    def begin_pass(self, pos_y: float) -> None:
        self._y0 = float(pos_y)
        self._x = 0.0
        self._path = 0.0

    def observe(self, vy_px_s: float, dy_px: float) -> None:
        """One tracked frame's motion: vertical speed and vertical step,
        both in preview pixels (per second and per frame)."""
        if self._y0 is None:
            return
        self._x += vy_px_s * dy_px
        self._path += abs(dy_px)

    def mark(self, pos_y: float) -> float | None:
        """The operator is back on the feature. Returns the pass's
        drift in pixels, or None if the pass carried no evidence."""
        if self._y0 is None:
            return None
        drift = float(pos_y) - self._y0
        x, self._y0 = self._x, None
        if abs(x) < self.MIN_EVIDENCE:
            return None
        self.passes.append((drift, x))
        return drift

    def fit(self) -> float | None:
        """Least-squares readout time over the passes, in microseconds,
        or None without enough evidence."""
        if not self.passes:
            return None
        num = sum(d * x for d, x in self.passes)
        den = sum(x * x for d, x in self.passes)
        if den <= 0.0:
            return None
        # Positive, not negative: the ritual observes *position* deltas
        # while the pipeline corrects *offsets*, and the tracker inverts
        # between the two -- drift and evidence both flip, their ratio
        # once. Returned in the pipeline's convention so the number goes
        # straight into set_readout; the probe pins this end to end.
        return num / den * self.h * 1e6
