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
            self.hint.setText(
                f"Over budget. Frames are being dropped to keep up, and "
                f"<b>{worst}</b> is the largest single cost. Turning it "
                f"off is the cheapest test of whether it is the problem.")
        elif total > 0.7 * self._budget_ms:
            self.hint.setText(
                "Close to the budget. This will not hold on a slower "
                "machine.")
        else:
            self.hint.setText(
                "Comfortable. Costs are smoothed over about a second, so "
                "switch a feature on and watch its row settle.")
