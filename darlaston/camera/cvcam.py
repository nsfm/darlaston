"""The part of a UVC camera that is the same on every platform.

A camera streamed through `cv2.VideoCapture` has one shape wherever it
is: a reader thread that owns the capture and blocks in `read` for a
whole frame period, control writes queued so the interface never waits
on that read, a pool of frames for the live view, and a capture that
stops the preview, asks for the best format at the largest size, waits
for the pipeline to catch up, and restores everything after.

That shape was written once for V4L2 and measured on real hardware --
every number in it (settle frames, warm-up frames, the lock discipline)
was found by watching a camera misbehave. Writing it a second time for
Windows would mean a second copy of every one of those lessons, and the
next bug fixed in one of them and not the other. So the loop lives here,
and a platform backend adds only what differs: how a device is found,
what it is called, and how a control value reaches it.

What a subclass provides:

  * `_open_capture()` -- a `cv2.VideoCapture` that is open, or an
    exception that says why not.
  * `_write_control(what, value)` -- the actual write, called holding
    the capture lock on the reader thread. "exposure" arrives in
    microseconds and "gain" as a percentage; the subclass knows the
    device's units.
  * `_usable` -- the pixel formats worth streaming, best first, and
    `_info` built by `open`.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np

from .base import CameraBackend, CameraInfo, Resolution
from .buffers import BufferPool, Frame

#: Thrown away after opening, and after any control change. The first
#: frame off a freshly opened UVC camera can be garbage -- measured at a
#: mean of 254 where the settled value was 89 -- and an exposure change
#: takes exactly two frames to appear: frame 0 carries the old value,
#: frame 1 is transitional, frame 2 onward is stable. Repeatable in both
#: directions across four transitions, so the number is measured rather
#: than picked.
SETTLE_FRAMES = 3
WARMUP_FRAMES = 8

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
PREFERRED_FOURCC = ("Y16 ", "GREY", "YUYV", "YUY2", "UYVY", "YU12", "NV12",
                    "RGB3", "RGB4", "MJPG")


def usable_formats(formats: list[str]) -> list[str]:
    """The device's formats, best first, with the undecodable ones gone."""
    offered = [f for f in formats if f.strip() not in REFUSED_FOURCC]
    rank = {name.strip(): i for i, name in enumerate(PREFERRED_FOURCC)}
    return sorted(offered, key=lambda f: rank.get(f.strip(), 99))


class CvCaptureBackend(CameraBackend):
    """A colour camera behind `cv2.VideoCapture`. Colour in, colour out."""

    #: What the reader thread is called, for anyone reading a dump.
    THREAD_NAME = "cv-camera"

    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None
        self._info: CameraInfo | None = None
        self._pool: BufferPool | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._seq = 0
        self._exposure_us = 20000
        self._gain_pct = 100
        #: Formats worth streaming, best first. Filled in by `open`.
        self._usable: list = []
        #: Control changes waiting for the reader thread. Guarded by its
        #: own lock, which is only ever held for a dictionary write --
        #: never across a frame read, which is the whole point.
        self._pending: dict = {}
        self._pending_lock = threading.Lock()
        self._current = 0
        self._on_frame: Callable[[Frame], None] | None = None

    # ---- what a subclass supplies ---------------------------------------

    def _open_capture(self) -> cv2.VideoCapture:
        raise NotImplementedError

    def _write_control(self, what: str, value) -> None:
        """The actual writes. Called holding `_lock`, never from the UI."""

    # ---- lifecycle -------------------------------------------------------

    def _warm_up(self) -> None:
        """Discard the first frames, which may be blown or empty."""
        for _ in range(WARMUP_FRAMES):
            try:
                self._cap.grab()
            except Exception:
                break

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

    def _format(self) -> str:
        """The pixel format the device is delivering right now."""
        got = int(self._cap.get(cv2.CAP_PROP_FOURCC))
        return "".join(chr((got >> (8 * i)) & 0xFF) for i in range(4))

    def _set_format(self, fourcc: str) -> str:
        """Ask for a pixel format, and report what was actually granted.

        Reported rather than assumed, because `set` returns True for
        formats the device will not really deliver -- measured on a
        camera that accepts H264 and then hands back frames nothing can
        decode.
        """
        self._cap.set(cv2.CAP_PROP_FOURCC,
                      cv2.VideoWriter_fourcc(*fourcc.ljust(4)[:4]))
        return self._format()

    def best_capture_format(self) -> str:
        """The best format this camera will really give us for a capture.

        Uncompressed if there is any, because MJPG subsampling and
        ringing land exactly on detail a few pixels wide, which is what a
        diatom's striae are. It costs frame rate -- measured at 6 fps
        against MJPG's 30 at 1920x1080 -- which is why this is only used
        for captures and the preview stays fast.
        """
        for name in (self._usable or ["MJPG"]):
            if name.strip() != "MJPG":
                return name
        return "MJPG"

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
            if self._cap is None:
                return         # closed underneath us; not an error
            self._select(index)
        if streaming and on_frame is not None:
            self.start_stream(on_frame)

    def _apply_pending(self) -> None:
        """Write any queued control changes. Reader thread only."""
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        if not pending:
            return
        with self._lock:
            if self._cap is None:
                return
            for what, value in pending.items():
                try:
                    self._write_control(what, value)
                except Exception:
                    pass          # a dropped link surfaces elsewhere

    def _queue(self, what: str, value) -> None:
        """Ask for a control change without waiting for one.

        Always queued first, under the small lock, so there is no window
        where a stream starting or stopping loses the value or sends the
        caller to the capture lock -- which the reader holds across a
        whole frame read, and which is the freeze this exists to avoid.

        Only when no reader is running does the caller apply it, and by
        then the value is already recorded, so the two paths cannot
        disagree about what was asked for.
        """
        with self._pending_lock:
            self._pending[what] = value
        if not self._running.is_set():
            self._apply_pending()

    def set_exposure(self, microseconds: int) -> None:
        self._exposure_us = int(microseconds)
        self._queue("exposure", int(microseconds))

    def set_gain(self, percent: int) -> None:
        """Map our percentage onto whatever scale this device uses.

        Queued rather than written: this is reached from a slider, and
        the reader holds the capture for a whole frame period at a time.
        """
        self._gain_pct = int(percent)
        self._queue("gain", int(percent))

    def _flush(self) -> None:
        """Throw away the frames still carrying the old setting."""
        for _ in range(SETTLE_FRAMES):
            try:
                self._cap.grab()
            except Exception:
                return

    def get_exposure(self) -> int:
        """What we last asked for, not what the device says.

        Asking the device means taking the capture lock, which the reader
        holds for a whole frame period -- so this froze the window from
        two Qt slots, exactly as the setters used to. It was also called
        once per frame from the reader itself, a lock round trip and an
        ioctl for a number already in hand.

        The value is ours: nothing else writes the exposure.
        """
        return self._exposure_us

    def get_gain(self) -> int:
        with self._lock:
            if self._cap is None:
                return self._gain_pct
            value = self._cap.get(cv2.CAP_PROP_GAIN)
        return int(value) if value and value > 0 else self._gain_pct

    # ---- streaming -------------------------------------------------------

    def start_stream(self, on_frame: Callable[[Frame], None]) -> None:
        if self._thread is not None or self._cap is None:
            return
        res = self._info.resolutions[self._current]
        self._on_frame = on_frame
        self._pool = BufferPool((res.height, res.width, 3), np.uint8, count=4)
        self._running.set()
        self._thread = threading.Thread(target=self._loop, args=(on_frame,),
                                        daemon=True, name=self.THREAD_NAME)
        self._thread.start()

    def _drain_pending(self) -> None:
        """Apply anything queued, now, on the caller's thread.

        Called where the reader is about to stop or has stopped, so a
        change made a moment ago is not simply discarded. That mattered
        most before a capture: `grab_raw` stops the stream first, so an
        exposure moved and then captured immediately was taken at the old
        value while the frame's own metadata reported the new one.
        """
        self._apply_pending()

    def stop_stream(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                # The reader is stuck in a long `read`. Leaving the
                # handle behind and starting another would give two
                # threads writing one buffer pool, so keep it and let
                # `start_stream` refuse rather than double up.
                return
            self._thread = None
        # Whatever the interface queued while the reader was blocked in
        # its last `read` is still waiting, and there is nobody left to
        # apply it. Draining here is what stops a capture being taken at
        # the previous exposure while reporting the new one.
        self._drain_pending()

    def _loop(self, on_frame: Callable[[Frame], None]) -> None:
        while self._running.is_set():
            # Controls are applied here, by the thread that owns the
            # capture, between frames. Nothing else may touch it while a
            # stream is running: `read` blocks for a whole frame period,
            # so anyone waiting on the same lock waits that long, and at
            # a half-second exposure that is a half-second of frozen
            # window per slider movement.
            self._apply_pending()
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

    # ---- profiling -------------------------------------------------------

    def snapshot(self, index: int):
        """One settled greyscale frame from a given resolution mode.

        For measuring what the camera does rather than for keeping. Mode
        changes take about a second on this hardware and the first frames
        after one still come from the old pipeline, so this waits rather
        than trusting the first thing it is handed.
        """
        with self._lock:
            if self._cap is None:
                raise RuntimeError("camera is not open")
            previous = self._current
            self._select(index)
            for _ in range(SETTLE_FRAMES + 5):
                self._cap.read()
            ok, image = self._cap.read()
            self._select(previous)
        if not ok or image is None:
            raise RuntimeError(f"no frame from mode {index}")
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def level(self) -> float:
        """The mean level of a settled frame, for a photometric sweep.

        This is where waiting for a control change belongs -- not in the
        setter, which a slider calls on the interface thread. A frame
        change lands on the third frame, measured in both directions, so
        discarding `SETTLE_FRAMES` and reading the next is enough.
        """
        with self._lock:
            if self._cap is None:
                raise RuntimeError("camera is not open")
            self._flush()
            ok, image = self._cap.read()
        return float(image.mean()) if ok and image is not None else 0.0

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
                # Read before switching. This used to restore whatever
                # `_set_format` reported *granting*, compare it against a
                # hard-coded "MJPG" and put the camera back into MJPG on
                # any mismatch -- so a camera with no MJPG at all, whose
                # preview was already the uncompressed format, was left
                # being asked for a format it does not have.
                before = self._format()
                # Uncompressed for the capture where the camera has one.
                # The preview stays compressed and fast; this is the one
                # frame that has to be worth keeping.
                self._set_format(self.best_capture_format())
                self._select(0)               # index 0 is the largest
                # The first frames after a mode change come from the old
                # pipeline; discard a few rather than saving one.
                for _ in range(SETTLE_FRAMES + 3):
                    self._cap.read()
                ok, image = self._cap.read()
                self._select(previous)
                if self._format() != before:
                    self._set_format(before)   # back to the fast preview
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
