"""Small painted widgets. Qt lives here and nowhere below it."""
from __future__ import annotations

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
        h, w = rgb.shape[:2]
        # QImage does not copy, so keep the buffer alive on the instance.
        self._buf = np.ascontiguousarray(rgb)
        self._image = QtGui.QImage(self._buf.data, w, h, self._buf.strides[0],
                                   QtGui.QImage.Format.Format_BGR888)
        if peaking is not None:
            self._peaking = self._peaking_overlay(peaking)
        self.update()

    def _peaking_overlay(self, field: np.ndarray) -> QtGui.QImage:
        """Threshold to a target fraction of lit pixels rather than a fixed
        level -- Magic Lantern's servo idea. A fixed threshold either saturates
        or goes blank while racking, which is exactly when it must stay legible.
        """
        target = 0.005
        cut = float(np.quantile(field, 1.0 - target))
        mask = (field >= max(cut, 1e-6)).astype(np.uint8)
        h, w = mask.shape
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., 0] = 90        # B
        rgba[..., 1] = 235       # G
        rgba[..., 2] = 255       # R
        rgba[..., 3] = mask * 220
        self._peak_buf = np.ascontiguousarray(rgba)
        return QtGui.QImage(self._peak_buf.data, w, h, self._peak_buf.strides[0],
                            QtGui.QImage.Format.Format_ARGB32)

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

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#111110"))
        if self._image is None:
            p.setPen(DIM)
            p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                       "no signal")
            return
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        target = self._fit(self._image.size())
        p.drawImage(target, self._image)
        if self._peaking is not None:
            p.drawImage(target, self._peaking)

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
            p.setPen(DIM)
            p.drawText(QtCore.QRect(0, 18, w, 16),
                       QtCore.Qt.AlignmentFlag.AlignCenter,
                       "not sweeping")
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
                   "covered — sweep complete" if done
                   else f"{self._value * 100:.0f}% through focus"
                   + ("  still finding structure"
                      if self._value >= 0.999 else ""))
