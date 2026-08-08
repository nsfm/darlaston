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


# ---- the origin reset, against a correlation in flight ----------------------

def test_resetting_the_origin_mid_correlation_does_not_plant_it_stale():
    """Found by an audit of this path, and it is the bug `reset_tracking`
    was written to fix, resurrected by the scheduler.

    The keyframe state is read and written by `_track` without the lock,
    and `phaseCorrelate` holds that window open for milliseconds of every
    frame. A press landing inside it used to clear the reference and then
    hand the in-flight measurement -- taken against the reference that no
    longer existed -- to the freshly reset tracker, planting the origin up
    to a keyframe interval from where the operator pressed.

    The reset is injected at the correlation boundary here, which is only
    a scheduler outcome made reliable.
    """
    import cv2
    from darlaston.live.pipeline import LivePipeline
    from darlaston.camera.buffers import Frame

    p = LivePipeline(on_signals=lambda _s: None)
    rng = np.random.default_rng(4)
    scene = rng.integers(20, 230, (900, 1400, 3), dtype=np.uint8)

    def feed(shift):
        # A plain array rather than the pool: nothing releases these, and
        # `_pool=None` makes release a no-op, which is what the pipeline's
        # own loop would otherwise have done for us.
        p._analyse(Frame(data=np.roll(scene, shift, axis=1), seq=1,
                         timestamp=0.0, exposure_us=8000, gain_pct=100,
                         binned=True, _pool=None))

    feed(0)
    feed(40)                       # travel, so there is a position to lose
    assert p.stage_position() is not None

    # The press lands while the next frame is correlating.
    real = cv2.phaseCorrelate

    def racing(a, b):
        out = real(a, b)
        p.reset_tracking()         # the operator, mid-correlation
        return out

    cv2.phaseCorrelate = racing
    try:
        feed(80)
    finally:
        cv2.phaseCorrelate = real

    where = p.stage_position()
    assert where is None or max(abs(where[0]), abs(where[1])) < 1.0, (
        f"origin planted at {where} by a measurement taken against a "
        "reference that had already been thrown away")


def test_an_origin_reset_between_frames_still_works():
    """The ordinary path, which the fix must not have cost."""
    from darlaston.live.pipeline import LivePipeline
    from darlaston.camera.buffers import Frame

    p = LivePipeline(on_signals=lambda _s: None)
    rng = np.random.default_rng(5)
    scene = rng.integers(20, 230, (900, 1400, 3), dtype=np.uint8)

    def feed(shift):
        p._analyse(Frame(data=np.roll(scene, shift, axis=1), seq=1,
                         timestamp=0.0, exposure_us=8000, gain_pct=100,
                         binned=True, _pool=None))

    feed(0)
    feed(40)
    p.reset_tracking()
    feed(40)
    where = p.stage_position()
    assert where is None or max(abs(where[0]), abs(where[1])) < 1.0


# ---- blankness, only while it is being asked about --------------------------

def test_blankness_is_not_computed_when_nothing_is_asking():
    """Its one live consumer is the stack trigger, which uses it to avoid
    firing a slice on empty glass, and that runs only during a stack.

    The opportunist looks like a second consumer and is not: `observe` is
    advisory and reads the stage offset alone, and its own banking
    measures blankness on the raw frame rather than on this. So outside a
    stack this was 2.5 ms of every frame answering a question nobody
    asked.
    """
    from darlaston.live.pipeline import LivePipeline
    from darlaston.camera.buffers import Frame

    p = LivePipeline(on_signals=lambda _s: None)
    seen = []
    frame = Frame(data=np.full((400, 600, 3), 128, np.uint8), seq=1,
                  timestamp=0.0, exposure_us=8000, gain_pct=100,
                  binned=True, _pool=None)

    p.set_blank_watch(False)
    p._analyse(frame)                    # lets the detector be built
    p._blank = _Counting(seen)
    p._analyse(frame)
    assert seen == [], "blankness computed with nothing asking for it"

    p.set_blank_watch(True)
    p._analyse(frame)
    assert seen == [True], "blankness not computed when the trigger wants it"


class _Counting:
    def __init__(self, log):
        self._log = log

    def looks_blank(self, _gray, white=255):
        self._log.append(True)
        return False


def test_the_verdict_reads_false_rather_than_stale_when_unwatched():
    """A frame carrying last week's answer would be worse than one
    carrying no answer, since the trigger reads it as a veto."""
    from darlaston.live.pipeline import LivePipeline
    from darlaston.camera.buffers import Frame

    got = []
    p = LivePipeline(on_signals=lambda s: got.append(s.looks_blank))
    frame = Frame(data=np.full((400, 600, 3), 250, np.uint8), seq=1,
                  timestamp=0.0, exposure_us=8000, gain_pct=100,
                  binned=True, _pool=None)
    p.set_blank_watch(False)
    p._analyse(frame)
    assert got and got[-1] is False


# ---- the colour the preview arrives in --------------------------------------

def test_the_stream_states_its_byte_order():
    """Nate's amber pseudoscorpion previewed blue while its captures came
    out right.

    The SDK header: "0 => RGB, 1 => BGR, default value: 1(Win), 0(macOS,
    Linux, Android)". Everything downstream reads BGR, so on Linux and
    macOS the ISP handed us RGB and red and blue swapped. Captures were
    unaffected because they go through grab_raw and our own demosaic
    rather than the ISP, which is exactly the pattern that let it survive.

    No test could have caught it from the picture: the synthetic camera
    writes one value to all three channels, so a channel swap is invisible
    to it. This asserts the option is *stated* instead.
    """
    from darlaston.camera import toupcam

    assert toupcam._BYTEORDER_BGR == 0x2a
    src = (toupcam.__file__ and
           __import__("pathlib").Path(toupcam.__file__).read_text())
    # Set on the streaming path and on the ISP grab it is compared against.
    assert src.count("_BYTEORDER_BGR, 1") == 2


def test_the_synthetic_camera_cannot_see_a_channel_swap():
    """Recorded so the gap is known rather than rediscovered.

    If the mock ever grows colour, it becomes able to catch this class of
    fault and this test should be replaced by one that does.
    """
    from darlaston.camera.mock import MockCamera

    cam = MockCamera(fps=30.0)
    cam.open()
    got = []
    cam.start_stream(lambda f: got.append(np.array(f.data[:8, :8])))
    for _ in range(60):
        if got:
            break
        __import__("time").sleep(0.02)
    cam.stop_stream()
    cam.close()
    assert got, "the synthetic camera delivered no frame"
    patch = got[0]
    assert (patch[..., 0] == patch[..., 2]).all(), (
        "the mock now has distinguishable channels: it can catch a red "
        "and blue swap, so test the picture rather than the option")
