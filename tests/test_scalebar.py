"""The one element on a photograph that makes a claim about the world.

Every test here is a case where the right answer is to draw nothing. That
is the whole design: a missing scale bar is a mild disappointment and a
wrong one gets published.
"""
import numpy as np
import pytest

from darlaston.process import scalebar


def _frame(w=5440, h=3648):
    # Mid grey, so a white plate and dark ink are both visible against it.
    return np.full((h, w, 3), 128, np.uint8)


# ---- choosing the length ---------------------------------------------------

def test_the_bar_says_a_round_number_or_nothing():
    """"137 um" is arithmetic showing through. Nobody reads that as a
    reference length."""
    for um_per_px in (0.0231, 0.24, 1.7, 13.0):
        picked = scalebar.choose(um_per_px, 5440)
        assert picked is not None
        micrometres, _ = picked
        assert micrometres in scalebar.STEPS, micrometres


def test_the_bar_never_spans_the_picture():
    """A bar across a third of the frame is a reference. One across the
    whole of it is a stripe."""
    for um_per_px in (0.01, 0.24, 2.0, 40.0):
        picked = scalebar.choose(um_per_px, 5440)
        if picked is None:
            continue
        _, length = picked
        assert length <= 5440 * scalebar.MAX_FRACTION + 1


def test_a_field_too_small_for_the_finest_step_gets_no_bar():
    """At very high magnification the whole field may be under a
    micrometre. There is no honest round number to draw."""
    assert scalebar.choose(0.00002, 400) is None


def test_nothing_is_drawn_from_an_unknown_scale():
    assert scalebar.choose(0.0, 5440) is None
    assert scalebar.choose(-1.0, 5440) is None
    img = _frame()
    before = img.copy()
    assert not scalebar.draw(img, None)
    assert np.array_equal(img, before), "drew something from nothing"


# ---- drawing ---------------------------------------------------------------

@pytest.mark.parametrize("style", scalebar.STYLES)
def test_every_style_lands_in_the_bottom_right_and_nowhere_else(style):
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, style=style), style
    h, w = img.shape[:2]
    # The top-left three quarters of the picture are untouched, whatever
    # furniture the style brings with it.
    assert (img[:h // 2, :w // 2] == 128).all(), "drew over the subject"
    corner = img[h // 2:, w // 2:]
    assert not (corner == 128).all(), "drew nothing"


def test_the_adaptive_style_flips_its_ink_to_suit_the_field():
    """Dark ink on brightfield, light on darkfield, and the decision is
    measured off the pixels the bar is about to cover rather than assumed
    from the illumination setting -- which is right about the lamp and
    silent about the subject."""
    def ink_of(background):
        img = np.full((900, 1400, 3), background, np.uint8)
        assert scalebar.draw(img, 1.0, style="adaptive")
        corner = img[450:, 700:]
        moved = corner[corner[..., 0] != background]
        return float(moved.mean())

    assert ink_of(230) < 80, "drew light ink on a bright field"
    assert ink_of(20) > 180, "drew dark ink on a dark field"


def test_it_scales_with_the_frame_rather_than_the_pixel():
    """The same bar on a 20 MP capture and on a plate cell a fifth the
    size. A fixed pixel thickness is a hairline on one and a slab on the
    other."""
    small, big = _frame(560, 373), _frame(5440, 3648)
    assert scalebar.draw(small, 5.0)
    assert scalebar.draw(big, 0.515)

    def ink_rows(img):
        dark = (img < 60).all(axis=2)
        return int(dark.any(axis=1).sum())

    # Not equal, but the same order of magnitude relative to the frame.
    ratio = ink_rows(big) / max(ink_rows(small), 1)
    assert 3 < ratio < 30, f"thickness ratio {ratio:.1f} for a 9.7x frame"


@pytest.mark.parametrize("style", scalebar.STYLES)
def test_a_bar_that_would_not_fit_is_not_drawn_clipped(style):
    tiny = _frame(60, 40)
    before = tiny.copy()
    scalebar.draw(tiny, 1.0, style=style)
    assert np.array_equal(tiny, before), f"{style} drew a clipped bar"


def test_an_unknown_style_falls_back_rather_than_failing():
    """A settings file from a newer version, or a hand-edited one. The
    measurement is the point and the dressing is not worth a crash."""
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, style="art-deco")
    assert not (img[550:, 800:] == 128).all()


def test_the_label_reads_in_whichever_unit_is_sensible():
    assert scalebar.label(1) == "1 \u00b5m"
    assert scalebar.label(500) == "500 \u00b5m"
    assert scalebar.label(1000) == "1 mm"
    assert scalebar.label(5000) == "5 mm"


def test_the_ascii_spelling_is_available_but_is_not_the_default():
    """It used to be the only option, because the Hershey stroke font has
    no glyph for the sign and would have drawn a hollow box on the one
    element of the picture that makes a claim. With a real rasteriser the
    correct character is the default, and "um" is the escape hatch for
    tooling further down the line that cannot take it."""
    assert scalebar.label(200, plain=True) == "200 um"
    assert all(ord(c) < 128 for c in scalebar.label(200, plain=True))
    assert "\u00b5" in scalebar.label(200)


def test_a_mono_frame_is_left_alone_rather_than_crashing():
    """Roughly a quarter of ToupTek's microscopy range is monochrome."""
    grey = np.full((400, 600), 128, np.uint8)
    before = grey.copy()
    assert not scalebar.draw(grey, 1.0)
    assert np.array_equal(grey, before)


@pytest.mark.parametrize("um_per_px", [0.0515, 0.129, 0.24, 0.515, 1.29])
def test_the_drawn_length_matches_what_it_claims(um_per_px):
    """The bar is a measurement. Its pixel length must be the labelled
    micrometres divided by the scale, within a pixel of rounding."""
    picked = scalebar.choose(um_per_px, 5440)
    assert picked is not None
    micrometres, length = picked
    assert abs(length * um_per_px - micrometres) < um_per_px


# ---- corner and size -------------------------------------------------------

@pytest.mark.parametrize("corner,left,top", [
    ("br", False, False), ("bl", True, False),
    ("tr", False, True), ("tl", True, True)])
def test_the_bar_lands_in_the_corner_it_was_asked_for(corner, left, top):
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, corner=corner)
    ys, xs = np.where((img != 128).any(axis=2))
    h, w = img.shape[:2]
    # bool(), because a numpy bool is not a Python bool and `is False`
    # against one is quietly always false.
    assert bool(xs.max() < w / 2) is left, f"{corner}: wrong side"
    assert bool(ys.max() < h / 2) is top, f"{corner}: wrong half"


def test_the_label_stays_inside_the_frame_in_a_top_corner():
    """The label sits above the rule in every corner, so at the top that
    room has to come out of the inset rather than out of the picture."""
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, corner="tl", size="large")
    ys, _xs = np.where((img != 128).any(axis=2))
    assert ys.min() > 0, "the type ran off the top edge"


@pytest.mark.parametrize("size", ["small", "medium", "large"])
def test_size_changes_the_furniture_and_never_the_measurement(size):
    """A presentation control that silently reported 500 um where the last
    one said 200 would be a measurement changing itself to suit taste."""
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, size=size)
    # The length comes from the ladder, which `size` does not touch.
    assert scalebar.choose(1.0, 1600) == (500, 500)


def test_larger_really_is_larger():
    heights = []
    for size in ("small", "medium", "large"):
        img = _frame(1600, 1100)
        assert scalebar.draw(img, 1.0, size=size)
        ys, _ = np.where((img != 128).any(axis=2))
        heights.append(ys.max() - ys.min())
    assert heights[0] < heights[1] < heights[2], heights


def test_an_unknown_corner_or_size_falls_back_rather_than_failing():
    """A settings file from a newer version, or a hand-edited one."""
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, corner="middle", size="enormous")


def _rule_and_label(img):
    """(row of the rule, mean row of the label).

    The rule is the row carrying the most ink, being a solid line across
    the bar's whole length. Everything else is type.
    """
    ink = (img != 128).any(axis=2)
    per_row = ink.sum(axis=1)
    rule = int(np.argmax(per_row))
    rows = np.where(per_row > 0)[0]
    label = rows[np.abs(rows - rule) > 3]
    return rule, float(label.mean())


@pytest.mark.parametrize("corner,below", [("br", False), ("bl", False),
                                          ("tl", True), ("tr", True)])
def test_automatic_puts_the_label_on_the_inside(corner, below):
    """The type ends up the part further from the edge of the frame: below
    the rule at the top, above it at the bottom."""
    img = _frame(1600, 1100)
    assert scalebar.draw(img, 1.0, corner=corner, label_at="auto")
    rule, label = _rule_and_label(img)
    assert (label > rule) is below, f"{corner}: label on the wrong side"


def test_the_label_side_can_be_overridden():
    """It is taste, so it is a choice rather than only a rule."""
    for corner in ("br", "tl"):
        above = _frame(1600, 1100)
        below = _frame(1600, 1100)
        assert scalebar.draw(above, 1.0, corner=corner, label_at="above")
        assert scalebar.draw(below, 1.0, corner=corner, label_at="below")
        ra, la = _rule_and_label(above)
        rb, lb = _rule_and_label(below)
        assert la < ra, f"{corner}: 'above' put the label below the rule"
        assert lb > rb, f"{corner}: 'below' put the label above the rule"


def test_the_shared_resolver_prefers_the_profile_and_falls_back_to_the_sdk():
    """The capture and the live overlay have to agree about scale. They
    did not: the live path read only the profile, so Nate's camera, whose
    profile carries pixel_um 0.0, got no bar anywhere and no explanation.
    """
    import types

    from darlaston.process.metadata import sensor_pitch

    told = types.SimpleNamespace(camera=types.SimpleNamespace(pixel_um=2.4))
    unknown = types.SimpleNamespace(camera=types.SimpleNamespace(pixel_um=0.0))
    info = types.SimpleNamespace(resolutions=(
        types.SimpleNamespace(index=1, pixel_um=9.6),
        types.SimpleNamespace(index=0, pixel_um=4.8)))

    assert sensor_pitch(told, info) == 2.4, "the profile is the better answer"
    # Full resolution is index 0, and binned modes report a larger pitch.
    assert sensor_pitch(unknown, info) == 4.8
    assert sensor_pitch(unknown, None) is None
    assert sensor_pitch(unknown, types.SimpleNamespace(resolutions=())) is None
