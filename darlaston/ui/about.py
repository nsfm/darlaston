"""Who this is named after.

Worth a dialog rather than a line in a README: the name is the one part of the
project that carries an argument, and someone who clicks it should find the
person rather than a version number.

Frameless and dragged by its own face, like the floating panels. A window
whose whole content is one short piece of writing does not need a title bar
repeating a name already set in eighteen point at the top of it.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .. import __version__
from . import theme

#: His, from the bottom of a 1917 price list. Carried as an inscription
#: rather than as a claim: this program does not make slides, and the line
#: means something only while it stays his.
TAGLINE = "Every Slide Perfect"


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About darlaston")
        self.setWindowFlags(QtCore.Qt.WindowType.Dialog
                            | QtCore.Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(470)
        self._drag_from: QtCore.QPoint | None = None

        fam = theme.load_fonts()
        wordmark = QtWidgets.QLabel("darlaston")
        wordmark.setStyleSheet(
            f"font-family: '{fam['display']}'; font-size: 34px;"
            f"color: {theme.BRASS}; background: transparent;")

        tagline = QtWidgets.QLabel(TAGLINE.upper())
        tagline.setProperty("role", "label")

        body = QtWidgets.QLabel(
            "Herbert William Hutton Darlaston (1867-1949) was an amateur "
            "naturalist from Birmingham. In 1888 a friend introduced him to "
            "an elderly microscopist named Neville, and he came away \"in a "
            "hot fever to do similar work\". He prepared nearly a thousand "
            "slides that year; over the next twelve he made thousands more "
            "and gave most of them away to beginners."
            "<br><br>"
            "At thirty-nine he started working at it professionally, sending "
            "slides by post as far as Australia. His brochures promised "
            "<i>Every Slide Perfect</i>. He made so many, so well, you can "
            "still find them today, over a hundred years later.")
        body.setWordWrap(True)
        body.setProperty("role", "body")

        licence = QtWidgets.QLabel(
            "<i>darlaston</i> is provided freely to use, share, and modify "
            "under the GPLv3, with a linking exception for the camera SDKs, "
            "which are never redistributed. Type is IBM Plex and Petit "
            "Formal Script, both under the SIL Open Font License.")
        licence.setWordWrap(True)
        licence.setProperty("role", "key")

        version = QtWidgets.QLabel(__version__)
        version.setStyleSheet(
            f"font-family: '{fam['mono']}'; font-size: 10px;"
            f"color: {theme.DIM}; background: transparent;")

        close = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.reject)

        # Everything lives inside a panel frame rather than on the dialog:
        # a stylesheet border on a QDialog subclass needs a paintEvent to
        # draw at all, and the theme already knows what a bordered panel
        # looks like. One rounded rectangle, reused.
        panel = QtWidgets.QFrame()
        panel.setProperty("role", "panel")

        col = QtWidgets.QVBoxLayout(panel)
        col.setContentsMargins(26, 22, 26, 18)
        col.setSpacing(0)
        col.addWidget(wordmark)
        col.addWidget(tagline)
        col.addSpacing(18)
        col.addWidget(body)
        col.addSpacing(18)
        col.addWidget(licence)
        col.addSpacing(6)
        col.addWidget(version)
        col.addSpacing(14)
        col.addWidget(close)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        self.setStyleSheet(theme.stylesheet())

    # ---- dragged by its own face ----------------------------------------

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """Anywhere counts, since there is no bar to grab.

        The button box swallows its own presses before this sees them, so
        there is nothing to exclude by hand.
        """
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
