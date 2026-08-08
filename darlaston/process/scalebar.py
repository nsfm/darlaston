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

import functools

import cv2
import numpy as np

#: Faces offered, bundled ones first. The values are family names as Qt
#: knows them once `theme.load_fonts` has registered the bundled files.
#: "hershey" is the stroke font OpenCV draws itself, kept as the fallback
#: for headless use rather than as a choice anybody should want: it is a
#: 1960s pen-plotter face, it has a slashed zero, and it has no micro sign.
FACES = ("IBM Plex Mono", "IBM Plex Sans", "sans-serif", "serif", "hershey")
DEFAULT_FACE = "IBM Plex Mono"


def _qt_available() -> bool:
    """Is there a Qt application to rasterise with?

    Capture runs inside the app, so ordinarily yes. `plate` can be run
    from a shell over a folder of DNGs, and there it is no, which is the
    whole reason the Hershey path stays.
    """
    try:
        from PySide6 import QtWidgets
    except Exception:
        return False
    return QtWidgets.QApplication.instance() is not None


def _hershey_metrics(text, px, weight):
    scale = px / 30.0
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale,
                                     weight)
    return tw, th + base


def measure(text: str, face: str, px: int, weight: int = 1):
    """(width, height) of `text` at `px`, in the given face."""
    if face == "hershey" or not _qt_available():
        return _hershey_metrics(text, px, weight)
    from PySide6 import QtGui
    font = QtGui.QFont(face)
    font.setPixelSize(max(1, int(px)))
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    m = QtGui.QFontMetrics(font)
    rect = m.tightBoundingRect(text)
    return rect.width(), rect.height()


def stamp(image, text: str, face: str, px: int, origin, colour,
          weight: int = 1) -> None:
    """Draw `text` with its bottom-left at `origin`, in place.

    Rendered into a small transparent tile and composited, rather than by
    converting the whole frame to a QImage and back. A 20 MP capture is 60
    MB a copy and this is a line of type; paying for the frame twice to
    place it would show up in the write queue.
    """
    if face == "hershey" or not _qt_available():
        cv2.putText(image, text, (int(origin[0]), int(origin[1])),
                    cv2.FONT_HERSHEY_DUPLEX, px / 30.0, colour, weight,
                    cv2.LINE_AA)
        return
    if image.ndim == 2 and isinstance(colour, (tuple, list)):
        colour = (colour[0], colour[0], colour[0])
    from PySide6 import QtCore, QtGui

    tw, th = measure(text, face, px, weight)
    pad = max(2, px // 4)
    w, h = tw + pad * 2, th + pad * 2
    tile = QtGui.QImage(w, h, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    tile.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(tile)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
    font = QtGui.QFont(face)
    font.setPixelSize(max(1, int(px)))
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    painter.setFont(font)
    # `colour` is BGR, as everything in this file is; QColor takes RGB.
    painter.setPen(QtGui.QColor(int(colour[2]), int(colour[1]),
                                int(colour[0])))
    painter.drawText(QtCore.QRect(0, 0, w, h),
                     int(QtCore.Qt.AlignmentFlag.AlignCenter), text)
    painter.end()

    # ARGB32 is one uint32 per pixel, so on a little-endian machine the
    # bytes run B, G, R, A. Measured rather than reasoned about: that is
    # already BGR, and swapping it here would have turned the type blue.
    buf = np.frombuffer(tile.constBits(), np.uint8).reshape(h, w, 4)
    alpha = buf[..., 3:4].astype(np.float32) / 255.0
    rgb = buf[..., :3].astype(np.float32)
    # Premultiplied, so the colour is already scaled by alpha.
    x0, y0 = int(origin[0]) - pad, int(origin[1]) - th - pad
    x1, y1 = x0 + w, y0 + h
    ih, iw = image.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > iw or y1 > ih:
        return
    if image.ndim == 2:
        # The shadow pass draws into a single-channel mask to be blurred,
        # so the ink value is a scalar and there are no channels to
        # composite. Coverage is the whole story there.
        patch = image[y0:y1, x0:x1].astype(np.float32)
        ink = float(colour[0] if isinstance(colour, (tuple, list)) else colour)
        image[y0:y1, x0:x1] = np.maximum(
            patch, alpha[..., 0] * ink).astype(np.uint8)
        return
    patch = image[y0:y1, x0:x1].astype(np.float32)
    image[y0:y1, x0:x1] = (patch * (1.0 - alpha) + rgb).astype(np.uint8)

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

#: Which corner. Bottom right by default, which is where a photographer
#: expects a caption and where the subject is least often placed.
CORNERS = ("br", "bl", "tr", "tl")
DEFAULT_CORNER = "br"

#: How large the furniture is, as a multiplier on the frame-derived unit.
#: Three steps rather than a percentage: everything in this dialog is
#: taste, and a number with no right answer only invites fiddling.
SIZES = {"small": 0.72, "medium": 1.0, "large": 1.45}
DEFAULT_SIZE = "medium"

#: Which side of the rule the label sits on. "auto" puts it on the inside:
#: below the rule in a top corner, above it in a bottom one, so the type
#: is always the part further from the frame edge. Taste, so it is a
#: choice, with the sensible answer as the default rather than the only
#: answer.
LABELS = ("auto", "above", "below")
DEFAULT_LABEL = "auto"

#: How solid the bar is. One over the whole mark, furniture and type
#: together, so a half-opaque bar is half-opaque everywhere rather than
#: dissolving into a legible label on a ghostly rule.
MIN_OPACITY = 0.15


def _org(x, y_rule, clearance, gap, th, below):
    """Where the label's bottom-left corner goes."""
    return (x, (y_rule + clearance + gap + th) if below
            else (y_rule - clearance - gap))

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


def label(micrometres: int, plain: bool = False) -> str:
    """Micrometres, or millimetres once that stops being sensible.

    `plain` writes "um" instead of the micro sign. It used to be the only
    option, because the Hershey stroke font has no glyph for the sign and
    would have drawn a hollow box on the one element of the picture that
    makes a claim. With a real rasteriser the sign is available, so the
    correct character is the default and the ASCII spelling is the escape
    hatch for anyone whose downstream tooling cannot take it.
    """
    if micrometres >= 1000 and micrometres % 1000 == 0:
        return f"{micrometres // 1000} mm"
    return f"{micrometres} um" if plain else f"{micrometres} \u00b5m"


#: How the bar is dressed. The measurement is identical in all of them;
#: only the furniture differs, and furniture is taste.
STYLES = ("adaptive", "plate", "scrim", "ruler", "caps", "shadow")
DEFAULT_STYLE = "adaptive"

_FONT = cv2.FONT_HERSHEY_DUPLEX
_INK_DARK = (16, 16, 18)
_INK_LIGHT = (250, 250, 250)


def _geometry(image, um_per_px, margin, corner=DEFAULT_CORNER,
              size=DEFAULT_SIZE, label_at=DEFAULT_LABEL):
    """Everything the styles share: where the bar goes and how big.

    `unit` scales the furniture off the frame, so a bar looks the same on
    a 20 MP capture and on a plate cell a fifth the size. A fixed pixel
    thickness is a hairline on one and a slab on the other. `size` then
    scales that again, by taste.

    `size` deliberately does not change the bar's *length*. The length
    comes from the 1-2-5 ladder, so letting size move it would mean
    "Large" quietly reporting 500 um where "Medium" reported 200 -- a
    presentation control silently changing the measurement. Large means a
    bigger label on the same honest number.

    Which side of the rule the label sits on is `label_at`, and "auto"
    puts it on the inside: below in a top corner, above in a bottom one,
    so the type is always the part further from the edge of the frame.
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
    unit = max(0.6, w / 900.0) * SIZES.get(size, SIZES[DEFAULT_SIZE])
    inset = int(round(margin * w))

    # Room for the label, which sits above the rule in every corner. At
    # the top that room has to come out of the inset rather than out of
    # the picture, or the type runs off the edge.
    headroom = int(round(46 * unit))
    left = corner in ("bl", "tl")
    top = corner in ("tl", "tr")
    below = top if label_at == "auto" else (label_at == "below")

    x0 = inset if left else w - inset - length
    x1 = x0 + length
    # The rule goes at the inset, and whichever side the label is on gets
    # the headroom. At the top with the label above, that room has to come
    # out of the inset or the type runs off the edge.
    if top:
        y1 = inset if below else inset + headroom
    else:
        y1 = h - inset - headroom if below else h - inset

    if (x0 < 0 or x1 > w
            or y1 - (0 if below else headroom) < 0
            or y1 + (headroom if below else 0) > h):
        return None                 # it does not fit; a clipped bar is worse
    return micrometres, length, unit, x0, x1, y1, below


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
         style: str = DEFAULT_STYLE, face: str = DEFAULT_FACE,
         corner: str = DEFAULT_CORNER, size: str = DEFAULT_SIZE,
         label_at: str = DEFAULT_LABEL, plain_units: bool = False,
         opacity: float = 1.0, margin: float = 0.035,
         ink: tuple[int, int, int] | None = None) -> bool:
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
    geom = _geometry(image, um_per_px, margin,
                     corner if corner in CORNERS else DEFAULT_CORNER,
                     size if size in SIZES else DEFAULT_SIZE,
                     label_at if label_at in LABELS else DEFAULT_LABEL)
    if geom is None:
        return False
    fn = _STYLES.get(style) or _STYLES[DEFAULT_STYLE]
    if ink is not None and fn is _adaptive:
        # Only `adaptive` has an opinion to override: it is the one style
        # that reads what it is about to cover. See `tile`, which needs
        # that decision made once and held, rather than measured per
        # render off a canvas it chose for other reasons.
        fn = functools.partial(_adaptive, ink=tuple(int(c) for c in ink))
    face = face if face in FACES else DEFAULT_FACE
    alpha = min(1.0, max(MIN_OPACITY, float(opacity)))

    if alpha >= 0.999:
        fn(image, *geom, face, plain_units)
        return True

    # Draw at full strength into the region the mark occupies, then blend
    # that region back. Blending the whole frame would be two copies of a
    # 20 MP capture for a mark a few hundred pixels across, and this runs
    # on every live frame when the overlay is on.
    _micrometres, length, unit, x0, x1, y1, _below = geom
    pad = int(round(60 * unit))
    h, w = image.shape[:2]
    ry0, ry1 = max(0, y1 - pad), min(h, y1 + pad)
    rx0, rx1 = max(0, x0 - pad), min(w, x1 + pad)
    before = image[ry0:ry1, rx0:rx1].copy()
    fn(image, *geom, face, plain_units)
    after = image[ry0:ry1, rx0:rx1]
    image[ry0:ry1, rx0:rx1] = (
        before.astype(np.float32) * (1.0 - alpha)
        + after.astype(np.float32) * alpha).astype(np.uint8)
    return True


# ---- the styles ------------------------------------------------------------

def _adaptive(img, m, length, unit, x0, x1, y1, below, face, plain,
              ink=None):
    """No furniture at all: read the corner and pick ink that contrasts.

    The default, because it is the only one that adds nothing to the
    photograph but the mark itself. Brightfield gets dark ink, darkfield
    and inverted get light, and the decision is measured off the pixels
    the bar is about to cover rather than assumed from the illumination
    setting -- which is right about the lamp and silent about the subject.
    """
    thick = max(2, int(round(4 * unit)))
    text = label(m, plain)
    px, wt = int(round(0.85 * 30 * unit)), max(1, int(round(1.6 * unit)))
    tw, th = measure(text, face, px, wt)
    org = _org(x0 + (length - tw) // 2, y1, thick, int(10 * unit), th, below)
    if ink is None:
        lo, hi = sorted((org[1] - th, y1 + 2))
        region = img[max(0, lo):hi, x0:x1]
        ink = _INK_DARK if float(region.mean()) > 120 else _INK_LIGHT
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), ink, -1)
    stamp(img, text, face, px, org, ink, wt)


def _plate(img, m, length, unit, x0, x1, y1, below, face, plain):
    """The journal figure: white tablet, black rule. What we shipped."""
    thick = max(2, int(round(3 * unit)))
    text = label(m, plain)
    px, wt = int(round(0.72 * 30 * unit)), max(1, int(round(1.2 * unit)))
    tw, th = measure(text, face, px, wt)
    pad = int(round(11 * unit))
    ty = _org(0, y1, thick, pad, th, below)[1]
    px0 = x0 - pad
    px1 = x1 + pad
    py0 = min(y1 - thick, ty - th) - pad
    py1 = max(y1, ty) + pad
    h, w = img.shape[:2]
    if px0 < 0 or py0 < 0 or px1 > w or py1 > h:
        return
    cv2.rectangle(img, (px0, py0), (px1, py1), (255, 255, 255), -1)
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), _INK_DARK, -1)
    stamp(img, text, face, px,
          _org(x0 + (length - tw) // 2, y1, thick, pad, th, below),
          _INK_DARK, wt)


def _scrim(img, m, length, unit, x0, x1, y1, below, face, plain):
    """A translucent dark tablet. Legible over anything, at the cost of
    covering a little of the picture."""
    thick = max(2, int(round(3.5 * unit)))
    text = label(m, plain)
    px, wt = int(round(0.78 * 30 * unit)), max(1, int(round(1.4 * unit)))
    tw, th = measure(text, face, px, wt)
    pad = int(round(13 * unit))
    h, w = img.shape[:2]
    ty = _org(0, y1, thick, int(9 * unit), th, below)[1]
    bx0 = max(0, x0 - pad)
    bx1 = min(w, x1 + pad)
    by0 = max(0, min(y1 - thick, ty - th) - pad)
    by1 = min(h, max(y1, ty) + pad)
    patch = img[by0:by1, bx0:bx1].astype(np.float32)
    img[by0:by1, bx0:bx1] = (patch * 0.38 + 18 * 0.62).astype(np.uint8)
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), _INK_LIGHT, -1)
    stamp(img, text, face, px,
          _org(x0 + (length - tw) // 2, y1, thick, int(9 * unit), th, below),
          _INK_LIGHT, wt)


def _ruler(img, m, length, unit, x0, x1, y1, below, face, plain):
    """The cartographer's bar: alternating segments, so the eye can halve
    and quarter the length without a ruler."""
    thick = max(3, int(round(5.5 * unit)))
    text = label(m, plain)
    px, wt = int(round(0.72 * 30 * unit)), max(1, int(round(1.3 * unit)))
    tw, th = measure(text, face, px, wt)
    org = _org(x1 - tw, y1, thick, int(9 * unit), th, below)

    def mark(canvas, col):
        cv2.rectangle(canvas, (x0, y1 - thick), (x1, y1), col, -1)
        stamp(canvas, text, face, px, org, (col, col, col) if isinstance(col, int) else col, wt)

    _shadow(img, mark, int(8 * unit), 0.55)
    for i in range(4):
        a = x0 + round(length * i / 4)
        b = x0 + round(length * (i + 1) / 4)
        cv2.rectangle(img, (a, y1 - thick), (b, y1),
                      _INK_LIGHT if i % 2 == 0 else _INK_DARK, -1)
    cv2.rectangle(img, (x0, y1 - thick), (x1, y1), _INK_LIGHT,
                  max(1, int(round(0.8 * unit))))
    stamp(img, text, face, px, org, _INK_LIGHT, wt)


def _caps(img, m, length, unit, x0, x1, y1, below, face, plain):
    """A draughtsman's dimension line: hairline rule with end serifs. The
    lightest touch here, and the one that reads as a measurement rather
    than a label."""
    line = max(1, int(round(2.2 * unit)))
    cap = int(round(9 * unit))
    text = label(m, plain)
    px, wt = int(round(0.78 * 30 * unit)), max(1, int(round(1.4 * unit)))
    tw, th = measure(text, face, px, wt)
    org = _org(x0 + (length - tw) // 2, y1, cap, int(8 * unit), th, below)

    def mark(canvas, col):
        cv2.line(canvas, (x0, y1), (x1, y1), col, line, cv2.LINE_AA)
        cv2.line(canvas, (x0, y1 - cap), (x0, y1 + cap), col, line, cv2.LINE_AA)
        cv2.line(canvas, (x1, y1 - cap), (x1, y1 + cap), col, line, cv2.LINE_AA)
        stamp(canvas, text, face, px, org, (col, col, col) if isinstance(col, int) else col, wt)

    _shadow(img, mark, int(8 * unit))
    mark(img, _INK_LIGHT)


def _shadow_style(img, m, length, unit, x0, x1, y1, below, face, plain):
    """White rule and white type over a soft shadow, no box. Best on a
    dark or busy field, where white with a shadow beats any tablet."""
    thick = max(2, int(round(4 * unit)))
    text = label(m, plain)
    px, wt = int(round(0.85 * 30 * unit)), max(1, int(round(1.6 * unit)))
    tw, th = measure(text, face, px, wt)
    org = _org(x0 + (length - tw) // 2, y1, thick, int(10 * unit), th, below)

    def mark(canvas, col):
        cv2.rectangle(canvas, (x0, y1 - thick), (x1, y1), col, -1)
        stamp(canvas, text, face, px, org, (col, col, col) if isinstance(col, int) else col, wt)

    _shadow(img, mark, int(9 * unit))
    mark(img, _INK_LIGHT)


_STYLES = {"adaptive": _adaptive, "plate": _plate, "scrim": _scrim,
           "ruler": _ruler, "caps": _caps, "shadow": _shadow_style}



#: Ink for a bar over a light corner, and over a dark one. Named here
#: because `tile` has to make the choice `adaptive` would have made, and
#: it makes it off the real picture rather than off a synthetic canvas.
INK_ON_LIGHT = _INK_DARK
INK_ON_DARK = _INK_LIGHT


def tile(shape: tuple[int, int], um_per_px: float | None, *,
         light_ground: bool = False, **style):
    """Render the bar once, on nothing. Returns (BGRA, x, y) with the
    tile premultiplied and cropped to the mark, or None if there was
    nothing to draw.

    Painting the styled bar into every live frame costs a redraw of the
    mark plus, for the tablet styles, a read-modify-write of the region
    behind it -- per frame, for a picture that changes while the mark does
    not. The mark only depends on the scale, the style and the size, so it
    can be rendered once and blitted.

    Getting it out of the renderer without teaching six styles to report
    their own coverage: draw the same bar over black and over white. A
    pixel the bar never touched differs by the full range; one it painted
    opaquely does not differ at all; and one it painted *through* --
    `scrim`'s tablet, `shadow`'s falloff -- differs by exactly the light
    it let past. That difference is the transparency, so

        alpha = 1 - (over_white - over_black) / 255

    and since compositing over black leaves `alpha * colour`, the black
    render *is* the premultiplied colour, with no division to undo.

    This was tried before and abandoned, because `adaptive` reads the
    corner it is about to sit in: over black it draws light ink and over
    white it draws dark, so the two renders disagree about the mark rather
    than about the ground, and subtracting them is meaningless. The fix is
    to take the decision away from the canvas. The caller knows what is
    actually under the corner; it passes `light_ground`, both renders use
    the same ink, and the subtraction means what it says again.
    """
    h, w = int(shape[0]), int(shape[1])
    if h < 8 or w < 8 or not um_per_px or um_per_px <= 0:
        return None
    style.pop("ink", None)
    ink = INK_ON_LIGHT if light_ground else INK_ON_DARK

    # Where the mark will land, before drawing it. The alternative is to
    # difference the whole canvas and find the mark afterwards, and at
    # Nate's window size the channel split alone is 28 ms of an 18 MP
    # deinterleave to describe a few hundred pixels. This is the same
    # function `draw` places with, so the box cannot disagree with it.
    geom = _geometry(image_like(h, w), um_per_px, style.get("margin", 0.035),
                     style.get("corner", DEFAULT_CORNER)
                     if style.get("corner") in CORNERS else DEFAULT_CORNER,
                     style.get("size", DEFAULT_SIZE)
                     if style.get("size") in SIZES else DEFAULT_SIZE,
                     style.get("label_at", DEFAULT_LABEL)
                     if style.get("label_at") in LABELS else DEFAULT_LABEL)
    if geom is None:
        return None
    _m, _length, unit, gx0, gx1, gy1, _below = geom
    pad = int(round(70 * unit))
    y0, y1 = max(0, gy1 - pad), min(h, gy1 + pad)
    x0, x1 = max(0, gx0 - pad), min(w, gx1 + pad)

    over_black = np.zeros((h, w, 3), np.uint8)
    over_white = np.full((h, w, 3), 255, np.uint8)
    if not draw(over_black, um_per_px, ink=ink, **style):
        return None
    draw(over_white, um_per_px, ink=ink, **style)
    black = over_black[y0:y1, x0:x1]
    white = over_white[y0:y1, x0:x1]

    # A pixel the bar never touched differs by the full range; one it
    # painted opaquely does not differ at all; one it painted *through*
    # differs by exactly the light it let past. Per channel in principle;
    # in practice every style blends uniformly, so the widest
    # disagreement is the honest one and taking the maximum keeps a
    # rounding wobble in one channel from thinning the mark.
    b, g, r = cv2.split(cv2.absdiff(white, black))
    alpha = cv2.subtract(255, cv2.max(cv2.max(b, g), r))

    # The box above is generous, because it is derived from the rule and
    # has to hold whatever furniture the style puts around it. Trim to
    # what was actually lit, so the per-frame blit is the mark and not the
    # margin around it. Cheap here and not on the full canvas: this is a
    # few hundred pixels square.
    cx, cy, cw, ch = cv2.boundingRect(alpha)
    if not cw or not ch:
        return None
    out = np.empty((ch, cw, 4), np.uint8)
    # `black` composited over nothing is already the premultiplied colour.
    out[:, :, :3] = black[cy:cy + ch, cx:cx + cw]
    out[:, :, 3] = alpha[cy:cy + ch, cx:cx + cw]
    return out, x0 + cx, y0 + cy


def image_like(h: int, w: int) -> np.ndarray:
    """A stand-in with the right shape, for asking `_geometry` a question
    without allocating a frame to ask it about."""
    return np.broadcast_to(np.zeros(3, np.uint8), (h, w, 3))
