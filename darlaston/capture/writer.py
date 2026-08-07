"""Work that happens after the shutter, off the thread that has to be free.

The operator's obligation ends when the pixels leave the sensor. Everything
after that -- calibration, the raw, the sidecar JPEG -- is arithmetic and
bookkeeping, and none of it needs a hand held still on a focus knob.

It was all one thread, and the cost was measured on a real 18-slice stack:
5.4 s per slice, of which the exposure is a small part and 2.4 s is the
JPEG sidecar alone. That is 2.4 s per slice of somebody holding a 1960s
Zeiss motionless while this process demosaics twenty megapixels. Over a
stack it is most of the session.

So: one worker, strictly FIFO, and a bound.

FIFO because order is meaning. Slices are numbered as they land and a
stack is a sequence through Z; letting two writes race would number them
by whichever finished first, which is a function of JPEG entropy rather
than of where the focus was. One worker also keeps the queue's own memory
predictable and leaves the cores to the live preview, which is still
running and is what the operator is actually looking at.

Bounded by *bytes*, not by frames, because a frame is not a unit: a 20 MP
Bayer frame is 40 MB and a 2 MP webcam's is 6 MB, and a queue that holds
"twelve frames" means two entirely different things on the two cameras
this app already supports. Bytes are the thing that runs out.

Full means refuse, never drop. A live frame lost is invisible and a
capture lost is gone (ARCHITECTURE.md 3.1), so backpressure travels
backwards to the shutter: `submit` returns False, the capture does not
start, and the trigger fires at the next pause instead. The failure mode
is the app being as slow as it used to be, which is a bad afternoon
rather than a lost photograph.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

_log = logging.getLogger(__name__)

MB = 1024 * 1024


def _available_bytes() -> int | None:
    """What the machine says it can spare, or None if it will not say."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def default_budget() -> int:
    """Bytes of un-written capture the queue may hold.

    An eighth of what the machine says is available, clamped. The share is
    small on purpose: this is a photography application, the other seven
    eighths are for the operating system's page cache doing the actual
    writing, and a queue that grows until it swaps has turned a slow
    afternoon into a stopped one.

    The floor of 256 MB is six 20 MP frames, enough that a burst of
    stack slices does not stall on a modest machine. The ceiling of 2 GB
    is about fifty, which is more slices than anybody racks in one go --
    past that the queue is hiding a disk that cannot keep up, and hiding
    it is not a kindness.
    """
    spare = _available_bytes()
    if spare is None:                    # not Linux, or /proc unreadable
        return 512 * 1024 * 1024
    # The floor is itself a share on a small machine. A flat 256 MB is
    # a sixteenth of a roomy laptop and a quarter of a cramped one, and on
    # the cramped one that is the difference between a queue and a swap
    # storm. Nobody should need 64 GB to take a photograph.
    floor = min(256 * MB, spare // 4)
    return max(floor, min(spare // 8, 2048 * MB))


class WriteQueue:
    """Deferred post-shutter work, in order, within a memory budget."""

    #: Never more than this many outstanding regardless of size. A guard
    #: against a small-sensor camera queueing hundreds of tiny frames and
    #: turning a crash into hundreds of lost captures rather than a few.
    MAX_PENDING = 24

    #: The watermark. Past this share of the budget the queue is no longer
    #: absorbing the delay, it is only postponing it, and the operator is
    #: about to be refused a capture. They should hear about it from the
    #: status strip before they hear about it from a shutter that does
    #: nothing -- a delay is fine, an unexplained one is not. Under it,
    #: the queue is doing its job and has nothing to say.
    PRESSURE = 0.75

    def __init__(self, budget: int | None = None,
                 name: str = "capture-writer") -> None:
        self.budget = int(budget if budget is not None else default_budget())
        self._name = name
        self._queue: deque[tuple[int, Callable[[], None], str]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        #: Bytes accepted and not yet finished. Includes the job currently
        #: running, because its memory is still held.
        self._bytes = 0
        self._running = 0
        self._worker: threading.Thread | None = None
        self._stopping = False
        #: Highest depth reached, for saying afterwards whether the queue
        #: was ever the thing in the way.
        self.high_water = 0

    # ---- the producer side -----------------------------------------------

    def submit(self, nbytes: int, job: Callable[[], None],
               label: str = "") -> bool:
        """Take ownership of some finished work. False when there is no room.

        `nbytes` is what the job holds alive, not what it will write: the
        point of the budget is resident memory, and a 30 MB DNG comes from
        a 40 MB array that stays referenced until the job is done.
        """
        nbytes = max(0, int(nbytes))
        with self._wake:
            if self._stopping:
                return False
            if self._queue and (self._bytes + nbytes > self.budget
                                or len(self._queue) + self._running
                                >= self.MAX_PENDING):
                # `self._queue and` deliberately: a single job larger than
                # the entire budget must still run, or a camera with big
                # enough frames could never capture at all. The bound is
                # about how many pile up, not about permission to work.
                return False
            self._bytes += nbytes
            self._queue.append((nbytes, job, label))
            self.high_water = max(self.high_water, self.depth)
            self._ensure_worker()
            self._wake.notify()
            return True

    # ---- what the rest of the app asks it ---------------------------------

    @property
    def depth(self) -> int:
        """Jobs accepted and not yet finished, including the running one."""
        return len(self._queue) + self._running

    @property
    def pending_bytes(self) -> int:
        return self._bytes

    @property
    def idle(self) -> bool:
        return self.depth == 0

    @property
    def under_pressure(self) -> bool:
        """Past the watermark: still working, but about to start refusing.

        The status strip watches this. A delay the operator was told about
        is a machine keeping up as best it can; the same delay unannounced
        is a shutter that has started ignoring them.
        """
        if self._bytes >= self.budget * self.PRESSURE:
            return True
        return self.depth >= self.MAX_PENDING * self.PRESSURE

    @property
    def fullness(self) -> float:
        """How full, 0 to 1, by whichever bound is closer to biting."""
        by_bytes = self._bytes / self.budget if self.budget else 0.0
        by_count = self.depth / self.MAX_PENDING
        return min(1.0, max(by_bytes, by_count))

    def room_for(self, nbytes: int) -> bool:
        """Would `submit` accept this? Asked before starting an exposure.

        Racy by nature -- the worker may finish between the question and
        the answer -- and harmlessly so: the only consequence of a stale
        yes is that `submit` refuses and the capture is not taken.
        """
        with self._lock:
            if not self._queue:
                return True
            return (self._bytes + max(0, int(nbytes)) <= self.budget
                    and self.depth < self.MAX_PENDING)

    def drain(self, timeout: float = 30.0) -> bool:
        """Wait for everything accepted to reach disk. True if it all did.

        Called before the window closes. The queue holds photographs that
        exist nowhere else, so this is the difference between quitting and
        losing them.
        """
        deadline = time.monotonic() + timeout
        with self._wake:
            while self.depth and time.monotonic() < deadline:
                self._wake.wait(0.1)
            return self.depth == 0

    def stop(self, timeout: float = 30.0) -> bool:
        """Drain, then retire the worker. Rejects anything new."""
        with self._wake:
            self._stopping = True
            self._wake.notify_all()
        done = self.drain(timeout)
        worker = self._worker
        if worker is not None:
            worker.join(timeout=1.0)
        return done

    # ---- the worker -------------------------------------------------------

    def _ensure_worker(self) -> None:
        """Started on first use, not in __init__.

        A thread per constructed object is a thread per test, and this
        object is constructed in a good many of them. Nothing to join if
        nothing was ever submitted.
        """
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._serve, daemon=True,
                                        name=self._name)
        self._worker.start()

    def _serve(self) -> None:
        while True:
            with self._wake:
                while not self._queue:
                    if self._stopping:
                        return
                    # Timed, not indefinite: `stop` may be called while
                    # this is parked, and a daemon thread that never wakes
                    # keeps a queue object alive for the process's life.
                    self._wake.wait(0.25)
                nbytes, job, label = self._queue.popleft()
                self._running += 1
            try:
                job()
            except Exception:
                # The queue outlives any single capture. One frame that
                # cannot be written must not take the next twenty with
                # it, and the job itself is responsible for telling the
                # operator -- it is the half that knows what failed.
                _log.exception("write job failed%s",
                               f" ({label})" if label else "")
            finally:
                with self._wake:
                    self._running -= 1
                    self._bytes = max(0, self._bytes - nbytes)
                    self._wake.notify_all()
