"""Four named reticles, and a graduated one that means micrometres."""
from __future__ import annotations

import numpy as np
import pytest
from PySide6 import QtCore, QtGui


def _view(qapp, style, um_per_px=None):
    from darlaston.ui.widgets import LiveView
    view = LiveView()
    view.resize(800, 600)
    view.framing_cross = True
    view.crosshair = style
    view.set_scale_bar(um_per_px, {})
    rgb = np.full((300, 400, 3), 200, np.uint8)
    view.set_frame(rgb, None)
    view.show()
    for _ in range(3):
        qapp.processEvents()
    return view


def _lines(view):
    """What _draw_guides would draw, by driving it with a recording painter."""
    drawn = {"lines": [], "ellipses": 0, "texts": []}
    class P(QtGui.QPainter):
        pass
    image = QtGui.QImage(800, 600, QtGui.QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QtGui.QPainter(image)
    orig_lines, orig_ellipse, orig_path = painter.drawLines, painter.drawEllipse, painter.drawPath
    painter.drawLines = lambda ls: drawn["lines"].extend(ls)
    painter.drawEllipse = lambda *a: drawn.__setitem__("ellipses", drawn["ellipses"] + 1)
    painter.drawPath = lambda path: drawn["texts"].append(path.boundingRect())
    view._draw_guides(painter, QtCore.QRect(0, 0, 800, 600))
    painter.end()
    return drawn


@pytest.mark.parametrize("style", ["plain", "open", "reticle", "graduated"])
def test_every_cross_draws(qapp, style):
    view = _view(qapp, style)
    drawn = _lines(view)
    assert drawn["lines"], style
    assert drawn["ellipses"] == (2 if style == "reticle" else 0), "the circle, dark under light"


def test_the_open_cross_leaves_the_centre_alone(qapp):
    drawn = _lines(_view(qapp, "open"))
    centre = QtCore.QPointF(400, 300)
    for line in drawn["lines"][::2]:   # the first pass draws each line once
        for pt in (line.p1(), line.p2()):
            assert (pt - centre).manhattanLength() > 5, "a line reaches the centre"


def test_the_graduated_cross_ticks_in_micrometres_only_when_the_scale_is_known(qapp):
    bare = _lines(_view(qapp, "graduated"))
    assert len(bare["lines"]) == 2 * 2 and not bare["texts"], "a plain cross until the optics say"
    # 1 um per pixel: 20 um ticks are 20 px apart, the arm is 0.2 * 600 = 120 px, six ticks.
    ruled = _lines(_view(qapp, "graduated", um_per_px=1.0))
    first_pass = ruled["lines"][: len(ruled["lines"]) // 2]
    ticks = len(first_pass) - 2
    assert ticks == 6 * 4, f"six ticks on each of four arms, got {ticks}"
    assert ruled["texts"], "and the arm's length written once"
    # Coarser optics: 10 um per pixel means 200 um ticks (20 px), the label says so.
    coarse = _view(qapp, "graduated", um_per_px=10.0)
    lines, label = coarse._graduated(QtCore.QRect(0, 0, 800, 600), QtCore.QPointF(400, 300), 600, [])
    assert label is not None and label[1] == "1.2 mm"


def test_choosing_a_cross_turns_the_cross_on_and_is_remembered(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.session.settings import Settings
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        assert win.settings.crosshair == "plain"
        win._crosshair_actions["reticle"].trigger()
        assert win.settings.crosshair == "reticle" and win.settings.framing_cross
        assert win.cross_action.isChecked()
        assert win.view.crosshair == "reticle" and win.view.framing_cross
        assert Settings.load().crosshair == "reticle"
    finally:
        win.shutdown()
