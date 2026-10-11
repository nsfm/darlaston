"""Collecting a stack by driving the focus motor, where there is one.

The rack-then-pause trigger (`StackTrigger`) watches a hand on the fine
focus and fires at each pause. This is the other way round: the program
has the knob. Given two ends and a count, it moves the motor to each
plane in turn, waits for the picture to stop changing, fires the same
capture the trigger would have, waits for the slice to land, and moves
on. When it reaches the far end it says so, and the stack is finished
the way a hand-racked one is.

It is fed from the same place as the trigger -- one call per live frame
with the live signals -- and fires through the same function, so a
motorised stack goes through exactly the capture path a racked one does:
same session, same files, same assembly preview, same stacked-mosaic
tiles. The two never run at once; the window arms one or the other.

Settling is judged by the picture, not by a timer. A UVC camera's ISP
pipeline is a few frames deep and a motor takes however long it takes,
so the honest signal that a plane has arrived is the sharpness field
holding still frame to frame -- the same field-distance the trigger
uses, with the same self-calibrating floor idea reduced to its simplest
form: quiet for a few frames in a row, after a minimum number of frames
have gone by since the move.
"""
from __future__ import annotations

import time
from enum import Enum, auto
from typing import Callable

import cv2
import numpy as np

from ..camera.base import FocusAxis
from .stack import field_distance


class Phase(Enum):
    IDLE = auto()
    MOVING = auto()         #: a position has been asked for; watching for stillness
    EXPOSING = auto()       #: fired; waiting for the slice to land
    DONE = auto()


def plan(near: int, far: int, count: int, step: int = 1) -> list[int]:
    """The positions to visit, near to far, inclusive of both ends.

    Rounded to the motor's step and de-duplicated, so asking for more
    slices than the span has steps gives every step once rather than
    the same plane twice. One slice is the near end alone; the order is
    always near to far so a stack reads the same way every time.
    """
    count = max(1, int(count))
    if count == 1 or near == far:
        return [int(near)]
    raw = np.linspace(near, far, count)
    step = max(1, int(step))
    positions = [int(round(p / step)) * step for p in raw]
    out: list[int] = []
    for p in positions:
        if not out or p != out[-1]:
            out.append(p)
    return out


class FocusSweep:
    """Steps a focus motor through a plan, firing a slice at each stop."""

    #: Small square the sharpness field is reduced to for comparison.
    SIZE = 64
    #: Frames that must pass after a move before stillness is even
    #: considered. The ISP pipeline is a few frames deep -- a control
    #: change lands on the third frame, measured -- and the first frames
    #: after a move still show the old plane.
    MIN_FRAMES = 4
    #: Consecutive quiet frames that mean the plane has arrived.
    QUIET_FRAMES = 3
    #: "Quiet" is frame-to-frame distance below this fraction of the
    #: loudest frame seen since the move. Relative, like the trigger's,
    #: because the absolute scale depends on the subject.
    QUIET_BELOW_PEAK = 0.25
    #: And below this absolute floor regardless, so a motor that has
    #: not moved at all (asked for the position it was already at)
    #: does not wait for a peak that never comes.
    QUIET_FLOOR = 2e-3
    #: Give up waiting for stillness after this long and fire anyway.
    #: A camera whose picture never settles (noise, a flickering lamp)
    #: should still produce a stack, just a worse one.
    SETTLE_TIMEOUT = 3.0

    def __init__(self, axis: Callable[[], FocusAxis | None],
                 fire: Callable[[], bool]) -> None:
        self._axis = axis
        self._fire = fire
        self.phase = Phase.IDLE
        self.positions: list[int] = []
        self.at = 0                      #: index of the plane being worked
        self._prev: np.ndarray | None = None
        self._since_move = 0
        self._quiet = 0
        self._peak = 0.0
        self._moved_at = 0.0
        self._origin: int | None = None
        #: Set when the sweep ends, by whichever means. The window reads
        #: it once and finishes the stack.
        self.finished = False
        self.failed = ""

    # ---- control -----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.phase in (Phase.MOVING, Phase.EXPOSING)

    @property
    def total(self) -> int:
        return len(self.positions)

    @property
    def done(self) -> int:
        """Slices landed so far."""
        return self.at if self.phase is not Phase.DONE else self.total

    def start(self, positions: list[int]) -> bool:
        """Begin. False when there is no motor or nothing to do."""
        axis = self._axis()
        if axis is None or not positions:
            return False
        self.positions = list(positions)
        self.at = 0
        self.finished = False
        self.failed = ""
        self._origin = axis.get()
        # Two hands on one knob is the failure this prevents: the
        # camera's own autofocus must be off for the whole sweep.
        axis.set_auto(False)
        self._move_to(self.positions[0])
        return True

    def abort(self, restore: bool = True) -> None:
        """Stop where we are. The stack keeps whatever landed."""
        if self.phase in (Phase.MOVING, Phase.EXPOSING):
            self.phase = Phase.IDLE
            if restore:
                self._go_home()
        self.finished = False

    def _go_home(self) -> None:
        axis = self._axis()
        if axis is not None and self._origin is not None:
            axis.set(self._origin)

    def _move_to(self, position: int) -> None:
        axis = self._axis()
        if axis is None:
            self.phase = Phase.IDLE
            self.failed = "the focus motor went away"
            self.finished = True
            return
        axis.set(position)
        self.phase = Phase.MOVING
        self._since_move = 0
        self._quiet = 0
        self._peak = 0.0
        self._prev = None
        self._moved_at = time.monotonic()

    # ---- the capture path reporting back -------------------------------------

    def slice_landed(self) -> None:
        if self.phase is not Phase.EXPOSING:
            return
        self.at += 1
        if self.at >= len(self.positions):
            self.phase = Phase.DONE
            self.finished = True
            self._go_home()
            return
        self._move_to(self.positions[self.at])

    def capture_failed(self) -> None:
        """The slice did not land. Stop rather than guess; the stack
        keeps what it has and the window says why."""
        if self.phase is Phase.EXPOSING:
            self.phase = Phase.IDLE
            self.failed = "a capture failed"
            self.finished = True
            self._go_home()

    # ---- one frame ---------------------------------------------------------------

    def observe(self, signals) -> bool:
        """One live frame. Returns True when a capture was fired."""
        if self.phase is not Phase.MOVING:
            return False
        self._since_move += 1
        field = getattr(signals, "sharpness_field", None)
        if field is not None:
            small = cv2.resize(field, (self.SIZE, self.SIZE),
                               interpolation=cv2.INTER_AREA)
            prev, self._prev = self._prev, small
            if prev is not None:
                d = field_distance(prev, small)
                self._peak = max(self._peak, d)
                if d < self.QUIET_FLOOR or d < self._peak * self.QUIET_BELOW_PEAK:
                    self._quiet += 1
                else:
                    self._quiet = 0
        still = (self._since_move >= self.MIN_FRAMES
                 and self._quiet >= self.QUIET_FRAMES)
        overdue = time.monotonic() - self._moved_at > self.SETTLE_TIMEOUT
        if not (still or overdue):
            return False
        if self._fire():
            self.phase = Phase.EXPOSING
            return True
        # Refused -- the capture path is busy. Ask again next frame.
        return False
