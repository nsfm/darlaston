"""The presentation window: the live view again, for an audience.

At an event the operator's screen is an instrument panel, and the one
thing a visitor wants from it is the picture. This window shows only
that, so it can be dragged to a second screen and filled, with the few
words an audience actually asks for: how big is that, what magnification
is this, what is it.

Deliberately not a control surface. Nothing here touches the camera or
the files; it is a mirror with a caption, and closing it changes
nothing about the session.
"""
from __future__ import annotations

import math
import time
from functools import lru_cache

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import _
from . import icons, theme
from .widgets import BAD, POINTER_S, UI_METER, ScaleBarOverlay, draw_pointer

#: Centimetres per unit the screen may be measured in. Projector
#: screens are quoted in feet or metres and desktop displays in inches
#: or centimetres, and a measurement is only welcome in the unit the
#: tape in hand actually shows.
CM_PER = {"cm": 1.0, "m": 100.0, "in": 2.54, "ft": 30.48}


def screen_width_cm(settings) -> float:
    """The measured screen width in centimetres, whatever unit it was
    taken in. Zero means unmeasured."""
    return (settings.present_screen_width
            * CM_PER.get(settings.present_screen_unit, 1.0))


@lru_cache(maxsize=32)
def _glyph(name: str, colour: str, px: int) -> QtGui.QPixmap:
    """One of the marks, rasterised for the captions.

    Not through `icons._render`, on purpose: that pins the stroke at
    hairline device width because the interface's marks sit beside
    hairline furniture. These sit beside demibold projection type, so
    the stroke scales with the size like the letters do.
    """
    from PySide6 import QtSvg

    image = QtGui.QImage(px, px, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    renderer = QtSvg.QSvgRenderer(
        QtCore.QByteArray(icons.tinted(name, colour)))
    if renderer.isValid():
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
    return QtGui.QPixmap.fromImage(image)


class PresentView(QtWidgets.QWidget):
    """The mirrored frame, letterboxed or filled, with the overlays
    painted on.

    Fed the same preview array as the operator's view and does its own
    fit, because the two windows are different sizes on different
    screens and a bar or a caption sized for one is wrong on the other.
    Every setter treats equal input as a no-op, so the window may
    restate everything on every frame -- the pattern the scale bar
    overlay established, and the reason restating is affordable.
    """

    #: The caption grid, as a fraction of the picture's width from each
    #: edge -- the same inset rule the scale bar's geometry uses, so the
    #: type and the mark sit on one grid instead of two nearly equal
    #: ones. Two grids a few pixels apart is what "misaligned" looks
    #: like without anyone being able to say why.
    MARGIN = 0.035

    #: Type scale per caption size, and ink opacity per caption
    #: opacity. A tabletop monitor and a hall projector are read from
    #: very different distances, and how far the words may sit into the
    #: picture is taste; both are the operator's call.
    SIZES = {"small": 0.75, "normal": 1.0, "large": 1.4}
    OPACITIES = {"solid": 1.0, "soft": 0.78, "faint": 0.55}

    #: Ink for the captions, as a fraction of full scale either side of
    #: mid, with hysteresis -- the same rule the scale bar uses, for the
    #: same reason: this text sits over fields that are blown white in
    #: brightfield and near black in darkfield, and one colour of ink
    #: disappears into one of them.
    INK_SPLIT = 0.5
    INK_HOLD = 0.12
    #: How often a corner is re-read, in frames. A field does not change
    #: from bright to dark between frames, and the corners are the only
    #: pixels this widget reads rather than blits.
    INK_EVERY = 15

    #: How long the pointer may sit still over the picture before it is
    #: hidden. An arrow parked on the specimen is the one piece of the
    #: operator's screen that always sneaks onto the projector.
    CURSOR_MS = 2000

    def __init__(self) -> None:
        super().__init__()
        self._image: QtGui.QImage | None = None
        self._buf: np.ndarray | None = None
        self._src: np.ndarray | None = None
        self._src_width = 0
        self._image_at: QtCore.QRect | None = None
        self._frame_count = 0
        #: corner -> (frame it was read at, bright or not).
        self._ink: dict[str, tuple[int, bool]] = {}
        self._bar = ScaleBarOverlay(self)
        self._bar_style: dict | None = None
        self._um: float | None = None
        self._fill = False
        self._caption_size = "normal"
        self._caption_opacity = "solid"
        #: Millimetres one displayed pixel covers on the audience's
        #: screen, when the operator has measured it. None means unknown
        #: and nothing is claimed.
        self._screen_mm_per_px: float | None = None
        self._header = ("", "")
        self._subject = ("", "")
        self._magnification = ""
        self._live = False
        #: Whether frames are actually arriving. The window turns this
        #: off when the feed goes quiet, because a live marker over a
        #: frozen picture is the exact lie the marker exists to prevent.
        self._live_lit = True
        self._held = False
        #: The presenter's ring: (fraction of the source frame each way,
        #: when it was placed), or None. And the crop the frame is shown
        #: through, so the ring lands on the same piece of glass however
        #: the window is being filled.
        self._pointer: tuple[tuple[float, float], float] | None = None
        self._crop = (0, 0, 1, 1)
        #: Repaints the ring while it blooms. Frames usually do this for
        #: free; the timer is for a held or quiet picture, where pointing
        #: is half the reason to have held it.
        self._pulse = QtCore.QTimer(self)
        self._pulse.setInterval(33)
        self._pulse.timeout.connect(self._pulse_tick)
        self._cursor = QtCore.QTimer(self)
        self._cursor.setSingleShot(True)
        self._cursor.setInterval(self.CURSOR_MS)
        self._cursor.timeout.connect(
            lambda: self.setCursor(QtCore.Qt.CursorShape.BlankCursor))
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    # ---- what the window restates every frame ---------------------------

    def set_frame(self, bgr: np.ndarray) -> None:
        """Mirror one preview frame. One resize, linear both ways: on the
        way down this is the `fast` trade the operator's view defaults
        to, and on the way up -- a projector is usually larger than the
        preview -- linear is the only sensible magnifier anyway."""
        self._src = bgr
        self._frame_count += 1
        self._refit()

    def _refit(self) -> None:
        """Lay the kept frame into the current widget, cropped to fill
        or letterboxed to fit. Re-run on resize as well as on arrival,
        because a held picture still has to follow its window onto the
        projector."""
        src = self._src
        if src is None:
            return
        h, w = src.shape[:2]
        if self._fill:
            # The window's shape wins and the frame gives up its edges,
            # centred. What is shown is a crop, so the width the scale
            # bar converts against is the crop's, or the bar would be
            # honest about a frame nobody is seeing all of.
            target = self.rect()
            tw, th = max(1, target.width()), max(1, target.height())
            scale = max(tw / w, th / h)
            cw = max(1, min(w, int(round(tw / scale))))
            ch = max(1, min(h, int(round(th / scale))))
            x0, y0 = (w - cw) // 2, (h - ch) // 2
            src = src[y0:y0 + ch, x0:x0 + cw]
            self._src_width = cw
            self._crop = (x0, y0, cw, ch)
        else:
            target = self._fit(QtCore.QSize(w, h))
            tw, th = max(1, target.width()), max(1, target.height())
            self._src_width = w
            self._crop = (0, 0, w, h)
        if (tw, th) != src.shape[:2][::-1]:
            src = cv2.resize(src, (tw, th), interpolation=cv2.INTER_LINEAR)
        # QImage does not copy, so the buffer lives on the instance.
        self._buf = np.ascontiguousarray(src)
        self._image = QtGui.QImage(self._buf.data, tw, th,
                                   self._buf.strides[0],
                                   QtGui.QImage.Format.Format_BGR888)
        self._image_at = target
        self._bar.set_scale(self._um if self._bar_style else None,
                            self._bar_style)
        self._bar.place(target, self._src_width, self._corner_light)
        self.update()

    def set_scale(self, um_per_px) -> None:
        """Micrometres per pixel of the frame as fed, bar or no bar: the
        screen magnification reads it too. Frozen while held, like every
        setter that describes the slide rather than the screen -- a held
        picture keeps the words that were true of it."""
        if self._held:
            return
        um = float(um_per_px) if um_per_px else None
        if um != self._um:
            self._um = um
            self.update()

    def set_bar(self, style: dict | None) -> None:
        self._bar_style = dict(style) if style else None
        self._bar.set_scale(self._um if self._bar_style else None,
                            self._bar_style)

    def set_fill(self, on: bool) -> None:
        if bool(on) != self._fill:
            self._fill = bool(on)
            self._refit()

    def set_caption_size(self, size: str) -> None:
        if size != self._caption_size and size in self.SIZES:
            self._caption_size = size
            self.update()

    def set_caption_opacity(self, opacity: str) -> None:
        if opacity != self._caption_opacity and opacity in self.OPACITIES:
            self._caption_opacity = opacity
            self.update()

    def set_screen_scale(self, mm_per_px) -> None:
        mm = float(mm_per_px) if mm_per_px else None
        if mm != self._screen_mm_per_px:
            self._screen_mm_per_px = mm
            self.update()

    def set_header(self, title: str, subtitle: str) -> None:
        if self._held:
            return
        if (title, subtitle) != self._header:
            self._header = (title, subtitle)
            self.update()

    def set_subject(self, subject: str, note: str) -> None:
        # Frozen while held: the operator retypes the subject boxes for
        # the next slide during exactly this window, and the audience
        # must not watch the old picture get relabelled letter by letter.
        if self._held:
            return
        if (subject, note) != self._subject:
            self._subject = (subject, note)
            self.update()

    def set_magnification(self, optical: str) -> None:
        if self._held:
            return
        if optical != self._magnification:
            self._magnification = optical
            self.update()

    def set_live(self, on: bool) -> None:
        if bool(on) != self._live:
            self._live = bool(on)
            self.update()

    def set_live_lit(self, lit: bool) -> None:
        if bool(lit) != self._live_lit:
            self._live_lit = bool(lit)
            self.update()

    def set_held(self, held: bool) -> None:
        if bool(held) != self._held:
            self._held = bool(held)
            self.update()

    def set_pointer(self, fx: float, fy: float) -> None:
        """The presenter's ring, at a fraction of the source frame each
        way. Deliberately not gated by hold: pointing at a held picture
        is half the reason it was held."""
        self._pointer = ((float(fx), float(fy)), time.monotonic())
        self._pulse.start()
        self.update()

    def _pulse_tick(self) -> None:
        if self._pointer is None:
            self._pulse.stop()
        self.update()

    # ---- painting --------------------------------------------------------

    def paintEvent(self, _event) -> None:
        with QtGui.QPainter(self) as p:
            p.fillRect(self.rect(), QtGui.QColor("#101210"))
            if self._image is None:
                # Quiet, not apologetic. This face is for an audience,
                # and "no signal" shouted at a hallway helps nobody.
                return
            target = self._image_at or self._fit(self._image.size())
            p.drawImage(target.topLeft(), self._image)
            p.save()
            p.setClipRect(target)
            self._draw_overlays(p, target)
            self._draw_pointer(p, target)
            p.restore()

    def _draw_pointer(self, p: QtGui.QPainter, target: QtCore.QRect) -> None:
        """The ring, mapped through whatever crop the frame is shown
        with, so it lands on the same piece of glass the operator
        pointed at -- or nowhere, if that piece was cropped away."""
        if self._pointer is None or self._src is None:
            return
        (fx, fy), placed = self._pointer
        h, w = self._src.shape[:2]
        x0, y0, cw, ch = self._crop
        tx = (fx * w - x0) / max(cw, 1)
        ty = (fy * h - y0) / max(ch, 1)
        if not (0.0 <= tx <= 1.0 and 0.0 <= ty <= 1.0):
            self._pointer = None
            return
        if not draw_pointer(p, target, (tx, ty),
                            time.monotonic() - placed):
            self._pointer = None

    def _draw_overlays(self, p: QtGui.QPainter, target: QtCore.QRect) -> None:
        """The four corners, sized to the picture rather than the window,
        so filling the projector scales the words with the image."""
        th = target.height()
        margin = max(8, int(round(self.MARGIN * target.width())))
        scale = self.SIZES[self._caption_size]
        fams = theme.load_fonts()

        def font(family: str, px: int, weight=None) -> QtGui.QFont:
            f = QtGui.QFont(family)
            f.setPixelSize(max(9, int(px * scale)))
            if weight is not None:
                f.setWeight(weight)
            return f

        title_px = max(13, int(th * 0.045))
        sub_px = max(11, int(th * 0.030))
        mag_px = max(14, int(th * 0.050))
        demi = QtGui.QFont.Weight.DemiBold

        # The mark's tight box, so a caption sharing its corner aligns
        # to the rule itself rather than to the tile's padding.
        mark = self._bar.mark_rect()
        corner = (self._bar_style or {}).get("corner", "br")

        title, subtitle = self._header
        self._block(p, target, "tl", margin,
                    [(title, font(fams["sans"], title_px, demi), 0.92),
                     (subtitle, font(fams["sans"], sub_px), 0.70)],
                    mark if corner == "tl" else None)
        subject, note = self._subject
        self._block(p, target, "bl", margin,
                    [(subject, font(fams["sans"], title_px, demi), 0.92),
                     (note, font(fams["sans"], sub_px), 0.70)],
                    mark if corner == "bl" else None)
        # The magnification is set in the mono face on purpose: it is a
        # reading, like the scale bar's label, not a caption.
        self._magnification_line(p, target, margin,
                                 font(fams["mono"], mag_px, demi),
                                 mark if corner == "br" else None)
        if self._live and self._live_lit and not self._held:
            self._marker(p, target, margin,
                         font(fams["sans"], sub_px, demi),
                         mark if corner == "tr" else None)

    def _mag_segments(self, target: QtCore.QRect) -> list[tuple[str, str]]:
        """(mark, reading) pairs for the magnification line.

        The microscope's own figure first, and beside it -- once the
        audience's screen has been measured -- how large things really
        appear *there*: the honest answer to "what magnification is
        this", and the number that changes when the window is resized,
        which is the explanation working by itself. Rounded to two
        figures: a screen measured with a tape does not support 1,937x
        and printing it would claim it does.
        """
        if not self._magnification:
            return []
        segments = [("microscope", self._magnification)]
        mm = self._screen_mm_per_px
        if mm and self._um and self._src_width and target.width() >= 8:
            per_target = self._um * (self._src_width / float(target.width()))
            mag = mm * 1000.0 / per_target
            if mag >= 10:
                step = 10 ** max(0, int(math.floor(math.log10(mag))) - 1)
                shown = f"{int(round(mag / step) * step):,}×"
            else:
                shown = f"{mag:.1f}×"
            segments.append(("display", shown))
        return segments

    def _magnification_line(self, p: QtGui.QPainter, target: QtCore.QRect,
                            margin: int, f: QtGui.QFont,
                            mark: QtCore.QRect | None) -> None:
        """One line, each reading behind its mark: the microscope's
        figure, then the measured screen's. The marks carry what "on
        this screen" was spelling out, in no language at all."""
        segments = self._mag_segments(target)
        if not segments:
            return
        m = QtGui.QFontMetrics(f)
        side = m.ascent()
        pad = max(3, side // 4)          # mark to its own reading
        space = max(8, side // 2)        # between the two readings
        width = sum(side + pad + m.horizontalAdvance(t)
                    for _n, t in segments) + space * (len(segments) - 1)
        edge = (mark.right() + 1) if mark is not None \
            else target.right() + 1 - margin
        h = m.height()
        y = target.bottom() + 1 - margin - h
        if mark is not None:
            y = min(y, mark.top() - max(2, h // 6) - h)
        light = self._corner_light("br")
        base = QtGui.QColor("#101210") if light else QtGui.QColor("#e4e7e0")
        dim = self.OPACITIES[self._caption_opacity]
        ink = QtGui.QColor(base)
        ink.setAlphaF(0.92 * dim)
        p.setFont(f)
        x = edge - width
        # The marks centre on the digits' own height, not on the line
        # box: a glyph sat on the baseline reads as hanging below type
        # whose visual middle is half the cap height up.
        glyph_top = y + m.ascent() - (m.capHeight() + side) / 2.0
        for name, text in segments:
            glyph = _glyph(name, base.name(), side)
            p.save()
            p.setOpacity(0.92 * dim)
            p.drawPixmap(int(x), int(round(glyph_top)), glyph)
            p.restore()
            x += side + pad
            p.setPen(ink)
            p.drawText(QtCore.QPointF(x, y + m.ascent()), text)
            x += m.horizontalAdvance(text) + space

    def _block(self, p: QtGui.QPainter, target: QtCore.QRect, corner: str,
               margin: int, lines, mark: QtCore.QRect | None) -> None:
        """A stack of left or right aligned lines in one corner, in
        whichever ink the field under it can carry. Handed the bar's
        mark when they share the corner: the type goes flush against
        the rule's own edge and clear of its height."""
        lines = [(t, f, a) for t, f, a in lines if t]
        if not lines:
            return
        metrics = [QtGui.QFontMetrics(f) for _t, f, _a in lines]
        heights = [m.height() for m in metrics]
        gap = max(2, heights[0] // 6)
        total = sum(heights) + gap * (len(lines) - 1)
        right = corner.endswith("r")
        edge = ((mark.right() + 1 if right else mark.left())
                if mark is not None
                else (target.right() + 1 - margin if right
                      else target.left() + margin))
        if corner.startswith("t"):
            y = target.top() + margin
            if mark is not None:
                y = max(y, mark.bottom() + gap)
        else:
            y = target.bottom() + 1 - margin - total
            if mark is not None:
                y = min(y, mark.top() - gap - total)
        dim = self.OPACITIES[self._caption_opacity]
        base = (QtGui.QColor("#101210") if self._corner_light(corner)
                else QtGui.QColor("#e4e7e0"))
        for (text, f, alpha), m, h in zip(lines, metrics, heights):
            ink = QtGui.QColor(base)
            ink.setAlphaF(alpha * dim)
            x = edge - m.horizontalAdvance(text) if right else edge
            p.setFont(f)
            p.setPen(ink)
            p.drawText(QtCore.QPointF(x, y + m.ascent()), text)
            y += h + gap

    def _marker(self, p: QtGui.QPainter, target: QtCore.QRect,
                margin: int, f: QtGui.QFont,
                mark: QtCore.QRect | None) -> None:
        """The top right word: a breathing dot and "live". The breath is
        the point -- a static badge could be part of a recording, and
        the slow pulse is the one thing a loop of frames cannot fake
        cheaply. While held the corner simply goes quiet: the claim
        comes down, and a deliberate freeze needs no apology."""
        m = QtGui.QFontMetrics(f)
        text = _("present.live")
        tw = m.horizontalAdvance(text)
        gap = max(4, int(m.ascent() * 0.3))
        y = target.top() + margin
        if mark is not None:
            y = max(y, mark.bottom() + gap)
        x = target.right() + 1 - margin - tw
        ink = (QtGui.QColor("#101210") if self._corner_light("tr")
               else QtGui.QColor("#e4e7e0"))
        ink.setAlphaF(0.92 * self.OPACITIES[self._caption_opacity])
        p.setFont(f)
        p.setPen(ink)
        p.drawText(QtCore.QPointF(x, y + m.ascent()), text)
        dot = QtGui.QColor(BAD)
        # The breath never takes the dot below legible: at the bottom of
        # a 0.2 floor it disappeared into a brightfield ground entirely,
        # which reads as the marker going out -- and going out means
        # something here.
        dot.setAlphaF(0.7 + 0.3 * math.sin(time.monotonic() * math.pi))
        d = max(6, int(m.ascent() * 0.6))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        # Centred on the lowercase body of the word beside it, which is
        # where the eye reads the line's middle -- the ascender of the
        # "l" does not move the optical centre.
        cy = y + m.ascent() - m.xHeight() * 0.5
        p.drawEllipse(QtCore.QPointF(x - gap - d, cy), d * 0.5, d * 0.5)

    # ---- geometry and ink ------------------------------------------------

    def _fit(self, size: QtCore.QSize) -> QtCore.QRect:
        scaled = size.scaled(self.size(),
                             QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QtCore.QRect(x, y, scaled.width(), scaled.height())

    def resizeEvent(self, event) -> None:
        # A held or momentarily quiet picture still follows its window:
        # without this, dragging to the projector or going fullscreen
        # would leave the old letterbox until the next frame -- which,
        # held, never comes.
        super().resizeEvent(event)
        if self._src is not None:
            self._refit()

    def mouseMoveEvent(self, event) -> None:
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self._cursor.start()
        super().mouseMoveEvent(event)

    def hideEvent(self, event) -> None:
        self._cursor.stop()
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        super().hideEvent(event)

    def _corner_light(self, corner: str) -> bool:
        """Is this corner of the frame bright? Read from the reduced
        frame already on hand, held with hysteresis so a half lit field
        does not flicker the ink, and only re-read now and then."""
        buf = self._buf
        if buf is None or not buf.size:
            return True
        at, cached = self._ink.get(corner, (0, None))
        if cached is not None and self._frame_count - at < self.INK_EVERY:
            return cached
        h, w = buf.shape[:2]
        ph, pw = max(1, h // 6), max(1, w // 5)
        rows = slice(h - ph, h) if corner.startswith("b") else slice(0, ph)
        cols = slice(w - pw, w) if corner.endswith("r") else slice(0, pw)
        level = float(buf[rows, cols][::8, ::8].mean()) / 255.0
        if cached is None:
            light = level > self.INK_SPLIT
        elif cached:
            light = level > self.INK_SPLIT - self.INK_HOLD
        else:
            light = level > self.INK_SPLIT + self.INK_HOLD
        self._ink[corner] = (self._frame_count, light)
        return light


class PresentWindow(QtWidgets.QWidget):
    """The top level window the view lives in.

    Plain system frame, because this window's job is to be dragged to
    another screen and then filled -- double click fills it, Escape
    steps back out, and the frame is only ever seen in between. Space
    holds the picture while a slide is swapped, so the audience sees
    the last good field rather than the blur of the change.
    """

    closed = QtCore.Signal()
    held_changed = QtCore.Signal(bool)

    #: How long the feed may go quiet before the live marker goes out.
    STALE_S = 2.0

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_("present.title"))
        self.view = PresentView()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self._last_frame = 0.0
        self._held = False
        #: Watches for the feed stopping. Only the marker needs this: the
        #: picture freezing is visible on its own, but a frozen frame
        #: under a lit "live" would be the marker lying.
        self._stale = QtCore.QTimer(self)
        self._stale.setInterval(1000)
        self._stale.timeout.connect(self._check_stale)
        self.resize(960, 640)
        theme.match_frame(self)

    def set_frame(self, bgr: np.ndarray) -> None:
        start = time.perf_counter()
        try:
            self._last_frame = time.monotonic()
            self.view.set_live_lit(True)
            if not self._held:
                self.view.set_frame(bgr)
        finally:
            UI_METER.since("presentation", start)

    @property
    def held(self) -> bool:
        return self._held

    def set_held(self, held: bool) -> None:
        held = bool(held)
        if held == self._held:
            return
        self._held = held
        self.view.set_held(held)
        self.held_changed.emit(held)

    def _check_stale(self) -> None:
        if time.monotonic() - self._last_frame > self.STALE_S:
            self.view.set_live_lit(False)

    def showEvent(self, event) -> None:
        self._stale.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._stale.stop()
        super().hideEvent(event)

    def mouseDoubleClickEvent(self, _event) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.showNormal() if self.isFullScreen() else self.close()
            return
        if event.key() == QtCore.Qt.Key.Key_Space:
            self.set_held(not self._held)
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        # A hold is a moment, not a mode worth keeping. Reopening later
        # to a mysteriously frozen picture would read as a fault.
        self.set_held(False)
        self.closed.emit()
        super().closeEvent(event)


class HeaderDialog(QtWidgets.QDialog):
    """The two free lines, and nothing else to configure about them.

    Writes the settings itself on accept, and turns the header on when
    there is text and off when there is none -- typing a name and then
    hunting for the switch that shows it is a step nobody needs.
    """

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(_("present.header.title"))
        explain = QtWidgets.QLabel(_("present.header.explain"))
        explain.setWordWrap(True)
        self.first = QtWidgets.QLineEdit(settings.present_header_title)
        self.first.setPlaceholderText(_("present.header.first"))
        self.second = QtWidgets.QLineEdit(settings.present_header_subtitle)
        self.second.setPlaceholderText(_("present.header.second"))
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(explain)
        col.addWidget(self.first)
        col.addWidget(self.second)
        col.addWidget(buttons)
        self.setMinimumWidth(420)

    def accept(self) -> None:
        s = self._settings
        s.present_header_title = self.first.text().strip()
        s.present_header_subtitle = self.second.text().strip()
        s.present_header = bool(s.present_header_title
                                or s.present_header_subtitle)
        s.save()
        super().accept()


class ScreenDialog(QtWidgets.QDialog):
    """How wide the audience's picture really is.

    One measured number, in whatever unit the tape in hand shows --
    projector screens are quoted in feet or metres and desktop displays
    in inches or centimetres, and asking somebody to convert is asking
    for a wrong magnification. It buys the one figure an audience can
    actually stand next to: how large things appear on *that* screen.
    Empty means unmeasured, and unmeasured claims nothing.
    """

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(_("present.screen.title"))
        explain = QtWidgets.QLabel(_("present.screen.explain"))
        explain.setWordWrap(True)
        self.width_field = QtWidgets.QLineEdit(
            f"{settings.present_screen_width:g}"
            if settings.present_screen_width else "")
        self.width_field.setPlaceholderText(_("present.screen.width"))
        self.unit = QtWidgets.QComboBox()
        for value, label in (("cm", _("present.screen.unit.cm")),
                             ("in", _("present.screen.unit.in")),
                             ("m", _("present.screen.unit.m")),
                             ("ft", _("present.screen.unit.ft"))):
            self.unit.addItem(label, value)
        at = self.unit.findData(settings.present_screen_unit)
        if at >= 0:
            self.unit.setCurrentIndex(at)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.width_field, 1)
        row.addWidget(self.unit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(explain)
        col.addLayout(row)
        col.addWidget(buttons)
        self.setMinimumWidth(420)

    def accept(self) -> None:
        try:
            width = float(self.width_field.text().replace(",", "."))
        except ValueError:
            width = 0.0
        self._settings.present_screen_width = max(0.0, width)
        self._settings.present_screen_unit = self.unit.currentData()
        self._settings.save()
        super().accept()
