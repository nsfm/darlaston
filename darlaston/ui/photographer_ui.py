"""Who took the photograph.

The one piece of a capture's provenance the application cannot infer. The
objective, the illumination, the exposure and the scale all come from the
instrument; the person does not, and a slide photograph without attribution
is a photograph that cannot be published, credited or licensed.

Deliberately small, and deliberately empty by default. An unset copyright
notice is worse than none — it looks like a claim that the work is unowned —
so blank fields write no tag at all.
"""
from __future__ import annotations

from datetime import datetime

from PySide6 import QtCore, QtWidgets

from . import theme

#: Offered because most microscopy that gets shared is shared under one of
#: these, and typing a licence string from memory is how you get it wrong.
LICENCES = (
    ("All rights reserved", "{year} {name}. All rights reserved."),
    ("CC BY 4.0", "{year} {name}. CC BY 4.0."),
    ("CC BY-SA 4.0", "{year} {name}. CC BY-SA 4.0."),
    ("CC BY-NC 4.0", "{year} {name}. CC BY-NC 4.0."),
    ("CC0 / public domain", "{year} {name}. CC0 1.0, public domain."),
)


class PhotographerDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Photographer")
        self.setMinimumWidth(460)
        self._settings = settings

        self.name = QtWidgets.QLineEdit(settings.artist)
        self.name.setPlaceholderText("your name, as you want it credited")

        self.licence = QtWidgets.QComboBox()
        self.licence.addItem("— none —", "")
        for label, template in LICENCES:
            self.licence.addItem(label, template)

        self.notice = QtWidgets.QLineEdit(settings.copyright)
        self.notice.setPlaceholderText(
            "left empty, no copyright tag is written at all")

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow("Name", self.name)
        form.addRow("Licence", self.licence)
        form.addRow("Notice", self.notice)

        self.note = QtWidgets.QLabel()
        self.note.setProperty("role", "key")
        self.note.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(12)
        col.addLayout(form)
        col.addWidget(self.note)
        col.addStretch(1)
        col.addWidget(buttons)
        self.setStyleSheet(theme.stylesheet())

        self.licence.currentIndexChanged.connect(self._apply_licence)
        self.name.textChanged.connect(self._describe)
        self.notice.textChanged.connect(self._describe)
        self._describe()

    def _apply_licence(self) -> None:
        template = self.licence.currentData()
        if not template:
            return
        self.notice.setText(template.format(year=datetime.now().year,
                                            name=self.name.text().strip()
                                            or "your name"))

    def _describe(self) -> None:
        bits = []
        if self.name.text().strip():
            bits.append(f"Artist: {self.name.text().strip()}")
        if self.notice.text().strip():
            bits.append(f"Copyright: {self.notice.text().strip()}")
        self.note.setText(
            "Written into every capture from now on.\n" + "\n".join(bits)
            if bits else
            "Nothing set — captures carry no attribution, which is fine "
            "until you share one.")

    def _save(self) -> None:
        self._settings.artist = self.name.text().strip()
        self._settings.copyright = self.notice.text().strip()
        self._settings.save()
        self.accept()
