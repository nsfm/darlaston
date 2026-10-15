"""Setup > Appearance: the rail's side, the window frame, room for more."""
from __future__ import annotations

import pytest
from PySide6 import QtGui


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

def test_every_palette_says_which_it_is_and_is_legible():
    """Named for things seen down the tube. Dark by default, for a dark
    room beside a bright eyepiece; the light ones say so, and the frame
    follows. Legible by the WCAG numbers rather than by eye, so "high
    contrast" is a figure."""
    from darlaston.ui import theme

    assert theme.PALETTES[theme.DEFAULT_PALETTE].dark
    from darlaston.ui import theme

    for name, p in theme.PALETTES.items():
        assert p.name == name
        if p.dark:
            assert theme._luminance(p.bg) < 0.05, f"{name}: ground is not dark"
        else:
            assert theme._luminance(p.bg) > 0.6, f"{name}: ground is not light"
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


def test_the_painted_widgets_and_the_application_palette_follow_the_theme(qapp):
    """The first light palette showed every graph and slider still black:
    widgets.py had a private palette, and spin boxes drew in the desktop's
    colours. Both follow the theme now, in place."""
    from PySide6 import QtGui, QtWidgets
    from darlaston.ui import theme, widgets

    try:
        theme.use("blepharisma-light")
        light = theme.PALETTES["blepharisma-light"]
        assert widgets.INK.name() == light.ink
        assert widgets.PANEL.name() == light.sunk, "a graph sits on the sunk ground"
        assert widgets.BG_TEXT.name() == light.on_accent
        assert widgets.GROUND.name() == light.bg
        pal = QtWidgets.QApplication.instance().palette()
        assert pal.color(QtGui.QPalette.ColorRole.Base).name() == light.sunk
        assert pal.color(QtGui.QPalette.ColorRole.Highlight).name() == light.accent
        assert "QSpinBox, QDoubleSpinBox {" in theme.stylesheet()
        assert light.sunk in theme.stylesheet()
    finally:
        theme.use(theme.DEFAULT_PALETTE)
        assert widgets.INK.name() == theme.PALETTES[theme.DEFAULT_PALETTE].ink


def test_a_dressed_sheet_follows_the_palette(qapp):
    """A sheet set once at construction was the one thing use() could
    not reach. dress() keeps the recipe and applies it again."""
    from PySide6 import QtWidgets
    from darlaston.ui import theme

    label = QtWidgets.QLabel("x")
    theme.dress(label, lambda: f"color: {theme.BRASS};")
    try:
        theme.use("stentor")
        assert theme.PALETTES["stentor"].accent in label.styleSheet()
        theme.use("pseudoscorpion")
        assert theme.PALETTES["pseudoscorpion"].accent in label.styleSheet()
    finally:
        theme.use(theme.DEFAULT_PALETTE)


# ---- marks on the picture -----------------------------------------------------

def test_noctiluca_is_red_on_black_and_its_fault_colour_is_not_red():
    from darlaston.ui import theme

    p = theme.PALETTES["noctiluca"]
    assert p.dark
    r, g, b = QtGui.QColor(p.ink).getRgb()[:3]
    assert r > 2 * g and r > 2 * b, "the ink is red"
    r, g, b = QtGui.QColor(p.bad).getRgb()[:3]
    assert g > 180 and b > 150, "a fault is the one pale thing on the page"


def test_auto_marks_keep_gold_on_a_grey_field_and_leave_a_magenta_one():
    """A grey field is equally far from every ink, so the old gold
    stays. A magenta mount pushes the marks to green, and the choice
    sticks unless another ink wins by a margin."""
    from darlaston.ui import theme

    assert theme.pick_mark((128, 128, 128)) == "gold"
    assert theme.pick_mark((240, 240, 240), "gold") == "gold"
    assert theme.pick_mark((255, 100, 255), "gold") == "green"
    # Hysteresis: from green, a field that favours cyan by a hair does
    # not move the marks.
    assert theme.pick_mark((255, 100, 255), "green") == "green"
    # Against a green field nothing is further than magenta.
    assert theme.pick_mark((90, 220, 110), "gold") == "magenta"


def test_the_marks_follow_the_setting_and_the_picture(qapp):
    import numpy as np
    from darlaston.ui import theme
    from darlaston.ui.widgets import MarkPicker

    magenta = np.zeros((64, 96, 3), np.uint8)
    magenta[...] = (255, 100, 255)           # BGR, like the view's buffer
    try:
        theme.use_marks("auto")
        picker = MarkPicker()
        assert picker.resolve(magenta).name() == theme.MARK_INKS["green"]
        assert picker.changed, "the peaking tint has to be rewritten"
        theme.use_marks("cyan")
        assert picker.resolve(magenta).name() == theme.MARK_INKS["cyan"]
        theme.use_marks("accent")
        assert picker.resolve(None).name() == theme.BRASS
        theme.use("stentor")
        assert picker.resolve(None).name() == theme.PALETTES["stentor"].accent
        theme.use_marks("nonsense")
        assert theme.mark == "auto"
    finally:
        theme.use(theme.DEFAULT_PALETTE)
        theme.use_marks("auto")


def test_the_appearance_page_offers_the_marks_and_remembers(qapp, tmp_path, monkeypatch):
    from darlaston.session.settings import Settings
    from darlaston.ui.appearance_ui import AppearanceDialog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = Settings()
    told = []
    dialog = AppearanceDialog(settings, lambda: None, None,
                              on_marks=lambda: told.append(settings.marks))
    choices = {b.property("value") for b in dialog._marks.buttons()}
    assert choices == set(theme_choices())
    for b in dialog._marks.buttons():
        if b.property("value") == "green":
            b.setChecked(True)
    assert told == ["green"]
    assert Settings.load().marks == "green"


def theme_choices():
    from darlaston.ui import theme
    return theme.MARK_CHOICES
