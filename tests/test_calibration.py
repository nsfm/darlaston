"""Calibration maths.

Worth testing carefully: an error here is silent and corrupts every image that
passes through, in ways that look like the microscope's fault.
"""
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


def test_bayer_normalise_removes_the_checkerboard():
    """A single scalar norm would leave a 2x2 pattern, because the phases
    differ in sensitivity by about three times on this sensor."""
    flat = np.zeros((32, 32), np.float32)
    flat[0::2, 0::2] = 2400      # G
    flat[0::2, 1::2] = 800       # B
    flat[1::2, 0::2] = 1700      # R
    flat[1::2, 1::2] = 2400      # G
    out = F.bayer_normalise(flat)
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


def test_blank_detector_rejects_a_subject():
    rng = np.random.default_rng(1)
    blank = (rng.normal(140, 1.5, (512, 512))).clip(0, 255).astype(np.uint8)
    busy = blank.copy()
    for cx in range(60, 500, 90):        # some diatoms
        busy[cx:cx + 40, 100:400] = 40
    d = BlankDetector()
    assert d.looks_blank(blank), "a genuinely empty field was rejected"
    assert not d.looks_blank(busy), "a field full of subject was banked"


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
