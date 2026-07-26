#!/usr/bin/env python3
"""Convert a raw Bayer capture to DNG for Darktable / RawTherapee.

    to_dng.py <raw_bayer.tif> [--dark FILE] [--flat FILE] [--black N] [--out FILE]

The point of this tool is to get out of the way. Everything subjective --
white balance, tone, colour -- belongs in a real raw developer, not here. This
writes the sensor data with correct enough metadata that Darktable opens it
sensibly and hands you the sliders.

Notes on what it does and does not claim:

  * Orientation is handled by rawio, which verifies it from the Bayer phases
    rather than assuming, so the declared GBRG pattern is always valid.
  * BlackLevel comes from a dark frame if given, otherwise from --black,
    otherwise 0. A real dark frame is strongly preferred: this sensor has no
    TEC, so the offset moves with ambient temperature.
  * AsShotNeutral is MEASURED when --flat is given -- a featureless illuminated
    field is neutral by definition. Without one it falls back to a grey-world
    estimate, which on a test frame here landed within 3% of the measurement.
    Only the balance is taken from the flat; the illumination field itself is
    not something DNG can carry.
  * ColorMatrix1 is PROVISIONAL. There is no published colour matrix for this
    sensor, and under a halogen lamp through phase optics the notion is
    shakier still. It is set to a neutral starting point so Darktable renders
    predictably; correct it there, or measure one from a colour target later.
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from pidng.core import RAW2DNG, DNGTags, Tag
from pidng.defs import (CalibrationIlluminant, CFAPattern, DNGVersion,
                        Orientation, PhotometricInterpretation,
                        PreviewColorSpace)

sys.path.insert(0, str(Path(__file__).parent))
import rawio  # noqa: E402

WHITE_LEVEL = rawio.WHITE_LEVEL

# Provisional. Row-major 3x3 as (numerator, denominator) pairs, XYZ->camera.
# A neutral placeholder, not a measurement -- see the module docstring.
COLOR_MATRIX = [[1, 1], [0, 1], [0, 1],
                [0, 1], [1, 1], [0, 1],
                [0, 1], [0, 1], [1, 1]]


def grey_world_neutral(raw):
    """AsShotNeutral estimate from the Bayer phases.

    After the flip the pattern is GBRG, so phase (0,0) and (1,1) are green,
    (0,1) is blue and (1,0) is red. DNG wants the *reciprocal* of the gains
    that would neutralise the image, normalised to green.
    """
    g = (raw[0::2, 0::2].mean() + raw[1::2, 1::2].mean()) / 2
    b = raw[0::2, 1::2].mean()
    r = raw[1::2, 0::2].mean()
    n = np.array([r, b], np.float64) / max(g, 1e-6)
    # Clamp to something sane so a wild frame cannot produce a broken file.
    n = np.clip(n, 0.05, 20.0)
    return [[int(round(n[0] * 10000)), 10000],
            [10000, 10000],
            [int(round(n[1] * 10000)), 10000]]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("raw")
    p.add_argument("--dark", help="dark frame; its mean becomes BlackLevel")
    p.add_argument("--flat", help="flat field; used only to measure white "
                                  "balance, not applied to the pixels")
    p.add_argument("--black", type=int, default=None)
    p.add_argument("--out")
    a = p.parse_args()

    # rawio puts every frame in canonical orientation, verified from the Bayer
    # phases rather than assumed, so the declared GBRG pattern is always valid.
    raw, _ = rawio.read_raw(a.raw)
    if raw.dtype != np.uint16:
        sys.exit(f"expected 16-bit data, got {raw.dtype}")
    h, w = raw.shape

    dark = rawio.read_raw(a.dark)[0] if a.dark else None
    if dark is not None:
        black = int(round(float(dark.mean())))
        src = f"dark frame mean ({Path(a.dark).name})"
    elif a.black is not None:
        black, src = a.black, "--black"
    else:
        black, src = 0, "default"

    # A featureless illuminated field is neutral by definition, so a flat gives
    # a measured white balance rather than an estimate. Only the balance is
    # taken from it -- the illumination field itself is Darktable's problem or
    # a later calibration step, not something DNG can carry.
    if a.flat:
        gr, gg, gb = rawio.white_balance_from_flat(rawio.read_raw(a.flat)[0],
                                                   dark)
        neutral = [[int(round(10000 / gr)), 10000], [10000, 10000],
                   [int(round(10000 / gb)), 10000]]
        wb_src = f"measured from {Path(a.flat).name}"
    else:
        neutral = grey_world_neutral(raw)
        wb_src = "grey-world estimate from the frame itself"

    t = DNGTags()
    t.set(Tag.ImageWidth, w)
    t.set(Tag.ImageLength, h)
    t.set(Tag.TileWidth, w)
    t.set(Tag.TileLength, h)
    t.set(Tag.Orientation, Orientation.Horizontal)
    t.set(Tag.PhotometricInterpretation, PhotometricInterpretation.Color_Filter_Array)
    t.set(Tag.SamplesPerPixel, 1)
    t.set(Tag.BitsPerSample, 16)
    t.set(Tag.CFARepeatPatternDim, [2, 2])
    t.set(Tag.CFAPattern, CFAPattern.GBRG)
    t.set(Tag.BlackLevel, black)
    t.set(Tag.WhiteLevel, WHITE_LEVEL)
    t.set(Tag.ColorMatrix1, COLOR_MATRIX)
    t.set(Tag.CalibrationIlluminant1, CalibrationIlluminant.Standard_Light_A)
    t.set(Tag.AsShotNeutral, neutral)
    t.set(Tag.BaselineExposure, [[0, 1]])
    t.set(Tag.Make, "ToupTek")
    t.set(Tag.Model, "E3ISPM20000KPA")
    t.set(Tag.DNGVersion, DNGVersion.V1_4)
    t.set(Tag.PreviewColorSpace, PreviewColorSpace.sRGB)

    out = Path(a.out) if a.out else Path(a.raw).with_suffix("")
    conv = RAW2DNG()
    conv.options(t, path="", compress=False)
    conv.convert(raw, filename=str(out))
    written = out.with_suffix(".dng")
    print(f"wrote {written}  ({written.stat().st_size/1e6:.1f} MB)")
    print(f"  {w} x {h}, GBRG, black {black} ({src}), white {WHITE_LEVEL}")
    print(f"  white balance: {wb_src}")
    print("  ColorMatrix1 is provisional -- correct it in Darktable")


if __name__ == "__main__":
    main()
