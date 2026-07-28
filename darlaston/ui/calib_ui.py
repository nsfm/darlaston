"""The calibration panel.

Shows what exists for the current configuration and offers to make what does
not. Deliberately a nag rather than a gate: sometimes the light is right and
the diatom is beautiful and calibration can wait. Capture anyway, and the file
records what it did and did not have.

The flat entry usually shows a count rather than a button, because blank fields
are banked automatically while hunting for a subject — most of the time enough
are already in hand before anyone thinks to ask.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import theme


class _Row(QtWidgets.QWidget):
    """One product: its state, and the way to fix it."""

    act = QtCore.Signal()

    def __init__(self, label: str, action: str, hint: str = "") -> None:
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

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        row.addWidget(self.mark)
        row.addWidget(self.name)
        row.addStretch(1)
        row.addWidget(self.detail)
        row.addWidget(self.button)

    def set_state(self, present: bool, detail: str = "",
                  actionable: bool = True) -> None:
        self.mark.setText("●" if present else "○")
        self.mark.setStyleSheet(
            f"color: {theme.GOOD if present else theme.DIM};")
        self.detail.setText(detail)
        self.button.setEnabled(actionable)


class CalibrationPanel(QtWidgets.QWidget):
    """Status for the current configuration, plus the routines."""

    capture_dark = QtCore.Signal()
    build_flat = QtCore.Signal()
    build_lut = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.dark = _Row("dark", "Shoot",
                         "Cap the lens or switch the lamp off, then shoot.\n"
                         "Averaged over several frames; ages after about eight "
                         "hours because this sensor has no cooling.")
        self.flat = _Row("flat", "Build",
                         "Blank fields are banked automatically while you hunt "
                         "for a subject.\nFour at distinct stage positions are "
                         "enough to median away slide debris.")
        self.wb = _Row("white balance", "—",
                       "Measured from the flat. A featureless illuminated "
                       "field is neutral by definition.")
        self.wb.button.setEnabled(False)
        self.lut = _Row("preview profile", "Profile",
                        "Measures what the ISP does to the sensor data, so the "
                        "histogram can report real headroom.\nTakes an "
                        "exposure ramp, so it needs a few seconds.")

        self.dark.act.connect(self.capture_dark)
        self.flat.act.connect(self.build_flat)
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

    def set_status(self, status: dict, banked: int = 0, wanted: int = 4,
                   live: bool = True) -> None:
        self.dark.set_state(status.get("dark", False), actionable=live)
        have_flat = status.get("flat", False)
        # The flat's action only becomes available once enough blank fields
        # have been banked -- otherwise the button is an invitation to fail.
        # Say the counter fills itself. It climbing unprompted is the design
        # working, but it reads as a glitch when nothing says so.
        detail = "" if have_flat else f"{banked}/{wanted} auto"
        if not have_flat and 0 < banked < 3:
            detail += "  3+ rejects debris"
        self.flat.set_state(have_flat, detail,
                            actionable=live and banked >= 2 and not have_flat)
        self.wb.set_state(status.get("white_balance", False),
                          "from flat", actionable=False)
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
