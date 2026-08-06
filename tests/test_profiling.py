"""Measuring what a camera really does.

Every number these tests assert against came off real hardware first:
a 1280x720 mode that is a pure centre crop, a 640x480 that is a 2x
downscale as well, and an exposure response that drops at 32 and jumps
at 48 reproducibly in both sweep directions.
"""
import numpy as np

from darlaston.camera.profiling import (Response, TRUSTED, measure_geometry,
                                        measure_response)


def _field(w, h, seed=0):
    """Something with detail in it, since that is what matching needs."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h // 8, w // 8), dtype=np.uint8)
    import cv2
    return cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)


def test_a_centre_crop_is_recognised_as_one():
    """1280x720 out of 1920x1080 at 1:1 -- measured at 0.99 on real
    hardware."""
    import cv2

    big = _field(1920, 1080)
    crop = big[180:180 + 720, 320:320 + 1280]
    got, = measure_geometry(big, {(1280, 720): crop})
    assert got.trusted, f"confidence only {got.confidence:.2f}"
    assert abs(got.scale - 1.0) < 0.01, f"called a crop a {got.scale}x scale"
    assert abs(got.left - 320) <= 2 and abs(got.top - 180) <= 2


def test_a_downscaled_mode_is_not_mistaken_for_a_crop():
    """640x480 on the real camera is a 2x downscale *and* a crop, so it
    covers a wider field than 800x600 does -- which nothing about the
    pixel counts would tell you. Searching only 1:1 calls this "no
    match", which is what happened before scales were tried at all."""
    import cv2

    big = _field(1920, 1080, seed=1)
    half = cv2.resize(big, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    mode = half[30:30 + 480, 160:160 + 640]

    got, = measure_geometry(big, {(640, 480): mode})
    assert got.trusted, f"confidence only {got.confidence:.2f}"
    assert abs(got.scale - 2.0) < 0.01, f"read the scale as {got.scale}"
    # And it really does cover more slide than a larger-numbered mode.
    smaller_but_wider = got.field_fraction
    crop800 = measure_geometry(big, {(800, 600): big[240:840, 560:1360]})[0]
    assert smaller_but_wider > crop800.field_fraction, \
        "640x480 should see more of the slide than 800x600 on this camera"


def test_a_blank_field_is_reported_as_unmeasured():
    """The failure that matters. A field with nothing in it will happily
    return a confident-looking answer to a question it cannot see, and
    believing it would put a wrong micrometres-per-pixel into every
    file."""
    blank = np.full((1080, 1920), 128, dtype=np.uint8)
    got, = measure_geometry(blank, {(1280, 720): np.full((720, 1280), 128,
                                                         dtype=np.uint8)})
    assert not got.trusted, \
        f"claimed {got.confidence:.2f} confidence about a blank field"


def test_the_usable_range_stops_where_the_camera_stops_behaving():
    """A slider has to be monotonic or it is not a slider. This camera's
    brightness climbs to 31, drops hard at 32, climbs again to 47, then
    jumps at 48 -- reproducible in both directions, so it is the device
    and not the measurement."""
    measured = Response(points=tuple(
        [(e, 8.0 + e * 4.3) for e in range(1, 32)]        # climbs
        + [(e, 53.0 + (e - 32) * 6.0) for e in range(32, 48)]   # drops, climbs
        + [(e, 203.0 + (e - 48) * 3.0) for e in range(48, 52)]  # jumps
    ))
    usable = measured.usable
    assert usable, "found no monotonic run at all"
    values = [v for v, _l in usable]
    levels = [l for _v, l in usable]
    assert levels == sorted(levels), "the usable run is not monotonic"
    assert values[0] == 1 and values[-1] == 31, \
        f"took {values[0]}..{values[-1]}; the clean run is 1..31"


def test_asking_for_a_brightness_gives_the_shortest_exposure_reaching_it():
    """Where a camera reaches the same level at more than one setting,
    the shorter exposure moves less and reads out sooner."""
    measured = Response(points=((1, 10.0), (2, 20.0), (3, 40.0), (4, 40.0)))
    assert measured.value_for(20.0) == 2
    assert measured.value_for(35.0) == 3
    assert measured.value_for(40.0) == 3, "picked the longer of two equals"
    assert measured.value_for(999.0) == 4, "asked past the end"


def test_a_sweep_records_what_it_was_told():
    """The sweep itself owns no hardware: it is handed a setter and a
    reader, so it can be run against anything and tested against
    nothing."""
    seen, level = [], {"v": 0}

    def set_value(v):
        seen.append(v)
        level["v"] = v * 3.0

    got = measure_response(lambda: level["v"], set_value, [1, 2, 5])
    assert seen == [1, 2, 5], "did not sweep what it was given"
    assert got.points == ((1, 3.0), (2, 6.0), (5, 15.0))


def test_a_dark_field_is_not_a_response_curve():
    """The failure that actually happened. Run against an unlit field,
    the first fourteen readings were all level 3 and the rest jumped
    151, 26, 24, 74 with no relation to exposure. The longest accidental
    monotonic run through that noise was twelve steps, and it was stored
    as though it meant something."""
    dark = Response(points=tuple((v, 3.0) for v in range(1, 40)))
    assert not dark.trustworthy, "a flat noise floor read as a measurement"

    real = Response(points=tuple((v, min(255.0, v * 8.0))
                                 for v in range(1, 40)))
    assert real.trustworthy


def test_readings_that_move_without_the_control_are_refused():
    """What an unsettled sweep looks like: plenty of swing, no order."""
    import random

    rng = random.Random(7)
    noisy = Response(points=tuple((v, rng.uniform(20, 160))
                                  for v in range(1, 40)))
    assert not noisy.trustworthy, "stored noise as a response curve"


def test_the_sweep_is_planned_from_the_field_not_from_the_range():
    """Sweeping a fixed range regardless of illumination is what put
    fourteen noise-floor readings at the front of a real table."""
    from darlaston.camera.profiling import plan_sweep

    class _Bright:
        """Responds early: clipped by exposure 20."""

        def __init__(self):
            self.value = 1

        def set_exposure(self, us):
            self.value = us // 100

        def level(self):
            return min(255.0, self.value * 14.0)

    planned = plan_sweep(_Bright(), (100, 500_000))
    assert planned, "found no usable range on a field that responds"
    assert max(planned) < 200, \
        f"kept sweeping to {max(planned)} long after it clipped"

    class _Dark:
        def set_exposure(self, us):
            pass

        def level(self):
            return 3.0

    assert plan_sweep(_Dark(), (100, 500_000)) == [], \
        "planned a sweep of a field that never responds"
