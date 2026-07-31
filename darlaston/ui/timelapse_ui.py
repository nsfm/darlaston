"""Starting and stopping a timelapse.

A dialog rather than a rail panel: a timelapse is a session you set up and
walk away from, not a control you ride. The rail shows its progress in the
status strip; this dialog is only the doorway.
"""
from __future__ import annotations

from PySide6 import QtWidgets

from . import theme

#: A full-resolution frame is ~40 MB; the estimate exists so "every 10s
#: overnight" is priced before it happens rather than discovered at dawn.
_FRAME_BYTES = 40e6


class TimelapseDialog(QtWidgets.QDialog):
    def __init__(self, timelapse, on_start, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Timelapse")
        self.setMinimumWidth(380)
        self._timelapse = timelapse
        self._on_start = on_start

        self.interval = QtWidgets.QSpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setValue(10)
        self.interval.setSuffix(" s")

        self.count = QtWidgets.QSpinBox()
        self.count.setRange(0, 99_999)
        self.count.setValue(120)
        self.count.setSpecialValueText("until stopped")
        self.count.setToolTip("Zero runs until you stop it.")

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow("Every", self.interval)
        form.addRow("Frames", self.count)

        self.estimate = QtWidgets.QLabel("")
        self.estimate.setProperty("role", "key")
        self.estimate.setWordWrap(True)
        self.count.valueChanged.connect(self._update_estimate)
        self.interval.valueChanged.connect(self._update_estimate)

        self.action = QtWidgets.QPushButton()
        self.action.clicked.connect(self._act)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(self.action)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(12)
        col.addLayout(form)
        col.addWidget(self.estimate)
        col.addStretch(1)
        col.addLayout(row)

        self.setStyleSheet(theme.stylesheet())
        self._sync()
        self._update_estimate()

    def _sync(self) -> None:
        running = self._timelapse.running
        self.action.setText("Stop" if running else "Start")
        self.interval.setEnabled(not running)
        self.count.setEnabled(not running)
        if running:
            self.estimate.setText("Running -- progress is in the status bar.")

    def _update_estimate(self) -> None:
        if self._timelapse.running:
            return
        n = self.count.value()
        if n == 0:
            per_hour = 3600 / self.interval.value() * _FRAME_BYTES
            self.estimate.setText(
                f"About {per_hour / 1e9:.1f} GB per hour until stopped.")
            return
        secs = n * self.interval.value()
        span = (f"{secs / 3600:.1f} hours" if secs >= 3600
                else f"{secs / 60:.0f} minutes" if secs >= 90
                else f"{secs:.0f} seconds")
        self.estimate.setText(
            f"About {n * _FRAME_BYTES / 1e9:.1f} GB over {span}."
            + ("  Longer than a dark master lives -- reshoot it midway "
               "or expect later frames uncalibrated." if secs > 8 * 3600
               else ""))

    def _act(self) -> None:
        if self._timelapse.running:
            self._timelapse.stop()
            self._sync()
            self.action.setText("Stopping…")
            self.action.setEnabled(False)
            return
        if self._on_start(self.interval.value(), self.count.value()):
            self.accept()
