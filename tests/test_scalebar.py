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

def test_the_bar_lands_in_the_bottom_right_and_nowhere_else():
    img = _frame(1200, 800)
    assert scalebar.draw(img, 1.0)
    h, w = img.shape[:2]
    # The top-left three quarters of the picture are untouched.
    assert (img[:h // 2, :w // 2] == 128).all(), "drew over the subject"
    corner = img[h // 2:, w // 2:]
    assert (corner == 255).any(), "no plate"
    assert (corner < 60).any(), "no ink"


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


def test_a_bar_that_would_not_fit_is_not_drawn_clipped():
    tiny = _frame(60, 40)
    before = tiny.copy()
    scalebar.draw(tiny, 1.0)
    assert np.array_equal(tiny, before), "drew a clipped bar"


def test_the_label_reads_in_whichever_unit_is_sensible():
    assert scalebar.label(1) == "1 um"
    assert scalebar.label(500) == "500 um"
    assert scalebar.label(1000) == "1 mm"
    assert scalebar.label(5000) == "5 mm"
    # ASCII: this goes through a Hershey font with no micro sign, and a
    # hollow box on the one element making a claim is worse than "um".
    assert all(ord(c) < 128 for c in scalebar.label(200))


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
