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

    def nudge(self, delta: tuple[float, float]) -> None:
        """Shift the position and its anchor together, by an externally
        measured correction -- the relocalizer matching the view against
        the map's own bank. Moving both keeps the keyframe arithmetic
        consistent: the next `anchor` still measures a true shift from a
        keyframe whose planted position moved with the belief."""
        if not self._ever:
            return
        self._pos = (self._pos[0] + delta[0], self._pos[1] + delta[1])
        self._anchor = (self._anchor[0] + delta[0],
                        self._anchor[1] + delta[1])

    def refix(self, pos: tuple[float, float]) -> None:
        """Plant the position absolutely: the relocalizer found familiar
        ground after a blank crossing. Deliberately does not claim a
        lock -- the next good correlation earns that -- and the caller
        must drop the keyframe, which belongs to ground from before the
        crossing."""
        self._pos = (float(pos[0]), float(pos[1]))
        self._anchor = self._pos
        self._ever = True

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


class Terrain:
    """The slide as one picture: a world-space canvas at thumbnail scale.

    The map used to be a list of postcard snapshots, composited at every
    paint and matched against as separate candidates. This is the other
    architecture, spiked before building (spike/tracking/canvas_spike.py):
    one growing canvas that observations are painted into. Painting is a
    feathered paste at 0.3 ms; a session's canvas is a few megabytes;
    coverage can only grow, which retires the whole family of bugs where
    refreshing a postcard orphaned the ground it used to cover; and the
    relocalizer matches against the terrain at a believed position rather
    than holding a beauty contest between lookalike postcards -- which
    removed a measured 1100 px aliasing tail outright.

    Numpy only, deliberately: Qt lives in widgets and nowhere below it.
    The canvas is premultiplied BGRA where **alpha is the validity
    mask** -- 255 where painted, 0 over the void, and the void's colour
    is zero, which premultiplied alpha requires anyway. One buffer
    serves the map view (wrapped as a QImage without copying) and the
    matcher (grey windows cut from it on demand).

    All positions are view *centres* in preview pixels, the same
    coordinates the tracker integrates and the postcards used.
    """

    #: Canvas pixels across one field: the resolution rule, shared with
    #: the postcard thumbnails the mosaic tiles still use.
    THUMB_W = 120
    #: Canvas pixels of edge ramp where painting over existing terrain.
    #: Full strength over the void: a ramp against nothing would fringe
    #: the frontier dark.
    FEATHER = 6
    #: Canvas pixels of headroom added per growth, so roaming does not
    #: reallocate every field.
    MARGIN = 240
    #: Hard ceiling per axis, in canvas pixels -- forty fields across.
    #: Far beyond any session, and the map stops growing rather than
    #: evicting: dropping old terrain would erase the very ground the
    #: operator wants to find again.
    MAX_SIDE = 4800
    #: Share of a window that must be painted before it may be matched.
    #: The void is zeros, and zeros correlate like blank glass lies.
    WINDOW_VALID = 0.6

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.rgba: np.ndarray | None = None
        #: World coordinate (preview px) of canvas pixel (0, 0).
        self.org = (0.0, 0.0)
        #: Preview size this map is at scale for. Any other size is
        #: refused: a mode change rescales the world, and the window
        #: clears the map when that happens.
        self.size: tuple[int, int] | None = None
        self._th = 0
        self._ramp: np.ndarray | None = None
        self._painted = 0
        self._grown = 0

    # ---- geometry --------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self.rgba is not None and self._painted > 0

    @property
    def scale(self) -> float:
        """Preview pixels per canvas pixel."""
        return (self.size[0] / self.THUMB_W) if self.size else 0.0

    @property
    def fields_painted(self) -> float:
        """Painted area, in fields -- the honest version of the old
        snapshot count, which was postcards rather than ground."""
        return self._painted / max(self.THUMB_W * self._th, 1)

    def bounds(self) -> tuple[float, float, float, float] | None:
        """World extent of the painted ground, or None before any."""
        if not self.ready:
            return None
        ys, xs = np.nonzero(self.rgba[..., 3])
        s = self.scale
        return (self.org[0] + float(xs.min()) * s,
                self.org[1] + float(ys.min()) * s,
                self.org[0] + float(xs.max() + 1) * s,
                self.org[1] + float(ys.max() + 1) * s)

    def _to_canvas(self, world: tuple[float, float]) -> tuple[float, float]:
        s = self.scale
        return ((world[0] - self.org[0]) / s, (world[1] - self.org[1]) / s)

    # ---- painting --------------------------------------------------------

    def _establish(self, w: int, h: int, pos: tuple[float, float]) -> None:
        self.size = (w, h)
        self._th = max(1, round(self.THUMB_W * h / w))
        f = float(self.FEATHER)
        yy, xx = np.mgrid[0:self._th, 0:self.THUMB_W].astype(np.float32)
        d = np.minimum.reduce([xx, yy, self.THUMB_W - 1 - xx,
                               self._th - 1 - yy])
        self._ramp = np.clip((d + 1) / f, 0.0, 1.0)[..., None]
        side_w = self.THUMB_W + 2 * self.MARGIN
        side_h = self._th + 2 * self.MARGIN
        self.rgba = np.zeros((side_h, side_w, 4), np.uint8)
        s = self.scale
        self.org = (pos[0] - w / 2 - self.MARGIN * s,
                    pos[1] - h / 2 - self.MARGIN * s)

    def _grow(self, x0: int, y0: int) -> tuple[int, int] | None:
        """Reallocate so a paste at canvas (x0, y0) fits, with margin.
        Returns the paste position in the (possibly new) canvas, or None
        when growth would pass the ceiling."""
        h, w = self.rgba.shape[:2]
        left = self.MARGIN - x0 if x0 < 0 else 0
        top = self.MARGIN - y0 if y0 < 0 else 0
        right = (x0 + self.THUMB_W + self.MARGIN - w
                 if x0 + self.THUMB_W > w else 0)
        bottom = (y0 + self._th + self.MARGIN - h
                  if y0 + self._th > h else 0)
        if not (left or top or right or bottom):
            return x0, y0
        nw, nh = w + left + right, h + top + bottom
        if nw > self.MAX_SIDE or nh > self.MAX_SIDE:
            return None
        grown = np.zeros((nh, nw, 4), np.uint8)
        grown[top:top + h, left:left + w] = self.rgba
        self.rgba = grown
        self._grown += 1
        s = self.scale
        self.org = (self.org[0] - left * s, self.org[1] - top * s)
        return x0 + left, y0 + top

    def paint(self, pos: tuple[float, float], preview: np.ndarray) -> bool:
        """Lay the current view into the map at its world position.
        Returns whether anything changed."""
        h, w = preview.shape[:2]
        if self.size is None:
            self._establish(w, h, pos)
        elif (w, h) != self.size:
            return False
        thumb = cv2.resize(preview, (self.THUMB_W, self._th),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        s = self.scale
        cx = (pos[0] - w / 2 - self.org[0]) / s
        cy = (pos[1] - h / 2 - self.org[1]) / s
        at = self._grow(int(round(cx)), int(round(cy)))
        if at is None:
            return False
        x0, y0 = at
        region = self.rgba[y0:y0 + self._th, x0:x0 + self.THUMB_W]
        painted = region[..., 3:] > 0
        # Feathered newest-wins. Averaging measured no better under the
        # sub-canvas-pixel misregistration that drift correction leaves,
        # and newest-wins keeps the map saying what the slide looks like
        # *now*, which is the map a live subject needs.
        alpha = np.where(painted, self._ramp, 1.0)
        rgb = region[..., :3].astype(np.float32)
        region[..., :3] = (rgb * (1.0 - alpha)
                           + thumb * alpha).astype(np.uint8)
        self._painted += int(region.shape[0] * region.shape[1]
                             - np.count_nonzero(painted))
        region[..., 3] = 255
        return True

    # ---- matching --------------------------------------------------------

    def window(self, pos: tuple[float, float]):
        """(grey float window, painted share, actual centre) at a world
        position, or (None, 0.0, None) off the canvas.

        The window lands on an integer canvas pixel, so its *actual*
        centre is returned alongside: the rounding remainder is up to
        half a canvas pixel -- eight preview pixels -- and a fix computed
        against the request inherits it as error. That number was paid
        once, in the spike, to be remembered here.
        """
        if self.rgba is None:
            return None, 0.0, None
        cx, cy = self._to_canvas(pos)
        x0 = int(round(cx - self.THUMB_W / 2))
        y0 = int(round(cy - self._th / 2))
        h, w = self.rgba.shape[:2]
        if x0 < 0 or y0 < 0 or x0 + self.THUMB_W > w or y0 + self._th > h:
            return None, 0.0, None
        region = self.rgba[y0:y0 + self._th, x0:x0 + self.THUMB_W]
        share = float(np.count_nonzero(region[..., 3])) \
            / (self.THUMB_W * self._th)
        gray = cv2.cvtColor(region[..., :3], cv2.COLOR_BGR2GRAY) \
            .astype(np.float32)
        s = self.scale
        at = (self.org[0] + (x0 + self.THUMB_W / 2) * s,
              self.org[1] + (y0 + self._th / 2) * s)
        return gray, share, at

    def candidates(self) -> list:
        """World centres of every matchable window, on a half-window
        grid over the painted ground. The integral image makes the
        validity test one subtraction per position."""
        if not self.ready:
            return []
        alpha = (self.rgba[..., 3] > 0).astype(np.uint8)
        integral = cv2.integral(alpha)
        h, w = alpha.shape
        tw, th = self.THUMB_W, self._th
        need = self.WINDOW_VALID * tw * th
        s = self.scale
        out = []
        for y0 in range(0, h - th + 1, max(1, th // 2)):
            for x0 in range(0, w - tw + 1, max(1, tw // 2)):
                filled = (integral[y0 + th, x0 + tw]
                          - integral[y0, x0 + tw]
                          - integral[y0 + th, x0] + integral[y0, x0])
                if filled >= need:
                    out.append((self.org[0] + (x0 + tw / 2) * s,
                                self.org[1] + (y0 + th / 2) * s))
        return out


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

    #: Repaint the current ground once the view has moved this far, in
    #: frame widths. Under this, a paint changes almost nothing and a
    #: panning hand repaints plenty; the parked case is covered below.
    PAINT_STEP = 0.04
    #: Frames between paints of a parked view, so revisited ground still
    #: freshens -- a live subject moves under a still stage -- without
    #: painting the same pixels thirty times a second.
    REFRESH_EVERY = 24
    THUMB_W = 120

    def __init__(self) -> None:
        #: The ground itself, as one growing picture. Coverage can only
        #: grow: painting is additive, which retires the whole family of
        #: bugs where refreshing a postcard orphaned ground it covered.
        self.terrain = Terrain()
        self.pins: list[Pin] = []
        #: Captured mosaic tiles, drawn over the reconnaissance layer. Same
        #: world space; a tile is just a snapshot somebody paid 40 MB for.
        self.tiles: list[Snapshot] = []
        self.target: int | None = None      # pin id being navigated to
        self._next_pin = 1
        self._since_paint = 0
        self._last_paint: tuple[float, float] | None = None

    def reset(self) -> None:
        self.terrain.reset()
        self.pins.clear()
        self.tiles.clear()
        self.target = None
        self._next_pin = 1
        self._since_paint = 0
        self._last_paint = None

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
        self._since_paint += 1
        if pos is None or not tracking:
            return False
        w = preview.shape[1]
        if self._last_paint is not None:
            moved = math.hypot(pos[0] - self._last_paint[0],
                               pos[1] - self._last_paint[1])
            if (moved < self.PAINT_STEP * w
                    and self._since_paint < self.REFRESH_EVERY):
                return False
        if not self.terrain.paint(pos, preview):
            return False
        self._last_paint = (float(pos[0]), float(pos[1]))
        self._since_paint = 0
        return True

    def _snap(self, pos: tuple[float, float], preview: np.ndarray) -> Snapshot:
        h, w = preview.shape[:2]
        tw = self.THUMB_W
        th = max(1, round(tw * h / w))
        thumb = cv2.resize(preview, (tw, th), interpolation=cv2.INTER_AREA)
        return Snapshot(pos=(float(pos[0]), float(pos[1])), size=(w, h),
                        thumb=thumb)

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
        """(x0, y0, x1, y1) covering the painted ground, every tile and
        pin, and optionally the current view -- so the fit-all render
        never crops anything off."""
        xs: list[float] = []
        ys: list[float] = []
        ground = self.terrain.bounds()
        if ground is not None:
            xs += [ground[0], ground[2]]
            ys += [ground[1], ground[3]]
        for s in self.tiles:
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
