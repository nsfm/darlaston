"""Choosing a halo setting by looking at crops of the merge.

The load-bearing property here is that a crop rendered with its influence
margin equals the same crop taken from a whole-frame clamp. If that ever
stops being true the operator would be choosing against one picture and
receiving another, so it is tested rather than trusted.
"""
from __future__ import annotations

import cv2
import numpy as np

from darlaston.process import sampler, slope


def _scene(n=8, size=200):
    """A textured disk over flat field, blurred per slice, plus a depth map."""
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[0:size, 0:size]
    disk = (xx - size / 2) ** 2 + (yy - size / 2) ** 2 < 45 ** 2
    tex = rng.normal(0, 300, (size, size)).astype(np.float32) + 2200
    field = np.full((size, size), 900.0, np.float32)
    lumas = np.stack([
        cv2.GaussianBlur(np.where(disk, tex, field), (0, 0),
                         1.0 + 2.5 * abs(k - n // 2)).astype(np.float32)
        for k in range(n)])
    depth = np.where(disk, float(n - 2), 1.0).astype(np.float32)
    depth = cv2.GaussianBlur(depth, (0, 0), 2.0)
    return lumas, depth, disk


def test_margin_shrinks_as_the_bound_loosens():
    assert sampler.margin_for(0.2, 15) > sampler.margin_for(0.6, 15)
    assert sampler.margin_for(0.0, 15) == 0
    assert sampler.margin_for(0.001, 30) <= sampler.MAX_MARGIN


def test_crops_land_on_the_boundary_and_do_not_overlap():
    lumas, depth, disk = _scene()
    edge = cv2.morphologyEx(disk.astype(np.uint8), cv2.MORPH_GRADIENT,
                            np.ones((5, 5), np.uint8)) > 0
    boxes = sampler.crops(depth, disk, count=2, size=64)
    assert boxes
    for y, x in boxes:
        assert edge[y:y + 64, x:x + 64].any()
    if len(boxes) == 2:
        (y0, x0), (y1, x1) = boxes
        assert abs(y0 - y1) >= 32 or abs(x0 - x1) >= 32


def test_crops_of_a_blank_field_returns_nothing_to_compare():
    flat = np.zeros((80, 80), np.float32)
    assert sampler.crops(flat, np.zeros((80, 80), bool), count=3) == []


def test_render_with_margin_equals_clamping_the_whole_frame():
    """The claim the speed argument rests on, and it is exact.

    Without the margin the same crops were measured wrong by 2.2 to 7.2
    slices on real stacks, which is a quarter of a whole stack, so this is
    the difference between a correct fast path and a badly wrong one.
    """
    lumas, depth, disk = _scene()
    levels = len(lumas)
    boxes = sampler.crops(depth, disk, count=2, size=64)
    assert boxes
    for s in (0.2, 0.5):
        whole = slope.clamp(depth, s)
        for (y, x) in boxes:
            got = sampler.render(lumas, depth, (y, x), s, levels, size=64)
            d = whole[y:y + 64, x:x + 64]
            want = None
            for i in range(levels):
                piece = lumas[i][y:y + 64, x:x + 64] * np.clip(
                    1.0 - np.abs(d - i), 0.0, 1.0)
                want = piece if want is None else want + piece
            assert np.abs(got - want).max() < 1e-4


def test_render_without_a_bound_is_the_plain_blend():
    lumas, depth, disk = _scene()
    got = sampler.render(lumas, depth, (60, 60), 0.0, len(lumas), size=48)
    want = None
    d = depth[60:108, 60:108]
    for i in range(len(lumas)):
        piece = lumas[i][60:108, 60:108] * np.clip(1.0 - np.abs(d - i),
                                                   0.0, 1.0)
        want = piece if want is None else want + piece
    assert np.abs(got - want).max() < 1e-4


def test_preview_covers_every_requested_bound():
    lumas, depth, disk = _scene()
    slopes = [0.0, 0.3, 0.6]
    boxes, rendered = sampler.preview(lumas, depth, disk, slopes,
                                      count=2, size=64)
    assert set(rendered) == set(slopes)
    for s in slopes:
        assert len(rendered[s]) == len(boxes)
        assert all(im.shape == (64, 64) for im in rendered[s])


def test_the_bounds_actually_differ_from_one_another():
    """A comparison of identical pictures would be a cruel joke."""
    lumas, depth, disk = _scene()
    _boxes, rendered = sampler.preview(lumas, depth, disk, [0.0, 0.25],
                                       count=1, size=64)
    assert np.abs(rendered[0.0][0] - rendered[0.25][0]).max() > 1.0
