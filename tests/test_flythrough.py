"""The film of a mosaic.

Rendered small and short here: what these check is the geometry and the
choices, not the encoder, and a real composite takes forty seconds.
"""
import numpy as np
import pytest

from darlaston.process import flythrough as F


def _canvas(w=1600, h=1500, blobs=((300, 300), (1200, 400), (700, 1100))):
    """A stand-in mosaic: plain ground, a few detailed specimens, and the
    black wedges a real stitched canvas carries at its corners."""
    rng = np.random.default_rng(4)
    img = np.full((h, w, 3), 210, np.uint8)
    for cx, cy in blobs:
        yy, xx = np.mgrid[-120:120, -120:120]
        disc = (xx ** 2 + yy ** 2) < 120 ** 2
        patch = img[cy - 120:cy + 120, cx - 120:cx + 120]
        texture = (rng.random(disc.shape) * 120).astype(np.uint8)
        for c in range(3):
            patch[:, :, c][disc] = texture[disc]
    img[:90, :250] = 0            # the untouched corners of a real scan
    img[-70:, -400:] = 0
    return img


def test_content_box_ignores_the_unscanned_corners():
    img = _canvas()
    grey = np.zeros((100, 100), np.uint8)
    grey[20:80, 30:70] = 200
    x, y, w, h = F._content_box(grey)
    assert (x, y, w, h) == (30, 20, 40, 60)


def test_subjects_land_on_structure_not_on_empty_glass():
    """A diatom is fine edges on a plain ground, so the search is for
    structure. Picking by brightness would find the mountant."""
    img = _canvas()
    import cv2
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    picks = F._subjects(grey, 1.0, 3, apart=300)
    assert len(picks) == 3
    for px, py in picks:
        near = min((px - cx) ** 2 + (py - cy) ** 2
                   for cx, cy in ((300, 300), (1200, 400), (700, 1100)))
        assert near < 200 ** 2, f"({px},{py}) is not on a specimen"
    # And they are spread out, rather than three points on the same blob.
    for i, a in enumerate(picks):
        for b in picks[i + 1:]:
            assert (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 > 200 ** 2


def test_dark_specimen_interiors_are_not_mistaken_for_empty_canvas():
    """Unscanned canvas is contiguous with the edge of the frame; the dark
    middle of a diatom is not. Deciding by darkness alone ate 3% of the
    specimen texture here, and on a darkfield mosaic -- where most of a real
    specimen is nearly black -- it would eat the subject."""
    # Already 16:9, so nothing is padded and the coordinates stay put.
    img = np.full((F.OUT_H, F.OUT_W, 3), 200, np.uint8)
    img[:, :80] = 0                   # unscanned strip, touching the edge
    img[300:400, 600:700] = 0         # a black specimen, sealed inside
    out = F._on_mountant(img.copy())
    assert out.shape == img.shape, "no padding was needed here"
    assert (out[:, :80] > 40).all(), "the unscanned strip should be filled"
    assert (out[300:400, 600:700] == 0).all(), "a specimen was flooded"


def test_the_ground_is_evened_out_and_padded_to_the_aspect():
    """The reveal should land on an arrangement floating on mountant, not
    on the ragged black edge of how it was photographed."""
    img = _canvas()
    out = F._on_mountant(img.copy())
    assert (out[:90, :250] > 40).all(), "unscanned corner still black"
    h, w = out.shape[:2]
    assert w / h == pytest.approx(F.OUT_W / F.OUT_H, rel=0.02)
    assert w >= img.shape[1] and h >= img.shape[0], "padding, never cropping"
    # Specimens must survive: only pure-black canvas may be filled. Counted
    # rather than sampled at a coordinate, because padding moves everything.
    def textured(a):
        return int(((a[:, :, 0] > 5) & (a[:, :, 0] < 150)).sum())
    assert textured(out) > textured(img) * 0.98, "a dark specimen was flooded"
    # The corners the scan never reached are gone; a dark speck sealed
    # inside a specimen is not the same thing and must stay.
    assert (out[:90, :250] > 40).all()


def test_frames_never_upscale_and_never_leave_the_image():
    img = _canvas(3000, 2000)
    # Asked for something tighter than one output pixel per source pixel.
    f = F._frame(img, 100, 100, 10)
    assert f.shape[:2] == (F.OUT_H, F.OUT_W)
    # Asked for more than exists, and off the edge in both directions.
    f = F._frame(img, -5000, 9999, 99999)
    assert f.shape[:2] == (F.OUT_H, F.OUT_W)
    assert f.std() > 0, "clamping produced an empty frame"


def test_easing_starts_and_stops_at_rest():
    assert F._ease(0.0) == 0.0
    assert F._ease(1.0) == 1.0
    assert F._ease(0.5) == pytest.approx(0.5)
    # Slower at the ends than in the middle, which is what makes it read
    # as a camera move rather than a machine one.
    assert F._ease(0.1) < 0.1 and F._ease(0.9) > 0.9
    assert F._ease(-1) == 0.0 and F._ease(2) == 1.0


def test_it_refuses_a_directory_with_no_composite(tmp_path):
    with pytest.raises(FileNotFoundError):
        F.flythrough(tmp_path)
