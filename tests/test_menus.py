"""The menu structure.

One axis decides every entry: before a session, during it, or after it.
The failure that produced the previous structure was that each stage menu
grew its own preferences entry, which is why they are gathered now.
"""
import pytest

from darlaston.camera.mock import MockCamera
from darlaston.ui.main import MainWindow


@pytest.fixture
def win(qapp):
    w = MainWindow(lambda: MockCamera(fps=30.0))
    yield w
    w.shutdown()


def entries(win, menu):
    return [a.text() for a in win.toolbar.menus[menu].actions()
            if not a.isSeparator()]


def test_three_menus_on_one_axis(win):
    assert list(win.toolbar.menus) == ["Setup", "Capture", "Darkroom"]
    # The one-entry menu is gone: an entry is invisible until its menu is
    # opened, so a menu of one advertises nothing.
    assert "Session" not in win.toolbar.menus


def test_setup_holds_what_outlives_the_session(win):
    assert entries(win, "Setup") == [
        "Microscopes…", "Cameras…", "Photographer…", "Files…",
        "Performance…", "Install camera SDK…", "Install DNG thumbnailer…"]


def test_capture_holds_only_what_needs_a_camera(win):
    got = entries(win, "Capture")
    assert got == ["Timelapse…", "Framing guides", "Write white balance",
                   "Calibration", "Slide map", "Performance monitor"]
    # The two that were near-homonyms are now in different menus, and the
    # readout is named for what it shows rather than for its subject.
    assert "Performance…" in entries(win, "Setup")
    assert "Performance panel" not in got


def test_darkroom_never_touches_the_camera(win):
    assert entries(win, "Darkroom") == [
        "Stitch mosaic…", "Fly through a mosaic…", "Render from depth…",
        "Make a plate…", "Arrange specimens…"]


def test_ellipsis_means_a_further_choice_follows(win):
    """The rule the whole toolbar keeps: an ellipsis when something else
    must be chosen before anything happens, none when it happens now --
    and that includes every toggle."""
    for name, menu in win.toolbar.menus.items():
        for a in menu.actions():
            if a.isSeparator() or a.menu():
                continue
            if a.isCheckable():
                assert not a.text().endswith("…"), f"{name}/{a.text()}"
            else:
                assert a.text().endswith("…"), f"{name}/{a.text()}"


def test_every_view_panel_has_one_route_and_it_tells_the_truth(win):
    """There were four panels summoned four different ways, and the slide
    map had no route at all -- closing it lost it until the next launch."""
    pairs = ((win.calib_action, win.calib_window),
             (win.map_action, win.map_window),
             (win.perf_action, win.perf_window))
    for action, window in pairs:
        assert action.isCheckable()
        assert action.isChecked() is (not window.isHidden()), \
            f"{action.text()} disagrees with its panel at startup"

        action.setChecked(True)
        assert not window.isHidden(), f"{action.text()} did not open"
        action.setChecked(False)
        assert window.isHidden(), f"{action.text()} did not close"

        # Dragged shut by its own corner: a stale check mark is worse than
        # no check mark.
        action.setChecked(True)
        window.closed.emit()
        assert not action.isChecked(), \
            f"{action.text()} stayed ticked after the panel closed itself"


def test_the_rail_button_is_a_second_view_of_one_truth(win):
    """Not a second copy of it. The calibration button and the menu entry
    have to agree however either is used."""
    win.calib_action.setChecked(False)
    win._toggle_calibration()
    assert win.calib_action.isChecked() and not win.calib_window.isHidden()
    win._toggle_calibration()
    assert not win.calib_action.isChecked() and win.calib_window.isHidden()


def test_white_balance_says_so_when_it_is_off(win):
    """A checkable entry is fine when you can see its effect. This one
    changes every file written and is visible nowhere else, so its off
    state -- the unusual one -- earns a chip."""
    win.wb_action.setChecked(True)
    assert win.strip.wb_off.isHidden()
    assert win.capture.white_balance is True

    win.wb_action.setChecked(False)
    assert not win.strip.wb_off.isHidden(), "no warning that files go unbalanced"
    assert win.capture.white_balance is False


def test_no_microscopy_false_friends_in_the_render_list():
    """In microscopy an artefact is a spurious feature introduced by
    preparation -- the wrong word for a list of pictures you are inviting
    somebody to trust."""
    from darlaston.i18n import _
    from darlaston.ui import darkroom_ui

    assert hasattr(darkroom_ui, "RENDERS")
    assert not hasattr(darkroom_ui, "ARTIFACTS")
    # The table holds catalogue keys, so the words have to be looked up:
    # checking the keys would pass no matter what the list said.
    for _key, label, hint, _on in darkroom_ui.RENDERS:
        words = (_(label) + _(hint)).lower()
        assert "artifact" not in words
        assert "artefact" not in words


def test_measure_from_can_be_turned_off_and_back_on(win):
    """Clicked, not called. Qt toggles a checkable button *before* `clicked`
    reaches the handler, so a handler that asks the buttons what was active
    sees the state after the click -- every button looks like the one
    already chosen, and every click collapses to `full`. Calling the
    handler directly skips that and hides it.
    """
    from darlaston.live.focus import Region

    b = win._region_buttons

    def chosen():
        return [r for r, x in b.items() if x.isChecked()]

    assert chosen() == [Region.CENTRE]

    b[Region.FULL].click()
    assert chosen() == [Region.FULL]

    # Full must not be a trap: the others stay reachable from it.
    b[Region.SPOT].click()
    assert chosen() == [Region.SPOT]
    b[Region.CENTRE].click()
    assert chosen() == [Region.CENTRE]

    # Clicking the active one releases the restriction.
    b[Region.CENTRE].click()
    assert chosen() == [Region.FULL]

    # And full has nowhere to release to.
    b[Region.FULL].click()
    assert chosen() == [Region.FULL]

    # A dragged box wins over every preset and unchecks them all.
    win._on_custom_region((0.1, 0.1, 0.2, 0.2))
    assert chosen() == []
    b[Region.SPOT].click()
    assert chosen() == [Region.SPOT]
