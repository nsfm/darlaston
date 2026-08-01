"""A dialog with our own frame, dragged by its own face.

The platform's title bar is a strip of somebody else's design language
across the top of every window we draw, and on a dialog whose whole
content is a short piece of writing it repeats a name already set in
large type underneath it. So: frameless, a hairline border from the
theme, and drag it by any part of it that is not a control.

The subtlety worth having in one place rather than four is that a QLabel
consumes mouse events. Dragging a frameless dialog by "the background"
works until the pointer crosses a word, and then stops -- with the
closed-hand cursor still showing, which reads as the window being stuck
rather than as the label being in the way. Labels that are not
interactive are told to let the press through.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class FramedDialog(QtWidgets.QDialog):
    """Base for dialogs that carry their own frame.

    Subclasses build into `self.content`, a vertical layout inside the
    bordered panel, and call `finish()` once everything is in it.
    """

    def __init__(self, parent=None, width: int | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.WindowType.Dialog
                            | QtCore.Qt.WindowType.FramelessWindowHint)
        if width is not None:
            self.setMinimumWidth(width)
        self._drag_from: QtCore.QPoint | None = None

        self.frame = QtWidgets.QFrame()
        self.frame.setProperty("role", "panel")
        self.content = QtWidgets.QVBoxLayout(self.frame)
        self.content.setContentsMargins(22, 20, 22, 16)
        self.content.setSpacing(12)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        self.setStyleSheet(theme.stylesheet())

    def finish(self) -> None:
        """Call once the content is built.

        Walks the labels and lets mouse events fall through them, so the
        whole face of the dialog drags rather than only the gaps between
        the words. Labels a person might want to select or click a link
        in are left alone -- those have a reason to want the press.
        """
        # Tested against what the label actually offers, not against
        # NoTextInteraction: a QLabel defaults to LinksAccessibleByMouse
        # whether or not it contains a link, so comparing for "no
        # interaction" matches nothing and silently protects every label
        # in the dialog.
        wants_mouse = (QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                       | QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse)
        for label in self.findChildren(QtWidgets.QLabel):
            interactive = bool(label.textInteractionFlags() & wants_mouse)
            if interactive and ("href=" in label.text()
                                or label.textInteractionFlags()
                                & QtCore.Qt.TextInteractionFlag
                                .TextSelectableByMouse):
                continue                     # it has a reason to want the press
            label.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Belt and braces over the stylesheet rule: whether a standard
        # button gets a system icon is a platform style hint, and a hint
        # is a thing that can come back.
        for box in self.findChildren(QtWidgets.QDialogButtonBox):
            for button in box.buttons():
                button.setIcon(QtGui.QIcon())

    # ---- dragged by its own face ----------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() is QtCore.Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.pos()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_from = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)
