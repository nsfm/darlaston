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

Streaming goes through OpenCV's V4L2 path rather than hand-rolled ioctls,
and the loop itself is shared with every other `cv2`-fed camera (see
`cvcam.py`); only the asking is here.
For a sensor whose ISP has already made every decision, a decode we did
ourselves would be identical work for identical pixels -- the ioctls here
are only for *asking* what a device is, which OpenCV cannot report.
"""
from __future__ import annotations

import fcntl
import glob
import os
import struct

import cv2

from .base import CameraInfo, Control, FocusAxis, FocusRange, Resolution
from .cvcam import (PREFERRED_FOURCC, REFUSED_FOURCC,  # noqa: F401 -- re-exported
                    SETTLE_FRAMES, WARMUP_FRAMES, CvCaptureBackend)
from .cvcam import usable_formats as _usable_formats
from .errors import CameraBusy, NoCameraFound, PermissionDenied

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
_VIDIOC_G_CTRL = (3 << 30) | (8 << 16) | (ord("V") << 8) | 27
_VIDIOC_S_CTRL = (3 << 30) | (8 << 16) | (ord("V") << 8) | 28
_VIDIOC_QUERYMENU = (3 << 30) | (44 << 16) | (ord("V") << 8) | 37
_CTRL_FLAG_NEXT = 0x80000000
_CTRL_FLAG_DISABLED = 0x0001
_CTRL_FLAG_READ_ONLY = 0x0004
_CTRL_FLAG_INACTIVE = 0x0010
_CTRL_FLAG_WRITE_ONLY = 0x0040
_CTRL_FLAG_HAS_PAYLOAD = 0x0100
_CTRL_TYPE_INTEGER, _CTRL_TYPE_BOOLEAN, _CTRL_TYPE_MENU = 1, 2, 3
_CTRL_TYPE_INTEGER_MENU = 9
_CTRL_KINDS = {_CTRL_TYPE_INTEGER: "int", _CTRL_TYPE_BOOLEAN: "bool",
               _CTRL_TYPE_MENU: "menu", _CTRL_TYPE_INTEGER_MENU: "menu"}

#: The switch that runs a control, by the names the kernel gives them,
#: old and new. V4L2 keeps the automatic as a sibling control rather
#: than a flag on the one it drives, so the pairing has to be known.
_AUTO_SIBLINGS = {
    ("Exposure Time, Absolute", "Exposure (Absolute)"): ("Auto Exposure", "Exposure, Auto"),
    ("White Balance Temperature",): ("White Balance, Automatic", "White Balance Temperature, Auto"),
    ("Focus, Absolute",): ("Focus, Automatic Continuous", "Focus, Auto"),
    ("Hue",): ("Hue, Auto",),
    ("Brightness",): ("Brightness, Automatic",),
    ("Gain",): ("Gain, Automatic",),
}


def _ioctl(fh, request: int, buf) -> None:
    """One seam for the tests to stand on."""
    fcntl.ioctl(fh, request, buf, True)


def _menu_entries(fh, cid: int, low: int, high: int, kind: int) -> tuple[str, ...]:
    """A menu's entries, by index. A hole in the index range is a real
    thing (a camera may skip a mode) and comes back as an empty name."""
    entries = []
    for index in range(low, min(high, low + 32) + 1):
        buf = bytearray(44)
        struct.pack_into("<II", buf, 0, cid, index)
        try:
            _ioctl(fh, _VIDIOC_QUERYMENU, buf)
        except OSError:
            entries.append("")
            continue
        if kind == _CTRL_TYPE_INTEGER_MENU:
            entries.append(str(struct.unpack_from("<q", buf, 8)[0]))
        else:
            entries.append(bytes(buf[8:40]).split(b"\0")[0].decode("ascii", "replace"))
    return tuple(entries)


def _read_value(fh, cid: int) -> int | None:
    buf = bytearray(8)
    struct.pack_into("<I", buf, 0, cid)
    try:
        _ioctl(fh, _VIDIOC_G_CTRL, buf)
    except OSError:
        return None
    return struct.unpack_from("<i", buf, 4)[0]


def read_control(node: str, cid: int) -> int | None:
    """The current value of one control, by id, on its own handle."""
    try:
        fh = open(node, "rb", buffering=0)
    except OSError:
        return None
    with fh:
        return _read_value(fh, cid)


def write_control(node: str, cid: int, value: int) -> bool:
    """Set one control by id, on our own handle beside the stream.

    Not through cv2, which reaches only the handful of properties it
    has names for. A control is device state, not stream state, so a
    write on a second handle lands all the same.
    """
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        buf = bytearray(8)
        struct.pack_into("<Ii", buf, 0, cid, int(value))
        _ioctl(fd, _VIDIOC_S_CTRL, buf)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
#: The focus motor, by identifier. By identifier and not by name
#: because the name has changed under us: `Focus (absolute)` on older
#: kernels, `Focus, Absolute` now, and the auto switch went from
#: `Focus, Auto` to `Focus, Automatic Continuous`. The numbers are ABI.
V4L2_CID_FOCUS_ABSOLUTE = 0x009a090a
V4L2_CID_FOCUS_AUTO = 0x009a090c

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
    if not bus:
        return node

    # The port alone is not unique. One camera can publish several
    # capture nodes on one port -- measured on a laptop whose built-in
    # camera and its infrared face-unlock sensor share a port and differ
    # only by USB interface. Keyed on the port alone, remembering one
    # could open the other, and an infrared sensor is a memorable thing
    # to find yourself looking through.
    return _key_from(bus, node)


def _why_it_would_not_open(node: str, card: str) -> Exception:
    """Ask the device directly, since OpenCV only says no.

    `cv2.VideoCapture` reports failure without a reason, and the two
    likely reasons want opposite advice: another program holding the
    camera is fixed by closing that program, and a user who is not in the
    `video` group is fixed by an administrator. Guessing "busy" for both
    sends half the people down the wrong path.

    Measured in practice: guvcview holding the device produced exactly
    this failure, and so does a fresh Linux install where nobody has been
    added to the group yet.
    """
    try:
        open(node, "rb", buffering=0).close()
    except PermissionError:
        return PermissionDenied(
            f"{card} at {node} is present, but this user may not open it.")
    except OSError as why:
        import errno

        if why.errno == errno.EBUSY:
            return CameraBusy(
                f"{card} at {node} is held by another program.")
        return CameraBusy(f"{card} at {node} would not open: {why}")
    # The device opens fine on its own, so whatever stopped OpenCV was
    # not ownership. Say so rather than inventing a cause.
    return CameraBusy(
        f"{card} at {node} opened, but no video could be started from it.")


def fingerprint(found: dict) -> str:
    """A short hash of everything this camera says about itself.

    Not a serial. Two identical cameras produce the same fingerprint, so
    this identifies a *model in a configuration* rather than a specific
    device -- which is exactly what is wanted alongside the port, because
    the port identifies the instance and nothing identifies both.

    The point is the case where somebody moves the cable. The port key
    changes, the camera looks like a stranger, and the description they
    wrote is orphaned. With a fingerprint we can say "this is the same
    kind of camera you called X, on a different port" and offer to carry
    the description over -- which is a question worth asking, where
    silently forgetting is not.

    Built from what the device publishes and a person cannot change: its
    name, its formats, its sizes, and the names of its controls. Values
    are deliberately excluded, since exposure and gain move constantly
    and a fingerprint that changes when you adjust a slider is no use.
    """
    import hashlib

    parts = [
        found.get("card", ""),
        found.get("driver", ""),
        ",".join(sorted(found.get("formats") or [])),
        ",".join(f"{w}x{h}" for w, h in sorted(found.get("sizes") or [])),
        ",".join(sorted(c["name"] for c in (found.get("controls") or []))),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


def _key_from(bus: str, node: str) -> str:
    """Compose the key. Separated out so it can be asked directly.

    Testing this through `enumerate_cameras` meant testing it against
    whatever happened to be plugged in, which is not a test.
    """
    interface = _interface_of(node)
    if interface:
        return f"{bus}:{interface}"
    # Sysfs would not say. Fall back to the node, which renumbers across
    # reboots and is therefore a worse key -- but never a *colliding*
    # one. Two cameras sharing a key is not instability, it is opening
    # the wrong camera, and the wrong camera here is somebody's face.
    return f"{bus}:{os.path.basename(node)}"


def _manufacturer(node: str) -> str:
    """The USB manufacturer string, if the device publishes one."""
    import os

    name = os.path.basename(node)
    try:
        device = os.path.realpath(f"/sys/class/video4linux/{name}/device")
        with open(os.path.join(os.path.dirname(device), "manufacturer")) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _interface_of(node: str) -> str:
    """Which USB interface this node belongs to, from sysfs.

    The device link for /dev/videoN ends in the interface address, like
    `1-8:1.0` for the colour camera and `1-8:1.2` for the infrared one on
    the same physical camera.
    """
    import os

    name = os.path.basename(node)
    try:
        target = os.readlink(f"/sys/class/video4linux/{name}/device")
    except OSError:
        return ""
    tail = os.path.basename(target.rstrip("/"))
    return tail.split(":")[-1] if ":" in tail else ""


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
                _ioctl(fh, _VIDIOC_QUERYCTRL, buf)
            except OSError:
                break            # EINVAL ends the walk; anything else, stop
            got, kind = struct.unpack_from("<II", buf, 0)
            name = bytes(buf[8:40]).split(b"\0")[0].decode("ascii", "replace")
            low, high, step, default, flags = struct.unpack_from("<iiiiI",
                                                                 buf, 40)
            cid = got | _CTRL_FLAG_NEXT
            if flags & _CTRL_FLAG_DISABLED or name.endswith("Controls"):
                # The "User Controls"/"Camera Controls" entries are class
                # headers, not settings. Keeping them would put two
                # unusable rows in front of somebody.
                continue
            if kind not in _CTRL_KINDS or flags & _CTRL_FLAG_HAS_PAYLOAD:
                # Strings, 64-bit integers, buttons, regions: nothing a
                # bar or a tick could show, and nothing we would drive.
                continue
            # Kept now, where before only the range was: the type, the
            # flags and the value. The inactive flag is the camera
            # saying its own automatic owns this control right now,
            # which is exactly the thing the rail has to know.
            menu = (_menu_entries(fh, got, low, high, kind)
                    if _CTRL_KINDS[kind] == "menu" else ())
            value = (None if flags & _CTRL_FLAG_WRITE_ONLY
                     else _read_value(fh, got))
            found.append({"id": got, "name": name, "min": low,
                          "max": high, "step": step, "default": default,
                          "type": _CTRL_KINDS[kind], "flags": flags,
                          "value": value, "menu": menu})
    return found


def _auto_sibling(name: str, by_name: dict) -> dict | None:
    for driven, switches in _AUTO_SIBLINGS.items():
        if name in driven:
            return next((by_name[s] for s in switches if s in by_name), None)
    return None


def to_controls(raw: list[dict]) -> tuple[Control, ...]:
    """The enumeration's dicts as the records the rest of the program
    reads, with each control's automatic found among its siblings."""
    by_name = {c["name"]: c for c in raw}
    out = []
    for c in raw:
        sibling = _auto_sibling(c["name"], by_name)
        out.append(Control(
            id=c["id"], name=c["name"], kind=c.get("type", "int"),
            min=c["min"], max=c["max"], step=max(1, c.get("step", 1)),
            default=c["default"], value=c.get("value"),
            auto_id=sibling["id"] if sibling else None,
            inactive=bool(c.get("flags", 0) & _CTRL_FLAG_INACTIVE),
            menu=tuple(c.get("menu", ()))))
    return tuple(out)


def usable_formats(found: dict) -> list[str]:
    """The device's formats, best first, with the undecodable ones gone."""
    return _usable_formats(found["formats"])


def _detail(found: dict) -> dict:
    """Everything about a device that costs an ioctl to learn.

    Split out from the enumeration so a single node can be described the
    same way, without walking every other camera on the machine to do it.
    """
    node = found["node"]
    found["controls"] = controls(node)
    found["usable"] = usable_formats(found)
    found["key"] = identity(node)
    found["fingerprint"] = fingerprint(found)
    return found


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
        out.append(_detail(found))
    # Biggest first, and a device that offers nothing we can decode goes
    # last however large its sensor.
    out.sort(key=lambda d: (bool(d["usable"]),
                            d["sizes"][0][0] * d["sizes"][0][1]), reverse=True)
    return out


class V4L2Backend(CvCaptureBackend):
    """A UVC camera on Linux. The asking is here; the streaming is shared."""

    THREAD_NAME = "v4l2-camera"

    def __init__(self, index: int = 0, node: str | None = None,
                 key: str = "", fingerprint: str = "") -> None:
        super().__init__()
        self._node = node
        self._index = index
        #: Who this backend is for, as opposed to where it last was. A
        #: node number is a position in a list the kernel rebuilds on
        #: every replug; these survive it. Empty means "whatever is at
        #: `node`", which is what a bare backend has always meant.
        self._key = key
        self._fingerprint = fingerprint
        #: Filled in by `open`, from the driver. Empty means the device
        #: has told us it offers nothing, which is a real answer.
        self._controls: dict = {}
        self._focus: _V4L2Focus | None = None

    @property
    def focus_axis(self) -> FocusAxis | None:
        return self._focus

    # ---- lifecycle -------------------------------------------------------

    def _is_it(self, found: dict) -> bool:
        """Is the device described here the one this backend is for?"""
        if self._key and found.get("key") != self._key:
            return False
        if self._fingerprint and found.get("fingerprint") != self._fingerprint:
            return False
        return True

    def _locate(self) -> dict | None:
        """Which node this camera is on *now*, which is not always where
        it was.

        Node numbers renumber. Pulling a camera out and putting it back
        can move it, or move everything else around it, and this backend
        is rebuilt on every reconnect from a record captured before that
        happened. Holding the old number meant a session that reconnected
        for ever to a device no longer there -- or, worse, opened whatever
        had taken the number.

        The remembered node is still the first guess: it is nearly always
        right and costs one ioctl against a full enumeration's per-device
        format walk. It is only checked, not trusted.
        """
        wanted = self._key or self._fingerprint
        if self._node:
            found = describe(self._node)
            if found and found["sizes"]:
                found = _detail(found)
                if not wanted or self._is_it(found):
                    return found
            if not wanted:
                return None            # a bare node backend; nothing to search
        elif not wanted:
            return (enumerate_cameras() or [None])[self._index]

        seen = enumerate_cameras()
        for found in seen:
            if self._key and found.get("key") == self._key:
                return found
        # The port key has moved too, so the cable is in a different
        # socket. A device publishing the same name, formats, sizes and
        # controls is that camera -- unless there are two of them, in
        # which case guessing is worse than waiting.
        same = [f for f in seen
                if self._fingerprint and f.get("fingerprint") == self._fingerprint]
        return same[0] if len(same) == 1 else None

    def open(self) -> CameraInfo:
        found = self._locate()
        if not found:
            raise NoCameraFound("USB camera")
        self._node = found["node"]
        self._cap = cv2.VideoCapture(self._node, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            raise _why_it_would_not_open(self._node, found["card"])
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
        # What this device really offers, rather than what a UVC camera
        # might. Measured on two cameras in one machine: sixteen controls
        # on one, none whatsoever on the other. Claiming a range we
        # cannot drive puts a slider in front of somebody that moves and
        # does nothing.
        self._controls = {c["name"]: c
                          for c in (found.get("controls") or controls(self._node))}
        self._usable = found.get("usable") or usable_formats(found)
        motor = self._by_id(V4L2_CID_FOCUS_ABSOLUTE)
        self._focus = _V4L2Focus(self, motor) if motor else None
        self._settle()

        self._info = CameraInfo(
            model=found["card"],
            # The stable port key, not /dev/videoN. The library files
            # cameras by serial, and a node number renumbers across
            # reboots -- so a described camera came back as a stranger
            # and was filed again under a new name. This is the same key
            # the picker uses, so the two agree about what a camera is.
            serial=found.get("key") or identity(self._node),
            resolutions=resolutions, max_bit_depth=8,
            bayer_pattern="",              # decoded already; no CFA exists
            exposure_range_us=self._exposure_range(),
            gain_range_pct=self._gain_range(),
            auto_exposure_switch=bool(self._has("Auto Exposure")),
            controls=to_controls(list(self._controls.values())),
            # Who made it, not which kernel module drives it. Read from
            # the USB descriptor, and frequently absent or a placeholder
            # -- one camera here says "HD Camera Manufacturer" -- but a
            # placeholder the device actually published beats a name we
            # invented for it.
            brand=_manufacturer(self._node),
            raw_capable=bool(found["raw"]),
            software_trigger=True,
            fingerprint=found.get("fingerprint", ""),
        )

        # After `_info`, deliberately. `_select` reads the resolution list
        # from it, so choosing a mode first fell through to a hard-coded
        # 1280x720 while recording index 0 -- and every frame was then
        # upscaled to a full-resolution pool.
        #
        # The largest mode is the preview because these modes *crop*: at
        # 1280x720 you see the middle 44% of what a capture records,
        # which is the wrong way round for composing a shot.
        self._select(0)
        return self._info

    def _has(self, *names: str) -> dict | None:
        for name in names:
            found = self._controls.get(name)
            if found:
                return found
        return None

    def _by_id(self, cid: int) -> dict | None:
        return next((c for c in self._controls.values()
                     if c.get("id") == cid), None)

    def _exposure_range(self) -> tuple[int, int] | None:
        """In microseconds, from the control's own limits.

        UVC reports exposure in 100 us units, so the driver's 1..5000
        becomes 100 us to half a second. Taking the device's word for it
        beats the range we used to assert, which was the same on every
        camera and true of none.
        """
        found = self._has("Exposure Time, Absolute", "Exposure (Absolute)")
        if not found:
            return None
        return (max(1, found["min"]) * 100, max(1, found["max"]) * 100)

    def _gain_range(self) -> tuple[int, int] | None:
        """As a percentage where 100 is the device's own floor.

        UVC gain units are device-defined and mean nothing across
        cameras: this one runs 0..100 and measured 2.8x end to end, so
        the number is a position on its scale rather than a true
        multiplier. Said here rather than converted, because inventing a
        multiplier we have not measured would be worse than a scale that
        is honestly just a scale.
        """
        found = self._has("Gain")
        if not found:
            return None
        return (100, 100 + max(1, found["max"] - found["min"]))

    def _settle(self) -> None:
        """Put the camera into a known state, and let it get there.

        Three things, each learned the hard way on real hardware:

        * **Manual exposure**, where the device has it. Automatic
          exposure re-exposes between frames, which shows up as tiles
          that do not match across a mosaic and slices that do not match
          within a stack.
        * **Auto white balance off.** It adjusts channel gains underneath
          a measurement. It also has no business anywhere near a
          calibration sweep, and it is what makes a tungsten-lit field
          come out yellow.
        * **Discard the first frames.** The first frame after opening can
          be a blown or empty one -- measured at 254 mean where the
          settled value was 89 -- and after any control change the next
          two frames still carry the old setting.
        """
        if self._cap is None:
            return
        try:
            # Only where there is an exposure time to take over. A camera
            # with the switch and no time meters itself; switching its
            # auto off would leave it at whatever it last chose, dark or
            # blown, with nothing of ours to drive in its place.
            if self._has("Auto Exposure") and self._exposure_range():
                self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)     # manual
            if self._has("White Balance, Automatic", "White Balance Temperature, Auto"):
                self._cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        except Exception:
            pass
        self._warm_up()

    # ---- settings --------------------------------------------------------

    # ---- every control, by id ----------------------------------------------

    def controls(self) -> tuple[Control, ...]:
        """Read again rather than remembered: the values and the
        inactive flags are what change under the camera's own automatic."""
        if self._node is None:
            return ()
        fresh = controls(self._node)
        if fresh:
            self._controls = {c["name"]: c for c in fresh}
        return to_controls(list(self._controls.values()))

    def get_control(self, control_id: int) -> int | None:
        return read_control(self._node, control_id) if self._node else None

    def _auto_value(self, switch: dict, on: bool) -> int:
        """What 'on' is for a switch: a bool is 1, and UVC's auto
        exposure menu is 3 (aperture priority) against 1 (manual)."""
        if switch.get("type") == "menu":
            return 3 if on else 1
        return 1 if on else 0

    def _write_control(self, what: str, value) -> None:
        """The actual writes. Called holding `_lock`, never from the UI."""
        if what.startswith("control:"):
            write_control(self._node, int(what.split(":", 1)[1]), int(value))
        elif what.startswith("auto:"):
            cid = int(what.split(":", 1)[1])
            driven = self._by_id(cid)
            switch = _auto_sibling(driven["name"], self._controls) if driven else None
            if switch is not None:
                write_control(self._node, switch["id"],
                              self._auto_value(switch, bool(value)))
        elif what == "auto_exposure":
            # UVC's menu: 1 is manual, 3 is aperture priority, which is
            # what every webcam measured calls its automatic.
            self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if value else 1)
        elif what == "exposure":
            if self._exposure_range() is None:
                # No time to set. The session sends its exposure on every
                # connect and mode change, and on a camera that meters
                # itself the write below would switch its automatic off
                # and leave it frozen at whatever it last chose.
                return
            # Only writable once auto-exposure is off -- control 1
            # (manual) on UVC, not 0. Setting the value first silently
            # does nothing.
            self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            self._cap.set(cv2.CAP_PROP_EXPOSURE, max(1, int(value) // 100))
        elif what == "gain":
            found = self._has("Gain")
            if not found:
                return
            level = found["min"] + max(0, int(value) - 100)
            self._cap.set(cv2.CAP_PROP_GAIN,
                          min(found["max"], max(found["min"], level)))
        elif what == "focus":
            # OpenCV maps these onto V4L2_CID_FOCUS_AUTO and
            # V4L2_CID_FOCUS_ABSOLUTE. Autofocus must be off first: a
            # position written while the camera is still driving the
            # motor is refused with EIO on every camera measured.
            if self._by_id(V4L2_CID_FOCUS_AUTO):
                self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            self._cap.set(cv2.CAP_PROP_FOCUS, int(value))
        elif what == "focus_auto":
            if self._by_id(V4L2_CID_FOCUS_AUTO):
                self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if value else 0)
            if not value and self._focus is not None:
                # Taking the motor back: wherever the camera left it is
                # where it is, and the slider should say so.
                got = self._cap.get(cv2.CAP_PROP_FOCUS)
                if got and got > 0:
                    self._focus._position = int(got)


class _V4L2Focus(FocusAxis):
    """The focus motor behind `V4L2_CID_FOCUS_ABSOLUTE`.

    Writes go through the backend's control queue, so they land on the
    reader thread between frames like every other control, and the
    position read back is the one last asked for.
    """

    def __init__(self, backend: V4L2Backend, control: dict) -> None:
        self._backend = backend
        self._range = FocusRange(control["min"], control["max"],
                                 max(1, control.get("step") or 1))
        self._position = control.get("default", control["min"])

    def range(self) -> FocusRange:
        return self._range

    def get(self) -> int:
        return self._position

    def set(self, position: int) -> None:
        position = max(self._range.lo, min(self._range.hi, int(position)))
        self._position = position
        self._backend._queue("focus", position)

    def set_auto(self, on: bool) -> None:
        self._backend._queue("focus_auto", bool(on))
