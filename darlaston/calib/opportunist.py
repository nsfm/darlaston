"""Collecting flat fields, while the operator is deliberately collecting them.

Crossing empty slide looking for a subject *looks* like a free flat field,
and this used to take one whenever it saw one. It is not free, and it was
not sound:

  * the frame is not free. A flat has to be raw, so taking one stops the
    stream, reconfigures, moves forty megabytes and restarts -- over a
    second of frozen preview, unasked.
  * a flat is only valid at the illumination it was shot under, and
    nothing here can read the lamp or the condenser. Grabbing across a
    session in which those are adjusted constantly builds a flat out of
    frames that do not agree with each other or with the captures it
    corrects.
  * blankness is a guess, and a wrong guess is not a wasted frame -- it
    bakes a specimen into the flat, and its inverse then appears on every
    frame that flat ever corrects, undetectably.

So it is off unless the operator has said they are collecting, from the
calibration panel, having put the light and the optics where they want
them. It still earns its place there: four frames at *different* stage
positions median away slide debris, and gathering those while the stage
moves naturally beats asking somebody to find four empty spots by hand.

While collecting, a frame is taken only when all of these hold:

  * the preview looks like empty slide, patch by patch rather than on
    average, so a few specimens on a lot of empty ground cannot pass
  * the view has been still for a moment -- nobody is mid-move
  * the stage is far enough from every patch already banked, because four
    frames of the same patch do not median away that patch's debris
  * enough time has passed since the last *attempt*, successful or not

and the pause is announced while it happens.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from .service import FlatBank


class Opportunist:
    """Banks raw blank fields, while the operator is collecting them."""

    #: Never twice within this many seconds, however still and blank it looks.
    MIN_INTERVAL = 4.0

    def __init__(self, session, on_banked: Callable[[int, int], None] | None = None,
                 on_busy: Callable[[bool], None] | None = None,
                 wanted: int = 4) -> None:
        self._session = session
        self._on_banked = on_banked or (lambda _n, _w: None)
        self._on_busy = on_busy or (lambda _b: None)
        self.bank = FlatBank(wanted=wanted)
        #: Off until asked. See the module docstring for why this is not a
        #: convenience default.
        self.enabled = False
        self._key: str | None = None
        self._last = 0.0
        self._grabbing = threading.Lock()

    # ---- configuration ---------------------------------------------------

    def set_key(self, key: str) -> None:
        """Blank fields belong to one optical configuration.

        Changing objective or illumination invalidates everything banked --
        a flat is only valid for the stack it was shot through.
        """
        if key != self._key:
            self._key = key
            self.bank.reset()
            self._on_banked(0, self.bank.wanted)

    @property
    def count(self) -> int:
        return self.bank.count

    # ---- the live path ---------------------------------------------------

    def observe(self, signals) -> None:
        """Called for every analysed frame. Must be cheap and must not block."""
        if signals.xy_offset is not None:
            self.bank.note_motion(*signals.xy_offset)
        if not self._should_grab(signals):
            return
        if not self._grabbing.acquire(blocking=False):
            return
        threading.Thread(target=self._grab, daemon=True,
                         name="bank-flat").start()

    def _should_grab(self, signals) -> bool:
        return (self.enabled
                and signals.looks_blank
                and signals.settled
                and not self.bank.complete
                and time.time() - self._last > self.MIN_INTERVAL
                and self._session.backend is not None)

    def _grab(self) -> None:
        try:
            self._on_busy(True)
            backend = self._session.backend
            if backend is None:
                return
            # Charged against the interval whatever the bank decides. The
            # grab is what costs -- it stalls the preview for over a second
            # -- and that has already happened by the time the bank has an
            # opinion. Only marking successes meant a rejected frame left
            # the clock untouched, so the very next frame qualified again:
            # park on a field the bank has already seen and the preview
            # grabs back to back for ever, at well under one frame a
            # second, with nothing in the performance monitor to show for
            # it because the stall is in the camera and not in the loop.
            self._last = time.time()
            with backend.grab_raw() as frame:
                raw = frame.copy()
            # Position is checked inside the bank, so a frame taken on a patch
            # we already have is discarded rather than silently duplicating it.
            if self.bank.offer(raw):
                self._last = time.time()
                # Enough is enough: stop of its own accord rather than
                # leaving a mode running that nobody needs any more.
                if self.bank.complete:
                    self.enabled = False
                self._on_banked(self.bank.count, self.bank.wanted)
        except Exception:
            pass          # a failed opportunistic grab must never surface
        finally:
            self._on_busy(False)
            self._grabbing.release()

    @property
    def frames(self) -> list[np.ndarray]:
        return self.bank.frames
