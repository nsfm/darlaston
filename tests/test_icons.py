"""The small drawn marks.

Qt's defaults are a filled triangle and whatever glyph the fallback font
has for a multiplication sign. This interface is built out of one-pixel
hairlines, so both were the heaviest thing on the screen, attached to the
least important controls.
"""
import re

import pytest

from darlaston.ui import icons, theme


def test_every_icon_is_a_single_stroked_svg():
    """One weight throughout. A filled shape among stroked ones reads as a
    different family, which is how this looked before."""
    assert icons.available(), "no icons found at all"
    for name in icons.available():
        src = icons._source(name)
        assert "currentColor" in src, f"{name} hardcodes a colour"
        assert 'viewBox="0 0 16 16"' in src, f"{name} is not on the 16px grid"
        # Stroked, not filled: every path either says fill="none" or
        # inherits it from a group that does.
        assert 'fill="none"' in src, f"{name} is filled rather than stroked"
        widths = set(re.findall(r'stroke-width="([\d.]+)"', src))
        assert widths, f"{name} sets no stroke width"
        assert all(1.0 <= float(w) <= 1.5 for w in widths), \
            f"{name} is off-weight: {widths}"


def test_tinting_replaces_the_colour_and_nothing_else():
    plain = icons._source("close")
    out = icons.tinted("close", theme.BRASS).decode()
    assert theme.BRASS in out
    assert "currentColor" not in out
    assert len(out) - len(plain) == (len(theme.BRASS) - len("currentColor")) * \
        plain.count("currentColor")


def test_icons_actually_render(qapp):
    """A build without Qt's SVG plugin returns a null icon rather than
    raising, but if that happens here the interface has lost its marks and
    we want to know from a test rather than from a screenshot."""
    for name in icons.available():
        ic = icons.icon(name, theme.DIM, 16)
        assert not ic.isNull(), f"{name} did not render"
        pix = ic.pixmap(16, 16)
        assert not pix.isNull() and pix.width() == 16


def test_hover_icons_carry_both_states(qapp):
    """Qt draws Mode.Active under the pointer, so hover works with nothing
    connected -- which is how the text versions behaved, and becoming a
    picture should not have cost that."""
    from PySide6 import QtGui

    ic = icons.hover_icon("close", theme.DIM, theme.INK, 12)
    rest = ic.pixmap(12, 12, QtGui.QIcon.Mode.Normal).toImage()
    over = ic.pixmap(12, 12, QtGui.QIcon.Mode.Active).toImage()
    assert not rest.isNull() and not over.isNull()
    assert rest != over, "the pointed-at state is identical to the resting one"


def test_stylesheet_paths_exist_and_are_reachable_by_qt(qapp):
    """Stylesheets cannot tint an image, so CSS-reached marks need a real
    file in the colour they will be drawn in."""
    from pathlib import Path

    p = icons.path_for("chevron-down", theme.DIM)
    assert Path(p).exists()
    assert theme.DIM in Path(p).read_text()
    assert "\\" not in p, "Qt wants forward slashes inside url()"
    # Cached rather than rewritten on every call.
    assert icons.path_for("chevron-down", theme.DIM) == p

    css = theme.stylesheet()
    assert "down-arrow" in css and "image: url(" in css
    for url in re.findall(r"image: url\(([^)]+)\)", css):
        assert Path(url).exists(), f"stylesheet points at a missing {url}"


def test_a_missing_icon_says_which_one():
    with pytest.raises(FileNotFoundError, match="nosuchmark"):
        icons._source("nosuchmark")
