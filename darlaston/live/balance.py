"""The working white balance: what the operator picks off the screen.

Distinct from the one `calib/` measures, and the distinction is the whole
design. That one is shot from a blank field under a known lamp, keyed to
the optical configuration, and persists because the optics do. This one
is a per-scene adjustment somebody makes while looking down the eyepiece,
because the mount is warm, or the lamp is turned down, or the last slide
was stained differently. Two lifetimes, two controls.

Both end up as the same three numbers: gains that multiply each channel
to make a chosen region neutral, in R, G, B order with green pinned at
one -- the same convention `calib.frames.white_balance_from_flat`
returns, and the reciprocal of what a DNG's AsShotNeutral wants.

Nothing here imports Qt, and it must not: this runs inside the live
pipeline. See ARCHITECTURE.md 4.
"""
from __future__ import annotations

import cv2
import numpy as np

#: No correction at all. Not None, because "off" and "picked, and it came
#: out neutral" are different states and only one of them should be able
#: to disappear when it is written to disk and read back.
UNITY = (1.0, 1.0, 1.0)

#: Beyond this, a gain is not a correction, it is a mistake -- a region
#: picked on something that is not grey at all, or on a blown highlight
#: where one channel has already clipped and reads far darker than it is.
#: Four stops either way, in linear light, is far more than any real
#: illuminant needs and still leaves room for a deeply orange halogen lamp
#: at low volts. In display terms it is a 3.5x ratio.
MAX_GAIN = 16.0

#: The gains are kept as *linear-light* ratios, because that is what the
#: capture path multiplies into a DNG's AsShotNeutral, where they meet
#: linear sensor values. The preview is display-encoded, so a pick off its
#: means is linearised before the ratio is taken, and the lookup that
#: applies the gain to the preview linearises each code, scales, and
#: re-encodes.
#:
#: What that does and does not change, measured: a power law commutes with
#: a scale, so the preview lookup built this way is the *same* lookup the
#: old display-domain code produced (to within one code, wherever the
#: clamp does not bind). The preview was never the thing being fixed; the
#: number written into the file was. A display ratio of 1.33 used to go
#: into the file as 1.33 where the linear truth was 1.87, so every picked
#: trim under-corrected the raw. The preview's own residual cast comes
#: from elsewhere -- clipping, and the ISP's curve not being a power law
#: -- and is dealt with where it arises.
GAMMA = 2.2


def from_display_ratio(gains) -> tuple[float, float, float]:
    """Gains stored by an older build, which kept display-domain ratios,
    carried into linear light. Idempotence is not possible -- a number
    does not say which domain it is in -- so the settings file says."""
    r, g, b = sane(gains)
    return sane((r ** GAMMA, 1.0, b ** GAMMA))


def _linear(v):
    """A display-gamma value (0..255) as linear light in 0..1."""
    return (np.asarray(v, np.float32) / 255.0) ** GAMMA


def gains_from_means(b: float, g: float, r: float
                     ) -> tuple[float, float, float]:
    """Neutralising gains from BGR channel means, derived in linear light.

    Green is the reference (see `sane`). The means arrive display-encoded --
    from `cv2.mean` of an 8-bit patch -- so they are linearised before the
    ratio is taken, which is the whole correctness of the thing.
    """
    rl, gl, bl = float(_linear(r)), float(_linear(g)), float(_linear(b))
    if min(rl, gl, bl) <= 0:
        return UNITY
    return sane((gl / rl, 1.0, gl / bl))


def sane(gains) -> tuple[float, float, float]:
    """Gains, clamped, with green normalised to one.

    Green is the reference because it is the channel a Bayer sensor has
    most of and the one that clips last on this hardware. Normalising here
    rather than trusting the caller means a stored value from an older
    version, or a hand-edited settings file, cannot brighten the whole
    preview by pretending to be a white balance.
    """
    try:
        r, g, b = (float(v) for v in gains)
    except (TypeError, ValueError):
        return UNITY
    if not all(np.isfinite(v) and v > 0 for v in (r, g, b)):
        return UNITY
    r, b = r / g, b / g
    return (min(max(r, 1 / MAX_GAIN), MAX_GAIN), 1.0,
            min(max(b, 1 / MAX_GAIN), MAX_GAIN))


def from_region(bgr: np.ndarray) -> tuple[float, float, float]:
    """Gains that would make this patch of preview neutral.

    Takes BGR, because that is what OpenCV hands back and what the whole
    live path carries. Returns R, G, B, because that is what everything
    downstream of a capture expects.
    """
    a = np.asarray(bgr, np.float32)
    if a.size == 0:
        return UNITY
    if a.ndim == 2:                      # mono: there is nothing to balance
        return UNITY
    b, g, r = (float(a[..., i].mean()) for i in range(3))
    return gains_from_means(b, g, r)


def lut(gains) -> np.ndarray | None:
    """A 256-entry per-channel lookup, or None when there is nothing to do.

    A lookup rather than arithmetic on the frame. The preview is 6.65 MB
    and this runs on every one of them, so the difference between a table
    and three multiplies plus a clip is the difference between something
    that costs nothing and something that shows up in the frame budget.
    Exact for 8-bit data, which is what a preview is.

    Shaped (1, 256, 3) in BGR order, because that is what `cv2.LUT` wants
    beside a BGR image.
    """
    r, g, b = sane(gains)
    if (r, g, b) == UNITY:
        return None
    # The gain is a linear-light scaling, so each display value is taken to
    # linear, scaled, and returned. Under a pure power law this is the same
    # table as scaling the code by gain ** (1 / GAMMA) directly; it is
    # written this way so the one stored quantity -- a linear ratio -- is
    # the thing applied, and the file and the preview cannot drift apart
    # by a conversion one of them forgot.
    lin = (np.arange(256, dtype=np.float32) / 255.0) ** GAMMA
    table = np.empty((1, 256, 3), np.uint8)
    for channel, gain in enumerate((b, g, r)):        # cv2 order
        corrected = np.clip(lin * gain, 0.0, 1.0) ** (1.0 / GAMMA)
        table[0, :, channel] = np.clip(corrected * 255.0 + 0.5, 0, 255)
    return table


def applied(bgr: np.ndarray, table: np.ndarray | None) -> np.ndarray:
    """The frame with the balance on it. Always a fresh array."""
    if table is None:
        return bgr.copy()
    return cv2.LUT(bgr, table)


#: A channel mean this close to full scale is a patch that has clipped,
#: and a ratio taken from it describes the clipping, not the light.
CLIPPED_MEAN = 250.0
#: A share of a channel's pixels at full scale past which the channel
#: counts as blown. Two percent: a hot pixel or a slide edge is less.
CLIPPED_SHARE = 0.02


def clipped_channels(sample_bgr, shares_bgr=None) -> tuple[bool, bool, bool]:
    """Which of B, G, R is blown in this patch.

    Either by its mean sitting at the top of the scale, or by enough of
    its pixels being there -- a textured patch can be blown over a
    quarter of its area while its mean reads a respectable 200.
    """
    shares = tuple(shares_bgr) if shares_bgr else (0.0, 0.0, 0.0)
    return tuple(float(v) >= CLIPPED_MEAN or share > CLIPPED_SHARE
                 for v, share in zip(sample_bgr, shares))


def patch_is_clipped(sample_bgr, shares_bgr=None) -> bool:
    """Would a pick off this patch neutralise a lie?

    Measured on a magenta mount: metered on green, 85% of red and 83% of
    blue were at 255 in the preview, and a pick there made the mount grey
    and left everything dimmer -- every edge of the specimen -- magenta.
    """
    return any(clipped_channels(sample_bgr, shares_bgr))


class IspBalancer:
    """Drive a camera's own white-balance codes until a patch reads neutral.

    The codes' scale is the vendor's and undocumented, so this does not
    compute them: it steps, watches what the step did to the patch, and
    learns the slope as it goes. Each `propose` takes the latest sample
    and answers the next codes to write, or None when there is nothing
    left to do -- converged, stuck at the range's edge, or out of steps.

    Green is never moved. The two ratios that define a balance are
    red-to-green and blue-to-green, and moving all three would turn a
    balance into an exposure.
    """

    #: Initial guess at how many codes make one stop. Replaced by the
    #: measured slope after the first step lands.
    SLOPE = 64.0
    #: Within this many stops of neutral is neutral: a 2% difference in
    #: channel means, which is below what the eye sees in a preview.
    DONE_STOPS = 0.03
    #: When a channel is clipped the ratio is unreadable; step down by
    #: this much and look again.
    BLIND_STEP_STOPS = 1.0
    MAX_STEPS = 8
    #: The slope is only believed within this band. Outside it the
    #: measurement was noise or a clipped channel moving.
    SLOPE_BOUNDS = (16.0, 256.0)

    def __init__(self, codes: tuple[int, int, int],
                 lo: int = -127, hi: int = 127) -> None:
        self.codes = tuple(int(c) for c in codes)
        self.lo, self.hi = lo, hi
        self.steps = 0
        self.done = False
        self.reason = ""
        self._slope = [self.SLOPE, self.SLOPE]          # red, blue
        self._last: tuple[tuple[int, int, int], tuple[float, float]] | None = None

    @staticmethod
    def _errors(sample_bgr) -> tuple[float, float] | None:
        """How far red and blue sit from green, in stops. Positive means
        the channel needs more gain."""
        b, g, r = (float(v) for v in sample_bgr)
        gr, _, gb = gains_from_means(b, g, r)
        if (gr, 1.0, gb) == UNITY and min(r, g, b) <= 0:
            return None
        return (float(np.log2(gr)), float(np.log2(gb)))

    def propose(self, sample_bgr, shares_bgr=None
                ) -> tuple[int, int, int] | None:
        if self.done:
            return None
        if self.steps >= self.MAX_STEPS:
            self.done, self.reason = True, "out of steps"
            return None
        errs = self._errors(sample_bgr)
        if errs is None:
            self.done, self.reason = True, "nothing to read"
            return None
        blown_b, blown_g, blown_r = clipped_channels(sample_bgr, shares_bgr)
        blind = blown_b or blown_g or blown_r
        if blind:
            # Which channels are blown decides the direction; the size is
            # a fixed stride because the ratio cannot be trusted.
            errs = (-self.BLIND_STEP_STOPS if blown_r else 0.0,
                    -self.BLIND_STEP_STOPS if blown_b else 0.0)
            if errs == (0.0, 0.0):
                # Only green is blown: nothing a balance can do about that.
                self.done, self.reason = True, "green is clipped"
                return None
        elif self._last is not None:
            # Learn the slope from what the previous step actually did.
            before_codes, before_errs = self._last
            for i, k in enumerate((0, 2)):
                dcode = self.codes[k] - before_codes[k]
                derr = before_errs[i] - errs[i]
                if dcode and derr:
                    measured = abs(dcode / derr)
                    if self.SLOPE_BOUNDS[0] <= measured <= self.SLOPE_BOUNDS[1]:
                        self._slope[i] = measured
        if not blind and all(abs(e) < self.DONE_STOPS for e in errs):
            self.done, self.reason = True, "neutral"
            return None

        r_code = self.codes[0] + int(round(errs[0] * self._slope[0]))
        b_code = self.codes[2] + int(round(errs[1] * self._slope[1]))
        proposed = (max(self.lo, min(self.hi, r_code)), self.codes[1],
                    max(self.lo, min(self.hi, b_code)))
        if proposed == self.codes:
            # Nothing moved: either the ask rounded to nothing, which is
            # neutral enough, or the clamp ate it, which is the edge.
            beyond = any(c < self.lo or c > self.hi for c in (r_code, b_code))
            self.done = True
            self.reason = "at the edge of the range" if beyond else "neutral"
            return None
        self._last = (self.codes, errs if not blind else errs)
        self.codes = proposed
        self.steps += 1
        return proposed
