"""Ask OpenCV, out loud, what it makes of the cameras on a Windows box.

A diagnostic for the CI runner and for a user's machine alike: prints
the video I/O backends OpenCV was built with, what our own DirectShow
enumeration sees, and then tries to open each device through every
backend OpenCV offers with its own debug logging switched on, so a
refusal comes with OpenCV's reason rather than a bare False.

    OPENCV_VIDEOIO_DEBUG=1 OPENCV_LOG_LEVEL=DEBUG python tools/wincam_probe.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENCV_VIDEOIO_DEBUG", "1")
os.environ.setdefault("OPENCV_LOG_LEVEL", "DEBUG")

import cv2  # noqa: E402


def section(title: str) -> None:
    print(f"\n==== {title} ====", flush=True)


def media_components() -> dict:
    """Which pieces of the Windows media stack this machine has.

    OpenCV's DirectShow capture builds a graph with qedit's sample
    grabber; its Media Foundation backend needs mfplat. A Windows Server
    image can ship without either -- the "Server-Media-Foundation"
    feature is optional there -- and then every open fails with no
    reason given, whatever the camera.
    """
    import ctypes
    from ctypes import byref, c_void_p

    out = {}
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name in ("qedit.dll", "mfplat.dll", "mf.dll", "quartz.dll", "ksproxy.ax"):
        out[name] = (system32 / name).exists()
    try:
        from darlaston.camera.dshow import GUID, _ole32
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        for label, clsid in (
                ("SampleGrabber", "C1F400A0-3F08-11D3-9F0B-006008039E37"),
                ("NullRenderer", "C1F400A4-3F08-11D3-9F0B-006008039E37"),
                ("FilterGraph", "e436ebb3-524f-11ce-9f53-0020af0ba770"),
                ("CaptureGraphBuilder2", "BF87B6E1-8C27-11d0-B3F0-00AA003761C5")):
            ptr = c_void_p()
            iid = GUID.of("00000000-0000-0000-C000-000000000046")
            try:
                _ole32().CoCreateInstance(byref(GUID.of(clsid)), None,
                                          ctypes.c_uint32(1), byref(iid), byref(ptr))
                out[f"CoCreateInstance({label})"] = "ok"
            except OSError as why:
                out[f"CoCreateInstance({label})"] = f"FAILED {why}"
    except Exception as why:
        out["com"] = f"? {why}"
    return out


def main() -> int:
    section("media components")
    for key, value in media_components().items():
        print(f"   {key}: {value}")

    section("OpenCV")
    print(cv2.__version__)
    info = cv2.getBuildInformation()
    start = info.find("Video I/O")
    print(info[start:start + 900] if start >= 0 else "(no Video I/O section)")
    try:
        print("backends:", [cv2.videoio_registry.getBackendName(b)
                            for b in cv2.videoio_registry.getCameraBackends()])
    except Exception as why:
        print("backends: ?", why)

    section("our enumeration")
    from darlaston.camera import dshow
    found = dshow.enumerate_cameras()
    for d in found:
        print(d["index"], repr(d["name"]), "path=", repr(d["path"]))
        print("   display=", repr(d["display"]))
        print("   formats=", d["formats"], "sizes=", d["sizes"][:8])
        print("   controls=", [(c["name"], c["min"], c["max"]) for c in d["controls"]])

    section("OpenCV opens")
    apis = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF), ("ANY", cv2.CAP_ANY)]
    for index in range(max(2, len(found))):
        for name, api in apis:
            print(f"\n-- index {index} via {name}", flush=True)
            cap = cv2.VideoCapture(index, api)
            opened = cap.isOpened()
            print("   isOpened:", opened, "backend:",
                  cap.getBackendName() if opened else "-")
            if opened:
                w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                code = int(cap.get(cv2.CAP_PROP_FOURCC)) & 0xFFFFFFFF
                print(f"   size {w:.0f}x{h:.0f} fourcc 0x{code:08x}")
                got = None
                for _ in range(40):
                    ok, got = cap.read()
                    if ok and got is not None:
                        break
                    time.sleep(0.05)
                print("   frame:", None if got is None else got.shape,
                      None if got is None else got.reshape(-1, 3).mean(axis=0))
            cap.release()

    section("open with params (size + RGB24 fourcc first)")
    for index in range(max(1, len(found))):
        # Signed 32-bit integers only: the params sequence refuses a
        # float, and RGB24's Data1 is above INT_MAX as written.
        params = [cv2.CAP_PROP_FRAME_WIDTH, 640, cv2.CAP_PROP_FRAME_HEIGHT, 480,
                  cv2.CAP_PROP_FOURCC, int(0xe436eb7d) - (1 << 32)]
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW, params)
        print(f"index {index} DSHOW+params isOpened:", cap.isOpened())
        if cap.isOpened():
            ok, got = cap.read()
            print("   frame:", ok, None if got is None else got.shape)
        cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
