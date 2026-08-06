"""Writing DNG: the vocabulary, over our own TIFF writer.

Two shapes, both verified end to end against darktable-cli 5.4.1:

  * **Bayer DNG** for a single frame -- the sensor's own mosaic, so the full raw
    pipeline stays available, written as packed 12-bit because that is what
    the sensor actually produces.
  * **Linear DNG** for anything stacked or stitched, which is demosaiced by
    then but still deserves to arrive in a raw developer with white balance
    live and non-destructive.

pidng is gone. It got us to first light and then set four limits at once: the
full image in IFD0 with no SubIFD to hang a preview from, roughly 3.2 GB of
working memory to write a 1.6 GB file, uncompressed 16-bit only, and no path
past 4 GB. `process/tiff.py` replaces it; this module is now only the DNG
*vocabulary* -- which tags a raw developer needs, and what we put in them.

Nothing outside camera/ now depends on anything but numpy, OpenCV and Qt.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from . import tiff as T
from .metadata import CaptureMetadata
from .tiff import DngWriter, make_preview

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



def _ratio(value: float) -> list[int]:
    """A rational with enough denominator to survive the value.

    A fixed 1/10000 is fine for an f-number and destroys a working distance:
    0.6 mm is 0.0006 m, which rounds to 6/10000 and reads as "0.00 m" in
    every viewer. The denominator scales with how small the number is, and
    stays inside the unsigned 32 bits TIFF allows for both halves.
    """
    if value == 0:
        return [0, 1]
    magnitude = abs(value)
    if magnitude >= 1000:
        den = 100
    elif magnitude >= 1:
        den = 10000
    elif magnitude >= 0.001:
        den = 10_000_000
    else:
        den = 1_000_000_000
    num = int(round(value * den))
    # Trim the common factors of ten so the stored form is the tidiest one
    # that still says the same thing.
    while den > 1 and num % 10 == 0 and den % 10 == 0:
        num //= 10
        den //= 10
    return [num, den]


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
    # Omitted when unknown. It used to fall back to "darlaston", which
    # names the software rather than the manufacturer and is a claim
    # about hardware we did not make.
    if meta.make:
        w.add(T.MAKE, T.ASCII, meta.make)
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
    if meta.focal_length_mm:
        w.add(T.FOCAL_LENGTH, T.RATIONAL, [_ratio(meta.focal_length_mm)],
              where="exif")
    if meta.f_number:
        w.add(T.F_NUMBER, T.RATIONAL, [_ratio(meta.f_number)], where="exif")
    if meta.lens_model:
        w.add(T.LENS_MODEL, T.ASCII, meta.lens_model, where="exif")
    if meta.lens_make:
        w.add(T.LENS_MAKE, T.ASCII, meta.lens_make, where="exif")
    if meta.lens_serial:
        w.add(T.LENS_SERIAL, T.ASCII, meta.lens_serial, where="exif")
    if meta.focal_length_mm and meta.f_number:
        # A prime, so both ends of the zoom range are the same number. The
        # tag is four rationals: min focal, max focal, min f, max f.
        f, n = meta.focal_length_mm, meta.f_number
        w.add(T.LENS_SPECIFICATION, T.RATIONAL,
              [_ratio(f), _ratio(f), _ratio(n), _ratio(n)], where="exif")

    # The scale of the thing. Pixels per millimetre at the sensor, which
    # divided by magnification is how much slide one pixel covers.
    if meta.focal_plane_per_mm:
        # Written per centimetre with unit 3. Unit 4 (millimetre) exists and
        # would take the number as it stands, but centimetre is the value
        # every reader honours, and a scale nobody can read is not a scale.
        per_cm = meta.focal_plane_per_mm * 10.0
        w.add(T.FOCAL_PLANE_X_RES, T.RATIONAL, [_ratio(per_cm)], where="exif")
        w.add(T.FOCAL_PLANE_Y_RES, T.RATIONAL, [_ratio(per_cm)], where="exif")
        w.add(T.FOCAL_PLANE_RES_UNIT, T.SHORT, 3, where="exif")
    if meta.subject_distance_m:
        w.add(T.SUBJECT_DISTANCE, T.RATIONAL,
              [_ratio(meta.subject_distance_m)], where="exif")
    if meta.light_source:
        w.add(T.LIGHT_SOURCE, T.SHORT, int(meta.light_source), where="exif")
    if meta.image_number is not None:
        # IFD0, not the Exif IFD: ImageNumber is TIFF/EP, and readers look
        # for it beside Make and Model rather than beside ExposureTime.
        w.add(T.IMAGE_NUMBER, T.LONG, int(meta.image_number))
    if meta.unique_id:
        w.add(T.IMAGE_UNIQUE_ID, T.ASCII, meta.unique_id, where="exif")

    # Constants that are true of every capture this application makes, and
    # each one stops a reader guessing. Auto-exposure and auto-white-balance
    # are switched off deliberately -- they would make frames incomparable,
    # which breaks stacking and mosaicking both -- so "manual" is a fact.
    w.add(T.EXPOSURE_PROGRAM, T.SHORT, 1, where="exif")           # manual
    w.add(T.EXPOSURE_MODE, T.SHORT, 1, where="exif")              # manual
    w.add(T.WHITE_BALANCE_MODE, T.SHORT, 1, where="exif")         # manual
    w.add(T.FLASH, T.SHORT, 0x20, where="exif")                   # none fitted
    w.add(T.SENSING_METHOD, T.SHORT, 2, where="exif")             # one-chip
    w.add(T.FILE_SOURCE, T.UNDEFINED, b"\x03", where="exif")      # DSC
    w.add(T.SCENE_TYPE, T.UNDEFINED, b"\x01", where="exif")       # direct
    w.add(T.CUSTOM_RENDERED, T.SHORT, 0, where="exif")            # normal
    w.add(T.METERING_MODE, T.SHORT, 0, where="exif")              # unknown
    if meta.iso:
        # 0 none, 1 low gain up, 2 high gain up. Above 4x is "high" by any
        # reading, and this is the field that warns a developer the noise
        # floor is lifted.
        w.add(T.GAIN_CONTROL, T.SHORT,
              0 if meta.iso <= 100 else (1 if meta.iso < 400 else 2),
              where="exif")

    stamp = datetime.now().strftime("%Y:%m:%d %H:%M:%S")
    w.add(T.DATETIME_ORIGINAL, T.ASCII, stamp, where="exif")
    w.add(T.DATETIME_DIGITIZED, T.ASCII, stamp, where="exif")
    if meta.subsec:
        # A timelapse at one frame a second orders wrongly without this.
        w.add(T.SUBSEC_TIME_ORIGINAL, T.ASCII, meta.subsec, where="exif")
        w.add(T.SUBSEC_TIME_DIGITIZED, T.ASCII, meta.subsec, where="exif")
    if meta.artist:
        w.add(T.ARTIST, T.ASCII, meta.artist)
    if meta.copyright:
        w.add(T.COPYRIGHT, T.ASCII, meta.copyright)
    if meta.comment:
        w.add(T.USER_COMMENT, T.UNDEFINED,
              _ASCII_PREFIX + meta.comment.encode("ascii", "ignore"),
              where="exif")


def _version() -> str:
    from .. import __version__
    return __version__


def write_bayer_streamed(path: Path, rows, height: int, width: int, *,
                         preview: np.ndarray,
                         pattern: str | None = "GBRG", black: int = 0,
                         white: int = WHITE_LEVEL,
                         neutral: tuple[float, float, float] = (1.0, 1.0, 1.0),
                         meta: CaptureMetadata | None = None,
                         bits: int = 16, compress: bool = False,
                         progress=None) -> Path:
    """A single-plane DNG written strip by strip, with a preview.

    `pattern` of None means the sensor is monochrome: no CFA tags are
    written and the photometric becomes plain black-is-zero. Roughly a
    quarter of ToupTek's microscopy range is mono, and a greyscale frame
    labelled with a Bayer pattern is not merely wrong -- every developer
    that opens it will demosaic noise into colour and no part of the
    result means anything.
    """
    path = Path(path).with_suffix(".dng")
    path.parent.mkdir(parents=True, exist_ok=True)
    mono = pattern is None
    w = DngWriter(path, width, height, samples=1, bits=bits,
                  photometric=(T.PHOTO_BLACK_IS_ZERO if mono
                               else T.PHOTO_CFA),
                  compression=(T.COMPRESSION_DEFLATE if compress
                               else T.COMPRESSION_NONE))
    w.set_preview(preview)
    _our_tags(w, black, white, (1.0, 1.0, 1.0) if mono else neutral, meta)
    if not mono:
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
