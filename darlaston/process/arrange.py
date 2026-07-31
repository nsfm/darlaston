"""Arranging: what Darlaston did with a bristle, done in software.

Victorian mounters did not merely photograph diatoms, they *arranged*
them -- rosettes, rows, spirals, whole pictures laid out one frustule at
a time under the scope with a single bristle and a great deal of
patience. The arranged slide is the art form this program is named
after, and it is the one thing here that is purely for delight.

So: find the specimens in a finished capture, cut each one out, turn it
to a canonical orientation, and lay them out in a pattern. The original
slide is untouched; the arrangement is a picture *of* it.

The finding is the honest part. Diatoms are textured and mountant is
smooth, so a pooled Laplacian energy separates them, and a component is
accepted only if it looks like a frustule -- elongated, convex enough,
big enough. On a sparse slide that works well. On a crowded one, where
valves overlap and touch, connected components merge them into one blob
and no amount of watershed fixes elongated objects lying across each
other; measured on a real dense field, it finds nothing at all. That is
reported rather than papered over, because an arrangement built from
mis-cut fragments would be worse than none.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .wiggle import load_pair

#: Working width for the search. Big enough to keep small valves, small
#: enough that the morphology is quick.
FIND_W = 2200
#: Structure-energy threshold, as a fraction of the field's 99th
#: percentile. Above this is textured enough to be a specimen.
ENERGY = 0.08
#: Shape gates. A frustule is elongated and reasonably solid; anything
#: outside these is debris, a clump, or two valves the mask has fused.
MIN_AREA = 1500
MIN_ELONGATION = 2.2
MAX_ELONGATION = 20.0
MIN_FILL = 0.55
#: Feather on the cut edge, in pixels: a hard cut looks like a sticker.
FEATHER = 3.0

_PAPER = (247, 245, 240)


@dataclass
class Specimen:
    """One cut-out frustule, turned so its long axis runs horizontally."""

    image: np.ndarray            # BGR, already rotated
    alpha: np.ndarray            # float32 0..1, same shape
    length: float                # long axis, in the source's pixels
    width: float
    area: float

    @property
    def aspect(self) -> float:
        return self.length / max(self.width, 1e-6)


def find_specimens(directory: Path | str,
                   limit: int = 60) -> list[Specimen]:
    """Cut every plausible frustule out of a finished capture."""
    img, _depth = load_pair(directory, width=FIND_W)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = cv2.Laplacian(grey, cv2.CV_32F, ksize=3)
    energy = cv2.GaussianBlur(lap * lap, (0, 0), 7.0)
    energy /= max(float(np.percentile(energy, 99)), 1e-6)

    mask = (energy > ENERGY).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)

    found: list[Specimen] = []
    for k in range(1, count):
        area = int(stats[k, cv2.CC_STAT_AREA])
        if area < MIN_AREA:
            continue
        blob = (labels == k).astype(np.uint8)
        contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue
        (cx, cy), (w, h), angle = cv2.minAreaRect(
            max(contours, key=cv2.contourArea))
        long_side, short_side = max(w, h), min(w, h)
        if short_side < 10:
            continue
        if not (MIN_ELONGATION < long_side / short_side < MAX_ELONGATION):
            continue
        if area / max(w * h, 1e-6) < MIN_FILL:
            continue
        # Touching the frame means we are looking at part of something.
        x, y = stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP]
        bw, bh = stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT]
        if x <= 1 or y <= 1 or x + bw >= img.shape[1] - 1 \
                or y + bh >= img.shape[0] - 1:
            continue

        # Turn the long axis horizontal, about the specimen's own centre.
        turn = angle if w >= h else angle + 90.0
        m = cv2.getRotationMatrix2D((cx, cy), turn, 1.0)
        pad = int(long_side / 2 + 12)
        m[0, 2] += pad - cx
        m[1, 2] += int(short_side / 2 + 12) - cy
        size = (pad * 2, int(short_side / 2 + 12) * 2)
        cut = cv2.warpAffine(img, m, size, flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        soft = cv2.warpAffine(blob.astype(np.float32) * 255, m, size,
                              flags=cv2.INTER_LINEAR)
        alpha = cv2.GaussianBlur(soft, (0, 0), FEATHER) / 255.0
        alpha = np.clip(alpha, 0, 1)
        # Stretch each specimen's own levels inside its own mask. A
        # brightfield frustule is a faint thing on a bright ground, and
        # lifted onto paper at capture contrast it reads as a ghost. This
        # is a display decision on a display artifact -- the plate keeps
        # capture contrast, because a plate is evidence and this is not.
        inside = alpha > 0.5
        if inside.sum() > 50:
            vals = cut[inside].astype(np.float32)
            lo = float(np.percentile(vals, 2))
            hi = float(np.percentile(vals, 98))
            if hi - lo > 4:
                cut = np.clip((cut.astype(np.float32) - lo)
                              * (235.0 / (hi - lo)) + 12.0,
                              0, 255).astype(np.uint8)
        found.append(Specimen(image=cut, alpha=alpha,
                              length=float(long_side),
                              width=float(short_side), area=float(area)))

    found.sort(key=lambda s: s.area, reverse=True)
    return found[:limit]


def _paste(sheet: np.ndarray, spec: Specimen, cx: float, cy: float,
           turn_deg: float, scale: float) -> None:
    """Composite one specimen onto the sheet, rotated and scaled."""
    h, w = spec.image.shape[:2]
    nw, nh = max(2, int(w * scale)), max(2, int(h * scale))
    img = cv2.resize(spec.image, (nw, nh), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(spec.alpha, (nw, nh), interpolation=cv2.INTER_AREA)

    diag = int(math.hypot(nw, nh)) + 4
    m = cv2.getRotationMatrix2D((nw / 2, nh / 2), turn_deg, 1.0)
    m[0, 2] += diag / 2 - nw / 2
    m[1, 2] += diag / 2 - nh / 2
    img = cv2.warpAffine(img, m, (diag, diag), flags=cv2.INTER_LINEAR,
                         borderValue=_PAPER)
    alpha = cv2.warpAffine(alpha, m, (diag, diag), flags=cv2.INTER_LINEAR,
                           borderValue=0.0)

    x0, y0 = int(cx - diag / 2), int(cy - diag / 2)
    x1, y1 = x0 + diag, y0 + diag
    sx0, sy0 = max(0, -x0), max(0, -y0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(sheet.shape[1], x1), min(sheet.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return
    a = alpha[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)][:, :, None]
    piece = img[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)].astype(np.float32)
    dst = sheet[y0:y1, x0:x1].astype(np.float32)
    sheet[y0:y1, x0:x1] = np.clip(piece * a + dst * (1 - a),
                                  0, 255).astype(np.uint8)


def arrange(specimens: list[Specimen], target: Path | str,
            style: str = "rosette", size: int = 1600,
            title: str = "") -> Path:
    """Lay specimens out on paper. Styles: rosette, spiral, rows.

    Sizes are preserved *relative to each other*: the arrangement is
    scaled as a whole so the largest specimen fits, and a valve twice
    the length of its neighbour still looks twice as long. Normalising
    each to the same size would be prettier and would lie.
    """
    if not specimens:
        raise ValueError("nothing to arrange — no specimens were found")
    # Balance the ring. Specimens arrive sorted by size, and placing them
    # in that order puts every big valve on one side and tips the whole
    # flower over. Dealing alternately from the two ends of the sorted
    # list spreads the sizes evenly around the circle, which is what an
    # arranger does by eye.
    if style != "rows":
        ordered, lo, hi = [], 0, len(specimens) - 1
        while lo <= hi:
            ordered.append(specimens[lo])
            lo += 1
            if lo <= hi:
                ordered.append(specimens[hi])
                hi -= 1
        specimens = ordered
    sheet = np.full((size, size, 3), _PAPER, np.uint8)
    cx = cy = size / 2
    longest = max(s.length for s in specimens)

    if style == "rows":
        columns = max(1, int(math.ceil(math.sqrt(len(specimens)))))
        rows = int(math.ceil(len(specimens) / columns))
        cell = size / max(columns, rows) * 0.92
        scale = min(1.0, cell / longest)
        for i, spec in enumerate(specimens):
            r, c = divmod(i, columns)
            x = size / 2 + (c - (columns - 1) / 2) * cell
            y = size / 2 + (r - (rows - 1) / 2) * cell
            _paste(sheet, spec, x, y, 0.0, scale)
    else:
        # Rosette and spiral both place radially, pointing outward, which
        # is how an arranger works around a centre. The spiral simply
        # lets the radius grow.
        n = len(specimens)
        # Sized so the longest specimen spans most of the radius: the
        # first attempt left a hole in the middle you could park a bus in.
        scale = min(1.4, (size * 0.42) / max(longest, 1e-6))
        for i, spec in enumerate(specimens):
            frac = i / max(n, 1)
            if style == "spiral":
                theta = frac * 2.0 * math.pi * 2.6
                radius = size * 0.05 + frac * size * 0.30
            else:
                theta = frac * 2.0 * math.pi
                radius = size * 0.14
            # Petals point outward: the specimen's long axis lies along
            # the radius, so the pattern reads as radiating rather than
            # as a ring of tick marks.
            reach = spec.length * scale / 2
            x = cx + math.cos(theta) * (radius + reach * 0.9)
            y = cy + math.sin(theta) * (radius + reach * 0.9)
            _paste(sheet, spec, x, y, -math.degrees(theta), scale)

    # Centre what was actually drawn: specimen lengths vary several-fold,
    # so the ideal circle and the ink on the paper are not concentric.
    ink = np.any(np.abs(sheet.astype(np.int16)
                        - np.int16(_PAPER)) > 6, axis=2)
    if ink.any():
        ys, xs = np.nonzero(ink)
        dx = int(size / 2 - (xs.min() + xs.max()) / 2)
        dy = int(size / 2 - (ys.min() + ys.max()) / 2)
        if dx or dy:
            m = np.float32([[1, 0, dx], [0, 1, dy]])
            sheet = cv2.warpAffine(sheet, m, (size, size),
                                   borderMode=cv2.BORDER_CONSTANT,
                                   borderValue=_PAPER)

    if title:
        cv2.putText(sheet, title, (34, 52), cv2.FONT_HERSHEY_DUPLEX, 0.9,
                    (28, 28, 30), 1, cv2.LINE_AA)
    note = (f"{len(specimens)} specimens, arranged by darlaston · "
            "relative sizes preserved")
    cv2.putText(sheet, note, (34, size - 26), cv2.FONT_HERSHEY_SIMPLEX,
                0.46, (120, 118, 112), 1, cv2.LINE_AA)

    target = Path(target)
    cv2.imwrite(str(target), sheet)
    return target


def arrangement(directory: Path | str, target: Path | str,
                style: str = "rosette", title: str = "") -> tuple[Path, int]:
    """Find and arrange in one call. Returns (path, how many were found)."""
    specimens = find_specimens(directory)
    return arrange(specimens, target, style=style, title=title), \
        len(specimens)


if __name__ == "__main__":
    import sys
    src, out = sys.argv[1], sys.argv[2]
    style = sys.argv[3] if len(sys.argv) > 3 else "rosette"
    path, n = arrangement(src, out, style=style, title="An arrangement")
    print(f"{path}  ({n} specimens)")
