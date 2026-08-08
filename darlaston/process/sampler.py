"""Showing the operator a few settings and letting them pick.

The slope bound is the one control in the stack pipeline with no derivable
optimum: it depends on the subject, not the optics, and the objective is
which photograph looks better. So the interface is a comparison rather than
a number, and the whole point is that it appears mid-merge, when the answer
is still cheap to change.

**Where to sample.** At a subject boundary that also carries depth
contrast. That is where the settings differ most, and where the operator
can actually see a difference. A window of empty field would show nothing
at all, and a boundary between two similar depths not much more, which is
why the score is boundary strength times local depth range rather than
either alone.

**Why it is fast enough to be live.** Reading slices, registering them and
mapping depth is 9 to 20 seconds and happens once. After that a variant
costs 11 to 28 ms, because the clamp's influence is *bounded*: a seed at
depth s can constrain a pixel only within |s - d| / slope pixels of it, so
outside `range / slope` nothing reaches. Cropping with that margin is
exact rather than approximate, measured at a maximum blend difference of
zero against clamping the whole frame; without the margin the same crops
were wrong by 2.2 to 7.2 slices.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import slope

#: Sample window, in half-res pixels. Large enough to hold a boundary and
#: some of the field beside it at a glance.
CROP = 224
#: Ceiling on the clamp margin, for a gentle bound over a deep stack where
#: the exact influence radius would be most of the frame anyway.
MAX_MARGIN = 320


def margin_for(slope_: float, levels: int, feather: float = 0.0) -> int:
    """How far outside a crop the render has to look, in pixels.

    Two reaches, and the larger wins. A clamp seed at depth s constrains a
    pixel only within |s - d| / slope of it. The blend's feather is a
    Gaussian on the weights, which reaches about three sigma.
    """
    reach = 0.0
    if slope_ > 0:
        reach = min(MAX_MARGIN, np.ceil(max(levels - 1, 1) / slope_))
    return int(max(reach, np.ceil(3 * feather)))


def crops(depth: np.ndarray, solid: np.ndarray, count: int = 3,
          size: int = CROP) -> list[tuple[int, int]]:
    """Windows worth comparing settings in, as (top, left) pairs.

    Scored by subject boundary times local depth range, so a long edge
    between two similar depths loses to one where the geometry steps.
    Chosen greedily and then suppressed, so the windows do not overlap.
    """
    h, w = depth.shape
    size = min(size, h, w)
    edge = cv2.morphologyEx(solid.astype(np.uint8), cv2.MORPH_GRADIENT,
                            np.ones((5, 5), np.uint8)).astype(np.float32)
    k = np.ones((9, 9), np.uint8)
    relief = cv2.dilate(depth, k) - cv2.erode(depth, k)
    score = cv2.boxFilter(edge * relief, -1, (size // 2, size // 2))
    half = size // 2
    score[:half] = score[h - half:] = 0
    score[:, :half] = score[:, w - half:] = 0

    out = []
    for _ in range(count):
        _, peak, _, (cx, cy) = cv2.minMaxLoc(score)
        if peak <= 0:
            break
        out.append((int(np.clip(cy - half, 0, h - size)),
                    int(np.clip(cx - half, 0, w - size))))
        cv2.circle(score, (cx, cy), size, 0, -1)
    return out


def render(lumas: np.ndarray, depth: np.ndarray, box: tuple[int, int],
           slope_: float, levels: int, size: int = CROP,
           width: np.ndarray | float = 1.0,
           feather: float = 0.0) -> np.ndarray:
    """One crop under one slope, blended as the merge blends.

    Every step the merge takes is taken here: the per-pixel hat width from
    confidence, the normalisation that variable width makes necessary, and
    the Gaussian feather on the weights. An earlier version skipped all
    three and used a unit-width hat, which differed from the real blend by
    a mean of 1.2 and a maximum of 23 counts on a crop spanning 155. Small,
    and wrong in a way that matters here specifically: the operator is
    choosing against these pictures and receiving another one.

    The clamp and the feather both run over the crop plus their reach and
    the surplus is discarded, so this agrees with the whole-frame merge
    rather than approximating it.
    """
    y, x = box
    m = margin_for(slope_, levels, feather)
    h, w = depth.shape
    y0, x0 = max(0, y - m), max(0, x - m)
    y1, x1 = min(h, y + size + m), min(w, x + size + m)
    sub = (slice(y - y0, y - y0 + size), slice(x - x0, x - x0 + size))

    d = depth[y0:y1, x0:x1]
    if slope_ > 0:
        d = slope.clamp(d, slope_)
    wd = width[y0:y1, x0:x1] if isinstance(width, np.ndarray) else width

    norm = np.zeros_like(d)
    for i in range(levels):
        norm += np.clip(1.0 - np.abs(d - i) / wd, 0.0, 1.0)
    np.maximum(norm, 1e-6, out=norm)

    acc = None
    for i in range(levels):
        hat = np.clip(1.0 - np.abs(d - i) / wd, 0.0, 1.0) / norm
        if feather > 0:
            hat = cv2.GaussianBlur(hat, (0, 0), feather)
        piece = lumas[i][y0:y1, x0:x1] * hat
        acc = piece if acc is None else acc + piece
    return acc[sub]


def preview(lumas: np.ndarray, depth: np.ndarray, solid: np.ndarray,
            slopes, count: int = 3, size: int = CROP,
            width: np.ndarray | float = 1.0, feather: float = 0.0):
    """(boxes, {slope: [crop per box]}) for the whole comparison."""
    boxes = crops(depth, solid, count, size)
    levels = len(lumas)
    return boxes, {s: [render(lumas, depth, b, s, levels, size,
                              width, feather)
                       for b in boxes] for s in slopes}
