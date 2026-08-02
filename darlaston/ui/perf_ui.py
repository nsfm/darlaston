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
from ..i18n import N_, _
from . import theme
from .framed import FramedDialog
from .widgets import UI_METER


class PerfPanel(QtWidgets.QWidget):
    """A live cost table for the frame loop."""

    def __init__(self) -> None:
        super().__init__()
        self._budget_ms = 1000.0 / 40

        self.summary = QtWidgets.QLabel("")
        self.summary.setWordWrap(True)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [_("perf.table.heading.feature"), _("perf.table.heading.ms"),
             _("perf.table.heading.share")])
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
        theirs = ("" if not driver
                  else _("perf.summary.detail.driver", count=driver))
        # Every number is rounded here, so the catalogue carries words and
        # not format specs.
        self.summary.setText(
            _("perf.summary.detail", total=f"{total:.1f}",
              budget=f"{self._budget_ms:.0f}", fps=f"{fps:.0f}",
              dropped=f"{drop_pct:.0f}", driver=theirs))

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
            worst = rows[0][0] if rows else _("perf.hint.detail.unnamed")
            # The frame rate is named first because it measured as the
            # larger lever by far: on a two-core machine, capping 40 to 15
            # took the app from 185% of a core to 80%, which no single
            # feature here can match. It also reaches the work this table
            # cannot see, since a frame the camera never sends costs nothing
            # to pull over USB and nothing to demosaic either.
            self.hint.setText(_("perf.hint.detail.over", worst=worst))
        elif total > 0.7 * self._budget_ms:
            self.hint.setText(_("perf.hint.detail.close"))
        else:
            self.hint.setText(_("perf.hint.detail.comfortable"))


#: The preview scaling choices: value, label, and one line saying what it
#: does for you. The reasoning behind each -- what was measured, what was
#: rejected and why -- belongs in the source and in TODO.md, not in front
#: of somebody who only wants a smoother preview. The label and the line
#: are catalogue keys, because this table is built at import time.
PREVIEW_CHOICES = (
    ("full", N_("perf.preview.full.label"), N_("perf.preview.full.detail")),
    ("fast", N_("perf.preview.fast.label"), N_("perf.preview.fast.detail")),
    ("reduced", N_("perf.preview.reduced.label"),
     N_("perf.preview.reduced.detail")),
)


class PerformanceDialog(FramedDialog):
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
        super().__init__(parent, width=520)
        self.setWindowTitle(_("perf.title"))
        self._settings = settings
        self._on_change = on_change

        col = self.content
        col.setSpacing(10)

        col.addWidget(_heading(_("perf.preview.heading")))
        # The one thing worth saying up front: nothing here reaches the
        # files. Without it, "quality" beside a camera reads as a threat.
        col.addWidget(_note(_("perf.preview.note.captures")))
        self._quality = QtWidgets.QButtonGroup(self)
        for value, label, why in PREVIEW_CHOICES:
            button = QtWidgets.QRadioButton(_(label))
            button.setChecked(settings.preview_quality == value)
            self._quality.addButton(button)
            button.setProperty("value", value)
            col.addWidget(button)
            col.addWidget(_note(_(why)))
        self._quality.buttonToggled.connect(self._quality_changed)

        col.addSpacing(4)
        col.addWidget(_heading(_("perf.threads.heading")))
        row = QtWidgets.QHBoxLayout()
        self.threads = QtWidgets.QComboBox()
        cores = usable_cores()
        self.threads.addItem(
            _("perf.threads.option.automatic", n=THREAD_BUDGET), 0)
        for n in (1, 2, 4, 8, 16, 32):
            if n <= cores and n != THREAD_BUDGET:
                # A bare number, so there is nothing here to translate.
                self.threads.addItem(f"{n}", n)
        if cores not in (1, 2, 4, 8, 16, 32):
            self.threads.addItem(_("perf.threads.option.all", n=cores), cores)
        at = self.threads.findData(settings.cpu_threads)
        self.threads.setCurrentIndex(at if at >= 0 else 0)
        self.threads.currentIndexChanged.connect(self._threads_changed)
        row.addWidget(self.threads)
        row.addStretch(1)
        col.addLayout(row)
        col.addWidget(_note(_("perf.threads.note.diminishing")))

        col.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        col.addWidget(buttons)
        self.finish()

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
