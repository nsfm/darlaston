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
from . import theme


class SdkDialog(QtWidgets.QDialog):
    """Choose a vendor, see the terms of the download, fetch it."""

    progress = QtCore.Signal(int, int)
    finished_with = QtCore.Signal(object, str)   # (path or None, message)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Install camera SDK")
        self.setStyleSheet(theme.stylesheet())
        self._worker: threading.Thread | None = None

        head = QtWidgets.QLabel(
            "Your camera needs a driver made by the company that built "
            "it, which darlaston cannot include. Pick your camera's brand "
            "and darlaston will download the driver and check that it "
            "works.")
        head.setWordWrap(True)

        self.picker = QtWidgets.QComboBox()
        for source in sdk_install.sources():
            suffix = ("" if source.automatic else "  (manual download)")
            self.picker.addItem(f"{source.label}{suffix}", source.brand)
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
            "Download and install",
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.clicked.connect(self._start)
        self.buttons.rejected.connect(self.reject)

        col = QtWidgets.QVBoxLayout(self)
        col.addWidget(head)
        col.addSpacing(8)
        col.addWidget(QtWidgets.QLabel("Camera brand"))
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
            lines.append("<b>Already installed.</b> Downloading again "
                         "will replace it.")
        if source.automatic:
            lines.append(
                f"About <b>{source.approx_mb} MB</b>, downloaded from "
                f"{source.url.split('/')[2]} and saved in "
                f"<code>{where}</code>.")
        else:
            lines.append(
                f"{source.label} does not offer a direct download link. "
                f"Get the Linux driver from "
                f"<a href='{source.page}'>their website</a>, then unzip it "
                f"into <code>{where}</code>.")
        if source.note:
            lines.append(source.note)
        self.detail.setText("<br><br>".join(lines))
        # A brand we cannot fetch gets a button that still does
        # something, rather than a greyed-out one that reads as broken.
        self.go.setText("Download and install" if source.automatic
                        else "Open download page")
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
            self.status.setText(
                f"Opened their website. Unzip the Linux driver into "
                f"{where}, which has been created for you.")
            return
        self.go.setEnabled(False)
        self.picker.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.status.setText(f"Contacting {source.url.split('/')[2]}…")

        def work():
            try:
                path = sdk_install.download(
                    source, on_progress=lambda a, b: self.progress.emit(a, b))
                self.finished_with.emit(
                    path, "Installed. Unplug the camera and plug it back "
                          "in, and darlaston will find it.")
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
            self.bar.setFormat(
                f"%p%  ({done / 1e6:.0f} of {total / 1e6:.0f} MB)")
        else:
            # No Content-Length: keep it indeterminate rather than
            # inventing a percentage.
            self.bar.setFormat(f"{done / 1e6:.0f} MB")

    @QtCore.Slot(object, str)
    def _on_done(self, path, message: str) -> None:
        self._worker = None
        self.bar.hide()
        self.status.setText(message)
        self.picker.setEnabled(True)
        self._describe()
        if path is not None:
            self.go.setText("Done")
            self.go.setEnabled(False)
