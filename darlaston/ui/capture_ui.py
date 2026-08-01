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
from .framed import FramedDialog


class ShutterButton(QtWidgets.QPushButton):
    """Big, bottom right, and honest about what it is doing.

    A capture stops the preview, reconfigures the camera, pulls forty megabytes
    and writes them. That is seconds, not milliseconds, so the control reports
    its stage rather than appearing to hang.
    """

    LABELS = {"idle": "Capture", "exposing": "Exposing…", "writing": "Writing…"}

    def _label(self, state: str) -> str:
        # Burst states arrive as "exposing 3/16" -- show them as they are,
        # because a sixteen-frame average with a mute progress readout looks
        # exactly like a hang.
        base = (ShutterButton.LABELS.get(state)
                or state[:1].upper() + state[1:] + "…")
        if state == "idle" and self._average > 1:
            return f"{base}  ×{self._average}"
        return base

    def __init__(self) -> None:
        super().__init__("Capture")
        self._state = "idle"
        self._average = 1
        self.setMinimumHeight(46)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._restyle()

    def set_average(self, n: int) -> None:
        """Carry the burst count on the button itself, so a sixteen-frame
        capture is never a surprise at the moment of pressing."""
        self._average = n
        if self._state == "idle":
            self.setText(self._label("idle"))

    def set_state(self, state: str) -> None:
        self._state = state
        self.setText(self._label(state))
        self.setEnabled(state == "idle" and self.isEnabled() or state == "idle")
        self._restyle()

    def set_available(self, available: bool) -> None:
        self.setEnabled(available and self._state == "idle")
        self._restyle()

    def _restyle(self) -> None:
        busy = self._state != "idle"
        colour = theme.DIM if (busy or not self.isEnabled()) else theme.BRASS
        # Right side square: the averaging arrow butts against it to make one
        # pill, so the seam between them must not be rounded.
        self.setStyleSheet(
            f"QPushButton {{ border: 1px solid {colour};"
            f" border-top-left-radius: 4px; border-bottom-left-radius: 4px;"
            f" margin: 1px 0 1px 1px;"
            f" color: {colour}; font-size: 14px; letter-spacing: 1px;"
            f" background: transparent; }}"
            f"QPushButton:hover:enabled {{ background: rgba(200,155,74,0.10); }}"
            f"QPushButton:pressed:enabled {{ background: rgba(200,155,74,0.20); }}")


class ShutterBar(QtWidgets.QWidget):
    """The shutter, its averaging menu, and the last result, as one control.

    Three rail rows became one. The averaging choice is a split-button arrow
    rather than a segmented row, because it is picked rarely and read never;
    the result line lives *under the button inside the same frame*, so a
    capture reports where the eye already is instead of somewhere below.
    """

    triggered = QtCore.Signal()
    average_changed = QtCore.Signal(int)

    CHOICES = (1, 4, 16)

    def __init__(self) -> None:
        super().__init__()
        self.button = ShutterButton()
        self.button.clicked.connect(self.triggered)

        self.arrow = QtWidgets.QToolButton()
        self.arrow.setText("▾")
        self.arrow.setFixedSize(26, 46)
        self.arrow.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.arrow.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.arrow.setToolTip(
            "Average several exposures into one file.\n"
            "Noise falls as the square root: ×16 is two stops cleaner.\n"
            "Hold still for the whole burst -- motion between frames ghosts "
            "the average.")

        self._menu = QtWidgets.QMenu(self)
        self._actions = {}
        group = QtGui.QActionGroup(self._menu)
        group.setExclusive(True)
        for n in self.CHOICES:
            act = self._menu.addAction("single frame" if n == 1
                                       else f"average ×{n}")
            act.setCheckable(True)
            act.setChecked(n == 1)
            group.addAction(act)
            act.triggered.connect(lambda _=False, n=n: self._choose(n))
            self._actions[n] = act
        self.arrow.setMenu(self._menu)

        self.result = QtWidgets.QLabel("")
        self.result.setProperty("role", "key")
        self.result.setWordWrap(True)
        self.result.setVisible(False)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.button, 1)
        row.addWidget(self.arrow)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addLayout(row)
        col.addWidget(self.result)

        self._frames = 1
        self._restyle_arrow()

    @property
    def frames(self) -> int:
        return self._frames

    def _choose(self, n: int) -> None:
        self._frames = n
        self.button.set_average(n)
        self._restyle_arrow()
        self.average_changed.emit(n)

    def _restyle_arrow(self) -> None:
        colour = theme.BRASS if self._frames > 1 else theme.DIM
        self.arrow.setStyleSheet(
            f"QToolButton {{ border: 1px solid {colour}; border-left: 0;"
            f" border-top-right-radius: 4px; border-bottom-right-radius: 4px;"
            f" margin: 1px 1px 1px 0; color: {colour};"
            f" background: transparent; }}"
            f"QToolButton::menu-indicator {{ image: none; }}"
            f"QToolButton:hover {{ background: rgba(200,155,74,0.10); }}")

    # ---- state -----------------------------------------------------------

    def set_state(self, state: str) -> None:
        self.button.set_state(state)
        self.arrow.setEnabled(state == "idle")

    def set_available(self, available: bool) -> None:
        self.button.set_available(available)
        self.arrow.setEnabled(available)

    def set_result(self, text: str, ok: bool = True) -> None:
        self.result.setVisible(bool(text))
        self.result.setText(text)
        self.result.setStyleSheet(
            f"color: {theme.DIM if ok else theme.BAD};")


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
        self.slide.setPlaceholderText("slide -- mountant, coverslip, source")
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


class SettingsDialog(FramedDialog):
    """Where captures go and what they are called.

    Live preview of the resulting path, because a filename pattern you cannot
    see the result of is a filename pattern you will get wrong.
    """

    def __init__(self, settings: Settings, setup=None, parent=None) -> None:
        super().__init__(parent, width=560)
        self.setWindowTitle("Files")
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

        # No ordinary camera ships set to raw only, and until recently this
        # program could not write a photograph at all -- every output was a
        # negative you needed a raw developer to open.
        self.image_format = QtWidgets.QComboBox()
        for value, label in (("both", "Raw and JPEG"),
                             ("raw", "Raw only"),
                             ("jpeg", "JPEG only")):
            self.image_format.addItem(label, value)
        at = self.image_format.findData(settings.image_format)
        self.image_format.setCurrentIndex(at if at >= 0 else 0)
        self.image_format.setToolTip(
            "The JPEG is developed from the levels and white balance the raw "
            "file itself declares, so opening the raw later agrees with it.\n"
            "Mosaic tiles and stack slices always keep their raw whatever "
            "this says: the stitcher and the merge read them back.")

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow("Capture folder", root_row)
        form.addRow("Subfolder", self.folder)
        form.addRow("File name", self.filename)
        form.addRow("Write", self.image_format)
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

        col = self.content
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
        self.finish()

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
            self.preview.setText(f"invalid pattern -- {exc}")

    def _save(self) -> None:
        self._settings.capture_root = self.root.text()
        self._settings.folder_pattern = self.folder.text()
        self._settings.filename_pattern = self.filename.text()
        self._settings.keep_slices = self.keep_slices.isChecked()
        self._settings.image_format = str(self.image_format.currentData())
        self._settings.save()
        self.accept()


def _label(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setProperty("role", "label")
    return w
