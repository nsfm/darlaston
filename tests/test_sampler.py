"""Choosing a halo setting by looking at crops of the merge.

The load-bearing property is that a crop rendered here equals the same
region of the merge's own blend: the same clamp, the same per-pixel hat
width, the same normalisation and the same feather. If that ever stops
being true the operator is choosing against one picture and receiving
another, so it is tested rather than trusted.
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


def _whole_frame_blend(lumas, depth, slope_, width, feather):
    """What `merge` does, on the whole frame, at half res.

    Deliberately a transcription of the blend in `stack.merge` rather than
    a call into it, so that a change there which the sampler does not
    follow shows up as a failure here.
    """
    d = slope.clamp(depth, slope_) if slope_ > 0 else depth
    norm = np.zeros_like(d)
    for i in range(len(lumas)):
        norm += np.clip(1.0 - np.abs(d - i) / width, 0.0, 1.0)
    np.maximum(norm, 1e-6, out=norm)
    acc = np.zeros_like(d)
    for i in range(len(lumas)):
        hat = np.clip(1.0 - np.abs(d - i) / width, 0.0, 1.0) / norm
        if feather > 0:
            hat = cv2.GaussianBlur(hat, (0, 0), feather)
        acc += lumas[i] * hat
    return acc


def test_render_matches_the_merges_own_blend():
    """The claim the whole feature rests on: what is shown is what lands.

    Exact, not close. The operator is choosing against these crops and
    receiving a different render, so any gap here is a gap between the
    decision and its consequence. An earlier version skipped the hat
    width, the normalisation and the feather, and differed from the real
    blend by a mean of 1.2 and a maximum of 23 counts.
    """
    lumas, depth, disk = _scene()
    levels = len(lumas)
    boxes = sampler.crops(depth, disk, count=2, size=64)
    assert boxes
    for slope_ in (0.0, 0.2, 0.5):
        for width in (0.5, 1.0):
            for feather in (0.0, 2.0):
                whole = _whole_frame_blend(lumas, depth, slope_, width,
                                           feather)
                for (y, x) in boxes:
                    got = sampler.render(lumas, depth, (y, x), slope_,
                                         levels, size=64, width=width,
                                         feather=feather)
                    want = whole[y:y + 64, x:x + 64]
                    # A twentieth of a count, on a sensor whose values run
                    # to thousands. What is left at this level is float32
                    # accumulation order: a Gaussian over a crop and the
                    # same Gaussian over the frame do not sum identically,
                    # and neither does a sum over slices. Orders of
                    # magnitude below one step of the 16-bit output.
                    assert np.abs(got - want).max() < 0.05


def test_render_follows_a_per_pixel_hat_width():
    """Width arrives as a field, not a number, whenever confidence varies."""
    lumas, depth, disk = _scene()
    width = np.where(disk, 0.5, 4.0).astype(np.float32)
    whole = _whole_frame_blend(lumas, depth, 0.3, width, 2.0)
    for (y, x) in sampler.crops(depth, disk, count=1, size=64):
        got = sampler.render(lumas, depth, (y, x), 0.3, len(lumas),
                             size=64, width=width, feather=2.0)
        assert np.abs(got - whole[y:y + 64, x:x + 64]).max() < 0.05


def test_feather_alone_still_needs_a_margin():
    """With no bound the clamp reach is zero, but the blur still reaches."""
    assert sampler.margin_for(0.0, 15, feather=2.0) >= 6
    assert sampler.margin_for(0.0, 15, feather=0.0) == 0


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


# ---- the dialog -------------------------------------------------------------

def test_the_dialog_returns_the_bound_that_was_clicked(qapp):
    """Clicking a tile is the whole interface, so it is worth a test.

    The maths above proves the crops are right; this proves the answer
    gets back out. A merge is parked on that answer, so a tile that looks
    clickable and returns nothing would strand it.
    """
    from darlaston.ui import sampler_ui

    crop = np.linspace(0, 255, 64 * 64, dtype=np.float32).reshape(64, 64)
    rendered = {value: [crop] for _key, value in sampler_ui.CHOICES}
    dialog = sampler_ui.SamplerDialog(rendered)

    tiles = dialog.findChildren(sampler_ui._Tile)
    assert len(tiles) == len(sampler_ui.CHOICES)

    assert dialog.value() is None            # nothing chosen yet
    tiles[2].picked.emit(sampler_ui.CHOICES[2][1])
    assert dialog.value() == sampler_ui.CHOICES[2][1]


def test_the_dialog_says_nothing_when_it_is_dismissed(qapp):
    """Skipping keeps whatever the settings already had, rather than zero."""
    from darlaston.ui import sampler_ui

    crop = np.zeros((64, 64), np.float32)
    dialog = sampler_ui.SamplerDialog(
        {value: [crop] for _key, value in sampler_ui.CHOICES})
    dialog.reject()
    assert dialog.value() is None


def test_a_flat_crop_does_not_divide_by_zero(qapp):
    """A featureless window is exactly what the picker tries to avoid, but
    a stack of blank glass would hand it one anyway."""
    from darlaston.ui import sampler_ui

    flat = np.full((32, 32), 900.0, np.float32)
    pix = sampler_ui._pixmap(flat)
    assert not pix.isNull()
