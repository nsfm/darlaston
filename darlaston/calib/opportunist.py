"""Collecting flat fields without asking for them.

Most of a session is spent crossing empty slide looking for something worth
photographing. Those moments are free flat fields, if they can be taken without
interrupting anything.

The awkward constraint is that **a flat has to be raw**. Blank detection runs on
the 8-bit preview, but a flat built from preview frames cannot correct raw data
-- so noticing the moment is cheap and taking the frame is not: a full pull
stops the stream, reconfigures, moves forty megabytes and restarts, over a
second in all.

So the rule is conservative. A frame is only taken when all of these hold:

  * the preview looks like empty slide
  * the view has been still for a moment -- nobody is mid-move
  * the stage is far enough from every patch already banked, because four
    frames of the same patch do not median away that patch's debris
  * enough time has passed since the last one, so a long pause on an empty
    field does not fire repeatedly

Even then the pause is visible, and it can be switched off. A tool that freezes
unpredictably is worse than one that asks.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np

from .service import FlatBank


class Opportunist:
    """Watches the live signals and banks raw blank fields when it is safe to."""

    #: Never twice within this many seconds, however still and blank it looks.
    MIN_INTERVAL = 4.0

    def __init__(self, session, on_banked: Callable[[int, int], None] | None = None,
                 on_busy: Callable[[bool], None] | None = None,
                 wanted: int = 4) -> None:
        self._session = session
        self._on_banked = on_banked or (lambda _n, _w: None)
        self._on_busy = on_busy or (lambda _b: None)
        self.bank = FlatBank(wanted=wanted)
        self.enabled = True
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
                self._on_banked(self.bank.count, self.bank.wanted)
        except Exception:
            pass          # a failed opportunistic grab must never surface
        finally:
            self._on_busy(False)
            self._grabbing.release()

    @property
    def frames(self) -> list[np.ndarray]:
        return self.bank.frames
