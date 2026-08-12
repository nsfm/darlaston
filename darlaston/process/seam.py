"""Routing stitch seams through empty background.

The compositor (`stitch.composite`) blends overlapping tiles with a
raised-cosine window: over any overlap *both* tiles contribute across the
whole strip, weighted by distance to their own centre. That is a feather,
and it has one failure the arrangement work cares about -- where a specimen
sits in an overlap and the dead-reckoned positions are slightly off (the
module documents coherent residuals up to ~330 raw px at 25x), the feather
sums that specimen from two tiles at half each, slightly displaced: a soft
double-image. A seam that keeps the whole specimen inside *one* tile makes
the ghost impossible.

So this module decides, per overlapping pair, *where* the transition
between the two tiles should run, and hands the compositor a per-tile
*multiplier* to fold into its raised-cosine window. The rule is Efros &
Freeman's min-cost seam: over the overlap, find the path that crosses the
least specimen. The cost is specimen energy (fine-scale luma detail, which
lights up on a frustule and reads ~zero on empty field) plus a term that
pushes the seam to the *emptiest* run of background, not merely the first
non-specimen pixel -- margin against the very registration error the seam
exists to hide.

Two properties are load-bearing:

- **Feather across the seam, never a hard cut.** The tiles are not
  brightness-normalised, so a hard cut through near-uniform background
  would trade an invisible specimen seam for a *visible* low-frequency
  step (Mach bands). The side masks smoothstep across a band, so the
  transition stays graded.
- **Fall back to the old blend, exactly, where no specimen-free route
  exists.** The multiplier is 1 wherever the seam changes nothing, so an
  empty overlap or a specimen straddling the whole boundary (no lane) is
  left to the compositor's plain window -- bit for bit today's result. The
  guarantee is only claimed where it is earned, and nothing else regresses.

Everything is computed at a coarse *ownership scale* (a further decimation
of the composite scale): the seam only needs to be accurate to within its
own feather width, which is broad, so a small map decides it cheaply and
upsamples into the band loop. numpy + OpenCV only.
"""
from __future__ import annotations

import cv2
import numpy as np

#: The seam is decided on the composite scale decimated by this much again.
#: A broad feather does not need a fine seam, and a small map keeps the
#: per-pair shortest path cheap on a 40-tile mosaic.
OWN_DOWN = 4
#: Below this ownership-scale height/width an overlap is too thin to route a
#: seam through; the pair keeps the plain feather.
MIN_STRIP = 12
#: Smallest feather half-width, ownership-scale pixels: even a confident seam
#: through empty field grades over a few pixels rather than cutting.
FEATHER_MIN = 2.0
#: A specimen has to lift the local cost this many times above the background
#: floor to count as something the seam must protect. Diatoms separate from
#: field by ~3 decades (the stack mask reads the same gap); background
#: texture lifts it only ~2-3x. This factor sits in the two decades between,
#: so background never trips it and a specimen always does.
_SPECIMEN_FACTOR = 8.0
#: Most of the seam a genuine route leaves clear of the specimen entirely: a
#: specimen sitting *in* an overlap is skirted with zero crossings, while one
#: straddling the whole boundary forces the seam onto it for a quarter of its
#: length. This tolerance sits in that wide gap, so a clean lane fires and a
#: straddle -- however thin its bar -- falls back to the plain blend.
_MAX_CROSS = 0.03


def cost_field(luma: np.ndarray) -> np.ndarray:
    """Specimen energy: high on detail, ~zero on empty field.

    Fine-scale Laplacian magnitude, lightly pooled. A defocused glow skirt
    is a broad low-frequency pedestal and scores low here; a resolved
    frustule scores high. This is the same signal the stack-time mask reads
    (three decades of subject/field separation on diatoms), but computed
    from a single tile's luma so it works for plain and stacked tiles alike
    -- the seam cannot assume a mask exists.
    """
    lum = luma.astype(np.float32)
    lap = cv2.Laplacian(lum, cv2.CV_32F, ksize=3)
    return cv2.GaussianBlur(np.abs(lap), (0, 0), 1.5)


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _min_cost_seam(cost: np.ndarray, vertical: bool) -> np.ndarray:
    """Efros & Freeman min-cost path across a strip.

    `vertical` seam runs top to bottom and picks one column per row (the
    partition of a left/right overlap); otherwise it runs left to right and
    picks one row per column. Returns the seam index per line, in strip
    coordinates. The classic shape-from-focus dynamic program: each step
    may move by at most one pixel across the strip, so the seam is a
    connected curve, not a scatter of independent minima.
    """
    c = cost if vertical else cost.T           # normalise to: pick col per row
    h, w = c.shape
    acc = c.copy()
    back = np.zeros((h, w), np.int32)
    idx = np.arange(w)
    for r in range(1, h):
        prev = acc[r - 1]
        left = np.empty(w, np.float32)
        left[0] = np.inf
        left[1:] = prev[:-1]
        right = np.empty(w, np.float32)
        right[-1] = np.inf
        right[:-1] = prev[1:]
        step = np.argmin(np.stack([left, prev, right]), axis=0) - 1
        acc[r] = c[r] + np.minimum(np.minimum(left, prev), right)
        back[r] = np.clip(idx + step, 0, w - 1)
    seam = np.empty(h, np.int32)
    seam[-1] = int(np.argmin(acc[-1]))
    for r in range(h - 1, 0, -1):
        seam[r - 1] = back[r, seam[r]]
    return seam


def _feather_width(cost: np.ndarray, seam: np.ndarray, vertical: bool,
                   across: int) -> float | None:
    """The feather half-width for this pair, in ownership-scale pixels, or
    None when the pair should keep the plain raised-cosine blend untouched.

    Three regimes, decided from the cost the seam actually runs through:

    - **Empty strip** (no specimen anywhere): nothing to protect from
      ghosting, so the seam does nothing and the compositor's own window
      blends the pair exactly as it does today. Returns None.
    - **Seam forced through detail** (a specimen straddles the whole
      boundary, no lane): the crossing is unavoidable, so leave the old
      wide blend rather than cut through the specimen. The guarantee is not
      claimed here. Returns None.
    - **Route found** (the seam threads background around a specimen): the
      feather is sized to the seam's *clearance* from that specimen, so the
      graded band commits the specimen to one tile without ever bleeding
      onto it. This is the regime that kills the ghost. Returns the width.
    """
    c = cost if vertical else cost.T
    h = c.shape[0]
    on = c[np.arange(h), seam]
    floor = float(np.percentile(c, 20))
    peak = float(np.percentile(c, 98))
    if peak < _SPECIMEN_FACTOR * (floor + 1e-6):
        return None                            # background only: leave blend
    # The threshold between field and specimen is their log-midpoint: the two
    # populations separate multiplicatively, so the geometric mean sits in
    # the gap regardless of how bright either happens to be.
    gate = float(np.sqrt(floor * peak))
    if float((on >= gate).mean()) > _MAX_CROSS:
        return None                            # forced through: straddle
    # Route found: how close does the seam come to the specimen?
    spec = (c > gate).astype(np.uint8)
    dt = cv2.distanceTransform(1 - spec, cv2.DIST_L2, 3)
    clearance = float(dt[np.arange(h), seam].min())
    return float(np.clip(0.7 * clearance, FEATHER_MIN, 0.5 * across))


def _side_masks(shape: tuple[int, int], seam: np.ndarray, vertical: bool,
                feather: float) -> tuple[np.ndarray, np.ndarray]:
    """Graded ownership either side of the seam. First mask -> the low-index
    side (left, or top), second -> the high-index side. They sum to one, so
    the pair is a partition that feathers across `feather` pixels."""
    h, w = shape
    if vertical:
        coord = np.broadcast_to(np.arange(w)[None, :], (h, w))
        line = seam[:, None]
    else:
        coord = np.broadcast_to(np.arange(h)[:, None], (h, w))
        line = seam[None, :]
    t = (line - coord) / (2.0 * feather) + 0.5   # 1 well before, 0 well after
    lo = _smoothstep(t).astype(np.float32)
    return lo, (1.0 - lo).astype(np.float32)


#: Levels in the blend pyramid. Five spans from the finest detail (committed
#: to one tile at the seam) to a brightness step graded over ~16x the seam
#: width -- enough to hide an unnormalised step without a global pyramid.
MULTIBAND_LEVELS = 5


def _gaussian_pyramid(img: np.ndarray, levels: int) -> list[np.ndarray]:
    pyr = [img]
    for _ in range(levels - 1):
        if min(pyr[-1].shape[:2]) < 4:
            break
        pyr.append(cv2.pyrDown(pyr[-1]))
    return pyr


def _laplacian_pyramid(gpyr: list[np.ndarray]) -> list[np.ndarray]:
    lap = []
    for k in range(len(gpyr) - 1):
        size = (gpyr[k].shape[1], gpyr[k].shape[0])
        lap.append(gpyr[k] - cv2.pyrUp(gpyr[k + 1], dstsize=size))
    lap.append(gpyr[-1])                        # coarsest level is the residual
    return lap


def multiband_blend(a: np.ndarray, b: np.ndarray, w: np.ndarray,
                    levels: int = MULTIBAND_LEVELS) -> np.ndarray:
    """Burt & Adelson multiresolution blend of `a` and `b` by weight `w`.

    `w` is the weight of `a` in [0, 1] (so `b` gets `1 - w`). The blend is
    done per spatial frequency: at each pyramid level the mask is the
    Gaussian-decimated `w`, so the fine levels commit detail with a nearly
    sharp mask -- a specimen goes wholly to one tile, no ghost -- while the
    coarse levels blend with a mask smeared over many pixels, grading a
    low-frequency brightness step across a wide band that no single-band
    feather could match without also smearing the detail.

    This is the archival ceiling the seam mask alone cannot reach: the seam
    decides *where*, and given the same mask this makes the crossing
    invisible at every scale rather than only the one the feather picks.
    """
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    la = _laplacian_pyramid(_gaussian_pyramid(a, levels))
    lb = _laplacian_pyramid(_gaussian_pyramid(b, levels))
    gw = _gaussian_pyramid(w.astype(np.float32), levels)
    n = len(la)
    blended = []
    for k in range(n):
        wk = gw[k]
        if la[k].ndim == 3:
            wk = wk[:, :, None]
        blended.append(wk * la[k] + (1.0 - wk) * lb[k])
    out = blended[-1]
    for k in range(n - 2, -1, -1):
        size = (blended[k].shape[1], blended[k].shape[0])
        out = cv2.pyrUp(out, dstsize=size) + blended[k]
    return out


def weights(lumas: list[np.ndarray],
            positions: list[tuple[float, float]],
            shapes: list[tuple[int, int]],
            scale: float) -> tuple[list[np.ndarray], float]:
    """Per-tile weight *multipliers* that route seams around specimens.

    `lumas` are the half-resolution tile lumas registration already read
    (`stitch.luma_half`); `positions`/`shapes` are raw-pixel geometry;
    `scale` is the composite scale. Returns `(mult, own_scale)` where
    `mult[i]` is a float32 field at ownership scale, in [0, 1], that the band
    loop resizes to the tile's composite box and multiplies into its own
    raised-cosine `_window`.

    A multiplier is 1 everywhere the seam changes nothing, and drops toward 0
    on the far side of a seam that was routed around a specimen -- handing
    that specimen to a single tile. Because untouched pairs stay at 1, the
    compositor's plain blend is recovered *exactly* wherever no specimen-free
    route exists: empty overlaps, and specimens that straddle the whole
    boundary. The seam can only ever take weight away from a tile in the
    region a neighbour is better placed to own, so it cannot regress a blend
    that was already clean.
    """
    own_scale = scale / OWN_DOWN
    n = len(lumas)
    mult: list[np.ndarray] = []
    boxes: list[tuple[int, int, int, int]] = []
    costs: list[np.ndarray] = []
    for luma, pos, (th, tw) in zip(lumas, positions, shapes):
        ow = max(2, int(round(tw * own_scale)))
        oh = max(2, int(round(th * own_scale)))
        small = cv2.resize(luma, (ow, oh), interpolation=cv2.INTER_AREA)
        costs.append(cost_field(small))
        mult.append(np.ones((oh, ow), np.float32))
        x = (pos[0] - tw / 2.0) * own_scale
        y = (pos[1] - th / 2.0) * own_scale
        boxes.append((x, y, ow, oh))

    for i in range(n):
        xi, yi, wi, hi = boxes[i]
        for j in range(i + 1, n):
            xj, yj, wj, hj = boxes[j]
            # Overlap rectangle in ownership-canvas coordinates.
            ox0 = max(xi, xj)
            oy0 = max(yi, yj)
            ox1 = min(xi + wi, xj + wj)
            oy1 = min(yi + hi, yj + hj)
            ow = int(round(ox1 - ox0))
            oh = int(round(oy1 - oy0))
            if ow < MIN_STRIP or oh < MIN_STRIP:
                continue
            # The two tiles' cost over the shared rectangle; the seam should
            # avoid a specimen seen by *either* tile, so take the max.
            ai0x, ai0y = int(round(ox0 - xi)), int(round(oy0 - yi))
            aj0x, aj0y = int(round(ox0 - xj)), int(round(oy0 - yj))
            ca = costs[i][ai0y:ai0y + oh, ai0x:ai0x + ow]
            cb = costs[j][aj0y:aj0y + oh, aj0x:aj0x + ow]
            if ca.shape != cb.shape or ca.size == 0:
                continue
            cost = np.maximum(ca, cb)

            # Bias the seam toward the emptiest background, not the first
            # empty pixel: distance to the nearest specimen, inverted, so a
            # lane far from any detail is cheaper than one grazing a rim.
            thr = float(np.median(cost)) + 1e-6
            empty = (cost <= thr).astype(np.uint8)
            dt = cv2.distanceTransform(empty, cv2.DIST_L2, 3)
            span = float(dt.max()) + 1e-6
            routed = cost + thr * (1.0 - dt / span)

            # A left/right overlap is taller than wide: the seam runs down
            # it, picking a column per row. A top/bottom overlap runs across.
            vertical = oh >= ow
            seam = _min_cost_seam(routed, vertical)
            # Feather narrow where the seam threads background around a
            # specimen (no ghost); None where there was nothing to protect or
            # no lane to find, and then this pair keeps the plain blend.
            across = ow if vertical else oh
            feather = _feather_width(cost, seam, vertical, across)
            if feather is None:
                continue
            lo, hi_mask = _side_masks((oh, ow), seam, vertical, feather)
            # Which tile is on the low-index (left/top) side of the seam? The
            # one whose centre sits lower on that axis keeps that side; the
            # other's multiplier is driven toward zero there.
            if vertical:
                i_is_lo = (xi + wi / 2.0) <= (xj + wj / 2.0)
            else:
                i_is_lo = (yi + hi / 2.0) <= (yj + hj / 2.0)
            mi = lo if i_is_lo else hi_mask
            mj = hi_mask if i_is_lo else lo
            mult[i][ai0y:ai0y + oh, ai0x:ai0x + ow] *= mi
            mult[j][aj0y:aj0y + oh, aj0x:aj0x + ow] *= mj

    return mult, own_scale
