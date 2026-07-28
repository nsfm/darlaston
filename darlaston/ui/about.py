"""Who this is named after.

Worth a dialog rather than a line in a README: the name is the one part of the
project that carries an argument, and someone who clicks it should find the
person rather than a version number.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .. import __version__
from . import theme


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About darlaston")
        self.setFixedWidth(460)

        wordmark = QtWidgets.QLabel("darlaston")
        wordmark.setStyleSheet(
            f"font-family: '{theme.load_fonts()['sans']}'; font-size: 26px;"
            f"font-style: italic; color: {theme.BRASS};")

        version = QtWidgets.QLabel(__version__)
        version.setProperty("role", "key")

        body = QtWidgets.QLabel(
            "Named for <b>Herbert William Hutton Darlaston</b> (1867–1949), a "
            "Birmingham mounter who took up microscopy in 1887 and turned "
            "professional around 1905 because too many people wanted his "
            "slides. He advertised in <i>English Mechanic</i> from 20 Freer "
            "Road, Birchfield. His diatom mounts survive from Constantinople "
            "to Santa Maria, California."
            "<br><br>"
            "Almost every tool in this field is named after optics or the "
            "company that sold them. This one is named after a person who "
            "prepared the slides.")
        body.setWordWrap(True)
        body.setProperty("role", "body")

        licence = QtWidgets.QLabel(
            "GPLv3, with a linking exception for the ToupTek SDK, which is "
            "never redistributed. Type is IBM Plex, under the SIL Open Font "
            "License.")
        licence.setWordWrap(True)
        licence.setProperty("role", "key")

        close = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 18)
        col.setSpacing(12)
        col.addWidget(wordmark)
        col.addWidget(version)
        col.addSpacing(4)
        col.addWidget(body)
        col.addWidget(licence)
        col.addSpacing(4)
        col.addWidget(close)
        self.setStyleSheet(theme.stylesheet())
