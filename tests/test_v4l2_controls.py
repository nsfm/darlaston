"""V4L2 controls, every one of them: kept with their type, flags, value
and menu, and written by id on our own handle beside the stream."""
from __future__ import annotations

import struct

import pytest

from darlaston.camera import v4l2


class FakeDevice:
    """A driver's control table, answering the four ioctls the walk and
    the writes use. The exposure time is inactive while the auto
    exposure menu sits on aperture priority, as a real one is."""

    def __init__(self):
        # id -> (type, name, min, max, step, default, flags, value, menu)
        self.table = {
            0x00980900: (1, "Brightness", -64, 64, 1, 0, 0, 0, ()),
            0x0098090c: (2, "White Balance, Automatic", 0, 1, 1, 1, 0, 1, ()),
            0x0098091a: (1, "White Balance Temperature", 2800, 6500, 1, 4600, 0x10, 4600, ()),
            0x00980913: (1, "Gain", 0, 100, 1, 0, 0, 0, ()),
            0x00980918: (3, "Power Line Frequency", 0, 2, 1, 1, 0, 1, ("Disabled", "50 Hz", "60 Hz")),
            0x009a0901: (3, "Auto Exposure", 0, 3, 1, 3, 0, 3, ("", "Manual Mode", "", "Aperture Priority Mode")),
            0x009a0902: (1, "Exposure Time, Absolute", 1, 5000, 1, 78, 0x10, 78, ()),
            0x00981ae1: (0x0106, "Region Of Interest Rectangle", 0, 0, 1, 0, 0x100, 0, ()),
            0x009a0001: (6, "Camera Controls", 0, 0, 0, 0, 0x44, 0, ()),
        }
        self.writes: list[tuple[int, int]] = []

    def ioctl(self, fh, request, buf):
        if request == v4l2._VIDIOC_QUERYCTRL:
            cid = struct.unpack_from("<I", buf, 0)[0]
            if cid & v4l2._CTRL_FLAG_NEXT:
                after = cid & ~v4l2._CTRL_FLAG_NEXT
                nxt = sorted(i for i in self.table if i > after)
                if not nxt:
                    raise OSError(22, "EINVAL")
                cid = nxt[0]
            if cid not in self.table:
                raise OSError(22, "EINVAL")
            kind, name, lo, hi, step, default, flags, _, _ = self.table[cid]
            struct.pack_into("<II", buf, 0, cid, kind)
            buf[8:40] = name.encode().ljust(32, b"\0")
            struct.pack_into("<iiiiI", buf, 40, lo, hi, step, default, flags)
        elif request == v4l2._VIDIOC_QUERYMENU:
            cid, index = struct.unpack_from("<II", buf, 0)
            menu = self.table[cid][8]
            if index >= len(menu) or not menu[index]:
                raise OSError(22, "EINVAL")
            buf[8:40] = menu[index].encode().ljust(32, b"\0")
        elif request == v4l2._VIDIOC_G_CTRL:
            cid = struct.unpack_from("<I", buf, 0)[0]
            if cid not in self.table:
                raise OSError(22, "EINVAL")
            struct.pack_into("<i", buf, 4, self.table[cid][7])
        elif request == v4l2._VIDIOC_S_CTRL:
            cid, value = struct.unpack_from("<Ii", buf, 0)
            if cid not in self.table:
                raise OSError(22, "EINVAL")
            self.writes.append((cid, value))
            row = list(self.table[cid]); row[7] = value; self.table[cid] = tuple(row)
        else:
            raise OSError(25, "ENOTTY")


@pytest.fixture
def device(monkeypatch, tmp_path):
    dev = FakeDevice()
    monkeypatch.setattr(v4l2, "_ioctl", dev.ioctl)
    node = tmp_path / "video9"
    node.write_bytes(b"")
    monkeypatch.setattr(v4l2.os, "open", lambda *a, **k: 7)
    monkeypatch.setattr(v4l2.os, "close", lambda fd: None)
    return dev, str(node)


def test_the_walk_keeps_type_flags_value_and_menu(device):
    dev, node = device
    raw = {c["name"]: c for c in v4l2.controls(node)}
    assert "Camera Controls" not in raw, "a class header is not a control"
    assert raw["Region Of Interest Rectangle"]["type"] == "other", "kept for the fingerprint"
    assert all(c["value"] is None for c in v4l2.controls(node, values=False)), "listing reads no values"
    assert raw["Brightness"]["type"] == "int" and raw["Brightness"]["value"] == 0
    assert raw["White Balance, Automatic"]["type"] == "bool"
    assert raw["Auto Exposure"]["type"] == "menu"
    assert raw["Auto Exposure"]["menu"] == ("", "Manual Mode", "", "Aperture Priority Mode")
    assert raw["Power Line Frequency"]["menu"][1] == "50 Hz"
    assert raw["Exposure Time, Absolute"]["flags"] & v4l2._CTRL_FLAG_INACTIVE
    assert raw["Exposure Time, Absolute"]["value"] == 78


def test_the_records_pair_each_control_with_its_automatic(device):
    dev, node = device
    by = {c.name: c for c in v4l2.to_controls(v4l2.controls(node))}
    time = by["Exposure Time, Absolute"]
    assert time.auto and time.auto_id == by["Auto Exposure"].id
    assert time.inactive, "the camera's automatic owns it right now"
    temp = by["White Balance Temperature"]
    assert temp.auto_id == by["White Balance, Automatic"].id and temp.inactive
    assert not by["Gain"].auto and not by["Gain"].inactive, "no sibling switch here"
    assert by["Power Line Frequency"].kind == "menu" and by["Power Line Frequency"].menu[2] == "60 Hz"
    assert "Region Of Interest Rectangle" not in by, "other kinds are not records"
    assert all(c.writable for c in by.values())


def test_the_auto_exposure_entry_comes_from_what_the_camera_offered():
    full = {"type": "menu", "min": 0, "menu": ("", "Manual Mode", "", "Aperture Priority Mode")}
    assert v4l2.ae_menu_value(full, True) == 3 and v4l2.ae_menu_value(full, False) == 1
    # Confusingly, the real menu often starts its names at index 1.
    bare = {"type": "menu", "min": 0, "menu": ("Auto Mode", "Manual Mode", "", "")}
    assert v4l2.ae_menu_value(bare, True) == 0, "no aperture priority: plain auto"
    assert v4l2.ae_menu_value(bare, False) == 1
    stuck = {"type": "menu", "min": 0, "menu": ("Auto Mode", "", "", "Aperture Priority Mode")}
    assert v4l2.ae_menu_value(stuck, False) is None, "no manual entry: never ours"
    assert v4l2.ae_menu_value({"type": "menu", "min": 0, "menu": ()}, True) == 3, "unknown: the usual"


def test_an_exposure_the_camera_will_not_take_is_no_exposure(device):
    dev, node = device
    cam = v4l2.V4L2Backend.__new__(v4l2.V4L2Backend)
    cam._controls = {c["name"]: c for c in v4l2.controls(node)}
    assert cam._exposure_range() == (100, 500000)
    assert cam._auto_exposure_switch()
    cam._controls["Exposure Time, Absolute"]["flags"] |= v4l2._CTRL_FLAG_READ_ONLY
    assert cam._exposure_range() is None, "read-only"
    cam._controls["Exposure Time, Absolute"]["flags"] &= ~v4l2._CTRL_FLAG_READ_ONLY
    cam._controls["Auto Exposure"]["menu"] = ("Auto Mode", "", "", "Aperture Priority Mode")
    assert cam._exposure_range() is None, "no manual mode to take it in"
    assert not cam._auto_exposure_switch(), "and no switch that goes both ways"


def test_a_control_is_written_by_id_on_our_own_handle(device):
    dev, node = device
    assert v4l2.write_control(node, 0x00980900, 12)
    assert dev.writes == [(0x00980900, 12)]
    assert v4l2.read_control(node, 0x00980900) == 12
    assert not v4l2.write_control(node, 0x12345678, 1), "a control the device lacks"
    assert v4l2.read_control(node, 0x12345678) is None


def test_the_backend_routes_generic_writes_and_the_automatic_to_the_sibling(device, monkeypatch):
    dev, node = device
    cam = v4l2.V4L2Backend.__new__(v4l2.V4L2Backend)
    cam._node = node
    cam._controls = {c["name"]: c for c in v4l2.controls(node)}
    from darlaston.camera.base import CameraInfo, Resolution
    cam._info = CameraInfo(model="m", serial="s", resolutions=(Resolution(0, 640, 480, 0.0),),
                           max_bit_depth=8, bayer_pattern="", exposure_range_us=(100, 500000),
                           gain_range_pct=(100, 200), controls=v4l2.to_controls(list(cam._controls.values())))
    import threading
    cam._pending, cam._pending_lock = {}, threading.Lock()
    cam._lock = threading.Lock()
    cam._running = threading.Event()
    class Cap:                   # open, as far as the queue is concerned
        def release(self): pass
    cam._cap = Cap()
    cam._thread = None
    by = {c.name: c for c in cam.controls()}
    assert cam.set_control(by["Gain"].id, 40)
    assert (0x00980913, 40) in dev.writes
    assert not cam.set_control(0x12345678, 1), "an id the camera never reported"
    # Taking the exposure back from the camera writes its menu, 1 for
    # manual; handing it over writes 3, aperture priority.
    assert cam.set_auto(by["Exposure Time, Absolute"].id, False)
    assert dev.writes[-1] == (0x009a0901, 1)
    assert cam.set_auto(by["Exposure Time, Absolute"].id, True)
    assert dev.writes[-1] == (0x009a0901, 3)
    # A bool switch is 1 and 0.
    assert cam.set_auto(by["White Balance Temperature"].id, False)
    assert dev.writes[-1] == (0x0098090c, 0)
    assert not cam.set_auto(by["Gain"].id, True), "no automatic to hand it to"
    assert cam.get_control(by["Gain"].id) == 40
    # A walk that comes back short does not shrink the table.
    full = len(cam._controls)
    gone = dev.table.pop(0x00980913)
    cam.controls()
    assert len(cam._controls) == full and "Gain" in cam._controls
    dev.table[0x00980913] = gone
    cam.close()
