"""LatestFrame — the single most important object in the application.

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
    def stats(self) -> tuple[int, int]:
        """(delivered, dropped). Dropped frames are health, not failure --
        a live path that never drops is a live path that is about to lag."""
        return self._delivered, self._dropped
