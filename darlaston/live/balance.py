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
