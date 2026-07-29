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

    accepted = QtCore.Signal(object)          # carries the chosen payload
    dismissed = QtCore.Signal()

    def __init__(self, host: QtWidgets.QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._payload = None

        self.text = QtWidgets.QLabel("")
        self.text.setStyleSheet(f"color: {theme.INK}; font-size: 12px;")
        self.detail = QtWidgets.QLabel("")
        self.detail.setStyleSheet(f"color: {theme.DIM}; font-size: 10px;")

        # Buttons are built per proposal, because how many there are is the
        # message. When the detector knows which objective it is, one button
        # says yes; when it knows only that the turret moved and which pair
        # it is between, two buttons name both -- which is the honest offer,
        # and keeps every click in one place instead of sending the operator
        # to the rail to correct it.
        self._buttons: list[QtWidgets.QPushButton] = []

        # An escape hatch listing every position. Without it a wrong
        # proposal can only be waved away, and waving it away leaves the
        # application believing an objective that is no longer in the light
        # path -- which then makes the *next* proposal wrong too, by
        # stepping from a stale position. One unanswered rotation used to
        # poison every rotation after it.
        self.other = QtWidgets.QToolButton()
        self.other.setText("Other ▾")
        self.other.setFixedHeight(24)
        self.other.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.other.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.other.setStyleSheet(
            f"QToolButton {{ border: 1px solid {theme.DIM};"
            f" border-radius: 12px; margin: 1px; padding: 2px 12px;"
            f" color: {theme.DIM}; background: transparent; }}"
            f"QToolButton::menu-indicator {{ image: none; }}"
            f"QToolButton:hover {{ color: {theme.INK}; }}")
        self._other_menu = QtWidgets.QMenu(self)
        self.other.setMenu(self._other_menu)

        # Says something different from ignoring the bar: this one asserts
        # that nothing moved, so the recorded objective is still right.
        self.no = QtWidgets.QPushButton("Didn't switch")
        self.no.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.no.setFixedHeight(24)
        self.no.setStyleSheet(self._style(theme.DIM))
        self.no.clicked.connect(self._answered_no)

        words = QtWidgets.QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        words.addWidget(self.text)
        words.addWidget(self.detail)

        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(14, 8, 10, 8)
        self._row.setSpacing(8)
        self._row.addLayout(words, 1)
        self._row.addWidget(self.no)
        self._row.addWidget(self.other)

        #: Was the last dismissal a click on "Didn't switch" rather than the
        #: timer running out? The two mean different things: one asserts the
        #: recorded objective is still right, the other says nothing at all.
        self.answered_explicitly = False

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)
        self.hide()

    # ---- showing ---------------------------------------------------------

    @staticmethod
    def _style(colour: str) -> str:
        return (f"QPushButton {{ border: 1px solid {colour};"
                f" border-radius: 12px; margin: 1px; padding: 2px 14px;"
                f" color: {colour}; background: transparent; }}"
                f"QPushButton:hover {{ background: rgba(200,155,74,0.12); }}")

    def propose(self, text: str, detail: str, choices, others=None) -> None:
        """`choices` is a list of (label, payload), most likely first.

        `others` populates the escape-hatch menu with every position, so a
        proposal can always be answered correctly rather than only refused.
        """
        self._other_menu.clear()
        for label, payload in (others or []):
            act = self._other_menu.addAction(label)
            act.triggered.connect(
                lambda _=False, p=payload: self._choose(p))
        self.other.setVisible(bool(others))
        for b in self._buttons:
            self._row.removeWidget(b)
            b.deleteLater()
        self._buttons = []

        self.text.setText(text)
        self.detail.setText(detail)
        for label, payload in choices:
            b = QtWidgets.QPushButton(label)
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(24)
            b.setStyleSheet(self._style(theme.BRASS))
            b.clicked.connect(lambda _=False, p=payload: self._choose(p))
            self._buttons.append(b)
            self._row.addWidget(b)
        self.answered_explicitly = False
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self._timer.start(TIMEOUT_MS)

    def place(self) -> None:
        w = min(max(self.sizeHint().width(), 340), self._host.width() - 40)
        self.setFixedWidth(w)
        self.move((self._host.width() - w) // 2, 18)

    def _choose(self, payload) -> None:
        self._timer.stop()
        self.hide()
        self.accepted.emit(payload)

    def _answered_no(self) -> None:
        self.answered_explicitly = True
        self._dismiss()

    def _dismiss(self) -> None:
        self._timer.stop()
        self.hide()
        self.dismissed.emit()
        self.answered_explicitly = False

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
