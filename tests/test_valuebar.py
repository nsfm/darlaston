"""The sensor bars: a slider that is its own label."""
import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from darlaston.ui import theme
from darlaston.ui.widgets import BG_TEXT, BRASS, ValueBar


@pytest.fixture
def bar(qapp):
    b = ValueBar("exposure")
    b.setRange(0, 1000)
    b.setValue(400)
    b.set_value_text("8.3 ms")
    b.resize(240, ValueBar.HEIGHT)
    return b


def test_it_behaves_like_the_slider_it_replaced(bar):
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 1000, 400)
    seen = []
    bar.valueChanged.connect(seen.append)

    bar.setValue(700)
    assert bar.value() == 700 and seen == [700]

    # Clamped, and silent when nothing changed -- the camera sync sets the
    # value it just read back, and that must not loop.
    bar.setValue(99999)
    assert bar.value() == 1000
    seen.clear()
    bar.setValue(1000)
    assert seen == [], "re-setting the same value emitted a change"


def test_clicking_and_dragging_set_the_value(bar):
    def press(x, kind, buttons):
        return QtGui.QMouseEvent(
            kind, QtCore.QPointF(x, 10), QtCore.QPointF(x, 10),
            QtCore.Qt.MouseButton.LeftButton, buttons,
            QtCore.Qt.KeyboardModifier.NoModifier)

    bar.mousePressEvent(press(bar.width() // 2,
                              QtCore.QEvent.Type.MouseButtonPress,
                              QtCore.Qt.MouseButton.LeftButton))
    assert 450 < bar.value() < 550, f"clicked the middle, got {bar.value()}"

    bar.mouseMoveEvent(press(bar.width() - 1, QtCore.QEvent.Type.MouseMove,
                             QtCore.Qt.MouseButton.LeftButton))
    assert bar.value() == 1000
    bar.mouseMoveEvent(press(-50, QtCore.QEvent.Type.MouseMove,
                             QtCore.Qt.MouseButton.LeftButton))
    assert bar.value() == 0, "dragging past the end should clamp, not wrap"


def test_the_dissolve_keeps_clear_of_the_letterforms(bar):
    """Half amber and half ground is the one background no colour of text
    can be read against -- but a rectangle around a word punches a hole the
    size of the word. The exclusion is the stroked glyph outlines, so the
    pattern runs right up to the letters and between them.
    """
    bar.setValue(120)                      # fill edge inside the label
    halo = bar._text_halo()
    assert not halo.isEmpty()

    img = bar.grab().toImage()

    def is_amber(x, y):
        c = QtGui.QColor(img.pixel(x, y))
        return (abs(c.red() - BRASS.red()) < 24
                and abs(c.green() - BRASS.green()) < 24
                and abs(c.blue() - BRASS.blue()) < 24)

    # Nothing amber lands on a letter or in its ring of air.
    hits = [(x, y) for x in range(bar.width()) for y in range(bar.height())
            if halo.contains(QtCore.QPointF(x + 0.5, y + 0.5))
            and is_amber(x, y)]
    # Everything the failure needs, in the failure. This one only ever
    # missed on a machine nobody could reach interactively, and "18 cells
    # landed on the type" is not enough to work out why from a log.
    assert not hits, (
        f"{len(hits)} dither cells landed on the type at {hits[:12]}\n"
        f"  widget      : {bar.width()}x{bar.height()}\n"
        f"  face asked  : {bar._label_font().family()!r}\n"
        f"  face used   : {QtGui.QFontInfo(bar._label_font()).family()!r}\n"
        f"  px size     : {QtGui.QFontInfo(bar._label_font()).pixelSize()}\n"
        f"  baseline/xs : {bar._text_geometry()}\n"
        f"  halo bounds : {halo.boundingRect()}\n"
        f"  fill edge   : {bar._fill_x()}  label {bar._label!r} "
        f"value {bar._text!r}")

    # And the pattern is still present around them -- an exclusion that
    # cleared the whole area would pass the check above and lose the point.
    box = halo.boundingRect().adjusted(-14, 0, 14, 0)
    near = sum(1 for x in range(max(0, int(box.left())),
                                min(bar.width(), int(box.right())))
               for y in range(bar.height()) if is_amber(x, y))
    assert near > 20, "the dissolve was cleared away entirely, not worked around"


def test_a_full_bar_shows_no_dissolve(qapp):
    """A bar at maximum has nothing left to fade into, and a pattern
    running off the end of the track reads as an unfinished edge."""
    b = ValueBar("gain")
    b.setRange(0, 100)
    b.set_value_text("20.0x")
    b.resize(240, ValueBar.HEIGHT)

    def amber_columns():
        img = b.grab().toImage()
        cols = set()
        for x in range(b.width()):
            for y in range(4, b.height() - 4):
                c = QtGui.QColor(img.pixel(x, y))
                if (abs(c.red() - BRASS.red()) < 24
                        and abs(c.green() - BRASS.green()) < 24
                        and abs(c.blue() - BRASS.blue()) < 24):
                    cols.add(x)
                    break
        return cols

    b.setValue(50)
    mid = amber_columns()
    b.setValue(100)
    full = amber_columns()
    assert len(full) > len(mid), "a fuller bar should carry more amber"

    # At maximum the amber is unbroken: no gaps, which is what a dissolve
    # would leave behind.
    run = sorted(full)
    assert run, "a full bar drew no fill at all"
    gaps = [b - a for a, b in zip(run, run[1:]) if b - a > 1]
    assert not gaps, f"the fill is still dissolving at maximum: {gaps}"


def test_type_flips_colour_across_the_fill(qapp):
    """Drawn twice under opposite clips, so a word lying across the edge is
    dark on its left half and light on its right."""
    b = ValueBar("exposure")
    b.setRange(0, 100)
    b.set_value_text("x")
    b.resize(240, ValueBar.HEIGHT)

    b.setValue(100)                        # the label sits on solid amber
    img = b.grab().toImage()
    darks = sum(1 for x in range(6, 70) for y in range(4, b.height() - 4)
                if QtGui.QColor(img.pixel(x, y)).lightness()
                < BG_TEXT.lightness() + 30)
    assert darks > 20, "no dark type over the amber"

    b.setValue(0)                          # and on the bare ground
    img = b.grab().toImage()
    lights = sum(1 for x in range(6, 70) for y in range(4, b.height() - 4)
                 if QtGui.QColor(img.pixel(x, y)).lightness() > 140)
    assert lights > 20, "no light type over the ground"


def test_the_shutter_reads_against_its_own_fill(qapp):
    """White on this amber is about 1.9:1. The near-black ground is 7:1."""
    from darlaston.ui.capture_ui import ShutterButton

    def luminance(c):
        parts = []
        for v in (c.redF(), c.greenF(), c.blueF()):
            parts.append(v / 12.92 if v <= 0.03928
                         else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

    fill, text = QtGui.QColor(theme.BRASS), QtGui.QColor(theme.BG)
    lo, hi = sorted((luminance(fill), luminance(text)))
    ratio = (hi + 0.05) / (lo + 0.05)
    assert ratio > 4.5, f"the shutter's type is at {ratio:.1f}:1"


def test_the_focus_box_says_what_it_is(qapp):
    """A dashed rectangle over a live image is a question until it is
    named: it could be a crop, a region of interest, a warning."""
    import numpy as np
    from darlaston.ui.widgets import LiveView

    view = LiveView()
    view.resize(600, 400)
    view.set_frame(np.full((300, 450, 3), 128, np.uint8), None)
    view.set_focus_rect((0.2, 0.2, 0.5, 0.5))
    img = view.grab().toImage()

    # The plate is drawn inside the box's own top-left corner, so that
    # corner is darker than the grey field it sits on. The plate is
    # translucent, so over mid grey it lands near lightness 45 rather
    # than at its own value -- which is the point of a plate.
    def darker_than(image, limit):
        return sum(1 for x in range(view.width()) for y in range(view.height())
                   if QtGui.QColor(image.pixel(x, y)).lightness() < limit)

    dark = darker_than(img, 70)
    assert dark > 200, "no plate was drawn for the label"

    # And it refuses to label a box too small to hold the word without
    # covering what the box is pointing at.
    view.set_focus_rect((0.48, 0.48, 0.02, 0.02))
    small = view.grab().toImage()
    tiny_dark = darker_than(small, 70)
    assert tiny_dark < dark, "a tiny box was labelled over its own contents"
