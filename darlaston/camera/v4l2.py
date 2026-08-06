"""Ordinary USB cameras, through V4L2.

This is the backend for the cameras microscopists actually buy first: the
50-to-200-pound class that clamps to an eyepiece or screws into a C-mount
and appears as `/dev/video0`. They are UVC devices, and the honest thing
to say about them up front is that **they do not give you raw**.

That is not a driver limitation, it is the class. UVC standardises exactly
two uncompressed payloads, YUY2 and NV12; raw Bayer is only ever a
vendor-specific GUID, and a survey of several hundred thousand real USB
descriptor dumps found not one consumer microscope emitting it. The ISP
sits on the bridge chip, debayers, white-balances and gamma-corrects, and
the USB side has no bypass -- one vendor's support desk puts it as "there
is no firmware or hardware path to bypass this limitation."

So this backend deliberately does not pretend. It delivers 8-bit
demosaiced colour, captures are written as *linear* DNGs rather than
files claiming a CFA pattern they do not have, and everything downstream
that needs real sensor data -- the measured white balance, the flat
field's per-Bayer-phase normalisation -- is simply not offered. What does
still work is most of why this program exists: stage tracking, the slide
map, mosaic capture and stitching, focus peaking, the rack-then-pause
stack trigger, and every depth render.

Streaming goes through OpenCV's V4L2 path rather than hand-rolled ioctls.
For a sensor whose ISP has already made every decision, a decode we did
ourselves would be identical work for identical pixels -- the ioctls here
are only for *asking* what a device is, which OpenCV cannot report.
"""
from __future__ import annotations

import fcntl
import glob
import struct
import threading
import time
from typing import Callable

import cv2
import numpy as np

from .base import CameraBackend, CameraInfo, Resolution
from .buffers import BufferPool, Frame
from .errors import CameraBusy, NoCameraFound

#: Raw Bayer fourccs, so a device that *does* offer one can be recognised
#: and reported. The Imaging Source's industrial cameras are the realistic
#: case: a TIS engineer upstreamed these format GUIDs into the kernel in
#: 2014 and 2016, which is why their cameras give Bayer over plain UVC and
#: essentially nobody else's do. Nothing here consumes it yet.
BAYER_FOURCC = {
    "BA81": "SBGGR8", "GBRG": "SGBRG8", "GRBG": "SGRBG8", "RGGB": "SRGGB8",
    "BYR2": "SBGGR16", "BG16": "SBGGR16", "GB16": "SGBRG16",
    "RG16": "SRGGB16", "GR16": "SGRBG16", "RW10": "SRGGB10P",
}

_VIDIOC_QUERYCAP = (2 << 30) | (104 << 16) | (ord("V") << 8) | 0
_VIDIOC_ENUM_FMT = (3 << 30) | (64 << 16) | (ord("V") << 8) | 2
_VIDIOC_ENUM_FRAMESIZES = (3 << 30) | (44 << 16) | (ord("V") << 8) | 74
_CAP_VIDEO_CAPTURE = 0x00000001
_FRMSIZE_DISCRETE = 1
_VIDIOC_QUERYCTRL = (3 << 30) | (68 << 16) | (ord("V") << 8) | 36
#: Walk the control list rather than guessing at identifiers.
_CTRL_FLAG_NEXT = 0x80000000
_CTRL_FLAG_DISABLED = 0x0001

#: Formats we will not stream, whatever the device says it can do.
#:
#: H.264 is the trap: OpenCV accepts `CAP_PROP_FOURCC` for it and returns
#: True, then hands back frames nothing can decode. Measured on a
#: 0ac8:3420, which advertises H264 first and would therefore win any
#: naive "take the first format" negotiation.
REFUSED_FOURCC = ("H264", "HEVC", "MPG4", "MJPGH264")

#: Preferred in this order. Uncompressed first, because MJPG chroma
#: subsampling and ringing land exactly on the fine detail this program
#: exists to record -- a diatom's striae are a few pixels wide. The cost
#: is measured and real: on a USB 2.0 industrial camera at 1920x1080,
#: YUYV gave 5.0 fps against MJPG's 9.3. So the *preview* may still
#: choose speed; this is the order for a capture.
PREFERRED_FOURCC = ("Y16 ", "GREY", "YUYV", "UYVY", "YU12", "MJPG")


def _fourcc(value: int) -> str:
    return "".join(chr((value >> (8 * k)) & 0xFF) for k in range(4))


def describe(node: str) -> dict | None:
    """What one `/dev/videoN` is and offers, or None if it is not a camera.

    Many devices publish several nodes -- metadata planes, for instance --
    and only some are capture nodes; picking the wrong one gets you an
    unopenable device with a plausible name.
    """
    try:
        fh = open(node, "rb", buffering=0)
    except OSError:
        return None
    with fh:
        buf = bytearray(104)
        try:
            fcntl.ioctl(fh, _VIDIOC_QUERYCAP, buf, True)
        except OSError:
            return None
        card = bytes(buf[16:48]).split(b"\0")[0].decode("ascii", "replace")
        driver = bytes(buf[0:16]).split(b"\0")[0].decode("ascii", "replace")
        caps, device_caps = struct.unpack_from("<II", buf, 84)
        if not (device_caps or caps) & _CAP_VIDEO_CAPTURE:
            return None

        formats, sizes = [], []
        for index in range(64):
            fmt = bytearray(64)
            struct.pack_into("<II", fmt, 0, index, 1)
            try:
                fcntl.ioctl(fh, _VIDIOC_ENUM_FMT, fmt, True)
            except OSError:
                break
            (pixelformat,) = struct.unpack_from("<I", fmt, 44)
            formats.append(_fourcc(pixelformat))
            if not sizes:
                sizes = _sizes(fh, pixelformat)
    return {"node": node, "card": card, "driver": driver,
            "formats": formats, "sizes": sizes,
            "raw": [f for f in formats if f in BAYER_FOURCC]}


def _sizes(fh, pixelformat: int, limit: int = 24) -> list[tuple[int, int]]:
    found = []
    for index in range(limit):
        buf = bytearray(44)
        struct.pack_into("<II", buf, 0, index, pixelformat)
        try:
            fcntl.ioctl(fh, _VIDIOC_ENUM_FRAMESIZES, buf, True)
        except OSError:
            break
        (kind,) = struct.unpack_from("<I", buf, 8)
        if kind == _FRMSIZE_DISCRETE:
            found.append(struct.unpack_from("<II", buf, 12))
        else:
            _minw, maxw, _sw, _minh, maxh = struct.unpack_from("<IIIII",
                                                               buf, 12)
            found.append((maxw, maxh))
            break
    return sorted(set(found), reverse=True)


def identity(node: str) -> str:
    """A key for this camera that survives a replug.

    `/dev/videoN` renumbers, and the cameras that most need identifying
    are the ones least able to say who they are: measured on two here,
    one reports a serial of "0000" and the other reports no serial, no
    manufacturer and no product string at all. So the serial is not
    usable and the name is not unique.

    The physical port is. `bus_info` from QUERYCAP is the same thing
    `/dev/v4l/by-path` encodes, and it holds across replug and reboot for
    as long as the camera stays in the same socket. Moving the cable
    breaks it, which is the honest limit -- and the reason the caller
    should offer "is this the one you called X?" rather than silently
    forgetting.
    """
    try:
        fh = open(node, "rb", buffering=0)
    except OSError:
        return node
    with fh:
        buf = bytearray(104)
        try:
            fcntl.ioctl(fh, _VIDIOC_QUERYCAP, buf, True)
        except OSError:
            return node
        bus = bytes(buf[48:80]).split(b"\0")[0].decode("ascii", "replace")
    return bus or node


def controls(node: str) -> list[dict]:
    """Every control the driver advertises for this device.

    Empty is a real answer, not a failure. Cameras that expose nothing at
    all exist and are not rare: measured on a 0ac8:3420, whose USB
    descriptors declare `bmControls = 0x0000` for both the camera
    terminal and the processing unit, so `uvcvideo` registers no controls
    at probe time and no amount of asking will produce any. A second
    camera on the same machine, costing less, advertises sixteen.

    Which is the whole reason this exists: capability has to be asked
    for, per device, at runtime. It cannot be assumed from the class of
    device, the price, or the vendor.
    """
    found = []
    try:
        fh = open(node, "rb", buffering=0)
    except OSError:
        return found
    with fh:
        cid = _CTRL_FLAG_NEXT
        while len(found) < 128:
            buf = bytearray(68)
            struct.pack_into("<I", buf, 0, cid)
            try:
                fcntl.ioctl(fh, _VIDIOC_QUERYCTRL, buf, True)
            except OSError:
                break            # EINVAL ends the walk; anything else, stop
            got, kind = struct.unpack_from("<II", buf, 0)
            name = bytes(buf[8:40]).split(b"\0")[0].decode("ascii", "replace")
            low, high, step, default, flags = struct.unpack_from("<iiiiI",
                                                                 buf, 40)
            if not flags & _CTRL_FLAG_DISABLED and not name.endswith(
                    "Controls"):
                # The "User Controls"/"Camera Controls" entries are class
                # headers, not settings. Keeping them would put two
                # unusable rows in front of somebody.
                found.append({"id": got, "name": name, "min": low,
                              "max": high, "step": step, "default": default})
            cid = got | _CTRL_FLAG_NEXT
    return found


def usable_formats(found: dict) -> list[str]:
    """The device's formats, best first, with the undecodable ones gone."""
    offered = [f for f in found["formats"] if f.strip() not in REFUSED_FOURCC]
    rank = {name.strip(): i for i, name in enumerate(PREFERRED_FOURCC)}
    return sorted(offered, key=lambda f: rank.get(f.strip(), 99))


def enumerate_cameras() -> list[dict]:
    """Every V4L2 capture device, the biggest sensor first.

    Sorted by what the device can actually deliver, not by node number.
    It used to claim "largest first" in this docstring and return them in
    `/dev/videoN` order, which on a real machine put a 1920x1080 camera
    behind a 640x360 infrared face-unlock sensor -- so anything picking
    the first entry picked the wrong device.
    """
    out = []
    for node in sorted(glob.glob("/dev/video*")):
        found = describe(node)
        if not (found and found["sizes"]):
            continue
        found["controls"] = controls(node)
        found["usable"] = usable_formats(found)
        found["key"] = identity(node)
        out.append(found)
    # Biggest first, and a device that offers nothing we can decode goes
    # last however large its sensor.
    out.sort(key=lambda d: (bool(d["usable"]),
                            d["sizes"][0][0] * d["sizes"][0][1]), reverse=True)
    return out


class V4L2Backend(CameraBackend):
    """A UVC camera. Colour in, colour out, and honest about it."""

    def __init__(self, index: int = 0, node: str | None = None) -> None:
        self._node = node
        self._index = index
        self._cap: cv2.VideoCapture | None = None
        self._info: CameraInfo | None = None
        self._pool: BufferPool | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._seq = 0
        self._exposure_us = 20000
        self._gain_pct = 100
        self._current = 0

    # ---- lifecycle -------------------------------------------------------

    def open(self) -> CameraInfo:
        found = (describe(self._node) if self._node
                 else (enumerate_cameras() or [None])[self._index])
        if not found:
            raise NoCameraFound("USB camera")
        self._node = found["node"]
        self._cap = cv2.VideoCapture(self._node, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise CameraBusy(
                f"{found['card']} at {self._node} is present but would "
                "not open.")
        # MJPEG where offered: a 1600x1200 YUYV stream is 46 MB/s and will
        # not fit USB 2.0, which is what most of these cameras are on.
        if "MJPG" in found["formats"]:
            self._cap.set(cv2.CAP_PROP_FOURCC,
                          cv2.VideoWriter_fourcc(*"MJPG"))

        sizes = found["sizes"]
        resolutions = tuple(
            # Pixel pitch is unknowable here: UVC does not report it and
            # these cameras rarely publish it. Zero means "unknown", and
            # the scale bar refuses to draw rather than inventing one.
            Resolution(i, w, h, 0.0) for i, (w, h) in enumerate(sizes))
        self._select(0)
        self._info = CameraInfo(
            model=found["card"], serial=self._node,
            resolutions=resolutions, max_bit_depth=8,
            bayer_pattern="",              # decoded already; no CFA exists
            exposure_range_us=(100, 1_000_000),
            gain_range_pct=(100, 1600),
            brand=found["driver"],
            raw_capable=bool(found["raw"]),
            software_trigger=True,
        )
        return self._info

    def close(self) -> None:
        self.stop_stream()
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    @property
    def info(self) -> CameraInfo | None:
        return self._info

    # ---- settings --------------------------------------------------------

    def _select(self, index: int) -> None:
        if self._info is not None:
            index = max(0, min(index, len(self._info.resolutions) - 1))
        res = (self._info.resolutions[index] if self._info
               else Resolution(0, 1280, 720, 0.0))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, res.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res.height)
        self._current = index

    def set_resolution(self, index: int) -> None:
        streaming = self._running.is_set()
        on_frame = self._on_frame if streaming else None
        if streaming:
            self.stop_stream()
        with self._lock:
            self._select(index)
        if streaming and on_frame is not None:
            self.start_stream(on_frame)

    def set_exposure(self, microseconds: int) -> None:
        self._exposure_us = int(microseconds)
        with self._lock:
            if self._cap is None:
                return
            # V4L2 exposure is in 100 us units, and it is only writable
            # once auto-exposure is off -- which is control 1 (manual) on
            # UVC, not 0. Setting the value first silently does nothing.
            self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            self._cap.set(cv2.CAP_PROP_EXPOSURE, max(1, microseconds // 100))

    def set_gain(self, percent: int) -> None:
        self._gain_pct = int(percent)
        with self._lock:
            if self._cap is not None:
                self._cap.set(cv2.CAP_PROP_GAIN, percent)

    def get_exposure(self) -> int:
        with self._lock:
            if self._cap is None:
                return self._exposure_us
            value = self._cap.get(cv2.CAP_PROP_EXPOSURE)
        return int(value * 100) if value and value > 0 else self._exposure_us

    def get_gain(self) -> int:
        with self._lock:
            if self._cap is None:
                return self._gain_pct
            value = self._cap.get(cv2.CAP_PROP_GAIN)
        return int(value) if value and value > 0 else self._gain_pct

    # ---- streaming -------------------------------------------------------

    _on_frame: Callable[[Frame], None] | None = None

    def start_stream(self, on_frame: Callable[[Frame], None]) -> None:
        if self._thread is not None or self._cap is None:
            return
        res = self._info.resolutions[self._current]
        self._on_frame = on_frame
        self._pool = BufferPool((res.height, res.width, 3), np.uint8, count=4)
        self._running.set()
        self._thread = threading.Thread(target=self._loop, args=(on_frame,),
                                        daemon=True, name="v4l2-camera")
        self._thread.start()

    def stop_stream(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self, on_frame: Callable[[Frame], None]) -> None:
        while self._running.is_set():
            with self._lock:
                if self._cap is None:
                    return
                ok, image = self._cap.read()
            if not ok or image is None:
                time.sleep(0.02)
                continue
            buf = self._pool.acquire()
            if buf is None:
                continue                      # dropped: the consumer is behind
            if image.shape != buf.shape:
                # The driver may hand back a size it preferred over the one
                # asked for; fit rather than tearing.
                image = cv2.resize(image, (buf.shape[1], buf.shape[0]),
                                   interpolation=cv2.INTER_AREA)
            buf[...] = image
            self._seq += 1
            on_frame(Frame(data=buf, seq=self._seq, timestamp=time.time(),
                           exposure_us=self.get_exposure(),
                           gain_pct=self._gain_pct, binned=True,
                           _pool=self._pool))

    # ---- capture ---------------------------------------------------------

    def grab_raw(self, timeout_ms: int = 8000) -> Frame:
        """One full-resolution frame -- demosaiced, because that is all
        there is. Named `raw` to satisfy the interface, not to claim it."""
        if self._cap is None:
            raise RuntimeError("camera is not open")
        streaming = self._running.is_set()
        on_frame = self._on_frame
        if streaming:
            self.stop_stream()
        try:
            with self._lock:
                previous = self._current
                self._select(0)               # index 0 is the largest
                # The first frames after a size change come from the old
                # pipeline; discard a few rather than saving one.
                for _ in range(4):
                    self._cap.read()
                ok, image = self._cap.read()
                self._select(previous)
            if not ok or image is None:
                raise RuntimeError("capture failed -- the camera returned "
                                   "no frame")
            return Frame(data=np.ascontiguousarray(image), seq=self._seq,
                         timestamp=time.time(),
                         exposure_us=self.get_exposure(),
                         gain_pct=self._gain_pct, binned=False)
        finally:
            if streaming and on_frame is not None:
                self.start_stream(on_frame)
