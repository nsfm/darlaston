"""Opening in a sensible preview mode, and what the rail costs to draw.

Three small things that all showed up as "why is this slow" or "why does
that look wrong", which is the category of fault nobody files a report
about and everybody notices.
"""
from __future__ import annotations

import numpy as np
import pytest

from darlaston.camera.base import PREVIEW_MAX_MP, Resolution, preferred_preview
from darlaston.ui import theme


def _modes(*sizes):
    return [Resolution(i, w, h, 1.0) for i, (w, h) in enumerate(sizes)]


# ---- which mode to open in --------------------------------------------------

def test_it_picks_the_largest_worth_previewing():
    """On the ToupTek, 2736 over 5440. The 20 MP mode reads out at 12 fps
    whatever else is true, and takes the whole interface with it."""
    chosen = preferred_preview(_modes((5440, 3648), (2736, 1824),
                                      (1824, 1216)))
    assert chosen.width == 2736


def test_a_uvc_camera_gets_its_top_mode():
    """Whose largest is 1080p, which is comfortably under the ceiling, so
    the rule that demotes the ToupTek promotes this one."""
    chosen = preferred_preview(_modes((1920, 1080), (1280, 720), (640, 480)))
    assert chosen.width == 1920


def test_a_camera_offering_nothing_small_gets_its_least_bad():
    """Rather than nothing at all, which would leave the combo on
    whichever mode happened to be first."""
    chosen = preferred_preview(_modes((8000, 6000), (7000, 5000)))
    assert chosen.width == 7000


def test_no_modes_is_not_an_error():
    assert preferred_preview([]) is None
    assert preferred_preview(None) is None


def test_the_ceiling_is_about_what_a_screen_and_a_frame_budget_hold():
    """Not a round number picked for looking like one: two megapixels is
    a display, and everything per-frame scales with the count."""
    assert 2.0 < PREVIEW_MAX_MP < 12.0


def test_a_remembered_width_is_matched_by_width_not_by_index():
    """An index means nothing across a different camera or a changed mode
    list, and the same camera can renumber its modes between driver
    versions."""
    from darlaston.session.model import CameraProfile

    profile = CameraProfile(serial="x", preview_width=2736)
    modes = _modes((1824, 1216), (2736, 1824), (5440, 3648))
    found = next((r for r in modes if r.width == profile.preview_width), None)
    assert found is not None and found.index == 1


def test_a_camera_profile_starts_with_no_opinion():
    from darlaston.session.model import CameraProfile

    assert CameraProfile(serial="x").preview_width == 0


# ---- the live scale bar's cost ---------------------------------------------

def test_the_overlay_buffer_is_reused_between_frames(window):
    """Allocating it is nearly all of the cost. Measured at 5440 wide,
    which is 60 MB a frame: a fresh copy is 24.9 ms against 4.3 ms into a
    buffer already there, on a 33 ms budget."""
    win = window()
    frame = np.zeros((64, 96, 3), np.uint8)
    first = win._live_overlay_buffer(frame)
    second = win._live_overlay_buffer(frame)
    assert first is second


def test_the_buffer_follows_a_change_of_preview_size(window):
    win = window()
    small = win._live_overlay_buffer(np.zeros((64, 96, 3), np.uint8))
    big = win._live_overlay_buffer(np.zeros((128, 192, 3), np.uint8))
    assert big.shape == (128, 192, 3)
    assert big is not small


def test_the_buffer_carries_the_frame_rather_than_stale_pixels(window):
    win = window()
    win._live_overlay_buffer(np.full((16, 16, 3), 7, np.uint8))
    out = win._live_overlay_buffer(np.full((16, 16, 3), 200, np.uint8))
    assert int(out.min()) == 200


def test_the_frame_itself_is_never_drawn_into(window):
    """The rest of the window keeps that array: the balance sample, the
    tile thumbnail, the style window's preview. They want the photograph,
    not the photograph with a ruler on it."""
    win = window()
    frame = np.zeros((32, 48, 3), np.uint8)
    assert win._live_overlay_buffer(frame) is not frame


# ---- how a segment reads ---------------------------------------------------

def test_a_filled_segment_uses_dark_lettering():
    """Brass is a light colour, so a near-white label on it is two light
    tones and smudges at eleven pixels. The sliders already draw their
    labels this way where the fill passes under them."""
    css = theme.stylesheet()
    block = css.split('QPushButton[role="seg"]:checked')[1].split("}")[0]
    assert theme.ON_BRASS in block
    assert theme.INK not in block


def test_an_inviting_segment_that_is_on_still_looks_on():
    """Without an explicit rule the invitation wins on specificity, being
    one selector longer, and a switch doing its job goes back to looking
    like one asking to be pressed."""
    css = theme.stylesheet()
    assert 'QPushButton[role="seg"][invite="true"]:checked' in css
    block = css.split('QPushButton[role="seg"][invite="true"]:checked')[1] \
               .split("}")[0]
    assert theme.BRASS in block and theme.ON_BRASS in block


def test_a_switch_invites_a_press_only_when_there_is_one_to_make(window):
    """The rail already means something by brass lettering: there is
    something to do here. A switch that is on is not inviting, it is
    working, and one that is held cannot be pressed at all. So the
    property is set as the state changes rather than once at build time.
    """
    win = window()
    for button in (win.auto_exposure, win.auto_exposure_lock, win.wb_pick):
        assert button.property("role") == "seg"

    win.settings.auto_exposure = False
    win._ae_held_shown = None
    win._refresh_auto_exposure_hold()
    assert win.auto_exposure.property("invite") == "true"

    win.settings.auto_exposure = True
    win.auto_exposure.setChecked(True)
    win._ae_held_shown = None
    win._refresh_auto_exposure_hold()
    assert win.auto_exposure.property("invite") == "false"

    win.stack_session = object()          # held by something else
    win._ae_held_shown = None
    win._refresh_auto_exposure_hold()
    assert win.auto_exposure.property("invite") == "false"
    assert not win.auto_exposure.isEnabled()


def test_a_failing_scale_bar_does_not_take_the_preview_with_it(window,
                                                               monkeypatch):
    """It went black once, from a missing import raising before the frame
    was ever handed to the view, while everything reading the pipeline
    carried on as though nothing were wrong."""
    win = window()
    win.settings.scale_bar_live = True

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(win, "_preview_um_per_px", boom)
    frame = np.full((32, 48, 3), 128, np.uint8)
    shown = win._shown_preview(frame)
    assert shown is frame                       # the photograph, undecorated
    assert win.settings.scale_bar_live is False


# ---- what the analysis costs, per preview mode ------------------------------

def test_the_tracker_grid_is_a_size_not_a_fraction():
    """It used to be the preview divided by four, which is 684 wide at the
    2736 mode and 1360 at the 5440 one: four times the pixels into a phase
    correlation that gains nothing from them, measured at 38.0 ms of a
    143.9 ms frame on a camera whose frame period there is 83 ms.

    The 2736 grid must not move. That is the one the tracker's own
    measurements were taken on, and the note beside it records that a
    coarser 256 square grid measured worse.
    """
    from darlaston.live.pipeline import TRACK_WIDTH

    def grid(width):
        step = max(4, int(round(width / TRACK_WIDTH)))
        return width // step

    assert grid(2736) == 684              # unchanged, and the measured one
    assert grid(1824) == 456              # unchanged: never coarser than /4
    assert 600 < grid(5440) < 760         # brought to meet it


def test_no_mode_is_tracked_more_coarsely_than_it_was():
    """The floor of four exists so that reducing the large modes cannot
    quietly reduce the small ones as well."""
    from darlaston.live.pipeline import TRACK_WIDTH

    for width in (640, 1280, 1824, 2736, 3840, 5440):
        step = max(4, int(round(width / TRACK_WIDTH)))
        assert step >= 4
        assert width // step <= width // 4
