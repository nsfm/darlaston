"""The background mask and the slope clamp.

Several of these are deliberately tiny hand-checkable arrays rather than
synthetic scenes. Both of the bugs that got through the research phase of
this work were set-direction errors -- flooding from four corners instead
of the whole border, and running a distance transform on a mask instead of
its complement -- and each survived because it was only ever exercised on
2720x1824 fields where a wrong answer still looks plausible. A six-by-nine
array does not have that mercy.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from darlaston.process import mask, slope


def _grid(rows):
    return np.array([[c == "#" for c in row] for row in rows], bool)


# ---- hole filling -----------------------------------------------------------

def test_fill_holes_closes_an_enclosed_void():
    m = _grid(["#####",
               "#...#",
               "#.#.#",
               "#...#",
               "#####"])
    assert mask.fill_holes(m).all()


def test_fill_holes_leaves_background_that_reaches_the_edge():
    """The corner-seeded version filled 15 px here and was wrong.

    The bar splits the top border into two background regions. Only one of
    them touches a corner, so a flood seeded from the corners alone reads
    the other as enclosed and hands it to the subject.
    """
    m = _grid(["#...#....",
               "#...#....",
               "#...#....",
               "#...#....",
               "#...#....",
               "########.",
               "########."])
    out = mask.fill_holes(m)
    assert int(out.sum() - m.sum()) == 0


def test_fill_holes_handles_a_mask_covering_every_corner():
    m = _grid(["###...###",
               "###...###",
               ".........",
               "###...###",
               "###...###"])
    out = mask.fill_holes(m)
    assert int(out.sum() - m.sum()) == 0


def test_fill_holes_is_idempotent():
    m = _grid(["#####",
               "#...#",
               "#####"])
    once = mask.fill_holes(m)
    assert (mask.fill_holes(once) == once).all()


# ---- despeckle --------------------------------------------------------------

def test_despeckle_drops_small_and_keeps_large():
    m = np.zeros((100, 100), bool)
    m[10:60, 10:60] = True                    # 2500 px, 25% of frame
    m[90, 90] = True                          # a single pixel
    out = mask.despeckle(m, min_share=0.01)   # 100 px threshold
    assert out[30, 30]
    assert not out[90, 90]


def test_despeckle_of_nothing_is_nothing():
    assert not mask.despeckle(np.zeros((20, 20), bool)).any()


# ---- the mask end to end ----------------------------------------------------

def _stack_with_a_textured_disk(n=6, size=120, radius=30):
    """Slices where only a central disk ever comes into focus."""
    rng = np.random.default_rng(4)
    yy, xx = np.mgrid[0:size, 0:size]
    disk = (xx - size / 2) ** 2 + (yy - size / 2) ** 2 < radius ** 2
    texture = rng.normal(0, 400, (size, size)).astype(np.float32) + 2000
    field = np.full((size, size), 800.0, np.float32)
    out = []
    for k in range(n):
        sharp = np.where(disk, texture, field)
        blur = cv2.GaussianBlur(sharp, (0, 0), 1.0 + 3.0 * abs(k - n // 2))
        out.append(blur.astype(np.float32))
    return np.stack(out), disk


def test_subject_finds_the_disk_and_leaves_the_far_field():
    """The disk is claimed; field well away from it is not.

    Deliberately not asserting anything about the apron immediately
    around the disk. A defocused edge spreads a broad bright gradient,
    Tenengrad is a gradient measure, and so the gate reads that apron as
    subject. That is a known property rather than a defect here: this
    mask exists to stop featureless pixels voting on geometry, and the
    apron is handled by the slope bound on the composite side.
    """
    lumas, disk = _stack_with_a_textured_disk()
    found = mask.body(lumas)
    far = cv2.distanceTransform((~disk).astype(np.uint8),
                                cv2.DIST_L2, 5) > 25
    assert found[disk].mean() > 0.9
    assert found[far].mean() < 0.05


def test_flatten_removes_background_variance_and_spares_the_subject():
    rng = np.random.default_rng(11)
    depth = rng.uniform(0, 20, (60, 60)).astype(np.float32)
    solid = np.zeros((60, 60), bool)
    solid[20:40, 20:40] = True
    depth[20:40, 20:40] = np.linspace(3, 9, 400).reshape(20, 20)

    out = mask.flatten(depth, solid)
    assert out[~solid].std() == pytest.approx(0.0, abs=1e-6)
    assert (out[solid] == depth[solid]).all()


def test_flatten_without_any_background_is_a_no_op():
    depth = np.linspace(0, 5, 100, dtype=np.float32).reshape(10, 10)
    out = mask.flatten(depth, np.ones((10, 10), bool))
    assert (out == depth).all()


# ---- the slope clamp --------------------------------------------------------

def _naive_clamp(depth, slope_):
    """Literal transcription of the paper's Algorithm 1, for comparison.

    Grows each depth's region one pixel at a time and clamps the newly
    included ring, exactly as written. Far too slow for real frames, which
    is why the shipped version uses a distance transform, but it is the
    definition the fast one has to match.
    """
    out = depth.astype(np.float64).copy()
    k = np.ones((3, 3), np.uint8)
    for s in sorted(np.unique(np.round(depth)), reverse=True):
        seed = (np.round(out) == s).astype(np.uint8)
        if not seed.any():
            continue
        grown = seed.copy()
        for r in range(1, max(depth.shape)):
            bigger = cv2.dilate(grown, k)
            ring = (bigger > 0) & (grown == 0)
            if not ring.any():
                break
            np.clip(out, s - slope_ * r, s + slope_ * r, out=out, where=ring)
            grown = bigger
    return out.astype(np.float32)


def test_clamp_matches_the_papers_algorithm():
    """The distance-transform form must equal the iterative dilation.

    Chebyshev distance here, because a 3x3 dilation grows one chebyshev
    step per round; the shipped version uses euclidean, which is the norm
    the bound is actually stated in and differs only on diagonals.
    """
    rng = np.random.default_rng(19)
    for _ in range(3):
        d = rng.integers(0, 5, (24, 24)).astype(np.float32)
        want = _naive_clamp(d, 0.5)
        got = d.astype(np.float64).copy()
        for s in sorted(np.unique(np.round(d)), reverse=True):
            seed = np.round(got) == s
            if not seed.any():
                continue
            dist = cv2.distanceTransform((~seed).astype(np.uint8),
                                         cv2.DIST_C, 3)
            np.clip(got, s - 0.5 * dist, s + 0.5 * dist, out=got,
                    where=~seed)
        assert np.abs(got.astype(np.float32) - want).max() < 1e-4


def test_clamp_reduces_but_does_not_guarantee_the_bound():
    """It is a one-pass sequential clamp, and that has consequences.

    Each depth value is visited once, so a later pass can reintroduce a
    violation against a seed set an earlier pass established. The paper's
    Algorithm 1 is written this way and makes no convergence claim; on
    pure noise the residual is large, and the honest statement is that
    steep transitions are reduced rather than forbidden. On a real depth
    map -- already median filtered and bilaterally refined before this
    sees it -- the input is nothing like noise and the reduction is what
    the sweeps measured.
    """
    rng = np.random.default_rng(23)
    d = rng.uniform(0, 15, (40, 40)).astype(np.float32)
    out = slope.clamp(d, 0.4)
    steep = lambda a: max(np.abs(np.diff(a, axis=1)).max(),
                          np.abs(np.diff(a, axis=0)).max())
    assert steep(out) < steep(d) / 3
    assert np.abs(np.diff(out, axis=1)).mean() < \
        np.abs(np.diff(d, axis=1)).mean() / 3


def test_clamp_barely_moves_a_map_that_already_obeys_the_bound():
    """A gentle ramp survives, to within the seed sets' own rounding.

    Not exactly: seeds are taken at rounded depths while the map is
    continuous after sub-slice refinement, so a ramp gets nudged where it
    crosses a rounding boundary. Bounded by half a slice, and measured at
    0.07 for this one.
    """
    ramp = np.tile(np.linspace(0, 4, 50, dtype=np.float32), (20, 1))
    out = slope.clamp(ramp, 0.5)
    assert np.abs(out - ramp).max() < 0.5
    assert np.corrcoef(out.ravel(), ramp.ravel())[0, 1] > 0.999


def test_clamp_pulls_a_step_apart_over_the_permitted_distance():
    step = np.zeros((20, 40), np.float32)
    step[:, 20:] = 10.0
    out = slope.clamp(step, 0.5)
    # Far from the boundary the low side is still low, near it the map
    # has to climb, and it may climb no faster than the bound.
    assert out[10, 0] < 1.0
    assert out[10, 19] > 8.0
    assert np.abs(np.diff(out[10])).max() <= 0.5 + 1e-3


def test_clamp_is_monotone_in_the_bound():
    rng = np.random.default_rng(3)
    d = rng.uniform(0, 12, (48, 48)).astype(np.float32)
    loose = slope.clamp(d, 4.0)
    tight = slope.clamp(d, 0.25)
    assert np.abs(tight - d).mean() > np.abs(loose - d).mean()


def test_default_slope_is_in_range_and_falls_with_aperture():
    wide = slope.default_slope(30, 10.0, 0.32, 2.43)
    assert slope.SLOPE_MIN <= wide <= slope.SLOPE_MAX
    # A higher NA opens a wider cone, so one slice of defocus spreads
    # further and the map must change more slowly.
    assert slope.default_slope(30, 40.0, 0.65, 2.43) < wide


def test_default_slope_survives_nonsense_optics():
    assert slope.SLOPE_MIN <= slope.default_slope(10, 0, 0, 0) \
        <= slope.SLOPE_MAX
