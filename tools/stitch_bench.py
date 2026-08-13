"""Measured fence-posts for seam-aware stitching.

The stack bench (`stack_bench.py`) pins the merge; this pins the stitch.
Synthetic two-tile scenes with known ground truth, each modelling what the
seam is meant to fix or forbidden to break, and a table of the blends across
the metrics. Every change to `process.seam` must move these the right way
before it ships.

Blends:
  feather   -- the plain raised-cosine window the compositor ships.
  seam      -- the window narrowed by the seam multiplier (process.seam).
  multiband -- seam ownership blended per frequency (seam.multiband_blend),
               applied seam-locally; the archival ceiling for a specimen in
               an overlap that also carries a brightness step.
  lfc       -- a recorded dead-end (see blend_lfc): the cheap two-band
               approximation of multiband, kept to stop it being retried.

Scenes:
  ghost    -- a specimen sits in the overlap and the two tiles are placed
              with a known residual misalignment, so the feather sums it
              from both tiles slightly displaced: the soft double-image the
              seam exists to kill. The seam should route the whole specimen
              to one tile.
  straddle -- a specimen spans the entire overlap, no empty lane to route
              through. The seam cannot avoid it and must not pretend to:
              the feather widens and the result must match the plain blend,
              not regress.
  step     -- empty background with a brightness step between the tiles (the
              tiles are not normalised). A hard cut here would trade an
              invisible specimen seam for a visible background step, so the
              seam must stay graded: no worse a step than the feather.

Metrics, per scene:
  seam%    -- fraction of the seam curve (where ownership crosses 0.5) that
              lies on the specimen mask. The guarantee; target ~0 for ghost.
  ghost    -- RMSE against the clean single-tile oracle over the specimen
              region. The feather doubles the displaced specimen; a seam
              that routes around it does not.
  step     -- worst local gradient across the empty background. Catches a
              hard cut; must not exceed the feather's.

Run: PYTHONPATH=. .venv/bin/python tools/stitch_bench.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from darlaston.process import seam
from darlaston.process.stitch import _window

WHITE = 4095.0


# ---- scene construction -----------------------------------------------------

def _texture(rng, shape, scale, lo, hi):
    t = cv2.GaussianBlur(rng.normal(size=shape).astype(np.float32),
                         (0, 0), scale)
    t = (t - t.min()) / (np.ptp(t) + 1e-9)
    return lo + t * (hi - lo)


def _blob(rng, shape, cx, cy, r):
    """A high-contrast textured specimen: bright detailed disk, hard-edged
    enough to read as a frustule against empty field."""
    H, W = shape
    tex = _texture(rng, shape, 1.0, 1800, 3600)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    alpha = np.clip(r + 1.0 - d, 0, 1).astype(np.float32)
    return tex * alpha, alpha > 0.5


class Scene:
    """A ground-truth canvas cut into two horizontally-overlapping tiles.

    The tiles are exact crops of one canvas, so they agree perfectly; a
    residual is introduced by *telling* the compositor the second tile sits
    `res` pixels from where its content actually was cut, which is precisely
    how dead-reckoning drift ghosts a specimen in the overlap.
    """

    def __init__(self, canvas, mask, tw, overlap, res=(0.0, 0.0), gain_b=1.0):
        H, W = canvas.shape
        self.canvas = canvas
        self.mask = mask
        self.tw = tw
        self.th = H
        # Left tile [0:tw], right tile [W-tw:W]; they share `overlap` columns.
        self.ax, self.ay = 0, 0
        self.bx, self.by = W - tw, 0
        self.tile_a = canvas[:, :tw].copy()
        self.tile_b = (canvas[:, W - tw:].copy() * gain_b).astype(np.float32)
        self.overlap = overlap
        # The residual is the gap between where B's content was truly cut and
        # where the solver believes it sits. The compositor places tiles at
        # believed positions, so B's specimen lands displaced -- the ghost.
        self.bx_believed = self.bx + res[0]
        self.by_believed = self.by + res[1]
        self.pos_a = (self.ax + tw / 2.0, self.ay + self.th / 2.0)
        self.pos_b = (self.bx_believed + tw / 2.0,
                      self.by_believed + self.th / 2.0)
        self.W, self.H = W, H

    def oracle(self):
        """The clean canvas: one specimen, correctly placed, no ghost."""
        return self.canvas


# A generous overlap with a small specimen in it: the arrangement regime,
# where a diatom is a fraction of the overlap and the seam has room to route.
_H, _W, _TW = 240, 440, 300            # overlap = 2*TW - W = 160 columns
_CX, _CY = 220, 120                    # centre of the shared band


def scene_ghost():
    rng = np.random.default_rng(3)
    bg = _texture(rng, (_H, _W), 6.0, 600, 760)
    # One small specimen squarely in the overlap, empty field all around it.
    blob, mask = _blob(rng, (_H, _W), _CX, _CY, 18)
    canvas = np.where(mask, blob, bg).astype(np.float32)
    # A coherent residual like the documented dead-reckoning drift.
    return Scene(canvas, mask, _TW, 2 * _TW - _W, res=(9.0, 5.0))


def scene_straddle():
    rng = np.random.default_rng(5)
    bg = _texture(rng, (_H, _W), 6.0, 600, 760)
    # A specimen bar spanning the full width of the overlap: every top-to-
    # bottom seam must cross it, so there is no specimen-free route and the
    # seam must fall back to the feather rather than pretend it found one.
    tex = _texture(rng, (_H, _W), 1.0, 1800, 3600)
    yy, xx = np.mgrid[0:_H, 0:_W].astype(np.float32)
    mask = np.abs(yy - _CY) < 14
    canvas = np.where(mask, tex, bg).astype(np.float32)
    return Scene(canvas, mask, _TW, 2 * _TW - _W, res=(9.0, 5.0))


def scene_step():
    rng = np.random.default_rng(9)
    bg = _texture(rng, (_H, _W), 8.0, 640, 720)
    mask = np.zeros((_H, _W), bool)    # no specimen; empty field only
    # The right tile is 6% brighter: an unnormalised low-frequency step.
    return Scene(bg.astype(np.float32), mask, _TW, 2 * _TW - _W, gain_b=1.06)


def scene_ghost_step():
    """A specimen in the overlap *and* a brightness step: the case that
    separates single-band from multiband. The seam commits the specimen to
    one tile (no ghost), but a single-band feather narrow enough to do that
    turns the 6% step sharp. Multiband grades the step wide while keeping the
    specimen committed -- ghost gone and step gone at once."""
    rng = np.random.default_rng(15)
    bg = _texture(rng, (_H, _W), 6.0, 600, 760)
    blob, mask = _blob(rng, (_H, _W), _CX, _CY, 18)
    canvas = np.where(mask, blob, bg).astype(np.float32)
    return Scene(canvas, mask, _TW, 2 * _TW - _W, res=(9.0, 5.0), gain_b=1.06)


SCENES = {"ghost": scene_ghost, "straddle": scene_straddle,
          "step": scene_step, "ghost+step": scene_ghost_step}


# ---- the two blends ---------------------------------------------------------

def _place(scene, wa, wb):
    """The compositor's own acc/wacc blend, two tiles, given weight fields.

    Tiles land at *believed* positions -- A at the origin, B at the residual
    offset -- so the blend sees the same displacement the real stitch does.
    Slices are clipped to the canvas; a residual pushes a little of B off the
    edge, which is outside the overlap and does not matter.
    """
    H, W = scene.H, scene.W
    acc = np.zeros((H, W), np.float32)
    wacc = np.zeros((H, W), np.float32)
    places = ((scene.tile_a, scene.ax, scene.ay, wa),
              (scene.tile_b, int(round(scene.bx_believed)),
               int(round(scene.by_believed)), wb))
    for tile, x, y, w in places:
        th, tw = tile.shape
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + tw), min(H, y + th)
        sx0, sy0 = x0 - x, y0 - y
        acc[y0:y1, x0:x1] += (tile * w)[sy0:sy0 + (y1 - y0),
                                        sx0:sx0 + (x1 - x0)]
        wacc[y0:y1, x0:x1] += w[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    covered = wacc > 0
    out = np.zeros_like(acc)
    out[covered] = acc[covered] / wacc[covered]
    return out, wacc


def blend_feather(scene):
    wa = _window(scene.th, scene.tw)
    wb = _window(scene.th, scene.tw)
    out, _ = _place(scene, wa, wb)
    return out, None


def _seam_windows(scene):
    """The two tiles' final weights: the raised-cosine window narrowed by the
    seam multiplier, exactly as the compositor forms them."""
    lumas = [scene.tile_a, scene.tile_b]
    positions = [scene.pos_a, scene.pos_b]
    shapes = [(scene.th, scene.tw), (scene.th, scene.tw)]
    mult, _ = seam.weights(lumas, positions, shapes, scale=1.0)
    base = _window(scene.th, scene.tw)
    wa = base * cv2.resize(mult[0], (scene.tw, scene.th))
    wb = base * cv2.resize(mult[1], (scene.tw, scene.th))
    return wa, wb


def _ownership(scene, wa, wb):
    """Canvas-frame ownership fraction of tile A, placed where the tiles
    actually land (B at its believed position)."""
    own_a = np.zeros((scene.H, scene.W), np.float32)
    own_b = np.zeros((scene.H, scene.W), np.float32)
    bx = int(round(scene.bx_believed))
    x1 = min(scene.W, bx + scene.tw)
    own_a[:, scene.ax:scene.ax + scene.tw] += wa
    own_b[:, bx:x1] += wb[:, :x1 - bx]
    return own_a, own_b


def blend_seam(scene):
    wa, wb = _seam_windows(scene)
    out, _ = _place(scene, wa, wb)
    own_a, own_b = _ownership(scene, wa, wb)
    ratio = own_a / np.maximum(own_a + own_b, 1e-9)
    return out, ratio


def _canvas_tile(scene, tile, x, y, w):
    """A tile and its weight painted onto the full canvas, with a coverage
    mask, clipped at the edges the way the compositor clips."""
    H, W = scene.H, scene.W
    img = np.zeros((H, W), np.float32)
    wt = np.zeros((H, W), np.float32)
    cov = np.zeros((H, W), bool)
    th, tw = tile.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + tw), min(H, y + th)
    sx0, sy0 = x0 - x, y0 - y
    img[y0:y1, x0:x1] = tile[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    wt[y0:y1, x0:x1] = w[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    cov[y0:y1, x0:x1] = True
    return img, wt, cov


def blend_multiband(scene):
    """The seam's ownership mask blended per spatial frequency: the specimen
    committed sharp, a brightness step graded wide. Mirrors what a seam-local
    multiband pass in the compositor would produce.

    Seam-local: where no seam fired there is nothing to protect, and a global
    multiband only adds pyramid ringing to a gradient the feather already
    grades, so the pair falls back to the plain blend -- exactly what the
    compositor would do, running multiband only around a routed seam."""
    mult, _ = seam.weights([scene.tile_a, scene.tile_b],
                           [scene.pos_a, scene.pos_b],
                           [(scene.th, scene.tw)] * 2, scale=1.0)
    if min(float(m.min()) for m in mult) > 0.999:
        return blend_feather(scene)            # no seam here: plain blend
    wa, wb = _seam_windows(scene)
    a_img, a_wt, a_cov = _canvas_tile(scene, scene.tile_a, scene.ax, scene.ay,
                                      wa)
    b_img, b_wt, b_cov = _canvas_tile(
        scene, scene.tile_b, int(round(scene.bx_believed)),
        int(round(scene.by_believed)), wb)
    # Fill each tile's non-coverage with the other so the pyramid meets no
    # zero cliff at a tile edge; where a tile is absent its weight is 0, so
    # the fill never shows through.
    a_img = np.where(a_cov, a_img, b_img)
    b_img = np.where(b_cov, b_img, a_img)
    total = a_wt + b_wt
    w = np.where(total > 0, a_wt / np.maximum(total, 1e-9), 1.0)
    out = seam.multiband_blend(a_img, b_img, w)
    return out, w


def _canvas_ab(scene):
    """Both tiles and the seam ownership fraction, painted on the canvas with
    each tile's non-coverage filled by the other -- the shared prep for the
    multiband and streaming blends."""
    wa, wb = _seam_windows(scene)
    a_img, a_wt, a_cov = _canvas_tile(scene, scene.tile_a, scene.ax, scene.ay,
                                      wa)
    b_img, b_wt, b_cov = _canvas_tile(
        scene, scene.tile_b, int(round(scene.bx_believed)),
        int(round(scene.by_believed)), wb)
    a_img = np.where(a_cov, a_img, b_img)
    b_img = np.where(b_cov, b_img, a_img)
    total = a_wt + b_wt
    w = np.where(total > 0, a_wt / np.maximum(total, 1e-9), 1.0)
    return a_img, b_img, w


def blend_stream(scene, down=4):
    """The streaming-compatible multiband, as the reworked compositor will do
    it. Split at a cutoff scale: the low band is a *full* multiband but at
    1/`down` resolution, where it is cheap enough to hold and its per-level
    mask both commits a specimen's low frequencies and grades the brightness
    step wide; the high band is the detail above the cutoff, committed to one
    tile by the sharp seam mask and blended band by band. Sum them. No full
    pyramid at full resolution ever exists -- which is what lets the real
    compositor stream bands to the writer instead of holding a canvas."""
    a_img, b_img, w = _canvas_ab(scene)
    H, W = scene.H, scene.W
    lo = (max(1, W // down), max(1, H // down))

    def D(x):
        return cv2.resize(x, lo, interpolation=cv2.INTER_AREA)

    def U(x):
        return cv2.resize(x, (W, H), interpolation=cv2.INTER_LINEAR)

    low = U(seam.multiband_blend(D(a_img), D(b_img), D(w)))
    a_hi = a_img - U(D(a_img))
    b_hi = b_img - U(D(b_img))
    high = w * a_hi + (1.0 - w) * b_hi          # detail committed, sharp mask
    return low + high, w


def blend_lfc(scene, down=8):
    """Low-frequency compensation: a measured dead-end, kept as one.

    The hope was a streaming-cheap multiband: high frequencies from the seam
    composite (specimen committed, no ghost) and low frequencies from a
    wide-mask composite (brightness step graded), a global two-band split
    with no three-tile-junction patch boundary and no full pyramid to hold.

    It does not work, and the bench says so: ghost 445 -> 130 on the ghost
    scene (helps) but 0 -> 161 on ghost+step (a *regression* against the
    plain seam). The reason is fundamental. A specimen the seam routed around
    sits right beside the seam, so any spatially wide mask bleeds across it
    and de-commits it -- the ghost comes back. Full multiband escapes this
    because it is frequency-selective, not a single spatial mask: it commits
    detail with a sharp mask at the fine levels and grades the step with a
    wide mask at the coarse ones, in the same place. A two-band spatial split
    cannot do both at once where specimen and seam are close. So the archival
    win needs the real pyramid; this is here to stop the idea being retried."""
    mult, _ = seam.weights([scene.tile_a, scene.tile_b],
                           [scene.pos_a, scene.pos_b],
                           [(scene.th, scene.tw)] * 2, scale=1.0)
    base = _window(scene.th, scene.tw)
    wa = base * cv2.resize(mult[0], (scene.tw, scene.th))
    wb = base * cv2.resize(mult[1], (scene.tw, scene.th))
    seam_out, _ = _place(scene, wa, wb)

    sigma = scene.tw * 0.12                     # widen the ownership transition

    def wide(m):
        mm = cv2.resize(m, (scene.tw, scene.th))
        return base * cv2.GaussianBlur(mm, (0, 0), sigma)

    wide_out, _ = _place(scene, wide(mult[0]), wide(mult[1]))

    def lowpass(img):
        h, w = img.shape
        small = cv2.resize(img, (max(1, w // down), max(1, h // down)),
                           interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)

    correction = lowpass(wide_out) - lowpass(seam_out)
    return seam_out + correction, None


# ---- metrics ----------------------------------------------------------------

def _overlap_cols(scene):
    bx = int(round(scene.bx_believed))            # [x0, x1) shared columns
    return bx, scene.ax + scene.tw


def seam_on_specimen(scene, ratio):
    """Fraction of the seam curve that crosses the specimen mask."""
    if ratio is None or not scene.mask.any():
        return 0.0
    x0, x1 = _overlap_cols(scene)
    band = ratio[:, x0:x1]
    mask = scene.mask[:, x0:x1]
    # The seam is where ownership crosses 0.5: the transition, per row.
    crosses = (band[:, :-1] - 0.5) * (band[:, 1:] - 0.5) < 0
    on = crosses & mask[:, :-1]
    total = crosses.sum()
    return float(on.sum()) / float(total) if total else 0.0


def ghost_rmse(scene, out):
    """How far the specimen is from *any* single-tile placement.

    The ghost is doubling, not displacement: both tiles carry real residual,
    so neither position is the truth, and a specimen shown once -- from
    either tile -- is ghost-free even if it sits a few pixels from where the
    other tile would put it. So the reference is whichever single tile the
    result is closest to. The feather, summing two displaced copies at half
    each, is far from both; a seam that commits the specimen to one tile
    lands on it exactly."""
    if not scene.mask.any():
        return 0.0
    H, W = scene.th, scene.tw
    a_only, _ = _place(scene, np.ones((H, W), np.float32),
                       np.zeros((H, W), np.float32))
    b_only, _ = _place(scene, np.zeros((H, W), np.float32),
                       np.ones((H, W), np.float32))
    m = scene.mask
    to_a = float(np.sqrt(np.mean((out[m] - a_only[m]) ** 2)))
    to_b = float(np.sqrt(np.mean((out[m] - b_only[m]) ** 2)))
    return min(to_a, to_b)


def step_grad(scene, out):
    """Worst local gradient over the *clean* background of the overlap.

    This is the brightness-step test: an unnormalised step graded narrow (a
    seam that commits empty background to one tile) spikes here, while a wide
    grade (feather, or multiband's low frequencies) does not. The specimen
    and a margin around it are excluded, or its own committed edge -- real
    image content, not a defect -- would swamp the few-DN step entirely."""
    x0, x1 = _overlap_cols(scene)
    band = out[:, x0:x1]
    if scene.mask.any():
        near = cv2.dilate(scene.mask.astype(np.uint8),
                          np.ones((41, 41), np.uint8)).astype(bool)
        keep = ~near[:, x0:x1]
    else:
        keep = np.ones_like(band, bool)
    gx = np.abs(np.diff(band, axis=1))
    keep = keep[:, :-1]
    return float(gx[keep].max()) if keep.any() else 0.0


def main():
    print(f"{'scene':<10s} {'blend':<9s} {'seam%':>7s} {'ghost':>8s} "
          f"{'step':>7s}")
    for name, build in SCENES.items():
        scene = build()
        for blend_name, blend in (("feather", blend_feather),
                                  ("seam", blend_seam),
                                  ("multiband", blend_multiband),
                                  ("stream", blend_stream),
                                  ("lfc", blend_lfc)):
            out, ratio = blend(scene)
            s = seam_on_specimen(scene, ratio) * 100
            g = ghost_rmse(scene, out)
            st = step_grad(scene, out)
            print(f"{name:<10s} {blend_name:<9s} {s:>6.1f}% {g:>8.1f} "
                  f"{st:>7.1f}")


if __name__ == "__main__":
    main()
