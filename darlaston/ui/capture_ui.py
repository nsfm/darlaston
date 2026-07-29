"""Shutter, subject, and settings.

The shutter is deliberately the largest control in the window and never moves.
The subject field sits with it rather than in a preferences dialog, because it
is the one piece of context only the operator knows and it is the difference
between an archive and a folder of numbered files.
"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..session.settings import TOKENS, Settings, pictures_dir
from . import theme


class ShutterButton(QtWidgets.QPushButton):
    """Big, bottom right, and honest about what it is doing.

    A capture stops the preview, reconfigures the camera, pulls forty megabytes
    and writes them. That is seconds, not milliseconds, so the control reports
    its stage rather than appearing to hang.
    """

    LABELS = {"idle": "Capture", "exposing": "Exposing…", "writing": "Writing…"}

    def __init__(self) -> None:
        super().__init__("Capture")
        self._state = "idle"
        self.setMinimumHeight(46)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._restyle()

    def set_state(self, state: str) -> None:
        self._state = state
        self.setText(self.LABELS.get(state, "Capture"))
        self.setEnabled(state == "idle" and self.isEnabled() or state == "idle")
        self._restyle()

    def set_available(self, available: bool) -> None:
        self.setEnabled(available and self._state == "idle")
        self._restyle()

    def _restyle(self) -> None:
        busy = self._state != "idle"
        colour = theme.DIM if (busy or not self.isEnabled()) else theme.BRASS
        self.setStyleSheet(
            f"QPushButton {{ border: 1px solid {colour}; border-radius: 4px;"
            f" margin: 1px;"
            f" color: {colour}; font-size: 14px; letter-spacing: 1px;"
            f" background: transparent; }}"
            f"QPushButton:hover:enabled {{ background: rgba(200,155,74,0.10); }}"
            f"QPushButton:pressed:enabled {{ background: rgba(200,155,74,0.20); }}")


class SubjectField(QtWidgets.QWidget):
    """What is on the slide. Feeds the filename and the metadata."""

    changed = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("dopamine arrangement")
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self.changed)
        self.edit.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {theme.LINE}; border-radius: 3px;"
            f" margin: 1px;"
            f" padding: 5px 7px; background: {theme.SUNK}; }}"
            f"QLineEdit:focus {{ border-color: {theme.BRASS}; }}")

        self.slide = QtWidgets.QLineEdit()
        self.slide.setPlaceholderText("slide — mountant, coverslip, source")
        self.slide.setStyleSheet(self.edit.styleSheet())

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addWidget(self.edit)
        col.addWidget(self.slide)

    @property
    def subject(self) -> str:
        return self.edit.text().strip()

    @property
    def slide_note(self) -> str:
        return self.slide.text().strip()


class SettingsDialog(QtWidgets.QDialog):
    """Where captures go and what they are called.

    Live preview of the resulting path, because a filename pattern you cannot
    see the result of is a filename pattern you will get wrong.
    """

    def __init__(self, settings: Settings, setup=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self._settings = settings
        self._setup = setup

        self.root = QtWidgets.QLineEdit(settings.capture_root)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        root_row = QtWidgets.QHBoxLayout()
        root_row.addWidget(self.root, 1)
        root_row.addWidget(browse)

        self.folder = QtWidgets.QLineEdit(settings.folder_pattern)
        self.filename = QtWidgets.QLineEdit(settings.filename_pattern)
        self.keep_slices = QtWidgets.QCheckBox(
            "Keep individual Z-stack slices after stacking")
        self.keep_slices.setChecked(settings.keep_slices)
        self.keep_slices.setToolTip(
            "A 40-tile mosaic at 30 slices each is about 47 GB of raw frames.")

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow("Capture folder", root_row)
        form.addRow("Subfolder", self.folder)
        form.addRow("File name", self.filename)
        form.addRow("", self.keep_slices)

        self.preview = QtWidgets.QLabel()
        self.preview.setProperty("role", "key")
        self.preview.setWordWrap(True)

        tokens = QtWidgets.QLabel("  ".join("{" + k + "}" for k in TOKENS))
        tokens.setProperty("role", "key")
        tokens.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(14)
        col.addLayout(form)
        col.addWidget(_label("AVAILABLE TOKENS"))
        col.addWidget(tokens)
        col.addWidget(_label("EXAMPLE"))
        col.addWidget(self.preview)
        col.addStretch(1)
        col.addWidget(buttons)

        for w in (self.root, self.folder, self.filename):
            w.textChanged.connect(self._update_preview)
        self._update_preview()
        self.setStyleSheet(theme.stylesheet())

    def _browse(self) -> None:
        start = self.root.text() or str(pictures_dir())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Capture folder", start)
        if chosen:
            self.root.setText(chosen)

    def _update_preview(self) -> None:
        probe = Settings(capture_root=self.root.text(),
                         folder_pattern=self.folder.text(),
                         filename_pattern=self.filename.text())
        try:
            path = probe.resolve(setup=self._setup, seq=7, subject="dopamine")
            self.preview.setText(str(path))
        except Exception as exc:
            self.preview.setText(f"invalid pattern — {exc}")

    def _save(self) -> None:
        self._settings.capture_root = self.root.text()
        self._settings.folder_pattern = self.folder.text()
        self._settings.filename_pattern = self.filename.text()
        self._settings.keep_slices = self.keep_slices.isChecked()
        self._settings.save()
        self.accept()


def _label(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setProperty("role", "label")
    return w
