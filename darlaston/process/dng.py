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

from . import tiff as T
from .metadata import CaptureMetadata
from .tiff import DngWriter, make_preview

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


# --------------------------------------------------------------------------
# Our own writer
# --------------------------------------------------------------------------

def _our_tags(w: DngWriter, black: int, white: int,
              neutral: tuple[float, float, float],
              meta: CaptureMetadata | None) -> None:
    """The camera-level description, in IFD0 where DNG readers expect it."""
    w.add(T.DNG_VERSION, T.BYTE, [1, 4, 0, 0])
    w.add(T.DNG_BACKWARD, T.BYTE, [1, 1, 0, 0])
    w.add(T.COLOR_MATRIX1, T.SRATIONAL, NEUTRAL_MATRIX)
    w.add(T.CALIBRATION_ILLUMINANT1, T.SHORT, 17)      # Standard light A
    w.add(T.AS_SHOT_NEUTRAL, T.RATIONAL,
          [_ratio(neutral[0]), _ratio(neutral[1]), _ratio(neutral[2])])
    w.add(T.BLACK_LEVEL, T.SHORT, black, where="raw")
    w.add(T.WHITE_LEVEL, T.LONG, white, where="raw")
    w.add(T.SOFTWARE, T.ASCII, f"darlaston {_version()}")
    w.add(T.DATETIME, T.ASCII, datetime.now().strftime("%Y:%m:%d %H:%M:%S"))
    if meta is None:
        # rawspeed logs "Failed to find MAKE entry" and falls back to a
        # generic path without one. It still decodes, but a file that makes
        # a decoder complain is a file that will confuse someone later.
        w.add(T.MAKE, T.ASCII, "darlaston")
        w.add(T.MODEL, T.ASCII, "camera")
        w.add(T.UNIQUE_CAMERA_MODEL, T.ASCII, "camera")
        return
    w.add(T.MAKE, T.ASCII, meta.make or "darlaston")
    w.add(T.MODEL, T.ASCII, meta.model or "camera")
    w.add(T.UNIQUE_CAMERA_MODEL, T.ASCII,
          meta.unique_camera_model or meta.model or "camera")
    if meta.description:
        w.add(T.IMAGE_DESCRIPTION, T.ASCII, meta.description)
    if meta.serial:
        w.add(T.CAMERA_SERIAL, T.ASCII, meta.serial)
    if meta.exposure_seconds:
        w.add(T.EXPOSURE_TIME, T.RATIONAL, [_ratio(meta.exposure_seconds)],
              where="exif")
    if meta.iso:
        w.add(T.ISO_SPEED, T.SHORT, int(meta.iso), where="exif")
    if meta.lens_model:
        w.add(T.LENS_MODEL, T.ASCII, meta.lens_model, where="exif")
    w.add(T.DATETIME_ORIGINAL, T.ASCII,
          datetime.now().strftime("%Y:%m:%d %H:%M:%S"), where="exif")
    if meta.comment:
        w.add(T.USER_COMMENT, T.UNDEFINED,
              _ASCII_PREFIX + meta.comment.encode("ascii", "ignore"),
              where="exif")


def _version() -> str:
    from .. import __version__
    return __version__


def write_bayer_streamed(path: Path, rows, height: int, width: int, *,
                         preview: np.ndarray,
                         pattern: str = "GBRG", black: int = 0,
                         white: int = WHITE_LEVEL,
                         neutral: tuple[float, float, float] = (1.0, 1.0, 1.0),
                         meta: CaptureMetadata | None = None,
                         bits: int = 16, compress: bool = False,
                         progress=None) -> Path:
    """A Bayer DNG written strip by strip, with an embedded preview."""
    path = Path(path).with_suffix(".dng")
    path.parent.mkdir(parents=True, exist_ok=True)
    w = DngWriter(path, width, height, samples=1, bits=bits,
                  photometric=T.PHOTO_CFA,
                  compression=(T.COMPRESSION_DEFLATE if compress
                               else T.COMPRESSION_NONE))
    w.set_preview(preview)
    _our_tags(w, black, white, neutral, meta)
    w.add(T.CFA_REPEAT_DIM, T.SHORT, [2, 2], where="raw")
    w.add(T.CFA_PATTERN, T.BYTE, _CFA_BYTES.get(pattern, (1, 2, 0, 1)),
          where="raw")
    w.add(T.CFA_PLANE_COLOR, T.BYTE, [0, 1, 2], where="raw")
    w.add(T.CFA_LAYOUT, T.SHORT, 1, where="raw")
    return w.write(rows, progress=progress)


def write_linear_streamed(path: Path, rows, height: int, width: int, *,
                          preview: np.ndarray, black: int = 0,
                          white: int = 65535,
                          neutral: tuple[float, float, float] = (1.0, 1.0, 1.0),
                          meta: CaptureMetadata | None = None,
                          compress: bool = False, progress=None) -> Path:
    """A demosaiced-but-linear DNG, written strip by strip.

    This is the mosaic composite's output, and the reason the writer exists:
    a 272 MP composite cannot be handed to a library as one array.
    """
    path = Path(path).with_suffix(".dng")
    path.parent.mkdir(parents=True, exist_ok=True)
    w = DngWriter(path, width, height, samples=3, bits=16,
                  photometric=T.PHOTO_LINEAR_RAW,
                  compression=(T.COMPRESSION_DEFLATE if compress
                               else T.COMPRESSION_NONE))
    w.set_preview(preview)
    _our_tags(w, black, white, neutral, meta)
    return w.write(rows, progress=progress)


#: CFAPattern is four bytes of colour indices, 0=R 1=G 2=B, reading the 2x2
#: cell left to right then top to bottom.
_CFA_BYTES = {"GBRG": (1, 2, 0, 1), "GRBG": (1, 0, 2, 1),
              "RGGB": (0, 1, 1, 2), "BGGR": (2, 1, 1, 0)}
