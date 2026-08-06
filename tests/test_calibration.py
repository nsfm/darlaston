"""Calibration maths.

Worth testing carefully: an error here is silent and corrupts every image that
passes through, in ways that look like the microscope's fault.
"""
import cv2
import numpy as np
import pytest

from darlaston.calib import frames as F
from darlaston.calib.preview_lut import build as build_lut
from darlaston.calib.service import BlankDetector, FlatBank


def test_average_does_not_overflow_uint16():
    """Summing uint16 in its own dtype wraps at four frames, and the result
    looks plausible rather than obviously broken."""
    stack = [np.full((8, 8), 60000, np.uint16) for _ in range(8)]
    assert F.average_frames(stack).mean() == pytest.approx(60000)


def test_median_rejects_debris_in_a_minority():
    """The whole reason a flat is medianed rather than averaged."""
    clean = [np.full((16, 16), 1000.0, np.float32) for _ in range(5)]
    clean[2][4:8, 4:8] = 3000.0          # a diatom in one frame only
    out = F.median_frames(clean)
    assert out.max() == pytest.approx(1000.0), "debris survived the median"


def test_normalise_flat_removes_the_checkerboard():
    """A single scalar norm would leave a 2x2 pattern, because the phases
    differ in sensitivity by about three times on this sensor."""
    flat = np.zeros((32, 32), np.float32)
    flat[0::2, 0::2] = 2400      # G
    flat[0::2, 1::2] = 800       # B
    flat[1::2, 0::2] = 1700      # R
    flat[1::2, 1::2] = 2400      # G
    out = F.normalise_flat(flat)
    assert out.std() == pytest.approx(0.0, abs=1e-5)


def test_white_balance_neutralises_a_blank_field():
    flat = np.zeros((32, 32), np.float32)
    flat[0::2, 0::2] = 2400
    flat[1::2, 1::2] = 2400
    flat[0::2, 1::2] = 800       # blue, low
    flat[1::2, 0::2] = 1700      # red
    r, g, b = F.white_balance_from_flat(flat)
    assert g == 1.0
    assert r == pytest.approx(2400 / 1700, rel=1e-3)
    assert b == pytest.approx(2400 / 800, rel=1e-3)


def test_defect_threshold_survives_a_low_noise_master():
    """A master averaged over many frames has almost no spread, so a pure
    sigma rule tightens as the calibration improves and eventually flags a
    large fraction of the sensor. This caught 8192 pixels before the floor."""
    dark = np.full((256, 256), 1.0, np.float32)
    dark[10, 10] = 400.0
    dark[20, 20] = 900.0
    found = F.defect_map(dark)
    assert len(found) == 2, f"expected the two hot pixels, got {len(found)}"


def test_calibration_flattens_a_gradient():
    truth = np.full((64, 64), 1000.0, np.float32)
    gradient = np.linspace(0.7, 1.0, 64, dtype=np.float32)[None, :]
    dark = np.full((64, 64), 5.0, np.float32)
    flat = (np.full((64, 64), 2000.0, np.float32) * gradient) + dark
    raw = truth * gradient + dark

    before = raw.std() / raw.mean()
    after = F.calibrate(raw, dark=dark, flat=flat)
    assert after.std() / after.mean() < before / 20, "gradient survived"


# ---- a camera that demosaices before we see it ---------------------------
#
# Every function above was written against a 2D sensor frame. A UVC camera
# hands over three separated channels instead, and reading those with Bayer
# strides is silently wrong rather than an error: it is arithmetic that
# succeeds and means nothing.


def _bgr_flat(blue: float, green: float, red: float) -> np.ndarray:
    out = np.zeros((32, 32, 3), np.float32)
    out[..., 0], out[..., 1], out[..., 2] = blue, green, red
    return out


def test_normalise_flat_does_not_invent_a_checkerboard_on_colour():
    """The Bayer split applied to a demosaiced frame *creates* the 2x2
    pattern that this function exists to remove."""
    flat = _bgr_flat(800, 2400, 1700)
    flat[..., 1] *= np.linspace(0.6, 1.0, 32, dtype=np.float32)[None, :]
    out = F.normalise_flat(flat)
    for c in range(3):
        plane = out[..., c]
        assert plane.mean() == pytest.approx(1.0, rel=1e-4)
        # Neighbouring pixels within a channel differ only by the gradient
        # that was put there, never by a phase.
        assert abs(float(plane[0, 8] - plane[1, 8])) < 1e-5


def test_white_balance_reads_the_channels_of_a_colour_flat():
    """It used to average all three at every Bayer phase, so every gain came
    back at 1.0 -- a white balance that reported success and did nothing."""
    r, g, b = F.white_balance_from_flat(_bgr_flat(800, 2400, 1700))
    assert g == 1.0
    assert r == pytest.approx(2400 / 1700, rel=1e-3)
    assert b == pytest.approx(2400 / 800, rel=1e-3)


def test_defect_map_records_a_colour_hot_pixel_once():
    """`nonzero` on a 3D frame returns three index arrays. Keeping the first
    two filed one bad photosite as three defects."""
    dark = np.full((64, 64, 3), 1.0, np.float32)
    dark[10, 10, :] = 400.0                  # smeared across all channels
    dark[20, 20, 1] = 900.0                  # and one that is only green
    found = F.defect_map(dark)
    assert [tuple(p) for p in found] == [(10, 10), (20, 20)]


def test_calibration_repairs_a_colour_frame_without_raising():
    """`h, w = frame.shape` raised on every 3-channel capture that had a
    defect map stored, which is a crash in the capture path."""
    raw = np.full((32, 32, 3), 500.0, np.float32)
    raw[4, 4, :] = 4000.0
    dark = np.full((32, 32, 3), 5.0, np.float32)
    out = F.calibrate(raw, dark=dark, flat=_bgr_flat(800, 2400, 1700),
                      defects=np.array([[4, 4]], np.int32))
    assert out.shape == raw.shape
    # Repaired to its neighbours, in every channel, rather than left at
    # eight times the surrounding field.
    assert out[4, 4] == pytest.approx(out[0, 0], rel=1e-3)


#: The shape the live pipeline actually hands the detector: a quarter of
#: the preview in each axis. The tests used to call it at full preview
#: size, which is a path production never takes. (Measured: the extra
#: downsample moves the pooled maximum by under half a percent and has
#: never changed a verdict -- so this is about testing what runs, not a
#: defect that was hiding there.)
_PREVIEW = (1216 // 4, 1824 // 4)


def _slide(*, specks=(), dust=0, rng=None) -> np.ndarray:
    """Empty glass with optical shading, plus whatever is on it.

    `specks` is (radius, level) pairs at preview scale; `dust` is a count
    of tiny high-contrast specks, which must *not* veto blankness -- a
    flat is medianed across stage positions precisely so slide debris
    cancels, and sensor dust is the thing a flat exists to correct.
    """
    rng = rng or np.random.default_rng(1)
    h, w = _PREVIEW
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    shade = 1.0 - 0.18 * (((xx - w / 2) / (w / 2)) ** 2
                          + ((yy - h / 2) / (h / 2)) ** 2)
    img = 150.0 * shade + rng.normal(0, 1.4, (h, w))
    for _ in range(dust):
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                   int(rng.integers(1, 2)), float(rng.integers(30, 90)), -1)
    for radius, level in specks:
        cv2.circle(img, (int(rng.integers(radius * 2, w - radius * 2)),
                         int(rng.integers(radius * 2, h - radius * 2))),
                   radius, float(level), -1)
    return np.clip(img, 0, 255).astype(np.uint8)


def test_blank_detector_accepts_empty_glass_with_dust_on_it():
    """Dust is what a flat is *for*. Vetoing on it would mean never
    banking one."""
    d = BlankDetector()
    assert d.looks_blank(_slide()), "a genuinely empty field was rejected"
    assert d.looks_blank(_slide(dust=15)), "dust vetoed an empty field"


def test_blank_detector_rejects_a_subject():
    d = BlankDetector()
    busy = _slide(specks=[(10, 40)] * 4)
    assert not d.looks_blank(busy), "a field full of subject was banked"


def test_blank_detector_sees_a_specimen_smaller_than_its_pooling_window():
    """Pooling dilutes a subject by the ratio of its area to the window's,
    so a single 16-wide window buried anything much smaller than itself.
    One specimen a quarter of that window across used to read as empty
    glass -- and a flat with a specimen in it stamps that specimen's
    inverse onto every frame it ever corrects."""
    d = BlankDetector()
    for seed in range(8):
        rng = np.random.default_rng(seed)
        one = _slide(specks=[(3, 40)], rng=rng)
        assert not d.looks_blank(one), (
            f"seed {seed}: a lone small specimen was banked as empty slide")


def test_flat_bank_requires_distinct_positions():
    """Four frames of the same empty patch do not median away its debris."""
    bank = FlatBank(wanted=4, min_separation=100.0)
    frame = np.zeros((8, 8), np.float32)
    assert bank.offer(frame)
    bank.note_motion(10, 10)
    assert not bank.offer(frame), "banked a second frame of the same patch"
    bank.note_motion(400, 0)
    assert bank.offer(frame)
    assert bank.count == 2


def test_preview_lut_recovers_a_known_transform():
    """Synthesise an ISP that boosts blue 3x and clips, then check the LUT
    finds where each channel saturates."""
    rng = np.random.default_rng(2)
    raw = (rng.random((256, 256)) * 4095).astype(np.uint16)
    gains = {0: 1.0, 1: 1.4, 2: 3.0}      # R, G, B
    isp = np.zeros((256, 256, 3), np.uint8)
    sites = {0: (1, 0), 1: (0, 0), 2: (0, 1)}
    for ch, (dy, dx) in sites.items():
        block = np.clip(raw.astype(np.float32) * gains[ch] / 4095 * 255, 0, 255)
        isp[dy::2, dx::2, 2 - ch] = block[dy::2, dx::2].astype(np.uint8)

    lut = build_lut([(isp, raw)])
    # Blue, boosted 3x, must start pinning at roughly a third of full scale.
    assert lut.saturation[2] < 1800, f"blue saturation {lut.saturation[2]}"
    assert lut.saturation[0] > 3500, f"red saturation {lut.saturation[0]}"
    assert lut.saturation[2] < lut.saturation[1] < lut.saturation[0]
    # Red saturates latest, so it is the channel worth believing.
    assert lut.best_channel == 0


# ---- focus coverage ---------------------------------------------------------

def test_coverage_requires_passing_through_focus():
    """Reaching focus is not the same as passing it. Stopping at the peak
    means you may not hold the best slice, and coverage must say so."""
    from darlaston.live.coverage import FocusCoverage

    def field(sharpness):
        f = np.zeros((32, 32), np.float32)
        f[8:24, 8:24] = sharpness          # a subject in the middle
        return f

    rising = FocusCoverage()
    for v in (1.0, 4.0, 9.0, 14.0):        # up to the peak, then stop
        rising.update(field(v))
    assert rising.fraction < 0.05, "counted as covered without passing through"

    through = FocusCoverage()
    for v in (1.0, 4.0, 9.0, 14.0, 9.0, 4.0, 1.0):
        through.update(field(v))
    assert through.fraction > 0.95, "a full pass did not register"


def test_coverage_ignores_empty_slide():
    """On darkfield most of the frame is empty and will never be sharp. If it
    counted, coverage could never reach 100% and the number would be useless."""
    from darlaston.live.coverage import FocusCoverage

    cov = FocusCoverage()
    for v in (1.0, 8.0, 20.0, 8.0, 1.0):
        f = np.zeros((64, 64), np.float32)
        f[28:36, 28:36] = v                # 1.5% of the frame has anything
        cov.update(f)
    assert cov.fraction > 0.95, f"empty field dragged coverage to {cov.fraction}"


def test_coverage_tracks_regions_independently():
    """The case it exists for: a tilted plane, where regions pass through
    focus at different Z and coverage should climb rather than jump."""
    from darlaston.live.coverage import FocusCoverage

    cov = FocusCoverage()
    readings = []
    # Two halves that peak at different times.
    for step in range(9):
        f = np.zeros((32, 32), np.float32)
        f[:, :16] = 12.0 - abs(step - 2) * 5.0
        f[:, 16:] = 12.0 - abs(step - 6) * 5.0
        cov.update(np.maximum(f, 0.1))
        readings.append(cov.fraction)

    # The percentage alone lies here: at step 3 the second half is still below
    # the structure floor, so coverage reads 100% having seen half the frame.
    assert readings[-1] > 0.95, f"did not finish: {readings[-1]}"
    assert min(readings[4:8]) < 0.75, "the second half never joined the count"


def test_coverage_is_not_complete_while_structure_is_still_appearing():
    """Reading 100% and then falling back to 50% would destroy any trust in
    this as a stop signal, so completion also requires a settled denominator."""
    from darlaston.live.coverage import FocusCoverage

    cov = FocusCoverage()
    for step in range(4):                  # only the left half has appeared
        f = np.zeros((32, 32), np.float32)
        f[:, :16] = 12.0 - abs(step - 1) * 6.0
        f[:, 16:] = 0.1
        cov.update(np.maximum(f, 0.1))
    assert cov.fraction >= 0.999, "the left half should read as fully passed"
    assert not cov.complete, "claimed complete while half the frame was unseen"


def _field(count, radius, seed=7, shading=True):
    """A lit field with `count` specimens on it."""
    import cv2
    import numpy as np

    h, w = 1216, 1824
    rng = np.random.default_rng(seed)
    field = np.full((h, w), 170.0)
    if shading:                        # real optics are brighter in the middle
        yy, xx = np.mgrid[0:h, 0:w]
        field *= 1.0 - 0.22 * ((xx - w / 2) ** 2 / (w / 2) ** 2
                               + (yy - h / 2) ** 2 / (h / 2) ** 2)
    yy, xx = np.ogrid[:h, :w]
    for _ in range(count):
        cx = rng.integers(100, w - 100)
        cy = rng.integers(100, h - 100)
        field[(xx - cx) ** 2 + (yy - cy) ** 2 < radius * radius] *= 0.45
    return np.clip(cv2.GaussianBlur(field, (0, 0), 2.0), 0, 255).astype("uint8")


def test_a_few_specimens_are_not_an_empty_field():
    """The frame-wide mean dilutes sparse structure into nothing, and a
    mounted arrangement is mostly empty ground with specimens on it.

    Measured before the patch test existed: four obvious diatoms covering
    0.9% of the frame averaged out to 0.0084 against a 0.012 limit and were
    declared empty slide. A flat banked from that stamps the specimen's
    inverse onto every frame it ever corrects.
    """
    from darlaston.live.blank import BlankDetector

    detector = BlankDetector()
    assert detector.looks_blank(_field(0, 0)), "empty glass is empty"
    # Dust and debris are on every real slide and must not veto a flat.
    assert detector.looks_blank(_field(6, 4)), "dust is not a specimen"

    for count, radius in ((1, 30), (2, 35), (4, 40), (8, 45), (20, 50)):
        assert not detector.looks_blank(_field(count, radius)), (
            f"{count} specimens of radius {radius} read as empty slide")


def test_a_rejected_grab_still_costs_its_turn():
    """The grab is what stalls the preview, and it has already happened by
    the time the bank has an opinion about the frame.

    Charging the interval only on success meant a rejected frame left the
    clock untouched, so the next frame qualified again -- park on ground the
    bank already holds and it grabs back to back for ever.
    """
    import threading
    import time
    import types

    import numpy as np

    from darlaston.calib.opportunist import Opportunist

    grabs = []

    class Grab:
        def copy(self):
            return np.zeros((8, 8), np.uint16)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Backend:
        def grab_raw(self):
            grabs.append(time.time())
            return Grab()

    opportunist = Opportunist(types.SimpleNamespace(backend=Backend()))
    opportunist.enabled = True
    # A bank that refuses everything, which is what parking on ground it
    # already holds looks like.
    opportunist.bank.offer = lambda _raw: False

    signals = types.SimpleNamespace(xy_offset=(0.0, 0.0), looks_blank=True,
                                    settled=True)
    for _ in range(40):
        opportunist.observe(signals)
        time.sleep(0.005)
    for thread in threading.enumerate():
        if thread.name == "bank-flat":
            thread.join(timeout=5)

    assert len(grabs) <= 1, (
        f"{len(grabs)} raw grabs in 0.2 s; each one stalls the preview for "
        f"over a second")


def test_blank_fields_are_only_collected_when_asked_for(qapp):
    """Each frame freezes the preview for about a second, and a flat is only
    valid at the illumination it was shot under -- which nothing here can
    read. The operator saying "now" is the only reliable signal there is."""
    import types

    from darlaston.calib.opportunist import Opportunist
    from darlaston.ui.calib_ui import CalibrationPanel

    opportunist = Opportunist(types.SimpleNamespace(backend=object()))
    assert not opportunist.enabled, "collecting on by default"

    signals = types.SimpleNamespace(xy_offset=(0.0, 0.0), looks_blank=True,
                                    settled=True)
    assert not opportunist._should_grab(signals), \
        "a blank, settled field grabbed without being asked"
    opportunist.enabled = True
    assert opportunist._should_grab(signals)

    # The row is one control with three jobs, and it has to say which.
    panel = CalibrationPanel()
    asked = []
    built = []
    panel.collect_flat.connect(asked.append)
    panel.build_flat.connect(lambda: built.append(True))

    panel.set_status({"flat": False}, banked=0, collecting=False)
    assert panel.flat.button.text() == "Collect"
    panel.flat.button.click()
    assert asked == [True]

    panel.set_status({"flat": False}, banked=2, collecting=True)
    assert panel.flat.button.text() == "Stop"
    panel.flat.button.click()
    assert asked == [True, False]

    panel.set_status({"flat": False}, banked=4, collecting=False)
    assert panel.flat.button.text() == "Build"
    panel.flat.button.click()
    assert built == [True], "Build did not build"


def test_collecting_stops_itself_once_there_are_enough(qapp):
    """A mode with a cost should not stay on after it is done."""
    import types

    import numpy as np

    from darlaston.calib.opportunist import Opportunist

    opportunist = _collector(_raw_field(), wanted=2)
    opportunist.enabled = True
    for step in range(2):
        opportunist.bank.note_motion(500.0, 0.0)      # a fresh patch each time
        # observe() takes this before spawning the grab thread; called
        # directly, the test stands in for it.
        opportunist._grabbing.acquire()
        opportunist._grab()
    assert opportunist.bank.complete
    assert not opportunist.enabled, "left collecting after the bank filled"


def _raw_field(specimen: bool = False) -> np.ndarray:
    """A 12-bit raw blank field, optionally with a subject on it."""
    rng = np.random.default_rng(3)
    img = np.full((512, 512), 2000.0, np.float32) + rng.normal(0, 8, (512, 512))
    if specimen:
        cv2.circle(img, (300, 220), 24, 700.0, -1)
    return np.clip(img, 0, 4095).astype(np.uint16)


def _collector(raw, wanted=2):
    """An Opportunist whose camera hands back exactly `raw`."""
    import types

    from darlaston.calib.opportunist import Opportunist

    class Grab:
        def copy(self):
            return raw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    backend = types.SimpleNamespace(
        grab_raw=lambda: Grab(),
        info=types.SimpleNamespace(max_bit_depth=12))
    return Opportunist(types.SimpleNamespace(backend=backend), wanted=wanted)


def test_a_raw_frame_with_a_subject_on_it_is_never_banked():
    """Blankness was decided once, on a quarter-size preview, and that one
    verdict gated the outcome this module calls undetectable: a specimen
    baked into the flat, stamping its inverse on every frame thereafter.
    The frame that is actually about to be banked gets asked too."""
    opportunist = _collector(_raw_field(specimen=True))
    opportunist.enabled = True
    opportunist.bank.note_motion(500.0, 0.0)
    opportunist._grabbing.acquire()
    opportunist._grab()
    assert opportunist.bank.count == 0, "banked a flat with a specimen in it"


def test_a_raw_frame_of_empty_glass_still_gets_banked():
    """The guard above must not be so strict that nothing is ever banked."""
    opportunist = _collector(_raw_field())
    opportunist.enabled = True
    opportunist.bank.note_motion(500.0, 0.0)
    opportunist._grabbing.acquire()
    opportunist._grab()
    assert opportunist.bank.count == 1, "refused a genuinely empty field"
