"""The DirectShow backend against a real DirectShow device.

Runs only on Windows with a virtual camera registered and fed by
`tools/wincam_producer.py` -- which is what the `wincam` job in CI sets
up. Everywhere else every test here is skipped, not failed: a machine
without the device is the ordinary state, not a broken one.

What this proves that `test_dshow.py` cannot: that the COM vtables are
spelled right, that the index we describe is the index OpenCV opens,
that the format list comes back from `IAMStreamConfig`, and that a
device with no camera controls is reported as having none rather than
as having sliders that do nothing.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pytest

pytestmark = pytest.mark.wincam

if not sys.platform.startswith("win") or not os.environ.get("DARLASTON_WINCAM"):
    pytest.skip("needs Windows and a registered virtual camera "
                "(DARLASTON_WINCAM=1)", allow_module_level=True)

from darlaston.camera import discovery, dshow  # noqa: E402

VIRTUAL = "Unity Video Capture"


def _virtual() -> dict:
    found = [d for d in dshow.enumerate_cameras() if VIRTUAL in d["name"]]
    assert found, [d["name"] for d in dshow.enumerate_cameras()]
    return found[0]


def test_enumeration_sees_the_virtual_camera():
    d = _virtual()
    assert d["key"].startswith("@device:sw:"), d["key"]
    # Unity Capture writes "foo:bar" under DevicePath; it must not
    # become the key, because every registered copy would share it.
    assert "foo" not in d["key"]
    assert (640, 480) in d["sizes"], d["sizes"]
    assert "RGB3" in d["formats"] or "RGB4" in d["formats"], d["formats"]
    assert d["controls"] == [], "no IAMCameraControl, so no sliders"
    assert d["fingerprint"]


def test_the_index_we_describe_is_the_index_opencv_opens():
    import cv2

    d = _virtual()
    cap = cv2.VideoCapture(d["index"], cv2.CAP_DSHOW)
    try:
        assert cap.isOpened()
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        image = None
        for _ in range(60):
            ok, image = cap.read()
            if ok and image is not None:
                break
            time.sleep(0.05)
        assert image is not None and image.ndim == 3
    finally:
        cap.release()


def test_the_backend_streams_and_captures_the_producers_picture():
    from tools.wincam_producer import QUADRANTS

    d = _virtual()
    backend = dshow.DshowBackend(key=d["key"])
    info = backend.open()
    try:
        assert info.max_bit_depth == 8 and not info.raw_capable
        assert info.exposure_range_us is None and info.gain_range_pct is None
        assert backend.focus_axis is None
        seen = []
        backend.start_stream(lambda f: (seen.append(f.data.copy()), f.release()))
        for _ in range(100):
            if len(seen) >= 5:
                break
            time.sleep(0.05)
        backend.stop_stream()
        assert len(seen) >= 5, "no frames arrived from the virtual camera"
        frame = backend.grab_raw()
        h, w = frame.data.shape[:2]
        # The filter rescales nearest-neighbour to whatever size was
        # asked for, so the quadrants hold at any resolution.
        tl = frame.data[h // 8: h // 2 - h // 8, w // 8: w // 2 - w // 8].reshape(-1, 3)
        br = frame.data[h // 2 + h // 8: h - h // 8, w // 2 + w // 8: w - w // 8].reshape(-1, 3)
        assert np.allclose(tl.mean(axis=0), QUADRANTS["top_left"], atol=12), tl.mean(axis=0)
        assert np.allclose(br.mean(axis=0), QUADRANTS["bottom_right"], atol=12), br.mean(axis=0)
    finally:
        backend.close()


def test_discovery_offers_it_and_presence_sees_it():
    cams = [c for c in discovery.look() if c.kind == "dshow"]
    assert any(VIRTUAL in c.name for c in cams), cams
    cam = next(c for c in cams if VIRTUAL in c.name)
    assert discovery.presence_for(cam)()
    assert not dshow.present("not-a-key")
