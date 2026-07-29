"""Detecting an objective change from the image alone.

Rotating a turret produces two independent, measurable signals:

  1. **The field goes dark**, sweeping in from one side as the objective leaves
     the light path. Which half darkens first gives the direction of rotation,
     and therefore which way round the ring you moved.
  2. **The field of view changes by the magnification ratio.** Going 20x -> 40x
     doubles everything in frame. Log-polar phase correlation recovers scale
     the same way ordinary phase correlation recovers translation, so the
     measured ratio identifies which position you landed on.

Neither is sufficient alone. Direction cannot catch a two-position jump.
Ratio is unreliable when the objectives are not parfocal and the new image
arrives badly defocused, or when a sparse darkfield frame has too little
structure to correlate. **Requiring them to agree is what makes it usable** --
and their disagreement is exactly the cue to ask the operator rather than
guess, because a silent misdetection would poison every calibration lookup
downstream.

Nothing here ever changes state on its own. It proposes; the UI disposes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import cv2
import numpy as np


class Phase(Enum):
    STABLE = auto()
    DARK = auto()          # mid-rotation, objective out of the path
    SETTLING = auto()      # light is back, waiting for the image to hold still


@dataclass(frozen=True)
class TurretEvent:
    """A proposal, never a decision."""

    direction: int | None          # +1, -1, or None if it could not be read
    scale_ratio: float | None      # new field of view / old
    suggested_index: int | None
    confidence: float              # 0..1
    agree: bool                    # did the two signals corroborate?
    reason: str

    @property
    def should_ask(self) -> bool:
        return not self.agree or self.confidence < 0.6


class TurretDetector:
    """Feed it grayscale preview frames; it watches for a rotation.

    Deliberately cheap: everything runs on a small downsample, because this has
    to sit inside the live loop beside the tracker and the focus metric.
    """

    DARK_FRACTION = 0.35       # of the running-stable mean
    SETTLE_FRAMES = 6
    SETTLE_TOLERANCE = 0.02

    #: Which way a darkening left edge means the turret index moved.
    #:
    #: The detector's raw reading is unambiguous -- the occlusion sweeps in
    #: from one side and we can say which. What that means for the *index* is
    #: not: it depends on how the turret is mounted, which way it was
    #: threaded, and the order the positions were typed into the profile.
    #: There is no way to derive it, so it is a property of the stand, and a
    #: stand where detection is consistently one position out is a stand
    #: whose sign is -1.
    ROTATION_SIGN = 1

    def __init__(self, size: int = 256, rotation_sign: int = ROTATION_SIGN
                 ) -> None:
        self._size = size
        self._sign = 1 if rotation_sign >= 0 else -1
        self._phase = Phase.STABLE
        self._stable_mean: float | None = None
        self._reference: np.ndarray | None = None   # last good stable frame
        self._pre_dark: np.ndarray | None = None
        self._direction: int | None = None
        self._settle: list[float] = []

    # ---- entry point -----------------------------------------------------

    def feed(self, gray: np.ndarray, turret=None) -> TurretEvent | None:
        small = cv2.resize(gray, (self._size, self._size),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        mean = float(small.mean())

        if self._phase is Phase.STABLE:
            return self._when_stable(small, mean)
        if self._phase is Phase.DARK:
            return self._when_dark(small, mean)
        return self._when_settling(small, mean, turret)

    # ---- phases ----------------------------------------------------------

    def _when_stable(self, small, mean):
        if self._stable_mean is None:
            self._stable_mean = mean
        # Slow tracking, so a lamp adjustment does not read as a rotation.
        self._stable_mean = 0.95 * self._stable_mean + 0.05 * mean

        # Only keep a reference while the field is *fully* lit. Updating it
        # every frame sounds harmless and is not: darkness is declared at 35%
        # of the stable mean, so the last frame stored before that is already
        # a third occluded, and the magnification measurement is then
        # correlating a half-covered frame against a clean one. Measured, the
        # difference is a field-of-view ratio off by a factor of three versus
        # one good to a few percent.
        if mean > self._stable_mean * 0.9:
            self._reference = small.copy()

        if mean < self._stable_mean * self.DARK_FRACTION:
            self._phase = Phase.DARK
            self._pre_dark = self._reference
            self._direction = None
        return None

    def _when_dark(self, small, mean):
        # Which half is darker *right now* tells us which side the occlusion
        # swept in from, and therefore the direction of rotation.
        if self._direction is None:
            half = small.shape[1] // 2
            left, right = small[:, :half].mean(), small[:, half:].mean()
            spread = abs(left - right) / max(small.mean(), 1e-6)
            if spread > 0.15:
                # Raw reading: the darker half is the side the occlusion came
                # from. The stand's sign turns that into an index direction.
                raw = -1 if left < right else +1
                self._direction = raw * self._sign

        if self._stable_mean and mean > self._stable_mean * 0.6:
            self._phase = Phase.SETTLING
            self._settle = []
        return None

    def _when_settling(self, small, mean, turret):
        self._settle.append(mean)
        if len(self._settle) < self.SETTLE_FRAMES:
            return None

        recent = np.asarray(self._settle[-self.SETTLE_FRAMES:])
        if recent.std() / max(recent.mean(), 1e-6) > self.SETTLE_TOLERANCE:
            return None                      # still moving; keep waiting

        ratio = self._scale_ratio(self._pre_dark, small)
        event = self._decide(ratio, turret)

        self._phase = Phase.STABLE
        self._stable_mean = mean
        self._reference = small.copy()
        return event

    # ---- measurement -----------------------------------------------------

    @staticmethod
    def _scale_ratio(before: np.ndarray | None, after: np.ndarray) -> float | None:
        """Field-of-view ratio via log-polar phase correlation.

        A uniform scale change becomes a pure translation along the log-radius
        axis, which is exactly what phase correlation measures. Returns None
        when the correlation is too weak to trust -- which happens on sparse
        darkfield frames, and must not be papered over.

        **Returns the field-of-view ratio, not the content scale.** Those are
        reciprocals and confusing them is silent: correlation measures how
        much larger objects became, while everything downstream reasons about
        how much less slide is in frame. Going 20x to 40x makes objects twice
        the size and the field of view half, so this returns 0.5.
        """
        if before is None or before.shape != after.shape:
            return None

        # A featureless field correlates with itself perfectly and reports no
        # scale change at all -- a confident 1.0 that means "I cannot tell"
        # rather than "nothing moved". This application hunts across blank
        # glass constantly, so that reading would arrive often and be trusted.
        # Structure first, then measure.
        for plate in (before, after):
            mean = float(plate.mean())
            if mean <= 1e-6 or float(plate.std()) / mean < 0.02:
                return None

        h, w = after.shape
        centre = (w / 2.0, h / 2.0)
        # cv2.logPolar was removed in OpenCV 4; warpPolar with WARP_POLAR_LOG
        # is the replacement, and its scale constant is defined differently:
        # the output x axis is rho = K*log(r) with K = out_width/log(maxRadius),
        # so a uniform scale s appears as a shift of K*log(s).
        max_radius = min(w, h) / 2.0
        k = w / np.log(max(max_radius, 2.0))
        flags = (cv2.INTER_LINEAR | cv2.WARP_FILL_OUTLIERS
                 | cv2.WARP_POLAR_LOG)
        a = cv2.warpPolar(before, (w, h), centre, max_radius, flags)
        b = cv2.warpPolar(after, (w, h), centre, max_radius, flags)

        # Windowed along rho only would be ideal; the angle axis wraps, so a
        # 2-D Hann window is a small lie that costs little and keeps this to
        # one call.
        win = cv2.createHanningWindow((w, h), cv2.CV_32F)
        (dx, _dy), response = cv2.phaseCorrelate(a, b, win)
        if response < 0.05:
            return None
        content_scale = float(np.exp(dx / k))
        if content_scale <= 1e-6:
            return None
        return 1.0 / content_scale

    def _decide(self, ratio: float | None, turret) -> TurretEvent:
        if turret is None or not getattr(turret, "positions", None):
            return TurretEvent(self._direction, ratio, None, 0.3, False,
                               "objective change detected, turret unknown")

        # What the direction alone implies.
        by_direction = None
        if self._direction is not None:
            probe = type(turret)(list(turret.positions), turret.current)
            by_direction = probe.step(self._direction)

        # What the ratio alone implies: the position whose magnification ratio
        # best matches what was measured.
        by_ratio, best_err = None, None
        if ratio is not None:
            for i in range(len(turret.positions)):
                if i == turret.current or turret.positions[i] is None:
                    continue
                expected = turret.ratio_to(i)
                if not expected:
                    continue
                # Higher magnification shrinks the field of view.
                err = abs(np.log((1.0 / expected) / ratio))
                if best_err is None or err < best_err:
                    by_ratio, best_err = i, err

        # Agreement is not enough on its own: the ratio search returns the
        # *nearest* position however far away it is, so a badly measured
        # ratio can still rank the right answer first and produce a confident
        # corroboration out of noise. The fit has to be good as well.
        agree = (by_direction is not None and by_ratio is not None
                 and by_direction == by_ratio
                 and best_err is not None and best_err < 0.25)
        if agree:
            return TurretEvent(self._direction, ratio, by_direction, 0.95, True,
                               "direction and magnification agree")
        if by_ratio is not None and best_err is not None and best_err < 0.12:
            return TurretEvent(self._direction, ratio, by_ratio, 0.6, False,
                               "magnification matched, direction unclear")
        if by_direction is not None:
            return TurretEvent(self._direction, ratio, by_direction, 0.45, False,
                               "direction only; magnification could not be measured")
        return TurretEvent(None, ratio, None, 0.2, False,
                           "objective change detected, position unclear")
