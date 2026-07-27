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


class LiveView(QtWidgets.QWidget):
    """The preview. Scales to fit, keeps aspect, never upscales past 1:1."""

    def __init__(self) -> None:
        super().__init__()
        self._image: QtGui.QImage | None = None
        self._peaking: QtGui.QImage | None = None
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

    def set_data(self, hist: np.ndarray, clipped: float, black: float) -> None:
        self._hist, self._clipped, self._black = hist, clipped, black
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
        msg = f"clipped {self._clipped * 100:.2f}%   black {self._black * 100:.2f}%"
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
