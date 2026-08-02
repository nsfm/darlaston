"""Starting and stopping a timelapse.

A dialog rather than a rail panel: a timelapse is a session you set up and
walk away from, not a control you ride. The rail shows its progress in the
status strip; this dialog is only the doorway.
"""
from __future__ import annotations

from PySide6 import QtWidgets

from ..i18n import _
from . import theme

#: A full-resolution frame is ~40 MB; the estimate exists so "every 10s
#: overnight" is priced before it happens rather than discovered at dawn.
_FRAME_BYTES = 40e6


class TimelapseDialog(QtWidgets.QDialog):
    def __init__(self, timelapse, on_start, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("timelapse.title"))
        self.setMinimumWidth(380)
        self._timelapse = timelapse
        self._on_start = on_start

        self.interval = QtWidgets.QSpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setValue(10)
        # The SI symbol for a second, which is the same in every language,
        # so there is nothing here for a translator to do.
        self.interval.setSuffix(" s")

        self.count = QtWidgets.QSpinBox()
        self.count.setRange(0, 99_999)
        self.count.setValue(120)
        self.count.setSpecialValueText(_("timelapse.count.detail.unlimited"))
        self.count.setToolTip(_("timelapse.count.tooltip"))

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow(_("timelapse.interval.label"), self.interval)
        form.addRow(_("timelapse.count.label"), self.count)

        self.estimate = QtWidgets.QLabel("")
        self.estimate.setProperty("role", "key")
        self.estimate.setWordWrap(True)
        self.count.valueChanged.connect(self._update_estimate)
        self.interval.valueChanged.connect(self._update_estimate)

        self.action = QtWidgets.QPushButton()
        self.action.clicked.connect(self._act)
        close = QtWidgets.QPushButton(_("timelapse.action.close"))
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
        # Both spelled out, so each key is a literal the consistency check
        # can see.
        self.action.setText(_("timelapse.action.stop") if running
                            else _("timelapse.action.start"))
        self.interval.setEnabled(not running)
        self.count.setEnabled(not running)
        if running:
            self.estimate.setText(_("timelapse.estimate.detail.running"))

    def _update_estimate(self) -> None:
        if self._timelapse.running:
            return
        n = self.count.value()
        if n == 0:
            per_hour = 3600 / self.interval.value() * _FRAME_BYTES
            self.estimate.setText(
                _("timelapse.estimate.detail.hourly",
                  size=f"{per_hour / 1e9:.1f}"))
            return
        secs = n * self.interval.value()
        # Rounded here rather than in the catalogue, and each unit its own
        # key: the counts are fractional, so there is no plural form to pick
        # and n_ would have nothing to choose between.
        span = (_("timelapse.span.hours", count=f"{secs / 3600:.1f}")
                if secs >= 3600
                else _("timelapse.span.minutes", count=f"{secs / 60:.0f}")
                if secs >= 90
                else _("timelapse.span.seconds", count=f"{secs:.0f}"))
        size = f"{n * _FRAME_BYTES / 1e9:.1f}"
        # The warning is a whole second sentence rather than something
        # appended, so a translator can put it where their sentence wants it.
        self.estimate.setText(
            _("timelapse.estimate.detail.long", size=size, span=span)
            if secs > 8 * 3600
            else _("timelapse.estimate.detail", size=size, span=span))

    def _act(self) -> None:
        if self._timelapse.running:
            self._timelapse.stop()
            self._sync()
            self.action.setText(_("timelapse.action.stopping"))
            self.action.setEnabled(False)
            return
        if self._on_start(self.interval.value(), self.count.value()):
            self.accept()
