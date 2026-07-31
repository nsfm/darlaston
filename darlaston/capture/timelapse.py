"""Timelapse: the same capture, on a clock.

Deliberately thin. Each shot goes through StillCapture unchanged, so every
frame gets the same calibration, metadata, sequence numbering and moved
verdict a hand-triggered one would -- a timelapse is not a different kind of
photograph, it is the same photograph repeated.

Scheduling is start-to-start: shot k fires at t0 + k*interval, so the run
does not drift by the length of each capture. A shot that overruns its slot
fires the next one immediately rather than skipping it -- for subjects that
change slowly (crystal growth, drying mounts), a late frame beats a hole.

Two things this knows that a cron loop would not:

  * The dark master ages. This sensor has no cooling, and the store expires
    darks after eight hours -- a long run crosses that line and later frames
    silently lose their calibration. The status carries a flag once the run
    outlives the dark, so the UI can say so instead of nobody noticing.
  * Disk. Forty megabytes a frame adds up; the status reports bytes written
    so far and the free space remaining on the capture volume.
"""
from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from typing import Callable

#: The store's dark expiry, mirrored here so the status can warn when a run
#: outlives its calibration. Kept in one place there; this is a display hint.
DARK_MAX_AGE_S = 8 * 3600


@dataclass(frozen=True)
class TimelapseStatus:
    running: bool
    shot: int                  # completed so far
    count: int                 # 0 means until stopped
    next_in: float             # seconds to the next trigger, 0 when firing
    written_bytes: int = 0
    free_bytes: int = 0
    #: Set once the run has been going longer than a dark master stays valid.
    dark_stale: bool = False
    message: str = ""

    @property
    def summary(self) -> str:
        if not self.running:
            return self.message
        of = f"/{self.count}" if self.count else ""
        bits = [f"timelapse {self.shot}{of}"]
        if self.next_in >= 1:
            bits.append(f"next in {self.next_in:.0f}s")
        if self.written_bytes:
            bits.append(f"{self.written_bytes / 1e9:.1f} GB written")
        if self.free_bytes and self.free_bytes < 5e9:
            bits.append(f"{self.free_bytes / 1e9:.1f} GB free")
        if self.dark_stale:
            bits.append("dark is stale -- reshoot it")
        return " · ".join(bits)


class Timelapse:
    """Runs a StillCapture on an interval. One at a time, stoppable."""

    def __init__(self, capture,
                 on_status: Callable[[TimelapseStatus], None] | None = None,
                 on_shot: Callable[[object], None] | None = None) -> None:
        self._capture = capture
        self._on_status = on_status or (lambda _s: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._written = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval_s: float, count: int = 0, *, setup=None,
              subject: str = "", slide: str = "", frames: int = 1) -> bool:
        if self.running or interval_s <= 0:
            return False
        self._stop.clear()
        self._written = 0
        self._thread = threading.Thread(
            target=self._loop,
            args=(float(interval_s), int(count), setup, subject, slide,
                  int(frames)),
            daemon=True, name="timelapse")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def note_written(self, size_bytes: int) -> None:
        """The capture result's file size, fed back for the disk readout."""
        self._written += max(0, int(size_bytes))

    # ---- the loop --------------------------------------------------------

    def _loop(self, interval: float, count: int, setup, subject: str,
              slide: str, frames: int) -> None:
        t0 = time.monotonic()
        shot = 0
        while not self._stop.is_set() and (count == 0 or shot < count):
            # Start-to-start: no drift, and an overrun fires immediately.
            due = t0 + shot * interval
            while True:
                wait = due - time.monotonic()
                if wait <= 0:
                    break
                self._emit(shot, count, wait, t0)
                if self._stop.wait(min(wait, 1.0)):
                    self._emit(shot, count, 0, t0, done=True,
                               message=f"timelapse stopped -- {shot} frames")
                    return
            if not self._capture.trigger(setup, subject=subject, slide=slide,
                                         frames=frames):
                # Something else is mid-capture; try again shortly rather
                # than silently dropping the slot.
                if self._stop.wait(0.25):
                    break
                continue
            while self._capture.busy:
                if self._stop.wait(0.1):
                    # Let the in-flight shot finish; it still counts.
                    while self._capture.busy:
                        time.sleep(0.1)
                    shot += 1
                    self._emit(shot, count, 0, t0, done=True,
                               message=f"timelapse stopped -- {shot} frames")
                    return
            shot += 1
            # No "next in" after the final shot -- there is no next.
            left = (0.0 if count and shot >= count
                    else max(0.0, t0 + shot * interval - time.monotonic()))
            self._emit(shot, count, left, t0)
        self._emit(shot, count, 0, t0, done=True,
                   message=f"timelapse finished -- {shot} frames")

    def _emit(self, shot: int, count: int, next_in: float, t0: float,
              done: bool = False, message: str = "") -> None:
        free = 0
        try:
            root = getattr(getattr(self._capture, "_settings", None),
                           "capture_root", None)
            if root:
                free = shutil.disk_usage(root).free
        except OSError:
            pass
        self._on_status(TimelapseStatus(
            running=not done, shot=shot, count=count, next_in=next_in,
            written_bytes=self._written, free_bytes=free,
            dark_stale=(time.monotonic() - t0) > DARK_MAX_AGE_S,
            message=message))
