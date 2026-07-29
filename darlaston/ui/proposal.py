"""A suggestion the operator can accept or wave away.

Turret detection is right most of the time and wrong some of the time, and
the wrong times are expensive: the objective is the key to every calibration
lookup, so a silent misdetection would quietly attach the wrong flat to every
capture that followed. That asymmetry decides the interface. It cannot be a
notification, because a notification that is ignored has still changed the
state. It cannot be a modal dialog either -- a rotation happens while both
hands are on the scope and the eye is at the eyepiece, and a box that steals
focus mid-adjustment is worse than no detection at all.

So: a strip over the live view that states what it thinks, waits, and expires
on its own. Doing nothing is a valid answer and means no.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

#: How long a proposal stands before it withdraws itself. Long enough to
#: finish focusing and look up, short enough that a stale suggestion is never
#: accepted by accident.
TIMEOUT_MS = 20_000


class ProposalBar(QtWidgets.QWidget):
    """One line, two buttons, over the image."""

    accepted = QtCore.Signal(object)          # carries the payload
    dismissed = QtCore.Signal()

    def __init__(self, host: QtWidgets.QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._payload = None

        self.text = QtWidgets.QLabel("")
        self.text.setStyleSheet(f"color: {theme.INK}; font-size: 12px;")
        self.detail = QtWidgets.QLabel("")
        self.detail.setStyleSheet(f"color: {theme.DIM}; font-size: 10px;")

        self.yes = QtWidgets.QPushButton("Yes")
        self.no = QtWidgets.QPushButton("No")
        for b, colour in ((self.yes, theme.BRASS), (self.no, theme.DIM)):
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(24)
            b.setStyleSheet(
                f"QPushButton {{ border: 1px solid {colour};"
                f" border-radius: 12px; margin: 1px; padding: 2px 14px;"
                f" color: {colour}; background: transparent; }}"
                f"QPushButton:hover {{ background: rgba(200,155,74,0.12); }}")
        self.yes.clicked.connect(self._accept)
        self.no.clicked.connect(self._dismiss)

        words = QtWidgets.QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        words.addWidget(self.text)
        words.addWidget(self.detail)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(10)
        row.addLayout(words, 1)
        row.addWidget(self.no)
        row.addWidget(self.yes)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)
        self.hide()

    # ---- showing ---------------------------------------------------------

    def propose(self, text: str, detail: str, payload) -> None:
        self.text.setText(text)
        self.detail.setText(detail)
        self._payload = payload
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self._timer.start(TIMEOUT_MS)

    def place(self) -> None:
        w = min(max(self.sizeHint().width(), 340), self._host.width() - 40)
        self.setFixedWidth(w)
        self.move((self._host.width() - w) // 2, 18)

    def _accept(self) -> None:
        self._timer.stop()
        self.hide()
        self.accepted.emit(self._payload)

    def _dismiss(self) -> None:
        self._timer.stop()
        self.hide()
        self.dismissed.emit()

    # ---- paint -----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.BRASS)))
        p.setBrush(QtGui.QColor(16, 18, 16, 242))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5,
                                                              -0.5, -0.5),
                          6.0, 6.0)
        p.end()
