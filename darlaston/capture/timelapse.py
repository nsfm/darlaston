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
    silently lose their calibration. The status says so when it happens, so
    the UI can report it instead of nobody noticing.
  * Disk. Forty megabytes a frame adds up; the status reports bytes written
    so far and the free space remaining on the capture volume.

Neither of those matters as much as the third thing, which is simply
counting honestly. A timelapse is unattended by definition: nobody is
watching, and the only account of the night is the number in the status
line. It has to be the number of photographs, not the number of attempts.
"""
from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TimelapseStatus:
    running: bool
    shot: int                  # completed so far, and *kept*
    count: int                 # 0 means until stopped
    next_in: float             # seconds to the next trigger, 0 when firing
    written_bytes: int = 0
    free_bytes: int = 0
    #: Captures that started and did not produce a file.
    failed: int = 0
    #: Set once the calibration path has stopped applying a dark it was
    #: applying earlier in this run -- which is what "the dark expired"
    #: actually looks like from out here.
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
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.dark_stale:
            bits.append("dark is stale -- reshoot it")
        return " · ".join(bits)


class Timelapse:
    """Runs a StillCapture on an interval. One at a time, stoppable."""

    def __init__(self, capture,
                 on_status: Callable[[TimelapseStatus], None] | None = None
                 ) -> None:
        self._capture = capture
        self._on_status = on_status or (lambda _s: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._written = 0
        self._kept = 0
        self._failed = 0
        #: Whether the last capture had a dark applied, and whether any ever
        #: did. Both are needed: a run with no dark at all must not be
        #: reported as one whose dark went stale.
        self._dark_now = False
        self._dark_ever = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval_s: float, count: int = 0, *, setup=None,
              subject: str = "", slide: str = "", frames: int = 1) -> bool:
        if self.running or interval_s <= 0:
            return False
        self._stop.clear()
        self._written = self._kept = self._failed = 0
        self._dark_now = self._dark_ever = False
        self._thread = threading.Thread(
            target=self._loop,
            args=(float(interval_s), int(count), setup, subject, slide,
                  int(frames)),
            daemon=True, name="timelapse")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def note_result(self, result) -> None:
        """What a capture actually did, fed back from the result handler.

        The loop cannot see this for itself. `trigger` returns True the
        moment the shutter is claimed, and everything that can go wrong
        afterwards -- a full disk, a camera that left the bus, a folder
        that could not be created -- happens on the capture thread. So the
        loop was counting *attempts*, and a run in which all forty
        exposures failed still finished with "timelapse finished -- 40
        frames". Nobody is watching a timelapse; that line is the only
        account of the night, and it has to be true.

        The dark comes through here too. Whether it is *still being
        applied* is the honest question, and every result answers it.
        Comparing elapsed run time against the expiry instead said "fresh"
        for a dark shot seven hours and fifty minutes before the run --
        which the store would refuse ten minutes in -- and "stale" for one
        shot moments before it.
        """
        if not getattr(result, "ok", False):
            self._failed += 1
            return
        self._kept += 1
        applied = getattr(result, "applied", ()) or ()
        self._dark_now = "dark" in applied
        self._dark_ever = self._dark_ever or self._dark_now
        path = getattr(result, "path", None)
        if path:
            try:
                from pathlib import Path

                self._written += max(0, Path(path).stat().st_size)
            except OSError:
                pass

    # ---- the loop --------------------------------------------------------

    def _loop(self, interval: float, count: int, setup, subject: str,
              slide: str, frames: int) -> None:
        t0 = time.monotonic()
        # Attempts, not photographs. This drives the schedule -- shot k is
        # due at t0 + k*interval -- and it is also the stopping rule, so a
        # camera that fails every frame ends the run instead of retrying
        # all night. What gets *reported* is `self._kept`.
        shot = 0
        while not self._stop.is_set() and (count == 0 or shot < count):
            # Start-to-start: no drift, and an overrun fires immediately.
            due = t0 + shot * interval
            while True:
                wait = due - time.monotonic()
                if wait <= 0:
                    break
                self._emit(count, wait)
                if self._stop.wait(min(wait, 1.0)):
                    return self._finish(shot, count, "stopped")
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
                    return self._finish(shot + 1, count, "stopped")
            shot += 1
            # No "next in" after the final shot -- there is no next.
            left = (0.0 if count and shot >= count
                    else max(0.0, t0 + shot * interval - time.monotonic()))
            self._emit(count, left)
        self._finish(shot, count, "finished")

    def _finish(self, attempts: int, count: int, verb: str) -> None:
        """The last status line, once every verdict is actually in.

        `StillCapture` releases its lock before the result reaches the
        interface thread, so the loop can arrive here one frame ahead of
        the last verdict. Waiting for the tally to reconcile is the
        difference between a final count that is right and one that is
        right most of the time.

        Short when nothing is feeding results back at all. An embedding
        that never calls `note_result` -- or a test harness -- would
        otherwise pay the whole budget at the end of every run, waiting
        for something that is never coming.
        """
        budget = 2.0 if (self._kept or self._failed) else 0.5
        until = time.monotonic() + budget
        while (self._kept + self._failed) < attempts and \
                time.monotonic() < until:
            time.sleep(0.02)
        said = f"{self._kept} frames"
        if self._failed:
            said += f", {self._failed} failed"
        self._emit(count, 0.0, done=True, message=f"timelapse {verb} -- {said}")

    def _emit(self, count: int, next_in: float,
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
            running=not done, shot=self._kept, count=count, next_in=next_in,
            written_bytes=self._written, free_bytes=free,
            failed=self._failed,
            # A dark that was being applied and now is not. A run that
            # never had one is not stale, it is uncalibrated, and saying
            # "reshoot it" would be advice about a thing that never was.
            dark_stale=self._dark_ever and not self._dark_now,
            message=message))
