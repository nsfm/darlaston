"""Writing TIFF, and therefore DNG, ourselves.

pidng got us to first light and then became the limit. It writes the full
image straight into IFD0 with no SubIFD, so there is nowhere to hang a
preview and system thumbnailers refuse our files. It takes the whole image as
one array and costs about 3.2 GB of working memory on top of a 1.6 GB
composite. It writes uncompressed only, so a 12-bit frame occupies 16-bit
space -- 39.7 MB where 29.8 would do. And nothing in it can address past the
4 GB a classic TIFF allows.

Those are one problem: we do not control the bytes. So we write them.

This is a *narrow* writer, on purpose. It emits exactly the files darlaston
needs -- little-endian, stripped, uncompressed or Deflate, 8/12/16-bit -- and
knows nothing about the rest of the format. That is the same bargain as
`read_bayer_dng`: reading arbitrary DNGs is a dependency's job, reading and
writing our own is a few hundred lines.

Layout, in file order:

    header
    IFD0            reduced-resolution preview, and the camera-level tags
    IFD0 values
    SubIFD          the real image
    SubIFD values
    EXIF IFD
    preview pixels
    image strips    written one at a time from a generator

Strip offsets and byte counts cannot be known before the strips are written
when compression is on, so their arrays are placed at known positions and
patched afterwards. That keeps a single code path for compressed and
uncompressed rather than two that can drift apart.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Callable, Iterator, Sequence

import numpy as np

# ---- TIFF primitives -------------------------------------------------------

BYTE, ASCII, SHORT, LONG, RATIONAL, UNDEFINED, SRATIONAL = 1, 2, 3, 4, 5, 7, 10

_SIZES = {BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8,
          UNDEFINED: 1, SRATIONAL: 8}

# Tags, named where we use them. Numbers rather than an enum because the
# spec is written in numbers and a reader will be holding the spec.
NEW_SUBFILE_TYPE = 254
IMAGE_WIDTH = 256
IMAGE_LENGTH = 257
BITS_PER_SAMPLE = 258
COMPRESSION = 259
PHOTOMETRIC = 262
IMAGE_DESCRIPTION = 270
MAKE = 271
MODEL = 272
STRIP_OFFSETS = 273
ORIENTATION = 274
SAMPLES_PER_PIXEL = 277
ROWS_PER_STRIP = 278
STRIP_BYTE_COUNTS = 279
PLANAR_CONFIG = 284
SOFTWARE = 305
DATETIME = 306
SUB_IFDS = 330
EXIF_IFD = 34665
CFA_REPEAT_DIM = 33421
CFA_PATTERN = 33422
ARTIST = 315
COPYRIGHT = 33432
EXPOSURE_TIME = 33434
F_NUMBER = 33437
EXPOSURE_PROGRAM = 34850
ISO_SPEED = 34855
DATETIME_DIGITIZED = 36868
SUBJECT_DISTANCE = 37382
METERING_MODE = 37383
LIGHT_SOURCE = 37384
FLASH = 37385
FOCAL_LENGTH = 37386
#: EXIF and TIFF/EP both define focal-plane resolution and both chose
#: different numbers for it. TIFF/EP puts it at 0x920E-0x9210; EXIF, which is
#: what every reader actually parses inside the Exif IFD, puts it at
#: 0xA20E-0xA210. Writing the TIFF/EP numbers produces tags that are
#: structurally valid and that exiv2 silently skips -- measured, after doing
#: exactly that.
FOCAL_PLANE_X_RES = 41486
FOCAL_PLANE_Y_RES = 41487
FOCAL_PLANE_RES_UNIT = 41488
#: ImageNumber is a TIFF/EP tag and lives in IFD0, not the Exif IFD.
IMAGE_NUMBER = 37393
SUBSEC_TIME_ORIGINAL = 37521
SUBSEC_TIME_DIGITIZED = 37522
FILE_SOURCE = 41728
SCENE_TYPE = 41729
CUSTOM_RENDERED = 41985
EXPOSURE_MODE = 41986
WHITE_BALANCE_MODE = 41987
GAIN_CONTROL = 41991
SENSING_METHOD = 41495
IMAGE_UNIQUE_ID = 42016
LENS_SPECIFICATION = 42034
LENS_MAKE = 42035
LENS_SERIAL = 42037
DATETIME_ORIGINAL = 36867
USER_COMMENT = 37510
LENS_MODEL = 42036
DNG_VERSION = 50706
DNG_BACKWARD = 50707
UNIQUE_CAMERA_MODEL = 50708
CFA_PLANE_COLOR = 50710
CFA_LAYOUT = 50711
BLACK_LEVEL = 50714
WHITE_LEVEL = 50717
COLOR_MATRIX1 = 50721
CAMERA_SERIAL = 50735
CALIBRATION_ILLUMINANT1 = 50778
AS_SHOT_NEUTRAL = 50728

#: Compression values we emit.
#:
#: Deflate is implemented and round-trips through our own reader, but it is
#: **not usable for the files we ship**: measured against darktable 5.4.1,
#: rawspeed refuses it outright -- "Only float format is supported for
#: deflate-compressed data" -- because the DNG specification only allows
#: Deflate on floating-point samples. It stays here because the packing and
#: strip machinery is shared and the option is honest for our own use, but
#: nothing in the UI offers it. The size win belongs to 12-bit packing,
#: which darktable reads happily: measured on a real 20 MP frame, 39.9 MB
#: uncompressed, 30.0 MB packed, and only 27.0 MB with deflate on top -- so
#: the unreadable option was buying 10%, not a category change.
COMPRESSION_NONE = 1
COMPRESSION_DEFLATE = 8

#: Photometric values we emit.
PHOTO_RGB = 2
PHOTO_CFA = 32803
PHOTO_LINEAR_RAW = 34892

#: EXIF's UserComment is UNDEFINED and carries a character-set prefix.
_ASCII_PREFIX = b"ASCII\x00\x00\x00"


class _Tag:
    __slots__ = ("code", "kind", "count", "payload", "inline")

    def __init__(self, code: int, kind: int, values) -> None:
        self.code = code
        self.kind = kind
        if kind == ASCII:
            raw = values.encode("ascii", "replace") + b"\x00"
            self.count = len(raw)
            self.payload = raw
        elif kind == UNDEFINED:
            raw = bytes(values)
            self.count = len(raw)
            self.payload = raw
        elif kind in (RATIONAL, SRATIONAL):
            pairs = list(values)
            self.count = len(pairs)
            fmt = "<" + ("ii" if kind == SRATIONAL else "II") * self.count
            flat = [int(v) for pair in pairs for v in pair]
            self.payload = struct.pack(fmt, *flat)
        else:
            seq = list(values) if isinstance(values, (list, tuple)) else [values]
            self.count = len(seq)
            code_char = {BYTE: "B", SHORT: "H", LONG: "I"}[kind]
            self.payload = struct.pack("<" + code_char * self.count,
                                       *(int(v) for v in seq))
        self.inline = len(self.payload) <= 4


def _ifd_bytes(tags: Sequence[_Tag]) -> int:
    """Size of the IFD table itself: count, entries, next-pointer."""
    return 2 + 12 * len(tags) + 4


def _write_ifd(out, tags: list[_Tag], value_base: int, next_ifd: int = 0
               ) -> dict[int, int]:
    """Write an IFD and its out-of-line values. Returns tag -> value offset.

    The returned offsets are what makes patching possible: the strip arrays
    are written as placeholders and rewritten once the real sizes are known.
    """
    tags = sorted(tags, key=lambda t: t.code)   # TIFF requires ascending tags
    positions: dict[int, int] = {}
    out.write(struct.pack("<H", len(tags)))
    cursor = value_base
    for t in tags:
        if t.inline:
            positions[t.code] = out.tell() + 8
            body = t.payload + b"\x00" * (4 - len(t.payload))
            out.write(struct.pack("<HHI", t.code, t.kind, t.count) + body)
        else:
            positions[t.code] = cursor
            out.write(struct.pack("<HHII", t.code, t.kind, t.count, cursor))
            cursor += len(t.payload) + (len(t.payload) & 1)
    out.write(struct.pack("<I", next_ifd))
    for t in tags:
        if not t.inline:
            out.write(t.payload)
            if len(t.payload) & 1:
                out.write(b"\x00")
    return positions


# ---- packing ---------------------------------------------------------------

def pack_12(row: np.ndarray) -> bytes:
    """Two 12-bit samples into three bytes, big-endian within the pair.

    This is the packing DNG calls BitsPerSample 12, and it is where a quarter
    of the file size goes: our sensor is 12-bit and we have been storing it
    in 16, paying 4 bits of zeroes on every pixel.
    """
    flat = np.asarray(row, np.uint16).ravel()
    if flat.size & 1:
        flat = np.append(flat, np.uint16(0))
    a = flat[0::2].astype(np.uint32)
    b = flat[1::2].astype(np.uint32)
    out = np.empty((a.size, 3), np.uint8)
    out[:, 0] = (a >> 4) & 0xFF
    out[:, 1] = ((a & 0x0F) << 4) | ((b >> 8) & 0x0F)
    out[:, 2] = b & 0xFF
    return out.tobytes()


def _encode_strip(rows: np.ndarray, bits: int, compression: int) -> bytes:
    if bits == 12:
        raw = pack_12(rows)
    elif bits == 8:
        raw = np.ascontiguousarray(rows, np.uint8).tobytes()
    else:
        raw = np.ascontiguousarray(rows, np.uint16).tobytes()
    if compression == COMPRESSION_DEFLATE:
        # Level 6: measured on real sensor data, level 9 buys under 1% for
        # roughly three times the time, and this runs while the operator is
        # waiting to take the next photograph.
        return zlib.compress(raw, 6)
    return raw


# ---- the writer ------------------------------------------------------------

class DngWriter:
    """Streams a DNG to disk one strip at a time.

    `rows` is a callable taking (start, count) and returning that band of the
    image. Nothing requires the whole image to exist: a mosaic composite can
    hand back bands it renders on demand, and a captured frame can simply
    slice an array it already holds.
    """

    #: Rows per strip. Small enough that a strip of a 20000-pixel-wide
    #: composite is a few megabytes, large enough that the per-strip overhead
    #: and the compressor's window are not wasted.
    ROWS_PER_STRIP = 64

    def __init__(self, path: Path | str, width: int, height: int, *,
                 samples: int = 1, bits: int = 16,
                 photometric: int = PHOTO_CFA,
                 compression: int = COMPRESSION_NONE) -> None:
        self.path = Path(path)
        self.width, self.height = int(width), int(height)
        self.samples, self.bits = int(samples), int(bits)
        self.photometric = photometric
        self.compression = compression
        self.ifd0: list[_Tag] = []
        self.raw_tags: list[_Tag] = []
        self.exif: list[_Tag] = []
        self._preview: np.ndarray | None = None

    # ---- description -----------------------------------------------------

    def set_preview(self, rgb: np.ndarray) -> None:
        """The reduced-resolution image that goes in IFD0.

        This is the whole reason for writing our own: a conformant DNG puts a
        preview here and the real image in a SubIFD, which is what every
        thumbnailer and file manager looks for. pidng put the full image in
        IFD0 and left nowhere to hang one.
        """
        self._preview = np.ascontiguousarray(rgb, np.uint8)

    def add(self, code: int, kind: int, values, where: str = "ifd0") -> None:
        target = {"ifd0": self.ifd0, "raw": self.raw_tags,
                  "exif": self.exif}[where]
        target.append(_Tag(code, kind, values))

    # ---- writing ---------------------------------------------------------

    def write(self, rows: Callable[[int, int], np.ndarray],
              progress: Callable[[int, int], None] | None = None) -> Path:
        strips = (self.height + self.ROWS_PER_STRIP - 1) // self.ROWS_PER_STRIP

        pv = self._preview
        if pv is None:
            raise ValueError("a preview is required; that is the point")
        ph, pw = pv.shape[:2]

        ifd0 = list(self.ifd0) + [
            _Tag(NEW_SUBFILE_TYPE, LONG, 1),          # 1 = reduced resolution
            _Tag(IMAGE_WIDTH, LONG, pw),
            _Tag(IMAGE_LENGTH, LONG, ph),
            _Tag(BITS_PER_SAMPLE, SHORT, [8, 8, 8]),
            _Tag(COMPRESSION, SHORT, COMPRESSION_NONE),
            _Tag(PHOTOMETRIC, SHORT, PHOTO_RGB),
            _Tag(SAMPLES_PER_PIXEL, SHORT, 3),
            _Tag(ROWS_PER_STRIP, LONG, ph),
            _Tag(PLANAR_CONFIG, SHORT, 1),
            _Tag(ORIENTATION, SHORT, 1),
            _Tag(STRIP_OFFSETS, LONG, 0),             # patched
            _Tag(STRIP_BYTE_COUNTS, LONG, pv.nbytes),
            _Tag(SUB_IFDS, LONG, 0),                  # patched
            _Tag(EXIF_IFD, LONG, 0),                  # patched
        ]
        raw = list(self.raw_tags) + [
            _Tag(NEW_SUBFILE_TYPE, LONG, 0),          # 0 = the real thing
            _Tag(IMAGE_WIDTH, LONG, self.width),
            _Tag(IMAGE_LENGTH, LONG, self.height),
            _Tag(BITS_PER_SAMPLE, SHORT,
                 [self.bits] * self.samples if self.samples > 1
                 else self.bits),
            _Tag(COMPRESSION, SHORT, self.compression),
            _Tag(PHOTOMETRIC, SHORT, self.photometric),
            _Tag(SAMPLES_PER_PIXEL, SHORT, self.samples),
            _Tag(ROWS_PER_STRIP, LONG, self.ROWS_PER_STRIP),
            _Tag(PLANAR_CONFIG, SHORT, 1),
            _Tag(ORIENTATION, SHORT, 1),
            _Tag(STRIP_OFFSETS, LONG, [0] * strips),
            _Tag(STRIP_BYTE_COUNTS, LONG, [0] * strips),
        ]

        with open(self.path, "wb") as out:
            out.write(struct.pack("<2sHI", b"II", 42, 8))

            ifd0_at = out.tell()
            ifd0_values = ifd0_at + _ifd_bytes(ifd0)
            pos0 = _write_ifd(out, ifd0, ifd0_values)

            sub_at = out.tell()
            sub_values = sub_at + _ifd_bytes(raw)
            pos_raw = _write_ifd(out, raw, sub_values)

            exif_at = 0
            if self.exif:
                exif_at = out.tell()
                _write_ifd(out, self.exif, exif_at + _ifd_bytes(self.exif))

            preview_at = out.tell()
            out.write(pv.tobytes())

            # Strips, one at a time. This is the whole memory argument: the
            # writer never holds more than one strip beyond what the caller
            # chooses to keep.
            offsets, counts = [], []
            for i in range(strips):
                start = i * self.ROWS_PER_STRIP
                count = min(self.ROWS_PER_STRIP, self.height - start)
                blob = _encode_strip(rows(start, count), self.bits,
                                     self.compression)
                offsets.append(out.tell())
                counts.append(len(blob))
                out.write(blob)
                if progress is not None:
                    progress(i + 1, strips)

            # Patch what could not be known in advance.
            out.seek(pos0[STRIP_OFFSETS])
            out.write(struct.pack("<I", preview_at))
            out.seek(pos0[SUB_IFDS])
            out.write(struct.pack("<I", sub_at))
            if exif_at:
                out.seek(pos0[EXIF_IFD])
                out.write(struct.pack("<I", exif_at))
            out.seek(pos_raw[STRIP_OFFSETS])
            out.write(struct.pack("<" + "I" * strips, *offsets))
            out.seek(pos_raw[STRIP_BYTE_COUNTS])
            out.write(struct.pack("<" + "I" * strips, *counts))
        return self.path


def make_preview(image: np.ndarray, *, bayer: bool, width: int = 320,
                 black: int = 0, white: int = 4095,
                 neutral: tuple[float, float, float] | None = None
                 ) -> np.ndarray:
    """A small, viewable, roughly-corrected RGB image for IFD0.

    Deliberately crude -- gamma and a white balance, nothing else. Its job is
    to be recognisable in a file manager, not to be a rendering. A thumbnail
    that tries to be accurate would disagree with the developer's output and
    that is worse than one that is obviously a sketch.
    """
    import cv2

    a = np.asarray(image)
    if bayer:
        # Take one green per 2x2 cell rather than demosaicing: a preview does
        # not need the interpolation, and this is four times less work.
        g = ((a[0::2, 0::2].astype(np.float32)
              + a[1::2, 1::2].astype(np.float32)) / 2)
        r = a[1::2, 0::2].astype(np.float32)
        b = a[0::2, 1::2].astype(np.float32)
        rgb = np.dstack([r, g, b])
    else:
        rgb = a.astype(np.float32)
        if rgb.ndim == 2:
            rgb = np.dstack([rgb, rgb, rgb])

    h, w = rgb.shape[:2]
    scale = min(1.0, width / max(w, 1))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)

    rgb = np.clip((rgb - black) / max(white - black, 1), 0, 1)
    if neutral:
        for c, n in enumerate(neutral):
            if n > 1e-6:
                rgb[:, :, c] = np.clip(rgb[:, :, c] / n, 0, 1)
    # sRGB-ish gamma. A linear thumbnail of a microscope field looks black.
    rgb = np.power(rgb, 1 / 2.2)
    return (rgb * 255.0 + 0.5).astype(np.uint8)
