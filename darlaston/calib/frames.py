"""Building the calibration frames themselves.

Three products, three different ways of being made:

  * **Dark** -- average N frames with the light off. Averaging is right here:
    the signal is constant and the noise is not, so sqrt(N) applies directly.
  * **Flat** -- *median* several blank fields at different stage positions.
    Median rather than mean, because the whole point is to reject debris that
    happens to be in one frame. Slide debris moves with the stage and cancels;
    sensor dust and the illumination field do not, which is exactly the
    separation wanted.
  * **White balance** -- measured from the flat, since a featureless illuminated
    field is neutral by definition. Not an estimate.
"""
from __future__ import annotations

import numpy as np


def average_frames(frames: list[np.ndarray]) -> np.ndarray:
    """Mean, in float64 then back.

    Summing uint16 frames in their own dtype overflows at four frames; doing it
    in float32 starts losing the low bits that a dark frame is entirely made
    of.
    """
    if not frames:
        raise ValueError("no frames to average")
    acc = np.zeros(frames[0].shape, np.float64)
    for f in frames:
        acc += f
    return acc / len(frames)


def median_frames(frames: list[np.ndarray]) -> np.ndarray:
    """Per-pixel median. Rejects anything present in a minority of frames."""
    if not frames:
        raise ValueError("no frames to median")
    return np.median(np.stack(frames).astype(np.float32), axis=0)


def bayer_normalise(flat: np.ndarray) -> np.ndarray:
    """Normalise a flat per Bayer phase.

    A single scalar is wrong on undemosaiced data: the four phases have
    different sensitivity -- measured at 807 to 2464 on a blank field, a factor
    of three -- so one norm bakes a 2x2 checkerboard into every corrected
    frame. ASTAP splits its flat norm four ways for the same reason.
    """
    out = np.asarray(flat, np.float32).copy()
    for dy in (0, 1):
        for dx in (0, 1):
            phase = out[dy::2, dx::2]
            out[dy::2, dx::2] = phase / max(float(phase.mean()), 1e-6)
    return out


def white_balance_from_flat(flat: np.ndarray, dark: np.ndarray | None = None
                            ) -> tuple[float, float, float]:
    """Gains that neutralise a blank field, normalised to green.

    Canonical GBRG: (0,0) and (1,1) are green, (0,1) blue, (1,0) red. A
    featureless illuminated field is neutral by definition, so this is a
    measurement. On a real frame it landed within 3% of the grey-world guess,
    which is reassuring about both.
    """
    f = np.asarray(flat, np.float32)
    if dark is not None:
        f = np.maximum(f - np.asarray(dark, np.float32), 0)
    g = (float(f[0::2, 0::2].mean()) + float(f[1::2, 1::2].mean())) / 2
    b = float(f[0::2, 1::2].mean())
    r = float(f[1::2, 0::2].mean())
    g = max(g, 1e-6)
    return (g / max(r, 1e-6), 1.0, g / max(b, 1e-6))


def defect_map(dark: np.ndarray, sigma: float = 12.0,
               white_level: int = 4095) -> np.ndarray:
    """Hot pixels, as coordinates rather than a whole frame to subtract.

    PHD2's approach, and the right one for an uncooled sensor: repairing a few
    thousand known-bad pixels is cheaper per frame than subtracting a master,
    and temperature-robust in a way a subtracted master is not.

    Two things make the threshold delicate. The spread must come from a robust
    estimator, because standard deviation on a frame that is almost entirely
    one value is dominated by the handful of outliers we are trying to find.
    And it needs an absolute floor: a master built by averaging has had its
    noise divided by sqrt(N), so a pure sigma rule tightens as the calibration
    gets *better* and eventually flags a large fraction of the sensor.

    At short exposures on an uncooled sensor there is almost no dark current
    and this legitimately finds nothing. Hot pixels show up in the long
    darkfield exposures, which is where the map earns its keep.
    """
    d = np.asarray(dark, np.float32)
    median = float(np.median(d))
    mad = float(np.median(np.abs(d - median))) * 1.4826      # -> sigma-equivalent
    floor = white_level * 0.002                              # about 8 DN at 12 bits
    threshold = max(median + sigma * mad, median + floor)
    ys, xs = np.nonzero(d > threshold)
    return np.stack([ys, xs], axis=1).astype(np.int32)


def apply_defects(frame: np.ndarray, defects: np.ndarray) -> np.ndarray:
    """Replace known-bad pixels with the median of their Bayer neighbours.

    Same-phase neighbours only: substituting a red site's value into a green
    one would be worse than the hot pixel.
    """
    if defects.size == 0:
        return frame
    out = frame.copy()
    h, w = frame.shape
    for y, x in defects:
        y0, y1 = max(0, y - 2), min(h, y + 3)
        x0, x1 = max(0, x - 2), min(w, x + 3)
        patch = frame[y0:y1:2, x0:x1:2] if (y - y0) % 2 == 0 else frame[y0 + 1:y1:2, x0:x1:2]
        if patch.size > 1:
            out[y, x] = np.median(patch)
    return out


def calibrate(raw: np.ndarray, dark: np.ndarray | None = None,
              flat: np.ndarray | None = None,
              defects: np.ndarray | None = None,
              white_level: int = 4095) -> np.ndarray:
    """(raw - dark) / flat, in the order that matters.

    Dark first, because the flat itself needs dark-subtracting before it means
    anything. Flat normalised per Bayer phase. Defects repaired last, so a hot
    pixel is not first amplified by a dim corner of the flat.
    """
    x = np.asarray(raw, np.float32)
    if dark is not None:
        x = np.maximum(x - np.asarray(dark, np.float32), 0.0)
    if flat is not None:
        f = np.asarray(flat, np.float32)
        if dark is not None:
            f = np.maximum(f - np.asarray(dark, np.float32), 0.0)
        x = x / np.maximum(bayer_normalise(f), 1e-3)
    if defects is not None and defects.size:
        x = apply_defects(x, defects)
    return np.clip(x, 0, white_level)
