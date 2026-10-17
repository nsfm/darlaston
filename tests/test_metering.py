"""A camera that meters itself: no exposure time to set, a finished
picture handed to us, and perhaps an automatic of its own to switch."""
from __future__ import annotations

import dataclasses
import time

import pytest


class MeteringCamera:
    """A mock that reports no exposure control, like the X5FCAM and
    every webcam: it meters, we bias. Wraps the synthetic camera so the
    pipeline has frames."""

    def __init__(self, switch: bool = True) -> None:
        from darlaston.camera.mock import MockCamera
        self._inner = MockCamera(fps=30.0)
        self._switch = switch
        self.auto_writes: list[bool] = []

    def open(self):
        info = self._inner.open()
        self._info = dataclasses.replace(
            info, exposure_range_us=None, auto_exposure_switch=self._switch)
        return self._info

    @property
    def info(self):
        return self._info

    def set_auto_exposure(self, on: bool) -> bool:
        if not self._switch:
            return False
        self.auto_writes.append(bool(on))
        return True

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _window(tmp_path, monkeypatch, camera):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.ui.main import MainWindow
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance()
    win = MainWindow(lambda: camera)
    win.show()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not win.session.status.is_live:
        app.processEvents(); time.sleep(0.02)
    for _ in range(10):
        app.processEvents()
    assert win.session.status.is_live
    return win


def test_the_record_says_who_meters():
    from darlaston.camera.base import CameraInfo, Resolution

    base = dict(model="m", serial="s", resolutions=(Resolution(0, 640, 480, 0.0),),
                max_bit_depth=8, bayer_pattern="", gain_range_pct=(100, 155))
    assert CameraInfo(exposure_range_us=None, **base).meters_itself
    assert not CameraInfo(exposure_range_us=(100, 1000), **base).meters_itself
    assert not CameraInfo(exposure_range_us=None, **base).auto_exposure_switch


def test_the_rail_offers_what_a_metering_camera_has_and_nothing_else(qapp, tmp_path, monkeypatch):
    """No time bar, since there is no time; gain stays, since it biases
    the camera's own metering; Automatic is the camera's switch and
    'gain only' is gone, since there is nothing to pin."""
    cam = MeteringCamera(switch=True)
    win = _window(tmp_path, monkeypatch, cam)
    try:
        assert win.exposure.isHidden(), "a slider over a control that does not exist"
        assert not win.gain.isHidden() and win.gain.isEnabled()
        assert not win.auto_exposure.isHidden()
        assert win.auto_exposure_lock.isHidden()
        assert "meters itself" in win.auto_exposure.toolTip()
        # This camera's remembered switch position went to the camera at
        # open: on, for a camera never seen before.
        assert cam.auto_writes and cam.auto_writes[-1] is True
        assert win.auto_exposure.isChecked()
        # The switch is the camera's, and it is remembered with the camera
        # rather than with the global switch that runs our loop.
        win.auto_exposure.setChecked(False)
        assert cam.auto_writes[-1] is False
        assert win.setup.camera.camera_auto_exposure is False
        assert win.settings.auto_exposure, "the loop's own switch is untouched"
        # And no loop of ours moves the gain, however dark the picture.
        import numpy as np
        gain = win.gain.value()
        win.settings.auto_exposure = True

        class Signals:
            levels_seq = 99
            histogram = np.array([1000] * 20 + [0] * 236, dtype=np.float64)
        win._auto_expose_guarded(Signals())
        assert win.gain.value() == gain, "our loop ran on a camera that meters itself"
        assert win.settings.auto_exposure, "or it ran and failed, and turned itself off"
    finally:
        win.shutdown()


def test_the_hold_reaches_the_camera_s_own_automatic(qapp, tmp_path, monkeypatch):
    """A stack needs matching frames whichever engine meters. The row
    said 'paused'; now the camera is handed the switch off for the run."""
    cam = MeteringCamera(switch=True)
    win = _window(tmp_path, monkeypatch, cam)
    try:
        assert cam.auto_writes[-1] is True
        monkeypatch.setattr(win, "_auto_exposure_allowed", lambda: False)
        win._refresh_auto_exposure_hold()
        assert cam.auto_writes[-1] is False, "held, so the camera's automatic is off"
        assert not win.auto_exposure.isEnabled()
        monkeypatch.setattr(win, "_auto_exposure_allowed", lambda: True)
        win._refresh_auto_exposure_hold()
        assert cam.auto_writes[-1] is True, "and back when the hold ends"
    finally:
        win.shutdown()


def test_the_switch_survives_a_mode_change_and_a_reconnect(qapp, tmp_path, monkeypatch):
    """The position lived only in the window before; a cable that
    wobbled, or a new preview size, left the camera in manual under a
    button that still said Automatic."""
    cam = MeteringCamera(switch=True)
    win = _window(tmp_path, monkeypatch, cam)
    try:
        win.auto_exposure.setChecked(False)
        assert cam.auto_writes[-1] is False
        before = len(cam.auto_writes)
        other = 0 if win.session.preview_resolution != 0 else 1
        assert win.session.set_preview_resolution(other)
        assert len(cam.auto_writes) > before and cam.auto_writes[-1] is False
        # No exposure reached the camera either: it has no time to set.
        assert not getattr(cam._inner, "exposure_writes", None)
        before = len(cam.auto_writes)
        win.session._restore_controls(cam)
        assert cam.auto_writes[-1] is False and len(cam.auto_writes) == before + 1
    finally:
        win.shutdown()


def test_a_metering_camera_without_a_switch_offers_no_automatic(qapp, tmp_path, monkeypatch):
    cam = MeteringCamera(switch=False)
    win = _window(tmp_path, monkeypatch, cam)
    try:
        assert win.exposure.isHidden()
        assert win.auto_exposure.isHidden(), "a switch over a capability that does not exist"
        assert win.auto_exposure_lock.isHidden()
        assert not cam.auto_writes
    finally:
        win.shutdown()


def test_the_liveness_pass_no_longer_re_enables_a_missing_control(qapp, tmp_path, monkeypatch):
    """The bug the first tester saw: the fit disabled the bar, the
    liveness greying re-enabled it, and a live slider drove nothing."""
    cam = MeteringCamera(switch=True)
    win = _window(tmp_path, monkeypatch, cam)
    try:
        win._on_status(win.session.status)
        assert win.exposure.isHidden()
        assert not win.gain.isHidden() and win.gain.isEnabled()
    finally:
        win.shutdown()


def test_a_v4l2_camera_with_the_switch_and_no_time_is_left_metering(monkeypatch):
    """Settling used to force every camera with an auto-exposure menu
    to manual. One with no exposure time to take over was then left at
    whatever it last chose, with nothing of ours in its place."""
    from darlaston.camera import v4l2

    class Cap:
        def __init__(self): self.sets = []
        def set(self, prop, value): self.sets.append((prop, value)); return True
        def isOpened(self): return True

    cam = v4l2.V4L2Backend.__new__(v4l2.V4L2Backend)
    cam._cap = Cap()
    cam._controls = {"Auto Exposure": {"id": 1, "min": 0, "max": 3}}
    monkeypatch.setattr(cam, "_warm_up", lambda: None)
    cam._settle()
    import cv2
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 1) not in cam._cap.sets
    cam._controls["Exposure Time, Absolute"] = {"id": 2, "min": 1, "max": 5000}
    cam._settle()
    assert (cv2.CAP_PROP_AUTO_EXPOSURE, 1) in cam._cap.sets
    cam._cap.sets.clear()
    cam._write_control("auto_exposure", True)
    assert cam._cap.sets == [(cv2.CAP_PROP_AUTO_EXPOSURE, 3)]
    cam._write_control("auto_exposure", False)
    assert cam._cap.sets[-1] == (cv2.CAP_PROP_AUTO_EXPOSURE, 1)
