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

The SDK is never vendored -- see DISCOVERY.md 4c. Users install it themselves.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .base import CameraBackend, CameraInfo, Resolution
from .buffers import BufferPool, Frame

_toupcam = None


def load_sdk():
    """Import the vendor bindings from a user-installed SDK. Idempotent."""
    global _toupcam
    if _toupcam is not None:
        return _toupcam
    roots = ([Path(os.environ["TOUPCAM_SDK"])] if os.environ.get("TOUPCAM_SDK")
             else list(reversed(sorted(Path.home().glob("toup/sdk-*")))))
    for root in roots:
        lib, binding = root / "linux/x64/libtoupcam.so", root / "python"
        if lib.exists() and (binding / "toupcam.py").exists():
            ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            sys.path.insert(0, str(binding))
            _toupcam = __import__("toupcam")
            return _toupcam
    try:
        _toupcam = __import__("toupcam")
        return _toupcam
    except ImportError as exc:
        raise RuntimeError(
            "ToupTek SDK not found. Unpack it under ~/toup/sdk-*/ or set "
            "TOUPCAM_SDK to its root."
        ) from exc


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

    # ---- lifecycle -------------------------------------------------------

    def open(self) -> CameraInfo:
        devices = self._t.Toupcam.EnumV2()
        if not devices:
            raise RuntimeError("no ToupTek camera found")
        self._cam = self._t.Toupcam.Open(devices[self._index].id)
        if not self._cam:
            raise RuntimeError("failed to open camera (is ToupLite running?)")

        cam = self._cam
        resolutions = []
        for i in range(cam.get_ResolutionNumber()):
            w, h = cam.get_Resolution(i)
            try:
                px, _py = cam.get_PixelSize(i)
            except Exception:
                px = 0.0
            resolutions.append(Resolution(i, w, h, float(px)))

        fourcc, _bits = cam.get_RawFormat()
        pattern = "".join(chr((fourcc >> (8 * k)) & 0xFF) for k in range(4))
        lo, hi, _default = cam.get_ExpTimeRange()
        glo, ghi, _gdef = cam.get_ExpoAGainRange()

        self._info = CameraInfo(
            model=devices[self._index].displayname,
            serial=self._serial(),
            resolutions=tuple(resolutions),
            max_bit_depth=cam.get_MaxBitDepth(),
            bayer_pattern=pattern,
            exposure_range_us=(int(lo), int(hi)),
            gain_range_pct=(int(glo), int(ghi)),
        )
        return self._info

    def _serial(self) -> str:
        try:
            return self._cam.get_SerialNumber()
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
        cam.put_Option(t.TOUPCAM_OPTION_RAW, 0)
        cam.put_Option(t.TOUPCAM_OPTION_BITDEPTH, 0)
        cam.put_Option(t.TOUPCAM_OPTION_TRIGGER, 0)   # free-running video

        self._pool = BufferPool((h, w, 3), np.uint8, count=4)
        self._size = (w, h)
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
            self._cam.PullImageV4(buf, 0, 24, 0, None)
        except Exception:
            pool._give_back(buf)
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

    def grab_raw(self, timeout_ms: int = 8000) -> Frame:
        """One full-resolution 12-bit frame, canonical orientation.

        Stops the preview stream, reconfigures, grabs, and leaves the caller to
        restart streaming -- the SDK will not accept mode changes while running.
        """
        t, cam = self._t, self._cam
        was_streaming = self._pool is not None
        on_frame = self._on_frame
        if was_streaming:
            self.stop_stream()

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

        if was_streaming and on_frame is not None:
            self.start_stream(on_frame)

        return Frame(data=buf, seq=self._seq, timestamp=time.time(),
                     exposure_us=self._exposure_us, gain_pct=self._gain_pct,
                     binned=False, _pool=pool)
