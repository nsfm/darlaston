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

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import _
from . import theme
from .widgets import BAD, UI_METER, ScaleBarOverlay


class PresentView(QtWidgets.QWidget):
    """The mirrored frame, letterboxed, with the overlays painted on.

    Fed the same preview array as the operator's view and does its own
    fit, because the two windows are different sizes on different
    screens and a bar or a caption sized for one is wrong on the other.
    Every setter treats equal input as a no-op, so the window may
    restate everything on every frame -- the pattern the scale bar
    overlay established, and the reason restating is affordable.
    """

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

    def __init__(self) -> None:
        super().__init__()
        self._image: QtGui.QImage | None = None
        self._buf: np.ndarray | None = None
        self._image_at: QtCore.QRect | None = None
        self._frame_count = 0
        #: corner -> (frame it was read at, bright or not).
        self._ink: dict[str, tuple[int, bool]] = {}
        self._bar = ScaleBarOverlay(self)
        self._bar_corner = "br"
        self._header = ("", "")
        self._subject = ("", "")
        self._magnification = ("", "")
        self._live = False
        #: Whether frames are actually arriving. The window turns this
        #: off when the feed goes quiet, because a live marker over a
        #: frozen picture is the exact lie the marker exists to prevent.
        self._live_lit = True
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    # ---- what the window restates every frame ---------------------------

    def set_frame(self, bgr: np.ndarray) -> None:
        """Mirror one preview frame. One resize, linear both ways: on the
        way down this is the `fast` trade the operator's view defaults
        to, and on the way up -- a projector is usually larger than the
        preview -- linear is the only sensible magnifier anyway."""
        h, w = bgr.shape[:2]
        target = self._fit(QtCore.QSize(w, h))
        tw, th = max(1, target.width()), max(1, target.height())
        if (tw, th) != (w, h):
            bgr = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_LINEAR)
        self._frame_count += 1
        # QImage does not copy, so the buffer lives on the instance.
        self._buf = np.ascontiguousarray(bgr)
        self._image = QtGui.QImage(self._buf.data, tw, th,
                                   self._buf.strides[0],
                                   QtGui.QImage.Format.Format_BGR888)
        self._image_at = target
        self._bar.place(target, w, self._corner_light)
        self.update()

    def set_scale_bar(self, um_per_px, style) -> None:
        self._bar_corner = (style or {}).get("corner", "br")
        self._bar.set_scale(um_per_px, style)

    def set_header(self, title: str, subtitle: str) -> None:
        if (title, subtitle) != self._header:
            self._header = (title, subtitle)
            self.update()

    def set_subject(self, subject: str, note: str) -> None:
        if (subject, note) != self._subject:
            self._subject = (subject, note)
            self.update()

    def set_magnification(self, primary: str, secondary: str) -> None:
        if (primary, secondary) != self._magnification:
            self._magnification = (primary, secondary)
            self.update()

    def set_live(self, on: bool) -> None:
        if bool(on) != self._live:
            self._live = bool(on)
            self.update()

    def set_live_lit(self, lit: bool) -> None:
        if bool(lit) != self._live_lit:
            self._live_lit = bool(lit)
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
            p.restore()

    def _draw_overlays(self, p: QtGui.QPainter, target: QtCore.QRect) -> None:
        """The four corners, sized to the picture rather than the window,
        so filling the projector scales the words with the image."""
        th = target.height()
        margin = max(10, int(th * 0.028))
        fams = theme.load_fonts()

        def font(family: str, px: int, weight=None) -> QtGui.QFont:
            f = QtGui.QFont(family)
            f.setPixelSize(px)
            if weight is not None:
                f.setWeight(weight)
            return f

        title_px = max(13, int(th * 0.045))
        sub_px = max(11, int(th * 0.030))
        mag_px = max(14, int(th * 0.050))
        demi = QtGui.QFont.Weight.DemiBold

        # Where the scale bar is, so a caption sharing its corner steps
        # out of the way rather than through it.
        avoid = self._bar.geometry() if self._bar.isVisible() else None

        title, subtitle = self._header
        self._block(p, target, "tl", margin,
                    [(title, font(fams["sans"], title_px, demi), 0.92),
                     (subtitle, font(fams["sans"], sub_px), 0.70)],
                    avoid if self._bar_corner == "tl" else None)
        subject, note = self._subject
        self._block(p, target, "bl", margin,
                    [(subject, font(fams["sans"], title_px, demi), 0.92),
                     (note, font(fams["sans"], sub_px), 0.70)],
                    avoid if self._bar_corner == "bl" else None)
        # The magnification is set in the mono face on purpose: it is a
        # reading, like the scale bar's label, not a caption.
        primary, secondary = self._magnification
        self._block(p, target, "br", margin,
                    [(primary, font(fams["mono"], mag_px, demi), 0.92),
                     (secondary, font(fams["mono"], sub_px), 0.70)],
                    avoid if self._bar_corner == "br" else None)
        if self._live and self._live_lit:
            self._live_marker(p, target, margin,
                              font(fams["sans"], sub_px, demi),
                              avoid if self._bar_corner == "tr" else None)

    def _block(self, p: QtGui.QPainter, target: QtCore.QRect, corner: str,
               margin: int, lines, avoid: QtCore.QRect | None) -> None:
        """A stack of left or right aligned lines in one corner, in
        whichever ink the field under it can carry."""
        lines = [(t, f, a) for t, f, a in lines if t]
        if not lines:
            return
        metrics = [QtGui.QFontMetrics(f) for _t, f, _a in lines]
        heights = [m.height() for m in metrics]
        gap = max(2, heights[0] // 6)
        total = sum(heights) + gap * (len(lines) - 1)
        if corner.startswith("t"):
            y = target.top() + margin
            if avoid is not None:
                y = max(y, avoid.bottom() + gap)
        else:
            y = target.bottom() - margin - total
            if avoid is not None:
                y = min(y, avoid.top() - gap - total)
        base = (QtGui.QColor("#101210") if self._corner_light(corner)
                else QtGui.QColor("#e4e7e0"))
        for (text, f, alpha), m, h in zip(lines, metrics, heights):
            ink = QtGui.QColor(base)
            ink.setAlphaF(alpha)
            x = (target.right() - margin - m.horizontalAdvance(text)
                 if corner.endswith("r") else target.left() + margin)
            p.setFont(f)
            p.setPen(ink)
            p.drawText(QtCore.QPointF(x, y + m.ascent()), text)
            y += h + gap

    def _live_marker(self, p: QtGui.QPainter, target: QtCore.QRect,
                     margin: int, f: QtGui.QFont,
                     avoid: QtCore.QRect | None) -> None:
        """A breathing dot and the word. The breath is the point: a
        static badge could be part of a recording, and the slow pulse is
        the one thing a loop of the same frames cannot fake cheaply."""
        m = QtGui.QFontMetrics(f)
        text = _("present.live")
        tw = m.horizontalAdvance(text)
        d = max(6, int(m.ascent() * 0.6))
        gap = max(4, d // 2)
        y = target.top() + margin
        if avoid is not None:
            y = max(y, avoid.bottom() + gap)
        x = target.right() - margin - tw
        ink = (QtGui.QColor("#101210") if self._corner_light("tr")
               else QtGui.QColor("#e4e7e0"))
        ink.setAlphaF(0.92)
        p.setFont(f)
        p.setPen(ink)
        p.drawText(QtCore.QPointF(x, y + m.ascent()), text)
        dot = QtGui.QColor(BAD)
        # The breath never takes the dot below legible: at the bottom of
        # a 0.2 floor it disappeared into a brightfield ground entirely,
        # which reads as the marker going out -- and going out means
        # something here.
        dot.setAlphaF(0.7 + 0.3 * math.sin(time.monotonic() * math.pi))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        cy = y + m.ascent() - d * 0.5 - m.descent() * 0.5
        p.drawEllipse(QtCore.QPointF(x - gap - d * 0.5, cy), d * 0.5, d * 0.5)

    # ---- geometry and ink ------------------------------------------------

    def _fit(self, size: QtCore.QSize) -> QtCore.QRect:
        scaled = size.scaled(self.size(),
                             QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QtCore.QRect(x, y, scaled.width(), scaled.height())

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
    steps back out, and the frame is only ever seen in between.
    """

    closed = QtCore.Signal()

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
        #: Watches for the feed stopping. Only the marker needs this: the
        #: picture freezing is visible on its own, but a frozen frame
        #: under a lit "Live" would be the marker lying.
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
            self.view.set_frame(bgr)
        finally:
            UI_METER.since("presentation", start)

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
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
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
