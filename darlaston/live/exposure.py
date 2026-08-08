"""Automatic exposure: what to meter, and how hard to move.

No camera and no Qt in here. The loop is a function from a measurement and
a state to a new state, so its stability can be tested against a simulated
sensor rather than discovered on the bench.

**What is metered.** Not the mean. Ordinary auto-exposure drives a frame's
average toward mid grey, which is wrong for a micrograph in both
directions: brightfield is mostly bright empty field, so mean metering
underexposes it by a stop or more, and darkfield is mostly black, so mean
metering blows the specimen out. What both want is the same thing said
once -- **the brightest real content sits just below clipping** -- so the
target is a high percentile of the histogram rather than its centre.

**Which percentile is the wrong question.** It depends on how much of the
frame the bright content occupies, and that varies by more than the
illumination mode does: a crowded darkfield mount and a single diatom on
one differ by two orders of magnitude. So instead of picking a percentile,
split the histogram into a dark and a bright population and meter the
bright one. Measured across coverage from 20% down to 0.1% of the frame,
that reads a steady 0.714 while a fixed 99th percentile falls from 0.749
to 0.039 -- which is the black surround, and a loop driving *that* to
target would open up four stops and bury the specimen in clipping.

It also means the operator never has to tell us. The illumination setting
stays available as an override for a scene that defeats the split, and is
not otherwise consulted.

**Why clipping is treated as worse than darkness.** A blown highlight has
no information in it and no exposure adjustment afterwards will invent
any, while a dark frame merely has a poor signal to noise ratio. So the
loop comes down fast and goes up slowly.

**Why exposure is spent before gain.** Gain amplifies read noise along
with signal. Exposure costs frame rate instead, and only past the point
where the sensor could actually have delivered a faster frame: at full
resolution this sensor reads out at 12 fps whatever the exposure, so
anything up to an 83 ms exposure there is free. Hence a ceiling that is
the slower of the frame rate the operator wants and the rate the mode can
actually sustain, rather than a fixed fraction of a second.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

#: Where the brightest real content is asked to sit, as a fraction of
#: full scale. Below clipping rather than at it, because the measurement
#: is an estimate and a sensor's response bends near the top.
TARGET = 0.88
#: Percentiles for the override path, when the histogram split is asked
#: not to decide. Kept because a scene can be genuinely unimodal, not
#: because the operator should normally have to answer this.
METERING = {
    "brightfield": (99.0, 0.90),
    "darkfield": (99.9, 0.82),
    "phase": (99.5, 0.88),
    "dic": (99.5, 0.88),
}
DEFAULT_METERING = (99.5, TARGET)
#: Errors smaller than this are left alone, in stops. Without a deadband
#: the controls twitch every frame against sensor noise, which reads as
#: an unstable instrument even when the picture is fine.
DEADBAND_STOPS = 0.10
#: Past this much error the loop is not tracking drift, it is recovering
#: from an event: a lamp turned up, a slide swapped, an objective changed.
#: It moves at full stride until back within it.
LARGE_STOPS = 1.5
#: Fraction of the remaining error taken per frame, when tracking. Two of
#: them because clipping is unrecoverable and darkness is not.
DAMPING_DOWN = 0.50
DAMPING_UP = 0.20
#: Never move further than this in one step while tracking, in stops. A
#: single noisy measurement should not be able to swing the picture.
MAX_STEP_STOPS = 1.0


@dataclass(frozen=True)
class Limits:
    """What this camera and this preview mode can actually do."""

    exposure_us: tuple[int, int]
    gain_pct: tuple[int, int]
    #: How long a frame takes to read out in the current mode. Exposure
    #: below this is free: the sensor could not have gone faster anyway.
    readout_us: int = 0
    target_fps: float = 30.0

    @property
    def exposure_ceiling_us(self) -> int:
        """Longest exposure that does not cost the operator frame rate."""
        wanted = 1e6 / max(self.target_fps, 1e-6)
        return int(min(self.exposure_us[1], max(wanted, self.readout_us)))

    @property
    def supported(self) -> bool:
        return self.exposure_us[1] > self.exposure_us[0] or \
            self.gain_pct[1] > self.gain_pct[0]


@dataclass(frozen=True)
class State:
    """Where the controls are, and what the loop may touch."""

    exposure_us: int
    gain_pct: int
    #: Set when the operator has pinned exposure and wants gain used
    #: alone. Worth having for its own sake, and necessary on a camera
    #: whose exposure response is not monotonic, where a loop that hunts
    #: on exposure will never settle.
    lock_exposure: bool = False


def measure(luma: np.ndarray, full_scale: float,
            illumination: str | None = None) -> float:
    """How bright the frame's brightest real content is, 0 to 1.

    `luma` may be any single channel view of the frame, at any scale: the
    percentile is a property of the distribution, so a decimated preview
    answers the same as the full frame for a fraction of the work.
    """
    pct, _target = METERING.get((illumination or "").strip().lower(),
                                DEFAULT_METERING)
    if luma.size == 0 or full_scale <= 0:
        return 0.0
    return float(np.percentile(luma, pct) / full_scale)


def target_for(illumination: str | None = None) -> float:
    _pct, target = METERING.get((illumination or "").strip().lower(),
                                DEFAULT_METERING)
    return target


def error_stops(level: float, target: float) -> float:
    """How far off we are, in stops. Positive means too dark.

    A floor under the measurement rather than a guard around the log: a
    frame that reads exactly zero is a lens cap or a closed shutter, and
    the loop should open up hard rather than divide by nothing.
    """
    return float(np.log2(max(target, 1e-6) / max(level, 1e-4)))


def step(state: State, limits: Limits, level: float,
         target: float = TARGET,
         reactivity: float = 1.0, hurry: bool = False) -> State:
    """One iteration. Returns the state unchanged if nothing needs doing.

    `reactivity` scales the damping, for an operator who wants the loop
    calmer or more eager. `hurry` skips the damping entirely for one step
    and is meant for known events rather than measured ones: changing
    objective loses four to sixteen times the light, and the objective
    selector knows it happened before the histogram does.
    """
    if not limits.supported:
        return state

    err = error_stops(level, target)
    if abs(err) < DEADBAND_STOPS and not hurry:
        return state

    big = hurry or abs(err) >= LARGE_STOPS
    if big:
        move = err
    else:
        damping = (DAMPING_UP if err > 0 else DAMPING_DOWN) * reactivity
        move = float(np.clip(err * damping, -MAX_STEP_STOPS, MAX_STEP_STOPS))

    return _apply(state, limits, 2.0 ** move)


def _apply(state: State, limits: Limits, factor: float) -> State:
    """Spend `factor` more light across exposure and gain, in that order.

    The ladder is its own hysteresis. Going up, gain is not touched until
    exposure has reached the ceiling; coming down, exposure is not touched
    until gain is back to its minimum. So a scene sitting exactly at the
    handover moves one control at a time instead of trading between them
    every frame.
    """
    exp_lo, exp_hi = limits.exposure_us
    gain_lo, gain_hi = limits.gain_pct
    ceiling = max(limits.exposure_ceiling_us, exp_lo)
    exposure, gain = float(state.exposure_us), float(state.gain_pct)

    if factor >= 1.0:
        if not state.lock_exposure and exposure < ceiling:
            room = ceiling / max(exposure, 1e-6)
            spend = min(factor, room)
            exposure *= spend
            factor /= spend
        if factor > 1.0 + 1e-9:
            gain = min(gain * factor, gain_hi)
    else:
        if gain > gain_lo:
            room = gain_lo / max(gain, 1e-6)          # < 1
            spend = max(factor, room)
            gain *= spend
            factor /= spend
        if factor < 1.0 - 1e-9 and not state.lock_exposure:
            exposure = max(exposure * factor, exp_lo)

    return replace(
        state,
        exposure_us=int(round(float(np.clip(exposure, exp_lo, exp_hi)))),
        gain_pct=int(round(float(np.clip(gain, gain_lo, gain_hi)))),
    )


# ---- metering from the histogram the pipeline already builds ---------------

def _quantile(hist: np.ndarray, pct: float) -> float:
    """Bin position of a percentile, as a fraction of the range.

    A one-bin histogram is not a degenerate input to guard against at the
    call sites: the split hands one back whenever a population lands in
    the top or bottom bin, which a clipped frame does routinely.
    """
    if hist.size == 0:
        return 0.0
    cum = np.cumsum(hist.astype(np.float64))
    total = float(cum[-1])
    if total <= 0:
        return 0.0
    if hist.size == 1:
        return 1.0
    idx = int(np.searchsorted(cum, total * pct / 100.0))
    return min(idx, hist.size - 1) / float(hist.size - 1)


def _otsu_split(hist: np.ndarray) -> int:
    """Bin index that best separates the histogram into two populations."""
    p = hist.astype(np.float64)
    total = p.sum()
    if total <= 0:
        return 0
    p = p / total
    levels = np.arange(len(p), dtype=np.float64)
    w0 = np.cumsum(p)
    m0 = np.cumsum(p * levels)
    mt = m0[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mt * w0 - m0) ** 2 / (w0 * (1.0 - w0))
    return int(np.nanargmax(np.where(np.isfinite(between), between, -1.0)))


#: How far apart the two populations must sit, as a fraction of full
#: scale, before the split is believed. Otsu always returns a threshold,
#: including on a frame that holds only one population, where it obligingly
#: cuts the sensor noise in half -- measured at a specimen covering 0.01%
#: of the frame, where the reported bright share jumps to 0.50 and the
#: reading collapses to 0.035. Separation catches that; a minimum size
#: does not, and rejects small real specimens on the way past.
MIN_SEPARATION = 0.05
#: Below this share the bright population is hot pixels rather than
#: subject. A secondary guard: the separation test does the real work.
MIN_SHARE = 1e-5


def bright_level(hist: np.ndarray, within_pct: float = 90.0,
                 min_share: float = MIN_SHARE) -> float:
    """Level of the brightest real content, without being told the mode.

    Splits the histogram into a dark and a bright population and meters
    the bright one. That is the same question in brightfield and in
    darkfield, and the answer stops depending on which the operator is
    on: in brightfield the bright population is the field, in darkfield
    it is the specimen, and either way what we want is for it to sit just
    under clipping.

    Measured stable at 0.71 across specimen coverage from 20% of the
    frame down to 0.05%, which is about five hundred pixels of a preview.
    Below that there is not enough of a population for any histogram
    method to find, and this falls back to a plain high percentile rather
    than metering its own noise.
    """
    if hist.size == 0 or hist.sum() <= 0:
        return 0.0
    cut = _otsu_split(hist)
    upper = hist[cut + 1:]
    share = float(upper.sum()) / float(hist.sum())
    if cut >= len(hist) - 2 or share < min_share:
        return _quantile(hist, 99.5)

    lo = (cut + 1) / float(len(hist) - 1)
    level = lo + _quantile(upper, within_pct) * (1.0 - lo)
    lower = hist[:cut + 1]
    dark = _quantile(lower, 50.0) * lo if lower.sum() > 0 else 0.0
    if level - dark < MIN_SEPARATION:
        # One population, cut arbitrarily. Nothing here to meter separately.
        return _quantile(hist, 99.5)
    return level


def measure_hist(hist: np.ndarray, illumination: str | None = None,
                 detect: bool = True) -> float:
    """The metered level, from the histogram the pipeline already has.

    Free, in the sense that matters: the live loop builds this histogram
    for the levels display over every pixel, so metering costs a
    cumulative sum over 256 bins rather than another pass over two
    megapixels. It also inherits that histogram's rate limiting, and
    subsampling instead was rejected in the pipeline for a reason that
    applies here too, that a one pixel streak vanishes from a strided
    read.
    """
    if detect:
        return bright_level(hist)
    pct, _target = METERING.get((illumination or "").strip().lower(),
                                DEFAULT_METERING)
    return _quantile(hist, pct)
