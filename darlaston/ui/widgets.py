"""Small painted widgets. Qt lives here and nowhere below it."""
from __future__ import annotations

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

INK = QtGui.QColor("#e8e6e3")
DIM = QtGui.QColor("#6b6864")
PANEL = QtGui.QColor("#0b0d0b")
GOOD = QtGui.QColor("#5fb37a")
WARN = QtGui.QColor("#d9a441")
BAD = QtGui.QColor("#d0605e")
BRASS = QtGui.QColor("#c89b4a")


class LiveView(QtWidgets.QWidget):
    """The preview. Scales to fit, keeps aspect, never upscales past 1:1."""

    #: Normalised (x, y, w, h) the operator dragged out on the image.
    region_drawn = QtCore.Signal(tuple)

    def __init__(self) -> None:
        super().__init__()
        self._image: QtGui.QImage | None = None
        self._peaking: QtGui.QImage | None = None
        self._focus_rect: tuple[float, float, float, float] | None = None
        self._remaining: QtGui.QImage | None = None
        self._drag_from: QtCore.QPointF | None = None
        self._drag_to: QtCore.QPointF | None = None
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setMinimumSize(480, 320)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    def set_frame(self, rgb: np.ndarray, peaking: np.ndarray | None) -> None:
        """Scale to the widget here, so painting is a straight blit.

        Qt's SmoothPixmapTransform resampling a 2.2 MP frame down to the
        widget ran at 5.4 ms per paint on its own and 15.5 ms with the
        peaking overlay alongside -- half the frame budget, on the thread
        that also has to stay responsive. OpenCV does the same reduction in
        about a millisecond and releases the GIL while it works, so the
        resize moves here and paintEvent draws one-to-one.
        """
        h, w = rgb.shape[:2]
        target = self._fit(QtCore.QSize(w, h))
        tw, th = max(1, target.width()), max(1, target.height())
        if (tw, th) != (w, h):
            # INTER_AREA is the right *reduction* filter and is what Qt's
            # smooth transform was approximating; it is a poor magnifier, so
            # a window larger than the frame gets linear instead.
            rgb = cv2.resize(rgb, (tw, th),
                             interpolation=cv2.INTER_AREA if tw < w
                             else cv2.INTER_LINEAR)
        # QImage does not copy, so keep the buffer alive on the instance.
        self._buf = np.ascontiguousarray(rgb)
        self._image = QtGui.QImage(self._buf.data, tw, th,
                                   self._buf.strides[0],
                                   QtGui.QImage.Format.Format_BGR888)
        self._image_at = target
        if peaking is not None:
            self._peaking = self._peaking_overlay(peaking, (tw, th))
        self.update()

    def _peaking_overlay(self, field: np.ndarray,
                         size: tuple[int, int] | None = None) -> QtGui.QImage:
        """Threshold to a target fraction of lit pixels rather than a fixed
        level -- Magic Lantern's servo idea. A fixed threshold either saturates
        or goes blank while racking, which is exactly when it must stay legible.

        Thresholded at the field's own resolution and only then resized, so
        the quantile is taken over the real distribution rather than over
        interpolated values.
        """
        target = 0.005
        # np.quantile sorts the whole field -- 5.2 ms on a half-resolution
        # frame, which made this the most expensive thing on the UI thread.
        # A single partition finds the same element in 0.87 ms, and the
        # answer is identical because that is all a quantile of this kind is.
        flat = field.ravel()
        k = max(0, min(flat.size - 1, int(flat.size * (1.0 - target))))
        cut = float(np.partition(flat, k)[k])
        mask = (field >= max(cut, 1e-6)).astype(np.uint8)
        if size is not None and (mask.shape[1], mask.shape[0]) != size:
            mask = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST)
        h, w = mask.shape
        # Only the alpha changes between frames, so the buffer is allocated
        # once and the colour written once. Rebuilding a 3 MB RGBA array
        # every frame was pure churn for three constant channels.
        buf = getattr(self, "_peak_buf", None)
        if buf is None or buf.shape[:2] != (h, w):
            buf = np.empty((h, w, 4), np.uint8)
            buf[..., 0] = 90         # B
            buf[..., 1] = 235        # G
            buf[..., 2] = 255        # R
            self._peak_buf = buf
            self._peak_image = QtGui.QImage(
                buf.data, w, h, buf.strides[0],
                QtGui.QImage.Format.Format_ARGB32)
        np.multiply(mask, 220, out=buf[..., 3], casting="unsafe")
        return self._peak_image

    def set_focus_rect(self, rect) -> None:
        self._focus_rect = rect

    def set_remaining(self, mask, rect) -> None:
        """Regions that still need to go through focus.

        Drawn so it says *where* to keep racking, not merely that you are not
        finished. Brass at low opacity: it must not be mistaken for the red
        that means clipping.
        """
        if mask is None or rect is None:
            self._remaining = None
            return
        h, w = mask.shape
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., 0] = 74       # B
        rgba[..., 1] = 155      # G
        rgba[..., 2] = 200      # R
        rgba[..., 3] = (mask > 0).astype(np.uint8) * 70
        self._rem_buf = np.ascontiguousarray(rgba)
        self._remaining = QtGui.QImage(self._rem_buf.data, w, h,
                                       self._rem_buf.strides[0],
                                       QtGui.QImage.Format.Format_ARGB32)

    def clear_peaking(self) -> None:
        self._peaking = None
        self.update()

    def set_notice(self, text: str | None) -> None:
        """A message that cannot be missed, over the frozen preview.

        Exists for the exposure: the pull freezes the live view for over a
        second and the operator has no reason to know that racking during it
        smears the frame. The screen is the viewfinder, so the screen says so.
        """
        self._notice = text
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#111110"))
        if self._image is None:
            p.setPen(DIM)
            p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                       "no signal")
            return
        # No SmoothPixmapTransform: the image already arrives at widget size,
        # so this is a blit rather than a resample.
        target = getattr(self, "_image_at", None) or self._fit(self._image.size())
        p.drawImage(target.topLeft(), self._image)
        if self._peaking is not None:
            p.drawImage(target.topLeft(), self._peaking)

        # The measured region, drawn so it is never a mystery what the focus
        # number refers to.
        if self._focus_rect is not None:
            x, y, w, h = self._focus_rect
            box = QtCore.QRectF(target.x() + x * target.width(),
                                target.y() + y * target.height(),
                                w * target.width(), h * target.height())
            if self._remaining is not None:
                p.drawImage(box, self._remaining)
            pen = QtGui.QPen(BRASS, 1)
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRect(box)

        if self._drag_from is not None and self._drag_to is not None:
            p.setPen(QtGui.QPen(BRASS, 1))
            p.drawRect(QtCore.QRectF(self._drag_from, self._drag_to).normalized())

        notice = getattr(self, "_notice", None)
        if notice:
            band = QtCore.QRect(0, self.height() // 2 - 34,
                                self.width(), 68)
            p.fillRect(band, QtGui.QColor(0, 0, 0, 170))
            f = p.font()
            f.setPointSizeF(17.0)
            f.setBold(True)
            f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 1.5)
            p.setFont(f)
            p.setPen(WARN)
            p.drawText(band, QtCore.Qt.AlignmentFlag.AlignCenter, notice)

    # ---- drawing a region ------------------------------------------------

    def mousePressEvent(self, e) -> None:
        if self._image is not None:
            self._drag_from = e.position()
            self._drag_to = e.position()
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self._drag_to = e.position()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_from is None or self._image is None:
            return
        box = QtCore.QRectF(self._drag_from, e.position()).normalized()
        self._drag_from = self._drag_to = None
        target = self._fit(self._image.size())
        if box.width() < 12 or box.height() < 12 or target.width() == 0:
            self.update()
            return
        # Widget coordinates back to image-normalised, clamped to the frame.
        nx = (box.x() - target.x()) / target.width()
        ny = (box.y() - target.y()) / target.height()
        nw, nh = box.width() / target.width(), box.height() / target.height()
        nx, ny = max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))
        nw, nh = min(nw, 1.0 - nx), min(nh, 1.0 - ny)
        if nw > 0.02 and nh > 0.02:
            self.region_drawn.emit((nx, ny, nw, nh))
        self.update()

    def _fit(self, size: QtCore.QSize) -> QtCore.QRect:
        scaled = size.scaled(self.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QtCore.QRect(x, y, scaled.width(), scaled.height())


class Histogram(QtWidgets.QWidget):
    """Log-scaled histogram with explicit clipping and black-crush markers.

    Non-negotiable, because the incumbent software was blowing 62% of the frame
    and giving no indication whatsoever.
    """

    def __init__(self) -> None:
        super().__init__()
        self._hist = np.zeros(256, np.float32)
        self._clipped = 0.0
        self._black = 0.0
        self.setFixedHeight(96)

    def set_data(self, hist: np.ndarray, clipped: float, black: float,
                 per_channel: tuple[float, float, float] | None = None) -> None:
        self._hist, self._clipped, self._black = hist, clipped, black
        self._per = per_channel
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), PANEL)
        h, w = self.height(), self.width()
        v = np.log1p(self._hist)
        peak = float(v.max()) or 1.0
        p.setPen(QtGui.QPen(INK, 1))
        for i in range(256):
            x = int(i * w / 256)
            bar = int(v[i] / peak * (h - 14))
            p.drawLine(x, h - 12, x, h - 12 - bar)

        # Warning bands live at the ends, where the damage happens.
        if self._clipped > 0.0005:
            p.fillRect(w - 6, 0, 6, h - 12, BAD)
        if self._black > 0.0005:
            p.fillRect(0, 0, 6, h - 12, WARN)

        p.setPen(DIM)
        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        per = getattr(self, "_per", None)
        if per and max(per) > 0.0005:
            hot = "".join(n for n, v in zip("RGB", per) if v > 0.0005)
            msg = (f"clipped {self._clipped * 100:.2f}% (green)   {hot} hot"
                   f"   black {self._black * 100:.2f}%")
        else:
            msg = (f"clipped {self._clipped * 100:.2f}%"
                   f"   black {self._black * 100:.2f}%")
        p.drawText(QtCore.QRect(0, h - 12, w, 12),
                   QtCore.Qt.AlignmentFlag.AlignCenter, msg)


class FocusTraceView(QtWidgets.QWidget):
    """Rolling, peak-normalised. An absolute focus number means nothing across
    metrics or subjects; the shape and the distance from peak mean everything."""

    def __init__(self) -> None:
        super().__init__()
        self._values = np.zeros(0, np.float32)
        self._fraction = 0.0
        self.setFixedHeight(96)

    def set_data(self, values: np.ndarray, fraction_of_peak: float) -> None:
        self._values, self._fraction = values, fraction_of_peak
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), PANEL)
        h, w = self.height(), self.width()
        p.setPen(QtGui.QPen(DIM, 1, QtCore.Qt.PenStyle.DashLine))
        p.drawLine(0, 6, w, 6)

        v = self._values
        if v.size > 1:
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            colour = (GOOD if self._fraction > 0.95
                      else WARN if self._fraction > 0.75 else BAD)
            p.setPen(QtGui.QPen(colour, 1.6))
            path = QtGui.QPainterPath()
            for i, val in enumerate(v):
                x = i * w / max(len(v) - 1, 1)
                y = 6 + (1.0 - float(val)) * (h - 24)
                path.lineTo(x, y) if i else path.moveTo(x, y)
            p.drawPath(path)

        p.setPen(DIM)
        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.drawText(QtCore.QRect(0, h - 14, w, 14),
                   QtCore.Qt.AlignmentFlag.AlignCenter,
                   f"{self._fraction * 100:.0f}% of peak sharpness")


class FocusGroup(QtWidgets.QWidget):
    """The focus trace, its two toggles, and the coverage bar as one object.

    Previously three stacked things: a graph, two separate checkboxes, and a
    coverage meter that reserved its height to say "not sweeping". That is
    four rows of rail to express one idea. Here the toggles sit on the
    graph's own header, where what they modify is unambiguous, and the
    coverage bar appears only while there is coverage to report.
    """

    peaking_toggled = QtCore.Signal(bool)
    sweep_toggled = QtCore.Signal(bool)
    stack_toggled = QtCore.Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.trace = FocusTraceView()
        self.coverage = CoverageMeter()
        self.coverage.setVisible(False)

        self.stack = _Toggle("stack", "Capture a Z-stack: rack the fine "
                                      "focus, pause, and a slice is taken.\n"
                                      "Rack again for the next. The knob is "
                                      "the whole interface --\nnothing is "
                                      "clicked between slices.")
        self.stack.toggled.connect(self._on_stack)
        self.peaking = _Toggle("peak", "Highlight the sharpest edges in the "
                                       "live view.")
        self.sweep = _Toggle("sweep", "Accumulate which parts of the frame "
                                      "have been through focus.\nRack past "
                                      "focus in both directions; it reads "
                                      "100%\nwhen every region with something "
                                      "in it has been passed.")
        self.peaking.toggled.connect(self.peaking_toggled)
        self.sweep.toggled.connect(self._on_sweep)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        label = QtWidgets.QLabel("FOCUS")
        label.setProperty("role", "label")
        header.addWidget(label)
        header.addStretch(1)
        header.addWidget(self.stack)
        header.addWidget(self.peaking)
        header.addWidget(self.sweep)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addLayout(header)
        col.addWidget(self.trace)
        col.addWidget(self.coverage)

    def _on_stack(self, on: bool) -> None:
        # A stack needs the sweep: coverage is its finish line, and the
        # trigger reads the sharpness field the sweep computes.
        if on and not self.sweep.isChecked():
            self.sweep.setChecked(True)
        self.stack_toggled.emit(on)

    def _on_sweep(self, on: bool) -> None:
        # The bar only exists while it has something to say; an empty meter
        # reading "not sweeping" was a permanent apology for its own presence.
        self.coverage.setVisible(on)
        self.sweep_toggled.emit(on)

    def set_data(self, values, fraction) -> None:
        self.trace.set_data(values, fraction)

    def set_coverage(self, value, complete: bool = False) -> None:
        self.coverage.set_value(value, complete)


class _Toggle(QtWidgets.QPushButton):
    """A small checkable chip. Reads as a state, not as a form field."""

    def __init__(self, text: str, hint: str = "") -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setToolTip(hint)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(19)
        self.setStyleSheet(
            "QPushButton { border: 1px solid #2a2e29; border-radius: 9px;"
            " margin: 1px; padding: 1px 9px; font-size: 10px;"
            " color: #757a72; background: transparent; }"
            "QPushButton:hover { color: #e4e7e0; }"
            "QPushButton:checked { color: #101210; background: #c89b4a;"
            " border-color: #c89b4a; }")


class CoverageMeter(QtWidgets.QWidget):
    """How much of the frame has been through focus.

    The number that turns "have I taken enough slices?" from a feel into a
    stop condition. Reads zero until something has risen *and fallen* -- you
    have to pass through focus, not merely reach it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._value: float | None = None
        self.setFixedHeight(34)

    def set_value(self, value: float | None, complete: bool = False) -> None:
        self._value = value
        self._complete = complete
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        h, w = self.height(), self.width()
        p.fillRect(QtCore.QRect(0, 10, w, 6), PANEL)

        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        if self._value is None:
            # The meter is only shown while sweeping now, so an empty one
            # means the sweep has started and nothing has come through focus
            # yet -- which is a real state and worth naming truthfully.
            p.setPen(DIM)
            p.drawText(QtCore.QRect(0, 18, w, 16),
                       QtCore.Qt.AlignmentFlag.AlignCenter,
                       "rack through focus to begin")
            return

        # Complete is not the same as 100%: the structured area must also
        # have stopped growing, or a frame that has only shown half of itself
        # would read as finished.
        done = getattr(self, "_complete", False)
        colour = GOOD if done else BRASS
        p.fillRect(QtCore.QRect(0, 10, int(w * self._value), 6), colour)
        p.setPen(DIM)
        p.drawText(QtCore.QRect(0, 18, w, 16),
                   QtCore.Qt.AlignmentFlag.AlignCenter,
                   "covered -- sweep complete" if done
                   else f"{self._value * 100:.0f}% through focus"
                   + ("  still finding structure"
                      if self._value >= 0.999 else ""))
