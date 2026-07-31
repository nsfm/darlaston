"""How many threads OpenCV may use, and why it is fewer than it wants.

Left alone, OpenCV takes every core for every operation. For this program
that is close to pure waste, and it is what made the fans audible.

The live loop is the obvious case. It is latency work -- a deadline forty
times a second, shared with the camera thread and the UI -- and its
operations are small and memory-bound, so spreading each one over sixteen
threads costs more in dispatch than it saves in arithmetic. Measured on
the real camera, CPU as a percentage of one core against the analysed
frame cost, on a 25 ms budget:

    16 threads   299%   15.4 ms      <- what OpenCV picks unaided
     6 threads   209%   16.1 ms
     4 threads   191%   16.0 ms      <- here
     3 threads   175%   19.7 ms
     2 threads   164%   24.0 ms      <- no headroom left

Below 4 it starts costing real frame time in earnest, and 2 leaves
nothing for a larger preview resolution or a slower machine. Nothing was
dropped at any setting.

Four is not quite free. Across five capped-against-uncapped pairs taken
back to back, the honest figures are 299% and 14.9 ms against 191% and
16.4 ms: a third of the machine given back for 1.5 ms, about 6% of the
frame budget, with 34% of it still spare. Worth taking, because the
budget was not the thing that was short.

The batch jobs were the surprise. Stitching and merging are throughput
work with somebody waiting, so they were given every core through a
context manager -- and measuring it refuted the whole idea. Both are
bound by reading a few hundred megabytes of DNG and by the parts that
were never parallel, not by arithmetic. A fourteen-second merge of
fifteen slices, three runs each:

    16 threads   14.1-14.3 s wall, 22.4-22.6 s CPU
     4 threads   14.5-14.7 s wall, 17.2-17.3 s CPU

Sixteen threads buy 0.4 s of a fourteen-second job and spend five extra
CPU-seconds doing it. A stitch of fifteen tiles behaves the same way,
6.7 s against 7.0 s. So the context manager and its six call sites came
back out, and one number covers the whole process.

The cap only bites on large machines, which are exactly the ones with
cores to waste: on a four-core laptop it is what OpenCV would have
chosen anyway.
"""
from __future__ import annotations

import os

import cv2


def usable_cores() -> int:
    """How many cores this process may actually run on.

    `cpu_count` reports the machine; affinity reports our share of it,
    which is what a container or a `taskset` leaves us and what the live
    loop has to live within. Linux only, so fall back to the machine.
    """
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


#: Threads OpenCV may use, anywhere in the process. Four is the knee of the
#: curve above, and measured no worse than sixteen for the batch work too.
THREAD_BUDGET = min(4, usable_cores())


def apply_thread_budget() -> None:
    """Set the budget. Call once, at startup, before anything opens a camera."""
    cv2.setNumThreads(THREAD_BUDGET)
