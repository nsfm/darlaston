"""Installing a camera vendor's SDK, from inside the application.

The screen a first-time user actually hits. It shows what will be
downloaded and from where before anything happens, because this is a
large proprietary archive from a third party and starting that quietly
on someone's behalf is not a thing to do.

Brands without a verified direct link get their download page instead of
a guessed URL, with the destination directory spelled out so the manual
route is as clear as the automatic one.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..camera import sdk_install
from ..i18n import _
from . import theme


class SdkDialog(QtWidgets.QDialog):
    """Choose a vendor, see the terms of the download, fetch it."""

    progress = QtCore.Signal(int, int)
    finished_with = QtCore.Signal(object, str)   # (path or None, message)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("sdk.title"))
        self.setStyleSheet(theme.stylesheet())
        self._worker: threading.Thread | None = None

        head = QtWidgets.QLabel(_("sdk.heading"))
        head.setWordWrap(True)

        self.picker = QtWidgets.QComboBox()
        for source in sdk_install.sources():
            # The brand's own name is data and never translated; only the
            # note beside it is. Both branches spelled out, so the key is a
            # literal the consistency check can see.
            self.picker.addItem(
                source.label if source.automatic
                else _("sdk.brand.option.manual", label=source.label),
                source.brand)
        self.picker.currentIndexChanged.connect(self._describe)

        self.detail = QtWidgets.QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setProperty("role", "key")
        self.detail.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.detail.setOpenExternalLinks(True)
        self.detail.setMinimumHeight(96)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.hide()

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "key")

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        self.go = self.buttons.addButton(
            _("sdk.action.download"),
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.clicked.connect(self._start)
        self.buttons.rejected.connect(self.reject)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(head)
        col.addSpacing(8)
        col.addWidget(QtWidgets.QLabel(_("sdk.brand.label")))
        col.addWidget(self.picker)
        col.addWidget(self.detail)
        col.addWidget(self.bar)
        col.addWidget(self.status)
        col.addStretch(1)
        col.addWidget(self.buttons)
        self.resize(560, 400)

        self.progress.connect(self._on_progress)
        self.finished_with.connect(self._on_done)
        self._describe()

    # ---- presentation ----------------------------------------------------

    def _current(self) -> sdk_install.Source:
        return sdk_install.find(self.picker.currentData())

    def _describe(self) -> None:
        source = self._current()
        already = source.brand in sdk_install.installed()
        where = sdk_install.INSTALL_ROOT / source.brand
        lines = []
        if already:
            lines.append(_("sdk.detail.already"))
        if source.automatic:
            lines.append(_("sdk.detail.automatic", size=source.approx_mb,
                           host=source.url.split("/")[2], where=where))
        else:
            lines.append(_("sdk.detail.manual", label=source.label,
                           page=source.page, where=where))
        if source.note:
            lines.append(source.note)
        self.detail.setText("<br><br>".join(lines))
        # A brand we cannot fetch gets a button that still does
        # something, rather than a greyed-out one that reads as broken.
        # Both keys spelled out, so the consistency check can see them.
        self.go.setText(_("sdk.action.download") if source.automatic
                        else _("sdk.action.page"))
        self.go.setEnabled(self._worker is None)

    # ---- the work --------------------------------------------------------

    def _start(self) -> None:
        source = self._current()
        if self._worker is not None:
            return
        if not source.automatic:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(source.page))
            where = sdk_install.INSTALL_ROOT / source.brand
            where.mkdir(parents=True, exist_ok=True)
            self.status.setText(_("sdk.status.opened", where=where))
            return
        self.go.setEnabled(False)
        self.picker.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.status.setText(
            _("sdk.status.contacting", host=source.url.split("/")[2]))

        def work():
            try:
                path = sdk_install.download(
                    source, on_progress=lambda a, b: self.progress.emit(a, b))
                self.finished_with.emit(path, _("sdk.status.installed"))
            except Exception as exc:
                self.finished_with.emit(None, str(exc))

        self._worker = threading.Thread(target=work, daemon=True,
                                        name="sdk-download")
        self._worker.start()

    @QtCore.Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            # Megabytes are rounded here, so the catalogue carries words and
            # not format specs. `%p%` is Qt's own percentage placeholder and
            # passes through the substitution untouched.
            self.bar.setFormat(_("sdk.progress.detail",
                                 done=f"{done / 1e6:.0f}",
                                 total=f"{total / 1e6:.0f}"))
        else:
            # No Content-Length: keep it indeterminate rather than
            # inventing a percentage.
            self.bar.setFormat(_("sdk.progress.detail.unsized",
                                 done=f"{done / 1e6:.0f}"))

    @QtCore.Slot(object, str)
    def _on_done(self, path, message: str) -> None:
        self._worker = None
        self.bar.hide()
        self.status.setText(message)
        self.picker.setEnabled(True)
        self._describe()
        if path is not None:
            self.go.setText(_("sdk.action.done"))
            self.go.setEnabled(False)
