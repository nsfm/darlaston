"""Stitching, with the size decided before the work starts.

A mosaic's output resolution is a real trade, not a preference: at full
resolution a seventeen-tile run is 275 megapixels and 1.6 GB, past what most
viewers open comfortably and near what a DNG can address at all. So the
dialog measures the actual geometry from the manifest and says what each
choice costs, rather than offering "quality: high" and letting the operator
find out afterwards.
"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import theme

#: Names and scales. Full is genuinely full sensor resolution; the rest are
#: honest fractions of it rather than invented tiers.
CHOICES = (("Full", 1.0), ("Half", 0.5), ("Quarter", 0.25), ("Preview", 0.125))


class StitchDialog(QtWidgets.QDialog):
    def __init__(self, directory: Path, on_start, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stitch mosaic")
        self.setMinimumWidth(460)
        self._dir = Path(directory)
        self._on_start = on_start
        self._geom = None                 # filled by the survey

        self.where = QtWidgets.QLabel(self._dir.name)
        self.where.setProperty("role", "key")
        self.where.setWordWrap(True)

        self.buttons = QtWidgets.QButtonGroup(self)
        self.rows = QtWidgets.QVBoxLayout()
        self.rows.setSpacing(4)
        self._radios: list[QtWidgets.QRadioButton] = []
        for i, (label, scale) in enumerate(CHOICES):
            b = QtWidgets.QRadioButton(label)
            b.setChecked(scale == 0.5)
            self.buttons.addButton(b, i)
            self._radios.append(b)
            self.rows.addWidget(b)

        self.note = QtWidgets.QLabel("measuring…")
        self.note.setProperty("role", "key")
        self.note.setWordWrap(True)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        self.progress.setStyleSheet(
            f"QProgressBar {{ border: 0; background: {theme.SUNK}; }}"
            f"QProgressBar::chunk {{ background: {theme.BRASS}; }}")

        self.go = QtWidgets.QPushButton("Stitch")
        self.go.clicked.connect(self._start)
        self.go.setEnabled(False)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(self.go)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(12)
        col.addWidget(_label("TILE FOLDER"))
        col.addWidget(self.where)
        col.addWidget(_label("OUTPUT SIZE"))
        col.addLayout(self.rows)
        col.addWidget(self.note)
        col.addWidget(self.progress)
        col.addStretch(1)
        col.addLayout(row)
        self.setStyleSheet(theme.stylesheet())

        self.buttons.idToggled.connect(lambda *_: self._update())
        QtCore.QTimer.singleShot(0, self._survey)

    # ---- geometry --------------------------------------------------------

    def _survey(self) -> None:
        """Read the manifest and size every choice. Cheap: headers only."""
        try:
            from ..process.stitch import plan, survey
            session, positions, shapes = survey(self._dir)
            if len(session.tiles) < 2:
                self.note.setText("A mosaic needs at least two tiles.")
                return
            self._geom = {scale: plan(positions, shapes, scale)
                          for _, scale in CHOICES}
            self._tiles = len(session.tiles)
            self.go.setEnabled(True)
            self._update()
        except Exception as exc:
            self.note.setText(f"Cannot read that folder: {exc}")

    def _scale(self) -> float:
        return CHOICES[max(0, self.buttons.checkedId())][1]

    def _update(self) -> None:
        if not self._geom:
            return
        for (label, scale), radio in zip(CHOICES, self._radios):
            g = self._geom[scale]
            radio.setText(f"{label}   {g['w']} × {g['h']}   "
                          f"{g['megapixels']:.0f} MP   "
                          f"{g['bytes'] / 1e9:.2f} GB")
            radio.setEnabled(g["fits"])
        g = self._geom[self._scale()]
        if not g["fits"]:
            self.note.setText(
                "Past the 4 GB a DNG can address. Choose a smaller size.")
            self.go.setEnabled(False)
            return
        self.go.setEnabled(True)
        self.note.setText(
            f"{self._tiles} tiles. Positions come from the manifest and are "
            f"refined seam by seam; illumination is levelled from the tiles "
            f"themselves.")

    # ---- running ---------------------------------------------------------

    def _start(self) -> None:
        self.go.setEnabled(False)
        self.progress.show()
        self.progress.setRange(0, 0)          # indeterminate until banding
        self.note.setText("registering…")
        self._on_start(self._dir, self._scale(), self)

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.note.setText(f"compositing band {done} of {total}…")

    def finished_with(self, text: str, ok: bool) -> None:
        self.progress.hide()
        self.go.setEnabled(True)
        self.note.setText(text)
        self.note.setStyleSheet(f"color: {theme.DIM if ok else theme.BAD};")


def _label(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setProperty("role", "label")
    return w
