"""The tracking-drift ritual: the fit, and the dialog that wears it.

The physics -- that the fitted constant flattens a simulated rolling
shutter through the real pipeline -- is pinned by
spike/tracking/cycle_drift_probe.py, which takes minutes and stays out
of this suite on purpose. Here: the arithmetic, and the flow.
"""
import types

import numpy as np
import pytest

from darlaston.live.driftcal import DriftCalibration


def _pass(cal, true_readout_us, speed=30.0, frames=120, fps=30.0):
    """One closed pass whose drift follows the model exactly."""
    cal.begin_pass(0.0)
    x = 0.0
    for i in range(frames):
        d = speed if i < frames // 2 else -speed
        cal.observe(d * fps, d)
        x += d * fps * d
    drift = true_readout_us / 1e6 / cal.h * x
    return cal.mark(drift)


def test_the_fit_recovers_a_known_readout():
    cal = DriftCalibration(1216)
    for _ in range(2):
        _pass(cal, true_readout_us=16000.0)
    assert cal.fit() == pytest.approx(16000.0, rel=1e-6)

    # Sign carries the readout direction -- a camera mounted
    # back-to-front calibrates to the opposite sign, no ceremony.
    flipped = DriftCalibration(1216)
    for _ in range(2):
        _pass(flipped, true_readout_us=-16000.0)
    assert flipped.fit() == pytest.approx(-16000.0, rel=1e-6)


def test_a_pass_without_evidence_cannot_testify():
    cal = DriftCalibration(1216)
    cal.begin_pass(0.0)
    cal.observe(30.0, 1.0)                      # one tiny step
    assert cal.mark(5.0) is None, "no travel, no testimony"
    assert not cal.passes
    assert cal.fit() is None


def test_the_ritual_dialog_walks_the_passes(qapp):
    from darlaston.ui.calib_ui import DriftDialog

    written = []
    pipeline = types.SimpleNamespace(set_readout=written.append)
    camera = types.SimpleNamespace(readout_us=0.0)
    dlg = DriftDialog(pipeline, camera)
    dlg.set_frame_height(1216)

    textured = np.random.default_rng(1).integers(
        0, 255, (152, 228), np.uint8)

    def sig(pos_y, tracking=True):
        return types.SimpleNamespace(
            stage_pos=(0.0, pos_y), stage_tracking=tracking,
            track_small=textured, stats={"analysed_fps": 30.0})

    def run_pass(drift):
        y = dlg._pos_y
        for i in range(60):
            y += 30.0
            dlg.observe(sig(y))
        for i in range(60):
            y -= 30.0
            dlg.observe(sig(y))
        dlg.observe(sig(y + drift))
        dlg._pressed()                          # Centred

    # Blank ground refuses to arm; textured, tracked ground arms.
    dlg.observe(types.SimpleNamespace(
        stage_pos=(0.0, 0.0), stage_tracking=True,
        track_small=np.full((152, 228), 200, np.uint8),
        stats={"analysed_fps": 30.0}))
    assert not dlg.action.isEnabled(), "blank glass cannot testify"
    dlg.observe(sig(0.0))
    assert dlg.action.isEnabled()

    dlg._pressed()                              # Start
    run_pass(-12.0)
    run_pass(-12.0)
    # The fit went live for the verification pass.
    assert written and written[-1] != 0.0
    fitted = written[-1]
    run_pass(-1.0)                              # verified: nearly flat
    assert "px" in dlg.result.text()

    dlg._save()
    assert dlg.saved and camera.readout_us == pytest.approx(fitted)


def test_walking_away_restores_the_profiles_truth(qapp):
    from darlaston.ui.calib_ui import DriftDialog

    written = []
    pipeline = types.SimpleNamespace(set_readout=written.append)
    camera = types.SimpleNamespace(readout_us=4321.0)
    dlg = DriftDialog(pipeline, camera)
    dlg.reject()
    assert written[-1] == 4321.0, \
        "closing the ritual must restore the calibrated truth"
