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
#: Eight stops either way is far more than any real illuminant needs and
#: still leaves room for a deeply orange halogen lamp at low volts.
MAX_GAIN = 16.0


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
    if min(r, g, b) <= 0:
        return UNITY
    return sane((g / r, 1.0, g / b))


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
    ramp = np.arange(256, dtype=np.float32)
    table = np.empty((1, 256, 3), np.uint8)
    for channel, gain in enumerate((b, g, r)):        # cv2 order
        table[0, :, channel] = np.clip(ramp * gain + 0.5, 0, 255)
    return table


def applied(bgr: np.ndarray, table: np.ndarray | None) -> np.ndarray:
    """The frame with the balance on it. Always a fresh array."""
    if table is None:
        return bgr.copy()
    return cv2.LUT(bgr, table)
