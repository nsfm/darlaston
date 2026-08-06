"""Everything darlaston can see, across every way of seeing it.

Three access models are in play and they have nothing in common:

  * **ToupTek and its rebadges** speak a vendor SDK, loaded at runtime.
  * **UVC cameras** speak V4L2, and are enumerated through `/dev/video*`.
  * **XIMEA** speaks a *different* vendor SDK and has no V4L2 node at all.

A person does not care about any of that. They care which of the things
on the bench is the one pointed down a microscope. So this flattens all
three into one list of `Camera` records with one kind of key, and the
rest of the application asks here rather than knowing about backends.

**Nothing is hidden.** It is tempting to filter out the laptop's own
webcam, or the infrared sensor beside it, on the grounds that nobody
photographs a diatom with either. The trouble is that any rule confident
enough to hide a device will one day hide the right one, and the
recovery from that is a command-line flag -- which is the thing this
exists to remove. So everything is listed, ordered so the likely answer
is first, and described well enough that the wrong one is obviously
wrong.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

#: A key that survives a replug. Not a serial: measured on three cameras,
#: one reports "0000", one reports nothing at all, and only the vendor
#: SDK cameras have a real one. The USB port is what is left, and it
#: holds across reboot for as long as the cable stays in the same socket.
#: `kind` disambiguates the two vendor SDKs, which have their own ideas.


@dataclass(frozen=True)
class Camera:
    """One thing that could be pointed at a slide."""

    key: str                       #: stable across replug; see above
    kind: str                      #: "toupcam" | "v4l2" | "ximea" | "mock"
    name: str                      #: what to call it in a list
    width: int = 0
    height: int = 0
    #: How many settings the device really exposes. Zero is a real
    #: answer, verified on a camera whose USB descriptors declare no
    #: controls at all, and it is the difference between an exposure
    #: slider that works and one that lies.
    controls: int = 0
    #: True when the device can hand us frames that have not been through
    #: a lossy encoder.
    uncompressed: bool = False
    detail: str = ""               #: one line, for underneath the name
    node: str = ""                 #: /dev/videoN, where that applies
    #: A hash of what the device says about itself: its name, formats,
    #: sizes and control names. Identifies a *model in a configuration*,
    #: not an instance -- two identical cameras share one. Its job is to
    #: recognise a camera whose cable has been moved, where the port key
    #: no longer matches and the device would otherwise be a stranger.
    fingerprint: str = ""

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1e6


def _v4l2() -> list[Camera]:
    if not sys.platform.startswith("linux"):
        return []
    try:
        from .v4l2 import enumerate_cameras
    except Exception:
        return []
    out = []
    for found in enumerate_cameras():
        w, h = found["sizes"][0] if found["sizes"] else (0, 0)
        usable = found.get("usable") or []
        uncompressed = any(f.strip() != "MJPG" for f in usable)
        out.append(Camera(
            key=f"v4l2:{found.get('key') or found['node']}",
            kind="v4l2",
            name=found["card"],
            width=w, height=h,
            controls=len(found.get("controls") or []),
            uncompressed=uncompressed,
            detail="/".join(usable),
            node=found["node"],
            fingerprint=found.get("fingerprint", ""),
        ))
    return out


def _toupcam() -> list[Camera]:
    """Anything from the ToupTek family, if the SDK is installed.

    Failure is silence: the SDK not being present is the ordinary state
    of a machine that has never had one of these cameras attached, and it
    is reported properly elsewhere when somebody actually chooses one.
    """
    try:
        from .toupcam import load_sdk
        sdk = load_sdk()
        devices = sdk.Toupcam.EnumV2()
    except Exception:
        return []
    out = []
    for i, dev in enumerate(devices):
        name = getattr(dev, "displayname", None) or f"camera {i + 1}"
        model = getattr(dev, "model", None)
        w = getattr(model, "res", [None])[0].width if model else 0
        h = getattr(model, "res", [None])[0].height if model else 0
        out.append(Camera(
            key=f"toupcam:{getattr(dev, 'id', i)}",
            kind="toupcam", name=str(name),
            width=int(w or 0), height=int(h or 0),
            controls=99,               # a real SDK; not a V4L2 count
            uncompressed=True,
            detail="raw capable",
        ))
    return out


#: What XIMEA's library is called, per platform. Loaded by name and
#: never shipped: the same rule as every other vendor SDK here.
_XIMEA_LIBS = {
    "linux": ("libm3api.so.2", "libm3api.so"),
    "darwin": ("libm3api.dylib",
               "/Library/Frameworks/m3api.framework/Versions/Current/m3api"),
    "win32": ("xiapi64.dll", "xiapi32.dll"),
}

#: XIMEA's USB vendor id, for noticing a camera whose SDK is absent.
XIMEA_VENDOR = "20f7"


def _load_ximea():
    """The XIMEA library, or the reason it would not load.

    Returns (library, None) or (None, reason). The reason matters: a
    vendor SDK that is installed and unloadable is a different problem
    from one that was never installed, and the two need different
    advice. Measured here: `libm3api.so.2` links against `libtiff.so.5`
    and this distribution ships version 6, so the SDK is present, the
    camera is attached, and nothing works.
    """
    import ctypes

    names = _XIMEA_LIBS.get(sys.platform, _XIMEA_LIBS["linux"])
    missing = True
    for name in names:
        try:
            return ctypes.CDLL(name), None
        except OSError as why:
            text = str(why)
            # "cannot open shared object file" for the library itself
            # means it is simply not installed. Anything else -- an
            # undefined symbol, a dependency it cannot find -- means it
            # is installed and broken, which is worth saying out loud.
            if name.split("/")[-1] not in text:
                missing = False
                reason = text
    return None, (None if missing else reason)


def ximea_attached() -> bool:
    """Is there a XIMEA on the bus, whatever the SDK thinks?

    Linux only, from sysfs, the same way `usb.present` answers for the
    ToupTek family. There is no way to ask this on macOS or Windows
    without a USB dependency we do not have, so elsewhere the answer is
    False and the SDK's own opinion is all we get. That is a smaller
    loss than it sounds: the case this catches is a camera attached with
    no SDK installed, and on the platforms where we cannot see it, the
    person is told the SDK is missing rather than told nothing.
    """
    if not sys.platform.startswith("linux"):
        return False
    from pathlib import Path

    for entry in Path("/sys/bus/usb/devices").glob("*/idVendor"):
        try:
            if entry.read_text().strip() == XIMEA_VENDOR:
                return True
        except OSError:
            continue
    return False


def trouble() -> list[str]:
    """Cameras we can see but cannot reach, and why.

    Not errors and not offers. A camera that is plugged in and unusable
    is the single most confusing state a person can be in, because the
    application looks like it simply has not noticed. Saying so is most
    of the fix.
    """
    said = []
    library, reason = _load_ximea()
    if library is None:
        if reason:
            said.append(f"A XIMEA camera library is installed but will not "
                        f"load: {reason}")
        elif ximea_attached():
            said.append("A XIMEA camera is attached, but its library is not "
                        "installed. darlaston loads it at runtime and never "
                        "ships it.")
    return said


def _ximea() -> list[Camera]:
    """XIMEA, which has no V4L2 node and needs its own library.

    Deliberately last and deliberately forgiving. `libm3api` is
    frequently installed but unloadable -- it links against libraries a
    distribution has since moved past, which is the ordinary fate of a
    vendor SDK -- and a camera we cannot load is not a camera we should
    offer.
    """
    import ctypes

    xi, _why = _load_ximea()
    if xi is None:
        return []
    try:
        xi.xiGetNumberDevices.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        count = ctypes.c_uint32(0)
        if xi.xiGetNumberDevices(ctypes.byref(count)) != 0 or not count.value:
            return []
        xi.xiGetDeviceInfoString.argtypes = [ctypes.c_uint32, ctypes.c_char_p,
                                             ctypes.c_char_p, ctypes.c_uint32]
    except Exception:
        return []

    out = []
    for index in range(count.value):
        def ask(prm: str) -> str:
            buf = ctypes.create_string_buffer(256)
            ok = xi.xiGetDeviceInfoString(index, prm.encode(), buf, 256)
            return buf.value.decode("ascii", "replace") if ok == 0 else ""
        serial = ask("device_sn")
        out.append(Camera(
            key=f"ximea:{serial or index}",
            kind="ximea",
            name=ask("device_name") or "XIMEA camera",
            controls=99,
            uncompressed=True,
            detail=ask("device_type"),
        ))
    return out


def look() -> list[Camera]:
    """Every camera on the machine, the likeliest answer first.

    Ordered by what a person would reach for: something that can hand us
    uncompressed frames beats something that cannot, and after that the
    bigger sensor wins. Neither is a filter -- an infrared face-unlock
    sensor stays in the list, at the bottom, named as itself.
    """
    found = _toupcam() + _ximea() + _v4l2()
    found.sort(key=lambda c: (c.uncompressed, c.controls > 0,
                              c.width * c.height), reverse=True)
    return found


def backend_for(camera: Camera):
    """The thing that actually opens this camera.

    One place that knows which access model goes with which kind, so
    nothing above here has to.
    """
    if camera.kind == "v4l2":
        from .v4l2 import V4L2Backend
        return V4L2Backend(node=camera.node)
    if camera.kind == "toupcam":
        from .toupcam import ToupcamBackend
        return ToupcamBackend()
    if camera.kind == "mock":
        from .mock import MockCamera
        return MockCamera(fps=30.0)
    raise NotImplementedError(
        f"no backend for a {camera.kind} camera yet")


def choose(cameras: list[Camera], remembered: str = "",
           fingerprint: str = "") -> Camera | None:
    """Which one to open, or None when a person has to say.

    Three questions, in order of how sure they make us:

    1. **The same port.** Nothing has moved; this is the camera.
    2. **The same fingerprint, and only one of it.** The cable has been
       moved to another socket. The port key no longer matches, but a
       camera publishing exactly the same name, formats, sizes and
       controls, with no other candidate, is that camera -- and treating
       it as a stranger would orphan everything written about it.
    3. **Only one camera at all.** Not a choice, and asking would be
       ceremony.

    Otherwise None: more than one candidate is a real question, and
    guessing is how somebody ends up photographing their own face for ten
    minutes before working out why.
    """
    if remembered:
        for camera in cameras:
            if camera.key == remembered:
                return camera
    if fingerprint:
        same = [c for c in cameras if c.fingerprint == fingerprint]
        if len(same) == 1:
            return same[0]
    return cameras[0] if len(cameras) == 1 else None
