"""A fade at the hidden end of a scroll area.

A thin scrollbar on a dark panel is easy to miss, and a region that
ends in a clean edge looks like it ends. This paints a short gradient
from the panel's colour to nothing over the top of the viewport while
there is content above, and over the bottom while there is content
below, so the eye reads "more" without a bar to find. Transparent to
the mouse; it is a picture, not a control.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class ScrollFade(QtWidgets.QWidget):
    """Sits over a QScrollArea's viewport and shades its hidden ends."""

    DEPTH = 22

    def __init__(self, area: QtWidgets.QScrollArea, ground: str | None = None) -> None:
        super().__init__(area.viewport())
        self._area = area
        self._ground = ground
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        area.viewport().installEventFilter(self)
        bar = area.verticalScrollBar()
        bar.valueChanged.connect(self._changed)
        bar.rangeChanged.connect(self._changed)
        self._fit()
        self.raise_()

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show):
            self._fit()
        return False

    def _fit(self) -> None:
        self.setGeometry(self._area.viewport().rect())
        self.raise_()
        self.update()

    def _changed(self, *_) -> None:
        self.update()

    @property
    def above(self) -> bool:
        return self._area.verticalScrollBar().value() > 0

    @property
    def below(self) -> bool:
        bar = self._area.verticalScrollBar()
        return bar.value() < bar.maximum()

    def paintEvent(self, _event) -> None:
        if not (self.above or self.below):
            return
        colour = QtGui.QColor(self._ground or theme.PANEL)
        clear = QtGui.QColor(colour)
        clear.setAlpha(0)
        with QtGui.QPainter(self) as p:
            w, h, d = self.width(), self.height(), self.DEPTH
            if self.above:
                grad = QtGui.QLinearGradient(0, 0, 0, d)
                grad.setColorAt(0.0, colour)
                grad.setColorAt(1.0, clear)
                p.fillRect(QtCore.QRect(0, 0, w, d), grad)
            if self.below:
                grad = QtGui.QLinearGradient(0, h - d, 0, h)
                grad.setColorAt(0.0, clear)
                grad.setColorAt(1.0, colour)
                p.fillRect(QtCore.QRect(0, h - d, w, d), grad)
