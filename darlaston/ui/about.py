"""Who this is named after.

Worth a dialog rather than a line in a README: the name is the one part of the
project that carries an argument, and someone who clicks it should find the
person rather than a version number.

Frameless and dragged by its own face, like the floating panels. A window
whose whole content is one short piece of writing does not need a title bar
repeating a name already set in eighteen point at the top of it.
"""
from __future__ import annotations

from PySide6 import QtWidgets

from .. import __version__
from ..i18n import _
from . import theme
from .framed import FramedDialog

#: His, from the bottom of a 1917 price list. Carried as an inscription
#: rather than as a claim: this program does not make slides, and the line
#: means something only while it stays his.
#:
#: **Deliberately not in the catalogue.** It is a quotation from a real
#: person, and translating it would be putting words in his mouth. It is
#: handed to the body text as a placeholder so it survives translation
#: there too.
TAGLINE = "Every Slide Perfect"


class AboutDialog(FramedDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, width=470)
        self.setWindowTitle(_("about.title"))

        fam = theme.load_fonts()
        wordmark = QtWidgets.QLabel(_("about.wordmark"))
        wordmark.setStyleSheet(
            f"font-family: '{fam['display']}'; font-size: 34px;"
            f"color: {theme.BRASS}; background: transparent;")

        tagline = QtWidgets.QLabel(TAGLINE.upper())
        tagline.setProperty("role", "label")

        body = QtWidgets.QLabel(_("about.body", tagline=TAGLINE))
        body.setWordWrap(True)
        body.setProperty("role", "body")

        licence = QtWidgets.QLabel(_("about.licence"))
        licence.setWordWrap(True)
        licence.setProperty("role", "key")

        version = QtWidgets.QLabel(__version__)
        version.setStyleSheet(
            f"font-family: '{fam['mono']}'; font-size: 10px;"
            f"color: {theme.DIM}; background: transparent;")

        close = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.reject)

        col = self.content
        col.setContentsMargins(26, 22, 26, 18)
        col.setSpacing(0)
        col.addWidget(wordmark)
        col.addWidget(tagline)
        col.addSpacing(18)
        col.addWidget(body)
        col.addSpacing(18)
        col.addWidget(licence)
        col.addSpacing(6)
        col.addWidget(version)
        col.addSpacing(14)
        col.addWidget(close)
        self.finish()
