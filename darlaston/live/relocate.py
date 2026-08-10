"""The slide map, used as a place-recognition database.

Dead reckoning integrates frame offsets and accumulates error; blank
glass is a gap in the encoder and loses the position outright. But the
map already banks a thumbnail of every field the operator has seen, at
the position it was seen -- and matching the current view against that
bank answers "where am I" absolutely, anywhere on trodden ground.

Spiked before building (spike/tracking/): on a unique synthetic scene
the bank re-fixes a lost position in 100% of trials with ~3 preview px
of error, through 5 um of defocus, at 0.15 ms per banked thumbnail.
Run continuously over trodden ground, the same match abolishes
accumulated drift: a 2% biased re-traversal went from 63 px mean error
uncorrected to 2 px corrected.

One primitive, two schedules:

**Tracking, near banked ground** -- every few frames, match against the
handful of snapshots overlapping the believed position and nudge the
tracker by the measured drift. Sub-millisecond, and it makes the map
rigid: revisits correct toward the first visit instead of banking an
offset copy of it.

**Lost** -- blank glass, or a jump too fast to measure -- sweep the
whole bank in distance-ranked chunks, a few dozen thumbs a frame, and
re-fix when two consecutive matches agree. The fix rejoins the *same*
origin, so the terrain and the pins survive the crossing.

Sign convention, settled by measurement and kept in writing because it
cost a spike round: with `phaseCorrelate(bank_thumb, probe_thumb)`, the
probe's position is the bank position *minus* the measured shift times
the thumb-to-preview scale.

Deliberately Qt-free: driven from the interface thread by whoever owns
the map and the pipeline, but testable headless with nothing but
arrays.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

#: Phase-correlation response below which a match is noise. The spike
#: measured true matches at 0.6 to 0.8 and the best wrong answer near
#: 0.2; this sits in the gap, nearer the noise so defocused true
#: matches survive.
RESPONSE_FLOOR = 0.35
#: Two consecutive fixes must agree within this many preview pixels
#: before a large correction is believed. Arrangements repeat similar
#: valves, so a single confident match is not proof.
AGREE_PX = 60.0
#: Corrections smaller than this are drift-scale and apply on one
#: match; larger ones are relocations and need the agreement above.
SMALL_PX = 50.0
#: While tracking, match every this-many frames. Drift accumulates
#: slowly; a correction cadence of a few hertz holds it near zero for
#: a fraction of a millisecond a frame.
CONTINUOUS_EVERY = 8
#: While lost, how many bank thumbs to try per frame. Chunked so the
#: interface thread never stalls; distance-ranked so the likely ground
#: is tried first.
SWEEP_CHUNK = 24
#: Largest believable correction while tracking, as a fraction of the
#: frame. Past this the window and the view barely overlap, and an
#: answer is a contradiction rather than a correction.
NEAR_FRAC = 0.55
#: A probe with less texture than this has nothing to match -- bare
#: glass correlates with everything and means nothing. Standard
#: deviation of the thumbnail, in grey levels.
TEXTURE_FLOOR = 4.0


@dataclass
class Fix:
    """One believed correction, in preview pixels."""

    #: Where the current view actually is.
    pos: tuple[float, float]
    #: pos minus where the tracker believed it was, or None when the
    #: tracker held no position at all (lost over blank glass).
    delta: tuple[float, float] | None
    #: The winning match's response, for whoever wants to log it.
    response: float


class Relocator:
    """Matches the live view against the map's own bank.

    Owns nothing but caches: the bank belongs to the map, the position
    to the tracker, and the schedule to `observe`, which is called once
    per delivered frame and decides for itself when to spend anything.
    """

    def __init__(self) -> None:
        self._since = 0
        self._window: tuple[tuple[int, int], np.ndarray] | None = None
        #: The sweep cursor while lost: ranked candidate order and how
        #: far through it this pass has read.
        self._sweep: list | None = None
        self._sweep_at = 0
        #: The previous large fix awaiting agreement.
        self._candidate: tuple[float, float] | None = None
        #: The tracker resumed from a held position that may be wrong by
        #: a whole blank crossing -- it re-keys on whatever ground it
        #: lands on and reports itself locked. Stay suspicious until the
        #: bank confirms or corrects, for a bounded number of sweeps:
        #: ground beyond the crossing may genuinely never have been seen,
        #: and suspicion of new ground must end rather than burn for ever.
        self._suspect_sweeps = 0
        self.searching = False

    # ---- the schedule ----------------------------------------------------

    def observe(self, preview: np.ndarray, pos, tracking: bool,
                terrain) -> Fix | None:
        """One frame's worth of work, or none. Returns a `Fix` to apply.

        `pos` is the tracker's current belief (which it holds even
        without a lock), `tracking` whether that belief is integrating.
        `terrain` is the map's canvas (`tracker.Terrain`).
        """
        h, w = preview.shape[:2]
        if not terrain.ready or terrain.size != (w, h):
            # Nothing painted yet, or painted at another preview scale:
            # a mode change rescales the world and clears the map.
            self.searching = False
            return None
        if tracking:
            if self._suspect_sweeps > 0:
                # Resumed, but from a position held across a gap: the
                # tracker is confident and may be wrong by the whole
                # crossing. Keep sweeping until the map agrees with the
                # belief, corrects it, or the budget decides this is
                # ground nobody has seen.
                fix = self._lost(preview, pos, terrain)
                if fix is not None:
                    self._settle()
                return fix
            self._sweep, self._sweep_at = None, 0
            self.searching = False
            self._since += 1
            if pos is None or self._since < CONTINUOUS_EVERY:
                return None
            self._since = 0
            # The agreement candidate deliberately survives between
            # these calls: a large claim's two witnesses are a whole
            # cadence apart, and clearing it here made agreement
            # impossible -- caught by its test.
            return self._continuous(preview, pos, terrain)
        self._since = 0
        self._suspect_sweeps = self.SUSPECT_SWEEPS
        return self._lost(preview, pos, terrain)

    #: Full passes over the bank a resumed-but-suspect position is
    #: allowed before it is believed: familiar ground answers in the
    #: first pass, and ground past the crossing may be genuinely new.
    SUSPECT_SWEEPS = 3

    def _settle(self) -> None:
        self._suspect_sweeps = 0
        self._sweep, self._sweep_at = None, 0
        self._candidate = None
        self.searching = False

    # ---- tracking: hold the map rigid ------------------------------------

    def _continuous(self, preview, pos, terrain) -> Fix | None:
        w = preview.shape[1]
        probe = self._thumb(preview)
        if probe is None:
            return None
        best = self._locate(probe, terrain, pos)
        if best is None:
            return None
        est, response = best
        delta = (est[0] - pos[0], est[1] - pos[1])
        span = max(abs(delta[0]), abs(delta[1]))
        if span > NEAR_FRAC * w:
            return None                # not a correction, a contradiction
        if span > SMALL_PX and not self._agreed(est):
            return None
        self._candidate = None
        return Fix(pos=est, delta=delta, response=response)

    # ---- lost: find familiar ground --------------------------------------

    def _lost(self, preview, pos, terrain) -> Fix | None:
        self.searching = True
        probe = self._thumb(preview)
        if probe is None:
            # Bare glass in view: nothing to match, and trying would
            # only prove that blank correlates with everything.
            self._sweep, self._sweep_at = None, 0
            return None
        if self._sweep is None:
            anchor = pos or (0.0, 0.0)
            self._sweep = sorted(
                terrain.candidates(),
                key=lambda c: (c[0] - anchor[0]) ** 2
                + (c[1] - anchor[1]) ** 2)
            self._sweep_at = 0
        chunk = self._sweep[self._sweep_at:self._sweep_at + SWEEP_CHUNK]
        self._sweep_at += len(chunk)
        if self._sweep_at >= len(self._sweep):
            self._sweep, self._sweep_at = None, 0    # next frame restarts
            # One full pass spent. Only the resumed-but-suspect state
            # budgets these; genuinely lost keeps sweeping for ever,
            # because there is nothing else worth spending frames on.
            if self._suspect_sweeps > 0:
                self._suspect_sweeps -= 1
        if not chunk:
            return None
        best = None
        for cand in chunk:
            found = self._locate(probe, terrain, cand)
            if found is not None and (best is None or found[1] > best[1]):
                best = found
        if best is None:
            return None
        est, response = best
        # A relocation is always a large claim, so it always needs two
        # consecutive matches in agreement -- similar valves in an
        # arrangement can produce one confident lie, but they do not
        # produce the same lie twice from two different frames.
        if not self._agreed(est):
            return None
        self._candidate = None
        self.searching = False
        delta = ((est[0] - pos[0], est[1] - pos[1])
                 if pos is not None else None)
        return Fix(pos=est, delta=delta, response=response)

    def _agreed(self, est: tuple[float, float]) -> bool:
        previous, self._candidate = self._candidate, est
        return (previous is not None
                and abs(est[0] - previous[0]) < AGREE_PX
                and abs(est[1] - previous[1]) < AGREE_PX)

    # ---- the primitive ---------------------------------------------------

    def _locate(self, probe, terrain, at) -> tuple | None:
        """The probe's position, measured against the terrain window at
        `at` -- computed from the window's *actual* centre, because the
        integer-canvas-pixel rounding remainder is eight preview pixels
        of error when computed against the request. Paid once, in the
        spike."""
        window, share, centre = terrain.window(at)
        if window is None or share < terrain.WINDOW_VALID:
            return None
        if window.shape != probe.shape:
            return None
        (dx, dy), response = cv2.phaseCorrelate(window, probe.copy(),
                                                self._hanning(probe.shape))
        if response < RESPONSE_FLOOR:
            return None
        scale = terrain.scale
        return (centre[0] - dx * scale, centre[1] - dy * scale), response

    def _thumb(self, preview: np.ndarray) -> np.ndarray | None:
        h, w = preview.shape[:2]
        tw = 120                       # Terrain.THUMB_W, and must stay so
        th = max(1, round(tw * h / w))
        small = cv2.resize(preview, (tw, th), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if float(gray.std()) < TEXTURE_FLOOR:
            return None
        return gray

    def _hanning(self, shape) -> np.ndarray:
        if self._window is None or self._window[0] != shape:
            self._window = (shape, cv2.createHanningWindow(
                shape[::-1], cv2.CV_32F))
        return self._window[1]
