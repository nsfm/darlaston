"""LatestFrame -- the single most important object in the application.

The live path is a *cell*, not a queue.

A queue means that when analysis falls behind, lag accumulates: the buffer
drains while the operator looks at a frame from several hundred milliseconds
ago, after the stage has already moved. That is the specific thing that makes
microscope software unpleasant to drive.

So: one slot, replace-on-write. Producers never block. A frame arriving while
the consumer is busy replaces the pending one and the old frame is released
back to its pool. Frames are dropped under load; the view is always of now.

See ARCHITECTURE.md 3.2.
"""
from __future__ import annotations

import threading
from typing import Generic, Protocol, TypeVar


class Releasable(Protocol):
    """Anything the cell may have to drop on the floor must know how to die."""

    def release(self) -> None: ...


T = TypeVar("T", bound=Releasable)


class LatestFrame(Generic[T]):
    """Single-slot handoff between one producer and one consumer."""

    __slots__ = ("_item", "_cond", "_closed", "_dropped", "_delivered")

    def __init__(self) -> None:
        self._item: T | None = None
        self._cond = threading.Condition(threading.Lock())
        self._closed = False
        self._dropped = 0
        self._delivered = 0

    def put(self, item: T) -> None:
        """Publish a frame. Never blocks. Replaces and releases any pending one."""
        with self._cond:
            if self._closed:
                item.release()
                return
            stale, self._item = self._item, item
            self._cond.notify()
        # Release outside the lock: a pool return must never be able to
        # deadlock the producer against a consumer holding the same lock.
        if stale is not None:
            self._dropped += 1
            stale.release()

    def take(self, timeout: float | None = None) -> T | None:
        """Wait for the next frame. Returns None on timeout or close.

        Ownership transfers to the caller, which becomes responsible for
        releasing it.
        """
        with self._cond:
            if self._item is None and not self._closed:
                self._cond.wait(timeout)
            item, self._item = self._item, None
            if item is not None:
                self._delivered += 1
            return item

    def close(self) -> None:
        """Wake every waiter and drop anything pending."""
        with self._cond:
            self._closed = True
            stale, self._item = self._item, None
            self._cond.notify_all()
        if stale is not None:
            stale.release()

    @property
    def closed(self) -> bool:
        """Whether `close` has been called.

        Worth asking, because a closed cell answers `take` immediately
        rather than waiting out the timeout -- so a consumer looping on
        one spins at full speed instead of idling.
        """
        return self._closed

    @property
    def stats(self) -> tuple[int, int]:
        """(delivered, dropped). Dropped frames are health, not failure --
        a live path that never drops is a live path that is about to lag."""
        return self._delivered, self._dropped


V = TypeVar("V")


class Newest(Generic[V]):
    """The same discipline, for a consumer that is woken rather than waiting.

    `LatestFrame` suits a consumer that blocks on `take`. The other end of
    the pipeline is a Qt slot, which does not block -- it is woken by a
    queued signal, and *that queue is unbounded*. Every emitted value waits
    its turn, so a stall on the interface thread does not cost frames, it
    costs latency: measured against a one-second stall at 34 fps, the worst
    delivery lag was 979 ms and each of the ~34 waiting frames held its own
    6.65 MB preview. The operator sees their hand a second ago, which is
    exactly what this module's docstring says must not happen.

    So: hold one value, replace it, and only ask to be woken when nobody
    has been asked already. The wake-up and the value travel separately --
    by the time the slot runs, the value it is handed may be several
    frames stale, and the one worth drawing is whatever is in here now.

    Measured the same way, this took the worst lag to 33 ms. It delivers
    fewer frames, and that is the point: the ones it drops were already
    too old to be worth drawing by the time anybody could see them.
    """

    __slots__ = ("_value", "_awake", "_lock", "_replaced")

    def __init__(self) -> None:
        self._value: V | None = None
        self._awake = False
        self._lock = threading.Lock()
        self._replaced = 0

    def put(self, value: V) -> bool:
        """Store `value`. True when the consumer still needs waking.

        False means a wake-up is already in flight and will collect this
        value when it lands -- so the caller must not send another.
        """
        with self._lock:
            if self._value is not None:
                self._replaced += 1
            self._value = value
            if self._awake:
                return False
            self._awake = True
            return True

    def take(self) -> V | None:
        """The newest value, and re-arm. None if there is nothing."""
        with self._lock:
            value, self._value = self._value, None
            self._awake = False
            return value

    @property
    def replaced(self) -> int:
        """Values superseded before anybody looked at them.

        Health, not failure, exactly as `LatestFrame.stats` is: a live path
        that never replaces is one that is about to lag.
        """
        return self._replaced
