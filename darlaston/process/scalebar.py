"""A scale bar, drawn on a photograph.

Already drawn on the printed plate and nowhere else, which is a strange
place for the only one to live: the plate is a thing you make at the end
of a session, and the photograph is the thing you send somebody.

The bar is the part of a plate worth being careful about, because it is
the only element that makes a claim about the physical world. Everything
else on the sheet is arrangement. So the rules here are conservative and
they are all the same rule: **draw nothing rather than draw a bar that
might be wrong.**

- No micrometres-per-pixel, no bar. It comes from sensor pitch over total
  magnification and both have to be known.
- Nothing that would be under three characters wide, or wider than a third
  of the frame. A bar too small to read is decoration and a bar that
  spans the picture is not a reference.

It draws on a copy of the pixels a person looks at, never on the raw. The
DNG stays exactly what the sensor recorded.
"""
from __future__ import annotations

import cv2
import numpy as np

#: The 1-2-5 ladder. A scale bar says a round number or it says nothing:
#: "137 um" is arithmetic showing through, and nobody reads it as a
#: reference length.
STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)

#: The longest a bar may be, as a fraction of the image width.
MAX_FRACTION = 1 / 3.0
#: ...and the shortest, below which it is decoration rather than a
#: reference somebody could measure against.
MIN_FRACTION = 1 / 12.0

_INK = (28, 28, 30)
_PAPER = (255, 255, 255)


def choose(um_per_px: float, width_px: int) -> tuple[int, int] | None:
    """Pick a round length. Returns (micrometres, pixels), or None.

    Separate from the drawing because it is the part with a decision in
    it, and a decision worth testing on its own.
    """
    if not um_per_px or um_per_px <= 0 or width_px <= 0:
        return None
    longest = um_per_px * width_px * MAX_FRACTION
    shortest = um_per_px * width_px * MIN_FRACTION
    choice = next((s for s in reversed(STEPS) if s <= longest), None)
    if choice is None or choice < shortest:
        # Either the field is smaller than the finest step we name, or the
        # only round number that fits is too short to mean anything. Both
        # are "say nothing".
        return None
    return choice, int(round(choice / um_per_px))


def label(micrometres: int) -> str:
    """Micrometres, or millimetres once that stops being sensible.

    ASCII rather than the micro sign: this goes through OpenCV's Hershey
    fonts, which have no glyph for it and would draw a hollow box on the
    one element of the picture that is making a claim.
    """
    if micrometres >= 1000 and micrometres % 1000 == 0:
        return f"{micrometres // 1000} mm"
    return f"{micrometres} um"


def draw(image: np.ndarray, um_per_px: float | None, *,
         margin: float = 0.03) -> bool:
    """Draw into the bottom-right of `image`, in place. Did it draw?

    This used to refuse when the turret belief was unconfirmed, on the
    reasoning that a scale derived from a stale objective is a guess in
    the clothes of a measurement. The reasoning held and the premise did
    not: the detector fires on ordinary movements -- a coverslip ink
    border, a filter change -- so the bar would have vanished for reasons
    the operator could not see. A control that disappears unaccountably is
    worse than one that is occasionally wrong about a number they chose.
    """
    if not um_per_px or um_per_px <= 0:
        return False
    if image is None or image.ndim != 3:
        return False
    h, w = image.shape[:2]
    picked = choose(float(um_per_px), w)
    if picked is None:
        return False
    micrometres, length = picked

    # Everything scales off the frame, so the bar looks the same on a
    # 20 MP capture and on a plate cell a fifth the size. Tuned against
    # the plate's own numbers at its 560 px cell.
    unit = max(1.0, w / 560.0)
    thick = max(2, int(round(3 * unit)))
    pad = int(round(8 * unit))
    font_scale = 0.52 * unit
    weight = max(1, int(round(unit)))

    text = label(micrometres)
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                     font_scale, weight)
    inset = int(round(margin * w))
    x1 = w - inset
    x0 = x1 - length
    y1 = h - inset

    # The plate, worked out before anything is drawn, because it reaches
    # `pad` further than the bar does in every direction. Checking the bar
    # alone passed on a frame small enough that the inset was under the
    # padding, and the plate then ran off the bottom edge -- a clipped
    # bar, which is the one outcome this whole module exists to avoid.
    px0, py0 = x0 - pad, y1 - th - base - pad * 2
    px1, py1 = x1 + pad, y1 + pad
    if px0 < 0 or py0 < 0 or px1 > w or py1 > h:
        return False

    # A white plate under both, because this lands on a photograph whose
    # corner may be any brightness at all. Dark ink on an unknown
    # background is a bar you cannot read on half the pictures you take.
    cv2.rectangle(image, (px0, py0), (px1, py1), _PAPER, -1)
    cv2.rectangle(image, (x0, y1 - thick), (x1, y1), _INK, -1)
    cv2.putText(image, text,
                (x0 + (length - tw) // 2, y1 - thick - pad),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, _INK, weight,
                cv2.LINE_AA)
    return True
