"""Writing DNG.

Two shapes, both verified end to end against darktable-cli 5.4.1:

  * **Bayer DNG** for a single frame — the sensor's own mosaic, so the full raw
    pipeline stays available.
  * **Linear DNG** for anything stacked or stitched, which is demosaiced by
    then but still deserves to arrive in a raw developer with white balance
    live and non-destructive.

Compression is not available. pidng writes it and rawspeed refuses to read it:
a component-count mismatch on linear, and unsupported predictor 6 on Bayer.

pidng names no UserComment or ISO tag, but its `Tag` class is only a set of
`(id, Type)` tuples and `DNGTags.set` accepts any of them -- so the two are
defined here rather than reached for through exiftool. Verified by reading them
back out. Keeping the dependency set small is worth a few lines.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from pidng.core import RAW2DNG, DNGTags, Tag
from pidng.defs import (CalibrationIlluminant, CFAPattern, DNGVersion,
                        Orientation, PhotometricInterpretation,
                        PreviewColorSpace)

from pidng.dng import Type

from .metadata import CaptureMetadata

#: Not named by pidng, but its Tag entries are just (id, Type) pairs and
#: DNGTags.set takes any of them.
USER_COMMENT = (37510, Type.Undefined)     # 0x9286
ISO_SPEED = (34855, Type.Short)            # 0x8827

#: UserComment is EXIF UNDEFINED and carries an 8-byte character-set prefix.
_ASCII_PREFIX = b"ASCII\x00\x00\x00"

WHITE_LEVEL = 4095

#: XYZ -> sRGB (D65). Provisional, and honestly so: there is no published
#: colour matrix for this sensor, and under a halogen lamp through phase optics
#: the notion is shakier still.
#:
#: This says "assume the camera has sRGB primaries", which is wrong -- but the
#: previous placeholder was an identity matrix, which says "assume the camera's
#: primaries *are* XYZ", and that is much more wrong. It rendered every capture
#: with a magenta cast, verified side by side in darktable. A measured matrix
#: from a colour target would be better; this is the conventional default when
#: there is nothing better.
_XYZ_TO_SRGB = (3.2406, -1.5372, -0.4986,
                -0.9689, 1.8758, 0.0415,
                0.0557, -0.2040, 1.0570)
NEUTRAL_MATRIX = [[int(round(v * 10000)), 10000] for v in _XYZ_TO_SRGB]

_CFA = {"GBRG": CFAPattern.GBRG, "GRBG": CFAPattern.GRBG,
        "RGGB": CFAPattern.RGGB, "BGGR": CFAPattern.BGGR}


def _ratio(value: float) -> list[int]:
    return [int(round(value * 10000)), 10000]


def _base_tags(w: int, h: int, black: int, meta: CaptureMetadata | None,
               neutral: tuple[float, float, float],
               white: int = WHITE_LEVEL) -> DNGTags:
    t = DNGTags()
    t.set(Tag.ImageWidth, w)
    t.set(Tag.ImageLength, h)
    t.set(Tag.TileWidth, w)
    t.set(Tag.TileLength, h)
    t.set(Tag.Orientation, Orientation.Horizontal)
    t.set(Tag.BlackLevel, black)
    t.set(Tag.WhiteLevel, white)
    t.set(Tag.ColorMatrix1, NEUTRAL_MATRIX)
    t.set(Tag.CalibrationIlluminant1, CalibrationIlluminant.Standard_Light_A)
    t.set(Tag.AsShotNeutral, [_ratio(neutral[0]), _ratio(neutral[1]),
                              _ratio(neutral[2])])
    t.set(Tag.DNGVersion, DNGVersion.V1_4)
    t.set(Tag.PreviewColorSpace, PreviewColorSpace.sRGB)
    if meta is not None:
        t.set(Tag.Make, meta.make)
        t.set(Tag.Model, meta.model or "camera")
        t.set(Tag.UniqueCameraModel, meta.unique_camera_model or meta.model)
        if meta.description:
            t.set(Tag.ImageDescription, meta.description)
        if meta.software:
            t.set(Tag.Software, meta.software)
        if meta.serial:
            t.set(Tag.CameraSerialNumber, meta.serial)
        if meta.lens_model:
            # The objective, where a photographer reads the lens.
            t.set(Tag.EXIFPhotoLensModel, meta.lens_model)
        if meta.exposure_seconds:
            # Rational tags take a list of pairs, not a flat pair.
            t.set(Tag.ExposureTime, [_ratio(meta.exposure_seconds)])
        t.set(Tag.DateTimeOriginal, datetime.now().strftime("%Y:%m:%d %H:%M:%S"))
        if meta.iso:
            # Gain is a multiplier on an already-collected signal, which is
            # exactly what ISO describes.
            t.set(ISO_SPEED, int(meta.iso))
        if meta.comment:
            t.set(USER_COMMENT,
                  list(_ASCII_PREFIX + meta.comment.encode("ascii", "ignore")))
    return t


def grey_world_neutral(raw: np.ndarray) -> tuple[float, float, float]:
    """AsShotNeutral estimate from the Bayer phases of a canonical GBRG frame.

    A starting point so the file opens at something reasonable rather than
    violently green. A raw capture of a blank field gives the real answer, and
    on a test frame the estimate landed within 3% of it.
    """
    g = (float(raw[0::2, 0::2].mean()) + float(raw[1::2, 1::2].mean())) / 2
    b = float(raw[0::2, 1::2].mean())
    r = float(raw[1::2, 0::2].mean())
    g = max(g, 1e-6)
    return (min(max(r / g, 0.05), 20.0), 1.0, min(max(b / g, 0.05), 20.0))


def write_bayer(path: Path, raw: np.ndarray, *, pattern: str = "GBRG",
                black: int = 0, neutral: tuple[float, float, float] | None = None,
                meta: CaptureMetadata | None = None,
                white: int = WHITE_LEVEL) -> Path:
    """One sensor frame, mosaic intact. `raw` must already be canonical.

    `white` exists for averaged captures: the mean of N frames carries real
    precision below one 12-bit LSB, and scaling it up rather than rounding it
    back to 12 bits is the difference between keeping the SNR that was just
    paid for and quantising it away.
    """
    if raw.dtype != np.uint16:
        raise ValueError(f"expected 16-bit data, got {raw.dtype}")
    h, w = raw.shape
    t = _base_tags(w, h, black, meta, neutral or grey_world_neutral(raw),
                   white=white)
    t.set(Tag.PhotometricInterpretation,
          PhotometricInterpretation.Color_Filter_Array)
    t.set(Tag.SamplesPerPixel, 1)
    t.set(Tag.BitsPerSample, 16)
    t.set(Tag.CFARepeatPatternDim, [2, 2])
    t.set(Tag.CFAPattern, _CFA.get(pattern, CFAPattern.GBRG))
    return _convert(path, np.ascontiguousarray(raw), t)


def write_linear(path: Path, rgb: np.ndarray, *, black: int = 0,
                 neutral: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 meta: CaptureMetadata | None = None) -> Path:
    """A stacked or stitched result, demosaiced but still linear.

    Keeps the composite inside Darktable's raw pipeline with white balance
    still live, which a TIFF would not.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("linear DNG wants an (h, w, 3) array")
    h, w = rgb.shape[:2]
    t = _base_tags(w, h, black, meta, neutral)
    t.set(Tag.PhotometricInterpretation, PhotometricInterpretation.Linear_Raw)
    t.set(Tag.SamplesPerPixel, 3)
    t.set(Tag.BitsPerSample, [16, 16, 16])
    return _convert(path, np.ascontiguousarray(rgb), t)


def _convert(path: Path, data: np.ndarray, tags: DNGTags) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conv = RAW2DNG()
    # Uncompressed deliberately -- see the module docstring.
    conv.options(tags, path="", compress=False)
    conv.convert(data, filename=str(path.with_suffix("")))
    return path.with_suffix(".dng")
