"""The plate: several captures arranged on one sheet, with real scale bars.

Darlaston and Möller did not photograph diatoms, they *arranged* them --
patterns and rosettes and taxonomic plates, laid out by hand under the
scope with a bristle. This is the software descendant: a grid of finished
captures, each labelled and each carrying a scale bar computed from the
optics it was actually taken through rather than drawn by eye.

The scale bar is the part worth being careful about, because it is the
one graphic in the whole program that a reader will *measure*. It is
drawn from `um_per_px` in the file's own structured comment -- sensor
pitch over total magnification, recorded at capture -- and deliberately
not from EXIF's FocalPlaneXResolution, which is a scale at the *sensor*
and would be wrong by the entire magnification chain. A file without
that field gets no bar at all, because a wrong scale bar is far worse
than none.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .stitch import read_metadata
from .wiggle import _develop, _read_composite

#: Nice round bar lengths in microns; the longest that fits in a third of
#: the cell wins.
_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000)
#: Plate furniture, in pixels at output scale.
MARGIN = 46
GUTTER = 26
CAPTION_H = 52
TITLE_H = 96

_INK = (28, 28, 30)
_PAPER = (247, 245, 240)
_RULE = (120, 118, 112)


def _source_image(path: Path) -> tuple[np.ndarray, float | None, str]:
    """(BGR image, microns per pixel, label) for one plate entry.

    Accepts a stack session folder, a mosaic folder, or a single DNG.
    """
    path = Path(path)
    dng = path
    if path.is_dir():
        for name in ("stacked.dng", "composite.dng"):
            if (path / name).exists():
                dng = path / name
                break
        else:
            shots = sorted(path.glob("*.dng"))
            if not shots:
                raise FileNotFoundError(f"{path}: no DNG to put on the plate")
            dng = shots[0]
    rgb, white = _read_composite(dng)
    img = _develop(rgb, white)
    um_per_px = None
    label = ""
    try:
        meta = read_metadata(dng)
        if meta is not None:
            label = meta.lens_model or ""
            # From the comment, not from FocalPlaneXResolution: that tag
            # is a scale at the *sensor*, and a scale bar drawn from it
            # would be wrong by the whole magnification chain.
            for part in (meta.comment or "").split():
                if part.startswith("um_per_px="):
                    try:
                        um_per_px = float(part.split("=", 1)[1])
                    except ValueError:
                        um_per_px = None
    except Exception:
        pass                     # a plate must not fail over a missing tag
    return img, um_per_px, label


def _scale_bar(cell: np.ndarray, um_per_px: float | None,
               source_w: int) -> None:
    """Draw a bar into the bottom-right of `cell`, in place."""
    if not um_per_px or um_per_px <= 0:
        return
    h, w = cell.shape[:2]
    # Microns per pixel *as drawn*, after the cell's own downscale.
    drawn = um_per_px * source_w / w
    longest = drawn * (w / 3.0)
    choice = next((s for s in reversed(_STEPS) if s <= longest), None)
    if choice is None:
        return
    length = int(round(choice / drawn))
    x1, y1 = w - 18, h - 22
    x0 = x1 - length
    cv2.rectangle(cell, (x0 - 8, y1 - 26), (x1 + 8, y1 + 10),
                  (255, 255, 255), -1)
    cv2.rectangle(cell, (x0, y1), (x1, y1 + 5), _INK, -1)
    text = f"{choice} um" if choice < 1000 else f"{choice // 1000} mm"
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
    cv2.putText(cell, text, (x1 - length // 2 - tw // 2, y1 - 7),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, _INK, 1, cv2.LINE_AA)


def plate(sources, target: Path | str, columns: int = 3,
          cell: int = 560, title: str = "", footer: str = "") -> Path:
    """Arrange `sources` into one printable plate. Returns the path."""
    sources = [Path(s) for s in sources]
    if not sources:
        raise ValueError("a plate needs at least one capture")
    columns = max(1, min(columns, len(sources)))
    rows = (len(sources) + columns - 1) // columns

    cell_h = round(cell * 2 / 3)
    top = TITLE_H if title else MARGIN
    width = MARGIN * 2 + columns * cell + (columns - 1) * GUTTER
    height = (top + rows * (cell_h + CAPTION_H) + (rows - 1) * GUTTER
              + MARGIN + (34 if footer else 0))
    sheet = np.full((height, width, 3), _PAPER, np.uint8)

    if title:
        cv2.putText(sheet, title, (MARGIN, 62), cv2.FONT_HERSHEY_DUPLEX,
                    1.05, _INK, 1, cv2.LINE_AA)
        cv2.line(sheet, (MARGIN, 76), (width - MARGIN, 76), _RULE, 1,
                 cv2.LINE_AA)

    for i, source in enumerate(sources):
        r, c = divmod(i, columns)
        x = MARGIN + c * (cell + GUTTER)
        y = top + r * (cell_h + CAPTION_H + GUTTER)
        img, um_per_px, lens = _source_image(source)
        sh, sw = img.shape[:2]
        scale = min(cell / sw, cell_h / sh)
        tile = cv2.resize(img, (max(1, round(sw * scale)),
                                max(1, round(sh * scale))),
                          interpolation=cv2.INTER_AREA)
        _scale_bar(tile, um_per_px, sw)
        th, tw = tile.shape[:2]
        ox, oy = x + (cell - tw) // 2, y + (cell_h - th) // 2
        sheet[oy:oy + th, ox:ox + tw] = tile
        cv2.rectangle(sheet, (ox - 1, oy - 1), (ox + tw, oy + th), _RULE, 1)

        name = source.name
        cv2.putText(sheet, f"{i + 1}.  {name}", (x, y + cell_h + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, _INK, 1, cv2.LINE_AA)
        if lens:
            cv2.putText(sheet, lens, (x, y + cell_h + 44),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.46, _RULE, 1,
                        cv2.LINE_AA)

    if footer:
        cv2.putText(sheet, footer, (MARGIN, height - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, _RULE, 1, cv2.LINE_AA)

    target = Path(target)
    cv2.imwrite(str(target), sheet)
    return target


if __name__ == "__main__":
    import sys
    out = sys.argv[1]
    print(plate(sys.argv[2:], out, title="Plate"))
