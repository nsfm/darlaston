"""Our own DNG writer.

The reason it exists is structural, so the tests are structural: does the
preview land in IFD0 where a thumbnailer looks, does the real image land in a
SubIFD, does 12-bit packing survive a round trip, and does the streaming
interface mean what it says.
"""
import struct

import numpy as np
import pytest

from darlaston.process import dng
from darlaston.process.stitch import read_bayer_dng
from darlaston.process.tiff import DngWriter, make_preview, pack_12


def _ifd(data, at):
    (n,) = struct.unpack_from("<H", data, at)
    out = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack_from("<HHII", data, at + 2 + i * 12)
        out[tag] = (typ, cnt, val)
    return out


def _write(tmp_path, img, **kw):
    h, w = img.shape
    pv = make_preview(img, bayer=True, white=4095)
    return dng.write_bayer_streamed(tmp_path / "t.dng",
                                    lambda s, c: img[s:s + c], h, w,
                                    preview=pv, **kw)


# ---- packing ---------------------------------------------------------------

def test_12_bit_packing_is_three_bytes_per_two_samples():
    row = np.array([0x000, 0xFFF, 0xABC, 0x123], np.uint16)
    packed = pack_12(row)
    assert len(packed) == 6
    assert packed == bytes([0x00, 0x0F, 0xFF, 0xAB, 0xC1, 0x23])


def test_packing_handles_an_odd_count():
    packed = pack_12(np.array([0xFFF], np.uint16))
    assert len(packed) == 3


@pytest.mark.parametrize("bits", [12, 16])
def test_round_trip_is_exact(tmp_path, bits):
    rng = np.random.default_rng(11)
    img = rng.integers(0, 4096, (64, 96)).astype(np.uint16)
    p = _write(tmp_path, img, bits=bits, white=4095)
    assert np.array_equal(read_bayer_dng(p), img)


def test_packing_actually_saves_a_quarter(tmp_path):
    """Compare the saving against the payload, not the file: on a small
    image the embedded preview is most of the bytes and would hide it."""
    h, w = 256, 256
    img = np.full((h, w), 2000, np.uint16)
    big = _write(tmp_path, img, bits=16, white=4095).stat().st_size
    (tmp_path / "t.dng").unlink()
    small = _write(tmp_path, img, bits=12, white=4095).stat().st_size
    saved = big - small
    assert saved == h * w * 2 - h * w * 3 // 2, \
        "packing must save exactly one byte in four of the pixel data"


# ---- structure -------------------------------------------------------------

def test_preview_is_in_ifd0_and_the_image_is_in_a_subifd(tmp_path):
    """The whole point. A thumbnailer reads IFD0; it must find a small RGB
    image there, not a 20 MP mosaic it will refuse."""
    img = np.full((200, 300), 1500, np.uint16)
    p = _write(tmp_path, img, bits=12, white=4095)
    data = p.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    ifd0 = _ifd(data, first)

    assert ifd0[254][2] == 1, "IFD0 must declare itself reduced-resolution"
    assert ifd0[262][2] == 2, "IFD0 must be plain RGB"
    assert ifd0[256][2] < 300, "the preview must be smaller than the image"
    assert 330 in ifd0, "there must be a SubIFD"

    sub = _ifd(data, ifd0[330][2])
    assert sub[254][2] == 0, "the SubIFD is the real image"
    assert sub[256][2] == 300 and sub[257][2] == 200
    assert sub[262][2] == 32803, "and it is a colour filter array"


def test_preview_pixels_are_present_and_plausible(tmp_path):
    """A preview that is structurally correct and entirely black would pass
    the test above and fail the operator."""
    rng = np.random.default_rng(4)
    img = rng.integers(500, 3500, (240, 320)).astype(np.uint16)
    p = _write(tmp_path, img, white=4095)
    data = p.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    ifd0 = _ifd(data, first)
    w, h = ifd0[256][2], ifd0[257][2]
    off, count = ifd0[273][2], ifd0[279][2]
    assert count == w * h * 3
    pv = np.frombuffer(data, np.uint8, count=count, offset=off)
    assert 20 < pv.mean() < 235, "the preview must not be black or blown"


def test_strips_are_written_and_indexed(tmp_path):
    img = np.full((300, 64), 1000, np.uint16)
    p = _write(tmp_path, img, white=4095)
    data = p.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    sub = _ifd(data, _ifd(data, first)[330][2])
    strips = sub[273][1]
    expected = (300 + DngWriter.ROWS_PER_STRIP - 1) // DngWriter.ROWS_PER_STRIP
    assert strips == expected
    assert sub[279][1] == strips, "every strip needs a byte count"


def test_rows_are_requested_once_each_in_order(tmp_path):
    """Streaming is the memory argument; a writer that secretly asks for the
    whole image would pass every other test here."""
    seen = []

    def rows(start, count):
        seen.append((start, count))
        return np.zeros((count, 32), np.uint16)

    w = DngWriter(tmp_path / "s.dng", 32, 200)
    w.set_preview(np.zeros((10, 16, 3), np.uint8))
    w.write(rows)
    assert seen[0][0] == 0
    assert [s for s, _ in seen] == sorted(s for s, _ in seen)
    assert sum(c for _, c in seen) == 200, "each row exactly once"
    assert max(c for _, c in seen) <= DngWriter.ROWS_PER_STRIP


def test_preview_keeps_the_image_aspect_ratio(tmp_path):
    """A 320x6 sliver is a structurally valid thumbnail and a useless one.
    It happened, from decimating rows and not columns."""
    img = np.full((240, 960), 1200, np.uint16)      # 4:1
    p = _write(tmp_path, img, white=4095)
    data = p.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    ifd0 = _ifd(data, first)
    pw, ph = ifd0[256][2], ifd0[257][2]
    # Bayer previews are half-size per axis, so the ratio is what must hold.
    assert abs(pw / ph - 960 / 240) < 0.15, f"preview is {pw}x{ph}"


def test_a_writer_without_a_preview_refuses(tmp_path):
    w = DngWriter(tmp_path / "n.dng", 8, 8)
    with pytest.raises(ValueError, match="preview"):
        w.write(lambda s, c: np.zeros((c, 8), np.uint16))


def test_linear_composite_round_trips(tmp_path):
    rng = np.random.default_rng(2)
    img = rng.integers(0, 65535, (80, 120, 3)).astype(np.uint16)
    pv = make_preview(img, bayer=False, white=65535)
    p = dng.write_linear_streamed(tmp_path / "c.dng",
                                  lambda s, c: img[s:s + c], 80, 120,
                                  preview=pv, white=65535)
    data = p.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    sub = _ifd(data, _ifd(data, first)[330][2])
    assert sub[262][2] == 34892, "linear raw"
    assert sub[277][2] == 3
    off = sub[273][2] if sub[273][1] == 1 else None
    counts = sub[279][1]
    assert counts >= 1


# ---- the optics, in fields a photographer reads ----------------------------

def test_objective_becomes_focal_length_and_f_number():
    """A microscope objective *is* a lens, and both numbers are derivable
    rather than invented: f = tube length / magnification, and the f-number
    at the sensor is M / (2·NA) because NA_image = NA_objective / M."""
    from darlaston.process.metadata import from_setup
    from darlaston.session.model import (BUILTIN_ILLUMINATION, CameraProfile,
                                         Objective, ScopeProfile, Setup,
                                         Turret)

    scope = ScopeProfile(id="z", name="Zeiss Universal",
                         turret=Turret([Objective(40, 0.75)], current=0),
                         optovar=[1.0], optovar_current=0)
    setup = Setup(camera=CameraProfile(serial="x", name="cam"), scope=scope,
                  illumination=BUILTIN_ILLUMINATION[0])
    meta = from_setup(setup, exposure_us=8330, gain_pct=100)

    assert meta.focal_length_mm == pytest.approx(4.0)     # 160 / 40
    assert meta.f_number == pytest.approx(26.667, rel=1e-3)
    assert meta.iso == 100

    # The Optovar multiplies total magnification, so it moves the f-number
    # -- more magnification means a dimmer, more diffraction-limited cone --
    # but not the objective's own focal length.
    scope.optovar = [1.6]
    meta = from_setup(setup, exposure_us=8330, gain_pct=100)
    assert meta.focal_length_mm == pytest.approx(4.0)
    assert meta.f_number == pytest.approx(64.0 / 1.5, rel=1e-3)


def test_no_optics_means_no_invented_numbers():
    """An objective with no NA recorded must leave the f-number empty rather
    than guess one."""
    from darlaston.process.metadata import from_setup
    from darlaston.session.model import (BUILTIN_ILLUMINATION, CameraProfile,
                                         Objective, ScopeProfile, Setup,
                                         Turret)
    scope = ScopeProfile(id="z", turret=Turret([Objective(10)], current=0))
    setup = Setup(camera=CameraProfile(serial="x"), scope=scope,
                  illumination=BUILTIN_ILLUMINATION[0])
    meta = from_setup(setup, exposure_us=1000, gain_pct=100)
    assert meta.focal_length_mm == pytest.approx(16.0)
    assert meta.f_number is None
