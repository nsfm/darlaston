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


def test_the_dissolve_gives_way_to_the_type(bar):
    """Half amber and half ground is the one background no colour of text
    can be read against, so no dither cell may land behind a word."""
    bar.setValue(120)                      # fill edge inside the label
    rects = bar._text_rects()
    assert len(rects) == 2, "a label and a reading"

    img = bar.grab().toImage()
    # Inside the label's box, every pixel is either the ground or the type
    # -- never a lone amber cell.
    label = rects[0]
    stray = 0
    for x in range(int(label.left()) + 4, int(label.right()) - 4):
        for y in range(4, bar.height() - 4):
            c = QtGui.QColor(img.pixel(x, y))
            if abs(c.red() - BRASS.red()) < 24 and abs(c.green() - BRASS.green()) < 24 \
                    and abs(c.blue() - BRASS.blue()) < 24:
                stray += 1
    assert stray == 0, f"{stray} dither cells landed behind the label"


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
