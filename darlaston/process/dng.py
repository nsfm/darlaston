"""Writing DNG.

Two shapes, both verified end to end against darktable-cli 5.4.1:

  * **Bayer DNG** for a single frame — the sensor's own mosaic, so the full raw
    pipeline stays available.
  * **Linear DNG** for anything stacked or stitched, which is demosaiced by
    then but still deserves to arrive in a raw developer with white balance
    live and non-destructive.

Compression is not available. pidng writes it and rawspeed refuses to read it:
a component-count mismatch on linear, and unsupported predictor 6 on Bayer.

pidng also exposes no UserComment tag and no ISO field, so the structured
key=value comment and the analogue gain cannot be written through it. The
human-readable summary carries the important part in ImageDescription; the
full structured set would need an exiftool pass afterwards, which is not worth
a dependency yet.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from pidng.core import RAW2DNG, DNGTags, Tag
from pidng.defs import (CalibrationIlluminant, CFAPattern, DNGVersion,
                        Orientation, PhotometricInterpretation,
                        PreviewColorSpace)

from .metadata import CaptureMetadata

WHITE_LEVEL = 4095

#: Provisional, and honestly so. There is no published colour matrix for this
#: sensor, and under a halogen lamp through phase optics the notion is shakier
#: still. Neutral, so Darktable renders predictably and the user corrects there.
NEUTRAL_MATRIX = [[1, 1], [0, 1], [0, 1],
                  [0, 1], [1, 1], [0, 1],
                  [0, 1], [0, 1], [1, 1]]

_CFA = {"GBRG": CFAPattern.GBRG, "GRBG": CFAPattern.GRBG,
        "RGGB": CFAPattern.RGGB, "BGGR": CFAPattern.BGGR}


def _ratio(value: float) -> list[int]:
    return [int(round(value * 10000)), 10000]


def _base_tags(w: int, h: int, black: int, meta: CaptureMetadata | None,
               neutral: tuple[float, float, float]) -> DNGTags:
    t = DNGTags()
    t.set(Tag.ImageWidth, w)
    t.set(Tag.ImageLength, h)
    t.set(Tag.TileWidth, w)
    t.set(Tag.TileLength, h)
    t.set(Tag.Orientation, Orientation.Horizontal)
    t.set(Tag.BlackLevel, black)
    t.set(Tag.WhiteLevel, WHITE_LEVEL)
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
                meta: CaptureMetadata | None = None) -> Path:
    """One sensor frame, mosaic intact. `raw` must already be canonical."""
    if raw.dtype != np.uint16:
        raise ValueError(f"expected 16-bit data, got {raw.dtype}")
    h, w = raw.shape
    t = _base_tags(w, h, black, meta, neutral or grey_world_neutral(raw))
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
