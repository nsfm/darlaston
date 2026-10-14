"""The Windows UVC backend, on a machine with no Windows.

Everything below the COM calls is plain Python and is tested here;
everything at the COM calls is tested on a Windows runner against a
virtual camera (`test_wincam.py`). The seam between the two is
`enumerate_cameras`, which hands back dictionaries, and `_Controls`,
which writes a property; both are faked here.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time

import numpy as np
import pytest

from darlaston.camera import dshow


def _found(**over) -> dict:
    base = {
        "index": 0, "name": "X5CAM4K8MPA", "display":
            r"@device:pnp:\\?\usb#vid_0547&pid_ab10&mi_00#7&2a1b3c4d&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global",
        "path": r"\\?\usb#vid_0547&pid_ab10&mi_00#7&2a1b3c4d&0&0000#{65e8773d-8f56-11d0-a3b9-00a0c9223196}\global",
        "vid": "0547", "pid": "ab10",
        "key": r"\\?\usb#vid_0547&pid_ab10&mi_00#7&2a1b3c4d&0&0000",
        "formats": ["MJPG", "YUY2"], "sizes": [(3840, 2160), (1920, 1080)],
        "controls": [
            {"name": "Exposure", "which": "camera", "prop": 4, "min": -13,
             "max": 1, "step": 1, "default": -6, "flags": 3},
            {"name": "Focus", "which": "camera", "prop": 6, "min": 0,
             "max": 854, "step": 1, "default": 400, "flags": 3},
            {"name": "Gain", "which": "amp", "prop": 9, "min": 0, "max": 100,
             "step": 1, "default": 0, "flags": 2},
        ],
        "raw": [], "usable": ["YUY2", "MJPG"],
    }
    base.update(over)
    base["fingerprint"] = dshow.fingerprint(base)
    return base


class _FakeControls:
    """Stands in for the COM handle: records every write."""
    instances: list = []

    def __init__(self, display):
        self.display = display
        self.writes: list[tuple] = []
        self.values = {("camera", 4): -6, ("camera", 6): 400, ("amp", 9): 0}
        self.closed = False
        _FakeControls.instances.append(self)

    def set(self, which, prop, value, auto=False):
        self.writes.append((which, prop, int(value), auto))
        self.values[(which, prop)] = int(value)
        return True

    def get(self, which, prop):
        return self.values.get((which, prop))

    def close(self):
        self.closed = True


class _FakeCapture:
    def __init__(self, index, api=None):
        self.index, self.api = index, api
        self._open = True
        self.props: dict = {}

    def isOpened(self):
        return self._open

    def set(self, prop, value):
        self.props[prop] = value
        return True

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def read(self):
        return True, np.full((1080, 1920, 3), 40, np.uint8)

    def grab(self):
        return True

    def release(self):
        self._open = False


@pytest.fixture
def fake(monkeypatch):
    _FakeControls.instances.clear()
    monkeypatch.setattr(dshow, "_Controls", _FakeControls)
    monkeypatch.setattr(dshow.cv2, "VideoCapture", _FakeCapture)
    return _FakeControls


def test_the_struct_layout_is_windows_sized_everywhere():
    """ctypes' c_long is 64 bits on Linux and 32 on Windows; a struct
    spelled with it is the right shape only on the machine that runs it
    and the wrong shape on the machine that tests it. Fixed widths."""
    assert ctypes.sizeof(dshow.GUID) == 16
    assert ctypes.sizeof(dshow.AM_MEDIA_TYPE) == 88 if sys.maxsize > 2**32 else True
    assert str(dshow.CLSID_SystemDeviceEnum) == "62be5d10-60eb-11d0-bd3b-00a0c911ce86"
    assert str(dshow.IID_IAMCameraControl) == "c6e13370-30ac-11d0-a18c-00a0c9118956"


def test_media_subtypes_get_the_names_the_preference_table_knows():
    assert dshow._subtype_name(dshow.MEDIASUBTYPE_RGB24) == "RGB3"
    assert dshow._subtype_name(dshow.MEDIASUBTYPE_ARGB32) == "RGB4"
    assert dshow._subtype_name(
        dshow.GUID.of("32595559-0000-0010-8000-00AA00389B71")) == "YUY2"
    assert dshow._subtype_name(
        dshow.GUID.of("47504A4D-0000-0010-8000-00AA00389B71")) == "MJPG"
    assert dshow._subtype_name(
        dshow.GUID.of("34363248-0000-0010-8000-00AA00389B71")) == "H264"


def test_the_key_is_the_usb_position_and_survives_a_virtual_camera():
    real = dshow._key_from(_found()["path"], "", "X5CAM4K8MPA")
    assert real == r"\\?\usb#vid_0547&pid_ab10&mi_00#7&2a1b3c4d&0&0000"
    assert "{" not in real, "the interface GUID is every camera's; it goes"
    virtual = dshow._key_from(
        "", r"@device:sw:{860BB310-5D01-11D0-BD3B-00A0C911CE86}\{ABC}",
        "Unity Video Capture")
    assert virtual.startswith("@device:sw:"), "no path: the moniker name is the key"
    fake_path = dshow._key_from("foo:bar", virtual.upper(), "Unity Video Capture")
    assert fake_path == virtual, "a software filter's made-up DevicePath is not a position"
    assert dshow._key_from("", "", "Thing") == "name:thing" or \
        dshow._key_from("", "", "Thing") == "name:Thing"


def test_exposure_is_log2_seconds_and_round_trips():
    assert dshow.exposure_to_us(0) == 1_000_000
    assert dshow.exposure_to_us(-13) == 122
    assert dshow.exposure_to_us(1) == 2_000_000
    for step in range(-13, 2):
        assert dshow.us_to_exposure(dshow.exposure_to_us(step)) == step


def test_the_backend_describes_itself_honestly(fake, monkeypatch):
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [_found()])
    cam = dshow.DshowBackend(key=_found()["key"])
    info = cam.open()
    assert info.max_bit_depth == 8 and not info.is_colour and not info.raw_capable
    assert info.resolutions[0].width == 3840
    assert info.exposure_range_us == (122, 2_000_000)
    assert info.gain_range_pct == (100, 200)
    assert info.brand == "ToupTek"
    assert info.serial == _found()["key"]
    assert cam.focus_axis is not None
    assert cam.focus_axis.range().hi == 854
    assert cam.focus_axis.get() == 400, "the position the device reported"
    # Settling took the controls into manual, at their current values.
    writes = fake.instances[0].writes
    assert ("camera", 4, -6, False) in writes
    assert ("camera", 6, 400, False) in writes
    cam.close()
    assert fake.instances[0].closed


def test_a_camera_without_controls_offers_no_sliders(fake, monkeypatch):
    bare = _found(controls=[], name="Unity Video Capture", path="",
                  display=r"@device:sw:{860BB310-5D01-11D0-BD3B-00A0C911CE86}\{ABC}",
                  vid="", pid="")
    bare["key"] = dshow._key_from("", bare["display"], bare["name"])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [bare])
    cam = dshow.DshowBackend(key=bare["key"])
    info = cam.open()
    assert info.exposure_range_us is None and info.gain_range_pct is None
    assert cam.focus_axis is None
    assert info.brand == ""
    assert not fake.instances, "a software filter gets no second handle"
    assert cam._device is None
    cam.close()


def test_formats_are_asked_for_the_way_opencv_dshow_understands(fake, monkeypatch):
    """OpenCV's DirectShow path matches CAP_PROP_FOURCC against each
    subtype's Data1. RGB24 has no four letters, so its Data1 goes; a
    code it does not know stops the device for good; and the format
    must be re-asked after a size change, which rebuilds the graph."""
    rgb = _found(formats=["RGB3", "RGB4"], usable=["RGB3", "RGB4"], controls=[])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [rgb])
    cam = dshow.DshowBackend(key=rgb["key"])
    cam.open()
    cap = cam._cap
    import cv2
    assert cam._wanted is None, "no MJPG offered, so nothing asked at open"
    assert cam.best_capture_format() == "RGB3"
    assert cam._set_format("RGB3") == "RGB3"
    assert cap.props[cv2.CAP_PROP_FOURCC] == float(0xe436eb7d)
    assert cam._set_format("YUY2") == "RGB3", "a format the device lacks is never sent"
    cap.props.clear()
    cam._select(1)
    order = list(cap.props)
    assert order.index(cv2.CAP_PROP_FOURCC) > order.index(cv2.CAP_PROP_FRAME_HEIGHT)
    cam.close()


def test_a_camera_that_will_not_open_leaves_no_handle_behind(fake, monkeypatch):
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [_found()])

    class _Shut(_FakeCapture):
        def __init__(self, *a):
            super().__init__(*a)
            self._open = False
            self.released = False

        def release(self):
            self.released = True

    made = []
    monkeypatch.setattr(dshow.cv2, "VideoCapture",
                        lambda *a: made.append(_Shut(*a)) or made[-1])
    from darlaston.camera.errors import CameraBusy

    cam = dshow.DshowBackend(key=_found()["key"])
    with pytest.raises(CameraBusy):
        cam.open()
    assert made[0].released and cam._cap is None


def test_control_writes_land_on_the_reader_thread_in_device_units(fake, monkeypatch):
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [_found()])
    cam = dshow.DshowBackend(key=_found()["key"])
    cam.open()
    device = fake.instances[0]
    device.writes.clear()
    seen = []
    cam.start_stream(lambda f: (seen.append(threading.current_thread().name),
                                f.release()))
    cam.set_exposure(250_000)            # 1/4 s -> log2 = -2
    cam.set_gain(150)                    # +50 on a 0..100 scale
    cam.focus_axis.set(600)
    cam.focus_axis.set(9999)             # clamped to the range
    for _ in range(100):
        if len(device.writes) >= 3:
            break
        time.sleep(0.02)
    cam.stop_stream()
    assert seen and seen[0] == "dshow-camera"
    assert ("camera", 4, -2, False) in device.writes
    assert cam.get_exposure() == 250_000, "a power of two, reported as the camera has it"
    assert ("amp", 9, 50, False) in device.writes
    assert cam.get_gain() == 150, "ours, not DirectShow's device units"
    assert ("camera", 6, 854, False) in device.writes, "coalesced to the last ask"
    assert cam.focus_axis.get() == 854
    cam.close()


def test_autofocus_is_handed_over_and_taken_back(fake, monkeypatch):
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [_found()])
    cam = dshow.DshowBackend(key=_found()["key"])
    cam.open()
    device = fake.instances[0]
    device.writes.clear()
    cam.focus_axis.set_auto(True)
    assert device.writes[-1][0:2] == ("camera", 6) and device.writes[-1][3] is True
    cam.focus_axis.set_auto(False)
    assert device.writes[-1][3] is False
    cam.close()


def test_a_moved_cable_is_found_by_fingerprint_when_unique(fake, monkeypatch):
    moved = _found(path=_found()["path"].replace("7&2a1b3c4d&0&0000",
                                                 "7&deadbeef&0&0003"))
    moved["key"] = dshow._key_from(moved["path"], "", moved["name"])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [moved])
    cam = dshow.DshowBackend(key=_found()["key"], fingerprint=_found()["fingerprint"])
    info = cam.open()
    assert info.serial == moved["key"]
    cam.close()


def test_two_of_the_same_camera_on_new_ports_is_a_question_not_a_guess(fake, monkeypatch):
    a = _found(path=_found()["path"].replace("2a1b3c4d", "aaaaaaaa"))
    b = _found(path=_found()["path"].replace("2a1b3c4d", "bbbbbbbb"))
    for d in (a, b):
        d["key"] = dshow._key_from(d["path"], "", d["name"])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [a, b])
    from darlaston.camera.errors import NoCameraFound

    cam = dshow.DshowBackend(key="gone", fingerprint=_found()["fingerprint"])
    with pytest.raises(NoCameraFound):
        cam.open()


def test_discovery_lists_them_on_windows_and_only_there(monkeypatch):
    from darlaston.camera import discovery

    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [_found()])
    monkeypatch.setattr(discovery.sys, "platform", "linux")
    assert discovery._dshow() == []
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    got = discovery._dshow()
    assert len(got) == 1
    cam = got[0]
    assert cam.kind == "dshow" and cam.key.startswith("dshow:")
    assert cam.width == 3840 and cam.uncompressed and cam.controls == 3
    backend = discovery.backend_for(cam)
    assert isinstance(backend, dshow.DshowBackend)
    assert discovery.presence_for(cam) is not None


def test_enumeration_is_silent_off_windows():
    if sys.platform.startswith("win"):
        pytest.skip("this is the no-Windows half")
    assert dshow.enumerate_cameras() == []
    assert dshow.present("anything") is True, "unknown has to mean try"


# ---- describing in a child, so a bad filter takes only the child ---------------

def _on_windows(monkeypatch):
    monkeypatch.setattr(dshow.sys, "platform", "win32")
    monkeypatch.setattr(dshow, "_probe_cache", None)


def _shallow(i, name, path=""):
    import json
    return json.dumps({"index": i, "name": name, "path": path, "display": f"@device:pnp:{i}",
                       "vid": "", "pid": "", "key": f"k{i}", "formats": [], "sizes": [],
                       "controls": [], "raw": [], "described": False})


def _deep(i, name, path=""):
    import json
    d = _found(index=i, name=name, path=path)
    d["described"] = True
    return json.dumps(d)


class _Child:
    """A probe child that dies describing one device until told to skip it."""

    def __init__(self, evil: int, names):
        self.evil, self.names = evil, names
        self.runs: list[list[str]] = []

    def __call__(self, cmd, **kw):
        import subprocess
        self.runs.append(list(cmd))
        skip = set()
        if "--probe-skip" in cmd:
            skip = {int(s) for s in cmd[cmd.index("--probe-skip") + 1].split(",")}
        out = [_shallow(i, n) for i, n in enumerate(self.names)]
        err = ["probing: " + ", ".join(self.names)]
        for i, n in enumerate(self.names):
            if i in skip:
                out.append(_shallow(i, n))
                continue
            err.append(f"describing: {i} {n}")
            if i == self.evil:
                return subprocess.CompletedProcess(cmd, -1073741819, stdout="\n".join(out) + "\n",
                                                   stderr="\n".join(err) + "\nFatal Python error: Segmentation fault\n")
            out.append(_deep(i, n))
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(out) + "\n", stderr="\n".join(err))


def test_a_child_that_answers_is_believed(monkeypatch):
    import subprocess

    _on_windows(monkeypatch)
    child = _Child(evil=-1, names=["X5CAM4K8MPA"])
    monkeypatch.setattr(subprocess, "run", child)
    got = dshow.enumerate_cameras()
    assert len(got) == 1 and got[0]["described"] and got[0]["sizes"][0] == (3840, 2160)
    assert all(isinstance(size, tuple) for size in got[0]["sizes"])


def test_a_device_that_kills_the_child_is_skipped_and_named(monkeypatch, caplog):
    """The first Windows machine: nine DirectShow devices, one of whose
    filters faults when bound. Everything before it stays described,
    it is named in the log and listed by name only, and the child runs
    again for the rest."""
    import logging
    import subprocess

    _on_windows(monkeypatch)
    names = ["XSplit Broadcaster", "Evil Virtual Camera", "X5FCAM4K8MPA", "Logi Capture"]
    child = _Child(evil=1, names=names)
    monkeypatch.setattr(subprocess, "run", child)
    with caplog.at_level(logging.WARNING, logger="darlaston.camera.dshow"):
        got = dshow.enumerate_cameras()
    assert [d["name"] for d in got] == names
    assert [d["described"] for d in got] == [True, False, True, True]
    assert got[2]["sizes"][0] == (3840, 2160), "the microscope, after the culprit, is described"
    assert len(child.runs) == 2 and child.runs[1][-2:] == ["--probe-skip", "1"]
    assert "Evil Virtual Camera" in caplog.text and "took the probe down" in caplog.text


def test_a_child_that_dies_before_naming_anything_leaves_the_names(monkeypatch):
    import subprocess

    _on_windows(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], -1073741819, stdout="", stderr="nothing"))
    names = [{"index": 0, "name": "X5CAM4K8MPA", "path": "", "display": "@device:pnp:x",
              "vid": "0547", "pid": "ab10", "key": "k", "formats": [], "sizes": [],
              "controls": [], "raw": []}]
    monkeypatch.setattr(dshow, "_walk_here", lambda describe: names)
    got = dshow.enumerate_cameras()
    assert len(got) == 1 and got[0]["name"] == "X5CAM4K8MPA"
    assert got[0]["usable"] == [] and got[0]["fingerprint"] == "" and not got[0]["described"]


def test_a_child_that_hangs_is_not_waited_for(monkeypatch):
    import subprocess

    _on_windows(monkeypatch)

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(a[0], k.get("timeout", 0))
    monkeypatch.setattr(subprocess, "run", hang)
    monkeypatch.setattr(dshow, "_walk_here", lambda describe: [])
    assert dshow.enumerate_cameras() == []


def test_the_answer_is_kept_for_a_few_seconds(monkeypatch):
    """Startup asks three times in two seconds; the child runs once."""
    import subprocess

    _on_windows(monkeypatch)
    child = _Child(evil=-1, names=["X5CAM4K8MPA"])
    monkeypatch.setattr(subprocess, "run", child)
    first = dshow.enumerate_cameras()
    second = dshow.enumerate_cameras()
    assert first == second and len(child.runs) == 1


def test_the_microscope_outranks_a_streaming_tool_whatever_the_numbers_say(monkeypatch):
    """The first Windows machine, as its log read: XSplit Broadcaster
    described at 1920x1080 uncompressed, the microscope beside it, and
    XSplit won the first open. A software filter is never the camera on
    the microscope; a microscopy vendor's device always is."""
    from darlaston.camera import discovery

    xsplit = _found(index=0, name="XSplit Broadcaster", vid="", pid="",
                    path=r"\\?\root#image#0000#{x}\global",
                    formats=["NV12", "YUY2"], usable=["NV12", "YUY2"],
                    sizes=[(1920, 1080), (1280, 720)], controls=[])
    xsplit["key"] = "a"
    webcam = _found(index=1, name="Insta360 Link", vid="2e1a", pid="4c01",
                    path=r"\\?\usb#vid_2e1a&pid_4c01&mi_00#7&2&0&0000#{x}\global",
                    sizes=[(3840, 2160)], controls=[])
    webcam["key"] = "b"
    scope = _found(index=2, name="X5FCAM4K8MPA", formats=["MJPG"], usable=["MJPG"],
                   sizes=[(1920, 1080)])
    scope["key"] = "c"
    undescribed = dict(_found(index=3, name="Camera Plug-in", vid="", pid="", path="",
                              formats=[], usable=[], sizes=[], controls=[]),
                       described=False)
    undescribed["key"] = "d"
    for d in (xsplit, webcam, scope):
        d["described"] = True
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [xsplit, webcam, scope, undescribed])
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    ranked = discovery.look()
    assert [c.name for c in ranked] == ["X5FCAM4K8MPA", "Insta360 Link",
                                        "XSplit Broadcaster", "Camera Plug-in"]
    assert ranked[0].microscope and ranked[0].physical
    assert ranked[1].physical and not ranked[1].microscope
    assert not ranked[2].physical, "a root-enumerated device is a software filter"
    assert ranked[3].detail == "undescribed"


def test_a_known_culprit_is_skipped_on_sight_and_a_new_one_is_reported(monkeypatch):
    import subprocess

    _on_windows(monkeypatch)
    names = ["XSplit Broadcaster", "Camera Plug-in", "X5FCAM4K8MPA", "Evil"]
    child = _Child(evil=3, names=names)
    monkeypatch.setattr(subprocess, "run", child)
    shallow = [{"index": i, "name": n} for i, n in enumerate(names)]
    monkeypatch.setattr(dshow, "_walk_here", lambda describe: shallow)
    monkeypatch.setattr(dshow, "skip_names", {"Camera Plug-in"})
    told = []
    monkeypatch.setattr(dshow, "on_culprit", told.append)
    got = dshow.enumerate_cameras()
    assert child.runs[0][-2:] == ["--probe-skip", "1"], "known on sight, no crash for it"
    assert child.runs[1][-2:] == ["--probe-skip", "1,3"], "the new one joins it"
    assert told == ["Evil"]
    assert dshow.skip_names == {"Camera Plug-in", "Evil"}
    assert [d["described"] for d in got] == [True, False, True, False]


def test_the_probe_command_knows_whether_it_is_frozen(monkeypatch):
    monkeypatch.setattr(dshow.sys, "frozen", True, raising=False)
    assert dshow._probe_command() == [dshow.sys.executable, "--probe-dshow"]
    assert dshow._probe_command({3, 1})[-2:] == ["--probe-skip", "1,3"]
    monkeypatch.delattr(dshow.sys, "frozen", raising=False)
    assert dshow._probe_command()[1:] == ["-m", "darlaston.ui.main", "--probe-dshow"]


def test_a_hard_fault_has_somewhere_to_write(tmp_path, monkeypatch):
    import faulthandler

    from darlaston import log

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(log, "_started", False)
    log.setup()
    assert faulthandler.is_enabled()
    assert log.crash_path().exists()
    assert "started" in log.crash_path().read_text()


def test_a_touptek_hybrid_keeps_its_motor_behind_backlight_compensation(fake, monkeypatch):
    """The tester, on the X5FCAM4K8MPA: no Focus control at all; the
    stepper answers to Backlight Compensation, 0..854. A backlight
    compensation is 0..1 or 0..2, so the range alone says what it is."""
    found = _found(controls=[
        {"name": "Exposure", "which": "camera", "prop": 4, "min": -13, "max": 1,
         "step": 1, "default": -6, "flags": 3},
        {"name": "Backlight", "which": "amp", "prop": 8, "min": 0, "max": 854,
         "step": 1, "default": 288, "flags": 2},
    ])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [found])
    cam = dshow.DshowBackend(key=found["key"])
    cam.open()
    axis = cam.focus_axis
    assert axis is not None and axis.range().hi == 854 and axis.get() == 288
    assert axis.behind == "Backlight"
    device = fake.instances[0]
    device.writes.clear()
    axis.set(500)
    assert ("amp", 8, 500, False) in device.writes, device.writes
    cam.close()


def test_a_real_backlight_compensation_is_not_a_motor(fake, monkeypatch):
    found = _found(vid="046d", pid="085e", controls=[
        {"name": "Backlight", "which": "amp", "prop": 8, "min": 0, "max": 1,
         "step": 1, "default": 0, "flags": 2},
    ])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [found])
    cam = dshow.DshowBackend(key=found["key"])
    cam.open()
    assert cam.focus_axis is None
    cam.close()
    wide_but_not_touptek = _found(vid="046d", pid="085e", controls=[
        {"name": "Backlight", "which": "amp", "prop": 8, "min": 0, "max": 854,
         "step": 1, "default": 0, "flags": 2},
    ])
    monkeypatch.setattr(dshow, "enumerate_cameras", lambda: [wide_but_not_touptek])
    cam = dshow.DshowBackend(key=wide_but_not_touptek["key"])
    cam.open()
    assert cam.focus_axis is None, "a wide range on somebody else's camera is a mystery, not a motor"
    cam.close()
