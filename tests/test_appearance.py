"""Setup > Appearance: the rail's side, the window frame, room for more."""
from __future__ import annotations

import pytest


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    return MainWindow(lambda: MockCamera(fps=30.0))


def _order(win) -> list[str]:
    body = win._body
    names = []
    for i in range(body.count()):
        w = body.itemAt(i).widget()
        names.append("rail" if w is win._rail_widget else "picture")
    return names


def test_the_rail_sits_on_the_right_until_asked(qapp, tmp_path, monkeypatch):
    win = _window(tmp_path, monkeypatch)
    try:
        assert _order(win) == ["picture", "rail"]
        win.settings.sidebar_side = "left"
        win._place_rail()
        assert _order(win) == ["rail", "picture"]
        win.settings.sidebar_side = "right"
        win._place_rail()
        assert _order(win) == ["picture", "rail"]
    finally:
        win.shutdown()


def test_the_appearance_page_moves_the_rail_and_remembers(qapp, tmp_path, monkeypatch):
    from darlaston.session.settings import Settings
    from darlaston.ui.appearance_ui import AppearanceDialog

    win = _window(tmp_path, monkeypatch)
    try:
        dialog = AppearanceDialog(win.settings, win._place_rail, win,
                                  frame_available=True, frame_on=False,
                                  set_frame=lambda on: False)
        left = next(b for b in dialog._side.buttons() if b.property("value") == "left")
        left.setChecked(True)
        assert _order(win) == ["rail", "picture"]
        assert Settings.load().sidebar_side == "left", "saved on the change, not on close"
        # A frame the window cannot take puts its tick back.
        dialog.frame.setChecked(True)
        assert not dialog.frame.isChecked()
    finally:
        win.shutdown()


def test_the_frame_row_is_absent_where_the_frame_is_never_ours(qapp, tmp_path, monkeypatch):
    from darlaston.session.settings import Settings
    from darlaston.ui import appearance_ui
    from darlaston.ui.appearance_ui import AppearanceDialog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(appearance_ui.sys, "platform", "darwin")
    dialog = AppearanceDialog(Settings(), lambda: None, None,
                              frame_available=True, frame_on=False)
    assert not dialog.frame.isVisibleTo(dialog)


def test_the_setup_menu_offers_appearance_and_not_a_lone_frame_tick(qapp, tmp_path, monkeypatch):
    win = _window(tmp_path, monkeypatch)
    try:
        assert not hasattr(win, "chrome_action")
        assert hasattr(win, "_open_appearance")
    finally:
        win.shutdown()
