"""Which pixels are the subject, and which are empty field.

`depth.png` is an argmax over per-slice sharpness, and argmax is undefined
where there is no sharpness. In a featureless region every slice scores
about zero plus sensor noise, so the winner is whichever exposure had the
largest noise excursion there: a near-uniform random draw. Measured on two
of Nate's stacks, background depth came out at 0.75 and 0.38 of uniform
entropy, spread over 18 of 30 and 7 of 15 slices.

This is also why the composites look fine while the depth maps do not. The
merge takes the winning slice's *pixel*, and where every slice looks
identical a random one is harmless. The depth map takes its *index*, and
there the coin toss is the whole output. So the cure belongs to the depth
map alone: measured against the composite, routing this into colour
selection erases material that was never in focus in any exposure, which on
a diatom mount means the specimens lying outside the captured z range.

The signal is enormous. Peak sharpness separates subject from field by
three orders of magnitude, 0.10 against 96.8 on the pseudoscorpion and 0.10
against 146.1 on the diatoms, so there are two decades of empty space to
put a threshold in. It need not be a constant at all: the populations
separate on a log scale, so Otsu finds the gap per stack. Verified against
an inverted stack standing in for darkfield, where the threshold moved from
10^0.27 to 10^0.80 and the mask barely changed, 25.0% of frame against
22.7%.

Result: background depth standard deviation 86.5 to 0 and 51.9 to 0, and
Nate's meshes went from unusable to the best he had had from the technique.
"""
from __future__ import annotations

import cv2
import numpy as np

from .stack import _sharpness

#: Subject fragments below this share of the frame are detector speckle
#: rather than specimen. One part in fifty thousand of a 20 MP frame is
#: about 400 half-res pixels, comfortably below any real diatom.
MIN_BLOB = 2e-5
#: Percentiles trimmed before the log histogram, so a handful of hot
#: pixels cannot set the scale Otsu searches within.
TRIM = (0.5, 99.5)


def peak_sharpness(lumas: np.ndarray) -> np.ndarray:
    """The best focus score each pixel ever achieved, over the stack."""
    best = None
    for luma in lumas:
        f = _sharpness(luma)
        best = f if best is None else np.maximum(best, f)
    return best


def subject(lumas: np.ndarray) -> np.ndarray:
    """True where the stack ever found focus. Otsu on the log peak.

    On the log because the two populations are separated by two decades
    and are nowhere near normal in linear space, which is also what makes
    the threshold transferable: it is found per stack from the gap between
    the populations rather than carried in as a constant.
    """
    v = np.log10(peak_sharpness(lumas) + 1e-4).astype(np.float32)
    lo, hi = np.percentile(v, TRIM)
    u8 = (np.clip((v - lo) / (hi - lo + 1e-9), 0, 1) * 255).astype(np.uint8)
    level, _ = cv2.threshold(u8, 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return u8 > level


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Close every region of `mask` that does not reach the frame edge.

    A featureless patch fully enclosed by confident subject is a hole in
    the *detector*, not in the specimen: a diatom frustule is a closed
    body and has no central void. Nate's observation, and it is a
    topological fact about the subject rather than another threshold.

    Seeded from the whole border. Flooding from the four corners instead
    is a real bug with real consequences, and this module's ancestor had
    it: any background region touching an edge without being
    corner-reachable reads as enclosed, which on a crowded diatom mount
    was 8.64% of the frame across 21 border-touching components of which
    only 3 were corner-reachable.
    """
    inv = (~mask).astype(np.uint8)
    n, lab = cv2.connectedComponents(inv, 4)
    border = np.unique(np.concatenate([lab[0, :], lab[-1, :],
                                       lab[:, 0], lab[:, -1]]))
    hole = np.ones(n, bool)
    hole[0] = False                        # label 0 is the mask itself
    hole[border[border > 0]] = False
    return mask | hole[lab]


def despeckle(mask: np.ndarray, min_share: float = MIN_BLOB) -> np.ndarray:
    """Drop connected components too small to be subject."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), 8)
    keep = np.zeros(n, bool)
    limit = min_share * mask.size
    for i in range(1, n):
        keep[i] = stats[i, cv2.CC_STAT_AREA] >= limit
    return keep[lab]


def body(lumas: np.ndarray) -> np.ndarray:
    """The subject as a solid: despeckled, hole filled, despeckled again.

    The second despeckle matters because filling holes can promote a ring
    of speckle into a solid blob that the first pass would have rejected.
    """
    return despeckle(fill_holes(despeckle(subject(lumas))))


def flatten(depth: np.ndarray, solid: np.ndarray) -> np.ndarray:
    """Background to a single plane, so those pixels stop voting.

    The plane sits at the background's own median depth rather than at an
    extreme of the specimen's range. An earlier version used the 2nd
    percentile of specimen depth and put the plane in *front* of the
    subject, which Nate saw as a pseudoscorpion at the ceiling with its
    body depressed into it. The median is unbiased and is what the meshes
    were judged on.

    The plane is kept rather than the vertices dropped: a solid background
    anchors the subject for printing, which is what Nate wants from these.
    """
    out = depth.copy()
    field = ~solid
    if field.any():
        out[field] = float(np.median(depth[field]))
    return out
