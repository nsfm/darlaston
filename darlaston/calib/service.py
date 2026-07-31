"""Running a calibration, and quietly collecting one when nobody asked.

Two ways in.

**Deliberate.** A short guided routine: cap the lamp for a dark, find blank
patches for a flat. Off the UI thread, reporting progress, because each frame
is a full-resolution pull.

**Opportunistic.** While hunting for a subject you spend most of the time
crossing empty slide. The live stream is watched for frames that look blank and
they are banked against the current optical combination, at *distinct stage
positions* -- four frames of the same empty patch do not median away debris.
By the time a flat is wanted, four are often already in hand and nothing was
asked of anyone.

The gate nags; it never blocks. Sometimes the light is right and the diatom is
beautiful and calibration can wait.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from ..live.blank import BlankDetector  # re-exported for convenience
from . import frames as F
from . import preview_lut
from .store import (CalibrationStore, Provenance, dark_key, flat_key,
                    illumination_key)


@dataclass(frozen=True)
class Progress:
    stage: str
    done: int = 0
    total: int = 0
    message: str = ""
    finished: bool = False
    ok: bool = True

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0


class FlatBank:
    """Blank frames banked at distinct positions, for one optical key."""

    def __init__(self, wanted: int = 4, min_separation: float = 120.0) -> None:
        self.wanted = wanted
        self._min_sep = min_separation
        self._frames: list[np.ndarray] = []
        self._positions: list[tuple[float, float]] = []
        self._cursor = (0.0, 0.0)

    def note_motion(self, dx: float, dy: float) -> None:
        self._cursor = (self._cursor[0] + dx, self._cursor[1] + dy)

    @property
    def count(self) -> int:
        return len(self._frames)

    @property
    def complete(self) -> bool:
        return len(self._frames) >= self.wanted

    def offer(self, frame: np.ndarray) -> bool:
        """Bank a frame if it is far enough from the ones already held."""
        if self.complete:
            return False
        for px, py in self._positions:
            if abs(px - self._cursor[0]) < self._min_sep and \
               abs(py - self._cursor[1]) < self._min_sep:
                return False          # same patch; its debris would survive
        self._frames.append(frame)
        self._positions.append(self._cursor)
        return True

    def reset(self) -> None:
        self._frames.clear()
        self._positions.clear()

    @property
    def frames(self) -> list[np.ndarray]:
        return list(self._frames)


class CalibrationService:
    """Deliberate routines. One at a time, off the UI thread."""

    def __init__(self, session, store: CalibrationStore | None = None,
                 on_progress: Callable[[Progress], None] | None = None) -> None:
        self._session = session
        self.store = store or CalibrationStore()
        self._emit = on_progress or (lambda _p: None)
        self._busy = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def _start(self, target, *args) -> bool:
        if not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._guard, args=(target, args), daemon=True,
                         name="calibration").start()
        return True

    def _guard(self, target, args) -> None:
        try:
            target(*args)
        except Exception as exc:
            self._emit(Progress("error", message=str(exc), finished=True,
                                ok=False))
        finally:
            self._busy.release()

    # ---- dark ------------------------------------------------------------

    def capture_dark(self, frames: int = 8) -> bool:
        return self._start(self._dark, frames)

    def _dark(self, count: int) -> None:
        backend = self._require_backend()
        exposure, gain = backend.get_exposure(), backend.get_gain()
        stack = []
        for i in range(count):
            self._emit(Progress("dark", i, count, "Capturing dark frames"))
            with backend.grab_raw() as frame:
                stack.append(frame.copy())
        master = F.average_frames(stack)
        defects = F.defect_map(master)

        key = dark_key(exposure, gain)
        self.store.put("dark", key,
                       Provenance("dark", key, frames=count,
                                  exposure_us=exposure, gain_pct=gain),
                       data=master.astype(np.float32),
                       values={"mean": float(master.mean()),
                               "read_noise": float(master.std()),
                               "hot_pixels": int(len(defects))})
        self.store.put("defects", key,
                       Provenance("defects", key, frames=count,
                                  exposure_us=exposure, gain_pct=gain),
                       data=defects)
        self._emit(Progress(
            "dark", count, count, finished=True,
            message=(f"black level {master.mean():.2f} DN, "
                     f"read noise {master.std():.2f} DN, "
                     f"{len(defects)} hot pixels")))

    # ---- flat ------------------------------------------------------------

    def build_flat(self, setup, banked: list[np.ndarray], slide: str = "") -> bool:
        return self._start(self._flat, setup, banked, slide)

    def _flat(self, setup, banked: list[np.ndarray], slide: str) -> None:
        if len(banked) < 2:
            raise RuntimeError("need at least two blank fields to median")
        backend = self._require_backend()
        exposure, gain = backend.get_exposure(), backend.get_gain()

        self._emit(Progress("flat", 0, 1, "Combining blank fields"))
        # Median, not mean: the point is to reject debris present in a
        # minority of frames.
        master = F.median_frames(banked)

        dark = self.store.get("dark", dark_key(exposure, gain))
        dark_data = dark.data if dark else None
        gains = F.white_balance_from_flat(master, dark_data)

        key = flat_key(setup, slide)
        self.store.put("flat", key,
                       Provenance("flat", key, frames=len(banked),
                                  exposure_us=exposure, gain_pct=gain,
                                  notes=slide),
                       data=master.astype(np.float32))
        wb_key = illumination_key(setup)
        self.store.put("wb", wb_key,
                       Provenance("white_balance", wb_key, frames=len(banked)),
                       values={"gains": list(gains)})
        # A median of two frames is their mean, and rejects nothing. The flat
        # is still worth having -- it captures the illumination field and the
        # white balance -- but any debris in either frame survives into it, so
        # say so rather than letting it look finished.
        caveat = ("  (two fields median to their mean -- debris survives; "
                  "three or more rejects it)" if len(banked) < 3 else "")
        self._emit(Progress(
            "flat", 1, 1, finished=True,
            message=(f"from {len(banked)} fields -- white balance "
                     f"R {gains[0]:.3f} G 1.000 B {gains[2]:.3f}{caveat}")))

    # ---- preview LUT -----------------------------------------------------

    def build_preview_lut(self, setup, steps: int = 4) -> bool:
        return self._start(self._lut, setup, steps)

    def _lut(self, setup, steps: int) -> None:
        backend = self._require_backend()
        base = backend.get_exposure()
        gain = backend.get_gain()

        # The whole method assumes the ISP transform is fixed across the ramp.
        # Continuous auto white balance would re-fit it between frames and the
        # resulting table would be silently wrong, so switch it off rather than
        # hoping it is already off. Verified as off on this camera, but that is
        # luck, not design.
        restore_awb = self._suspend_awb(backend)
        # An exposure ramp, because one frame does not exercise the whole
        # preview range -- measured, green only ever visited 116-255.
        ladder = [max(300, int(base * f)) for f in (0.25, 0.5, 1.0, 2.0)][:steps]

        pairs = []
        try:
            for i, exposure in enumerate(ladder):
                self._emit(Progress("lut", i, len(ladder),
                                    f"Profiling the preview at {exposure/1000:.1f} ms"))
                backend.set_exposure(exposure)
                time.sleep(0.4)          # let the setting take effect
                isp = self._grab_preview(backend)
                with backend.grab_raw() as frame:
                    pairs.append((isp, frame.copy()))
        finally:
            backend.set_exposure(base)
            restore_awb()

        lut = preview_lut.build(pairs)
        key = illumination_key(setup)
        self.store.put("lut", key,
                       Provenance("preview_lut", key, frames=len(pairs),
                                  gain_pct=gain),
                       values=lut.to_dict())
        sat = "  ".join(f"{n} {v}" for n, v in zip("RGB", lut.saturation))
        self._emit(Progress("lut", len(ladder), len(ladder), finished=True,
                            message=f"preview saturates at raw -- {sat}"))

    @staticmethod
    def _suspend_awb(backend) -> Callable[[], None]:
        """Turn continuous auto white balance off, returning a restorer."""
        cam = getattr(backend, "_cam", None)
        toup = getattr(backend, "_t", None)
        code = getattr(toup, "TOUPCAM_OPTION_AWB_CONTINUOUS", None)
        if cam is None or code is None:
            return lambda: None
        try:
            previous = cam.get_Option(code)
        except Exception:
            return lambda: None
        if not previous:
            return lambda: None
        try:
            cam.put_Option(code, 0)
        except Exception:
            return lambda: None

        def restore() -> None:
            try:
                cam.put_Option(code, previous)
            except Exception:
                pass
        return restore

    @staticmethod
    def _grab_preview(backend) -> np.ndarray:
        """One full-resolution ISP frame, to pair with a raw one.

        Must be the same size as the raw frame or the correspondence is
        meaningless, so this is not the binned preview.
        """
        from ..camera.toupcam import ToupcamBackend
        if not isinstance(backend, ToupcamBackend):
            raise RuntimeError("preview profiling needs the ToupTek backend")
        return backend.grab_isp_full()

    # ---- helpers ---------------------------------------------------------

    def _require_backend(self):
        backend = self._session.backend
        if backend is None:
            raise RuntimeError("No camera connected.")
        return backend
