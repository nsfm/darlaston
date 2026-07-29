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
