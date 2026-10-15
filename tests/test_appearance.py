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
        assert "border-right" in win._rail_widget.styleSheet(), "the hairline meets the picture"
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


# ---- the palettes --------------------------------------------------------------

def test_every_palette_is_dark_and_legible():
    """Named for things seen down the tube, and every one dark: this is
    used in a dark room beside a bright eyepiece. Legible by the WCAG
    numbers rather than by eye, so "high contrast" is a figure."""
    from darlaston.ui import theme

    for name, p in theme.PALETTES.items():
        assert p.name == name
        assert theme._luminance(p.bg) < 0.05, f"{name}: ground is not dark"
        assert theme.contrast(p.ink, p.bg) >= 7.0, f"{name}: ink on ground"
        assert theme.contrast(p.dim, p.panel) >= 3.0, f"{name}: dim on panel"
        assert theme.contrast(p.accent, p.bg) >= 3.0, f"{name}: accent on ground"
        assert theme.contrast(p.on_accent, p.accent) >= 4.5, f"{name}: lettering on accent"
        assert theme.contrast(p.bad, p.bg) >= 3.0, f"{name}: the fault colour"
    for name in ("stentor", "diatom"):
        p = theme.PALETTES[name]
        assert theme.contrast(p.ink, p.bg) >= 15.0, f"{name} is the high-contrast one"


def test_using_a_palette_rebinds_what_everything_draws_from():
    from darlaston.ui import theme

    try:
        theme.use("blepharisma")
        assert theme.BRASS == theme.PALETTES["blepharisma"].accent
        assert theme.PALETTES["blepharisma"].bg in theme.stylesheet()
        assert theme.use("no-such-thing").name == theme.DEFAULT_PALETTE
        assert theme.BG == theme.PALETTES[theme.DEFAULT_PALETTE].bg
    finally:
        theme.use(theme.DEFAULT_PALETTE)


def test_the_appearance_page_changes_the_palette_live(qapp, tmp_path, monkeypatch):
    from darlaston.session.settings import Settings
    from darlaston.ui import theme
    from darlaston.ui.appearance_ui import AppearanceDialog

    win = _window(tmp_path, monkeypatch)
    try:
        dialog = AppearanceDialog(win.settings, win._place_rail, win,
                                  on_palette=win._apply_palette)
        pink = next(b for b in dialog._palette.buttons()
                    if b.property("value") == "blepharisma")
        pink.setChecked(True)
        assert theme.current.name == "blepharisma"
        assert theme.PALETTES["blepharisma"].bg in win.styleSheet()
        assert theme.PALETTES["blepharisma"].panel in win._rail_widget.styleSheet()
        assert Settings.load().palette == "blepharisma"
    finally:
        win.shutdown()
        theme.use(theme.DEFAULT_PALETTE)
