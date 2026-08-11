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

import numpy as np
from PySide6 import QtCore, QtWidgets

from ..i18n import _
from ..live.driftcal import DriftCalibration
from . import theme
from .framed import FramedDialog


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
    #: Open the tracking-drift ritual.
    calibrate_drift = QtCore.Signal()

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
        self.drift = _Row(_("calib.drift.label"), _("calib.drift.action"),
                          _("calib.drift.tooltip"))
        self.drift.act.connect(self.calibrate_drift)

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
        for w in (self.dark, self.flat, self.wb, self.lut, self.drift,
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


class DriftDialog(FramedDialog):
    """The tracking-drift ritual: closed passes over one feature.

    The operator's re-centring is the ground truth -- a pass that truly
    closes has zero net travel, so the residual tracked position is the
    rolling shutter's accumulated lie, and two passes fit the readout
    time. A third pass runs with the correction live and reports both
    numbers, so the calibration demonstrates itself before it is saved.

    Fed frames by the main window while open; owns nothing but a
    `DriftCalibration` and the numbers it produces.
    """

    #: Measuring passes before the fit. Two is the floor for a fit that
    #: is more than one division; the verify pass makes three.
    PASSES = 2
    #: How far a pass should travel, in fields, before Centred means
    #: anything. Advisory in the copy; enforced only by MIN_EVIDENCE.
    FIELDS = 3
    #: The relocalizer's texture floor, reused: the ritual needs real
    #: ground for the same reason a probe does. Blank glass cannot
    #: testify about drift.
    TEXTURE_FLOOR = 4.0

    def __init__(self, pipeline, camera, parent=None) -> None:
        super().__init__(parent, width=440)
        self.setWindowTitle(_("calib.drift.title"))
        self._pipeline = pipeline
        self._camera = camera
        self._cal: DriftCalibration | None = None
        self._verify: DriftCalibration | None = None
        self._fitted: float | None = None
        self._drift_after: float | None = None
        self._last_y: float | None = None
        self._ready = False
        self.saved = False

        col = self.content
        col.setSpacing(10)
        self.instruction = QtWidgets.QLabel(_("calib.drift.intro"))
        self.instruction.setWordWrap(True)
        self.travel = QtWidgets.QLabel("")
        self.travel.setProperty("role", "key")
        self.action = QtWidgets.QPushButton(_("calib.drift.start"))
        self.action.setEnabled(False)
        self.action.clicked.connect(self._pressed)
        self.result = QtWidgets.QLabel("")
        self.result.setWordWrap(True)
        col.addWidget(self.instruction)
        col.addWidget(self.travel)
        col.addWidget(self.action)
        col.addWidget(self.result)
        col.addStretch(1)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Close)
        self.buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        col.addWidget(self.buttons)
        self.finish()

    # ---- the feed --------------------------------------------------------

    def observe(self, s) -> None:
        """One live frame, from the main window's fan-out."""
        pos, tracking = s.stage_pos, s.stage_tracking
        if self._cal is None:
            # Waiting to start: the button arms over tracked, textured
            # ground -- blank glass cannot testify about drift.
            textured = (s.track_small is not None
                        and float(np.std(s.track_small)) >= self.TEXTURE_FLOOR)
            self._ready = bool(tracking and pos is not None and textured)
            if pos is not None:
                self._pos_y = pos[1]
            self.action.setEnabled(self._ready)
            if not self._ready:
                self.travel.setText(_("calib.drift.waiting"))
            else:
                self.travel.setText("")
            return
        if pos is None or not tracking:
            self._last_y = None
            return
        y = pos[1]
        if self._last_y is not None:
            dy = y - self._last_y
            fps = s.stats.get("analysed_fps", 30.0) or 30.0
            live = self._verify if self._verify is not None else self._cal
            live.observe(dy * fps, dy)
            self.travel.setText(_("calib.drift.travel",
                                  fields=f"{live.fields_travelled:.1f}"))
        self._last_y = y
        self._pos_y = y

    # ---- the ritual ------------------------------------------------------

    def _pressed(self) -> None:
        if self._cal is None:
            self._cal = DriftCalibration(self._frame_h())
            self._cal.begin_pass(self._pos_y if hasattr(self, "_pos_y")
                                 else 0.0)
            self._last_y = None
            self.action.setText(_("calib.drift.centred"))
            self.instruction.setText(_("calib.drift.pass",
                                       fields=self.FIELDS))
            return
        live = self._verify if self._verify is not None else self._cal
        drift = live.mark(getattr(self, "_pos_y", 0.0))
        if self._verify is not None:
            self._finish(drift)
            return
        if drift is None:
            self.instruction.setText(_("calib.drift.short",
                                       fields=self.FIELDS))
            self._cal.begin_pass(getattr(self, "_pos_y", 0.0))
            return
        done = len(self._cal.passes)
        if done < self.PASSES:
            self.instruction.setText(_("calib.drift.again",
                                       fields=self.FIELDS))
            self._cal.begin_pass(getattr(self, "_pos_y", 0.0))
            return
        fitted = self._cal.fit()
        if fitted is None:
            self.instruction.setText(_("calib.drift.failed"))
            self._cal = None
            self.action.setText(_("calib.drift.start"))
            return
        self._fitted = fitted
        self._pipeline.set_readout(fitted)
        self._verify = DriftCalibration(self._frame_h())
        self._verify.begin_pass(getattr(self, "_pos_y", 0.0))
        self._last_y = None
        self.instruction.setText(_("calib.drift.verify", fields=self.FIELDS))

    def _finish(self, drift_after: float | None) -> None:
        before = (sum(abs(d) for d, _x in self._cal.passes)
                  / max(len(self._cal.passes), 1))
        after = abs(drift_after) if drift_after is not None else 0.0
        self._drift_after = after
        self.instruction.setText(_("calib.drift.done"))
        self.result.setText(_("calib.drift.result",
                              before=f"{before:.0f}", after=f"{after:.0f}",
                              us=f"{self._fitted:.0f}"))
        self.action.setEnabled(False)
        self.buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def _frame_h(self) -> int:
        return getattr(self, "_h", 1216)

    def set_frame_height(self, h: int) -> None:
        self._h = int(h)

    def _save(self) -> None:
        self._camera.readout_us = float(self._fitted or 0.0)
        self.saved = True
        self.accept()

    def reject(self) -> None:
        # Walked away: the pipeline goes back to the profile's truth,
        # whatever this session's experiment said.
        self._pipeline.set_readout(self._camera.readout_us)
        super().reject()
