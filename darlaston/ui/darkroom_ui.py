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

from ..i18n import N_, _
from . import theme

#: (key, label, hint, default on). Order is roughly cheapest first, which
#: is also the order the worker runs them in. The label and the hint are
#: catalogue keys rather than words: this table is built at import time and
#: read by main.py as well, so the words are looked up where they are shown.
RENDERS = [
    ("wiggle", N_("darkroom.render.wiggle.label"),
     N_("darkroom.render.wiggle.detail"), True),
    ("stereo", N_("darkroom.render.stereo.label"),
     N_("darkroom.render.stereo.detail"), True),
    ("dic", N_("darkroom.render.dic.label"),
     N_("darkroom.render.dic.detail"), True),
    ("mesh", N_("darkroom.render.mesh.label"),
     N_("darkroom.render.mesh.detail"), True),
    ("sirds", N_("darkroom.render.sirds.label"),
     N_("darkroom.render.sirds.detail"), False),
    ("pull", N_("darkroom.render.pull.label"),
     N_("darkroom.render.pull.detail"), True),
    ("turntable", N_("darkroom.render.turntable.label"),
     N_("darkroom.render.turntable.detail"), False),
]


class RenderDialog(QtWidgets.QDialog):
    """Pick which depth renders to make from one finished stack."""

    def __init__(self, directory: Path, run, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("darkroom.render.title"))
        self.setStyleSheet(theme.stylesheet())
        self._run = run
        self._directory = Path(directory)

        head = QtWidgets.QLabel(
            _("darkroom.render.heading", name=self._directory.name))
        head.setWordWrap(True)

        self.boxes = {}
        form = QtWidgets.QVBoxLayout()
        for key, label, hint, on in RENDERS:
            box = QtWidgets.QCheckBox(_(label))
            box.setChecked(on)
            box.setToolTip(_(hint))
            self.boxes[key] = box
            form.addWidget(box)
            note = QtWidgets.QLabel(_(hint))
            note.setProperty("role", "key")
            note.setContentsMargins(22, 0, 0, 6)
            form.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton(_("darkroom.render.action.render"),
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
        self.setWindowTitle(_("darkroom.plate.title"))
        self.setStyleSheet(theme.stylesheet())
        self._run = run
        self._start = Path(start)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        add = QtWidgets.QPushButton(_("darkroom.plate.action.add"))
        add.clicked.connect(self._add)
        drop = QtWidgets.QPushButton(_("darkroom.plate.action.remove"))
        drop.clicked.connect(self._drop)
        # The two arrows carry no words, so there is nothing to translate:
        # up is up in every language this could be read in.
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
        self.title.setPlaceholderText(_("darkroom.plate.caption.placeholder"))
        self.footer = QtWidgets.QLineEdit()
        self.footer.setPlaceholderText(_("darkroom.plate.footer.placeholder"))
        self.columns = QtWidgets.QSpinBox()
        self.columns.setRange(1, 8)
        self.columns.setValue(3)

        form = QtWidgets.QFormLayout()
        form.addRow(_("darkroom.plate.caption.label"), self.title)
        form.addRow(_("darkroom.plate.footer.label"), self.footer)
        form.addRow(_("darkroom.plate.columns.label"), self.columns)

        note = QtWidgets.QLabel(_("darkroom.plate.note.scale"))
        note.setWordWrap(True)
        note.setProperty("role", "key")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton(_("darkroom.plate.action.make"),
                                    QtWidgets.QDialogButtonBox.ButtonRole
                                    .AcceptRole)
        buttons.rejected.connect(self.reject)
        self.go.clicked.connect(self._accept)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(QtWidgets.QLabel(_("darkroom.plate.list.label")))
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
            self, _("darkroom.plate.add.title"), str(self._start))
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
        # `_filter` rather than `_`: the latter is the catalogue lookup, and
        # binding it here would make it a local for the whole function.
        target, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, _("darkroom.plate.save.title"),
            str(self._start / "plate.png"), "PNG (*.png)")
        if not target:
            return
        self.accept()
        self._run(sources, target, self.columns.value(),
                  self.title.text().strip(), self.footer.text().strip())


class ArrangeDialog(QtWidgets.QDialog):
    """Find the specimens in a capture and lay them out in a pattern."""

    STYLES = [
        ("rosette", N_("darkroom.arrange.rosette.label"),
         N_("darkroom.arrange.rosette.detail")),
        ("spiral", N_("darkroom.arrange.spiral.label"),
         N_("darkroom.arrange.spiral.detail")),
        ("rows", N_("darkroom.arrange.rows.label"),
         N_("darkroom.arrange.rows.detail")),
    ]

    def __init__(self, directory: Path, run, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("darkroom.arrange.title"))
        self.setStyleSheet(theme.stylesheet())
        self._run = run
        self._directory = Path(directory)

        head = QtWidgets.QLabel(
            _("darkroom.arrange.heading", name=self._directory.name))
        head.setWordWrap(True)

        self.style = QtWidgets.QComboBox()
        for key, label, hint in self.STYLES:
            self.style.addItem(
                _("darkroom.arrange.style.option", name=_(label),
                  detail=_(hint)), key)
        self.title = QtWidgets.QLineEdit()
        self.title.setPlaceholderText(_("darkroom.arrange.caption.placeholder"))

        form = QtWidgets.QFormLayout()
        form.addRow(_("darkroom.arrange.style.label"), self.style)
        form.addRow(_("darkroom.arrange.caption.label"), self.title)

        note = QtWidgets.QLabel(_("darkroom.arrange.note.isolation"))
        note.setWordWrap(True)
        note.setProperty("role", "key")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        go = buttons.addButton(_("darkroom.arrange.action.arrange"),
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
        # `_filter` rather than `_`, as in PlateDialog: `_` is the catalogue
        # lookup and must not become a local of this function.
        target, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, _("darkroom.arrange.save.title"),
            str(self._directory / "arrangement.png"), "PNG (*.png)")
        if not target:
            return
        self.accept()
        self._run(self._directory, target, self.style.currentData(),
                  self.title.text().strip())
