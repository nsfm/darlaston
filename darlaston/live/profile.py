"""Where the frame time actually goes.

The live loop runs eight or nine distinct pieces of work per frame and
until now reported only the resulting frame rate, which tells you that
something is expensive without telling you what. That is the wrong shape
of information for the decision it feeds: whether a feature earns its
cost, and which ones should be optional on a machine with four cores
instead of sixteen.

So stages are named after *features* rather than code regions --
"peaking", "stage tracking", "turret" -- because the actionable answer is
always "turn that one off", never "optimise lines 340 to 380".

Always on rather than behind a flag. `perf_counter` costs tens of
nanoseconds and a stage costs milliseconds, so the measurement is four
orders of magnitude below the thing measured; and a profiler you have to
enable is one that is off when the interesting stall happens. It also
avoids the trap where enabling profiling changes what you are profiling.

Values are exponential moving averages. A single frame is noise -- one
GC pause or one scheduler hiccup -- and what matters is the sustained
cost, which is also what makes the fans spin.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

#: Weight of each new sample. 0.1 settles in roughly thirty frames, about
#: a second of video, which is fast enough to see a feature switch on and
#: slow enough not to jitter while you read it.
ALPHA = 0.1


class Meter:
    """Rolling per-stage timings, cheap enough to leave running."""

    def __init__(self) -> None:
        self._ms: dict[str, float] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._frames = 0

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - start) * 1e3)

    def since(self, name: str, start: float) -> float:
        """Record the time since `start` under `name`; return a new mark.

        Chaining these along a function is deliberate: it puts a
        one-line marker at each feature boundary instead of wrapping
        every block in a `with`, which would mean reindenting the loop
        this file exists to keep readable.
        """
        now = time.perf_counter()
        self.record(name, (now - start) * 1e3)
        return now

    def record(self, name: str, ms: float) -> None:
        with self._lock:
            if name not in self._ms:
                self._ms[name] = ms
                self._order.append(name)
            else:
                self._ms[name] += ALPHA * (ms - self._ms[name])

    def skip(self, name: str) -> None:
        """Note that a stage did not run this frame.

        Without this a feature switched off keeps showing its last cost
        for ever, which reads as "still expensive" and is a lie.
        """
        self.record(name, 0.0)

    def frame(self) -> None:
        with self._lock:
            self._frames += 1

    def snapshot(self) -> dict[str, float]:
        """Stage -> milliseconds, in the order stages first appeared."""
        with self._lock:
            return {name: self._ms[name] for name in self._order}

    def total_ms(self) -> float:
        with self._lock:
            return sum(self._ms.values())

    def reset(self) -> None:
        with self._lock:
            self._ms.clear()
            self._order.clear()
            self._frames = 0
