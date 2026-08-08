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


# ---- the live scale bar, painted rather than composited ---------------------

class _Canvas:
    """A painter and the surface under it.

    The surface has to outlive the painter, and a helper that returns only
    the painter lets the pixmap be collected while Qt is still drawing on
    it, which segfaults rather than raising.
    """

    def __init__(self, view):
        from PySide6 import QtGui
        self.pixmap = QtGui.QPixmap(view.size())
        self.painter = QtGui.QPainter(self.pixmap)

    def __enter__(self):
        return self.painter

    def __exit__(self, *_exc):
        self.painter.end()
        return False


def test_the_bar_is_painted_and_never_touches_the_picture(window):
    """Compositing it into pixels cost 10.7 ms a frame at preview size and
    6.7 ms at display size, because the work scales with the area drawn
    and this window is close to 4K. Painting is 0.14 ms: the rule is a
    rectangle and the label is a line of text.

    What it costs is being pixel-identical to the bar a capture carries.
    What it keeps is the measurement, which comes from the same
    `scalebar.choose` either way.
    """
    from PySide6 import QtCore

    win = window()
    win.view.resize(600, 400)
    frame = np.full((400, 600, 3), 120, np.uint8)
    win.view.set_scale_bar(2.0, win.settings.bar_style(live=True))
    win.view._set_frame(frame, None)

    # The buffer the view hands to Qt is the picture, undecorated.
    assert np.array_equal(win.view._buf, frame)

    with _Canvas(win.view) as p:
        win.view._draw_scale_bar(p, QtCore.QRectF(0, 0, 600, 400))


def test_the_bar_measures_what_a_capture_would_measure(window):
    """The round number is the shared part. A live bar that said a
    different number from the photograph would be worse than none."""
    from darlaston.process import scalebar

    win = window()
    win.view.resize(600, 400)
    win.view.set_scale_bar(2.0, win.settings.bar_style(live=True))
    win.view._set_frame(np.full((400, 600, 3), 120, np.uint8), None)
    # Same helper the capture path uses, on the shown image's scale.
    assert scalebar.choose(2.0, 600) is not None


def test_no_bar_is_painted_when_none_is_wanted(window):
    from PySide6 import QtCore

    win = window()
    win.view.resize(600, 400)
    win.view.set_scale_bar(None, None)
    win.view._set_frame(np.full((400, 600, 3), 120, np.uint8), None)
    with _Canvas(win.view) as p:
        win.view._draw_scale_bar(p, QtCore.QRectF(0, 0, 600, 400))
    assert win.view._bar is None


def test_the_ink_follows_the_corner_it_sits_in(window):
    """Brightfield is a bright field with a dark subject and darkfield the
    reverse. One fixed ink is invisible on one of them."""
    win = window()
    win.view.resize(600, 400)
    win.view.set_scale_bar(2.0, win.settings.bar_style(live=True))

    win.view._ink_light = None
    win.view._set_frame(np.full((400, 600, 3), 240, np.uint8), None)
    assert win.view._corner_is_light("br") is True

    win.view._ink_light = None
    win.view._set_frame(np.full((400, 600, 3), 8, np.uint8), None)
    assert win.view._corner_is_light("br") is False


def test_the_corner_is_not_re_read_every_paint(window):
    """The only part of drawing the bar that reads pixels rather than
    drawing shapes, and a field does not change brightness between
    frames."""
    win = window()
    win.view.resize(600, 400)
    win.view.set_scale_bar(2.0, win.settings.bar_style(live=True))
    win.view._ink_light = None
    win.view._set_frame(np.full((400, 600, 3), 240, np.uint8), None)
    assert win.view._corner_is_light("br") is True

    # The picture goes dark, but the cached answer holds for a while.
    win.view._set_frame(np.full((400, 600, 3), 8, np.uint8), None)
    assert win.view._corner_is_light("br") is True
    for _ in range(win.view.BAR_INK_EVERY + 1):
        win.view._corner_is_light("br")
    assert win.view._corner_is_light("br") is False


def test_a_failing_bar_does_not_take_the_preview_with_it(window,
                                                         monkeypatch):
    """It went black once already, from an exception raised before the
    view was ever reached."""
    win = window()
    win.settings.scale_bar_live = True

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(win, "_preview_um_per_px", boom)
    win._offer_scale_bar(2736)
    assert win.settings.scale_bar_live is False
    assert win.view._bar is None


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


def test_the_bar_covers_the_same_distance_at_every_window_size(window):
    """Nate: the 100 um bar got longer at some window sizes, against a
    reference in the picture.

    Micrometres-per-pixel is quoted for a *preview* pixel and the bar is
    drawn over *displayed* ones. Moving the drawing into the paint dropped
    the term between them, so the length was computed from one scale and
    laid down in the other, and the error was whatever the reduction
    happened to be at that window size.
    """
    from darlaston.process import scalebar

    win = window()
    preview = np.random.default_rng(0).integers(
        60, 200, (912, 1368, 3), dtype=np.uint8)
    per_preview_px = 0.49

    covered = []
    for wide in (400, 700, 1000, 1360):
        win.view.resize(wide, int(wide * 912 / 1368))
        win.view.set_scale_bar(per_preview_px, win.settings.bar_style(
            live=True))
        win.view._set_frame(preview, None)
        shown = win.view._image.width()
        um_shown = per_preview_px * (win.view._src_width / shown)
        chosen = scalebar.choose(um_shown, shown)
        if chosen is None:
            continue
        micrometres, length = chosen
        covered.append((length * um_shown, micrometres))

    assert len(covered) >= 3, "no bar was chosen at most window sizes"
    for distance, says in covered:
        assert abs(distance - says) / says < 0.02, (
            f"a bar labelled {says} um covered {distance:.1f} um")


def test_the_source_width_is_recorded_for_the_bar(window):
    """The term whose absence caused it. Kept as its own check because a
    ratio that is silently 1.0 looks exactly like a correct one."""
    win = window()
    win.view.resize(400, 300)
    win.view._set_frame(np.zeros((912, 1368, 3), np.uint8), None)
    assert win.view._src_width == 1368
    assert win.view._image.width() < 1368, "the frame was not reduced"
