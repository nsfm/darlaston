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
  * and then, once it has been taken, **the raw frame itself looks blank**

That last one is the important one and it was missing. Everything above
it is decided from a quarter-size preview, which is the cheap thing to
look at and not the thing that goes into the flat. Asking the
full-resolution frame is one resize of an array already in hand, and it
is the only check that sees what is actually about to be banked.

The pause is announced while it happens.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

from ..i18n import N_
from .service import FlatBank

_log = logging.getLogger(__name__)


class Opportunist:
    """Banks raw blank fields, one per press."""

    def __init__(self, session, on_banked: Callable[[int, int], None] | None = None,
                 on_busy: Callable[[bool], None] | None = None,
                 on_warn: Callable[[str], None] | None = None,
                 wanted: int = 4) -> None:
        self._session = session
        self._on_banked = on_banked or (lambda _n, _w: None)
        self._on_busy = on_busy or (lambda _b: None)
        #: Said in the panel when a banked frame is worth a second look.
        #: Never a refusal -- see `_grab`.
        self._on_warn = on_warn or (lambda _k: None)
        self.bank = FlatBank(wanted=wanted)
        self._key: str | None = None
        self._grabbing = threading.Lock()
        #: Built on first use, on the grab thread. The live pipeline has
        #: its own; this one reads the raw frame rather than the preview.
        self._blank = None

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

    # ---- banking, on request ---------------------------------------------

    def observe(self, signals) -> None:
        """Follow the stage, so the panel can say whether the view moved.

        Advisory only now. This used to also decide *when* to grab, which
        could not work: see `FlatBank.looks_like_the_same_patch`.
        """
        if signals.xy_offset is not None:
            self.bank.note_motion(*signals.xy_offset)

    def bank_now(self) -> bool:
        """Take one blank field, because the operator said so.

        Returns False if there is nothing to grab with or the bank is
        already full; the grab itself runs on its own thread, because it
        stops the stream, reconfigures, moves forty megabytes and restarts
        -- over a second, and none of it belongs on the interface thread.
        """
        if self.bank.complete or self._session.backend is None:
            return False
        if not self._grabbing.acquire(blocking=False):
            return False              # one already in flight
        threading.Thread(target=self._grab, daemon=True,
                         name="bank-flat").start()
        return True

    def _grab(self) -> None:
        try:
            self._on_busy(True)
            backend = self._session.backend
            if backend is None:
                return
            with backend.grab_raw() as frame:
                raw = frame.copy()
            # The frame is banked whatever we think of it. The operator
            # looked through the eyepiece and pressed a button; refusing
            # them on the strength of a threshold measured against
            # synthetic fields would be this module deciding it knows
            # better than the person at the microscope, and it does not.
            #
            # It is still worth saying when the frame does not look blank,
            # because a specimen baked into a flat stamps its inverse on
            # every frame that flat ever corrects, and that is worth
            # noticing early rather than in the photographs.
            same = self.bank.looks_like_the_same_patch()
            if self.bank.offer(raw):
                self._on_banked(self.bank.count, self.bank.wanted)
                if not self._blank_enough(raw):
                    _log.warning(
                        "banked a flat field that does not look blank "
                        "(%d of %d)", self.bank.count, self.bank.wanted)
                    self._on_warn(N_("calib.flat.warn.not_blank"))
                elif same:
                    _log.info("banked a flat field the stage had not left")
                    self._on_warn(N_("calib.flat.warn.same_patch"))
        except Exception:
            # Never surfaced: this is a background convenience and an
            # error box for it would be an interruption nobody asked for.
            # It is still a real failure -- a camera that refuses every
            # grab means the bank never fills and nothing says why.
            _log.exception("an opportunistic flat grab failed")
        finally:
            self._on_busy(False)
            self._grabbing.release()

    def _blank_enough(self, raw: np.ndarray) -> bool:
        """Does the grabbed frame itself look like empty slide?

        Refuses on anything it cannot judge. A frame we are unsure about
        is one skipped opportunity to bank a flat; a specimen baked into
        the flat is on every photograph the rest of the session takes.
        """
        try:
            from ..live.blank import BlankDetector, as_field

            info = getattr(self._session.backend, "info", None)
            depth = getattr(info, "max_bit_depth", 0) or 8
            if self._blank is None:
                self._blank = BlankDetector()
            return self._blank.looks_blank(as_field(raw),
                                           white=(1 << depth) - 1)
        except Exception:
            return False

    @property
    def frames(self) -> list[np.ndarray]:
        return self.bank.frames
