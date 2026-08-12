"""Stitching, and the number a published scale bar is drawn from.

There was no test file here at all, which is how a composite came to
declare its scale bar in *tile* pixels while being built at half tile
resolution. Nothing that reads a mosaic back could tell -- the file is
internally consistent and simply wrong -- so the check has to run the
real thing and read the real number out of the real file.
"""
from pathlib import Path

import cv2
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


def _write_tile(path: Path, raw: np.ndarray) -> None:
    meta = CaptureMetadata(
        make="Test", model="Bench",
        comment=f"objective=40x um_per_px={TILE_UM_PER_PX:g} slide=none",
        focal_plane_per_mm=416.67, unique_id="t")
    preview = dng.make_preview(raw, bayer=True, white=4095)
    dng.write_bayer_streamed(path, lambda s, c: raw[s:s + c], raw.shape[0],
                             raw.shape[1], preview=preview, pattern="GBRG",
                             white=4095, meta=meta, bits=12)


def test_multiband_reconstructs_where_the_tiles_agree(tmp_path):
    """The band split has to put the mosaic back together. Two tiles cut from
    one source agree exactly in their overlap, so the multiband composite --
    low frequencies from a downscaled pyramid, high frequencies committed and
    added per band -- must land on the same picture the single-band blend
    does. If the two bands do not reconstruct, this drifts."""
    from darlaston.process.wiggle import _read_composite

    # Tiles the size real composite tiles are (~900 px): at 128 px the low-res
    # pyramid is so coarse the round trip alone costs ~2%, which is a property
    # of tile size, not of the wiring. At this size the reconstruction of two
    # agreeing tiles is under a percent, concentrated at the sharp feature edge.
    rng = np.random.default_rng(0)
    H, W, tw = 256, 1152, 640
    src = rng.normal(2000, 60, (H, W)).clip(0, 4095)
    src[90:170, 520:700] = 3400                # a feature sitting in the overlap
    _write_tile(tmp_path / "a.dng", src[:, :tw].astype(np.uint16))
    _write_tile(tmp_path / "b.dng", src[:, W - tw:].astype(np.uint16))
    session = MosaicSession(tmp_path, subject="recon")
    session.adopt(tmp_path / "a.dng", pos=(0.0, 0.0), frame=(tw, H))
    session.adopt(tmp_path / "b.dng", pos=(float(W - tw), 0.0), frame=(tw, H))
    positions = [(tw / 2, H / 2), (W - tw / 2, H / 2)]
    shapes = [(H, tw), (H, tw)]

    made = stitch.composite(session, positions, scale=1.0, shapes=shapes,
                            multiband=False)
    single, _ = _read_composite(made)
    made = stitch.composite(session, positions, scale=1.0, shapes=shapes,
                            multiband=True)
    multi, _ = _read_composite(made)

    assert multi.shape == single.shape
    # Agreeing tiles: the two blends must land on the same picture, bar the
    # small reconstruction error of the pyramid round trip.
    diff = np.abs(multi.astype(np.float32) - single.astype(np.float32))
    lit = single.max(axis=2) > 0
    rel = float(diff[lit].mean()) / (float(single[lit].mean()) + 1e-6)
    # Under 1.5%: the residual is the pyramid round trip at the hard synthetic
    # edge (a 1400-count step); a wiring fault reads as 10%+, which is what
    # this guards. Real diatom edges at capture scale are softer still.
    assert rel < 0.015, f"multiband drifted from the single-band blend ({rel:.3%})"


def test_multiband_grades_a_step_the_seam_leaves_sharp(tmp_path):
    """The payoff. Two tiles that agree in texture but differ in brightness
    (unnormalised exposure) with a specimen in their overlap: the seam routes
    around the specimen and commits the background to one tile across a narrow
    feather, which turns the 6% brightness step into a sharp edge. Multiband
    grades that low-frequency step across the whole overlap while leaving the
    committed detail alone, so the blurred step is markedly gentler."""
    from darlaston.process import seam
    from darlaston.process.wiggle import _read_composite

    rng = np.random.default_rng(0)
    H, W, tw = 256, 1152, 640
    bg = rng.normal(1500, 40, (H, W)).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    bg[np.sqrt((xx - 576.) ** 2 + (yy - 128.) ** 2) < 28] += 1800  # specimen
    _write_tile(tmp_path / "a.dng", bg[:, :tw].clip(0, 4095).astype(np.uint16))
    _write_tile(tmp_path / "b.dng",
                (bg[:, W - tw:] * 1.06).clip(0, 4095).astype(np.uint16))
    session = MosaicSession(tmp_path, subject="step")
    session.adopt(tmp_path / "a.dng", pos=(0.0, 0.0), frame=(tw, H))
    session.adopt(tmp_path / "b.dng", pos=(float(W - tw), 0.0), frame=(tw, H))
    pos = [(tw / 2., H / 2.), (W - tw / 2., H / 2.)]
    shapes = [(H, tw), (H, tw)]

    # Seam multipliers as stitch() computes them, with a small residual so the
    # seam fires and commits the background across a narrow feather.
    lum = [stitch.luma_half(stitch.read_tile(session.dir, t))
           for t in session.tiles]
    mult, _ = seam.weights(lum, [(tw / 2., H / 2.),
                                 (W - tw / 2. + 7, H / 2. + 4)], shapes, 1.0)
    assert min(float(m.min()) for m in mult) < 0.999, "the seam did not fire"

    made = stitch.composite(session, pos, scale=1.0, shapes=shapes,
                            seam_mult=mult, multiband=False)
    seam_only, _ = _read_composite(made)
    made = stitch.composite(session, pos, scale=1.0, shapes=shapes,
                            seam_mult=mult, multiband=True)
    banded, _ = _read_composite(made)

    def step_gradient(img):
        # The low-frequency step: blur away the texture, take a background row
        # band clear of the specimen, and find the steepest brightness change.
        b = cv2.GaussianBlur(img[20:60, 480:660].astype(np.float32).mean(2),
                             (0, 0), 10).mean(axis=0)
        return float(np.abs(np.diff(b)).max())

    sharp = step_gradient(seam_only)
    graded = step_gradient(banded)
    assert graded < 0.75 * sharp, \
        f"multiband did not grade the step (seam {sharp:.0f}, mb {graded:.0f})"
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
