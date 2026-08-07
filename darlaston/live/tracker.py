"""Stage position without a motorised stage: the image is the encoder.

The pipeline already phase-correlates consecutive preview frames for the
stillness test. Integrating those same offsets gives a running estimate of
where the field of view sits relative to wherever tracking started -- which is
everything slide navigation and, later, mosaic anchoring need. No hardware, no
calibration: units are preview pixels, and "fields" (frame-widths) turn out to
be the honest unit to show a person cranking a stage by hand.

Two honest limitations, both structural:

**Blank glass is a gap in the encoder.** Phase correlation needs structure to
correlate; a featureless field returns a low response and the position must
hold rather than integrate garbage. If the stage genuinely moves while the
view is featureless, that motion is lost and everything mapped afterwards is
offset by it. In practice slides carry debris and mounting texture almost
everywhere, so the gaps are short -- and the map view makes the failure visible
rather than silent, which is the most that can be done without an encoder.

**Drift accumulates.** Every frame-to-frame estimate carries subpixel error
and integration sums it. Fine for navigation ("about three fields north-east"),
not for stitching -- the mosaic path treats these positions as a *constraint*
for registration, never as the registration itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


class StageTracker:
    """Integrates per-frame offsets into a position, with the sense inverted:
    when the scene slides left, the view has moved right over the slide."""

    #: Below this phase-correlation response the match is noise, not motion.
    #: Blank fields and heavy defocus both land here. Chosen against the mock
    #: (blank ~0.01, tracked motion ~0.3+); may need tuning on hardware.
    CONFIDENCE = 0.10
    #: A single-frame shift beyond this fraction of the frame is rejected:
    #: phase correlation cannot measure past half a frame, and matches near
    #: that limit are where the wraparound lies live.
    #:
    #: Applied per axis. It used to be `MAX_STEP * min(shape)`, which on a
    #: 3:2 frame gave x 23% of its own width and y 35% of its height, for
    #: no physical reason -- the measurable range is a property of each
    #: axis separately.
    MAX_STEP = 0.35

    #: Re-anchor once the view has slid this far from the keyframe, as a
    #: fraction of the correlated image's shorter side. Far enough that the
    #: shift is measured well, near enough that the two frames still share
    #: most of their content.
    REKEY = 0.20

    def __init__(self) -> None:
        self._pos = (0.0, 0.0)
        self._anchor = (0.0, 0.0)
        self._tracking = False
        self._ever = False
        self._gated = 0

    def reset(self) -> None:
        self._pos = (0.0, 0.0)
        self._anchor = (0.0, 0.0)
        self._tracking = False
        self._ever = False
        self._gated = 0

    @property
    def position(self) -> tuple[float, float] | None:
        """View centre in preview pixels, origin where tracking began.
        None until the first successful lock, so a map is never seeded with
        a position that means nothing."""
        return self._pos if self._ever else None

    @property
    def tracking(self) -> bool:
        return self._tracking

    @property
    def gated(self) -> int:
        """Count of measured-but-rejected offsets. A consumer comparing
        positions across an interval must check this: a rejected match means
        the view changed by more than could be measured, which is not at all
        the same thing as it not having moved."""
        return self._gated

    def _acceptable(self, offset: tuple[float, float], confidence: float,
                    shape: tuple[int, ...]) -> bool:
        if confidence < self.CONFIDENCE:
            return False
        return (abs(offset[0]) <= self.MAX_STEP * shape[1]
                and abs(offset[1]) <= self.MAX_STEP * shape[0])

    def advance(self, offset: tuple[float, float] | None, confidence: float,
                shape: tuple[int, ...]) -> tuple[tuple[float, float] | None, bool]:
        """Fold in one frame's measured shift. Returns (position, locked).

        Kept for consumers that genuinely have only a frame-to-frame
        measurement. `anchor` is what the live pipeline uses, and is far
        more accurate for slow motion -- see its docstring.
        """
        if offset is None:
            self._tracking = False
            return self.position, False
        if not self._acceptable(offset, confidence, shape):
            self._tracking = False
            self._gated += 1
            return self.position, False
        self._pos = (self._pos[0] - offset[0], self._pos[1] - offset[1])
        self._anchor = self._pos
        self._tracking = True
        self._ever = True
        return self._pos, True

    def anchor(self, offset: tuple[float, float] | None, confidence: float,
               shape: tuple[int, ...]
               ) -> tuple[tuple[float, float] | None, bool, bool]:
        """Set the position from a shift measured against a *keyframe*.

        Returns (position, locked, wants_new_keyframe).

        Integrating consecutive frames is the obvious design and it loses
        half the travel of a slow hand. Phase correlation's sub-pixel
        estimator is biased toward whole pixels -- peak locking -- and the
        preview is reduced by four before correlating, so one pixel of
        stage motion is a quarter of a pixel here, deep in the biased
        region. Measured on Fourier-exact shifts through the shipped code
        path, integrating frame to frame over ninety frames:

            0.7 px/frame   -52% x   -53% y
            1.0 px/frame   -47% x   -49% y
            1.5 px/frame   -39% x   -40% y
            10  px/frame    -2% x    -3% y

        and the confidence stays above 0.93 throughout, so nothing is
        gated and nothing is logged while the position quietly stops
        keeping up. Measuring against a keyframe instead holds every one of
        those cases inside +-1%, because the shift being measured grows
        until it is large enough to measure well, and the answer *replaces*
        the position rather than adding to it. Error is then bounded by one
        keyframe interval instead of accumulating without limit.

        It is not free of bias, only bounded: a keyframe interval's worth
        of sub-pixel error still lands each time the anchor moves.
        """
        if offset is None:
            self._tracking = False
            return self.position, False, False
        if not self._acceptable(offset, confidence, shape):
            self._tracking = False
            self._gated += 1
            # A shift too big to trust is also a shift that has outrun its
            # keyframe, so ask for a fresh one rather than gating for ever.
            #
            # The anchor has to move with it. Asking for a new keyframe
            # while leaving the anchor where it was meant the next frame
            # computed `old_anchor - small_offset`: the position jumped
            # *backwards* by however far the view had travelled since the
            # last rekey, and reported itself locked while doing it. One
            # fast crank re-origined the map, and every pin and tile banked
            # afterwards was wrong by that much.
            #
            # Moving it to the last position we actually measured loses the
            # unmeasured jump instead, which is the limitation this module's
            # docstring already names and `gated` already counts. Lost is
            # recoverable; silently wrong is not.
            self._anchor = self._pos
            return self.position, False, True
        self._pos = (self._anchor[0] - offset[0], self._anchor[1] - offset[1])
        self._tracking = True
        self._ever = True
        rekey = max(abs(offset[0]), abs(offset[1])) > self.REKEY * min(shape[:2])
        if rekey:
            self._anchor = self._pos
        return self._pos, True, rekey


@dataclass
class Snapshot:
    """One remembered look at the slide: where, how big, and a thumbnail."""

    pos: tuple[float, float]        # world centre, preview pixels
    size: tuple[int, int]           # (w, h) of the view it was taken from
    thumb: np.ndarray               # small BGR copy
    #: Mosaic-tile bookkeeping (unused for reconnaissance snapshots):
    #: "ok", "merging" while a stacked tile's merge runs, "failed" if it
    #: did not; label is a small badge ("×13" for a 13-slice stack).
    state: str = "ok"
    label: str = ""


@dataclass
class Pin:
    """A point of interest the operator wants to find again."""

    id: int
    pos: tuple[float, float]


_COMPASS = ("E", "SE", "S", "SW", "W", "NW", "N", "NE")


class SlideMap:
    """The slide, as far as it has been seen.

    Built entirely from the preview stream -- no captures. Every frame the view
    passes over new ground, a thumbnail is banked at its tracked position; the
    map is simply everywhere the operator has already looked, which for slide
    hunting is exactly the map that matters.
    """

    #: Bank a new snapshot once the view has moved this far (in frame widths)
    #: from every existing one. Low enough that adjacent thumbs overlap and
    #: the map reads as continuous terrain rather than scattered postcards.
    SPACING = 0.35
    #: Within this of an existing snapshot, refresh it instead -- revisited
    #: ground shows what is there now, not what was there ten minutes ago.
    REFRESH = 0.15
    #: Frames between refreshes, so a parked view is not resizing thumbnails
    #: thirty times a second to overwrite itself with itself.
    REFRESH_EVERY = 24
    THUMB_W = 120
    #: Beyond this the map stops growing rather than evicting: dropping old
    #: snapshots would erase the very ground the operator wants to return to.
    #: 600 fields is far more slide than a session ever covers.
    LIMIT = 600

    def __init__(self) -> None:
        self.snapshots: list[Snapshot] = []
        self.pins: list[Pin] = []
        #: Captured mosaic tiles, drawn over the reconnaissance layer. Same
        #: world space; a tile is just a snapshot somebody paid 40 MB for.
        self.tiles: list[Snapshot] = []
        self.target: int | None = None      # pin id being navigated to
        self._next_pin = 1
        self._since_refresh = 0

    def reset(self) -> None:
        self.snapshots.clear()
        self.pins.clear()
        self.tiles.clear()
        self.target = None
        self._next_pin = 1
        self._since_refresh = 0

    # ---- mosaic tiles ----------------------------------------------------

    def add_tile(self, pos: tuple[float, float], preview: np.ndarray,
                 state: str = "ok", label: str = "") -> None:
        snap = self._snap(pos, preview)
        snap.state = state
        snap.label = label
        self.tiles.append(snap)

    def set_tile_state(self, index: int, state: str) -> None:
        """By mosaic tile index (1-based, adoption order)."""
        if 1 <= index <= len(self.tiles):
            self.tiles[index - 1].state = state

    def pop_tile(self) -> None:
        if self.tiles:
            self.tiles.pop()

    def clear_tiles(self) -> None:
        self.tiles.clear()

    # ---- building --------------------------------------------------------

    def observe(self, pos: tuple[float, float] | None, preview: np.ndarray,
                tracking: bool) -> bool:
        """Offer the current frame. Returns True if the map changed."""
        self._since_refresh += 1
        if pos is None or not tracking:
            return False
        h, w = preview.shape[:2]
        nearest, dist = self._nearest_snapshot(pos)
        if nearest is not None and dist < self.REFRESH * w:
            if self._since_refresh < self.REFRESH_EVERY:
                return False
            self._since_refresh = 0
            # Move to the end as well: freshly seen ground paints on top.
            self.snapshots.remove(nearest)
            self.snapshots.append(self._snap(pos, preview))
            return True
        if nearest is not None and dist < self.SPACING * w:
            return False
        if len(self.snapshots) >= self.LIMIT:
            return False
        self.snapshots.append(self._snap(pos, preview))
        return True

    def _snap(self, pos: tuple[float, float], preview: np.ndarray) -> Snapshot:
        h, w = preview.shape[:2]
        tw = self.THUMB_W
        th = max(1, round(tw * h / w))
        thumb = cv2.resize(preview, (tw, th), interpolation=cv2.INTER_AREA)
        return Snapshot(pos=(float(pos[0]), float(pos[1])), size=(w, h),
                        thumb=thumb)

    def _nearest_snapshot(self, pos) -> tuple[Snapshot | None, float]:
        best, best_d = None, math.inf
        for s in self.snapshots:
            d = math.hypot(s.pos[0] - pos[0], s.pos[1] - pos[1])
            if d < best_d:
                best, best_d = s, d
        return best, best_d

    # ---- pins ------------------------------------------------------------

    def add_pin(self, pos: tuple[float, float]) -> Pin:
        pin = Pin(self._next_pin, (float(pos[0]), float(pos[1])))
        self._next_pin += 1
        self.pins.append(pin)
        return pin

    def remove_pin(self, pin_id: int) -> None:
        self.pins = [p for p in self.pins if p.id != pin_id]
        if self.target == pin_id:
            self.target = None

    def pin_near(self, pos: tuple[float, float], radius: float) -> Pin | None:
        best, best_d = None, radius
        for p in self.pins:
            d = math.hypot(p.pos[0] - pos[0], p.pos[1] - pos[1])
            if d <= best_d:
                best, best_d = p, d
        return best

    def guidance(self, current: tuple[float, float] | None,
                 frame_w: int) -> tuple[float, str] | None:
        """Distance (in fields) and compass direction to the target pin.

        The one thing a hand-cranked return trip needs: a number that shrinks
        as you crank the right way.
        """
        if self.target is None or current is None or frame_w <= 0:
            return None
        pin = next((p for p in self.pins if p.id == self.target), None)
        if pin is None:
            return None
        dx = pin.pos[0] - current[0]
        dy = pin.pos[1] - current[1]
        fields = math.hypot(dx, dy) / frame_w
        # Image y grows downward, which is already how a map reads: +y is S.
        octant = int(round(math.atan2(dy, dx) / (math.pi / 4))) % 8
        return fields, _COMPASS[octant]

    # ---- extent ----------------------------------------------------------

    def bounds(self, extra: tuple[tuple[float, float], tuple[int, int]] | None
               = None) -> tuple[float, float, float, float] | None:
        """(x0, y0, x1, y1) covering every snapshot, pin, and optionally the
        current view -- so the fit-all render never crops anything off."""
        xs: list[float] = []
        ys: list[float] = []
        for s in self.snapshots + self.tiles:
            xs += [s.pos[0] - s.size[0] / 2, s.pos[0] + s.size[0] / 2]
            ys += [s.pos[1] - s.size[1] / 2, s.pos[1] + s.size[1] / 2]
        for p in self.pins:
            xs.append(p.pos[0])
            ys.append(p.pos[1])
        if extra is not None:
            (cx, cy), (w, h) = extra
            xs += [cx - w / 2, cx + w / 2]
            ys += [cy - h / 2, cy + h / 2]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)
