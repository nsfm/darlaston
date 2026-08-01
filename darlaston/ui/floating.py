"""Panels that float over the live view, and can be moved.

The rail ran out of room. The response is not a scrollbar -- that keeps every
control equally present and adds hunting on top -- but moving the things you
consult from the things you operate. Consulting surfaces float over the image,
where there is space and where a map or a coverage readout actually wants to
be; the rail keeps only what a hand reaches for while an eye is at the
eyepiece.

Floating means draggable. A panel pinned to one corner will always be over
the one diatom you care about eventually, and "move the window" is a thing
every person already knows how to do.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import icons, theme

#: Grab height at the top of a panel. Deliberately generous: this is a
#: precision instrument being operated by someone whose other hand is on a
#: focus knob.
TITLE_H = 22


class _Grip(QtWidgets.QWidget):
    """A corner to drag a panel bigger by.

    Its own widget rather than a rect tested inside the panel, because the
    panel's body fills everything but a nine-pixel margin and would
    otherwise take the press first. Raised above the body on every resize.
    """

    SIZE = 14

    def __init__(self, panel: QtWidgets.QWidget) -> None:
        super().__init__(panel)
        self._panel = panel
        self._from: QtCore.QPoint | None = None
        self._start: QtCore.QSize | None = None
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        self.setToolTip("Drag to resize")
        # Only the strokes, no plate under them: the theme gives every
        # QWidget the window background, which drew a slightly darker
        # square in the corner of the panel and left the hairlines
        # invisible on top of it.
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _event) -> None:
        """Three hairlines, at the weight everything else is drawn at."""
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.INK), 1))
        for offset in (1, 5, 9):
            p.drawLine(self.SIZE - 2 - offset, self.SIZE - 2,
                       self.SIZE - 2, self.SIZE - 2 - offset)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() is QtCore.Qt.MouseButton.LeftButton:
            self._from = event.globalPosition().toPoint()
            self._start = self._panel.size()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._from is None or self._start is None:
            return
        delta = event.globalPosition().toPoint() - self._from
        self._panel.resize(
            max(self._panel.minimumWidth(), self._start.width() + delta.x()),
            max(self._panel.minimumHeight(), self._start.height() + delta.y()))

    def mouseReleaseEvent(self, _event: QtGui.QMouseEvent) -> None:
        self._from = None
        self._start = None


class FloatingPanel(QtWidgets.QWidget):
    """A rounded, mostly-opaque panel that lives over the live view.

    Positions itself relative to its host on first show and then stays where
    it is put, in host-relative terms, so resizing the window does not fling
    it off the edge.
    """

    closed = QtCore.Signal()

    def __init__(self, title: str, host: QtWidgets.QWidget,
                 closable: bool = True) -> None:
        super().__init__(host)
        self._host = host
        self._title = title
        self._drag_from: QtCore.QPoint | None = None
        #: Position as a fraction of the host, so a window resize keeps the
        #: panel where it looks like it should be rather than in absolute
        #: pixels that may now be off-screen.
        self._rel = (0.02, 0.6)
        self._dragging = False
        #: Size and position are imposed once. After that the panel is the
        #: operator's: closing one and opening it again used to hand back
        #: whatever default was computed before the window had been laid
        #: out, which is how the slide map came back smaller every time.
        self._placed = False

        self.body = QtWidgets.QWidget(self)

        # A drawn mark rather than the multiplication sign: that glyph
        # arrives at a different weight and height from every fallback
        # font, and this one has to sit level with a hairline title rule.
        self._close = QtWidgets.QPushButton(self)
        self._close.setIcon(icons.hover_icon("close", theme.INK, theme.BRASS, 12))
        self._close.setIconSize(QtCore.QSize(12, 12))
        self._close.setFixedSize(16, 16)
        self._close.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self._close.setStyleSheet(
            "QPushButton { border: 0; background: transparent; }")
        self._close.clicked.connect(self._on_close)
        self._close.setVisible(closable)

        # Bottom-right, like every other resizeable thing on the desktop --
        # but drawn and handled here rather than with QSizeGrip, which only
        # resizes *top-level windows*. These are children of the live view,
        # so the stock grip was inert, invisible under the body widget, and
        # impossible to find because there was nothing to find.
        self._grip = _Grip(self)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(9, TITLE_H + 2, 9, 9)
        outer.setSpacing(0)
        outer.addWidget(self.body)
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    # ---- placement -------------------------------------------------------

    def place(self, size: tuple[int, int] | None = None,
              force: bool = False) -> None:
        """Position against the host, and size it the first time only.

        `resize` rather than `setFixedSize`: these are panels a person
        works in, and one that cannot be made bigger is one that will be
        the wrong size for somebody. The minimum keeps the title bar and
        its close mark reachable.
        """
        if size is not None and (force or not self._placed):
            self.setMinimumSize(180, TITLE_H + 40)
            self.setMaximumSize(16777215, 16777215)
            self.resize(*size)
        hw, hh = self._host.width(), self._host.height()
        x = int(self._rel[0] * hw)
        y = int(self._rel[1] * hh)
        # Never let a panel leave the host entirely; a title bar must stay
        # reachable or the panel cannot be moved back.
        x = max(-self.width() + 60, min(x, hw - 60))
        y = max(0, min(y, hh - TITLE_H))
        if not self._placed:
            self.move(x, y)
        self._placed = True
        self.raise_()

    def set_relative(self, fx: float, fy: float) -> None:
        self._rel = (fx, fy)

    # ---- dragging --------------------------------------------------------

    def _title_rect(self) -> QtCore.QRect:
        return QtCore.QRect(0, 0, self.width(), TITLE_H + 2)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if (event.button() is QtCore.Qt.MouseButton.LeftButton
                and self._title_rect().contains(event.pos())):
            self._drag_from = event.globalPosition().toPoint() - self.pos()
            self._dragging = True
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            self.raise_()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging and self._drag_from is not None:
            p = event.globalPosition().toPoint() - self._drag_from
            hw, hh = self._host.width(), self._host.height()
            x = max(-self.width() + 60, min(p.x(), hw - 60))
            y = max(0, min(p.y(), hh - TITLE_H))
            self.move(x, y)
            self._rel = (x / max(hw, 1), y / max(hh, 1))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._dragging = False
        self._drag_from = None
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    # ---- paint -----------------------------------------------------------

    def resizeEvent(self, event) -> None:
        self._close.move(self.width() - 22, 4)
        self._grip.move(self.width() - _Grip.SIZE - 2,
                        self.height() - _Grip.SIZE - 2)
        self._grip.raise_()
        super().resizeEvent(event)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.LINE)))
        p.setBrush(QtGui.QColor(16, 18, 16, 235))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5,
                                                              -0.5, -0.5),
                          5.0, 5.0)
        # Title row, and the grab affordance: a person must be able to see
        # where the panel can be picked up.
        f = p.font()
        f.setPointSizeF(7.5)
        f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(f)
        p.setPen(QtGui.QColor(theme.DIM))
        p.drawText(QtCore.QRect(9, 3, self.width() - 40, TITLE_H - 4),
                   QtCore.Qt.AlignmentFlag.AlignVCenter,
                   self._title.upper())
        p.setPen(QtGui.QPen(QtGui.QColor(theme.LINE)))
        p.drawLine(1, TITLE_H, self.width() - 2, TITLE_H)
        p.end()
