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
    #: The occlusion reading before the optical path's sign was applied:
    #: -1 for a darkening left edge, +1 for a right. Kept so the application
    #: can work out which sign *would* have been right when the operator
    #: corrects a proposal, and stop asking.
    raw_direction: int | None = None
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
    #: Rise above the darkest point that counts as the light returning.
    #: Recovery must be judged against the *occlusion*, not against the
    #: previous objective's level -- see _when_dark.
    RECOVER_RISE = 2.5
    #: Frames after which a stuck DARK gives up and re-baselines. Nothing
    #: should be able to wedge this detector permanently; a missed rotation
    #: is a nuisance, a detector that never works again is a fault.
    DARK_PATIENCE = 400

    #: Which way a darkening left edge means the turret index moved.
    #:
    #: The detector's raw reading is unambiguous -- the occlusion sweeps in
    #: from one side and we can say which. What that means for the *index* is
    #: not: it depends on how the turret is mounted, which way it was
    #: threaded, and the order the positions were typed into the profile.
    #: There is no way to derive it, so it is a property of the stand, and a
    #: stand where detection is consistently one position out is a stand
    #: whose sign is -1.
    #: How the occlusion reading maps onto the turret index.
    #:
    #: Not a property of the turret. Between the turret moving and a pixel
    #: darkening the handedness passes through the objective (which inverts
    #: the image), the head and any photo tube, however the camera happens to
    #: be screwed onto its C-mount, and our own vertical flip of the raw
    #: stream. Four stages, each able to reverse a sign, and their product is
    #: not derivable from anything visible in the image. So it is measured
    #: once from a correction rather than reasoned about.
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
        self._entering: int | None = None
        self._raw_direction: int | None = None
        self._halves: tuple[float, float] | None = None
        self._sweep_side: int | None = None
        self._sweep_strength = 0.0

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
        # Every frame, in every phase: the sweep can be over in two.
        self._observe_sweep(small)

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
            self._entering = None
            # Fully lit and steady: whatever sweep was recorded belonged to
            # something that did not become a rotation.
            self._sweep_side = None
            self._sweep_strength = 0.0
        elif mean < self._stable_mean * 0.75:
            # Already dimming but not yet dark. Read the asymmetry here too.
            #
            # The occlusion sweep is fast -- a few frames at most on a real
            # stand, with a leading edge that reads almost flat -- so waiting
            # until darkness is declared can mean sampling only frames that
            # are already uniformly black, with no side to read. This catches
            # the edge on its way in, and the DARK phase overrides it if it
            # gets a cleaner look.
            side = self._which_side(small)
            if side:
                self._entering = side

        # A baseline of essentially zero cannot meaningfully be dropped
        # below, and testing against it makes a detector staring at a
        # capped camera flip between phases every frame.
        if (self._stable_mean > 1e-3
                and mean < self._stable_mean * self.DARK_FRACTION):
            self._phase = Phase.DARK
            self._pre_dark = self._reference
            self._direction = None
            self._dark_min = mean
            self._raw_direction = None
            self._dark_frames = 0
            self._dark_levels = []
            # Carry any reading taken on the way in, so a sweep too fast to
            # sample mid-darkness still has a direction.
            side = self._sweep_side or self._entering
            if side:
                self._raw_direction = side
                self._direction = side * self._sign
        return None

    def _observe_sweep(self, small) -> None:
        """Record which half of the frame is darkening fastest.

        Reading which half is *darker* is the obvious test and a poor one.
        It is biased by whatever the illumination already does -- a field
        that is brighter on one side reads as though the occlusion always
        came from the other -- and it needs the edge to have travelled far
        enough to make a large absolute difference, which at 24 fps and a
        sweep of two or three frames it may never do.

        Which half *fell more since the last frame* has neither problem. Any
        standing unevenness cancels, because it is present in both frames,
        and a flat edge crossing the field produces a clear differential from
        its first frame onward. The strongest observation of the transition
        is kept, so one good frame is enough.
        """
        half = small.shape[1] // 2
        left = float(small[:, :half].mean())
        right = float(small[:, half:].mean())
        previous = self._halves
        self._halves = (left, right)
        if previous is None:
            return
        drop_left = previous[0] - left
        drop_right = previous[1] - right
        total = max(previous[0] + previous[1], 1e-6)
        # Only a real dimming says anything; noise around a steady level does
        # not, and neither does the field getting brighter.
        if (drop_left + drop_right) / total < 0.02:
            return
        # The *first* asymmetric dimming, not the strongest.
        #
        # An occlusion crosses the whole frame: the leading half darkens,
        # and then once it is dark the trailing half darkens as the edge
        # continues. Both produce a large differential and the second is
        # often marginally the larger, so keeping the strongest reads the
        # sweep backwards -- measured, and the reason six tests failed the
        # moment this was tried. Which side went first is the question, so
        # the first answer is kept and not overwritten.
        strength = abs(drop_left - drop_right) / total
        if self._sweep_side is None and strength > 0.03:
            self._sweep_strength = strength
            self._sweep_side = -1 if drop_left > drop_right else +1

    @staticmethod
    def _has_image(small) -> bool:
        """Is there a picture here, or just a blocked light path?

        Measured after averaging down to 32 squares. Sensor noise on a black
        frame has a large spread relative to its near-zero mean, so testing
        contrast at full resolution calls an occluded field "structured";
        averaging removes the noise and leaves only real detail behind.
        """
        tiny = cv2.resize(small, (32, 32), interpolation=cv2.INTER_AREA)
        mean = float(tiny.mean())
        if mean <= 1e-6:
            return False
        return float(tiny.std()) / mean > 0.03

    @staticmethod
    def _which_side(small) -> int | None:
        """Which half is darker: -1 for the left, +1 for the right, None if
        the frame is too even to say."""
        half = small.shape[1] // 2
        left = float(small[:, :half].mean())
        right = float(small[:, half:].mean())
        spread = abs(left - right) / max(float(small.mean()), 1e-6)
        if spread <= 0.15:
            return None
        return -1 if left < right else +1

    def _when_dark(self, small, mean):
        """Mid-rotation. Read the direction, and work out when light returns.

        Recovery used to be declared at 60% of the *previous objective's*
        stable mean, which assumes the next objective is about as bright as
        the last. On a real stand it is not: measured on a Zeiss in phase,
        10x to 16x lands at 22% of the previous level and 16x to 25x at 39%,
        because the phase annulus stops matching. Both are below the old
        threshold, so the detector entered DARK and could never leave -- no
        prompt for that rotation, and none for any rotation afterwards
        either, since it stayed wedged.

        Light returning is therefore judged against the *darkness*, not
        against the old brightness. Anything meaningfully above the darkest
        point is the objective arriving; and a level that simply goes quiet
        without ever rising is a rotation whose occlusion we sampled too
        briefly to see, which is equally an arrival.
        """
        self._dark_frames = getattr(self, "_dark_frames", 0) + 1
        self._dark_min = min(getattr(self, "_dark_min", mean), mean)

        # Which half is darker *right now* tells us which side the occlusion
        # swept in from, and therefore the direction of rotation.
        if self._direction is None:
            # Raw reading: the darker half is the side the occlusion came
            # from. The stand's sign turns that into an index direction.
            side = self._sweep_side or self._which_side(small)
            if side:
                self._raw_direction = side
                self._direction = side * self._sign

        levels = getattr(self, "_dark_levels", None)
        if levels is None:
            levels = self._dark_levels = []
        levels.append(mean)
        recent = levels[-self.SETTLE_FRAMES:]

        risen = mean > self._dark_min * self.RECOVER_RISE
        # A steady level is not the same as an arrival. A turret held
        # mid-rotation is perfectly steady and perfectly black, so the quiet
        # escape fired while the light path was still blocked and proposed an
        # objective from an occluded frame. It has to see an actual image.
        quiet = (len(recent) >= self.SETTLE_FRAMES
                 and float(np.std(recent)) / max(float(np.mean(recent)), 1e-6)
                 < self.SETTLE_TOLERANCE
                 and self._has_image(small))
        if risen or quiet:
            self._phase = Phase.SETTLING
            self._settle = []
            return None

        if self._dark_frames > self.DARK_PATIENCE:
            # Give up rather than stay wedged: re-baseline on whatever is
            # there now and watch again.
            self._phase = Phase.STABLE
            self._stable_mean = max(mean, 1e-6)
            self._reference = small.copy()
            self._pre_level = mean / self._scale
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
        self._sweep_side = None
        self._sweep_strength = 0.0
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
                               level_ratio=level_ratio, level=level,
                               raw_direction=self._raw_direction)

        n = len(turret.positions)
        current = turret.current

        # A turret is walked one detent at a time, so only the two
        # neighbours are candidates.
        #
        # This is a strong prior and it earns its place twice over. It
        # removes a whole class of wrong answers -- the magnification and
        # brightness searches previously ranked *every* position and could
        # nominate one two or three places away, which no hand movement
        # produces. And it turns all three signals onto the same binary
        # question, "which of the two neighbours", where before they were
        # each picking one of five. Brightness in particular becomes
        # decisive: on this stand 6.3x and 25x are 22% apart and were
        # confusable, and they are never adjacent.
        #
        # A genuine two-position spin is named wrongly by this, and the
        # "Other" menu is how that gets corrected. Being reliably right
        # about the ordinary case beats being occasionally right about the
        # rare one.
        neighbours = set()
        for delta in (+1, -1):
            probe = type(turret)(list(turret.positions), current)
            landed = probe.step(delta)
            if landed != current:
                neighbours.add(landed)

        # 1. Direction: one step round the ring, skipping empty positions.
        by_direction = None
        if self._direction is not None:
            probe = type(turret)(list(turret.positions), current)
            by_direction = probe.step(self._direction)

        # 2. Magnification: which position's field-of-view ratio fits best.
        by_ratio, ratio_err = None, None
        if ratio is not None and ratio > 1e-6:
            for i in sorted(neighbours):
                if turret.positions[i] is None:
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
            for i in sorted(neighbours):
                if turret.positions[i] is None:
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
                    level_ratio=level_ratio, votes=named, level=level,
                    raw_direction=self._raw_direction)

            # Only one signal spoke. Say which, because a stand where the
            # same one is always alone is a stand with something to fix --
            # a rotation sign the wrong way round, or a signature never
            # learned.
            only = agreed[0] if agreed else "one signal"
            if only == "brightness" and not level_is_learned:
                return TurretEvent(
                    self._direction, ratio, None, 0.25, False,
                    "objective change detected, position unclear",
                    level_ratio=level_ratio, votes=named, level=level,
                    raw_direction=self._raw_direction)
            return TurretEvent(self._direction, ratio, best, 0.5, False,
                               f"{only} only, unconfirmed",
                               level_ratio=level_ratio, votes=named,
                               level=level,
                               raw_direction=self._raw_direction)

        return TurretEvent(None, ratio, None, 0.2, False,
                           "objective change detected, position unclear",
                           level_ratio=level_ratio, votes=named, level=level,
                    raw_direction=self._raw_direction)
