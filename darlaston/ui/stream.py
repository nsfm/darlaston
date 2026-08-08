"""Serve the presentation over the local network.

The presentation window shares a screen with whoever is standing near
it. This shares the same face with whoever is on the network: OBS on a
streaming rig reads it as a browser or media source, and any browser on
the LAN -- a tablet propped on the table, a smart TV -- can just open
the address.

Deliberately MJPEG over plain HTTP. Every consumer already speaks it,
it needs nothing outside the standard library and the JPEG encoder
already shipped, and the re-encode to something better is the streaming
rig's job -- OBS transcodes whatever it is given, so cleverness here
would be paid for twice.

The live-path discipline holds (see live/cell.py): one frame slot,
replace on write, so a slow client is always shown *now* rather than a
backlog. Nothing is encoded while nobody is connected, and nothing at
all is served unless the operator turns it on -- a program used on an
institution's network must never be the reason a port is open that
nobody chose to open.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
from PySide6 import QtCore, QtGui

from ..i18n import _
from .present import PresentView
from .widgets import POINTER_S, UI_METER

_log = logging.getLogger(__name__)

#: What the stream renders at, and how often. 1080p is the streaming
#: canvas everything downstream expects, and nothing about glass under
#: a microscope needs the sensor's full grid on the wire. Thirty frames
#: a second, because a live stream that visibly stutters reads as
#: broken however sharp it is; the encode lands around ten milliseconds
#: a frame on a worker core, and the operator's own preview never pays
#: any of it.
STREAM_SIZE = (1920, 1080)
MAX_FPS = 30.0
JPEG_QUALITY = 85

#: How long the feed may go quiet before the stream's live marker goes
#: out -- the same promise the window makes.
STALE_S = 2.0


def _lan_ip() -> str:
    """The address a neighbour would reach this machine at.

    A UDP connect performs a route lookup and sends nothing at all --
    no packet leaves the machine for this question to be answered.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 1))
            return probe.getsockname()[0]
    except OSError:
        return "localhost"


class _Server(ThreadingHTTPServer):
    """One current JPEG, and however many readers of it.

    Clients wait on a condition for a frame newer than the one they
    last wrote; the encoder publishes and wakes them all. A generation
    counter rather than a queue, for the same reason the live path is a
    cell: a reader that falls behind skips, it never laps.
    """

    daemon_threads = True

    def __init__(self, address) -> None:
        super().__init__(address, _Handler)
        self.cond = threading.Condition()
        self.jpeg: bytes | None = None
        self.generation = 0
        self.clients = 0
        self.closing = False

    def publish(self, jpeg: bytes) -> None:
        with self.cond:
            self.jpeg = jpeg
            self.generation += 1
            self.cond.notify_all()

    def next_after(self, seen: int, timeout: float):
        """The newest frame once it is newer than `seen`, else (None, seen)."""
        with self.cond:
            if self.generation == seen and not self.closing:
                self.cond.wait(timeout)
            if self.generation == seen or self.jpeg is None:
                return None, seen
            return self.jpeg, self.generation

    def wake_all(self) -> None:
        with self.cond:
            self.closing = True
            self.cond.notify_all()


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title><style>
html, body {{ margin: 0; height: 100%; background: #101210; }}
img {{ width: 100%; height: 100%; object-fit: contain; }}
</style></head><body><img src="/stream" alt=""></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802 -- the stdlib's spelling
        if self.path in ("/", "/index.html"):
            body = _PAGE.format(title=_("app.title")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in ("/still", "/still.jpg"):
            # One photograph, freshly rendered: waiting for the next
            # publish counts as an audience, so the interface starts
            # feeding the moment somebody asks.
            with self.server.cond:
                self.server.clients += 1
                seen = self.server.generation
            try:
                jpeg, _seen = self.server.next_after(seen, timeout=2.0)
                if jpeg is None:
                    jpeg = self.server.jpeg
            finally:
                with self.server.cond:
                    self.server.clients -= 1
            if jpeg is None:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Content-Disposition",
                             'inline; filename="darlaston.jpg"')
            self.end_headers()
            self.wfile.write(jpeg)
            return
        if self.path != "/stream":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        seen = 0
        with self.server.cond:
            self.server.clients += 1
        try:
            while not self.server.closing:
                jpeg, seen = self.server.next_after(seen, timeout=1.0)
                if jpeg is None:
                    continue
                self.wfile.write(b"--frame\r\n"
                                 b"Content-Type: image/jpeg\r\n"
                                 b"Content-Length: "
                                 + str(len(jpeg)).encode("ascii")
                                 + b"\r\n\r\n" + jpeg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                       # the viewer left; that is not an event
        finally:
            with self.server.cond:
                self.server.clients -= 1

    def log_message(self, fmt: str, *args) -> None:
        _log.debug("stream: " + fmt, *args)


class Streamer(QtCore.QObject):
    """The presentation, rendered off screen and served.

    Owns its own `PresentView` at a fixed stream size, fed by the main
    window exactly as the visible one is. The view is rendered to pixels
    on the interface thread -- a blit and some text -- and everything
    that costs real time, the JPEG encode, happens on a worker.

    No measured-screen figure is ever fed to this view: the audience
    here is on screens nobody has measured, and unmeasured claims
    nothing.
    """

    def __init__(self, port: int, parent=None) -> None:
        super().__init__(parent)
        self.view = PresentView()
        self.view.resize(*STREAM_SIZE)
        self._server = _Server(("", int(port)))
        self._frame: np.ndarray | None = None
        self._frame_cond = threading.Condition()
        self._closed = False
        self._last_fed = 0.0
        self._last_pushed = 0.0
        self._last_published = 0.0
        self._encoder = threading.Thread(target=self._encode_loop,
                                         name="present-stream", daemon=True)
        self._listener = threading.Thread(target=self._server.serve_forever,
                                          name="present-stream-http",
                                          daemon=True)
        self._stale = QtCore.QTimer(self)
        self._stale.setInterval(1000)
        self._stale.timeout.connect(self._check_stale)
        #: Re-renders while the presenter's ring blooms, because the
        #: ring animates whether or not frames are arriving -- a held
        #: picture being pointed at is the whole use case.
        self._pointer_until = 0.0
        self._animate = QtCore.QTimer(self)
        self._animate.setInterval(33)
        self._animate.timeout.connect(self._animate_tick)
        self._encoder.start()
        self._listener.start()
        self._stale.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{_lan_ip()}:{self.port}/"

    def wants_frames(self) -> bool:
        """Whether anybody is watching. Nothing is rendered or encoded
        for an audience of zero."""
        return self._server.clients > 0

    # ---- fed from the interface thread ----------------------------------

    def frame(self, bgr: np.ndarray) -> None:
        start = time.perf_counter()
        try:
            self._last_fed = time.monotonic()
            self.view.set_live_lit(True)
            if self.view._held:
                return
            # Paced here as well as at the encoder, so a camera running
            # faster than the stream does not spend interface time
            # rendering frames the worker would only drop.
            if self._last_fed - self._last_pushed < 0.9 / MAX_FPS:
                return
            self.view.set_frame(bgr)
            self._push()
        finally:
            UI_METER.since("stream", start)

    def set_held(self, held: bool) -> None:
        """Mirror the presentation's hold. One publish either way, so
        the marker's change of mind reaches viewers of a stream that is
        otherwise frozen."""
        self.view.set_held(held)
        self._push()

    def pointer(self, fx: float, fy: float) -> None:
        """The presenter's ring, published immediately and then
        re-rendered on a timer until it has faded -- the stream's frames
        cannot be trusted to arrive while the picture is held."""
        self.view.set_pointer(fx, fy)
        self._push()
        self._pointer_until = time.monotonic() + POINTER_S + 0.2
        self._animate.start()

    def _animate_tick(self) -> None:
        self._push()
        if time.monotonic() > self._pointer_until:
            self._animate.stop()

    def _push(self) -> None:
        """Render the view as it stands and hand it to the encoder.
        One slot, replace on write: a slow encode is shown now, later,
        rather than everything since, late."""
        image = QtGui.QImage(self.view.width(), self.view.height(),
                             QtGui.QImage.Format.Format_BGR888)
        self.view.render(image)
        raw = np.frombuffer(image.constBits(), np.uint8)
        rows = raw.reshape(image.height(), image.bytesPerLine())
        pixels = rows[:, :image.width() * 3].reshape(
            image.height(), image.width(), 3).copy()
        self._last_pushed = time.monotonic()
        with self._frame_cond:
            self._frame = pixels
            self._frame_cond.notify()

    def _check_stale(self) -> None:
        """The same honesty the window keeps: if frames stop arriving,
        the live claim comes down -- and one more render goes out, or
        the change of mind would never reach a frozen stream."""
        if (self._last_fed and self.view._live_lit
                and time.monotonic() - self._last_fed > STALE_S):
            self.view.set_live_lit(False)
            self._push()

    # ---- the worker ------------------------------------------------------

    def _encode_loop(self) -> None:
        interval = 1.0 / MAX_FPS
        while True:
            with self._frame_cond:
                if self._frame is None and not self._closed:
                    self._frame_cond.wait(1.0)
                frame, self._frame = self._frame, None
                if self._closed:
                    return
            if frame is None:
                continue
            wait = self._last_published + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
                # Something newer may have landed while this one waited
                # out the pacing; it is the one worth encoding.
                with self._frame_cond:
                    newer, self._frame = self._frame, None
                if newer is not None:
                    frame = newer
            ok, jpeg = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                self._last_published = time.monotonic()
                self._server.publish(jpeg.tobytes())

    def stop(self) -> None:
        self._stale.stop()
        self._animate.stop()
        with self._frame_cond:
            self._closed = True
            self._frame_cond.notify_all()
        self._server.wake_all()
        self._server.shutdown()
        self._server.server_close()
        self._encoder.join(timeout=2.0)
