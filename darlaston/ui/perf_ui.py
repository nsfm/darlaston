"""What the live loop is spending its time on.

Built to answer one question: which features cost enough to be worth
making optional on a machine smaller than the one this was written on.
So it reports per-feature milliseconds against the frame budget, sorted
by cost, and says plainly when the total will not fit.

The budget is the honest denominator. At 40 fps a frame is 25 ms, and a
stage costing 8 ms is not "8 ms", it is a third of everything. Percentage
of budget is what tells you whether to care.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..cpu import THREAD_BUDGET, usable_cores
from . import theme
from .widgets import UI_METER


class PerfPanel(QtWidgets.QWidget):
    """A live cost table for the frame loop."""

    def __init__(self) -> None:
        super().__init__()
        self._budget_ms = 1000.0 / 40

        self.summary = QtWidgets.QLabel("")
        self.summary.setWordWrap(True)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["feature", "ms", "% of frame"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        self.hint = QtWidgets.QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "key")

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(self.summary)
        col.addWidget(self.table, 1)
        col.addWidget(self.hint)

    def set_budget(self, fps: int) -> None:
        """The frame budget follows the rate cap, since that is the
        deadline the loop is actually trying to hit."""
        self._budget_ms = 1000.0 / max(fps or 60, 1)

    def set_driver_dropped(self, count: int | None) -> None:
        """Frames the driver threw away before we ever saw them.

        Kept apart from our own drop count because they are opposite
        faults with opposite fixes: ours means the analysis could not
        keep up and wants less work per frame, theirs means the link or
        the host could not deliver and wants a lower rate, a smaller
        preview, or a different USB port. From the outside both look
        like the frame rate sagging.
        """
        self._driver_dropped = count

    def update_costs(self, costs: dict, stats: dict) -> None:
        rows = list(costs.items()) + list(UI_METER.snapshot().items())
        rows = [(name, ms) for name, ms in rows if ms >= 0.005]
        rows.sort(key=lambda kv: kv[1], reverse=True)
        total = sum(ms for _n, ms in rows)

        fps = stats.get("analysed_fps", 0.0)
        delivered = stats.get("delivered", 0) + stats.get("dropped", 0)
        dropped = stats.get("dropped", 0)
        drop_pct = 100.0 * dropped / max(delivered, 1)
        driver = getattr(self, "_driver_dropped", None)
        theirs = ("" if not driver else
                  f" &nbsp;·&nbsp; {driver} dropped by the driver")
        self.summary.setText(
            f"<b>{total:.1f} ms</b> per frame of a {self._budget_ms:.0f} ms "
            f"budget &nbsp;·&nbsp; {fps:.0f} fps &nbsp;·&nbsp; "
            f"{drop_pct:.0f}% of frames dropped{theirs}")

        self.table.setRowCount(len(rows))
        for r, (name, ms) in enumerate(rows):
            share = 100.0 * ms / max(self._budget_ms, 1e-6)
            for c, text in enumerate((name, f"{ms:.2f}", f"{share:.0f}%")):
                item = QtWidgets.QTableWidgetItem(text)
                if c:
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight
                        | QtCore.Qt.AlignmentFlag.AlignVCenter)
                # Anything over a fifth of the budget is a candidate for
                # being made optional, which is the decision this panel
                # exists to inform, so it is coloured rather than left to
                # be worked out from the number.
                if share >= 20:
                    item.setForeground(QtGui.QColor(theme.BRASS))
                self.table.setItem(r, c, item)

        if total > self._budget_ms:
            worst = rows[0][0] if rows else "something"
            # The frame rate is named first because it measured as the
            # larger lever by far: on a two-core machine, capping 40 to 15
            # took the app from 185% of a core to 80%, which no single
            # feature here can match. It also reaches the work this table
            # cannot see, since a frame the camera never sends costs nothing
            # to pull over USB and nothing to demosaic either.
            self.hint.setText(
                f"Over budget, so frames are being dropped to keep up. "
                f"Lowering the frame rate in the status bar is the biggest "
                f"single thing you can do, and it saves work this table "
                f"does not even show. After that, <b>{worst}</b> is the "
                f"largest cost here and turning it off is the cheapest "
                f"test of whether it is the problem.")
        elif total > 0.7 * self._budget_ms:
            self.hint.setText(
                "Close to the budget. This will not hold on a slower "
                "machine.")
        else:
            self.hint.setText(
                "Comfortable. Costs are smoothed over about a second, so "
                "switch a feature on and watch its row settle.")


#: The preview scaling choices: value, label, and one line saying what it
#: does for you. The reasoning behind each -- what was measured, what was
#: rejected and why -- belongs in the source and in TODO.md, not in front
#: of somebody who only wants a smoother preview.
PREVIEW_CHOICES = (
    ("full", "Full",
     "Highest preview quality; may affect framerate."),
    ("fast", "Fast",
     "Improves preview performance; slight loss of quality."),
    ("reduced", "Soft",
     "Improves preview performance; smoother than 'Fast' for certain "
     "scenes."),
)


class PerformanceDialog(QtWidgets.QDialog):
    """How much of the machine the live view is allowed to use.

    Both settings apply the moment they change, because the only way to
    judge a preview is to look at one with the slide you actually
    photograph. Neither touches a captured file.

    Each option gets one plain line about what it does for you. What was
    measured to arrive at these, and what was rejected on the way, lives
    in the source and in TODO.md -- it is our working, and it is not the
    business of somebody who wants a smoother preview.
    """

    def __init__(self, settings, on_change, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Performance")
        self.setStyleSheet(theme.stylesheet())
        self._settings = settings
        self._on_change = on_change

        col = QtWidgets.QVBoxLayout(self)
        col.setSpacing(10)

        col.addWidget(_heading("Preview quality"))
        # The one thing worth saying up front: nothing here reaches the
        # files. Without it, "quality" beside a camera reads as a threat.
        col.addWidget(_note("Does not affect captured images."))
        self._quality = QtWidgets.QButtonGroup(self)
        for value, label, why in PREVIEW_CHOICES:
            button = QtWidgets.QRadioButton(label)
            button.setChecked(settings.preview_quality == value)
            self._quality.addButton(button)
            button.setProperty("value", value)
            col.addWidget(button)
            col.addWidget(_note(why))
        self._quality.buttonToggled.connect(self._quality_changed)

        col.addSpacing(4)
        col.addWidget(_heading("Process threads"))
        row = QtWidgets.QHBoxLayout()
        self.threads = QtWidgets.QComboBox()
        cores = usable_cores()
        self.threads.addItem(f"Automatic ({THREAD_BUDGET})", 0)
        for n in (1, 2, 4, 8, 16, 32):
            if n <= cores and n != THREAD_BUDGET:
                self.threads.addItem(f"{n}", n)
        if cores not in (1, 2, 4, 8, 16, 32):
            self.threads.addItem(f"All ({cores})", cores)
        at = self.threads.findData(settings.cpu_threads)
        self.threads.setCurrentIndex(at if at >= 0 else 0)
        self.threads.currentIndexChanged.connect(self._threads_changed)
        row.addWidget(self.threads)
        row.addStretch(1)
        col.addLayout(row)
        col.addWidget(_note("More threads is not always faster."))

        col.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        col.addWidget(buttons)

    def _quality_changed(self, button, checked: bool) -> None:
        if checked:
            self._settings.preview_quality = button.property("value")
            self._apply()

    def _threads_changed(self) -> None:
        self._settings.cpu_threads = int(self.threads.currentData() or 0)
        self._apply()

    def _apply(self) -> None:
        # Saved on every change rather than on close, because the way this
        # gets used is to switch, look at the slide, and switch again -- and
        # a comparison you have to remember to confirm is one you will lose.
        self._settings.save()
        self._on_change()


def _heading(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text.upper())
    label.setProperty("role", "key")
    return label


def _note(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setProperty("role", "key")
    label.setIndent(18)
    # A word-wrapped QLabel reports a single line's height unless it is told
    # to ask its width first, which silently clips every one of these.
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    return label
