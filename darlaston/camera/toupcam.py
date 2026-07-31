"""ToupTek backend.

Every vendor quirk we paid for in blood lives here and is never allowed to
leak upward:

  * Exposure controls return E_UNEXPECTED before StartPullMode, and
    put_Option(RAW) returns it *after*. Mode flags before the stream, exposure
    after.
  * ZERO_PADDING is inverted from how its comment reads: 1 leaves the 12 bits
    left-justified (max 65504 = 4094<<4); 0 gives true 0..4095.
  * The raw stream arrives bottom-up. Flipping an even-height frame shifts the
    Bayer row parity, which is why an unflipped buffer looks like RGGB while
    get_RawFormat correctly reports GBRG. We flip, so callers always get
    canonical orientation with the sensor's own pattern valid.
  * The vendor's toupcam.py calls LoadLibrary('libtoupcam.so') with a bare
    name, so the library is preloaded by absolute path with RTLD_GLOBAL. Its
    SONAME is exactly that, so the later bare-name dlopen finds the copy
    already in the process.
  * PullImageV4 declares pImageData as c_char_p, so a numpy array must be
    handed over as a pointer rather than directly.
  * The binding's naming is inconsistent with the C API and with itself:
    ResolutionNumber, MaxBitDepth and SerialNumber carry no get_ prefix while
    get_Resolution, get_RawFormat and get_PixelSize do. Read the signatures,
    do not infer them.

The SDK is never vendored -- see DISCOVERY.md 4c. Users install it themselves.
"""
from __future__ import annotations

import ctypes
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .base import CameraBackend, CameraInfo, Resolution
from .buffers import BufferPool, Frame
from .errors import (CameraBusy, NoCameraFound, SdkMissing,
                     SdkTooOld, is_retryable)

def library_name(soname: str) -> str:
    """The file this platform's build of a vendor library is called.

    BRANDS records the Linux name because that is where this was written;
    the vendors ship one archive holding every platform, and only the
    filename convention changes. Windows drops the `lib` prefix.
    """
    stem = soname
    if stem.startswith("lib"):
        stem = stem[3:]
    stem = stem.split(".")[0]
    if sys.platform == "darwin":
        return f"lib{stem}.dylib"
    if sys.platform.startswith("win"):
        return f"{stem}.dll"
    return f"lib{stem}.so"


def library_dirs() -> tuple[str, ...]:
    """Where inside a vendor SDK this platform's build lives, best first.

    Read off the archive rather than assumed. ToupTek 59.30594 ships
    mac/ as a single universal binary covering Intel and Apple silicon,
    win/{x64,x86,arm64}, and for Linux x64, x86, armhf, armel, ostl and
    an arm64 split into glibc and musl builds -- which is why arm64
    returns two candidates and the loader tries both. Anything not
    matched falls back to the 64-bit desktop build for the platform,
    since that is what almost everyone is on.
    """
    if sys.platform == "darwin":
        return ("mac",)
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return ("win/arm64",) if "arm" in machine else ("win/x64", "win/x86")
    if machine in ("aarch64", "arm64"):
        return ("linux/arm64/glibc", "linux/arm64/musl")
    if machine.startswith("arm"):
        return ("linux/armhf", "linux/armel")
    if machine in ("i386", "i686", "x86"):
        return ("linux/x86",)
    return ("linux/x64",)


#: The rebadge family. ToupTek manufactures for a dozen brands and ships
#: each one the same SDK with its symbols renamed -- verified binary to
#: binary: 272 exported symbols matching byte for byte, headers differing
#: by two cosmetic lines, and identical `ModelV2` fields for the same USB
#: PID across libraries. So one backend drives all of them; only the names
#: change. (brand module, library soname, class name, constant prefix).
#:
#: Meade is the trap: `libmeadecam.so` exports `Toupcam_*`, not
#: `Meadecam_*`. That is exactly why the preload below is RTLD_LOCAL --
#: with RTLD_GLOBAL the first library loaded would answer for every brand
#: afterwards, silently.
BRANDS = [
    ("toupcam", "libtoupcam.so", "Toupcam", "TOUPCAM"),
    ("altaircam", "libaltaircam.so", "Altaircam", "ALTAIRCAM"),
    ("mallincam", "libmallincam.so", "Mallincam", "MALLINCAM"),
    ("meadecam", "libmeadecam.so", "Toupcam", "TOUPCAM"),
    ("ogmacam", "libogmacam.so", "Ogmacam", "OGMACAM"),
    ("nncam", "libnncam.so", "Nncam", "NNCAM"),
    ("bressercam", "libbressercam.so", "Bressercam", "BRESSERCAM"),
    ("starshootg", "libstarshootg.so", "Starshootg", "STARSHOOTG"),
    ("omegonprocam", "libomegonprocam.so", "Omegonprocam", "OMEGONPROCAM"),
    ("svbonycam", "libsvbonycam.so", "Svbonycam", "SVBONYCAM"),
    ("tscam", "libtscam.so", "Tscam", "TSCAM"),
    ("amcam", "libamcam.so", "Amcam", "AMCAM"),
]

#: Entry points this backend cannot work without. Two libraries with the
#: same SONAME are installed on at least one real machine -- a 2021 one
#: from a ToupLite bundle and a current SDK -- and the old one lacks every
#: function here. Without the check the failure is a confusing
#: AttributeError deep inside a capture.
REQUIRED = ("PullImageV4", "TriggerSync", "get_Model")

_vendor = None


class _Vendor:
    """One brand's binding, under the names the rest of this file uses.

    Call sites say `Toupcam` and `TOUPCAM_OPTION_*`; a rebadged SDK spells
    those `Altaircam` and `ALTAIRCAM_OPTION_*`. Translating here keeps the
    backend written once.
    """

    def __init__(self, module, brand: str, cls: str, prefix: str) -> None:
        self._module = module
        self.brand = brand
        self._cls = cls
        self._prefix = prefix

    def __getattr__(self, name: str):
        if name == "Toupcam":
            return getattr(self._module, self._cls)
        if name.startswith("TOUPCAM_"):
            return getattr(self._module,
                           self._prefix + name[len("TOUPCAM"):])
        return getattr(self._module, name)


def _load_brand(root: Path, module: str, soname: str, cls: str,
                prefix: str):
    """Try one brand inside one SDK root. Returns a _Vendor or None."""
    binding = root / "python"
    if not (binding / f"{module}.py").exists():
        return None
    name = library_name(soname)
    lib = next((root / d / name for d in library_dirs()
                if (root / d / name).exists()), None)
    if lib is None:
        return None
    # RTLD_LOCAL, deliberately: the absolute-path preload exists so the
    # binding's bare-name LoadLibrary finds *this* copy rather than
    # whatever else is on the search path, and dlopen matches by SONAME
    # regardless of symbol visibility -- measured, same handle either way.
    # Keeping symbols local is what lets two brands coexist.
    handle = ctypes.CDLL(str(lib), mode=ctypes.RTLD_LOCAL)
    missing = [fn for fn in REQUIRED
               if not hasattr(handle, f"{cls}_{fn}")]
    if missing:
        raise SdkTooOld(str(lib),
                        tuple(f"{cls}_{m}" for m in missing))
    sys.path.insert(0, str(binding))
    return _Vendor(__import__(module), module, cls, prefix)


def load_sdk():
    """Import a user-installed SDK, from ToupTek or any of its rebadges.

    Idempotent. Searches SDK roots newest-first, and within each root
    tries every brand -- so an Altair or Meade user needs no configuration
    beyond unpacking their own vendor's SDK where ours would go.
    """
    global _vendor
    if _vendor is not None:
        return _vendor
    if os.environ.get("TOUPCAM_SDK"):
        roots = [Path(os.environ["TOUPCAM_SDK"])]
    else:
        from .sdk_install import INSTALL_ROOT
        # Anything darlaston installed itself comes first: it was
        # verified when it landed, whereas a hand-unpacked ~/toup could
        # be the 2021 ToupLite copy.
        roots = [p for p in sorted(INSTALL_ROOT.glob("*"), reverse=True)
                 if p.is_dir()]
        roots += [p for pattern in ("toup/sdk-*", "toup")
                  for p in sorted(Path.home().glob(pattern), reverse=True)]
    for root in roots:
        for module, soname, cls, prefix in BRANDS:
            found = _load_brand(root, module, soname, cls, prefix)
            if found is not None:
                _vendor = found
                return _vendor
    for module, _soname, cls, prefix in BRANDS:
        try:
            _vendor = _Vendor(__import__(module), module, cls, prefix)
            return _vendor
        except ImportError:
            continue
    raise SdkMissing(tuple(b[0] for b in BRANDS))


class ToupcamBackend(CameraBackend):
    def __init__(self, index: int = 0) -> None:
        self._t = load_sdk()
        self._index = index
        self._cam = None
        self._info: CameraInfo | None = None
        self._pool: BufferPool | None = None
        self._on_frame: Callable[[Frame], None] | None = None
        self._seq = 0
        self._exposure_us = 8330
        self._gain_pct = 100
        self._preview = 2
        self._lock = threading.Lock()
        self._pull_failed = False

    # ---- lifecycle -------------------------------------------------------

    def open(self) -> CameraInfo:
        devices = self._t.Toupcam.EnumV2()
        if not devices:
            raise NoCameraFound("ToupTek-family camera")
        self._cam = self._t.Toupcam.Open(devices[self._index].id)
        if not self._cam:
            # The SDK enumerated it and then refused to open it, which in
            # practice means somebody else already has it.
            raise CameraBusy(
                f"{devices[self._index].displayname} is on the bus and "
                "enumerated, but would not open.")

        cam = self._cam
        resolutions = []
        for i in range(cam.ResolutionNumber()):
            w, h = cam.get_Resolution(i)
            try:
                px, _py = cam.get_PixelSize(i)
            except Exception:
                px = 0.0
            resolutions.append(Resolution(i, w, h, float(px)))

        # Capabilities, from the vendor's own model table rather than
        # inferred. One identical API covers ~1000 cameras with 711
        # distinct capability words -- 8-bit to 16-bit, colour and mono,
        # cooled and not -- so anything assumed here is wrong for
        # somebody. MONO is authoritative; get_RawFormat returns 'YYYY'
        # on a mono sensor, which is not a CFA pattern and must not be
        # written as one.
        flag = getattr(devices[self._index].model, "flag", 0) or 0
        mono = bool(flag & self._t.TOUPCAM_FLAG_MONO)
        fourcc, _bits = cam.get_RawFormat()
        pattern = "" if mono else "".join(
            chr((fourcc >> (8 * k)) & 0xFF) for k in range(4))
        lo, hi, _default = cam.get_ExpTimeRange()
        glo, ghi, _gdef = cam.get_ExpoAGainRange()

        self._info = CameraInfo(
            model=devices[self._index].displayname,
            serial=self._serial(),
            resolutions=tuple(resolutions),
            max_bit_depth=cam.MaxBitDepth(),
            bayer_pattern=pattern,
            exposure_range_us=(int(lo), int(hi)),
            gain_range_pct=(int(glo), int(ghi)),
            brand=self._t.brand,
            raw_capable=bool(flag & (self._t.TOUPCAM_FLAG_RAW8
                                     | self._t.TOUPCAM_FLAG_RAW10
                                     | self._t.TOUPCAM_FLAG_RAW12
                                     | self._t.TOUPCAM_FLAG_RAW14
                                     | self._t.TOUPCAM_FLAG_RAW16)),
            cooled=bool(flag & self._t.TOUPCAM_FLAG_TEC),
            has_fan=bool(flag & self._t.TOUPCAM_FLAG_FAN),
            # Advisory only, and measured to be so: our own E3ISPM does
            # not set this flag yet accepts a software trigger and has
            # been capturing through one all along. Never gate on it.
            software_trigger=bool(flag
                                  & self._t.TOUPCAM_FLAG_TRIGGER_SOFTWARE),
        )
        return self._info

    def _serial(self) -> str:
        try:
            return self._cam.SerialNumber()
        except Exception:
            return "unknown"

    def close(self) -> None:
        self.stop_stream()
        if self._cam is not None:
            self._cam.Close()
            self._cam = None
        self._info = None

    @property
    def info(self) -> CameraInfo | None:
        return self._info

    # ---- settings --------------------------------------------------------

    def set_resolution(self, index: int) -> None:
        self._preview = index

    def set_exposure(self, microseconds: int) -> None:
        self._exposure_us = int(microseconds)
        with self._lock:
            if self._cam is not None:
                try:
                    self._cam.put_ExpoTime(self._exposure_us)
                except Exception:
                    pass  # invalid before the stream starts; applied on start

    def set_gain(self, percent: int) -> None:
        self._gain_pct = int(percent)
        with self._lock:
            if self._cam is not None:
                try:
                    self._cam.put_ExpoAGain(self._gain_pct)
                except Exception:
                    pass

    def get_exposure(self) -> int:
        try:
            return int(self._cam.get_ExpoTime())
        except Exception:
            return self._exposure_us

    def get_gain(self) -> int:
        try:
            return int(self._cam.get_ExpoAGain())
        except Exception:
            return self._gain_pct

    def set_framerate_cap(self, fps: int) -> None:
        """Ask the camera for fewer frames rather than discarding more.

        Frames we drop were still pulled -- memcpy'd off USB, with the SDK's
        thread holding the GIL throughout -- so they actively steal time from
        analysis. Sleeping in our own loop would not help; the camera would
        keep producing them.
        """
        try:
            self._cam.put_Option(self._t.TOUPCAM_OPTION_FRAMERATE, int(fps))
        except Exception:
            pass

    def _apply_exposure(self) -> None:
        """Only valid once streaming. Auto-exposure stays off: it would make
        frames incomparable, which breaks stacking and mosaicking both."""
        self._cam.put_AutoExpoEnable(0)
        self._cam.put_ExpoTime(self._exposure_us)
        self._cam.put_ExpoAGain(self._gain_pct)

    # ---- streaming -------------------------------------------------------

    def start_stream(self, on_frame: Callable[[Frame], None]) -> None:
        t, cam = self._t, self._cam
        self._on_frame = on_frame
        cam.put_eSize(self._preview)
        w, h = cam.get_Size()

        # Mode flags go before the stream; put_Option(RAW) fails after it.
        #
        # The preview mode is stated in full rather than restored piecemeal.
        # grab_raw switches off the ISP, the linear mapping, the tone curve and
        # the colour matrix; restoring only what we happened to remember left
        # the colour matrix off and the preview came back magenta. Declaring
        # every flag means no capture path can leave this half-configured.
        for option, value in (
                (t.TOUPCAM_OPTION_RAW, 0),
                (t.TOUPCAM_OPTION_BITDEPTH, 0),
                (t.TOUPCAM_OPTION_TRIGGER, 0),        # free-running video
                (t.TOUPCAM_OPTION_ISP, 0),            # 0 = auto
                (t.TOUPCAM_OPTION_LINEAR, 1),
                (t.TOUPCAM_OPTION_CURVE, 2),          # logarithmic, the default
                (t.TOUPCAM_OPTION_COLORMATIX, 1)):
            try:
                cam.put_Option(option, value)
            except Exception:
                pass          # not every option exists on every model

        self._pool = BufferPool((h, w, 3), np.uint8, count=4)
        self._size = (w, h)
        self._pull_failed = False
        cam.StartPullModeWithCallback(self._callback, None)
        self._apply_exposure()

    def _callback(self, event, _ctx) -> None:
        """Runs on the SDK's own thread. Pull, hand off, return -- nothing else.

        Every millisecond spent here is a millisecond of the vendor's thread
        held, and the GIL with it. See ARCHITECTURE.md 3.4.
        """
        if event != self._t.TOUPCAM_EVENT_IMAGE:
            return
        pool = self._pool
        if pool is None:
            return
        buf = pool.acquire()
        if buf is None:
            # Every buffer in flight means the consumer is behind. Dropping is
            # the correct answer on the live path.
            return
        try:
            # The binding declares pImageData as c_char_p, so a bare ndarray
            # raises ArgumentError on every frame. Hand it the pointer.
            self._cam.PullImageV4(buf.ctypes.data_as(ctypes.c_char_p),
                                  0, 24, 0, None)
        except Exception as exc:
            pool._give_back(buf)
            # Report once. Swallowing this silently is how a stream that never
            # delivers a frame looks exactly like a stream that is merely idle.
            if not self._pull_failed:
                self._pull_failed = True
                print(f"[toupcam] PullImage failed: {exc!r}", file=sys.stderr)
            return
        self._seq += 1
        frame = Frame(data=buf, seq=self._seq, timestamp=time.time(),
                      exposure_us=self._exposure_us, gain_pct=self._gain_pct,
                      binned=self._preview != 0, _pool=pool)
        if self._on_frame is not None:
            self._on_frame(frame)
        else:
            frame.release()

    def stop_stream(self) -> None:
        self._on_frame = None
        if self._cam is not None:
            try:
                self._cam.Stop()
            except Exception:
                pass
        self._pool = None

    # ---- capture ---------------------------------------------------------

    def grab_isp_full(self, timeout_ms: int = 8000) -> np.ndarray:
        """One full-resolution frame through the ISP, as BGR.

        Pairs with grab_raw to measure what the ISP does to the sensor data.
        It must be full resolution, not the binned preview, or there is no
        per-pixel correspondence to measure.
        """
        t, cam = self._t, self._cam
        was_streaming = self._pool is not None
        on_frame = self._on_frame
        if was_streaming:
            self.stop_stream()

        cam.put_eSize(0)
        w, h = cam.get_Size()
        cam.put_Option(t.TOUPCAM_OPTION_RAW, 0)
        cam.put_Option(t.TOUPCAM_OPTION_BITDEPTH, 0)
        cam.put_Option(t.TOUPCAM_OPTION_TRIGGER, 1)
        cam.StartPullModeWithCallback(None, None)
        self._apply_exposure()

        buf = bytes(w * h * 3)
        cam.TriggerSync(timeout_ms, buf, 24, 0, None)
        cam.Stop()
        image = np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()

        if was_streaming and on_frame is not None:
            self.start_stream(on_frame)
        return image

    def grab_raw(self, timeout_ms: int = 8000) -> Frame:
        """One full-resolution 12-bit frame, canonical orientation.

        Stops the preview stream, reconfigures, grabs, and restores the
        preview -- **whatever happens**. The failure this structure exists to
        prevent: a throw inside the pull left the camera stopped in raw
        trigger mode with the preview never restarted, so one timeout on the
        tenth tile of a mosaic killed capture for the rest of the session. The
        restore is in a `finally`, and the mode is re-stated in full by
        `start_stream`, so no error path can leave the camera half-configured.

        A transient failure buys one retry. The capture path never drops
        (ARCHITECTURE.md §3.1), and a timeout on a link that has worked nine
        times is worth asking twice before troubling the operator.
        """
        was_streaming = self._pool is not None
        on_frame = self._on_frame
        if was_streaming:
            self.stop_stream()
        try:
            try:
                return self._pull_full(timeout_ms)
            except Exception as exc:
                if not is_retryable(exc):
                    raise
                # Put the camera down properly before asking again: whatever
                # state the failed attempt left it in, a fresh pull must
                # start from stopped.
                self._quiesce()
                return self._pull_full(timeout_ms)
        finally:
            if was_streaming and on_frame is not None:
                self._quiesce()
                try:
                    self.start_stream(on_frame)
                except Exception:
                    # Reported by the session's own health checks; raising
                    # here would mask the original capture error.
                    import traceback
                    traceback.print_exc()

    def _quiesce(self) -> None:
        """Stop the camera, ignoring the state it was in. Idempotent."""
        try:
            self._cam.Stop()
        except Exception:
            pass

    def _pull_full(self, timeout_ms: int) -> Frame:
        """The pull itself. Assumes the camera is stopped; leaves it stopped
        on success and in any state at all on failure -- the caller restores."""
        t, cam = self._t, self._cam
        cam.put_eSize(0)
        w, h = cam.get_Size()
        cam.put_Option(t.TOUPCAM_OPTION_RAW, 1)
        cam.put_Option(t.TOUPCAM_OPTION_BITDEPTH, 1)
        cam.put_Option(t.TOUPCAM_OPTION_ISP, -1)
        cam.put_Option(t.TOUPCAM_OPTION_LINEAR, 0)
        cam.put_Option(t.TOUPCAM_OPTION_CURVE, 0)
        cam.put_Option(t.TOUPCAM_OPTION_COLORMATIX, 0)
        cam.put_Option(t.TOUPCAM_OPTION_ZERO_PADDING, 0)
        cam.put_Option(t.TOUPCAM_OPTION_TRIGGER, 1)
        cam.StartPullModeWithCallback(None, None)
        self._apply_exposure()

        raw = bytes(w * h * 2)
        cam.TriggerSync(timeout_ms, raw, 16, w * 2, None)
        cam.Stop()

        arr = np.frombuffer(raw, np.uint16).reshape(h, w)
        canonical = np.ascontiguousarray(cv2.flip(arr, 0))

        pool = BufferPool((h, w), np.uint16, count=1)
        buf = pool.acquire()
        buf[:] = canonical
        self._seq += 1

        return Frame(data=buf, seq=self._seq, timestamp=time.time(),
                     exposure_us=self._exposure_us, gain_pct=self._gain_pct,
                     binned=False, _pool=pool)
