"""Watching a stack assemble.

A floating panel that shows the all-in-focus image building live: every
captured slice contributes its sharp regions, and the composite fills in as
the operator racks. It is a preview-resolution approximation of the real
merge -- winner-takes-all, no alignment -- and that is fine, because its job
is not accuracy. Its job is to show that the thing is working and to make
racking through a diatom feel like developing a photograph: the subject
emerges.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

#: Width of the running composite. Small enough that folding a slice in is
#: a few milliseconds, large enough to be worth watching.
WIDTH = 456


class StackAssembly(QtWidgets.QWidget):
    """The running composite, a slice count, and nothing else."""

    def __init__(self) -> None:
        super().__init__()
        self._rgb: np.ndarray | None = None
        self._sharp: np.ndarray | None = None
        self._image: QtGui.QImage | None = None
        self._count = 0
        self.setMinimumHeight(120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    def reset(self) -> None:
        self._rgb = None
        self._sharp = None
        self._image = None
        self._count = 0
        self.update()

    @property
    def count(self) -> int:
        return self._count

    def add_slice(self, raw: np.ndarray) -> None:
        """Fold one captured slice into the running composite.

        Quick half-Bayer demosaic and a pooled sharpness field, then each
        pixel keeps whichever slice was sharpest there so far. The 2x2 green
        mean stands in for luma exactly as the merge engine's does.
        """
        g = ((raw[0::2, 0::2].astype(np.float32)
              + raw[1::2, 1::2].astype(np.float32)) / 2)
        r = raw[1::2, 0::2].astype(np.float32)
        b = raw[0::2, 1::2].astype(np.float32)
        h, w = g.shape
        scale = WIDTH / w
        size = (WIDTH, max(1, int(h * scale)))
        rgb = cv2.resize(np.dstack([b, g, r]), size,
                         interpolation=cv2.INTER_AREA)
        luma = cv2.resize(g, size, interpolation=cv2.INTER_AREA)
        gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1)
        sharp = cv2.GaussianBlur(gx * gx + gy * gy, (0, 0), 3.0)

        if self._rgb is None:
            self._rgb = rgb
            self._sharp = sharp
        else:
            better = sharp > self._sharp
            self._rgb[better] = rgb[better]
            np.maximum(self._sharp, sharp, out=self._sharp)
        self._count += 1

        # Roughly balanced and gamma'd, or a raw microscope field shows as a
        # dark green rectangle.
        show = self._rgb.copy()
        means = [max(float(show[:, :, c].mean()), 1e-6) for c in range(3)]
        for c in range(3):
            show[:, :, c] *= means[1] / means[c]
        peak = float(np.percentile(show, 99.5)) or 1.0
        show = np.power(np.clip(show / peak, 0, 1), 1 / 2.2)
        self._buf = np.ascontiguousarray((show * 255).astype(np.uint8))
        hh, ww = self._buf.shape[:2]
        self._image = QtGui.QImage(self._buf.data, ww, hh,
                                   self._buf.strides[0],
                                   QtGui.QImage.Format.Format_BGR888)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(theme.SUNK))
        if self._image is None:
            p.setPen(QtGui.QColor(theme.DIM))
            p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                       "rack, pause — the first slice starts it")
            p.end()
            return
        scaled = self._image.size().scaled(
            self.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QtCore.QRect(x, y, scaled.width(), scaled.height()),
                    self._image)
        p.setPen(QtGui.QColor(theme.INK))
        f = p.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.fillRect(QtCore.QRect(x + 6, y + 6, 72, 18),
                   QtGui.QColor(0, 0, 0, 150))
        p.drawText(QtCore.QRect(x + 10, y + 6, 68, 18),
                   QtCore.Qt.AlignmentFlag.AlignVCenter,
                   f"{self._count} slice{'s' if self._count != 1 else ''}")
        p.end()
