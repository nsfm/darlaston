"""The auto-exposure loop, driven against a simulated sensor.

A control loop that has never been tested for stability is a control loop
that oscillates on the bench at the worst moment, so most of these run the
loop to convergence and then check that it stays put. The sensor model is
deliberately crude -- brightness proportional to exposure times gain, and
clipping at full scale -- because what is under test is the controller,
not the physics.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from darlaston.live import exposure as E

FULL = 255.0


class _Sensor:
    """Brightness proportional to exposure and gain, clipped at full scale.

    `scene` is what one microsecond at unity gain would produce, which is
    the only knob a test needs: dim scene, bright scene, lamp turned up
    mid run.
    """

    def __init__(self, scene: float = 1.2e-4, size: int = 64):
        self.scene = scene
        self.size = size

    def frame(self, state: E.State) -> np.ndarray:
        level = self.scene * state.exposure_us * (state.gain_pct / 100.0)
        img = np.full((self.size, self.size), level * FULL, np.float32)
        # A little structure, so a percentile is not identical to a mean.
        img[: self.size // 4] *= 0.35
        return np.clip(img, 0, FULL)


def _limits(readout_us=8000, target_fps=30.0, gain=(100, 4000)):
    return E.Limits(exposure_us=(100, 2_000_000), gain_pct=gain,
                    readout_us=readout_us, target_fps=target_fps)


def _settle(sensor, state, limits, rounds=200, **kw):
    """Run the loop against the sensor, metering the way the app will."""
    seen = []
    for _ in range(rounds):
        level = E.measure_hist(_hist(sensor.frame(state)))
        seen.append(level)
        state = E.step(state, limits, level, **kw)
    return state, seen


def _hist(img):
    return cv2.calcHist([img.astype(np.uint8)], [0], None, [256],
                        [0, 256]).ravel()


# ---- the ceiling ------------------------------------------------------------

def test_ceiling_is_the_frame_rate_the_operator_asked_for():
    lim = _limits(readout_us=8000, target_fps=30.0)
    assert lim.exposure_ceiling_us == pytest.approx(33333, abs=2)


def test_a_slow_mode_may_expose_for_its_whole_frame_period():
    """At full resolution this sensor reads out at 12 fps whatever the
    exposure, so 83 ms there costs the operator nothing and capping at
    1/30 s would spend gain to buy frame rate the mode cannot deliver."""
    lim = _limits(readout_us=83_000, target_fps=30.0)
    assert lim.exposure_ceiling_us == 83_000


def test_the_ceiling_never_exceeds_what_the_camera_offers():
    lim = E.Limits(exposure_us=(100, 20_000), gain_pct=(100, 4000),
                   readout_us=83_000, target_fps=30.0)
    assert lim.exposure_ceiling_us == 20_000


def test_a_camera_with_neither_control_is_left_alone():
    lim = E.Limits(exposure_us=(500, 500), gain_pct=(100, 100))
    state = E.State(exposure_us=500, gain_pct=100)
    assert E.step(state, lim, 0.01) == state


# ---- metering ---------------------------------------------------------------

def test_darkfield_meters_further_out_than_brightfield():
    """A sparse specimen on a black field has to be read from the tail.

    The threshold is not rhetorical: at 2% coverage a 99th percentile
    still lands inside the specimen and either setting would do. It is
    below one percent that brightfield metering starts reading the black
    surround and opening up until the specimen clips, and a diatom or two
    on a darkfield mount is well under that.
    """
    img = np.zeros((200, 200), np.float32)
    img[:1] = 200.0                              # 0.5% of the frame
    assert E.measure(img, FULL, "darkfield") > 0.5
    assert E.measure(img, FULL, "brightfield") < 0.1


def test_an_unknown_illumination_still_meters_something_sensible():
    img = np.full((32, 32), 128.0, np.float32)
    assert 0.4 < E.measure(img, FULL, "wobble") < 0.6
    assert 0.4 < E.measure(img, FULL, None) < 0.6


def test_error_is_in_stops_and_signed_toward_brighter():
    assert E.error_stops(0.45, 0.90) == pytest.approx(1.0)
    assert E.error_stops(1.80, 0.90) == pytest.approx(-1.0)


def test_a_black_frame_asks_for_more_light_rather_than_dividing_by_zero():
    assert E.error_stops(0.0, 0.9) > 5.0


# ---- convergence ------------------------------------------------------------

@pytest.mark.parametrize("scene", [1e-6, 1e-5, 1.2e-4, 3e-3])
def test_converges_and_holds(scene):
    sensor = _Sensor(scene)
    lim = _limits()
    state = E.State(exposure_us=10_000, gain_pct=100)
    state, _ = _settle(sensor, state, lim)

    level = E.measure_hist(_hist(sensor.frame(state)))
    assert abs(E.error_stops(level, E.TARGET)) < E.DEADBAND_STOPS * 2

    # And having arrived, it stops moving.
    after, seen = _settle(sensor, state, lim, rounds=30)
    assert after.exposure_us == state.exposure_us
    assert after.gain_pct == state.gain_pct
    assert max(seen) - min(seen) < 0.02


def test_it_does_not_oscillate_on_the_way_in():
    """Overshoot is what an eager loop looks like from the operator's
    chair: the picture pumps light and dark before settling."""
    sensor = _Sensor(1e-5)
    state = E.State(exposure_us=10_000, gain_pct=100)
    _final, seen = _settle(sensor, state, _limits(), rounds=60)
    # Once past the target it must not cross back and forth repeatedly.
    signs = np.sign([E.error_stops(v, E.TARGET) for v in seen])
    crossings = int((np.diff(signs) != 0).sum())
    assert crossings <= 2, crossings


def test_a_bright_scene_is_pulled_down_faster_than_a_dark_one_is_raised():
    """Clipping cannot be undone afterwards; darkness only costs noise."""
    lim = _limits()
    start = E.State(exposure_us=10_000, gain_pct=100)
    down = E.step(start, lim, E.TARGET * 2)     # a stop too bright
    up = E.step(start, lim, E.TARGET / 2)       # a stop too dark
    fell = 10_000 - down.exposure_us
    rose = up.exposure_us - 10_000
    assert fell > 0 and rose > 0
    assert fell / 10_000 > rose / 10_000


# ---- the ladder -------------------------------------------------------------

def test_exposure_is_spent_before_gain():
    lim = _limits(readout_us=8000)
    state = E.State(exposure_us=1_000, gain_pct=100)
    out = E.step(state, lim, 0.45, hurry=True)
    assert out.exposure_us > 1_000
    assert out.gain_pct == 100


def test_gain_takes_over_once_exposure_reaches_the_ceiling():
    lim = _limits(readout_us=8000)                  # ceiling 33.3 ms
    state = E.State(exposure_us=lim.exposure_ceiling_us, gain_pct=100)
    out = E.step(state, lim, 0.45, hurry=True)
    assert out.exposure_us <= lim.exposure_ceiling_us + 1
    assert out.gain_pct > 100


def test_gain_comes_off_before_exposure_does():
    """The ladder is its own hysteresis: a scene sitting at the handover
    moves one control at a time rather than trading between them."""
    lim = _limits()
    state = E.State(exposure_us=lim.exposure_ceiling_us, gain_pct=800)
    out = E.step(state, lim, 0.90 * 2, hurry=True)
    assert out.gain_pct < 800
    assert out.exposure_us == state.exposure_us


def test_a_locked_exposure_is_never_moved():
    """For a camera whose exposure response is not monotonic, where a loop
    that hunts on exposure will never settle."""
    lim = _limits()
    sensor = _Sensor(1e-5)
    state = E.State(exposure_us=12_345, gain_pct=100, lock_exposure=True)
    state, _ = _settle(sensor, state, lim)
    assert state.exposure_us == 12_345
    assert state.gain_pct > 100


def test_locked_exposure_still_converges_when_gain_alone_can_reach_it():
    lim = _limits(gain=(100, 8000))
    sensor = _Sensor(2e-6)
    state = E.State(exposure_us=20_000, gain_pct=100, lock_exposure=True)
    state, _ = _settle(sensor, state, lim)
    level = E.measure_hist(_hist(sensor.frame(state)))
    assert abs(E.error_stops(level, E.TARGET)) < E.DEADBAND_STOPS * 2


# ---- behaviour the operator feels ------------------------------------------

def test_small_errors_are_left_alone():
    lim = _limits()
    state = E.State(exposure_us=10_000, gain_pct=100)
    nudged = E.TARGET * 2 ** (E.DEADBAND_STOPS * 0.5)
    assert E.step(state, lim, nudged) == state


def test_hurry_crosses_the_deadband_because_the_caller_knows_better():
    """An objective change loses four to sixteen times the light, and the
    selector knows it happened before the histogram does."""
    lim = _limits()
    state = E.State(exposure_us=10_000, gain_pct=100)
    tiny = E.TARGET * 2 ** (E.DEADBAND_STOPS * 0.5)
    assert E.step(state, lim, tiny, hurry=True) != state


def test_reactivity_scales_how_far_one_step_travels():
    lim = _limits()
    state = E.State(exposure_us=10_000, gain_pct=100)
    calm = E.step(state, lim, 0.90 / 1.5, reactivity=0.25)
    eager = E.step(state, lim, 0.90 / 1.5, reactivity=1.0)
    assert 10_000 < calm.exposure_us < eager.exposure_us


def test_it_recovers_from_a_lamp_turned_up_mid_run():
    lim = _limits()
    sensor = _Sensor(1e-5)
    state = E.State(exposure_us=10_000, gain_pct=100)
    state, _ = _settle(sensor, state, lim)
    sensor.scene *= 8.0                       # three stops of lamp
    state, _ = _settle(sensor, state, lim, rounds=60)
    level = E.measure_hist(_hist(sensor.frame(state)))
    assert abs(E.error_stops(level, E.TARGET)) < E.DEADBAND_STOPS * 2


def test_it_never_leaves_the_camera_out_of_range():
    lim = E.Limits(exposure_us=(500, 30_000), gain_pct=(100, 200),
                   readout_us=8000)
    for scene in (1e-9, 1e-7, 1e-2, 1.0):
        sensor = _Sensor(scene)
        state, _ = _settle(sensor, E.State(10_000, 100), lim, rounds=80)
        assert 500 <= state.exposure_us <= 30_000
        assert 100 <= state.gain_pct <= 200


# ---- metering without being told the mode -----------------------------------

def _sparse(coverage, level=150.0, size=(600, 800), seed=3):
    """A bright specimen on a dark field, occupying `coverage` of it."""
    rng = np.random.default_rng(seed)
    img = rng.normal(6, 2, size).clip(0, 255)
    n = int(coverage * img.size)
    flat = img.ravel()
    flat[rng.choice(img.size, n, replace=False)] = \
        rng.normal(level, 25, n).clip(0, 255)
    return img.astype(np.uint8)


@pytest.mark.parametrize("coverage", [0.20, 0.05, 0.02, 0.005, 0.001,
                                      0.0005])
def test_the_reading_does_not_depend_on_how_much_frame_the_subject_fills(
        coverage):
    """The whole reason the histogram is split rather than sliced.

    A crowded darkfield mount and a single diatom on one differ by two
    orders of magnitude in coverage and not at all in what correct
    exposure means. Measured steady at about 0.71 across that range,
    where a fixed 99th percentile falls from 0.749 to 0.039 -- the black
    surround, which a loop would then drive to target and bury the
    specimen four stops into clipping.
    """
    level = E.measure_hist(_hist(_sparse(coverage)))
    assert 0.6 < level < 0.85


def test_a_fixed_percentile_is_what_this_replaced():
    """Kept as the counter-example, so the table is not reinstated."""
    thin = _hist(_sparse(0.001))
    assert E._quantile(thin, 99.0) < 0.1          # reads the black
    assert E.measure_hist(thin) > 0.6             # reads the specimen


def test_it_stands_down_when_there_is_only_one_population():
    """Otsu always returns a threshold, including on a frame that holds
    nothing to threshold, where it cuts the sensor noise in half. At a
    specimen covering 0.01% of the frame that is exactly what happens,
    and the reading collapses to 0.035 unless the separation is checked.
    """
    almost_nothing = _hist(_sparse(0.0001))
    assert E.measure_hist(almost_nothing) < 0.2   # falls back, not 0.035
    assert E.measure_hist(almost_nothing) == E._quantile(almost_nothing,
                                                         99.5)


def test_metering_is_proportional_to_exposure_in_both_polarities():
    """What a control loop actually needs: double the light, double the
    reading. Verified on a real brightfield micrograph and its inverse,
    where the frame *mean* differs fourfold at identical exposure."""
    rng = np.random.default_rng(11)
    scene = rng.normal(200, 18, (400, 500)).clip(0, 255)
    scene[120:180, 100:300] = rng.normal(60, 12, (60, 200)).clip(0, 255)
    for base in (scene, 255.0 - scene):
        readings = []
        for k in (0.25, 0.5):
            dim = np.clip(base * k, 0, 255)
            readings.append(E.measure_hist(_hist(dim)))
        assert readings[1] / readings[0] == pytest.approx(2.0, rel=0.06)


def test_a_uniform_frame_falls_back_instead_of_splitting_noise():
    """Otsu will always return a threshold; on a flat frame it is
    meaningless, so the split has to notice and stand down."""
    flat = _hist(np.full((200, 200), 180, np.uint8))
    assert E.measure_hist(flat) == pytest.approx(180 / 255, abs=0.02)


def test_an_empty_histogram_reads_zero_rather_than_raising():
    assert E.measure_hist(np.zeros(256, np.float32)) == 0.0
