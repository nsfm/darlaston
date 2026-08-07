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

#: Smaller than this and there is no room for a bar and a label without
#: one of them dominating the picture.
MIN_WIDTH = 240
MIN_HEIGHT = 160

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


#: How the bar is dressed. The measurement is identical in all of them;
#: only the furniture differs, and furniture is taste.
STYLES = ("adaptive", "plate", "scrim", "ruler", "caps", "shadow")
DEFAULT_STYLE = "adaptive"

_FONT = cv2.FONT_HERSHEY_DUPLEX
_INK_DARK = (16, 16, 18)
_INK_LIGHT = (250, 250, 250)


def _geometry(image, um_per_px, margin):
    """Everything the styles share: where the bar goes and how big.

    `unit` scales the furniture off the frame, so a bar looks the same on
    a 20 MP capture and on a plate cell a fifth the size. A fixed pixel
    thickness is a hairline on one and a slab on the other.
    """
    h, w = image.shape[:2]
    # Below this it is a thumbnail rather than a photograph, and every
    # style's furniture -- a rule plus a line of type plus its insets --
    # would take a third of the frame. Only `plate` used to notice,
    # because it alone checked whether its own tablet fitted; the rest
    # cheerfully drew a bar taller than the picture.
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return None
    picked = choose(float(um_per_px), w)
    if picked is None:
        return None
    micrometres, length = picked
    unit = max(0.6, w / 900.0)
    inset = int(round(margin * w))
    x1, y1 = w - inset, h - inset
    x0 = x1 - length
    if x0 < inset // 2 or y1 - int(40 * unit) < 0:
        return None                 # it does not fit; a clipped bar is worse
    return micrometres, length, unit, x0, x1, y1


def _shadow(image, mark, spread, strength=0.6):
    """A real soft shadow: draw the mark into a mask, blur it, darken under.

    Eight offset copies of the text was the first attempt and it looked
    like a smear. This is one blur and it reads as a shadow.
    """
    mask = np.zeros(image.shape[:2], np.uint8)
    mark(mask, 255)
    k = max(3, int(spread) | 1)
    blur = cv2.GaussianBlur(mask, (k, k), 0).astype(np.float32) / 255.0
    blur = np.clip(blur * 1.9, 0.0, 1.0)[..., None]
    image[:] = (image.astype(np.float32) * (1.0 - blur * strength)).astype(
        np.uint8)


def draw(image: np.ndarray, um_per_px: float | None, *,
         style: str = DEFAULT_STYLE, margin: float = 0.035) -> bool:
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
    geom = _geometry(image, um_per_px, margin)
    if geom is None:
        return False
    fn = _STYLES.get(style) or _STYLES[DEFAULT_STYLE]
    fn(image, *geom)
    return True


# ---- the styles ------------------------------------------------------------

def _adaptive(img, m, length, unit, x0, x1, y1):
    """No furniture at all: read the corner and pick ink that contrasts.

    The default, because it is the only one that adds nothing to the
    photograph but the mark itself. Brightfield gets dark ink, darkfield
    and inverted get light, and the decision is measured off the pixels
    the bar is about to cover rather than assumed from the illumination
    setting -- which is right about the lamp and silent about the subject.
    """
    thick = max(2, int(round(4 * unit)))
    text = label(m)
    fs, wt = 0.85 * unit, max(1, int(round(1.6 * unit)))
    (tw, th), _ = cv2.getTextSize(text, _FONT, fs, wt)
    org = (x0 + (length - tw) // 2, y1 - thick - int(10 * unit))
    region = img[max(0, org[1] - th):y1 + 2, x0:x1]
    ink = _INK_DARK if float(region.mean()) > 120 else _INK_LIGHT
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), ink, -1)
    cv2.putText(img, text, org, _FONT, fs, ink, wt, cv2.LINE_AA)


def _plate(img, m, length, unit, x0, x1, y1):
    """The journal figure: white tablet, black rule. What we shipped."""
    thick = max(2, int(round(3 * unit)))
    text = label(m)
    fs, wt = 0.72 * unit, max(1, int(round(1.2 * unit)))
    (tw, th), base = cv2.getTextSize(text, _FONT, fs, wt)
    pad = int(round(11 * unit))
    px0, py0 = x0 - pad, y1 - th - base - pad * 2
    px1, py1 = x1 + pad, y1 + pad
    h, w = img.shape[:2]
    if px0 < 0 or py0 < 0 or px1 > w or py1 > h:
        return
    cv2.rectangle(img, (px0, py0), (px1, py1), (255, 255, 255), -1)
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), _INK_DARK, -1)
    cv2.putText(img, text, (x0 + (length - tw) // 2, y1 - thick - pad),
                _FONT, fs, _INK_DARK, wt, cv2.LINE_AA)


def _scrim(img, m, length, unit, x0, x1, y1):
    """A translucent dark tablet. Legible over anything, at the cost of
    covering a little of the picture."""
    thick = max(2, int(round(3.5 * unit)))
    text = label(m)
    fs, wt = 0.78 * unit, max(1, int(round(1.4 * unit)))
    (tw, th), base = cv2.getTextSize(text, _FONT, fs, wt)
    pad = int(round(13 * unit))
    h, w = img.shape[:2]
    bx0, by0 = max(0, x0 - pad), max(0, y1 - thick - th - base - pad)
    bx1, by1 = min(w, x1 + pad), min(h, y1 + pad)
    patch = img[by0:by1, bx0:bx1].astype(np.float32)
    img[by0:by1, bx0:bx1] = (patch * 0.38 + 18 * 0.62).astype(np.uint8)
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), _INK_LIGHT, -1)
    cv2.putText(img, text, (x0 + (length - tw) // 2, y1 - thick - int(9 * unit)),
                _FONT, fs, _INK_LIGHT, wt, cv2.LINE_AA)


def _ruler(img, m, length, unit, x0, x1, y1):
    """The cartographer's bar: alternating segments, so the eye can halve
    and quarter the length without a ruler."""
    thick = max(3, int(round(5.5 * unit)))
    text = label(m)
    fs, wt = 0.72 * unit, max(1, int(round(1.3 * unit)))
    (tw, th), _ = cv2.getTextSize(text, _FONT, fs, wt)
    org = (x1 - tw, y1 - thick - int(9 * unit))

    def mark(canvas, col):
        cv2.rectangle(canvas, (x0, y1 - thick), (x1, y1), col, -1)
        cv2.putText(canvas, text, org, _FONT, fs, col, wt, cv2.LINE_AA)

    _shadow(img, mark, int(8 * unit), 0.55)
    for i in range(4):
        a = x0 + round(length * i / 4)
        b = x0 + round(length * (i + 1) / 4)
        cv2.rectangle(img, (a, y1 - thick), (b, y1),
                      _INK_LIGHT if i % 2 == 0 else _INK_DARK, -1)
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), _INK_LIGHT,
                  max(1, int(round(0.8 * unit))))
    cv2.putText(img, text, org, _FONT, fs, _INK_LIGHT, wt, cv2.LINE_AA)


def _caps(img, m, length, unit, x0, x1, y1):
    """A draughtsman's dimension line: hairline rule with end serifs. The
    lightest touch here, and the one that reads as a measurement rather
    than a label."""
    line = max(1, int(round(2.2 * unit)))
    cap = int(round(9 * unit))
    text = label(m)
    fs, wt = 0.78 * unit, max(1, int(round(1.4 * unit)))
    (tw, th), _ = cv2.getTextSize(text, _FONT, fs, wt)
    org = (x0 + (length - tw) // 2, y1 - cap - int(8 * unit))

    def mark(canvas, col):
        cv2.line(canvas, (x0, y1), (x1, y1), col, line, cv2.LINE_AA)
        cv2.line(canvas, (x0, y1 - cap), (x0, y1 + cap), col, line, cv2.LINE_AA)
        cv2.line(canvas, (x1, y1 - cap), (x1, y1 + cap), col, line, cv2.LINE_AA)
        cv2.putText(canvas, text, org, _FONT, fs, col, wt, cv2.LINE_AA)

    _shadow(img, mark, int(8 * unit))
    mark(img, _INK_LIGHT)


def _shadow_style(img, m, length, unit, x0, x1, y1):
    """White rule and white type over a soft shadow, no box. Best on a
    dark or busy field, where white with a shadow beats any tablet."""
    thick = max(2, int(round(4 * unit)))
    text = label(m)
    fs, wt = 0.85 * unit, max(1, int(round(1.6 * unit)))
    (tw, th), _ = cv2.getTextSize(text, _FONT, fs, wt)
    org = (x0 + (length - tw) // 2, y1 - thick - int(10 * unit))

    def mark(canvas, col):
        cv2.rectangle(canvas, (x0, y1 - thick), (x1, y1), col, -1)
        cv2.putText(canvas, text, org, _FONT, fs, col, wt, cv2.LINE_AA)

    _shadow(img, mark, int(9 * unit))
    mark(img, _INK_LIGHT)


_STYLES = {"adaptive": _adaptive, "plate": _plate, "scrim": _scrim,
           "ruler": _ruler, "caps": _caps, "shadow": _shadow_style}
