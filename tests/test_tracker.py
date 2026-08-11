"""Stage tracking and the slide map.

The one test that matters most here is the end-to-end sign check: the mock
camera's stage moves in known microns, frames flow through the real pipeline
analysis, and the integrated position must come out in the same direction and
at the right preview-pixel scale. Sign conventions in image registration are
exactly the kind of thing that looks right in code and is backwards on glass.
"""
import time

import numpy as np
import pytest

from darlaston.camera.buffers import BufferPool, Frame
from darlaston.camera.mock import MockCamera, _RESOLUTIONS
from darlaston.live.pipeline import LivePipeline
from darlaston.live.tracker import SlideMap, StageTracker, Terrain


# ---- the tracker's contract --------------------------------------------------

def test_integration_inverts_scene_motion():
    """Scene slides left -> view moved right. The map is of the slide,
    not of the image."""
    t = StageTracker()
    shape = (1216, 1824)
    t.advance((0.0, 0.0), 0.9, shape)          # first lock
    pos, locked = t.advance((-10.0, -4.0), 0.9, shape)
    assert locked
    assert pos == (10.0, 4.0)


def test_low_confidence_holds_rather_than_integrates():
    t = StageTracker()
    shape = (1216, 1824)
    t.advance((5.0, 0.0), 0.9, shape)
    held, locked = t.advance((50.0, 50.0), 0.02, shape)
    assert not locked
    assert held == (-5.0, 0.0), "a noise match must not move the position"
    assert not t.tracking


def test_absurd_jumps_are_rejected():
    """Phase correlation wraps past half a frame; a 'measured' shift near that
    limit is where the wraparound lies with a confident face."""
    t = StageTracker()
    shape = (100, 100)
    t.advance((0.0, 0.0), 0.9, shape)
    _, locked = t.advance((60.0, 0.0), 0.9, shape)
    assert not locked


def test_the_gate_band_is_no_longer_ys_blind_spot():
    """The deliberate change the earlier pin existed for. A 55 px shift
    on the track frame -- 36% of y's height, squarely in the band that
    used to be discarded whole -- is now accepted on both axes, because
    the pipeline verifies everything past 0.35 against the pixels
    before this gate ever sees it. The gate still ends at 0.45: past
    that sits too close to the half-frame wraparound to trust."""
    shape = (152, 228)                    # the track frame, h x w
    x = StageTracker()
    x.anchor((5.0, 5.0), 0.9, shape)
    _, locked, _ = x.anchor((55.0, 0.0), 0.9, shape)
    assert locked and x.gated == 0, "x absorbs the band, as always"

    y = StageTracker()
    y.anchor((5.0, 5.0), 0.9, shape)
    _, locked, _ = y.anchor((0.0, 55.0), 0.9, shape)
    assert locked and y.gated == 0, "y absorbs it now too"

    far = StageTracker()
    far.anchor((5.0, 5.0), 0.9, shape)
    _, locked, _ = far.anchor((0.0, 70.0), 0.9, shape)   # 0.46 of y
    assert not locked and far.gated == 1, "past 0.45 stays gated"


def test_no_position_until_first_lock():
    t = StageTracker()
    assert t.position is None
    t.advance(None, 0.0, (100, 100))
    assert t.position is None, "a map must never be seeded from nothing"
    t.advance((1.0, 1.0), 0.9, (100, 100))
    # The value, not merely that there is one: a property that answered
    # (0.0, 0.0) from the first frame would satisfy "is not None" and would
    # be the bug this guards against. The sense is inverted on purpose --
    # when the scene slides left, the view has moved right over the slide.
    assert t.position == (-1.0, -1.0)


# ---- the map's banking policy ------------------------------------------------

def _preview(w=160, h=120):
    return np.zeros((h, w, 3), np.uint8)


def test_map_paints_on_movement_not_on_time():
    m = SlideMap()
    p = _preview()
    assert m.observe((0.0, 0.0), p, tracking=True)
    assert not m.observe((4.0, 0.0), p, tracking=True), \
        "hand tremor must not repaint the map"
    one_field = m.terrain.fields_painted
    assert m.observe((0.5 * 160, 0.0), p, tracking=True)
    assert m.terrain.fields_painted > one_field, \
        "new ground must grow the painted area"


def test_map_ignores_untracked_frames():
    m = SlideMap()
    assert not m.observe((0.0, 0.0), _preview(), tracking=False)
    assert not m.observe(None, _preview(), tracking=True)
    assert not m.terrain.ready


@pytest.mark.serial
def test_revisit_refreshes_in_place():
    """Coming back to mapped ground updates it rather than duplicating it,
    and not thirty times a second."""
    m = SlideMap()
    p = _preview()
    m.observe((0.0, 0.0), p, tracking=True)
    m.observe((200.0, 0.0), p, tracking=True)
    covered = m.terrain.fields_painted
    # Return to the origin: the arrival paints once, and a parked view
    # repaints only at the refresh cadence after that.
    changed = sum(m.observe((1.0, 0.0), p, tracking=True)
                  for _ in range(3 * SlideMap.REFRESH_EVERY))
    assert 3 <= changed <= 5, "refresh should be rate-limited, not per-frame"
    assert m.terrain.fields_painted == pytest.approx(covered, rel=0.02), \
        "revisiting mapped ground must not grow the map"


def test_guidance_counts_down_in_fields():
    m = SlideMap()
    p = _preview()
    m.observe((0.0, 0.0), p, tracking=True)
    pin = m.add_pin((0.0, 0.0))
    m.target = pin.id
    fields, compass = m.guidance((320.0, 0.0), frame_w=160)
    assert fields == pytest.approx(2.0)
    assert compass == "W", "the pin is to the west of the current view"
    fields, _ = m.guidance((160.0, 0.0), frame_w=160)
    assert fields == pytest.approx(1.0), "the number must shrink as you close"


def test_pin_selection_radius():
    m = SlideMap()
    a = m.add_pin((0.0, 0.0))
    m.add_pin((100.0, 0.0))
    assert m.pin_near((3.0, 3.0), radius=10.0).id == a.id
    assert m.pin_near((50.0, 0.0), radius=10.0) is None


# ---- the hold-still guard ----------------------------------------------------

def _rig():
    """Mock camera plus a pipeline fed synchronously, no threads."""
    cam = MockCamera(fps=30.0)
    cam.open()
    res = _RESOLUTIONS[2]
    pool = BufferPool((res.height, res.width, 3), np.uint8, count=1)
    got = []
    pipe = LivePipeline(got.append)

    def push():
        buf = pool.acquire()
        cam._render_into(buf, res)
        frame = Frame(data=buf, seq=len(got), timestamp=time.time(),
                      exposure_us=8330, gain_pct=100, binned=True, _pool=pool)
        pipe._analyse(frame)
        frame.release()

    return cam, pipe, push, got


def test_mock_motion_blur_is_opt_in_and_exposure_scaled():
    """Off by default -- it would change what every moving test sees --
    and when on, the streak follows the shutter: the same move blurs at
    a long exposure and stays crisp at a short one."""
    def frame(exposure_us, blur):
        cam = MockCamera(fps=30.0)
        cam.open()
        cam.motion_blur = blur
        cam.set_exposure(exposure_us)
        res = _RESOLUTIONS[2]
        buf = np.empty((res.height, res.width, 3), np.uint8)
        cam._render_into(buf, res)          # establishes _blur_from
        x, y = cam.stage_xy
        cam.stage_xy = (x + 300.0, y)       # a fast move between frames
        cam._render_into(buf, res)
        cam.close()
        return buf.copy()

    import cv2

    def crispness(img):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return cv2.Laplacian(g, cv2.CV_64F).var()

    # Each blurred frame against its own same-exposure sharp baseline:
    # the mock folds exposure into brightness, so comparing across
    # exposures would measure darkness, not smear.
    r_long = crispness(frame(16000, True)) / crispness(frame(16000, False))
    r_short = crispness(frame(2000, True)) / crispness(frame(2000, False))
    assert r_long < 0.6, f"long exposure plus motion must smear ({r_long:.2f})"
    assert r_short > 1.5 * r_long, \
        f"the streak must follow the shutter ({r_short:.2f} vs {r_long:.2f})"


def test_guard_measures_motion_across_the_capture_gap():
    cam, pipe, push, _ = _rig()
    push(); push(); push()
    token = pipe.guard_begin()
    assert token is not None, "locked scene: the guard must arm"

    um = 2400.0                                # a deliberate crank mid-shot
    cam.stage_xy = (um, 0.0)
    push(); push()
    px = pipe.guard_measure(token, timeout=0.1)
    expected = um / _RESOLUTIONS[0].pixel_um * (1824 / 5440)
    assert px == pytest.approx(expected, rel=0.05)
    cam.close()


def test_guard_reads_near_zero_when_still():
    cam, pipe, push, _ = _rig()
    push(); push(); push()
    token = pipe.guard_begin()
    push(); push()
    px = pipe.guard_measure(token, timeout=0.1)
    assert px is not None and px < 3.0, "a still stage must not read as moved"
    cam.close()


def test_guard_stays_silent_without_a_lock():
    cam, pipe, push, _ = _rig()
    assert pipe.guard_begin() is None, \
        "no frames yet, nothing to measure against -- the guard must not arm"
    # And it does arm once there is something to measure against, or the
    # assertion above would hold just as well for a guard that never armed.
    push(); push()
    assert pipe.guard_begin() is not None, "the guard never arms at all"
    cam.close()


def test_guard_reports_unmeasurable_as_infinite():
    import math
    cam, pipe, push, _ = _rig()
    push(); push(); push()
    token = pipe.guard_begin()
    cam.stage_xy = (9000.0, 4000.0)            # far past what wraps honestly
    push(); push()
    px = pipe.guard_measure(token, timeout=0.1)
    # Either the tracker gated the absurd jump (inf) or correlation failed
    # outright (None). Both mean "could not follow that", and only inf is a
    # positive claim -- but a silent small number would be the actual bug.
    assert px is None or math.isinf(px) or px > 100.0
    cam.close()


@pytest.mark.serial
def test_guard_times_out_to_none_when_no_frames_arrive():
    import time as _t

    cam, pipe, push, _ = _rig()
    push(); push()
    token = pipe.guard_begin()
    t0 = _t.perf_counter()
    assert pipe.guard_measure(token, timeout=0.05) is None
    waited = _t.perf_counter() - t0
    # It *waited*, rather than answering None straight away. Without this
    # the test cannot tell a timeout from a guard that never measures
    # anything -- and a capture that returns instantly having measured
    # nothing is the failure, not the timeout.
    assert waited >= 0.04, f"returned after {waited * 1e3:.0f} ms; never waited"
    assert waited < 1.0, f"waited {waited:.2f}s for a 0.05s timeout"
    cam.close()


def test_tracker_counts_gated_offsets():
    t = StageTracker()
    shape = (100, 100)
    t.advance((1.0, 0.0), 0.9, shape)
    assert t.gated == 0
    t.advance((60.0, 0.0), 0.9, shape)         # jump gate
    t.advance((1.0, 0.0), 0.01, shape)         # confidence gate
    assert t.gated == 2


# ---- end to end against the synthetic stage ----------------------------------

def test_pipeline_recovers_commanded_stage_motion():
    """Move the mock stage in microns; the tracker must report the same
    motion, same direction, in preview pixels."""
    cam = MockCamera(fps=30.0)
    cam.open()
    res = _RESOLUTIONS[2]
    full = _RESOLUTIONS[0]
    pool = BufferPool((res.height, res.width, 3), np.uint8, count=1)

    got = []
    pipe = LivePipeline(got.append)

    def push(step):
        buf = pool.acquire()
        cam._render_into(buf, res)
        frame = Frame(data=buf, seq=step, timestamp=time.time(),
                      exposure_us=8330, gain_pct=100, binned=True, _pool=pool)
        pipe._analyse(frame)
        frame.release()

    # Steps in exact multiples of the sensor pitch, small enough that each
    # frame-to-frame shift is unambiguous for phase correlation.
    um = full.pixel_um
    push(0)
    for i in range(1, 9):
        cam.stage_xy = (i * 10 * um, i * 5 * um)
        push(i)

    final = got[-1]
    assert final.stage_tracking, "structured scene, small steps: must lock"
    scale = res.width / full.width
    expected = (80 * um / full.pixel_um * scale,
                40 * um / full.pixel_um * scale)
    assert final.stage_pos == pytest.approx(expected, abs=1.5), \
        "integrated position must match the commanded stage motion"
    assert final.stage_pos[0] > 0 and final.stage_pos[1] > 0, \
        "stage moved +x/+y, so the view must have moved +x/+y over the slide"
    cam.close()


def _fourier_walk(per_frame, frames=60, size=(480, 640)):
    """Integrate a slow, steady pan through the real pipeline.

    Frames are shifted by multiplying by a linear phase ramp, so each one
    is a band-limited-exact translation of the last. That matters: any
    interpolated or integer-quantised rig has its own sub-pixel error, and
    sub-pixel error is the thing under test. The mock camera cannot be used
    here at all -- it quantises stage position with int() twice, so it
    renders no sub-pixel motion.
    """
    import cv2
    import numpy as np

    from darlaston.camera.buffers import BufferPool, Frame
    from darlaston.live.pipeline import LivePipeline

    h, w = size
    rng = np.random.default_rng(9)
    base = cv2.GaussianBlur(rng.normal(128, 40, (h, w)), (0, 0), 2.0)
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    spectrum = np.fft.fft2(base)

    pool = BufferPool((h, w, 3), np.uint8, count=4)
    got = []
    pipe = LivePipeline(got.append)
    travel = 0.0
    for i in range(frames):
        travel += per_frame
        ramp = np.exp(-2j * np.pi * (fx * travel + fy * travel))
        grey = np.clip(np.real(np.fft.ifft2(spectrum * ramp)),
                       0, 255).astype(np.uint8)
        buf = pool.acquire()
        buf[:] = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
        frame = Frame(data=buf, seq=i, timestamp=i / 30.0, exposure_us=8000,
                      gain_pct=100, binned=True, _pool=pool)
        with frame:
            pipe._analyse(frame)
    return got[-1].stage_pos, travel - per_frame


def test_a_slow_pan_is_not_quietly_lost():
    """The bug this exists to prevent.

    Integrating consecutive frames loses most of the travel of a slow hand.
    Phase correlation's sub-pixel estimator is biased toward whole pixels
    -- peak locking -- and the preview is reduced by four before
    correlating, so one pixel of stage motion is a quarter of a pixel
    there, well inside the biased region. Confidence stays above 0.9
    throughout, so nothing is gated and nothing is logged while the
    position stops keeping up.

    Two measurements, because the size matters. At the real preview size
    of 1824x1216, integrating frame to frame over ninety frames came out
    at -47% x and -49% y for 1 px/frame. This test runs at 640x480 for
    speed, where the same walk loses about 99%. Either way the assertion
    below separates them by more than an order of magnitude; the
    5% bound is nowhere near either regime.
    """
    for per_frame in (0.7, 1.0, 1.5, 3.0):
        pos, travelled = _fourier_walk(per_frame)
        assert pos is not None, f"never locked at {per_frame} px/frame"
        for axis, got in enumerate(pos):
            error = (abs(got) - travelled) / travelled
            assert abs(error) < 0.05, (
                f"{per_frame} px/frame lost {error * 100:+.0f}% on axis "
                f"{'xy'[axis]}: {abs(got):.1f} of {travelled:.1f} px")


def test_the_step_limit_is_per_axis():
    """It used to be MAX_STEP * min(shape), so on a 3:2 frame x was allowed
    23% of its own width while y was allowed 35% of its height."""
    from darlaston.live.tracker import StageTracker

    shape = (1216, 1824)                       # h, w
    t = StageTracker()
    t.advance((0.0, 0.0), 0.9, shape)

    # 30% of the width is fine for x, and used to be rejected.
    pos, locked = t.advance((0.30 * 1824, 0.0), 0.9, shape)
    assert locked, "a shift well inside the frame width was rejected"

    # 46% of the height is past the limit for y.
    _, locked = t.advance((0.0, 0.46 * 1216), 0.9, shape)
    assert not locked


def test_the_anchor_replaces_the_position_rather_than_adding_to_it():
    from darlaston.live.tracker import StageTracker

    shape = (400, 400)
    t = StageTracker()
    # The same shift measured twice against one keyframe is one move, not
    # two. Integrating would double it.
    t.anchor((10.0, 0.0), 0.9, shape)
    pos, locked, rekey = t.anchor((10.0, 0.0), 0.9, shape)
    assert locked and not rekey
    assert pos == (-10.0, 0.0), pos

    # Once the view has slid far enough, a fresh keyframe is asked for and
    # the next measurement is relative to the new anchor.
    pos, _, rekey = t.anchor((0.30 * 400, 0.0), 0.9, shape)
    assert rekey, "no new keyframe wanted after a large slide"
    after, _, _ = t.anchor((5.0, 0.0), 0.9, shape)
    assert after[0] == pos[0] - 5.0


def test_an_unmeasurable_shift_asks_for_a_new_keyframe():
    """Otherwise a view that outran its keyframe gates for ever."""
    from darlaston.live.tracker import StageTracker

    t = StageTracker()
    t.anchor((0.0, 0.0), 0.9, (400, 400))
    before = t.gated
    pos, locked, rekey = t.anchor((0.0, 380.0), 0.9, (400, 400))
    assert not locked and rekey
    assert t.gated == before + 1


def test_a_gated_jump_does_not_teleport_the_position_backwards():
    """The frame after a rejected jump is measured against a *new*
    keyframe, so the anchor has to have moved to the last position we
    actually measured. Leaving it behind made the next frame compute
    `old_anchor - small_offset`: the position jumped back by however far
    the view had travelled since the last rekey, and said it was locked.
    One fast crank re-origined the whole map."""
    from darlaston.live.tracker import StageTracker

    shape = (400, 400)
    t = StageTracker()
    t.anchor((0.0, 0.0), 0.9, shape)
    # Sixty pixels of travel: measurable, and not far enough to rekey, so
    # the position and the anchor are deliberately apart when the jump
    # arrives. That gap is the size of the teleport.
    pos, _, rekey = t.anchor((60.0, 0.0), 0.9, shape)
    assert pos == (-60.0, 0.0) and not rekey

    held, locked, rekey = t.anchor((0.0, 380.0), 0.9, shape)
    assert not locked and rekey, "an unmeasurable jump should gate"
    assert held == pos, "the position moved on a rejected measurement"

    # The caller takes a fresh keyframe here. Five more pixels of travel
    # should read five pixels on from where we last knew we were.
    after, locked, _ = t.anchor((5.0, 0.0), 0.9, shape)
    assert locked
    assert after == (-65.0, 0.0), (
        f"position teleported to {after} instead of (-65.0, 0.0)")


def test_the_map_refuses_frames_from_before_its_clear(qapp):
    """The two-press clear: the frame in flight when a reset lands still
    carries the old origin's position, and banked, it repaints the
    current view at stale coordinates -- the map shows the same picture
    twice until a second press sweeps it. The generation fence refuses
    it instead."""
    import types

    from darlaston.ui.map_ui import SlideMapPanel

    panel = SlideMapPanel()
    frame = np.full((60, 80, 3), 120, np.uint8)

    def signal(gen, pos):
        return types.SimpleNamespace(preview=frame, stage_pos=pos,
                                     stage_tracking=True, track_gen=gen,
                                     track_gated=0, xy_offset=None,
                                     stats={})

    panel.update_live(signal(0, (10.0, 10.0)))
    assert panel.model.terrain.ready, "tracking never painted terrain"

    panel.model.reset()
    panel.ignore_before(1)
    panel.update_live(signal(0, (10.0, 10.0)))    # measured before the reset
    assert not panel.model.terrain.ready, \
        "a stale frame repainted cleared ground"

    panel.update_live(signal(1, (0.0, 0.0)))
    assert panel.model.terrain.ready, "fresh frames must still paint"


def test_the_map_banks_only_what_the_tracker_is_calm_about(qapp):
    """The banking quality bar, from the on-glass report: during a fast
    crank the map banked blurred frames, and one frame banked in the
    shadow of a gated jump -- position suspect, not yet corroborated --
    morphed the map and skewed everything after, because the misplaced
    paint is what the relocalizer then matched against."""
    import types

    from darlaston.ui.map_ui import SlideMapPanel

    frame = np.full((60, 80, 3), 120, np.uint8)

    def signal(pos, offset=None, gated=0, exposure=4000):
        return types.SimpleNamespace(
            preview=frame, stage_pos=pos, stage_tracking=True, track_gen=0,
            track_gated=gated, xy_offset=offset,
            stats={"analysed_fps": 30.0, "exposure_us": exposure})

    # A fast frame at a long exposure smears; the same speed at a short
    # exposure does not. 100 px/frame at 30 fps: 12 ms of shutter is
    # 36 px of streak, 2 ms is 6 px.
    smeared = SlideMapPanel()
    smeared.update_live(signal((0.0, 0.0), offset=(100.0, 0.0),
                               exposure=12000))
    assert not smeared.model.terrain.ready, "a smeared frame was banked"
    smeared.update_live(signal((0.0, 0.0), offset=(100.0, 0.0),
                               exposure=2000))
    assert smeared.model.terrain.ready, "short exposure must still bank"

    # After a gate, painting holds until the position has had its chance
    # to be corroborated -- and resumes on its own once it has.
    held = SlideMapPanel()
    held.update_live(signal((0.0, 0.0)))
    assert held.model.terrain.ready
    fields = held.model.terrain.fields_painted
    held.update_live(signal((500.0, 0.0), gated=1))
    for i in range(SlideMapPanel.GATE_HOLD - 1):
        held.update_live(signal((500.0 + i, 0.0), gated=1))
    assert held.model.terrain.fields_painted == fields, \
        "the map painted while the position was suspect"
    for i in range(4):
        held.update_live(signal((520.0 + i, 0.0), gated=1))
    assert held.model.terrain.fields_painted > fields, \
        "painting never resumed after the holdoff"


def test_clearing_the_map_fences_in_the_same_press(window):
    """One route: the panel's clear resets the tracking origin and sets
    the stale-frame fence together, whichever path asked for it."""
    win = window()
    before = win.pipeline.reset_tracking()
    assert isinstance(before, int)
    win.slidemap.clear()
    assert win.slidemap._min_gen == before + 1, (
        "the clear did not fence out frames from the old origin")


def test_the_gate_is_a_fraction_of_each_axis_and_y_keeps_500_now():
    """The dead-reckoning undershoot's pin, deliberately changed. A
    500 px jump used to survive in x (gate 638) and be discarded whole
    in y (gate 426); with the verified band the y gate reaches 547 and
    the same jump survives both axes. The gate is still a fraction of
    each axis's own extent, so the two still part ways -- just past the
    band's edge now."""
    shape = (1216, 1824)
    t = StageTracker()
    t.anchor((0.0, 0.0), 0.9, shape)
    _pos, locked, _rekey = t.anchor((500.0, 0.0), 0.9, shape)
    assert locked, "x keeps a 500 px jump, as it always did"

    t = StageTracker()
    t.anchor((0.0, 0.0), 0.9, shape)
    _pos, locked, _rekey = t.anchor((0.0, 500.0), 0.9, shape)
    assert locked and t.gated == 0, (
        "y keeps it too now: 500 px is inside the verified band")

    t = StageTracker()
    t.anchor((0.0, 0.0), 0.9, shape)
    _pos, locked, _rekey = t.anchor((0.0, 550.0), 0.9, shape)
    assert not locked and t.gated == 1, "past the band, y still gates"


def test_a_blind_tracker_costs_nothing_and_claims_nothing():
    """With nobody consuming the position, the correlation is skipped --
    and blind means blind: no position, no lock, no stillness, so the
    opportunist and the guard cannot mistake sleep for a measurement."""
    cam, pipe, push, got = _rig()
    push(); push(); push()
    assert got[-1].stage_pos is not None, "the premise: awake, it tracks"

    pipe.set_track_wanted(False)
    cam.stage_xy = (2400.0, 1800.0)             # travel nobody integrates
    push(); push()
    s = got[-1]
    assert s.stage_pos is None
    assert not s.stage_tracking
    assert not s.settled, "a blind tracker claimed stillness"

    pipe.set_track_wanted(True)
    pipe.reset_tracking()                       # what the window does on wake
    push(); push(); push()
    assert got[-1].stage_pos is not None, "it did not wake"
    cam.close()


def test_the_window_wakes_the_tracker_for_a_listener(window):
    win = window()
    win.map_window.hide()
    win._sync_track_wanted()
    assert win.pipeline._track_wanted is False, (
        "map closed, no session: the tracker should sleep")
    fence = win.slidemap._min_gen
    win.map_window.show()
    win._sync_track_wanted()
    assert win.pipeline._track_wanted is True
    assert win.slidemap._min_gen > fence, (
        "waking must clear the map and fence the blind gap")


def test_corrections_ride_the_generation_fence():
    """A nudge moves the position and its anchor together; a stale
    generation's correction describes an origin that no longer exists
    and must change nothing."""
    cam, pipe, push, got = _rig()
    push(); push(); push()
    gen = got[-1].track_gen
    before = pipe.stage_position()
    assert before is not None

    pipe.correct_tracking(gen, delta=(10.0, 4.0))
    moved = pipe.stage_position()
    assert moved == pytest.approx((before[0] + 10.0, before[1] + 4.0))

    # The stage has not moved, so the next frames must hold the nudged
    # position rather than snapping back to the keyframe's old belief.
    push(); push()
    held = got[-1].stage_pos
    assert held == pytest.approx(moved, abs=2.0)

    pipe.correct_tracking(gen - 1, delta=(500.0, 500.0))
    assert pipe.stage_position() == pytest.approx(held, abs=2.0), (
        "a stale correction was believed")
    cam.close()


def test_a_refix_plants_the_position_and_drops_the_key():
    """After a blank crossing the keyframe is of ground from before the
    gap; a refix plants the position absolutely and the next analysis
    pass measures from fresh ground rather than jumping back."""
    cam, pipe, push, got = _rig()
    push(); push(); push()
    gen = got[-1].track_gen
    pipe.correct_tracking(gen, refix=(500.0, 300.0))
    push(); push()
    assert got[-1].stage_pos == pytest.approx((500.0, 300.0), abs=2.0), (
        "the refix did not survive the next frames")
    cam.close()


def test_the_speed_gauge_shares_its_zones_with_the_banking_bar(qapp):
    """Green banks, red refuses -- the gauge and the banking bar read
    the same arithmetic, so the gauge can never say fine while the map
    is quietly dropping frames."""
    from darlaston.ui import theme
    from darlaston.ui.map_ui import SlideMapPanel

    panel = SlideMapPanel()
    panel.set_speed(None, None)
    assert panel.speed.isHidden()

    panel.set_speed(100.0, 1000.0)              # a tenth of the limit
    assert not panel.speed.isHidden()
    assert "µm/s" in panel.speed._text
    assert panel.speed.tone == theme.GOOD

    # The gauge is smoothed, so the zones are reached, not jumped to.
    for _ in range(40):
        panel.set_speed(800.0, 1000.0)          # closing on the edge
    assert panel.speed.tone == theme.BRASS

    for _ in range(40):
        panel.set_speed(1500.0, 1000.0)         # past it: the map refuses
    assert "mm/s" in panel.speed._text
    assert panel.speed.tone == theme.BAD

    panel.set_speed(None, None)
    assert panel.speed.isHidden()


def test_pips_are_slide_anchored_and_orientation_aware(window):
    """A pip is a point of slide space: centred on a grid node it sits
    dead centre, panning right moves it left, and it rides the same
    orientation mapping as every other overlay."""
    import types

    win = window()
    s = types.SimpleNamespace(preview=np.zeros((120, 160, 3), np.uint8),
                              stage_pos=(0.0, 0.0))
    pts = [(round(x, 3), round(y, 3)) for x, y in win._pip_points(s, 0, False)]
    assert (0.5, 0.5) in pts, "a grid node under the centre sits dead centre"

    s.stage_pos = (40.0, 0.0)                  # half a pip step to the right
    pts = [(round(x, 3), round(y, 3)) for x, y in win._pip_points(s, 0, False)]
    assert (0.25, 0.5) in pts, "panning right must carry the pip left"

    s.stage_pos = (0.0, 0.0)
    turned = [(round(x, 3), round(y, 3))
              for x, y in win._pip_points(s, 90, False)]
    assert (0.5, 0.5) in turned, "the centre survives any rotation"


def test_pips_reach_the_view_only_while_tracking(qapp, window):
    from darlaston.live.pipeline import LiveSignals

    win = window()
    got = []
    win.view.set_pips = lambda p: got.append(p)

    def sig(tracking):
        return LiveSignals(
            seq=1, timestamp=1.0, preview=np.zeros((6, 8, 3), np.uint8),
            histogram=np.zeros(256, np.int32), clipped_fraction=0.0,
            focus_metric=1.0, focus_fraction_of_peak=1.0,
            focus_trace=np.zeros(4, np.float32),
            stage_pos=(0.0, 0.0), stage_tracking=tracking,
            stats={"analysed_fps": 30.0, "delivered": 1, "dropped": 0,
                   "exposure_us": 8000, "gain_pct": 100})

    # Fed straight into the slot rather than published: the mock camera's
    # own live thread also publishes, and racing it for the newest-frame
    # cell made this test order-dependent under the full suite.
    import types

    win.settings.framing_pips = True
    queue = [sig(True)]
    win._newest = types.SimpleNamespace(take=lambda: queue.pop()
                                        if queue else None,
                                        put=lambda v: None, replaced=0)
    win._on_signals(None)
    assert got and got[-1], "tracking with pips on must deliver points"

    queue.append(sig(False))
    win._on_signals(None)
    assert got[-1] is None, "a lost position must never wear confident dots"


def test_the_relocalizer_goes_quiet_during_the_drift_ritual(window):
    """The ritual measures the tracker's raw drift; the relocalizer
    exists to abolish exactly that. So while the drift dialog is open,
    the relocalizer must not observe or correct -- or it would eat the
    drift being measured."""
    import types

    win = window()
    calls = []
    win.relocator.observe = lambda *a, **k: calls.append(1)

    s = types.SimpleNamespace(
        preview=np.zeros((120, 160, 3), np.uint8), stage_pos=(5.0, 5.0),
        stage_tracking=True, stats={}, xy_offset=None, track_gen=1,
        track_gated=0, track_small=None)

    win._track_wanted = True
    win._drift_dialog = None
    win._keep_tracking(s)
    assert calls, "the relocalizer runs in ordinary operation"

    calls.clear()
    win._drift_dialog = object()               # the ritual is open
    win._keep_tracking(s)
    assert not calls, "the relocalizer must not run during the ritual"


def test_the_window_says_what_tracking_is_doing(window):
    """The advisories: lost is said after it is sustained, a gated step
    is said the moment it happens, and neither is said while the
    tracker was put to sleep on purpose."""
    import types

    from darlaston.i18n import _

    win = window()
    frame = np.full((120, 160, 3), 120, np.uint8)

    def signal(tracking, gated=0):
        return types.SimpleNamespace(preview=frame, stage_pos=(0.0, 0.0),
                                     stage_tracking=tracking,
                                     stats={}, xy_offset=None,
                                     track_gen=1, track_gated=gated,
                                     track_small=None)

    win._keep_tracking(signal(True))
    assert win.slidemap._advisory is None

    for _i in range(25):                      # sustained, not instantaneous
        win._keep_tracking(signal(False))
    assert _("advice.track.blank") in win.slidemap._advisory

    win._keep_tracking(signal(True, gated=3))
    assert _("advice.track.fast") in win.slidemap._advisory

    win._track_wanted = False                 # asleep on purpose: quiet
    win._keep_tracking(signal(False, gated=9))
    assert win.slidemap._advisory is None


def test_coverage_never_shrinks():
    """The postcard map could lose ground on a partial revisit; the
    canvas cannot, structurally -- painting is additive. This test
    stands guard over that property through a meandering pass."""
    m = SlideMap()
    frame = np.full((60, 80, 3), 120, np.uint8)
    seen = 0.0
    walk = [(0.0, 0.0), (40.0, 0.0), (8.0, 0.0), (60.0, 20.0),
            (30.0, 10.0), (90.0, 0.0), (0.0, 0.0)]
    for pos in walk:
        for _ in range(SlideMap.REFRESH_EVERY + 1):
            m.observe(pos, frame, True)
        now = m.terrain.fields_painted
        assert now >= seen, "coverage shrank while crossing the map"
        seen = now


# ---- the terrain canvas ------------------------------------------------------

def test_growth_does_not_move_the_world():
    """Reallocation shifts the buffer and the origin together: ground
    painted before a growth sits at the same world position after."""
    m = SlideMap()
    frame = np.zeros((60, 80, 3), np.uint8)
    frame[:, :40] = 200                        # a recognisable half
    m.observe((0.0, 0.0), frame, True)
    before, _s1, at1 = m.terrain.window((0.0, 0.0))
    m.observe((80 * 30.0, 0.0), np.full((60, 80, 3), 90, np.uint8), True)
    assert m.terrain._grown >= 1, "the walk should have grown the canvas"
    after, _s2, at2 = m.terrain.window((0.0, 0.0))
    assert np.array_equal(before, after), "growth moved painted ground"
    assert at1 == at2


def test_the_canvas_has_a_ceiling():
    t = Terrain()
    frame = np.full((60, 80, 3), 120, np.uint8)
    assert t.paint((0.0, 0.0), frame)
    assert not t.paint((80.0 * Terrain.MAX_SIDE, 0.0), frame), \
        "the map must stop growing rather than evict"
    assert t.paint((40.0, 0.0), frame), "nearby ground still paints"


def test_the_frontier_has_no_dark_fringe():
    """Feathering applies only over already-painted ground: a ramp
    against the void would blend toward zero and rim every frontier
    with darkness."""
    t = Terrain()
    t.paint((0.0, 0.0), np.full((60, 80, 3), 200, np.uint8))
    painted = t.rgba[t.rgba[..., 3] > 0]
    assert painted[:, :3].min() >= 199, "the frontier was feathered dark"


def test_a_foreign_preview_size_is_refused_by_the_canvas():
    t = Terrain()
    assert t.paint((0.0, 0.0), np.full((60, 80, 3), 120, np.uint8))
    assert not t.paint((0.0, 0.0), np.full((30, 40, 3), 120, np.uint8))
    assert t.size == (80, 60)


def test_windows_report_their_actual_centre():
    """A window lands on an integer canvas pixel; the rounding remainder
    is real distance and was measured, in the spike, as eight preview
    pixels of silent error when computed against the request."""
    t = Terrain()
    t.paint((0.0, 0.0), np.full((60, 80, 3), 120, np.uint8))
    _win, _share, at = t.window((0.7, -0.3))
    half = t.scale / 2 + 1e-6
    assert abs(at[0] - 0.7) <= half and abs(at[1] + 0.3) <= half
