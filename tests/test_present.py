"""The presentation window: a mirror with a caption, for an audience.

The tests hold it to the promises that make it safe to leave running in
front of strangers: the picture is never decorated in the buffer, the
ink adapts to the field it sits over, the live marker cannot outlive the
feed, and a fault on the audience's face never reaches the operator's.
"""
from __future__ import annotations

import types

import numpy as np
import pytest
from PySide6 import QtCore, QtGui, QtTest

from darlaston.session.settings import Settings
from darlaston.ui.present import HeaderDialog, PresentView, PresentWindow


def _frame(level: int, w: int = 600, h: int = 400) -> np.ndarray:
    return np.full((h, w, 3), level, np.uint8)


def _pixels(view) -> np.ndarray:
    img = view.grab().toImage().convertToFormat(
        QtGui.QImage.Format.Format_RGB888)
    raw = np.frombuffer(img.constBits(), np.uint8)
    rows = raw.reshape(img.height(), img.bytesPerLine())
    return rows[:, :img.width() * 3].reshape(img.height(), img.width(),
                                             3).copy()


# ---- settings ---------------------------------------------------------------

def test_present_settings_survive_a_restart(tmp_path):
    s = Settings()
    s.present_header_title = "San Francisco Microscopical Society"
    s.present_header_subtitle = "sfmicrosociety.org"
    s.present_header = True
    s.present_live = True
    s.present_scale_bar = False
    path = tmp_path / "settings.json"
    s.save(path)
    back = Settings.load(path)
    assert back.present_header_title == s.present_header_title
    assert back.present_header_subtitle == s.present_header_subtitle
    assert back.present_header and back.present_live
    assert not back.present_scale_bar


# ---- the view ---------------------------------------------------------------

def test_the_picture_is_never_decorated_in_the_buffer(qapp):
    """Overlays are painted, the frame is blitted. What Qt composites is
    the photograph, so nothing here can ever leak into a capture path."""
    view = PresentView()
    view.resize(600, 400)
    view.set_scale_bar(2.0, Settings().bar_style())
    view.set_header("San Francisco Microscopical Society",
                    "sfmicrosociety.org")
    view.set_frame(_frame(120))
    assert np.array_equal(view._buf, _frame(120))
    assert view._bar._tile is not None, "no bar was rendered"
    assert view._bar.parent() is view


def test_the_frame_is_letterboxed_not_stretched(qapp):
    view = PresentView()
    view.resize(600, 400)
    view.set_frame(_frame(120, w=400, h=400))     # square into 3:2
    at = view._image_at
    assert at.width() == at.height() == 400
    assert at.left() == 100 and at.top() == 0


def test_captions_appear_and_read_dark_on_a_bright_field(qapp):
    view = PresentView()
    view.resize(600, 400)
    view.set_frame(_frame(240))
    before = _pixels(view)
    view.set_header("Waterbears", "Found in moss outside")
    after = _pixels(view)
    changed = np.any(before != after, axis=2)
    assert changed.sum() > 50, "the header drew nothing"
    # Dark ink on the bright field: the lettering is darker than the
    # ground it replaced, not lighter.
    assert (after[changed].mean() < before[changed].mean()
            ), "ink did not darken a bright field"
    # And in the top left corner, where a header belongs.
    ys, xs = np.nonzero(changed)
    assert ys.mean() < 200 and xs.mean() < 300


def test_captions_read_light_on_a_dark_field(qapp):
    view = PresentView()
    view.resize(600, 400)
    view.set_frame(_frame(8))
    before = _pixels(view)
    view.set_subject("Waterbears", "Found in moss outside")
    after = _pixels(view)
    changed = np.any(before != after, axis=2)
    assert changed.sum() > 50
    assert (after[changed].mean() > before[changed].mean()
            ), "ink did not lighten a dark field"
    ys, xs = np.nonzero(changed)
    assert ys.mean() > 200 and xs.mean() < 300, "subject belongs bottom left"


def test_magnification_sits_bottom_right(qapp):
    view = PresentView()
    view.resize(600, 400)
    view.set_frame(_frame(240))
    before = _pixels(view)
    view.set_magnification("250×", "25×/0.65")
    after = _pixels(view)
    changed = np.any(before != after, axis=2)
    assert changed.sum() > 50
    ys, xs = np.nonzero(changed)
    assert ys.mean() > 200 and xs.mean() > 300


def test_the_live_marker_is_red_and_can_go_out(qapp):
    view = PresentView()
    view.resize(600, 400)
    view.set_frame(_frame(120))
    view.set_live(True)
    lit = _pixels(view)
    reds = (lit[..., 0].astype(int) - lit[..., 1].astype(int)) > 20
    assert reds.sum() > 10, "no red dot was drawn"
    view.set_live_lit(False)
    out = _pixels(view)
    reds = (out[..., 0].astype(int) - out[..., 1].astype(int)) > 20
    assert reds.sum() == 0, "the marker survived the feed stopping"


def test_the_ink_is_read_per_corner(qapp):
    """A darkfield subject can blow one corner white while the others
    stay black; each caption answers for its own ground."""
    view = PresentView()
    view.resize(600, 400)
    frame = _frame(8)
    frame[:100, :200] = 245                       # bright top left only
    view.set_frame(frame)
    assert view._corner_light("tl") is True
    assert view._corner_light("br") is False


def test_the_corner_is_not_re_read_every_frame(qapp):
    view = PresentView()
    view.resize(600, 400)
    view.set_frame(_frame(240))
    assert view._corner_light("tl") is True
    view.set_frame(_frame(8))
    assert view._corner_light("tl") is True, "hysteresis read too eagerly"
    for _ in range(view.INK_EVERY + 1):
        view.set_frame(_frame(8))
    assert view._corner_light("tl") is False


# ---- the window -------------------------------------------------------------

def test_the_live_marker_cannot_outlive_the_feed(qapp):
    w = PresentWindow()
    w.view.set_live(True)
    w.set_frame(_frame(120))
    assert w.view._live_lit
    w._last_frame -= w.STALE_S + 1.0
    w._check_stale()
    assert not w.view._live_lit, "a frozen frame stayed marked live"
    w.set_frame(_frame(120))
    assert w.view._live_lit, "the marker did not relight with the feed"


def test_escape_closes_and_says_so(qapp):
    w = PresentWindow()
    told = []
    w.closed.connect(lambda: told.append(True))
    w.show()
    QtTest.QTest.keyClick(w, QtCore.Qt.Key.Key_Escape)
    assert told, "closing did not reach the closed signal"
    assert not w.isVisible()


def test_the_header_dialog_decides_whether_there_is_a_header(qapp):
    s = Settings()
    d = HeaderDialog(s)
    d.first.setText("San Francisco Microscopical Society")
    d.second.setText("sfmicrosociety.org")
    d.accept()
    assert s.present_header
    assert s.present_header_title == "San Francisco Microscopical Society"

    d = HeaderDialog(s)
    d.first.setText("")
    d.second.setText("")
    d.accept()
    assert not s.present_header, "an empty header stayed switched on"


# ---- the window in the application ------------------------------------------

def test_the_menu_and_the_window_are_one_truth(window):
    win = window()
    assert win.present_window is None, "built before anybody asked"
    win.present_action.setChecked(True)
    assert win.present_window is not None
    assert win.present_window.isVisible()

    # Closed by its own button: the check mark follows, and the place it
    # sat is kept for next time.
    win.present_window.close()
    assert not win.present_action.isChecked()
    assert win.settings.present_geometry


def test_the_frame_and_the_captions_reach_the_window(window):
    win = window()
    win.present_action.setChecked(True)
    win.subject.edit.setText("Waterbears")
    win.subject.slide.setText("Found in moss outside")
    win.settings.present_live = True
    s = types.SimpleNamespace(preview=_frame(120, w=160, h=120))
    win._offer_present(s)
    view = win.present_window.view
    assert view._image is not None
    assert view._subject == ("Waterbears", "Found in moss outside")
    assert view._live is True
    # No configured optics on the synthetic bench, so the window says
    # nothing about magnification rather than guessing.
    assert view._magnification == ("", "")


def test_a_present_fault_never_reaches_the_viewfinder(window, monkeypatch):
    """The shield the live bar earned the hard way, kept here too: the
    audience's window closing is a mishap, the operator's preview going
    black is not allowed."""
    win = window()
    win.present_action.setChecked(True)

    def boom(_frame):
        raise RuntimeError("synthetic fault")

    monkeypatch.setattr(win.present_window.view, "set_frame", boom)
    s = types.SimpleNamespace(preview=_frame(120, w=160, h=120))
    win._offer_present(s)                          # must not raise
    assert not win.present_window.isVisible()


def test_subject_off_means_the_words_come_down(window):
    win = window()
    win.present_action.setChecked(True)
    win.subject.edit.setText("Waterbears")
    s = types.SimpleNamespace(preview=_frame(120, w=160, h=120))
    win._offer_present(s)
    assert win.present_window.view._subject[0] == "Waterbears"
    win.settings.present_subject = False
    win._offer_present(s)
    assert win.present_window.view._subject == ("", "")


def test_magnification_is_empty_without_a_described_setup(window):
    win = window()
    assert win._present_magnification() == ("", "")
