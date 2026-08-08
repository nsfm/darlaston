"""Auto-exposure where it meets the window: interlocks and the sliders.

The control law is tested against a simulated sensor in test_exposure.py.
What is left here is everything that made it a feature rather than a
function -- when it is allowed to run, and where it puts its answer.
"""
from __future__ import annotations

import numpy as np
import pytest

from darlaston.live import exposure as E


def _signals(win, level: float = 0.2, seq: int = 1, when: float = 0.0):
    """A stand-in for a frame's worth of signals, at a chosen brightness."""
    hist = np.zeros(256, np.float32)
    hist[2] = 90_000.0                              # a dark field
    hist[int(np.clip(level, 0, 1) * 255)] = 10_000.0
    return type("S", (), {"histogram": hist, "seq": seq, "timestamp": when})()


def _at(win):
    return (win._slider_to_us(win.exposure.value()), int(win.gain.value()))


def _with_camera(win, monkeypatch, gain=(100, 4000)):
    """Stand in for an opened camera.

    The window fixture's camera opens asynchronously, so `status.info` is
    None for a while and the loop correctly declines to run without it.
    That is the behaviour under test elsewhere; here it is in the way.
    """
    monkeypatch.setattr(win, "_auto_exposure_limits", lambda: E.Limits(
        exposure_us=(300, 2_000_000), gain_pct=gain,
        readout_us=8000, target_fps=30.0))


# ---- the interlocks ---------------------------------------------------------

@pytest.mark.parametrize("busy", [
    "capture", "calibration", "stack", "mosaic", "sweep",
])
def test_it_does_not_move_while_something_else_owns_the_light(
        window, busy, monkeypatch):
    """A stack is the load-bearing one and not a courtesy.

    The merge blends slices assuming they share a photometric scale, and
    tools/stack_bench.py carries a scene called `drift` because a few
    percent of per-slice variation terraces the result. A loop adjusting
    exposure while somebody racks would manufacture that artefact.
    """
    win = window()
    win.settings.auto_exposure = True
    before = _at(win)

    if busy == "capture":
        monkeypatch.setattr(type(win.capture), "busy",
                            property(lambda _s: True))
    elif busy == "calibration":
        monkeypatch.setattr(type(win.calibration), "busy",
                            property(lambda _s: True))
    elif busy == "stack":
        win.stack_session = object()
    elif busy == "mosaic":
        win.mosaic = object()
    elif busy == "sweep":
        win.pipeline._sweeping = True

    assert not win._auto_exposure_allowed()
    win._auto_expose(_signals(win, 0.02))
    assert _at(win) == before


def test_leaving_an_interlock_resumes_at_full_stride(window):
    """Two minutes of stacking may have left the scene somewhere else
    entirely, and a loop damped to be calm would crawl back."""
    win = window()
    win.settings.auto_exposure = True
    win.stack_session = object()
    win._ae_hurry = False
    win._auto_expose(_signals(win, 0.02))
    assert win._ae_hurry is True


def test_switching_objective_asks_for_full_stride(window, monkeypatch):
    """Four to sixteen times the light arrives at once, and the selector
    knows before the histogram does."""
    win = window()
    win._ae_hurry = False
    # The rest of the handler wants a configured stand; only the flag is
    # under test, and it has to be set before anything that can fail.
    monkeypatch.setattr(win, "_remember_objective", lambda: None)
    try:
        win._on_objective_changed()
    except Exception:
        pass
    assert win._ae_hurry is True


# ---- where the answer goes --------------------------------------------------

def test_the_answer_lands_on_the_sliders(window, monkeypatch):
    """Through the controls rather than around them, which is what makes
    switching auto off leave them where auto had them."""
    win = window()
    _with_camera(win, monkeypatch)
    win.settings.auto_exposure = True
    win._ae_hurry = True
    before = _at(win)
    win._auto_expose(_signals(win, 0.02))          # far too dark
    after = _at(win)
    assert after != before
    # Too dark, so it opened up rather than stopping down.
    assert after[0] > before[0] or after[1] > before[1]


def test_turning_it_off_leaves_the_controls_where_it_left_them(window,
                                                               monkeypatch):
    win = window()
    _with_camera(win, monkeypatch)
    win.settings.auto_exposure = True
    win._ae_hurry = True
    win._auto_expose(_signals(win, 0.02))
    settled = _at(win)

    win.settings.auto_exposure = False
    for i in range(5):
        win._auto_expose(_signals(win, 0.02, seq=i + 2, when=i * 0.1))
    assert _at(win) == settled


def test_a_camera_with_neither_control_is_left_alone(window, monkeypatch):
    """A control the camera does not have is a range of one value, and
    the ladder simply never spends anything there."""
    win = window()
    monkeypatch.setattr(win, "_auto_exposure_limits", lambda: E.Limits(
        exposure_us=(8330, 8330), gain_pct=(100, 100), readout_us=8000))
    win.settings.auto_exposure = True
    win._ae_hurry = True
    before = _at(win)
    win._auto_expose(_signals(win, 0.02))
    assert _at(win) == before


def test_gain_alone_still_works_when_exposure_is_fixed(window, monkeypatch):
    """A camera offering only gain is not a camera with no auto-exposure."""
    win = window()
    monkeypatch.setattr(win, "_auto_exposure_limits", lambda: E.Limits(
        exposure_us=(8330, 8330), gain_pct=(100, 4000), readout_us=8000))
    win.settings.auto_exposure = True
    win._ae_hurry = True
    # Start where that camera can actually be: a slider outside the
    # camera's range is a separate wrong that the loop rightly corrects.
    win.exposure.setValue(win._us_to_slider(8330))
    before = _at(win)
    win._auto_expose(_signals(win, 0.02))
    after = _at(win)
    assert after[0] == before[0]
    assert after[1] > before[1]


# ---- the frame rate it meters against ---------------------------------------

def test_frame_rate_comes_from_sequence_numbers_not_arrivals(window):
    """Signals are coalesced, so counting arrivals here measures how busy
    the window is rather than how fast the sensor reads out."""
    win = window()
    win._ae_fps = 0.0
    win._ae_rate_mark = None
    win._note_frame_rate(_signals(win, seq=100, when=10.0))
    win._note_frame_rate(_signals(win, seq=130, when=11.0))   # 30 frames, 1 s
    assert win._ae_fps == pytest.approx(30.0, rel=0.05)


def test_a_gap_does_not_count_as_a_slow_frame(window):
    """A capture freezes the preview for over a second. Reading that as a
    frame rate would convince the loop it has headroom it does not."""
    win = window()
    win._ae_fps = 0.0
    win._ae_rate_mark = None
    win._note_frame_rate(_signals(win, seq=1, when=0.0))
    win._note_frame_rate(_signals(win, seq=2, when=9.0))      # a long gap
    assert win._ae_fps == 0.0


def test_an_unlearned_readout_reads_zero(window):
    """Subtracting the exposure from the measured period was the first
    answer here and it is what produced the light show. There is nothing
    to report until a frame arrives that exposure was not holding up."""
    win = window()
    win._ae_fps = 10.0
    assert win._measured_frame_period_us() == 0.0


def test_an_unmeasured_rate_lets_the_target_govern(window):
    win = window()
    win._ae_fps = 0.0
    assert win._measured_frame_period_us() == 0.0
    lim = win._auto_exposure_limits()
    if lim is not None:
        assert lim.exposure_ceiling_us == pytest.approx(
            1e6 / win.settings.auto_exposure_target_fps, rel=0.01)


# ---- it must never take the picture down ------------------------------------

def test_a_fault_stands_down_rather_than_stopping_the_preview(window,
                                                              monkeypatch):
    """It runs inside the handler that draws every frame."""
    win = window()
    win.settings.auto_exposure = True

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(win, "_auto_expose", boom)
    win._auto_expose_guarded(_signals(win))      # must not raise
    assert win.settings.auto_exposure is False

    # And having stood down, it stays down rather than raising again on
    # every frame that follows.
    win._auto_expose_guarded(_signals(win))


# ---- the readout figure, across a mode change -------------------------------

def test_the_readout_is_learned_from_quiet_frames_only(window):
    """The 5 k preview reads out at 12 fps whatever the exposure, so a
    long exposure there must not be read as a slow sensor."""
    win = window()
    win._ae_fps = 0.0
    win._ae_rate_mark = None
    win.exposure.setValue(win._us_to_slider(8_000))
    win._note_frame_rate(_signals(win, seq=0, when=0.0))
    win._note_frame_rate(_signals(win, seq=12, when=1.0))       # 12 fps
    assert win._measured_frame_period_us() == pytest.approx(83_000, rel=0.05)

    # Now a long exposure, which is itself why the frames are slow.
    win.exposure.setValue(win._us_to_slider(400_000))
    win._note_frame_rate(_signals(win, seq=14, when=1.8))
    win._note_frame_rate(_signals(win, seq=16, when=2.6))
    assert win._measured_frame_period_us() == pytest.approx(83_000, rel=0.05)


def test_the_ceiling_holds_still_as_exposure_rises(window, monkeypatch):
    """The light show: a ceiling that falls as exposure rises has an
    unstable crossing at half the frame period, and the loop chatters over
    it swinging gain in and out."""
    win = window()
    win._ae_fps = 0.0
    win._ae_rate_mark = None
    win.exposure.setValue(win._us_to_slider(8_000))
    win._note_frame_rate(_signals(win, seq=0, when=0.0))
    win._note_frame_rate(_signals(win, seq=12, when=1.0))

    # Hold the camera at 12 fps throughout and vary only the exposure,
    # which is the situation on the 5 k preview: the readout is what makes
    # the frames slow, and a longer exposure under it changes nothing.
    seen = set()
    seq, when = 12, 1.0
    for us in (8_000, 20_000, 40_000, 43_000, 60_000, 82_000):
        win.exposure.setValue(win._us_to_slider(us))
        seq, when = seq + 12, when + 1.0
        win._note_frame_rate(_signals(win, seq=seq, when=when))
        seen.add(int(win._measured_frame_period_us()))
    assert len(seen) == 1, f"the readout figure moved: {sorted(seen)}"


def test_changing_preview_mode_forgets_the_old_readout(window):
    """The two modes here differ by seven times, so a stale figure would
    hold the ceiling badly wrong until it was relearned."""
    win = window()
    win._ae_fps = 0.0
    win._ae_rate_mark = None
    win.exposure.setValue(win._us_to_slider(8_000))
    win._note_frame_rate(_signals(win, seq=0, when=0.0))
    win._note_frame_rate(_signals(win, seq=12, when=1.0))
    assert win._measured_frame_period_us() > 0

    win.strip.preview.setCurrentIndex(0)
    if win.session.status.is_live:
        assert win._measured_frame_period_us() == 0.0


# ---- one step per observation ----------------------------------------------

def _levelled(win, level=0.02, seq=1, when=0.0, levels_seq=1):
    s = _signals(win, level, seq, when)
    s.levels_seq = levels_seq
    return s


def test_it_acts_once_per_reading_not_once_per_frame(window, monkeypatch):
    """The pipeline takes the levels every few frames and hands out the
    same array in between, so acting per frame applies one measurement's
    correction several times over. That is dead time, and dead time
    oscillates: measured on a simulated sensor at three frames of it, the
    exposure swings 1448x against 1.00x when the loop acts once per
    reading. Nate saw it as the picture pumping at the 5 k preview.
    """
    win = window()
    _with_camera(win, monkeypatch)
    win.settings.auto_exposure = True
    win._ae_hurry = True

    win._auto_expose(_levelled(win, levels_seq=7))
    once = _at(win)

    # Two more frames carrying the very same reading.
    win._auto_expose(_levelled(win, seq=2, when=0.1, levels_seq=7))
    win._auto_expose(_levelled(win, seq=3, when=0.2, levels_seq=7))
    assert _at(win) == once, "acted more than once on one measurement"

    # A fresh reading, and it moves again.
    win._auto_expose(_levelled(win, seq=4, when=0.3, levels_seq=8))
    assert _at(win) != once


def test_signals_without_a_levels_count_still_work(window, monkeypatch):
    """The field is defaulted so anything constructing signals directly
    keeps working; the loop must not refuse to run for want of it."""
    win = window()
    _with_camera(win, monkeypatch)
    win.settings.auto_exposure = True
    win._ae_hurry = True
    before = _at(win)
    win._auto_expose(_signals(win, 0.02))          # no levels_seq at all
    assert _at(win) != before
