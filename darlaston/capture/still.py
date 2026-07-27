"""Taking one photograph.

Runs off the UI thread: a full-resolution pull stops the preview, reconfigures
the camera, grabs, restores, and then writes forty megabytes. None of that
belongs in an event loop.

The capture path never drops. A live frame lost is invisible; a capture lost is
gone -- see ARCHITECTURE.md 3.1.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from ..process import dng
from ..process.metadata import from_setup
from ..session.settings import Settings, next_sequence


@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    path: Path | None = None
    message: str = ""
    elapsed: float = 0.0
    width: int = 0
    height: int = 0
    clipped_fraction: float = 0.0

    @property
    def summary(self) -> str:
        if not self.ok:
            return self.message
        mp = self.width * self.height / 1e6
        return (f"{self.path.name}   {mp:.1f} MP   {self.elapsed:.1f}s"
                + (f"   {self.clipped_fraction * 100:.1f}% clipped"
                   if self.clipped_fraction > 0.0005 else ""))


class StillCapture:
    """One capture at a time, on its own thread.

    Refusing to start a second while one is running is deliberate: the camera
    cannot serve two full-resolution pulls, and a queue of shutter presses is
    never what anybody meant.
    """

    def __init__(self, session, settings: Settings,
                 on_state: Callable[[str], None] | None = None,
                 on_result: Callable[[CaptureResult], None] | None = None) -> None:
        self._session = session
        self._settings = settings
        self._on_state = on_state or (lambda _s: None)
        self._on_result = on_result or (lambda _r: None)
        self._busy = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def trigger(self, setup=None, subject: str = "",
                slide: str = "") -> bool:
        """Start a capture. Returns False if one is already running."""
        if not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, args=(setup, subject, slide),
                         daemon=True, name="capture").start()
        return True

    # ---- the work --------------------------------------------------------

    def _run(self, setup, subject: str, slide: str = "") -> None:
        started = time.perf_counter()
        try:
            backend = self._session.backend
            if backend is None:
                raise RuntimeError("No camera connected.")

            self._on_state("exposing")
            frame = backend.grab_raw()
            with frame:
                raw = frame.copy()          # explicit: outlives the pool
                exposure_us = frame.exposure_us
                gain_pct = frame.gain_pct

            self._on_state("writing")
            path = self._destination(setup, subject)
            info = backend.info
            pattern = info.bayer_pattern if info else "GBRG"

            meta = None
            if setup is not None:
                meta = from_setup(setup, exposure_us=exposure_us,
                                  gain_pct=gain_pct, subject=subject,
                                  slide=slide, app_version=_version())

            written = dng.write_bayer(path, raw, pattern=pattern, meta=meta)

            clipped = float((raw >= dng.WHITE_LEVEL).sum()) / raw.size
            self._on_state("idle")
            self._on_result(CaptureResult(
                ok=True, path=written, elapsed=time.perf_counter() - started,
                width=raw.shape[1], height=raw.shape[0],
                clipped_fraction=clipped))
        except Exception as exc:
            self._on_state("idle")
            self._on_result(CaptureResult(
                ok=False, message=_explain(exc),
                elapsed=time.perf_counter() - started))
        finally:
            self._busy.release()

    def _destination(self, setup, subject: str) -> Path:
        when = datetime.now()
        # Sequence comes from what is on disk, so it survives a crash, a manual
        # file move, and two copies of the app running at once.
        folder = self._settings.resolve(setup=setup, seq=1, subject=subject,
                                        when=when).parent
        seq = next_sequence(folder, self._settings.filename_pattern)
        return self._settings.resolve(setup=setup, seq=seq, subject=subject,
                                      when=when)


def _version() -> str:
    from .. import __version__
    return __version__


def _explain(exc: Exception) -> str:
    """Say what to do, not just what broke."""
    text = str(exc).lower()
    if "no camera" in text:
        return "No camera connected."
    if "space" in text or "no space" in text:
        return "The disk is full. A full-resolution frame needs about 40 MB."
    if "permission" in text:
        return "Cannot write there. Check the capture folder in Settings."
    if "timeout" in text or "0x8001011f" in text:
        return ("The camera did not deliver a frame in time. If this repeats, "
                "the link may have dropped to USB 2.0 — usually the cable.")
    return str(exc) or exc.__class__.__name__
