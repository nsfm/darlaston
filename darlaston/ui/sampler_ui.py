"""Pick a halo setting by looking at it, in the middle of the merge.

The bound this chooses has no derivable optimum. It depends on the
subject rather than the optics, and the question it answers is which
photograph looks better, so the interface is a comparison and not a
number. Nate's framing, and the right one: better or worse, one or two.

Timing is the whole trick. The merge stops after mapping depth, which is
where the data exists and before the blend, which is the expensive part.
A crop under one setting costs 11 to 28 ms, so every option is rendered
in well under a second and the merge resumes the moment one is clicked.
"""
from __future__ import annotations

import threading

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import N_, _
from ..process import sampler
from . import theme

#: The choices offered, as (label key, slope). Drawn from the range
#: measured across two very different subjects: a smooth carapace wanted
#: about 0.2, a mount of crossing diatoms about 0.6, and 0.35 is what the
#: optics imply for both. "off" is first so the eye starts from what the
#: merge would otherwise have produced.
CHOICES = (
    (N_("sampler.choice.off"), 0.0),
    (N_("sampler.choice.strong"), 0.20),
    (N_("sampler.choice.balanced"), 0.35),
    (N_("sampler.choice.gentle"), 0.60),
)
#: Displayed size of each crop. The crops are 224 px of half-res sensor,
#: shown a little larger so the halo is visible without leaning in.
TILE = 260


class _Tile(QtWidgets.QFrame):
    """One option: its crops stacked, its label, and a click."""

    picked = QtCore.Signal(float)

    def __init__(self, label: str, value: float, images, parent=None):
        super().__init__(parent)
        self._value = value
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(4)
        for im in images:
            lab = QtWidgets.QLabel()
            lab.setPixmap(_pixmap(im))
            lab.setFixedSize(TILE, TILE)
            lab.setScaledContents(True)
            col.addWidget(lab)
        cap = QtWidgets.QLabel(label)
        cap.setAlignment(QtCore.Qt.AlignCenter)
        col.addWidget(cap)

    def mouseReleaseEvent(self, event) -> None:      # noqa: N802 (Qt)
        if event.button() == QtCore.Qt.LeftButton:
            self.picked.emit(self._value)


def _pixmap(luma: np.ndarray) -> QtGui.QPixmap:
    """A half-res luma crop as something Qt can show.

    Normalised per crop rather than globally: these are compared against
    each other, and a stack whose field sits at a quarter of full scale
    would otherwise be judged in the dark.
    """
    lo, hi = np.percentile(luma, (0.5, 99.5))
    v = np.clip((luma - lo) / max(hi - lo, 1e-6), 0, 1)
    u8 = np.ascontiguousarray((v * 255).astype(np.uint8))
    h, w = u8.shape
    img = QtGui.QImage(u8.data, w, h, w, QtGui.QImage.Format_Grayscale8)
    return QtGui.QPixmap.fromImage(img.copy())


class SamplerDialog(QtWidgets.QDialog):
    """The comparison itself. Returns a slope, or None to keep the default."""

    def __init__(self, rendered, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("sampler.title"))
        self.setStyleSheet(theme.stylesheet())
        self._value: float | None = None

        head = QtWidgets.QLabel(_("sampler.intro"))
        head.setWordWrap(True)
        note = QtWidgets.QLabel(_("sampler.note"))
        note.setWordWrap(True)
        note.setObjectName("hint")

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        for key, value in CHOICES:
            if value not in rendered:
                continue
            tile = _Tile(_(key), value, rendered[value], self)
            tile.picked.connect(self._choose)
            row.addWidget(tile)

        skip = QtWidgets.QPushButton(_("sampler.action.skip"))
        skip.clicked.connect(self.reject)
        feet = QtWidgets.QHBoxLayout()
        feet.addWidget(note, 1)
        feet.addWidget(skip)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(head)
        col.addLayout(row)
        col.addLayout(feet)

    def _choose(self, value: float) -> None:
        self._value = float(value)
        self.accept()

    def value(self) -> float | None:
        return self._value


def ask(parent, lumas, depth, solid, levels,
        width=1.0, feather=0.0) -> float | None:
    """Render the options and put them in front of the operator.

    Called on the GUI thread. The merge worker is blocked behind this,
    which is exactly the intent: it has nothing useful to do until the
    answer arrives, and the answer costs a second to prepare.
    """
    slopes = [v for _key, v in CHOICES]
    boxes, rendered = sampler.preview(lumas, depth, solid, slopes,
                                      width=width, feather=feather)
    if not boxes:
        # Nothing with a boundary and depth contrast in it, so there is
        # nothing to compare. Silence is better than four identical crops.
        return None
    dialog = SamplerDialog(rendered, parent)
    dialog.exec()
    return dialog.value()


def bridge(window, signal) -> "callable":
    """A `choose_slope` for `merge`, callable from the merge thread.

    The merge runs in a worker; dialogs may only be built on the GUI
    thread. So the payload crosses on a signal, the worker waits on an
    event, and the answer comes back in a one-slot box. If the window has
    gone away the wait times out and the merge continues with whatever
    the settings already said, because a merge that has read every slice
    should not be lost to a closed dialog.
    """
    def choose(lumas, depth, solid, levels, width=1.0, feather=0.0):
        box: dict = {}
        done = threading.Event()
        signal.emit(("choose", lumas, depth, solid, levels, width, feather,
                     box, done))
        if not done.wait(timeout=600):
            return None
        return box.get("slope")
    return choose
