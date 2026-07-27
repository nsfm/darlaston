"""Pre-allocated frame buffers.

A full-resolution frame is 39.7 MB. Allocating one per frame makes the
allocator the frame-rate ceiling, so buffers come from a fixed ring and are
handed out as numpy views.

The rule that keeps this honest: **nothing may hold a Frame beyond its stage.**
Anything that needs to keep pixels takes an explicit copy and says so.

See ARCHITECTURE.md 3.3.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Frame:
    """A borrowed view of a pooled buffer, plus what was true when it was taken."""

    data: np.ndarray
    seq: int
    timestamp: float
    exposure_us: int
    gain_pct: int
    binned: bool
    _pool: "BufferPool | None" = field(default=None, repr=False)
    _released: bool = field(default=False, repr=False)

    def release(self) -> None:
        """Return the buffer. Idempotent, because double-release is a bug that
        should not also be a crash."""
        if self._released:
            return
        self._released = True
        if self._pool is not None:
            self._pool._give_back(self.data)

    def copy(self) -> np.ndarray:
        """Take ownership of the pixels. The explicit way to outlive the pool."""
        return self.data.copy()

    def __enter__(self) -> "Frame":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class BufferPool:
    """Fixed ring of identically shaped buffers.

    Exhaustion is not an error: it means every buffer is in flight, which on
    the live path means the consumer is behind. The producer is told so and
    drops the frame rather than allocating its way into a stall.
    """

    def __init__(self, shape: tuple[int, ...], dtype, count: int = 4) -> None:
        self.shape, self.dtype, self.count = shape, dtype, count
        self._free = [np.empty(shape, dtype) for _ in range(count)]
        self._lock = threading.Lock()
        self._exhausted = 0

    def acquire(self) -> np.ndarray | None:
        with self._lock:
            return self._free.pop() if self._free else self._note_exhausted()

    def _note_exhausted(self) -> None:
        self._exhausted += 1
        return None

    def _give_back(self, buf: np.ndarray) -> None:
        with self._lock:
            if len(self._free) < self.count:
                self._free.append(buf)

    @property
    def available(self) -> int:
        with self._lock:
            return len(self._free)

    @property
    def exhausted_count(self) -> int:
        return self._exhausted

    @property
    def nbytes(self) -> int:
        return int(np.prod(self.shape)) * np.dtype(self.dtype).itemsize * self.count
