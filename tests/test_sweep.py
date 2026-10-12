"""The motorised stack: a plan, a motor, and a picture that settles."""
from __future__ import annotations

import numpy as np
import pytest

from darlaston.camera.base import FocusAxis, FocusRange
from darlaston.capture.sweep import FocusSweep, Phase, plan


class _Axis(FocusAxis):
    def __init__(self, lo=0, hi=1000, step=1, at=500):
        self._range = FocusRange(lo, hi, step)
        self.position = at
        self.moves: list[int] = []
        self.auto: list[bool] = []

    def range(self):
        return self._range

    def get(self):
        return self.position

    def set(self, position):
        self.position = int(position)
        self.moves.append(int(position))

    def set_auto(self, on):
        self.auto.append(bool(on))


class _Signals:
    def __init__(self, field):
        self.sharpness_field = field


def _field(seed: int, noise: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random((64, 64)).astype(np.float32)
            + noise * np.random.default_rng(seed + 1000).random((64, 64)))


def test_a_plan_runs_near_to_far_inclusive():
    assert plan(100, 500, 5) == [100, 200, 300, 400, 500]
    assert plan(500, 100, 3) == [500, 300, 100], "the order is the operator's"
    assert plan(100, 100, 7) == [100], "a zero span is one plane, not seven"
    assert plan(0, 10, 1) == [0]


def test_a_plan_never_visits_one_plane_twice():
    """More slices than steps collapses to each step once."""
    got = plan(0, 4, 20)
    assert got == [0, 1, 2, 3, 4]
    assert plan(0, 100, 11, step=10) == list(range(0, 101, 10))


def _run_one_cycle(sweep: FocusSweep, fired: list, still_field, loud_field):
    """Frames after a move: first a few loud ones, then quiet ones."""
    for _ in range(2):
        assert not sweep.observe(_Signals(loud_field))
    for _ in range(sweep.MIN_FRAMES + sweep.QUIET_FRAMES + 2):
        if sweep.observe(_Signals(still_field)):
            return True
    return False


def test_the_sweep_moves_waits_fires_and_moves_again():
    axis = _Axis()
    fired = []
    sweep = FocusSweep(lambda: axis, lambda: fired.append(1) or True)
    assert sweep.start(plan(100, 300, 3))
    assert axis.auto == [False], "autofocus is taken away before the sweep"
    assert axis.moves == [100]
    assert sweep.phase is Phase.MOVING

    quiet = _field(1)
    assert _run_one_cycle(sweep, fired, quiet, _field(2))
    assert fired == [1] and sweep.phase is Phase.EXPOSING
    # Holding still while exposing must not fire again.
    assert not sweep.observe(_Signals(quiet))
    sweep.slice_landed()
    assert axis.moves == [100, 200] and sweep.phase is Phase.MOVING
    assert sweep.done == 1 and sweep.total == 3

    assert _run_one_cycle(sweep, fired, _field(3), _field(4))
    sweep.slice_landed()
    assert _run_one_cycle(sweep, fired, _field(5), _field(6))
    sweep.slice_landed()
    assert sweep.phase is Phase.DONE and sweep.finished
    assert sweep.done == 3
    assert axis.moves[-1] == 500, "the motor goes back where it started"


def test_a_refused_fire_is_asked_again_next_frame():
    axis = _Axis()
    answers = [False, False, True]
    sweep = FocusSweep(lambda: axis, lambda: answers.pop(0))
    sweep.start([10])
    quiet = _field(9)
    hits = 0
    for _ in range(sweep.MIN_FRAMES + sweep.QUIET_FRAMES + 6):
        if sweep.observe(_Signals(quiet)):
            hits += 1
    assert hits == 1 and not answers, "it kept asking until the shutter said yes"


def test_a_picture_that_never_settles_still_fires_eventually(monkeypatch):
    axis = _Axis()
    fired = []
    sweep = FocusSweep(lambda: axis, lambda: fired.append(1) or True)
    clock = [0.0]
    monkeypatch.setattr("darlaston.capture.sweep.time.monotonic",
                        lambda: clock[0])
    sweep.start([42])
    for i in range(20):
        clock[0] += 0.1
        sweep.observe(_Signals(_field(100 + i)))      # different every frame
    assert not fired, "noise alone is not stillness"
    clock[0] += sweep.SETTLE_TIMEOUT
    assert sweep.observe(_Signals(_field(999)))
    assert fired == [1]


def test_a_failed_capture_stops_the_sweep_and_says_so():
    axis = _Axis(at=250)
    sweep = FocusSweep(lambda: axis, lambda: True)
    sweep.start([0, 100])
    assert _run_one_cycle(sweep, [], _field(1), _field(2))
    sweep.capture_failed()
    assert sweep.finished and sweep.failed and sweep.phase is Phase.IDLE
    assert axis.position == 250


def test_abort_goes_home_and_keeps_quiet():
    axis = _Axis(at=777)
    sweep = FocusSweep(lambda: axis, lambda: True)
    sweep.start([1, 2, 3])
    sweep.abort()
    assert sweep.phase is Phase.IDLE and not sweep.finished
    assert axis.position == 777
    assert not sweep.observe(_Signals(_field(1)))


def test_no_motor_means_no_sweep():
    sweep = FocusSweep(lambda: None, lambda: True)
    assert not sweep.start([1, 2])
    assert sweep.phase is Phase.IDLE


def test_the_mock_camera_can_grow_a_motor():
    from darlaston.camera.mock import MockCamera

    cam = MockCamera()
    assert cam.focus_axis is None, "most cameras have no motor; the mock agrees"
    motor = cam.grow_focus_motor()
    assert cam.focus_axis is motor
    motor.set(motor.range().hi)
    assert cam.focus_z == pytest.approx(10.0)
    motor.set_auto(True)
    assert cam.focus_z == pytest.approx(0.0), "autofocus finds the plane"
