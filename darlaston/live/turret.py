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


def model_signatures(turret, condenser_na: float | None = 0.55
                     ) -> list[float | None]:
    """Predicted relative brightness per position, before anything is learned.

    Image-plane illuminance goes as (NA_effective / M)^2, and the term that
    matters is which NA is effective: **the condenser's, not the
    objective's**, for anything above about NA 0.5. Filling a 1.0 objective
    needs an oiled condenser and essentially nobody oils theirs, so in
    practice the condenser is the limit and brightness falls as 1/M^2 across
    the top of a turret. Assuming the objective always wins predicts nearly
    constant brightness across a matched set, which is wrong in the ordinary
    case and wrong in the direction that makes this signal look useless.

    A prior, not an answer. It lets detection work on the very first rotation
    instead of needing to be taught, and a learned signature replaces it per
    position as soon as one exists -- which matters because the things that
    produce the *largest* real differences are outside this model entirely:
    an objective with no phase ring going dark against the phase stop, a
    condenser matched to one position and not another.
    """
    out: list[float | None] = []
    for objective in getattr(turret, "positions", []) or []:
        if objective is None or not objective.magnification:
            out.append(None)
            continue
        na = objective.na
        if na is None:
            out.append(None)
            continue
        effective = min(na, condenser_na) if condenser_na else na
        out.append((effective / objective.magnification) ** 2)
    return out


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
    agree: bool                    # did the signals corroborate?
    reason: str
    #: How much brighter or darker the field became, with exposure and gain
    #: divided out. Only meaningful against a learned signature.
    level_ratio: float | None = None
    #: What each signal independently thought, for the UI and for debugging
    #: a stand where one of them is unreliable.
    votes: tuple = ()
    #: The level measured after settling, normalised. The application feeds
    #: this back once the operator confirms a position, which is how the
    #: signature gets learned in the first place.
    level: float | None = None

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
        #: Field level before the rotation, with exposure and gain divided
        #: out, so what remains is a property of the optics rather than of
        #: how bright the lamp happened to be.
        self._pre_level: float | None = None
        self._scale = 1.0
        self._learned = None

    # ---- entry point -----------------------------------------------------

    def feed(self, gray: np.ndarray, turret=None, exposure_gain: float = 1.0,
             signatures=None, learned=None) -> TurretEvent | None:
        """One preview frame.

        `exposure_gain` is exposure microseconds times gain, and dividing by
        it is what makes the brightness signal mean anything: the operator
        adjusts both constantly, and an unnormalised level says more about
        the last slider they touched than about which objective is in place.

        `signatures` is the learned normalised level for each turret
        position, or None where a position has never been confirmed. It is
        learned rather than derived on purpose -- brightness through a
        well-corrected set is nearly constant in theory, because NA rises
        with magnification, so (NA/M)^2 varies by only a few percent across a
        whole turret. What actually produces the large differences an
        operator sees is everything theory does not cover: an objective with
        no phase ring going dark against the phase stop, a condenser matched
        to one position and not another, the field diaphragm. None of that is
        derivable from the objective's engraving, and all of it is stable for
        a given stand.
        """
        self._scale = max(float(exposure_gain), 1e-9)
        self._learned = learned
        small = cv2.resize(gray, (self._size, self._size),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        mean = float(small.mean())

        if self._phase is Phase.STABLE:
            return self._when_stable(small, mean)
        if self._phase is Phase.DARK:
            return self._when_dark(small, mean)
        return self._when_settling(small, mean, turret, signatures)

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
            self._pre_level = mean / self._scale

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

    def _when_settling(self, small, mean, turret, signatures=None):
        self._settle.append(mean)
        if len(self._settle) < self.SETTLE_FRAMES:
            return None

        recent = np.asarray(self._settle[-self.SETTLE_FRAMES:])
        if recent.std() / max(recent.mean(), 1e-6) > self.SETTLE_TOLERANCE:
            return None                      # still moving; keep waiting

        ratio = self._scale_ratio(self._pre_dark, small)
        level = mean / self._scale
        level_ratio = (level / self._pre_level
                       if self._pre_level and self._pre_level > 1e-12 else None)
        event = self._decide(ratio, turret, level_ratio, level,
                             signatures, self._learned)

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

    def _decide(self, ratio, turret, level_ratio=None, level=None,
                signatures=None, learned=None) -> TurretEvent:
        """Three independent readings, and what to do when they disagree.

        Direction, magnification and brightness fail in different ways, which
        is the whole point of having all three. Direction cannot see a
        two-position jump. Magnification needs structure to correlate and
        objectives that are close to parcentric. Brightness needs a signature
        that has been learned, and drifts if the lamp is adjusted mid-turn.
        No two of them fail together for the same reason, so agreement
        between any two is worth far more than confidence in any one.
        """
        if turret is None or not getattr(turret, "positions", None):
            return TurretEvent(self._direction, ratio, None, 0.3, False,
                               "objective change detected, turret unknown",
                               level_ratio=level_ratio, level=level)

        n = len(turret.positions)
        current = turret.current

        # 1. Direction: one step round the ring, skipping empty positions.
        by_direction = None
        if self._direction is not None:
            probe = type(turret)(list(turret.positions), current)
            by_direction = probe.step(self._direction)

        # 2. Magnification: which position's field-of-view ratio fits best.
        by_ratio, ratio_err = None, None
        if ratio is not None and ratio > 1e-6:
            for i in range(n):
                if i == current or turret.positions[i] is None:
                    continue
                expected = turret.ratio_to(i)
                if not expected:
                    continue
                err = abs(np.log((1.0 / expected) / ratio))
                if ratio_err is None or err < ratio_err:
                    by_ratio, ratio_err = i, err
        if ratio_err is not None and ratio_err > 0.35:
            by_ratio = None                      # nearest, but not near

        # 3. Brightness, against what this stand has been seen to do.
        by_level, level_err = None, None
        if (level_ratio and signatures and current < len(signatures)
                and signatures[current]):
            for i in range(n):
                if i == current or turret.positions[i] is None:
                    continue
                if i >= len(signatures) or not signatures[i]:
                    continue
                expected = signatures[i] / signatures[current]
                if expected <= 1e-9:
                    continue
                err = abs(np.log(expected / level_ratio))
                if level_err is None or err < level_err:
                    by_level, level_err = i, err
        if level_err is not None and level_err > 0.4:
            by_level = None
        # A modelled brightness may corroborate but must never decide alone.
        # The condenser iris moves the predicted ratios further than the
        # objectives do, so an unlearned signature is a first guess wearing
        # the same clothes as evidence.
        level_is_learned = bool(
            learned and by_level is not None and current < len(learned)
            and by_level < len(learned)
            and learned[current] and learned[by_level])

        votes = tuple(v for v in (by_direction, by_ratio, by_level)
                      if v is not None)
        named = (("direction", by_direction), ("magnification", by_ratio),
                 ("brightness", by_level))

        if votes:
            # Whichever position the most signals landed on. Two out of three
            # is the case this design exists to produce.
            best = max(set(votes), key=votes.count)
            agreed = [name for name, v in named if v == best]
            if len(agreed) >= 2:
                return TurretEvent(
                    self._direction, ratio, best,
                    0.95 if len(agreed) > 2 else 0.9, True,
                    " and ".join(agreed) + " agree",
                    level_ratio=level_ratio, votes=named, level=level)

            # Only one signal spoke. Say which, because a stand where the
            # same one is always alone is a stand with something to fix --
            # a rotation sign the wrong way round, or a signature never
            # learned.
            only = agreed[0] if agreed else "one signal"
            if only == "brightness" and not level_is_learned:
                return TurretEvent(
                    self._direction, ratio, None, 0.25, False,
                    "objective change detected, position unclear",
                    level_ratio=level_ratio, votes=named, level=level)
            return TurretEvent(self._direction, ratio, best, 0.5, False,
                               f"{only} only, unconfirmed",
                               level_ratio=level_ratio, votes=named,
                               level=level)

        return TurretEvent(None, ratio, None, 0.2, False,
                           "objective change detected, position unclear",
                           level_ratio=level_ratio, votes=named, level=level)
