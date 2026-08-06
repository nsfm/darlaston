"""CameraSession -- owns the handle, the state machine, and reconnection.

Nothing else opens or closes a camera.

The whole point is that **disconnection is a state, not an exception**. The USB
link runs near saturation at full resolution and will drop when the cable is
nudged; two documented SDK hazards compound it (INDIGO: exposure is impossible
after a reopen without a reconnect; INDI: the camera switches between video and
trigger modes underneath you). So acquisition restarts underneath an analysis
thread and a window that both stay alive, and settings are re-applied in full
because the SDK does not remember them and half-applied state is worse than
none.

No Qt here. Callbacks only -- see ARCHITECTURE.md 4 and 5.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .base import CameraBackend, CameraInfo, CameraState
from .buffers import Frame
from . import usb
from .errors import CameraProblem, NoCameraFound
from ..cpu import usable_cores
from ..i18n import _

_log = logging.getLogger(__name__)


#: The preview rates the status bar offers, and therefore the only rates a
#: default may pick. Kept here rather than in the widget so the two cannot
#: drift apart: a default the combo has no entry for would leave the box
#: showing one number while the camera ran at another.
FRAMERATES = (15, 24, 30, 40, 60, 0)      # 0 is uncapped


def default_framerate_cap(cores: int | None = None) -> int:
    """Opening preview rate for this machine.

    Deliberately coarse. The thresholds come from measured budget headroom,
    not from taste, and the operator can move the rate afterwards -- the
    point is only that a laptop should not open at a workstation's settings
    and start dropping frames before anyone has touched anything.
    """
    cores = usable_cores() if cores is None else cores
    if cores >= 8:
        return 40
    if cores >= 4:
        return 30
    return 15


@dataclass(frozen=True)
class SessionStatus:
    state: CameraState
    info: CameraInfo | None = None
    link: usb.LinkInfo | None = None
    message: str = ""
    detail: str = ""
    #: Concrete things the operator can do about it, in order. Empty
    #: unless the failure knew.
    steps: tuple[str, ...] = ()
    #: Machine-readable failure category, so the window can offer the
    #: right button without reading the prose.
    kind: str = ""
    attempt: int = 0
    next_retry_in: float = 0.0

    @property
    def is_live(self) -> bool:
        return self.state is CameraState.STREAMING


class CameraSession:
    """Drives one backend through connect / stream / recover.

    Construction does not touch hardware, so a window can open before a camera
    exists -- which was the bug this class was written to remove.
    """

    POLL_INTERVAL = 1.0
    RETRY_BACKOFF = (0.5, 1.0, 2.0, 3.0, 5.0)

    def __init__(self, make_backend: Callable[[], CameraBackend],
                 on_status: Callable[[SessionStatus], None],
                 on_frame: Callable[[Frame], None],
                 # Index 0: the full field. This was 2, which on a
                 # ToupTek is a binned preview of the whole sensor and on
                 # a UVC camera is an arbitrary mode -- and those modes
                 # *crop*, so the preview showed the middle of what a
                 # capture would record and framing on it was misleading.
                 preview_resolution: int = 0,
                 is_present: Callable[[], bool] | None = None) -> None:
        # Presence is a property of the backend, not an assumption about USB.
        # A synthetic camera is always present; a tethered one may answer
        # differently again. Defaulting to "always" keeps the supervisor from
        # gating a backend it knows nothing about.
        self._is_present = is_present or (lambda: True)
        self._make_backend = make_backend
        self._on_status = on_status
        self._on_frame = on_frame
        self._preview = preview_resolution

        self._backend: CameraBackend | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._attempt = 0

        # Desired settings, re-applied on every (re)connect because the SDK
        # forgets them and a partially configured camera is worse than none.
        self._exposure_us = 8330
        self._gain_pct = 100
        #: Live preview rate. Frames we drop were still pulled -- memcpy'd off
        #: USB with the SDK's thread holding the GIL -- so asking the camera
        #: for fewer beats discarding more.
        #:
        #: This was 30 when analysis topped out near 47 fps. After profiling
        #: (see TODO) the live loop sustains about 57, so 30 had become the
        #: binding constraint rather than a sensible ceiling: a reported
        #: 27 fps was 90% of our own cap, not evidence of a bottleneck.
        #:
        #: 60 was tried and was too much: on real hardware it produced 25-45
        #: fps with a quarter of frames discarded, which is the failure this
        #: cap exists to prevent -- every dropped frame was still pulled over
        #: USB with the SDK's thread holding the GIL. 40 asks for slightly
        #: more than the loop sustains without paying for frames nobody sees,
        #: and it is adjustable in the status bar because the right number
        #: depends on the machine, the link and the preview resolution. It is
        #: also a courtesy: pinning a CPU to render frames the eye cannot use
        #: is not a feature. 0 disables the cap.
        #:
        #: 40 is right for the machine this was written on and wrong for a
        #: small one, so the default now follows the core count. Measured on
        #: the same camera and scene, with the process pinned: sixteen cores
        #: analyse a frame in 12.7 ms of a 25 ms budget and drop nothing,
        #: four take 29.0 ms, and two take 37.5 ms and drop a sixth of the
        #: frames. Most of that is not arithmetic -- the stages themselves
        #: measure about 3.5 ms of real work -- it is the analysis thread
        #: being descheduled while the SDK demosaics and Qt paints on the
        #: same few cores. So the cure is less work arriving, not cheaper
        #: work, and the rate is the only control that reaches all three:
        #: capping 40 to 15 took a two-core machine from 185% of a core to
        #: 80%. Still adjustable in the status bar; this only picks the
        #: opening guess.
        self.framerate_cap = default_framerate_cap()

        self._status = SessionStatus(CameraState.DISCONNECTED,
                                     message=_("session.waiting"))

    # ---- public surface --------------------------------------------------

    @property
    def status(self) -> SessionStatus:
        return self._status

    @property
    def backend(self) -> CameraBackend | None:
        return self._backend

    def start(self) -> None:
        if self._thread is not None:
            return
        self._running.set()
        self._thread = threading.Thread(target=self._supervise, daemon=True,
                                        name="camera-session")
        self._thread.start()

    def retarget(self, make_backend, is_present=None) -> None:
        """Point this session at a different camera, in place.

        The liveness check comes with it. It is specific to one device --
        one `/dev/video` node, one USB vendor id -- so leaving the old
        one behind means watching the camera we just stopped using and
        reporting *it* as disconnected. None means "no cheap check", which
        is the right answer for the synthetic camera and for any backend
        whose absence only its own errors can report.

        **The object identity is the whole point.** `StillCapture`, the
        calibration service and the opportunist are each handed this
        session when the window is built and hold it for the life of the
        program. Building a *new* session to switch cameras leaves all
        three pointing at the old one, which by then has no backend -- so
        the preview runs from the new camera while a capture reports "no
        camera connected" from the old.

        That was a real bug and an old one: switching to the synthetic
        camera did it too, long before there was more than one real
        camera to switch between.
        """
        self.stop()
        self._make_backend = make_backend
        self._is_present = is_present or (lambda: True)
        self._attempt = 0
        self.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=4.0)
            self._thread = None
        self._teardown()
        self._publish(CameraState.DISCONNECTED, message="Stopped")

    def actual_settings(self) -> tuple[int, int] | None:
        """(exposure_us, gain_pct) as the camera has them, or None if closed."""
        with self._lock:
            b = self._backend
        if b is None:
            return None
        try:
            return b.get_exposure(), b.get_gain()
        except Exception:
            return None

    @property
    def preview_resolution(self) -> int:
        return self._preview

    def set_preview_resolution(self, index: int) -> bool:
        """Restart the live stream at a different sensor mode.

        The mode cannot be changed while the stream runs, so this stops and
        restarts it -- a visible blink, which is honest about what is
        happening. Captures are unaffected: `grab_raw` always selects full
        resolution for itself, so a smaller preview costs nothing but preview
        detail, while every per-pixel stage of the live loop gets cheaper with
        the square of the reduction.
        """
        index = int(index)
        if index == self._preview:
            return True
        with self._lock:
            backend = self._backend
        self._preview = index
        if backend is None:
            return True
        try:
            backend.stop_stream()
            backend.set_resolution(index)
            backend.start_stream(self._on_frame)
            backend.set_exposure(self._exposure_us)
            backend.set_gain(self._gain_pct)
            if self.framerate_cap and hasattr(backend, "set_framerate_cap"):
                backend.set_framerate_cap(self.framerate_cap)
            return True
        except Exception:
            # The supervisor notices a dead stream and reconnects; saying so
            # in the interface here would race with it. It still goes in the
            # log, which is the only place a report can come from.
            _log.exception("could not restart the stream after a mode change")
            return False

    def set_framerate_cap(self, fps: int) -> bool:
        """Ask the camera for a different rate. No restart needed."""
        self.framerate_cap = int(fps)
        with self._lock:
            backend = self._backend
        if backend is None or not hasattr(backend, "set_framerate_cap"):
            return False
        try:
            backend.set_framerate_cap(self.framerate_cap)
            return True
        except Exception:
            return False

    def set_exposure(self, microseconds: int) -> None:
        self._exposure_us = int(microseconds)
        with self._lock:
            if self._backend is not None:
                try:
                    self._backend.set_exposure(self._exposure_us)
                except Exception:
                    pass   # a dropped link surfaces through the supervisor

    def set_gain(self, percent: int) -> None:
        self._gain_pct = int(percent)
        with self._lock:
            if self._backend is not None:
                try:
                    self._backend.set_gain(self._gain_pct)
                except Exception:
                    pass

    # ---- the supervisor --------------------------------------------------

    def _supervise(self) -> None:
        while self._running.is_set():
            try:
                if self._backend is None:
                    self._try_connect()
                else:
                    self._watch()
            except Exception as exc:              # never let the loop die
                self._fail(exc)
            time.sleep(self.POLL_INTERVAL)

    def _try_connect(self) -> None:
        link = usb.probe()
        if not self._is_present():
            # No detail line: it pointed at a synthetic camera that is no
            # longer offered, and anything else here only restates the
            # heading in more words.
            self._wait(link)
            return

        self._attempt += 1
        # Only the first attempt says so. A retry of something that has
        # already failed is not news, and publishing it made the waiting
        # page collapse to a one-line "Connecting" and expand again on
        # the next failure, once every few seconds, for ever.
        if self._attempt == 1:
            self._publish(CameraState.CONNECTING, link=link,
                          message=_("session.connecting"),
                          attempt=self._attempt)

        backend = self._make_backend()
        try:
            info = backend.open()
        except NoCameraFound:
            # Not a failure. This is the SDK reporting an empty
            # enumeration, which is the same fact sysfs gives us on Linux
            # and which Linux answers with a calm waiting screen -- it
            # simply arrives here later, because off Linux there is no bus
            # to read and `usb.present` has to guess yes.
            #
            # Rendering it as a fault was why macOS showed an error page
            # with numbered steps, and a flash, where Linux showed nothing
            # of the kind with the same camera in the same drawer.
            self._attempt = 0
            self._wait(link)
            return
        with self._lock:
            self._backend = backend
        self._attempt = 0

        backend.set_resolution(self._preview)
        backend.start_stream(self._on_frame)
        backend.set_exposure(self._exposure_us)
        backend.set_gain(self._gain_pct)
        if self.framerate_cap and hasattr(backend, "set_framerate_cap"):
            backend.set_framerate_cap(self.framerate_cap)

        detail = ""
        if link.is_degraded and link.advice:
            detail = link.advice
        self._publish(CameraState.STREAMING, info=info, link=link,
                      message="Live", detail=detail)

    def _wait(self, link) -> None:
        """Nothing is plugged in, and that is not a fault.

        One place, because two of them drifted: the state a session starts
        in and the state it returns to have to look identical or the
        difference reads as something having gone wrong.
        """
        self._publish(CameraState.DISCONNECTED, link=link,
                      message=_("session.waiting"))

    def _watch(self) -> None:
        """Cheap liveness check: if the device left the bus, the link is gone.

        Polling sysfs is far cheaper and far more decisive than waiting for the
        SDK to report a failure, which it often does not.
        """
        if self._is_present():
            return
        self._lost()

    def _lost(self) -> None:
        link = usb.probe()
        self._teardown()
        self._publish(
            CameraState.ERROR, link=link, message="Camera disconnected",
            detail="Reconnecting and restoring exposure, gain and mode. "
                   "Anything in progress is paused, not lost.")

    def _fail(self, exc: Exception) -> None:
        self._teardown()
        wait = self.RETRY_BACKOFF[min(self._attempt, len(self.RETRY_BACKOFF) - 1)]
        # A typed problem already knows how to describe itself and what to
        # tell the operator to do; anything else falls back to guessing
        # from the text, which is what everything used to do.
        if isinstance(exc, CameraProblem):
            heading, detail = exc.heading, exc.detail
            steps, kind = exc.steps, exc.kind
        else:
            heading, detail = "Could not open the camera", self._explain(exc)
            steps, kind = (), ""
        self._publish(CameraState.ERROR, link=usb.probe(),
                      message=heading, detail=detail, steps=steps, kind=kind,
                      attempt=self._attempt, next_retry_in=wait)
        time.sleep(wait)

    @staticmethod
    def _explain(exc: Exception) -> str:
        """Say what went wrong and what to do, never just the exception text."""
        text = str(exc).lower()
        if "another" in text or "busy" in text or "access" in text:
            return ("Another program is holding the camera. Close ToupLite or "
                    "any other capture software and it will connect.")
        if "not found" in text or "no toup" in text:
            return "No camera responded. Check the cable and try again."
        return str(exc) or exc.__class__.__name__

    def _teardown(self) -> None:
        with self._lock:
            backend, self._backend = self._backend, None
        if backend is None:
            return
        try:
            backend.stop_stream()
        except Exception:
            pass
        try:
            backend.close()
        except Exception:
            pass

    def _publish(self, state: CameraState, **kw) -> None:
        status = SessionStatus(state, **kw)
        self._status = status
        try:
            self._on_status(status)
        except Exception:
            _log.exception("a status handler raised")
