"""The darkroom: what happens to captures after the microscope is done.

Kept apart from Capture on purpose. Capture holds the things you set
before or during a session -- where files land, whether white balance is
written, the timelapse -- and the darkroom holds the things you do to
finished work afterwards. They were sharing a menu and it read as a
junk drawer.

The render dialog exists because the full set of depth renders takes
minutes and produces eight files. Offering them as checkboxes lets the
operator ask for the wobble alone, which takes seconds.
"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import theme

#: (key, label, hint, default on). Order is roughly cheapest first, which
#: is also the order the worker runs them in.
RENDERS = [
    ("wiggle", "Wigglegram", "a looping wobble -- wiggle.webm and .webp",
     True),
    ("stereo", "Stereo pair and anaglyph",
     "crossed-eye pair, plus red/cyan", True),
    ("dic", "Darlaston Inferred Contrast",
     "relief shaded from depth -- looks like DIC, is not DIC", True),
    ("mesh", "Printable mesh", "model.ply, watertight, colour per vertex",
     True),
    ("sirds", "Autostereogram", "a Magic Eye of the subject", False),
    ("pull", "Focus pull", "the focal plane drifting through, on video",
     True),
    ("turntable", "Turntable", "the surface lit from an orbiting light",
     False),
]


class RenderDialog(QtWidgets.QDialog):
    """Pick which depth renders to make from one finished stack."""

    def __init__(self, directory: Path, run, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Render depth")
        self.setStyleSheet(theme.stylesheet())
        self._run = run
        self._directory = Path(directory)

        head = QtWidgets.QLabel(
            f"<b>{self._directory.name}</b><br>"
            "Everything here is synthesised from the stack's depth map.")
        head.setWordWrap(True)

        self.boxes = {}
        form = QtWidgets.QVBoxLayout()
        for key, label, hint, on in RENDERS:
            box = QtWidgets.QCheckBox(label)
            box.setChecked(on)
            box.setToolTip(hint)
            self.boxes[key] = box
            form.addWidget(box)
            note = QtWidgets.QLabel(hint)
            note.setProperty("role", "key")
            note.setContentsMargins(22, 0, 0, 6)
            form.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Render",
                                    QtWidgets.QDialogButtonBox.ButtonRole
                                    .AcceptRole)
        buttons.rejected.connect(self.reject)
        self.go.clicked.connect(self._accept)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(head)
        col.addSpacing(6)
        col.addLayout(form)
        col.addStretch(1)
        col.addWidget(buttons)
        self.resize(430, 460)

    def _accept(self) -> None:
        wanted = [k for k, b in self.boxes.items() if b.isChecked()]
        if not wanted:
            return
        self.accept()
        self._run(self._directory, wanted)


class PlateDialog(QtWidgets.QDialog):
    """Arrange finished captures onto one printable sheet."""

    def __init__(self, start: Path, run, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Make a plate")
        self.setStyleSheet(theme.stylesheet())
        self._run = run
        self._start = Path(start)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        add = QtWidgets.QPushButton("Add…")
        add.clicked.connect(self._add)
        drop = QtWidgets.QPushButton("Remove")
        drop.clicked.connect(self._drop)
        up = QtWidgets.QPushButton("↑")
        up.clicked.connect(lambda: self._move(-1))
        down = QtWidgets.QPushButton("↓")
        down.clicked.connect(lambda: self._move(1))
        row = QtWidgets.QHBoxLayout()
        for b in (add, drop, up, down):
            b.setProperty("role", "seg")
            row.addWidget(b)
        row.addStretch(1)

        self.title = QtWidgets.QLineEdit()
        self.title.setPlaceholderText("Plate I -- arrangement, 25×/0.65")
        self.footer = QtWidgets.QLineEdit()
        self.footer.setPlaceholderText("collection, date, mountant…")
        self.columns = QtWidgets.QSpinBox()
        self.columns.setRange(1, 8)
        self.columns.setValue(3)

        form = QtWidgets.QFormLayout()
        form.addRow("Title", self.title)
        form.addRow("Footer", self.footer)
        form.addRow("Columns", self.columns)

        note = QtWidgets.QLabel(
            "Each cell gets a scale bar computed from the optics recorded "
            "in that file. Files captured without a pixel pitch set get no "
            "bar rather than a guessed one.")
        note.setWordWrap(True)
        note.setProperty("role", "key")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Make plate…",
                                    QtWidgets.QDialogButtonBox.ButtonRole
                                    .AcceptRole)
        buttons.rejected.connect(self.reject)
        self.go.clicked.connect(self._accept)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(QtWidgets.QLabel("Captures, in the order they appear:"))
        col.addWidget(self.list, 1)
        col.addLayout(row)
        col.addSpacing(8)
        col.addLayout(form)
        col.addWidget(note)
        col.addWidget(buttons)
        self.resize(520, 520)
        self._add()

    def _add(self) -> None:
        picked = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Stack, mosaic or capture folder", str(self._start))
        if picked:
            self.list.addItem(picked)
            self._start = Path(picked).parent

    def _drop(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.list.count():
            return
        self.list.insertItem(target, self.list.takeItem(row))
        self.list.setCurrentRow(target)

    def _accept(self) -> None:
        sources = [self.list.item(i).text()
                   for i in range(self.list.count())]
        if not sources:
            return
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save plate", str(self._start / "plate.png"),
            "PNG (*.png)")
        if not target:
            return
        self.accept()
        self._run(sources, target, self.columns.value(),
                  self.title.text().strip(), self.footer.text().strip())


class ArrangeDialog(QtWidgets.QDialog):
    """Find the specimens in a capture and lay them out in a pattern."""

    STYLES = [
        ("rosette", "Rosette", "radiating from a centre, sizes balanced"),
        ("spiral", "Spiral", "winding outward"),
        ("rows", "Rows", "a taxonomic plate, graded by size"),
    ]

    def __init__(self, directory: Path, run, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Arrange specimens")
        self.setStyleSheet(theme.stylesheet())
        self._run = run
        self._directory = Path(directory)

        head = QtWidgets.QLabel(
            f"<b>{self._directory.name}</b><br>"
            "Cuts each frustule out and lays them out -- what Darlaston did "
            "with a bristle.")
        head.setWordWrap(True)

        self.style = QtWidgets.QComboBox()
        for key, label, hint in self.STYLES:
            self.style.addItem(f"{label} -- {hint}", key)
        self.title = QtWidgets.QLineEdit()
        self.title.setPlaceholderText("An arrangement")

        form = QtWidgets.QFormLayout()
        form.addRow("Pattern", self.style)
        form.addRow("Title", self.title)

        note = QtWidgets.QLabel(
            "Only specimens it can isolate are used. A crowded field where "
            "valves overlap may yield few or none -- that is reported "
            "rather than guessed at.")
        note.setWordWrap(True)
        note.setProperty("role", "key")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        go = buttons.addButton("Arrange…",
                               QtWidgets.QDialogButtonBox.ButtonRole
                               .AcceptRole)
        buttons.rejected.connect(self.reject)
        go.clicked.connect(self._accept)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(head)
        col.addSpacing(6)
        col.addLayout(form)
        col.addWidget(note)
        col.addStretch(1)
        col.addWidget(buttons)
        self.resize(440, 260)

    def _accept(self) -> None:
        target, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save arrangement",
            str(self._directory / "arrangement.png"), "PNG (*.png)")
        if not target:
            return
        self.accept()
        self._run(self._directory, target, self.style.currentData(),
                  self.title.text().strip())
