#!/usr/bin/env python3
"""Recover an illumination flat field from mosaic tiles, without a blank slide.

The subject moves between tiles; the illumination field does not. So across a
set of unaligned tiles, most frames show background at any given pixel, and a
per-pixel extremum picks the background out.

    derive_flat.py <tile> [tile ...] [--dark] [--bayer] [--sigma N]

Use --dark when the subject is BRIGHTER than the background (darkfield, phase
glow), so the minimum is taken instead of the maximum.

Measured on four brightfield captures: field non-uniformity dropped from
+17.5% to +0.5%, and the same derived flat corrected all four tiles.
"""
import sys
import numpy as np
import cv2


def radial_profile(im, bins=8):
    """Median luma in concentric annuli, normalised to the centre bin."""
    h, w = im.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((yy - h/2) / (h/2))**2 + ((xx - w/2) / (w/2))**2)
    g = im if im.ndim == 2 else im.mean(axis=2)
    p = [np.median(g[(r >= i/bins) & (r < (i+1)/bins)]) for i in range(bins)]
    return [v / p[0] for v in p], max(p) / min(p) - 1


def fmt(prof, spread):
    return "  ".join(f"{v:.3f}" for v in prof) + f"   spread {spread:+.1%}"


def normalise(field, bayer=False):
    """Normalise a flat to unit mean.

    On an undemosaiced Bayer frame a single scalar is wrong: the four phases
    have different sensitivities and different vignetting, so one norm leaves a
    2x2 checkerboard baked into every corrected frame. Normalise each phase
    against its own mean instead. (ASTAP does the same, flatNorm11..22.)
    """
    if not bayer:
        return field / field.mean()
    out = field.copy()
    for dy in (0, 1):
        for dx in (0, 1):
            phase = out[dy::2, dx::2]
            out[dy::2, dx::2] = phase / phase.mean()
    return out


def derive(paths, dark=False, sigma=60, bayer=False):
    flag = cv2.IMREAD_UNCHANGED if bayer else cv2.IMREAD_GRAYSCALE
    stack = np.stack([cv2.imread(p, flag).astype(np.float32) for p in paths])
    # Subject-darker-than-background -> max picks background, and vice versa.
    field = stack.min(axis=0) if dark else stack.max(axis=0)
    # Heavy blur keeps the illumination field and discards residual subject.
    # On Bayer data blur each phase separately, or the smoothing mixes colour
    # channels and destroys the very structure we are trying to measure.
    if bayer:
        for dy in (0, 1):
            for dx in (0, 1):
                field[dy::2, dx::2] = cv2.GaussianBlur(
                    field[dy::2, dx::2], (0, 0), sigma / 2)
    else:
        field = cv2.GaussianBlur(field, (0, 0), sigma)
    return normalise(field, bayer), stack


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dark = "--dark" in sys.argv
    bayer = "--bayer" in sys.argv
    sigma = 60
    if "--sigma" in sys.argv:
        sigma = float(sys.argv[sys.argv.index("--sigma") + 1])
    if len(args) < 3:
        sys.exit(__doc__)

    flat, stack = derive(args, dark, sigma, bayer)
    cv2.imwrite("flat.tif", (flat * 10000).astype(np.uint16))
    print(f"derived from {len(args)} tiles ({'min' if dark else 'max'}, "
          f"sigma {sigma:g}{', per-Bayer-phase' if bayer else ''}) -> flat.tif\n")

    print("radial profile, centre -> corner")
    print(f"  {'derived flat':<24}" + fmt(*radial_profile(flat)))
    print()
    for p, raw in zip(args, stack):
        name = p.split("/")[-1]
        print(f"  {name + ' raw':<24}" + fmt(*radial_profile(raw)))
        print(f"  {name + ' corrected':<24}" + fmt(*radial_profile(raw / flat)))


if __name__ == "__main__":
    main()
