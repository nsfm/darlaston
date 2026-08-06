"""Stitching, and the number a published scale bar is drawn from.

There was no test file here at all, which is how a composite came to
declare its scale bar in *tile* pixels while being built at half tile
resolution. Nothing that reads a mosaic back could tell -- the file is
internally consistent and simply wrong -- so the check has to run the
real thing and read the real number out of the real file.
"""
from pathlib import Path

import numpy as np
import pytest

from darlaston.capture.mosaic import MosaicSession
from darlaston.process import dng, stitch
from darlaston.process.metadata import CaptureMetadata

#: What one tile pixel covers, in micrometres. An arbitrary number, chosen
#: to be nothing like any of the scale factors so a wrong answer cannot
#: coincide with a right one.
TILE_UM_PER_PX = 0.0731


def _tile(path: Path, seed: int) -> None:
    """One tile, written by our own writer with real metadata on it."""
    rng = np.random.default_rng(seed)
    raw = (rng.normal(2000, 60, (128, 160))).clip(0, 4095).astype(np.uint16)
    # Something to register on, in a different place per tile.
    raw[30 + seed * 4:60 + seed * 4, 40:90] = 3400
    meta = CaptureMetadata(
        make="Test", model="Bench",
        comment=f"objective=40x um_per_px={TILE_UM_PER_PX:g} slide=none",
        focal_plane_per_mm=416.67, unique_id=f"tile-{seed}")
    preview = dng.make_preview(raw, bayer=True, white=4095)
    dng.write_bayer_streamed(path, lambda s, c: raw[s:s + c], 128, 160,
                             preview=preview, pattern="GBRG", white=4095,
                             meta=meta, bits=12)


def _mosaic(tmp_path: Path) -> MosaicSession:
    session = MosaicSession(tmp_path, subject="scale")
    for i in range(2):
        made = tmp_path / f"shot_{i}.dng"
        _tile(made, seed=i)
        # 20% overlap along x, which is what the stitcher is steered to.
        session.adopt(made, pos=(i * 128.0, 0.0), frame=(160, 128))
    return session


def _um_per_px(path: Path) -> float:
    meta = stitch.read_metadata(path)
    assert meta is not None, "the composite carries no metadata at all"
    for part in meta.comment.split():
        key, _sep, value = part.partition("=")
        if key == "um_per_px":
            return float(value)
    raise AssertionError(f"no um_per_px in {meta.comment!r}")


@pytest.mark.parametrize("scale", [1.0, 0.5, 0.25, 0.125])
def test_the_composite_declares_its_own_pixel_scale(tmp_path, scale):
    """A composite built at `scale` has pixels 1/scale as wide, so each one
    covers 1/scale as much slide. It used to inherit the middle tile's
    number unchanged, and `plate` draws the bar straight from it -- a bar
    labelled 20 um came out 40 um long at the default 0.5 and 160 um at
    "preview". That is the exact failure `plate`'s docstring exists to
    prevent, and it is invisible in the file."""
    session = _mosaic(tmp_path)
    made = stitch.composite(session, [(0.0, 0.0), (128.0, 0.0)], scale=scale,
                            shapes=[(128, 160), (128, 160)])
    assert _um_per_px(made) == pytest.approx(TILE_UM_PER_PX / scale, rel=1e-3)


def test_the_composite_is_not_the_tile_it_was_built_from(tmp_path):
    """`unique_id` identifies *these pixels*. Two files answering to one id
    is the opposite of what the tag is for."""
    session = _mosaic(tmp_path)
    made = stitch.composite(session, [(0.0, 0.0), (128.0, 0.0)], scale=0.5,
                            shapes=[(128, 160), (128, 160)])
    meta = stitch.read_metadata(made)
    assert meta is not None
    assert not meta.unique_id, "the composite claims to be one of its tiles"
    # The provenance that should survive, does.
    assert meta.make == "Test"
    assert "objective=40x" in meta.comment


def test_a_composite_can_be_read_back_as_pixels(tmp_path):
    """The whole file, not just its header: a writer that emitted zeros
    would satisfy every assertion above."""
    session = _mosaic(tmp_path)
    made = stitch.composite(session, [(0.0, 0.0), (128.0, 0.0)], scale=1.0,
                            shapes=[(128, 160), (128, 160)])
    from darlaston.process.wiggle import _read_composite

    image, white = _read_composite(made)
    assert image.shape[2] == 3
    assert white == 4095 * 16
    lit = image[image > 0]
    assert lit.size > image.size // 4, "most of the composite is empty"
    assert float(lit.mean()) > 1000.0, "the composite came back nearly black"


def test_an_interrupted_write_leaves_no_file_to_be_misread(tmp_path):
    """Everything that makes a DNG readable is patched in at the very end,
    so a write that stops early left a file whose strip offsets were all
    still zero -- and our own reader follows offset 0 into the header and
    returns whatever is there rather than refusing."""
    target = tmp_path / "half.dng"
    # Taller than one strip (64 rows), so there is a "partway".
    raw = np.full((256, 64), 2000, np.uint16)

    def rows(start, count):
        if start:                       # fail partway, not on the first strip
            raise OSError("the disk filled up")
        return raw[start:start + count]

    with pytest.raises(OSError):
        dng.write_bayer_streamed(
            target, rows, 256, 64,
            preview=dng.make_preview(raw, bayer=True, white=4095),
            pattern="GBRG", white=4095, bits=12)
    assert not target.exists(), "a partial file was left where a good one goes"
    assert not list(tmp_path.glob("*.part")), "the part file was left behind"
