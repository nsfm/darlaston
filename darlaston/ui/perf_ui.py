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

    def update_costs(self, costs: dict, stats: dict) -> None:
        rows = list(costs.items()) + list(UI_METER.snapshot().items())
        rows = [(name, ms) for name, ms in rows if ms >= 0.005]
        rows.sort(key=lambda kv: kv[1], reverse=True)
        total = sum(ms for _n, ms in rows)

        fps = stats.get("analysed_fps", 0.0)
        delivered = stats.get("delivered", 0) + stats.get("dropped", 0)
        dropped = stats.get("dropped", 0)
        drop_pct = 100.0 * dropped / max(delivered, 1)
        self.summary.setText(
            f"<b>{total:.1f} ms</b> per frame of a {self._budget_ms:.0f} ms "
            f"budget &nbsp;·&nbsp; {fps:.0f} fps &nbsp;·&nbsp; "
            f"{drop_pct:.0f}% of frames dropped")

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


#: The preview scaling choices, in the order they are offered: value, label,
#: and the honest sentence about what it costs and what it takes away. The
#: milliseconds are measured on a 1824x1216 preview fitted to a 1039 px view.
PREVIEW_CHOICES = (
    ("full", "Full detail",
     "The frame reduced directly, correctly anti-aliased. The sharpest "
     "preview and by far the most work: about 4 to 9 ms of a 25 ms frame, "
     "on the same thread that has to stay responsive to you. Choose it "
     "when you want to look at the actual pixels rather than the field."),
    ("fast", "Fast (default)",
     "Fitted in a single step, several milliseconds less. It samples the "
     "frame rather than averaging it, which can alias detail near the "
     "limit of what the preview resolves -- but on a real diatom at 16x it "
     "came within 0.8 of 255 levels of Full detail, and moving the stage "
     "made it churn only 6% more. Free on most subjects, which is why it "
     "is the default. Worth checking against Full detail at high "
     "magnification, where fine detail sits closer to that limit."),
    ("reduced", "Softer",
     "Reduced by exactly half first, which is the only cheap reduction "
     "available, then fitted to the window. The cheapest, and the calmest "
     "of the three under motion, but visibly the softest: a third of the "
     "fine detail is gone before the window is fitted at all, and more "
     "than that on a large window, which shows plainly on areolae and "
     "striae. For when Fast is still costing more than you can spare."),
)


class PerformanceDialog(QtWidgets.QDialog):
    """Where the CPU goes, and how much of it you would rather spend.

    Both settings here are real trades rather than a fast-or-slow slider,
    so each one says what it costs and what it takes away, and both apply
    to the live view the moment they change. That is the point: the only
    way to judge a preview is to look at one, with the slide you actually
    photograph, and it should be possible to look at all three inside a
    minute without restarting anything.
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
        col.addWidget(_heading("Processor threads"))
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
        col.addWidget(_note(
            "More threads is not faster here. The live loop's work is small "
            "and memory-bound, so spreading it wider costs more in handing "
            "it out than it saves: measured on this machine, sixteen threads "
            "burned a third more of the processor than four and analysed a "
            "frame no quicker. Fewer than four does start costing real time."))

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
