"""Ordinary USB cameras on Windows, through DirectShow.

The Windows twin of `v4l2.py`: the same class of camera, the same
honesty about what it delivers (8-bit colour the bridge chip has already
made every decision about), the same shared streaming loop. What differs
is only the asking -- there is no `/dev/video*` to read, so the device
list, the formats, the controls and a key that survives a replug all come
from COM.

**Why DirectShow and not Media Foundation.** OpenCV offers both. Its
DirectShow path enumerates devices through `ICreateDevEnum` on the video
input category, in enumeration order, and so does this file -- so the
index it opens is the index we described, by construction rather than by
luck. Media Foundation keeps a different order, cannot see a DirectShow
virtual camera at all (which is how this gets tested on a machine with
no camera), and offers nothing more for a UVC device than the older
interface does. The controls go through `IAMCameraControl` and
`IAMVideoProcAmp` either way; that is the interface ToupTek's own
"autofocus" library turns out to use, measured by the GUIDs in its
binary, and it is where a focus motor is reached on this platform.

**What is written here and why it is ctypes.** No COM helper library is
a dependency of this project, and the three interfaces needed are small:
a device enumerator, a property bag, and a stream-config/camera-control
pair on the device filter. Each vtable is spelled out in full below with
its index, because an off-by-one in a COM vtable does not raise -- it
calls the wrong function with the right arguments and the failure, if
there is one, is somewhere else entirely.

Nothing in this module runs on a platform without `ole32`: the import is
harmless anywhere, and `enumerate_cameras` answers an empty list rather
than raising where there is no COM to ask.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import math
import re
import sys
import threading
from ctypes import (POINTER, Structure, byref, c_int, c_int32, c_ubyte,
                    c_uint32, c_ushort, c_void_p, c_wchar_p, cast)

import cv2

from .base import CameraInfo, FocusAxis, FocusRange, Resolution
from .cvcam import CvCaptureBackend, usable_formats
from .errors import CameraBusy, NoCameraFound

_log = logging.getLogger(__name__)

#: The calling convention COM uses. `WINFUNCTYPE` only exists on
#: Windows; elsewhere the module still imports, for the tests.
_FUNC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
#: Windows LONG is 32 bits on every architecture; ctypes' c_long is not
#: (it is 64 on Linux), and this module is tested where it does not run.
#: Every Windows integer here is spelled at its fixed width.
HRESULT = c_int32

S_OK = 0
S_FALSE = 1
#: CoInitializeEx on a thread already initialised the other way. Not a
#: failure: the thread has COM, just somebody else's flavour of it.
RPC_E_CHANGED_MODE = -2147417850
COINIT_APARTMENTTHREADED = 0x2

#: USB vendor ids the ToupTek family answers under. The same list as
#: `usb.py`, repeated here only because that module reads sysfs.
TOUPTEK_VIDS = ("0547", "a547", "04b4", "16d0", "9745", "0549")


class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_ushort), ("Data3", c_ushort),
                ("Data4", c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        """From the `{xxxxxxxx-xxxx-...}` spelling the headers use."""
        import uuid

        raw = uuid.UUID(text.strip("{}")).bytes_le
        out = cls()
        ctypes.memmove(byref(out), raw, 16)
        return out

    @property
    def raw(self) -> bytes:
        """The sixteen bytes, for comparing. `bytes()` on a Structure is
        not reliable across Python versions; `string_at` is."""
        return ctypes.string_at(byref(self), 16)

    def __str__(self) -> str:
        import uuid

        return str(uuid.UUID(bytes_le=self.raw))


# ---- the names ------------------------------------------------------------
#
# Each GUID here was checked against the Windows SDK headers (strmif.h,
# uuids.h, objidl.h) rather than remembered.

CLSID_SystemDeviceEnum = GUID.of("62BE5D10-60EB-11d0-BD3B-00A0C911CE86")
CLSID_VideoInputDeviceCategory = GUID.of("860BB310-5D01-11d0-BD3B-00A0C911CE86")
IID_ICreateDevEnum = GUID.of("29840822-5B84-11D0-BD3B-00A0C911CE86")
IID_IPropertyBag = GUID.of("55272A00-42CB-11CE-8135-00AA004BB851")
IID_IBaseFilter = GUID.of("56a86895-0ad4-11ce-b03a-0020af0ba770")
IID_IAMStreamConfig = GUID.of("C6E13340-30AC-11d0-A18C-00A0C9118956")
IID_IAMCameraControl = GUID.of("C6E13370-30AC-11d0-A18C-00A0C9118956")
IID_IAMVideoProcAmp = GUID.of("C6E13360-30AC-11d0-A18C-00A0C9118956")

FORMAT_VideoInfo = GUID.of("05589f80-c356-11ce-bf01-00aa0055595a")
FORMAT_VideoInfo2 = GUID.of("f72a76A0-eb0a-11d0-ace4-0000c0cc16ba")
MEDIASUBTYPE_RGB24 = GUID.of("e436eb7d-524f-11ce-9f53-0020af0ba770")
MEDIASUBTYPE_RGB32 = GUID.of("e436eb7e-524f-11ce-9f53-0020af0ba770")
MEDIASUBTYPE_ARGB32 = GUID.of("773c9ac0-3274-11d0-b724-00aa006c1a01")
#: Every fourcc-style subtype shares this tail; Data1 is the fourcc.
_FOURCC_TAIL = GUID.of("00000000-0000-0010-8000-00AA00389B71")

# IAMCameraControl properties and flags, from strmif.h.
CameraControl_Exposure = 4
CameraControl_Focus = 6
CameraControl_Flags_Auto = 0x1
CameraControl_Flags_Manual = 0x2
# IAMVideoProcAmp properties and flags.
VideoProcAmp_WhiteBalance = 7
VideoProcAmp_Backlight = 8
VideoProcAmp_Gain = 9
VideoProcAmp_Flags_Auto = 0x1
VideoProcAmp_Flags_Manual = 0x2

PINDIR_OUTPUT = 1

# ---- vtables ----------------------------------------------------------------
#
# Slot numbers. Every COM interface starts with IUnknown's three, and an
# interface that inherits another counts its parent's slots first.

_QUERY_INTERFACE, _ADD_REF, _RELEASE = 0, 1, 2
# ICreateDevEnum : IUnknown
_CREATE_CLASS_ENUMERATOR = 3
# IEnumMoniker : IUnknown -- Next, Skip, Reset, Clone
_ENUM_NEXT = 3
# IMoniker : IPersistStream : IPersist : IUnknown
#   IPersist adds GetClassID (3); IPersistStream adds IsDirty, Load,
#   Save, GetSizeMax (4..7); IMoniker then starts at 8.
_MONIKER_BIND_TO_OBJECT = 8
_MONIKER_BIND_TO_STORAGE = 9
_MONIKER_GET_DISPLAY_NAME = 20
# IPropertyBag : IUnknown -- Read, Write
_BAG_READ = 3
# IBaseFilter : IMediaFilter : IPersist : IUnknown
#   GetClassID (3); Stop, Pause, Run, GetState, SetSyncSource,
#   GetSyncSource (4..9); EnumPins (10), FindPin, QueryFilterInfo,
#   JoinFilterGraph, QueryVendorInfo.
_FILTER_ENUM_PINS = 10
# IEnumPins : IUnknown -- Next, Skip, Reset, Clone
_PINS_NEXT = 3
# IPin : IUnknown -- Connect, ReceiveConnection, Disconnect, ConnectedTo,
#   ConnectionMediaType, QueryPinInfo, QueryDirection (9), ...
_PIN_QUERY_DIRECTION = 9
# IAMStreamConfig : IUnknown -- SetFormat, GetFormat,
#   GetNumberOfCapabilities (5), GetStreamCaps (6)
_CONFIG_COUNT = 5
_CONFIG_CAPS = 6
# IAMCameraControl / IAMVideoProcAmp : IUnknown -- GetRange (3), Set (4),
#   Get (5). Identical shapes; only the property numbers differ.
_CTRL_GET_RANGE = 3
_CTRL_SET = 4
_CTRL_GET = 5


class AM_MEDIA_TYPE(Structure):
    _fields_ = [("majortype", GUID), ("subtype", GUID),
                ("bFixedSizeSamples", c_int), ("bTemporalCompression", c_int),
                ("lSampleSize", c_uint32), ("formattype", GUID),
                ("pUnk", c_void_p), ("cbFormat", c_uint32),
                ("pbFormat", c_void_p)]


class VARIANT(Structure):
    """Only as much of a VARIANT as reading a BSTR needs.

    The real union is 16 bytes on x64 after an 8-byte header; a pointer
    at offset 8 is where a BSTR lives, which is the only member used.
    """
    _fields_ = [("vt", c_ushort), ("_r1", c_ushort), ("_r2", c_ushort),
                ("_r3", c_ushort), ("pointer", c_void_p), ("_pad", c_void_p)]


VT_BSTR = 8


def _ole32():
    """ole32 with HRESULT checking: a failed call raises OSError."""
    return ctypes.oledll.ole32 if hasattr(ctypes, "oledll") else None


def _free(ptr) -> None:
    """CoTaskMemFree returns void, so it must not go through the
    HRESULT-checking loader, which would read garbage as a failure."""
    if ptr:
        ctypes.windll.ole32.CoTaskMemFree(ptr)


def _oleaut32():
    return ctypes.oledll.oleaut32 if hasattr(ctypes, "oledll") else None


class _Com:
    """A COM interface pointer, with its vtable callable by slot.

    Owns one reference and releases it when told. Nothing clever:
    `call(slot, argtypes, *args)` builds the function type on the spot
    and invokes it with `this` prepended, raising OSError on a failed
    HRESULT so callers can `try` around the device instead of checking
    every return.
    """

    def __init__(self, ptr: int | None) -> None:
        self.ptr = ptr

    def __bool__(self) -> bool:
        return bool(self.ptr)

    def _slot(self, index: int) -> int:
        vtable = cast(c_void_p(self.ptr), POINTER(POINTER(c_void_p)))[0]
        return vtable[index]

    def call(self, slot: int, argtypes: tuple, *args):
        proto = _FUNC(HRESULT, c_void_p, *argtypes)
        hr = proto(self._slot(slot))(self.ptr, *args)
        if hr < 0:
            raise OSError(hr, f"COM call {slot} failed with 0x{hr & 0xffffffff:08x}")
        return hr

    def query(self, iid: GUID) -> "_Com":
        out = c_void_p()
        self.call(_QUERY_INTERFACE, (POINTER(GUID), POINTER(c_void_p)),
                  byref(iid), byref(out))
        return _Com(out.value)

    def release(self) -> None:
        if self.ptr:
            try:
                _FUNC(c_uint32, c_void_p)(self._slot(_RELEASE))(self.ptr)
            except Exception:
                pass
            self.ptr = None


class _Apartment:
    """COM initialised for the duration, on this thread, balanced.

    A thread already initialised -- Qt's main thread is, as an
    apartment -- answers S_FALSE or RPC_E_CHANGED_MODE; both mean COM
    is available and only S_OK/S_FALSE want the matching uninitialise.
    """

    def __enter__(self) -> bool:
        ole = _ole32()
        if ole is None:
            return False
        try:
            hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        except OSError:
            return False
        self._undo = hr in (S_OK, S_FALSE)
        return True

    def __exit__(self, *_exc) -> None:
        if getattr(self, "_undo", False):
            try:
                ctypes.windll.ole32.CoUninitialize()
            except OSError:
                pass


def _create(clsid: GUID, iid: GUID) -> _Com:
    out = c_void_p()
    CLSCTX_INPROC_SERVER = 0x1
    _ole32().CoCreateInstance(byref(clsid), None, c_uint32(CLSCTX_INPROC_SERVER),
                              byref(iid), byref(out))
    return _Com(out.value)


def _bag_string(bag: _Com, name: str) -> str:
    """One string property off a device moniker's property bag, or ""."""
    var = VARIANT()
    try:
        bag.call(_BAG_READ, (c_wchar_p, POINTER(VARIANT), c_void_p),
                 name, byref(var), None)
    except OSError:
        return ""
    try:
        if var.vt == VT_BSTR and var.pointer:
            return ctypes.wstring_at(var.pointer)
        return ""
    finally:
        try:
            _oleaut32().VariantClear(byref(var))
        except Exception:
            pass


#: What OpenCV's DirectShow backend wants in CAP_PROP_FOURCC for the two
#: subtypes that have no fourcc: it matches the value against each
#: subtype's Data1, and RGB24's is not four letters. Asking for a code
#: it does not know stops the device and leaves it stopped.
_RGB_DATA1 = {"RGB3": MEDIASUBTYPE_RGB24.Data1, "RGB4": MEDIASUBTYPE_RGB32.Data1}


def _subtype_name(guid: GUID) -> str:
    """A fourcc-style name for a media subtype: DirectShow's own four
    letters where it has them (YUY2, MJPG, NV12), and RGB3/RGB4 for the
    two that are GUIDs rather than codes."""
    if guid.raw == MEDIASUBTYPE_RGB24.raw:
        return "RGB3"
    if guid.raw in (MEDIASUBTYPE_RGB32.raw, MEDIASUBTYPE_ARGB32.raw):
        return "RGB4"
    if guid.raw[4:] == _FOURCC_TAIL.raw[4:]:
        return "".join(chr((guid.Data1 >> (8 * i)) & 0xFF) for i in range(4))
    return str(guid)[:8]


def _free_media_type(pmt) -> None:
    if not pmt:
        return
    mt = pmt.contents
    if mt.cbFormat and mt.pbFormat:
        _free(c_void_p(mt.pbFormat))
    if mt.pUnk:
        _Com(mt.pUnk).release()
    _free(cast(pmt, c_void_p))


def _stream_caps(filt: _Com) -> tuple[list[str], list[tuple[int, int]]]:
    """Formats and frame sizes off the filter's first output pin.

    Read from `IAMStreamConfig` on an unconnected pin, which loads the
    driver but starts nothing -- so a camera another program is already
    streaming from can still be described, which is the case the
    picker is open for.
    """
    formats: list[str] = []
    sizes: set[tuple[int, int]] = set()
    pins = _Com(None)
    try:
        pins_ptr = c_void_p()
        filt.call(_FILTER_ENUM_PINS, (POINTER(c_void_p),), byref(pins_ptr))
        pins = _Com(pins_ptr.value)
        while True:
            pin_ptr, fetched = c_void_p(), c_uint32(0)
            hr = pins.call(_PINS_NEXT, (c_uint32, POINTER(c_void_p), POINTER(c_uint32)),
                           1, byref(pin_ptr), byref(fetched))
            if hr != S_OK or not fetched.value:
                break
            pin = _Com(pin_ptr.value)
            try:
                direction = c_int(0)
                pin.call(_PIN_QUERY_DIRECTION, (POINTER(c_int),), byref(direction))
                if direction.value != PINDIR_OUTPUT:
                    continue
                try:
                    config = pin.query(IID_IAMStreamConfig)
                except OSError:
                    continue
                try:
                    _read_caps(config, formats, sizes)
                finally:
                    config.release()
                if sizes:
                    break
            finally:
                pin.release()
    except OSError as why:
        _log.debug("stream caps unavailable: %s", why)
    finally:
        pins.release()
    return formats, sorted(sizes, reverse=True)


def _read_caps(config: _Com, formats: list[str], sizes: set) -> None:
    count, size = c_int(0), c_int(0)
    config.call(_CONFIG_COUNT, (POINTER(c_int), POINTER(c_int)),
                byref(count), byref(size))
    scc = ctypes.create_string_buffer(max(size.value, 128))
    for i in range(count.value):
        pmt = POINTER(AM_MEDIA_TYPE)()
        try:
            config.call(_CONFIG_CAPS, (c_int, POINTER(POINTER(AM_MEDIA_TYPE)), c_void_p),
                        i, byref(pmt), cast(scc, c_void_p))
        except OSError:
            continue
        try:
            mt = pmt.contents
            name = _subtype_name(mt.subtype)
            if name not in formats:
                formats.append(name)
            # VIDEOINFOHEADER keeps its BITMAPINFOHEADER at byte 48,
            # VIDEOINFOHEADER2 at 72; biWidth and biHeight follow biSize.
            if mt.formattype.raw == FORMAT_VideoInfo.raw:
                at = 48
            elif mt.formattype.raw == FORMAT_VideoInfo2.raw:
                at = 72
            else:
                continue
            if mt.cbFormat >= at + 12 and mt.pbFormat:
                w = ctypes.c_int32.from_address(mt.pbFormat + at + 4).value
                h = abs(ctypes.c_int32.from_address(mt.pbFormat + at + 8).value)
                if w > 0 and h > 0:
                    sizes.add((w, h))
        finally:
            _free_media_type(pmt)


#: Every standard property, not just the four we drive. A vendor can
#: wire anything behind a standard property -- ToupTek's HDMI/USB hybrids
#: put the focus motor behind Backlight Compensation, the tester found,
#: so a plain UVC host can drive it with no extension unit -- and the
#: only way to see that is to ask for every range and read them.
_CONTROL_NAMES = {
    ("camera", 0): "Pan", ("camera", 1): "Tilt", ("camera", 2): "Roll",
    ("camera", 3): "Zoom", ("camera", CameraControl_Exposure): "Exposure",
    ("camera", 5): "Iris", ("camera", CameraControl_Focus): "Focus",
    ("amp", 0): "Brightness", ("amp", 1): "Contrast", ("amp", 2): "Hue",
    ("amp", 3): "Saturation", ("amp", 4): "Sharpness", ("amp", 5): "Gamma",
    ("amp", 6): "ColorEnable", ("amp", VideoProcAmp_WhiteBalance): "WhiteBalance",
    ("amp", VideoProcAmp_Backlight): "Backlight", ("amp", VideoProcAmp_Gain): "Gain",
}

#: A Backlight Compensation whose range is wider than this is not a
#: backlight compensation -- a real one is 0..1 or 0..2 -- and on a
#: ToupTek device it is the focus motor: 0..854 on the X5FCAM4K8MPA.
FOCUS_BEHIND_BACKLIGHT_SPAN = 100


def _control_ranges(filt: _Com) -> list[dict]:
    """The controls this device really has, with their limits.

    Asked one by one, because a device that lacks a control says so by
    failing `GetRange` for it -- and a virtual camera lacks all four.
    Same recipe as the V4L2 control walk: capability is asked for, per
    device, never assumed from the class of device.
    """
    found = []
    for (which, prop), name in _CONTROL_NAMES.items():
        iid = IID_IAMCameraControl if which == "camera" else IID_IAMVideoProcAmp
        try:
            ctrl = filt.query(iid)
        except OSError:
            continue
        try:
            lo, hi, step, default, caps = (c_int32(), c_int32(), c_int32(),
                                           c_int32(), c_int32())
            ctrl.call(_CTRL_GET_RANGE,
                      (c_int32, POINTER(c_int32), POINTER(c_int32),
                       POINTER(c_int32), POINTER(c_int32), POINTER(c_int32)),
                      prop, byref(lo), byref(hi), byref(step), byref(default),
                      byref(caps))
        except OSError:
            continue
        finally:
            ctrl.release()
        if hi.value <= lo.value:
            continue
        found.append({"name": name, "which": which, "prop": prop,
                      "min": lo.value, "max": hi.value,
                      "step": max(1, step.value), "default": default.value,
                      "flags": caps.value})
    return found


def _key_from(path: str, display: str, name: str) -> str:
    """Something that survives a replug, and is never shared by two devices.

    The device path of a real camera carries its USB position -- the
    instance id Windows derives from the hub and port -- and holds for as
    long as the cable stays in the socket; the interface GUID and
    `\\global` on the end are the same for every camera and go. A
    virtual camera has no path and only a name, and the moniker's
    display name is what Windows itself keys it by.
    """
    # Only a kernel device path is a position. A software filter may
    # still write *something* under DevicePath -- Unity Capture writes
    # "foo:bar" for every instance it registers -- and keying on that
    # would give every copy of it the same key.
    if path and path.startswith("\\\\?\\"):
        return re.sub(r"#\{[0-9a-f-]+\}.*$", "", path.lower())
    return display.lower() if display else f"name:{name}"


def _vid_pid(text: str) -> tuple[str, str]:
    found = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", text.lower())
    return (found.group(1), found.group(2)) if found else ("", "")


def fingerprint(found: dict) -> str:
    """Same recipe as the V4L2 one: what the device publishes about
    itself and a person cannot change. See `v4l2.fingerprint`."""
    parts = [
        found.get("name", ""),
        ",".join(sorted(found.get("formats") or [])),
        ",".join(f"{w}x{h}" for w, h in sorted(found.get("sizes") or [])),
        ",".join(sorted(c["name"] for c in (found.get("controls") or []))),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _walk(describe: bool, skip=frozenset(), before_deep=None,
          emit=None) -> list[dict]:
    """Every video input device, in DirectShow's own order.

    `describe` binds each device's filter to read formats, sizes and
    controls. Without it only the names and paths are read, which is
    cheap enough to poll. `skip` holds indexes to leave undescribed,
    `before_deep(index, name)` is told just before a filter is bound,
    and `emit(found)` is handed each device as it completes -- the
    probe child uses all three so that a filter which takes the process
    down leaves everything before it on record and its own name last.
    """
    out: list[dict] = []
    devenum = _create(CLSID_SystemDeviceEnum, IID_ICreateDevEnum)
    monikers = _Com(None)
    try:
        ptr = c_void_p()
        hr = devenum.call(_CREATE_CLASS_ENUMERATOR,
                          (POINTER(GUID), POINTER(c_void_p), c_uint32),
                          byref(CLSID_VideoInputDeviceCategory), byref(ptr), 0)
        if hr != S_OK or not ptr.value:
            return out               # S_FALSE: the category is empty
        monikers = _Com(ptr.value)
        index = 0
        while True:
            mon_ptr, fetched = c_void_p(), c_uint32(0)
            hr = monikers.call(_ENUM_NEXT,
                               (c_uint32, POINTER(c_void_p), POINTER(c_uint32)),
                               1, byref(mon_ptr), byref(fetched))
            if hr != S_OK or not fetched.value:
                break
            moniker = _Com(mon_ptr.value)
            try:
                found = _describe(moniker, index,
                                  describe and index not in skip, before_deep)
                out.append(found)
                if emit is not None:
                    emit(found)
            except OSError as why:
                _log.debug("device %d could not be described: %s", index, why)
            finally:
                moniker.release()
            index += 1
    finally:
        monikers.release()
        devenum.release()
    return out


def _describe(moniker: _Com, index: int, deep: bool, before_deep=None) -> dict:
    name = path = display = ""
    bag_ptr = c_void_p()
    moniker.call(_MONIKER_BIND_TO_STORAGE,
                 (c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p)),
                 None, None, byref(IID_IPropertyBag), byref(bag_ptr))
    bag = _Com(bag_ptr.value)
    try:
        name = _bag_string(bag, "FriendlyName")
        path = _bag_string(bag, "DevicePath")
    finally:
        bag.release()
    text = c_wchar_p()
    ctx = c_void_p()
    try:
        # With a bind context, as the documentation asks and FFmpeg
        # does; the argument is not marked optional.
        _ole32().CreateBindCtx(0, byref(ctx))
        moniker.call(_MONIKER_GET_DISPLAY_NAME,
                     (c_void_p, c_void_p, POINTER(c_wchar_p)),
                     ctx, None, byref(text))
        display = text.value or ""
        _free(cast(text, c_void_p))
    except OSError:
        pass
    finally:
        _Com(ctx.value).release()
    vid, pid = _vid_pid(path or display)
    found = {"index": index, "name": name or f"camera {index + 1}",
             "path": path, "display": display, "vid": vid, "pid": pid,
             "key": _key_from(path, display, name),
             "formats": [], "sizes": [], "controls": [], "raw": [],
             "described": bool(deep)}
    if deep:
        if before_deep is not None:
            before_deep(index, found["name"])
        filt_ptr = c_void_p()
        try:
            moniker.call(_MONIKER_BIND_TO_OBJECT,
                         (c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p)),
                         None, None, byref(IID_IBaseFilter), byref(filt_ptr))
        except OSError as why:
            # Named and listed, undescribed. A device whose driver will
            # not bind right now is still a device somebody can see.
            _log.debug("%r would not bind: %s", name, why)
        else:
            filt = _Com(filt_ptr.value)
            try:
                found["formats"], found["sizes"] = _stream_caps(filt)
                found["controls"] = _control_ranges(filt)
            finally:
                filt.release()
        found["usable"] = usable_formats(found["formats"])
        found["fingerprint"] = fingerprint(found)
    return found


def _walk_here(describe: bool) -> list[dict]:
    """`_walk`, in this process, with COM up for the duration."""
    with _Apartment() as ok:
        if not ok:
            return []
        try:
            return _walk(describe=describe)
        except Exception as why:
            _log.debug("DirectShow enumeration failed: %s", why)
            return []


#: Where the child gets its time budget. A device filter that will not
#: bind can hang rather than fail; the real cameras measured describe in
#: well under a second each.
PROBE_TIMEOUT = 20.0


def _probe_command(skip=()) -> list[str]:
    """How to run this program again as a probe, frozen or not."""
    base = ([sys.executable] if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "darlaston.ui.main"])
    out = base + ["--probe-dshow"]
    if skip:
        out += ["--probe-skip", ",".join(str(i) for i in sorted(skip))]
    return out


def probe_main(skip_text: str = "") -> int:
    """The child's half: describe everything, one device at a time.

    Everything goes out as it is learned. First every device shallowly,
    one JSON line each, so the names and keys are on record before any
    filter is bound; then each device described, one line each, with a
    "describing" note to stderr just before its filter is bound. A
    filter that takes this process down therefore leaves the parent
    with every device named, every earlier device described, and the
    culprit's name as the last thing said. faulthandler adds the Python
    stack to stderr for the log. Devices in `--probe-skip` are the ones
    a previous run was killed by; they are left undescribed on purpose.
    """
    import faulthandler
    import json

    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
    except Exception:
        pass
    skip = frozenset(int(i) for i in skip_text.split(",") if i.strip())

    def emit(found: dict) -> None:
        print(json.dumps(found), flush=True)

    def before_deep(index: int, name: str) -> None:
        print(f"describing: {index} {name}", file=sys.stderr, flush=True)

    with _Apartment() as ok:
        if not ok:
            return 0
        shallow = _walk(describe=False, emit=emit)
        print("probing: " + ", ".join(d["name"] for d in shallow),
              file=sys.stderr, flush=True)
        _walk(describe=True, skip=skip, before_deep=before_deep, emit=emit)
    return 0


#: A child that keeps dying on new devices is not worth more than this
#: many runs; whatever it has not described by then is listed by name.
PROBE_RUNS = 4

#: The last run's answer, briefly. Startup asks three times in two
#: seconds -- discovery, the first open's locate, the presence poll --
#: and each child run is a second or two on a machine with many devices.
PROBE_CACHE_SECONDS = 5.0
_probe_cache: tuple[float, list[dict]] | None = None

#: Devices, by name, whose filters have taken the probe down before. Set
#: from the settings at startup and added to as they are found, so a
#: known culprit is skipped on the first run rather than rediscovered
#: with a crash and a re-run every start. Names rather than indexes,
#: because indexes renumber with every plug and unplug.
skip_names: set[str] = set()
#: Told the name of each new culprit, so whoever owns the settings can
#: write it down. The camera layer does not know where settings live.
on_culprit = None


def _run_probe(skip) -> tuple[int, str, str] | None:
    """One child run: (exit code, stdout, stderr), or None if it never
    finished."""
    import subprocess

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(_probe_command(skip), capture_output=True,
                              text=True, timeout=PROBE_TIMEOUT,
                              creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as why:
        _log.warning("DirectShow probe did not finish: %s", why)
        return None
    return done.returncode, done.stdout or "", done.stderr or ""


def _culprit(stderr: str) -> int | None:
    """Which device the child was describing when it died, from the
    last "describing:" note it managed to print."""
    for line in reversed(stderr.strip().splitlines()):
        if line.startswith("describing: "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _describe_in_child() -> list[dict] | None:
    """Every device described, by child processes, as far as they get.

    The child prints one line per device as it learns it. If it dies,
    the lines it printed stand, the device it was describing is named
    in the log and skipped, and the child runs again for the rest -- a
    few times at most. Devices never described come back by name, with
    `described` False, so the rest of the program can rank them below
    anything that answered properly. None only when no child ever
    printed a device at all.
    """
    import json

    merged: dict[int, dict] = {}
    skip: set[int] = set()
    if skip_names:
        # Names to indexes needs the list, and the shallow walk binds no
        # filters, so it is safe to take here.
        for device in _walk_here(describe=False):
            if device["name"] in skip_names:
                skip.add(device["index"])
    for _attempt in range(PROBE_RUNS):
        got = _run_probe(skip)
        if got is None:
            break
        code, out, err = got
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                device = json.loads(line)
            except ValueError:
                continue
            if not isinstance(device, dict) or "index" not in device:
                continue
            device["sizes"] = [tuple(size) for size in device.get("sizes") or []]
            earlier = merged.get(device["index"])
            # A described line outranks a shallow one for the same device.
            if earlier is None or device.get("described") or not earlier.get("described"):
                merged[device["index"]] = device
        if code == 0:
            break
        culprit = _culprit(err)
        if culprit is None or culprit in skip:
            _log.warning("DirectShow probe exited %d before naming a device; "
                         "stderr: %s", code, err.strip()[-600:])
            break
        name = merged.get(culprit, {}).get("name", f"index {culprit}")
        _log.warning("describing %r (DirectShow index %d) took the probe down "
                     "with exit %d; it is listed by name only. The child's "
                     "last words: %s", name, culprit, code, err.strip()[-600:])
        skip.add(culprit)
        if name not in skip_names:
            skip_names.add(name)
            if on_culprit is not None:
                try:
                    on_culprit(name)
                except Exception:
                    _log.debug("culprit not recorded", exc_info=True)
    if not merged:
        return None
    for device in merged.values():
        if not device.get("described"):
            device.setdefault("usable", [])
            device.setdefault("fingerprint", "")
    return [merged[i] for i in sorted(merged)]


def enumerate_cameras() -> list[dict]:
    """Every camera DirectShow can see, described. Empty off Windows.

    The describing -- binding each device's filter to read its formats
    and controls -- happens in a child process. A vendor's filter is
    somebody else's code running in ours, and one that faults takes the
    whole process with it before a window exists, which is exactly how
    the first Windows report read. When the child cannot answer, the
    devices are still listed by name from here, undescribed; opening one
    then binds its filter in earnest, where a failure is at least an
    error with a camera's name on it.
    """
    global _probe_cache
    if not sys.platform.startswith("win"):
        return []
    import time

    now = time.monotonic()
    if _probe_cache is not None and now - _probe_cache[0] < PROBE_CACHE_SECONDS:
        return [dict(d) for d in _probe_cache[1]]
    found = _describe_in_child()
    if found is None:
        found = _walk_here(describe=False)
        for d in found:
            d.setdefault("usable", [])
            d.setdefault("fingerprint", "")
            d.setdefault("described", False)
    _probe_cache = (now, found)
    return [dict(d) for d in found]


def present(key: str) -> bool:
    """Is the camera with this key still on the bus? Names only; cheap."""
    if not sys.platform.startswith("win"):
        return True
    try:
        return any(d["key"] == key for d in _walk_here(describe=False))
    except Exception:
        return True


def _bind(display: str) -> _Com:
    """The device filter for a moniker display name, freshly bound."""
    ole = _ole32()
    ctx, moniker, eaten = c_void_p(), c_void_p(), c_uint32(0)
    ole.CreateBindCtx(0, byref(ctx))
    bind_ctx = _Com(ctx.value)
    try:
        # Typed, every one. A bare Python int reaches a ctypes call as a
        # 32-bit C int, which on x64 is half a pointer.
        ole.MkParseDisplayName(c_void_p(bind_ctx.ptr), c_wchar_p(display),
                               byref(eaten), byref(moniker))
        mon = _Com(moniker.value)
        try:
            out = c_void_p()
            mon.call(_MONIKER_BIND_TO_OBJECT,
                     (c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p)),
                     bind_ctx.ptr, None, byref(IID_IBaseFilter), byref(out))
            return _Com(out.value)
        finally:
            mon.release()
    finally:
        bind_ctx.release()


class _Controls:
    """The device's camera controls, reached beside the stream rather
    than through it.

    OpenCV streams the picture; the controls are written through our own
    handle on the same device filter, because `IAMCameraControl` says
    exactly what a write means (which property, manual or automatic)
    where `cv2.set` has to be guessed at per backend. A control written
    through a second filter instance reaches the device all the same:
    the property lives in the driver, not in the graph.

    Only for kernel devices (`@device:pnp:`). A software filter keeps
    its state per instance, so a write to a second copy of it reaches
    nothing; those go through `cv2.set` and the graph's own filter.

    One handle, bound on first use from whichever thread asks, and used
    from every thread after. That leans on the kernel-streaming proxy
    being registered `ThreadingModel=Both`, which it is: the pointers
    are direct, no apartment marshalling is in play, and OpenCV's own
    DirectShow code made the same cross-thread calls for a decade. A
    handle per thread was the alternative and leaked a device handle
    per stream start, because every restart is a new reader thread.
    """

    def __init__(self, display: str) -> None:
        self._display = display
        self._filter: _Com | None = None
        self._cam: _Com | None = None
        self._amp: _Com | None = None
        self._failed = False
        self._lock = threading.Lock()

    def _here(self) -> tuple[_Com | None, _Com | None]:
        with self._lock:
            if self._filter is not None or self._failed:
                return self._cam, self._amp
            try:
                ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
                filt = _bind(self._display)
            except OSError as why:
                # Remembered, so a camera whose controls will not bind
                # does not pay for a bind attempt on every slider move.
                _log.debug("controls for %r would not bind: %s", self._display, why)
                self._failed = True
                return None, None
            self._filter = filt
            try:
                self._cam = filt.query(IID_IAMCameraControl)
            except OSError:
                pass
            try:
                self._amp = filt.query(IID_IAMVideoProcAmp)
            except OSError:
                pass
            return self._cam, self._amp

    def set(self, which: str, prop: int, value: int, auto: bool = False) -> bool:
        cam, amp = self._here()
        ctrl = cam if which == "camera" else amp
        if ctrl is None:
            return False
        flags = ((CameraControl_Flags_Auto if auto else CameraControl_Flags_Manual)
                 if which == "camera" else
                 (VideoProcAmp_Flags_Auto if auto else VideoProcAmp_Flags_Manual))
        try:
            ctrl.call(_CTRL_SET, (c_int32, c_int32, c_int32), prop, int(value), flags)
            return True
        except OSError as why:
            _log.debug("%s control %d refused %d: %s", which, prop, value, why)
            return False

    def get(self, which: str, prop: int) -> int | None:
        cam, amp = self._here()
        ctrl = cam if which == "camera" else amp
        if ctrl is None:
            return None
        value, flags = c_int32(), c_int32()
        try:
            ctrl.call(_CTRL_GET, (c_int32, POINTER(c_int32), POINTER(c_int32)),
                      prop, byref(value), byref(flags))
            return value.value
        except OSError:
            return None

    def close(self) -> None:
        with self._lock:
            for com in (self._cam, self._amp, self._filter):
                if com is not None:
                    com.release()
            self._cam = self._amp = self._filter = None


def exposure_to_us(log2_seconds: int) -> int:
    """DirectShow exposure is log2 of seconds: -13 is 1/8192 s, 1 is 2 s."""
    return max(1, int(round((2.0 ** int(log2_seconds)) * 1e6)))


def us_to_exposure(microseconds: int) -> int:
    return int(round(math.log2(max(1, int(microseconds)) / 1e6)))


class _DshowFocus(FocusAxis):
    """The focus motor behind a DirectShow property.

    Usually `IAMCameraControl` Focus. On ToupTek's HDMI/USB hybrids it is
    `IAMVideoProcAmp` Backlight Compensation, wearing a range no
    backlight compensation has; `found` says which, and the writes go
    wherever it says.
    """

    def __init__(self, backend: "DshowBackend", found: dict) -> None:
        self._backend = backend
        self._range = FocusRange(found["min"], found["max"], found["step"])
        self._position = found["default"]
        self.which, self.prop = found["which"], found["prop"]
        self.behind = found["name"]

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


class DshowBackend(CvCaptureBackend):
    """A UVC camera on Windows. The asking is here; the streaming is shared."""

    THREAD_NAME = "dshow-camera"

    def __init__(self, index: int = 0, key: str = "",
                 fingerprint: str = "") -> None:
        super().__init__()
        self._index = index
        self._key = key
        self._fingerprint = fingerprint
        self._controls: dict = {}
        self._found: dict | None = None
        self._device: _Controls | None = None
        self._focus: _DshowFocus | None = None
        #: The format the graph should be built with. Re-applied after
        #: every size change, because OpenCV's DirectShow path rebuilds
        #: the graph on a size change with whatever format it then
        #: defaults to, and the one asked for earlier is forgotten.
        self._wanted: str | None = None

    # ---- lifecycle -------------------------------------------------------

    def _locate(self) -> dict | None:
        """Which index this camera is at *now*. Same reasoning as the
        V4L2 twin: indexes renumber on replug, keys and fingerprints do
        not, and two of the same camera is a question rather than a
        guess."""
        seen = enumerate_cameras()
        if self._key:
            for found in seen:
                if found["key"] == self._key:
                    return found
        if self._fingerprint:
            same = [f for f in seen if f.get("fingerprint") == self._fingerprint]
            if len(same) == 1:
                return same[0]
        if not self._key and not self._fingerprint:
            return next((f for f in seen if f["index"] == self._index), None)
        return None

    def _open_capture(self) -> cv2.VideoCapture:
        return cv2.VideoCapture(self._found["index"], cv2.CAP_DSHOW)

    def open(self) -> CameraInfo:
        found = self._locate()
        if not found:
            raise NoCameraFound("USB camera")
        self._found = found
        _log.info("opening %r at DirectShow index %d; path=%r formats=%s "
                  "sizes=%s controls=%s", found["name"], found["index"],
                  found["path"], found["formats"], found["sizes"][:6],
                  [(c["name"], c["min"], c["max"], c["default"])
                   for c in found["controls"]])
        self._cap = self._open_capture()
        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            raise CameraBusy(
                f"{found['name']} is listed, but would not open. Another "
                "program may be using it.")
        # MJPEG for the preview where offered, for the same bandwidth
        # reason as on Linux. Applied by `_select`, after the size.
        self._wanted = "MJPG" if "MJPG" in found["formats"] else None

        sizes = found["sizes"] or [(int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                    int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))]
        resolutions = tuple(Resolution(i, w, h, 0.0)
                            for i, (w, h) in enumerate(sizes))
        self._controls = {c["name"]: c for c in found["controls"]}
        self._usable = found.get("usable") or usable_formats(found["formats"])
        # Our own handle on the device's controls, for kernel devices
        # only; see `_Controls`. A software camera is driven through
        # `cv2.set`, which reaches the graph's own filter.
        self._device = (_Controls(found["display"])
                        if found["display"].lower().startswith("@device:pnp:")
                        else None)
        self._focus = self._find_focus(found)
        self._settle()

        self._info = CameraInfo(
            model=found["name"],
            serial=found["key"],
            resolutions=resolutions, max_bit_depth=8,
            bayer_pattern="",
            exposure_range_us=self._exposure_range(),
            gain_range_pct=self._gain_range(),
            auto_exposure_switch=self._auto_exposure_switch(),
            brand="ToupTek" if found["vid"] in TOUPTEK_VIDS else "",
            raw_capable=False,
            software_trigger=True,
            fingerprint=found.get("fingerprint", ""),
        )
        self._select(0)
        return self._info

    def _find_focus(self, found: dict) -> "_DshowFocus | None":
        """The motor, wherever the vendor put it.

        Focus, where the camera has one. Otherwise, on a ToupTek device,
        a Backlight Compensation with a range far too wide to be one: the
        tester found their X5FCAM4K8MPA's stepper answers there, and
        nowhere a standard host would look.
        """
        if not found.get("display", "").lower().startswith("@device:pnp:"):
            # A software filter keeps its state per instance, so a write
            # through our own handle reaches nobody, and OpenCV's path
            # has no autofocus at all. ToupTek's own wrapper is the case:
            # the tester chose it and watched the slider drive nothing.
            # No motor is offered rather than one that lies.
            return None
        focus = self._controls.get("Focus")
        if focus:
            return _DshowFocus(self, focus)
        backlight = self._controls.get("Backlight")
        if (backlight and found.get("vid") in TOUPTEK_VIDS
                and backlight["max"] - backlight["min"] > FOCUS_BEHIND_BACKLIGHT_SPAN):
            _log.info("%r has no Focus control; its Backlight Compensation "
                      "runs %d..%d, which is a ToupTek focus motor, and is "
                      "used as one", found["name"], backlight["min"],
                      backlight["max"])
            return _DshowFocus(self, backlight)
        return None

    def close(self) -> None:
        super().close()
        if self._device is not None:
            self._device.close()
            self._device = None

    @property
    def focus_axis(self) -> FocusAxis | None:
        return self._focus

    def _exposure_range(self) -> tuple[int, int] | None:
        """None where the exposure cannot be set by hand, which includes
        a device that lists the property and then says it is automatic
        only: a range we cannot write is no range."""
        found = self._controls.get("Exposure")
        if not found or not self._manual_capable(found):
            return None
        return (exposure_to_us(found["min"]), exposure_to_us(found["max"]))

    @staticmethod
    def _manual_capable(control: dict) -> bool:
        flags = control.get("flags", CameraControl_Flags_Manual)
        return not flags or bool(flags & CameraControl_Flags_Manual)

    def _auto_exposure_switch(self) -> bool:
        found = self._controls.get("Exposure")
        return bool(found and found.get("flags", 0) & CameraControl_Flags_Auto)

    def _gain_range(self) -> tuple[int, int] | None:
        found = self._controls.get("Gain")
        if not found:
            return None
        return (100, 100 + max(1, found["max"] - found["min"]))

    def get_gain(self) -> int:
        """Ours, not the device's: `CAP_PROP_GAIN` on DirectShow comes
        back in device units, which the shared core would report as a
        percentage."""
        return self._gain_pct

    # ---- formats and sizes, the DirectShow way ---------------------------

    @staticmethod
    def _code(name: str) -> float:
        """What to hand `CAP_PROP_FOURCC` for one of our format names."""
        if name in _RGB_DATA1:
            return float(_RGB_DATA1[name])
        return float(cv2.VideoWriter_fourcc(*name.ljust(4)[:4]))

    def _format(self) -> str:
        got = int(self._cap.get(cv2.CAP_PROP_FOURCC)) & 0xFFFFFFFF
        for name, data1 in _RGB_DATA1.items():
            if got == data1:
                return name
        return "".join(chr((got >> (8 * i)) & 0xFF) for i in range(4))

    def _set_format(self, fourcc: str) -> str:
        """Ask for a format OpenCV's DirectShow path can name.

        It matches the value against the Data1 of every subtype it
        knows; a code it does not know stops the device and leaves it
        stopped, with the bad code remembered for every later size
        change. So only names from the device's own list are sent, and
        the two RGB subtypes go as their Data1 rather than as letters.
        """
        if fourcc.strip() not in [f.strip() for f in (self._found or {}).get("formats", [])]:
            return self._format()
        self._wanted = fourcc.strip()
        self._cap.set(cv2.CAP_PROP_FOURCC, self._code(self._wanted))
        return self._format()

    def _select(self, index: int) -> None:
        """Size first, then the format. OpenCV's DirectShow path rebuilds
        the graph when the height lands and forgets the format it was
        asked for before; asking again afterwards rebuilds it with the
        format and the size it just read back."""
        super()._select(index)
        if self._wanted:
            self._cap.set(cv2.CAP_PROP_FOURCC, self._code(self._wanted))

    def _settle(self) -> None:
        """Manual exposure, manual white balance, motor in our hands,
        first frames discarded. See the V4L2 twin for why each."""
        if self._cap is None or self._device is None:
            return
        try:
            if self._exposure_range() is not None:
                current = self._device.get("camera", CameraControl_Exposure)
                if current is not None:
                    self._device.set("camera", CameraControl_Exposure, current)
                    self._exposure_us = exposure_to_us(current)
            if "WhiteBalance" in self._controls:
                current = self._device.get("amp", VideoProcAmp_WhiteBalance)
                if current is not None:
                    self._device.set("amp", VideoProcAmp_WhiteBalance, current)
            if self._focus is not None:
                current = self._device.get(self._focus.which, self._focus.prop)
                if current is not None:
                    self._focus._position = current
                    self._device.set(self._focus.which, self._focus.prop, current)
        except Exception as why:
            _log.debug("settling controls: %s", why)
        self._warm_up()

    # ---- settings --------------------------------------------------------

    def _write_control(self, what: str, value) -> None:
        if what == "auto_exposure":
            found = self._controls.get("Exposure")
            if not found:
                return
            if self._device is not None:
                current = self._device.get("camera", CameraControl_Exposure)
                self._device.set("camera", CameraControl_Exposure,
                                 found["default"] if current is None else current,
                                 auto=bool(value))
            else:
                self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1 if value else 0)
        elif what == "exposure":
            found = self._controls.get("Exposure")
            if not found:
                return
            step = max(found["min"], min(found["max"], us_to_exposure(value)))
            # The frame's metadata should carry what the camera got,
            # which is the nearest power of two, not what was asked.
            self._exposure_us = exposure_to_us(step)
            if self._device is not None:
                self._device.set("camera", CameraControl_Exposure, step)
            else:
                self._cap.set(cv2.CAP_PROP_EXPOSURE, step)
        elif what == "gain":
            found = self._controls.get("Gain")
            if not found:
                return
            level = found["min"] + max(0, int(value) - 100)
            level = min(found["max"], max(found["min"], level))
            if self._device is not None:
                self._device.set("amp", VideoProcAmp_Gain, level)
            else:
                self._cap.set(cv2.CAP_PROP_GAIN, level)
        elif what == "focus":
            if self._focus is None:
                return
            if self._device is not None:
                self._device.set(self._focus.which, self._focus.prop, int(value))
            elif self._focus.which == "camera":
                self._cap.set(cv2.CAP_PROP_FOCUS, int(value))
            else:
                self._cap.set(cv2.CAP_PROP_BACKLIGHT, int(value))
        elif what == "focus_auto":
            if self._focus is None:
                return
            if self._device is not None:
                current = self._device.get(self._focus.which, self._focus.prop) or 0
                self._device.set(self._focus.which, self._focus.prop, current,
                                 auto=bool(value))
            elif self._focus.which == "camera":
                self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if value else 0)
                current = int(self._cap.get(cv2.CAP_PROP_FOCUS) or 0)
            else:
                current = self._focus.get()
            if not value and self._focus is not None:
                # Taking the motor back: wherever the camera left it is
                # where it is, and the slider should say so.
                self._focus._position = current
