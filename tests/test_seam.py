"""Seam-aware stitching: the invariants the bench measures, pinned fast.

`tools/stitch_bench.py` is the measured ground truth -- ghost energy against
the feather, the numbers that decide the design. This is the cheap guard
that keeps the three load-bearing properties from silently breaking:

  - a seam only fires where there is a specimen to route around;
  - where it fires, the specimen ends up owned by a single tile;
  - where it does not, the multiplier is exactly one, so the compositor's
    own blend is recovered bit for bit.
"""
import cv2
import numpy as np

from darlaston.process import seam


def _two_tiles(canvas, tw):
    """A canvas cut into a left and a right tile sharing an overlap, with the
    geometry `seam.weights` expects: half-res-ish lumas, raw-pixel centres."""
    h, w = canvas.shape
    a = canvas[:, :tw].astype(np.float32)
    b = canvas[:, w - tw:].astype(np.float32)
    lumas = [a, b]
    positions = [(tw / 2.0, h / 2.0), (w - tw / 2.0, h / 2.0)]
    shapes = [(h, tw), (h, tw)]
    return lumas, positions, shapes


def _bg(seed=1):
    rng = np.random.default_rng(seed)
    t = cv2.GaussianBlur(rng.normal(size=(240, 440)).astype(np.float32),
                         (0, 0), 8.0)
    t = (t - t.min()) / (np.ptp(t) + 1e-9)
    return 600 + t * 160                         # gentle field, ~600-760


def _blob(seed=2):
    rng = np.random.default_rng(seed)
    tex = cv2.GaussianBlur(rng.normal(size=(240, 440)).astype(np.float32),
                           (0, 0), 1.0)
    tex = 1800 + (tex - tex.min()) / (np.ptp(tex) + 1e-9) * 1800
    yy, xx = np.mgrid[0:240, 0:440].astype(np.float32)
    d = np.sqrt((xx - 220) ** 2 + (yy - 120) ** 2)
    mask = d < 18
    return tex, mask


def test_an_empty_overlap_is_left_exactly_alone():
    """No specimen, no seam: every multiplier is one, so the compositor's
    plain blend is recovered bit for bit. This is the fallback promise."""
    lumas, positions, shapes = _two_tiles(_bg(), 300)
    mult, _ = seam.weights(lumas, positions, shapes, scale=1.0)
    for m in mult:
        assert np.all(m == 1.0), "an empty overlap must not be touched"


def test_a_straddling_specimen_falls_back_to_the_plain_blend():
    """A specimen spanning the whole boundary has no lane to route through,
    so the seam must not pretend it found one: it leaves the plain blend
    rather than cut through the specimen."""
    bg = _bg()
    rng = np.random.default_rng(3)
    bar = 1800 + cv2.GaussianBlur(rng.normal(size=bg.shape).astype(np.float32),
                                  (0, 0), 1.0) * 400
    yy = np.mgrid[0:240, 0:440][0]
    canvas = np.where(np.abs(yy - 120) < 14, bar, bg).astype(np.float32)
    lumas, positions, shapes = _two_tiles(canvas, 300)
    mult, _ = seam.weights(lumas, positions, shapes, scale=1.0)
    for m in mult:
        assert np.all(m == 1.0), "no lane means no seam"


def test_a_specimen_in_the_overlap_is_given_to_one_tile():
    """The routed case: a specimen sitting in the overlap must end up owned
    by a single tile, so the feather can never sum it from two displaced
    copies. Ownership is the product of window and multiplier; over the
    specimen one tile's multiplier should win decisively."""
    bg = _bg()
    tex, mask = _blob()
    canvas = np.where(mask, tex, bg).astype(np.float32)
    lumas, positions, shapes = _two_tiles(canvas, 300)
    mult, own_scale = seam.weights(lumas, positions, shapes, scale=1.0)

    # A seam fired: at least one multiplier was driven well below one.
    assert min(float(m.min()) for m in mult) < 0.1, "no seam was routed"

    # Sample the two multipliers over the specimen, in the shared frame. The
    # specimen lands in both tiles' overlap; the one the seam handed it to
    # keeps ~1 there while the other is driven toward 0.
    h, w = canvas.shape
    ms = mask[:, w // 2 - 40:w // 2 + 40].mean(axis=1) > 0.3
    ma = cv2.resize(mult[0], (300, h))[:, -80:]        # right edge of left tile
    mb = cv2.resize(mult[1], (300, h))[:, :80]         # left edge of right tile
    over_a = ma[ms].mean() if ms.any() else 1.0
    over_b = mb[ms].mean() if ms.any() else 1.0
    # One side owns the specimen (near 1), the other yields it (near 0).
    assert abs(over_a - over_b) > 0.5, \
        "the specimen was split between both tiles, not given to one"
