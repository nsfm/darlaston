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
from darlaston.live.tracker import SlideMap, StageTracker


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


def test_no_position_until_first_lock():
    t = StageTracker()
    assert t.position is None
    t.advance(None, 0.0, (100, 100))
    assert t.position is None, "a map must never be seeded from nothing"
    t.advance((1.0, 1.0), 0.9, (100, 100))
    assert t.position is not None


# ---- the map's banking policy ------------------------------------------------

def _preview(w=160, h=120):
    return np.zeros((h, w, 3), np.uint8)


def test_map_banks_on_movement_not_on_time():
    m = SlideMap()
    p = _preview()
    assert m.observe((0.0, 0.0), p, tracking=True)
    assert not m.observe((4.0, 0.0), p, tracking=True), \
        "hand tremor must not spawn snapshots"
    assert m.observe((0.5 * 160, 0.0), p, tracking=True)
    assert len(m.snapshots) == 2


def test_map_ignores_untracked_frames():
    m = SlideMap()
    assert not m.observe((0.0, 0.0), _preview(), tracking=False)
    assert not m.observe(None, _preview(), tracking=True)
    assert len(m.snapshots) == 0


def test_revisit_refreshes_in_place():
    """Coming back to mapped ground updates it rather than duplicating it,
    and not thirty times a second."""
    m = SlideMap()
    p = _preview()
    m.observe((0.0, 0.0), p, tracking=True)
    m.observe((200.0, 0.0), p, tracking=True)
    # Return to the origin: within REFRESH range, but rate-limited.
    changed = sum(m.observe((1.0, 0.0), p, tracking=True)
                  for _ in range(3 * SlideMap.REFRESH_EVERY))
    assert len(m.snapshots) == 2, "a revisit must not duplicate"
    assert 2 <= changed <= 4, "refresh should be rate-limited, not per-frame"


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
    _cam, pipe, _push, _ = _rig()
    assert pipe.guard_begin() is None, \
        "no frames yet, nothing to measure against — the guard must not arm"


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


def test_guard_times_out_to_none_when_no_frames_arrive():
    cam, pipe, push, _ = _rig()
    push(); push()
    token = pipe.guard_begin()
    assert pipe.guard_measure(token, timeout=0.05) is None
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

    # 40% of the height is past the limit for y.
    _, locked = t.advance((0.0, 0.40 * 1216), 0.9, shape)
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
