"""The calibration panel.

Shows what exists for the current configuration and offers to make what does
not. Deliberately a nag rather than a gate: sometimes the light is right and
the diatom is beautiful and calibration can wait. Capture anyway, and the file
records what it did and did not have.

The flat entry is a small mode rather than one button: collecting blank
fields freezes the preview for a second at a time, and a flat is only valid
at the illumination it was shot under, so it happens when the operator says
so and not whenever the view happens to look empty.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..i18n import _
from . import theme


class _Row(QtWidgets.QWidget):
    """One product: its state, and the way to fix it."""

    act = QtCore.Signal()
    act2 = QtCore.Signal()

    def __init__(self, label: str, action: str, hint: str = "",
                 second: str = "", second_hint: str = "") -> None:
        super().__init__()
        self.mark = QtWidgets.QLabel("○")
        self.mark.setFixedWidth(12)
        self.name = QtWidgets.QLabel(label)
        self.name.setProperty("role", "key")
        self.detail = QtWidgets.QLabel("")
        self.detail.setProperty("role", "key")
        self.button = QtWidgets.QPushButton(action)
        self.button.setProperty("role", "seg")
        self.button.setFixedWidth(64)
        self.button.setToolTip(hint)
        self.button.clicked.connect(self.act)

        # A second action, for the one row that genuinely has two jobs:
        # gathering blank fields and then combining them. One button
        # cannot offer both, and cycling a single button through them
        # hides whichever is not showing.
        self.second: QtWidgets.QPushButton | None = None
        if second:
            self.second = QtWidgets.QPushButton(second)
            self.second.setProperty("role", "seg")
            self.second.setFixedWidth(64)
            self.second.setToolTip(second_hint)
            self.second.clicked.connect(self.act2)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        row.addWidget(self.mark)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.detail)
        row.addWidget(self.button)
        if self.second is not None:
            row.addWidget(self.second)

    def set_state(self, present: bool, detail: str = "",
                  actionable: bool = True) -> None:
        self.mark.setText("●" if present else "○")
        self.mark.setStyleSheet(
            f"color: {theme.GOOD if present else theme.DIM};")
        self.detail.setText(detail)
        self.button.setEnabled(actionable)


class CalibrationButton(QtWidgets.QPushButton):
    """One rail row that is both the status and the way in.

    Calibration is a start-of-session ritual: you do it, then you shoot for
    an hour. It does not deserve permanent residency in the rail, but its
    *state* does -- capturing against a missing flat should be a visible
    choice rather than a silent one. So the button reports what exists and
    opens the panel that fixes what does not.
    """

    ORDER = (("dark", "dark"), ("flat", "flat"),
             ("white_balance", "wb"), ("preview_lut", "profile"))

    def __init__(self) -> None:
        super().__init__(_("calib.button.label"))
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._missing: list[str] = []
        self._busy = False
        self._restyle()

    def set_status(self, status: dict, busy: bool = False) -> None:
        self._missing = [label for key, label in self.ORDER
                         if not status.get(key, False)]
        self._busy = busy
        # A count, not a list. The rail is 258 px wide and "no flat, wb,
        # profile" elided to "no flat, wb, profil" -- which is worse than a
        # number, because a truncated list looks like information and is not.
        # Which ones are missing is the panel's job; this is the summary.
        have = len(self.ORDER) - len(self._missing)
        if busy:
            self.setText(_("calib.button.working"))
        elif not self._missing:
            self.setText(_("calib.button.complete"))
        else:
            self.setText(_("calib.button.progress", have=have,
                           total=len(self.ORDER)))
        self.setToolTip(_("calib.button.tooltip"))
        self._restyle()

    def _restyle(self) -> None:
        colour = (theme.BRASS if self._busy
                  else theme.DIM if not self._missing else theme.BRASS)
        self.setStyleSheet(
            f"QPushButton {{ border: 1px solid {theme.LINE};"
            f" border-radius: 3px; margin: 1px; padding: 5px 8px;"
            f" text-align: left; color: {colour}; background: transparent; }}"
            f"QPushButton:hover {{ border-color: {theme.BRASS}; }}")


class CalibrationPanel(QtWidgets.QWidget):
    """Status for the current configuration, plus the routines."""

    capture_dark = QtCore.Signal()
    build_flat = QtCore.Signal()
    build_lut = QtCore.Signal()
    #: Take one blank field, now. A press rather than a mode: the
    #: automatic version could not work, because deciding the view had
    #: moved needed the tracker, and the tracker cannot follow empty glass.
    bank_flat = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.dark = _Row(_("calib.dark.label"), _("calib.dark.action"),
                         _("calib.dark.tooltip"))
        self.flat = _Row(_("calib.flat.label"), _("calib.flat.action.bank"),
                         _("calib.flat.tooltip"),
                         second=_("calib.flat.action.build"),
                         second_hint=_("calib.flat.build.tooltip"))
        self.wb = _Row(_("calib.wb.label"), _("calib.wb.action"),
                       _("calib.wb.tooltip"))
        self.wb.button.setEnabled(False)
        self.lut = _Row(_("calib.lut.label"), _("calib.lut.action"),
                        _("calib.lut.tooltip"))

        self.dark.act.connect(self.capture_dark)
        self.flat.act.connect(self._on_flat)
        self.flat.act2.connect(self.build_flat)
        self.lut.act.connect(self.build_lut)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        self.progress.setStyleSheet(
            f"QProgressBar {{ border: 0; background: {theme.SUNK}; }}"
            f"QProgressBar::chunk {{ background: {theme.BRASS}; }}")

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("role", "key")
        self.status.setWordWrap(True)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        for w in (self.dark, self.flat, self.wb, self.lut,
                  self.progress, self.status):
            col.addWidget(w)

    # ---- state -----------------------------------------------------------

    def _on_flat(self) -> None:
        self.bank_flat.emit()

    def set_status(self, status: dict, banked: int = 0, wanted: int = 4,
                   live: bool = True, collecting: bool = False) -> None:
        self.dark.set_state(status.get("dark", False), actionable=live)
        have_flat = status.get("flat", False)
        counts = {"banked": banked, "wanted": wanted}

        # Two jobs, two buttons. Bank stays available until there are
        # enough; Build appears once a median means anything, which is two
        # -- and says so at two, because a median of two rejects nothing.
        if have_flat:
            detail = ""
        elif banked == 0:
            detail = _("calib.flat.detail.how")
        elif banked >= wanted:
            detail = _("calib.flat.detail.ready", **counts)
        elif banked >= 2:
            detail = _("calib.flat.detail.debris", **counts)
        else:
            detail = _("calib.flat.detail.count", **counts)

        self.flat.button.setEnabled(live and not have_flat
                                    and banked < wanted)
        if self.flat.second is not None:
            self.flat.second.setEnabled(live and not have_flat and banked >= 2)
        self.flat.set_state(have_flat, detail, actionable=self.flat.button.isEnabled())
        self.wb.set_state(status.get("white_balance", False),
                          _("calib.wb.detail"), actionable=False)
        self.lut.set_state(status.get("preview_lut", False), actionable=live)

    def set_progress(self, progress) -> None:
        if progress.finished:
            self.progress.hide()
            self.status.setText(progress.message)
            self.status.setStyleSheet(
                f"color: {theme.DIM if progress.ok else theme.BAD};")
            return
        self.progress.show()
        self.progress.setRange(0, progress.total or 0)
        self.progress.setValue(progress.done)
        self.status.setText(progress.message)
        self.status.setStyleSheet(f"color: {theme.BRASS};")
