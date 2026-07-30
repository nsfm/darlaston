"""Stitching a mosaic: registration first, composition second.

The design rests on one measured fact (DISCOVERY.md 10a): phase correlation's
confidence cannot detect a *wrong* match -- a decoy scored 0.80 against the
true match's 0.91. So nothing here searches. The manifest's dead-reckoned
positions predict every pairwise offset; registration only *refines* within a
bounded window around the prediction, and a refinement that wanders past the
bound is discarded in favour of the prediction it failed to improve. Panorama
software fails on diatoms precisely because it searches; this cannot, because
it never looks anywhere the stage did not say it went.

The tiles are read back by a deliberately minimal parser that handles exactly
the files darlaston writes -- little-endian, uncompressed, 16-bit, one full-
frame tile -- and refuses anything else by name. Reading arbitrary DNGs is a
dependency's job; reading our own is sixty lines.

Registration runs on half-resolution luma (the mean of each Bayer cell): no
demosaic, no channel choice, and the 2x binning suppresses the mosaic pattern
that would otherwise correlate with itself at even offsets.

Composition weights each tile by a raised-cosine window, so tile centres --
where the optics are honest -- dominate every seam, and the field-curvature
softness at tile edges never wins a blend. Weights normalise away where only
one tile covers a pixel.
"""
from __future__ import annotations

import struct
import zlib
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..capture.mosaic import MosaicSession, Tile
from . import dng

#: Refinement beyond this fraction of the overlap strip is not a refinement,
#: it is the wraparound or a decoy. The prediction stands instead.
MAX_RESIDUAL = 0.25
#: Below this correlation response the strip had nothing to say (blank
#: overlap, say); the prediction stands.
MIN_RESPONSE = 0.12
#: Tile pairs predicted to share less than this fraction of a frame are not
#: neighbours; there is nothing to register.
MIN_OVERLAP = 0.04


# ---- reading our own files -------------------------------------------------

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8}


def _read_ifd(data: bytes, at: int) -> dict[int, tuple[int, int, int]]:
    """tag -> (type, count, value-or-offset)."""
    (count,) = struct.unpack_from("<H", data, at)
    out = {}
    for i in range(count):
        tag, typ, cnt, val = struct.unpack_from("<HHII", data, at + 2 + i * 12)
        out[tag] = (typ, cnt, val)
    return out


def _values(data: bytes, entry: tuple[int, int, int]) -> list[int]:
    typ, cnt, val = entry
    size = _TYPE_SIZE.get(typ, 1) * cnt
    if size <= 4:
        raw = struct.pack("<I", val)[:size]
    else:
        raw = data[val:val + size]
    fmt = {1: "B", 3: "H", 4: "I"}.get(typ)
    if fmt is None:
        return [val]
    return list(struct.unpack_from("<" + fmt * cnt, raw))


def _unpack_12(raw: bytes, count: int) -> np.ndarray:
    b = np.frombuffer(raw, np.uint8)
    trips = b[:(b.size // 3) * 3].reshape(-1, 3).astype(np.uint16)
    a = (trips[:, 0] << 4) | (trips[:, 1] >> 4)
    c = ((trips[:, 1] & 0x0F) << 8) | trips[:, 2]
    out = np.empty(a.size * 2, np.uint16)
    out[0::2] = a
    out[1::2] = c
    return out[:count]


def read_bayer_dng(path: Path | str) -> np.ndarray:
    """Read a DNG that darlaston wrote. Nothing else is attempted.

    Handles both shapes we have produced: the old pidng files, where the full
    image sits in IFD0 as a single tile, and our own, where IFD0 holds a
    preview and the real image lives in a SubIFD as strips. Reading our own
    history is not optional -- there are mosaics on disk in the old shape.
    """
    data = Path(path).read_bytes()
    if data[:4] != b"II\x2a\x00":
        raise ValueError(f"{path}: not a little-endian TIFF darlaston wrote")
    (first,) = struct.unpack_from("<I", data, 4)
    ifd = _read_ifd(data, first)

    # Prefer a SubIFD when there is one: that is where our writer puts the
    # real image, IFD0 being the preview.
    if 330 in ifd:
        sub = _values(data, ifd[330])[0]
        ifd = _read_ifd(data, sub)

    w = _values(data, ifd[256])[0]
    h = _values(data, ifd[257])[0]
    bits = _values(data, ifd[258])[0] if 258 in ifd else 16
    comp = _values(data, ifd[259])[0] if 259 in ifd else 1
    if bits not in (12, 16):
        raise ValueError(f"{path}: {bits}-bit is not a shape we write")

    if 324 in ifd:                                    # tiled (old pidng)
        offsets = _values(data, ifd[324])
        counts = _values(data, ifd[325]) if 325 in ifd else [w * h * 2]
    else:
        offsets = _values(data, ifd[273])
        counts = _values(data, ifd[279])

    chunks = []
    for off, n in zip(offsets, counts):
        raw = data[off:off + n]
        if comp == 8:
            raw = zlib.decompress(raw)
        elif comp != 1:
            raise ValueError(f"{path}: compression {comp} is not ours")
        chunks.append(raw)
    blob = b"".join(chunks)
    if bits == 12:
        flat = _unpack_12(blob, w * h)
    else:
        flat = np.frombuffer(blob, np.uint16, count=w * h)
    return flat.reshape(h, w).copy()


def read_white_level(path: Path | str) -> int:
    """The DNG's declared white level, from wherever the raw image lives.

    Needed because a mosaic may mix sources: single-exposure tiles are
    12-bit (white 4095) while a stacked tile's merge is a 16-bit blend
    (white 65520). Compositing them without normalising would land every
    stacked tile four stops bright -- the same class of defect that once
    shipped every composite four stops dark.
    """
    data = Path(path).read_bytes()
    if data[:4] != b"II\x2a\x00":
        raise ValueError(f"{path}: not a little-endian TIFF darlaston wrote")
    (first,) = struct.unpack_from("<I", data, 4)
    ifd = _read_ifd(data, first)
    if 330 in ifd:
        ifd = _read_ifd(data, _values(data, ifd[330])[0])
    if 50717 in ifd:                                  # WhiteLevel
        return int(_values(data, ifd[50717])[0])
    bits = _values(data, ifd[258])[0] if 258 in ifd else 16
    return (1 << bits) - 1


def read_tile(session_dir: Path, tile: Tile) -> np.ndarray:
    """A mosaic tile's image, normalised to the 12-bit domain.

    Merges the tile's stack first if its `stacked.dng` does not exist yet
    -- a mosaic closed mid-merge, or merged on another day, still
    stitches. The import is local because process.stack reads DNGs through
    this module.
    """
    path = Path(session_dir) / tile.filename
    if tile.stack and not path.exists():
        from .stack import merge as merge_stack
        merge_stack(Path(session_dir) / tile.stack, output="bayer")
    raw = read_bayer_dng(path)
    white = read_white_level(path)
    if white != 4095:
        raw = np.clip(raw.astype(np.float32) * (4095.0 / white) + 0.5,
                      0, 4095).astype(np.uint16)
    return raw


def read_metadata(path: Path | str):
    """Recover a CaptureMetadata from a DNG darlaston wrote.

    The merge outputs carry the provenance of their sources -- who took it,
    through what, at what settings -- and the sources are our own files, so
    the tags are exactly where we put them. Parsing them back beats
    threading metadata through every session manifest, because the file is
    the one artefact guaranteed to still be there.
    """
    from .metadata import CaptureMetadata

    data = Path(path).read_bytes()
    if data[:4] != b"II\x2a\x00":
        return None
    (first,) = struct.unpack_from("<I", data, 4)
    ifd0 = _read_ifd(data, first)

    def ascii_of(entry):
        if entry is None:
            return ""
        typ, cnt, val = entry
        raw = data[val:val + cnt] if cnt > 4 else struct.pack("<I", val)[:cnt]
        return raw.split(b"\x00")[0].decode("ascii", "replace")

    def rational(entry):
        if entry is None:
            return None
        _t, _c, val = entry
        num, den = struct.unpack_from("<II", data, val)
        return num / den if den else None

    exif = {}
    # A file written without capture metadata still carries the EXIF pointer
    # tag, patched to zero -- following that read garbage past the end of
    # the buffer. Zero is not an offset.
    if 34665 in ifd0 and 0 < ifd0[34665][2] < len(data) - 2:
        exif = _read_ifd(data, ifd0[34665][2])

    comment = ""
    if 37510 in exif:
        _t, cnt, val = exif[37510]
        raw = data[val:val + cnt]
        if raw.startswith(b"ASCII\x00\x00\x00"):
            comment = raw[8:].split(b"\x00")[0].decode("ascii", "replace")

    iso = exif.get(34855)
    return CaptureMetadata(
        make=ascii_of(ifd0.get(271)) or "ToupTek",
        model=ascii_of(ifd0.get(272)),
        unique_camera_model=ascii_of(ifd0.get(50708)),
        serial=ascii_of(ifd0.get(50735)),
        lens_model=ascii_of(exif.get(42036)),
        lens_make=ascii_of(exif.get(42035)),
        lens_serial=ascii_of(exif.get(42037)),
        exposure_seconds=rational(exif.get(33434)),
        iso=iso[2] if iso else None,
        focal_length_mm=rational(exif.get(37386)),
        f_number=rational(exif.get(33437)),
        focal_plane_per_mm=((rational(exif.get(41486)) or 0) / 10) or None,
        subject_distance_m=rational(exif.get(37382)),
        light_source=(exif[37384][2] if 37384 in exif else None),
        description=ascii_of(ifd0.get(270)),
        artist=ascii_of(ifd0.get(315)),
        copyright=ascii_of(ifd0.get(33432)),
        comment=comment,
    )


def luma_half(raw: np.ndarray) -> np.ndarray:
    """Half-resolution luma: the mean of each 2x2 Bayer cell. Pattern-free,
    which matters -- the mosaic itself correlates at even offsets."""
    f = raw.astype(np.float32)
    return (f[0::2, 0::2] + f[0::2, 1::2] + f[1::2, 0::2] + f[1::2, 1::2]) / 4


# ---- registration -----------------------------------------------------------

@dataclass
class Edge:
    i: int                       # tile indices into the session list
    j: int
    offset: tuple[float, float]  # refined pos_j - pos_i, raw pixels
    response: float
    refined: bool                # False when the prediction stood


def _predicted_raw_positions(tiles: list[Tile], shapes: list[tuple[int, int]]
                             ) -> list[tuple[float, float] | None]:
    """Manifest positions are preview pixels; scale each to its tile's raw
    geometry. The axes scale separately -- the preview is not an isotropic
    resize of the sensor (5440/1824 != 3648/1216)."""
    out = []
    for tile, (h, w) in zip(tiles, shapes):
        if tile.pos is None or not tile.frame[0]:
            out.append(None)
            continue
        sx, sy = w / tile.frame[0], h / tile.frame[1]
        out.append((tile.pos[0] * sx, tile.pos[1] * sy))
    return out


def _refine_pair(a: np.ndarray, b: np.ndarray,
                 d: tuple[float, float]) -> tuple[tuple[float, float], float] | None:
    """Refine predicted offset d (raw px, b relative to a) on the overlap.

    Works on half-res luma; returns (refined_offset, response) or None when
    the overlap is degenerate.
    """
    h, w = a.shape
    dx, dy = d[0] / 2.0, d[1] / 2.0          # half-res coordinates
    x0, x1 = max(0.0, dx), min(float(w), dx + w)
    y0, y1 = max(0.0, dy), min(float(h), dy + h)
    if x1 - x0 < 32 or y1 - y0 < 32:
        return None
    ax0, ay0 = int(x0), int(y0)
    aw, ah = int(x1 - x0), int(y1 - y0)
    bx0, by0 = int(round(ax0 - dx)), int(round(ay0 - dy))
    bx0, by0 = max(0, bx0), max(0, by0)
    aw = min(aw, w - bx0, w - ax0)
    ah = min(ah, h - by0, h - ay0)
    if aw < 32 or ah < 32:
        return None
    sa = a[ay0:ay0 + ah, ax0:ax0 + aw]
    sb = b[by0:by0 + ah, bx0:bx0 + aw]

    # Bound the strip so a 40-tile run stays quick; the residual scales back.
    down = max(1, int(np.ceil(max(aw, ah) / 768)))
    if down > 1:
        sa = cv2.resize(sa, (aw // down, ah // down),
                        interpolation=cv2.INTER_AREA)
        sb = cv2.resize(sb, (aw // down, ah // down),
                        interpolation=cv2.INTER_AREA)
    hann = cv2.createHanningWindow((sa.shape[1], sa.shape[0]), cv2.CV_32F)
    # Copies, not views: OpenCV 5's phaseCorrelate windows its inputs IN
    # PLACE, and at down == 1 these are views into the tile lumas -- which
    # are reused to register the tile's other neighbours.
    (rx, ry), response = cv2.phaseCorrelate(sa.copy(), sb.copy(), hann)

    limit = MAX_RESIDUAL * min(sa.shape)
    if abs(rx) > limit or abs(ry) > limit or response < MIN_RESPONSE:
        return None
    # sb sits at prediction; content shifted +r in b means b is really at
    # prediction - r. Half-res residual, times the extra downsample, times
    # two back to raw pixels.
    scale = 2.0 * down
    return (d[0] - rx * scale, d[1] - ry * scale), float(response)


def register(session: MosaicSession, lumas: list[np.ndarray],
             shapes: list[tuple[int, int]]) -> tuple[list, list[Edge]]:
    """All neighbour pairs refined, then one global least squares.

    Positions are anchored at tile 0. Every edge contributes
    (p_j - p_i = offset) weighted by its correlation response; the solve is
    dense and tiny (2N unknowns for N tiles).
    """
    predicted = _predicted_raw_positions(session.tiles, shapes)
    n = len(session.tiles)
    edges: list[Edge] = []
    for i in range(n):
        for j in range(i + 1, n):
            if predicted[i] is None or predicted[j] is None:
                continue
            h, w = shapes[i]
            d = (predicted[j][0] - predicted[i][0],
                 predicted[j][1] - predicted[i][1])
            if (max(0.0, w - abs(d[0])) * max(0.0, h - abs(d[1]))) \
                    < MIN_OVERLAP * w * h:
                continue
            refined = _refine_pair(lumas[i], lumas[j], d)
            if refined is None:
                edges.append(Edge(i, j, d, 0.0, refined=False))
            else:
                edges.append(Edge(i, j, refined[0], refined[1], refined=True))

    # Least squares over x and y independently; anchor tile 0.
    positions = [list(p) if p is not None else [0.0, 0.0] for p in predicted]
    if edges:
        for axis in (0, 1):
            rows, rhs, wts = [], [], []
            rows.append([1.0 if k == 0 else 0.0 for k in range(n)])
            rhs.append(positions[0][axis])
            wts.append(10.0)                      # the anchor
            for e in edges:
                row = [0.0] * n
                row[e.i], row[e.j] = -1.0, 1.0
                rows.append(row)
                rhs.append(e.offset[axis])
                wts.append(max(e.response, 0.05))
            A = np.asarray(rows) * np.asarray(wts)[:, None]
            b = np.asarray(rhs) * np.asarray(wts)
            solved, *_ = np.linalg.lstsq(A, b, rcond=None)
            for k in range(n):
                positions[k][axis] = float(solved[k])
    return [tuple(p) for p in positions], edges


# ---- composition ------------------------------------------------------------

#: Width the illumination profile is estimated at. Illumination is smooth by
#: physics, so nothing is lost, and the decimation is the first line of
#: defence against subject detail.
_FLAT_W = 64
#: Fewer tiles than this and the across-tile median is not robust enough to
#: separate illumination from subject; better no correction than a wrong one.
_FLAT_MIN_TILES = 4
#: Order of the surface fitted to the median. High enough for a real lamp and
#: condenser, far too low to describe a diatom.
_FLAT_DEGREE = 4


def _poly_basis(gx: np.ndarray, gy: np.ndarray, degree: int) -> np.ndarray:
    return np.stack([gx ** i * gy ** j for i in range(degree + 1)
                     for j in range(degree + 1 - i)], axis=-1)


def estimate_flat(lumas: list[np.ndarray]) -> np.ndarray | None:
    """The illumination profile, from the tiles themselves.

    Every tile saw different slide through the same optics, so the one thing
    they share is how the field is lit. Normalise each tile by its own median
    (exposure and subject density drop out), take the per-pixel median across
    tiles (structure mostly drops out, because no diatom is in the same place
    twice), then **fit a smooth surface** to what remains.

    The fit is the load-bearing step, and it is not decoration. The plain
    median alone fails in a way that looks fine and is not: where a pixel is
    covered by subject in more than half the tiles, the median returns a dark
    value, so the profile grows *holes* -- and dividing by a hole puts a
    bright blob in the composite. Measured on synthetic tiles with a dense
    subject, 1.8% of pixels went that way and the resulting profile still
    passed a plain range check. A low-order surface cannot have holes at all:
    the worst it can do is be gently wrong, which shades a composite rather
    than spotting it.

    Outliers are rejected iteratively against a robust scale, so the
    contaminated pixels are dropped rather than averaged in. Measured against
    a known 25% vignette: 0.014 worst-case error with sparse subject and
    0.024 with dense, where the plain median gave 0.044 and 0.365.

    This is a *cosmetic* correction applied at composite time. The tiles on
    disk are untouched, and a flat from the calibration store remains the
    better answer -- but even tiles captured with one keep some residual
    shading if the lamp or condenser has moved since, which is exactly what
    this removes.
    """
    if len(lumas) < _FLAT_MIN_TILES:
        return None
    stack = []
    for luma in lumas:
        h, w = luma.shape
        small = cv2.resize(luma, (_FLAT_W, max(2, round(_FLAT_W * h / w))),
                           interpolation=cv2.INTER_AREA)
        mid = float(np.median(small))
        if mid <= 1e-6:
            continue
        stack.append(small / mid)
    if len(stack) < _FLAT_MIN_TILES:
        return None
    median = np.median(np.stack(stack), axis=0).astype(np.float64)

    h, w = median.shape
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float64)
    gx = gx / (w - 1) * 2 - 1
    gy = gy / (h - 1) * 2 - 1
    terms = ((_FLAT_DEGREE + 1) * (_FLAT_DEGREE + 2)) // 2
    A = _poly_basis(gx, gy, _FLAT_DEGREE).reshape(-1, terms)
    b = median.ravel()

    keep = np.ones(b.size, bool)
    coeffs = None
    for _ in range(4):
        coeffs, *_ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
        resid = b - A @ coeffs
        centre = np.median(resid[keep])
        scale = 1.4826 * np.median(np.abs(resid[keep] - centre))
        nxt = np.abs(resid - centre) < max(2.5 * scale, 1e-4)
        if nxt.sum() < terms * 4:
            break
        keep = nxt
    if coeffs is None:
        return None

    flat = (A @ coeffs).reshape(h, w).astype(np.float32)
    mean = float(flat.mean())
    if mean <= 1e-6:
        return None
    # Normalised to unity so dividing by it changes shading, not exposure.
    flat /= mean
    # A profile this extreme is not illumination; it is a subject that
    # outvoted the median. Refuse rather than invent.
    if float(flat.min()) < 0.4 or float(flat.max()) > 2.5:
        return None
    return flat


def _window(h: int, w: int) -> np.ndarray:
    """Raised cosine, floored: centres dominate seams, edges never win, and
    the floor keeps mosaic borders (where only one tile exists) defined."""
    wy = 0.5 - 0.5 * np.cos(2 * np.pi * (np.arange(h) + 0.5) / h)
    wx = 0.5 - 0.5 * np.cos(2 * np.pi * (np.arange(w) + 0.5) / w)
    return np.maximum(np.outer(wy, wx), 1e-4).astype(np.float32)


#: OpenCV demosaic code for a GBRG sensor. Per the OpenCV docs, the two
#: letters name the second and third columns of the *second* row -- GBRG's
#: second row is R G R G, columns two and three are G, R.
_DEMOSAIC = {"GBRG": cv2.COLOR_BayerGR2BGR, "GRBG": cv2.COLOR_BayerGB2BGR,
             "RGGB": cv2.COLOR_BayerBG2BGR, "BGGR": cv2.COLOR_BayerRG2BGR}


#: Classic TIFF -- which DNG is -- addresses file offsets with 32 bits, so a
#: single image cannot exceed 4 GB and the header must fit too. Refuse before
#: spending ten minutes producing a file no reader will open.
_TIFF_LIMIT = 4_200_000_000
#: Float accumulator budget for one band. Sets how tall the bands are; the
#: only cost of smaller bands is re-reading tiles that straddle more of them.
_BAND_BYTES = 256_000_000


def plan(positions: list[tuple[float, float]],
         shapes: list[tuple[int, int]], scale: float) -> dict:
    """Output geometry and cost for a scale, without doing any work.

    Exists so the operator is told "19716 × 13923, 275 MP, 1.6 GB" *before*
    committing, rather than discovering it when the machine starts swapping.
    """
    w0 = min(x - s[1] / 2 for (x, _), s in zip(positions, shapes))
    h0 = min(y - s[0] / 2 for (_, y), s in zip(positions, shapes))
    w1 = max(x + s[1] / 2 for (x, _), s in zip(positions, shapes))
    h1 = max(y + s[0] / 2 for (_, y), s in zip(positions, shapes))
    cw, ch = int((w1 - w0) * scale) + 2, int((h1 - h0) * scale) + 2
    out_bytes = cw * ch * 3 * 2
    return {"w": cw, "h": ch, "origin": (w0, h0),
            "megapixels": cw * ch / 1e6, "bytes": out_bytes,
            "fits": out_bytes < _TIFF_LIMIT}


def composite(session: MosaicSession, positions: list[tuple[float, float]],
              scale: float = 0.25, pattern: str = "GBRG",
              flat: np.ndarray | None = None,
              shapes: list[tuple[int, int]] | None = None,
              progress=None) -> Path:
    """Blend the tiles at their solved positions into one linear DNG.

    Composed in horizontal bands. A single full-resolution accumulator for a
    275 MP mosaic is 3.3 GB of float32 plus another 1.1 GB of weights, and
    that grows with the square of how much slide you cover -- so instead the
    canvas is filled a band at a time and only the tiles that intersect the
    band are ever loaded. Peak memory becomes the finished image plus one
    band, and a 40-tile mosaic stops being a question of how much RAM the
    machine has.

    The cost is re-reading tiles that straddle a band boundary, which is
    bounded and cheap next to not being able to do it at all.
    """
    if shapes is None:
        shapes = []
        for t in session.tiles:
            raw = read_tile(session.dir, t)
            shapes.append(raw.shape)
            del raw

    geom = plan(positions, shapes, scale)
    cw, ch = geom["w"], geom["h"]
    if not geom["fits"]:
        raise ValueError(
            f"{geom['megapixels']:.0f} MP is {geom['bytes'] / 1e9:.1f} GB, "
            f"past what a DNG can address (4 GB). Use a smaller scale.")
    w0, h0 = geom["origin"]
    del geom

    # Where each tile lands, so a band knows what to ask for.
    boxes = []
    for pos, (th, tw) in zip(positions, shapes):
        sw, sh = max(2, int(tw * scale)), max(2, int(th * scale))
        x = max(0, int(round((pos[0] - tw / 2 - w0) * scale)))
        y = max(0, int(round((pos[1] - th / 2 - h0) * scale)))
        boxes.append((x, y, sw, sh))

    band_h = max(64, min(ch, int(_BAND_BYTES / max(cw * 3 * 4, 1))))
    result = np.zeros((ch, cw, 3), np.uint16)
    neutral = None
    bands = (ch + band_h - 1) // band_h

    for bi, top in enumerate(range(0, ch, band_h)):
        bot = min(ch, top + band_h)
        acc = np.zeros((bot - top, cw, 3), np.float32)
        wacc = np.zeros((bot - top, cw), np.float32)
        for tile, (x, y, sw, sh) in zip(session.tiles, boxes):
            if y >= bot or y + sh <= top:
                continue                      # this tile misses the band
            raw = read_tile(session.dir, tile)
            if neutral is None:
                neutral = dng.grey_world_neutral(raw)
            rgb = cv2.cvtColor(raw,
                               _DEMOSAIC.get(pattern, cv2.COLOR_BayerGR2BGR))
            del raw
            small = cv2.resize(rgb, (sw, sh),
                               interpolation=cv2.INTER_AREA).astype(np.float32)
            del rgb
            if flat is not None:
                # Achromatic: the profile is measured on luma, so it corrects
                # shading without inventing a colour cast it never measured.
                small /= cv2.resize(flat, (sw, sh),
                                    interpolation=cv2.INTER_LINEAR)[:, :, None]
            win = _window(sh, sw)

            # Intersect the tile with the band, in both coordinate frames.
            y0 = max(y, top)
            y1 = min(y + sh, bot)
            sy0, sy1 = y0 - y, y1 - y
            x1 = min(x + sw, cw)
            sx1 = x1 - x
            acc[y0 - top:y1 - top, x:x1] += (small[sy0:sy1, :sx1]
                                             * win[sy0:sy1, :sx1, None])
            wacc[y0 - top:y1 - top, x:x1] += win[sy0:sy1, :sx1]

        covered = wacc > 0
        blended = np.zeros_like(acc)
        blended[covered] = acc[covered] / wacc[covered, None]
        # BGR from OpenCV back to the RGB a linear DNG expects. Scaled x16:
        # the blend of 12-bit tiles lands in 0..4095, and writing that
        # against the 16-bit white level shipped every mosaic four stops
        # under -- the same defect the stack merge had, found there.
        result[top:bot] = np.clip(blended * 16.0 + 0.5,
                                  0, 65520).astype(np.uint16)[:, :, ::-1]
        del acc, wacc, blended
        if progress is not None:
            progress(bi + 1, bands)

    target = session.dir / "composite.dng"
    # Written strip by strip through our own writer. pidng needed roughly
    # 3.2 GB of working memory on top of a 1.6 GB composite, which made the
    # writer the peak of the whole operation once banding had fixed the
    # compositor; this adds one strip. It also embeds a preview, so the
    # result is something a file manager will show rather than refuse.
    # Subsample *both* axes by the same step: stepping rows only produced a
    # 320x6 sliver, which is structurally a valid thumbnail and useless as
    # one. Cheap decimation first so make_preview is not resizing 272 MP.
    step = max(1, ch // 400, cw // 400)
    thumb = result[::step, ::step]
    # Balance the thumbnail even when the file carries no measured neutral:
    # a raw microscope field is green-dominant, and an unbalanced preview is
    # a green rectangle that tells the operator nothing about their mosaic.
    means = [float(thumb[:, :, c][thumb[:, :, c] > 0].mean() or 1.0)
             for c in range(3)]
    grey = tuple(m / max(means[1], 1e-6) for m in means)
    preview = dng.make_preview(thumb, bayer=False, neutral=grey,
                               white=int(max(result.max(), 1)))
    try:
        meta = read_metadata(
            session.dir / session.tiles[len(session.tiles) // 2].filename)
    except Exception:
        meta = None                    # provenance is a bonus, never a gate
    return dng.write_linear_streamed(
        target, lambda s, c: result[s:s + c], ch, cw,
        preview=preview, neutral=neutral or (1.0, 1.0, 1.0),
        white=4095 * 16, meta=meta)


def survey(directory: Path | str) -> tuple[MosaicSession, list, list]:
    """Geometry only: what a stitch *would* produce, without producing it."""
    session = MosaicSession.load(directory)
    shapes = []
    for t in session.tiles:
        raw = read_tile(session.dir, t)
        shapes.append(raw.shape)
        del raw
    positions = [(0.0, 0.0)] * len(shapes)
    predicted = _predicted_raw_positions(session.tiles, shapes)
    positions = [p if p is not None else (0.0, 0.0) for p in predicted]
    return session, positions, shapes


def stitch(directory: Path | str, scale: float = 0.25,
           progress=None) -> tuple[Path, dict]:
    """The whole run: load, register, composite. Returns (path, report)."""
    session = MosaicSession.load(directory)
    if len(session.tiles) < 2:
        raise ValueError("a mosaic needs at least two tiles")
    lumas, shapes = [], []
    for t in session.tiles:
        raw = read_tile(session.dir, t)
        shapes.append(raw.shape)
        lumas.append(luma_half(raw))
        del raw
    positions, edges = register(session, lumas, shapes)
    flat = estimate_flat(lumas)
    del lumas
    geom = plan(positions, shapes, scale)
    path = composite(session, positions, scale=scale, flat=flat,
                     shapes=shapes, progress=progress)
    report = {
        "width": geom["w"], "height": geom["h"],
        "megapixels": round(geom["megapixels"], 1),
        "tiles": len(session.tiles),
        "edges": len(edges),
        "refined": sum(1 for e in edges if e.refined),
        "flattened": flat is not None,
        "falloff": (round(float(1.0 - flat.min() / flat.max()) * 100, 1)
                    if flat is not None else None),
        "positions": [tuple(round(v, 1) for v in p) for p in positions],
    }
    return path, report


if __name__ == "__main__":
    where = sys.argv[1]
    how = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
    path, report = stitch(where, scale=how)
    print(f"{path}  ({report['refined']}/{report['edges']} edges refined, "
          f"{report['tiles']} tiles)")
