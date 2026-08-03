"""Who took the photograph.

The one piece of a capture's provenance the application cannot infer. The
objective, the illumination, the exposure and the scale all come from the
instrument; the person does not, and a slide photograph without attribution
is a photograph that cannot be published, credited or licensed.

Deliberately small, and deliberately empty by default. An unset copyright
notice is worse than none -- it looks like a claim that the work is unowned --
so blank fields write no tag at all.
"""
from __future__ import annotations

from datetime import datetime

from PySide6 import QtWidgets

from ..i18n import N_, _
from .framed import FramedDialog

#: Offered because most microscopy that gets shared is shared under one of
#: these, and typing a licence string from memory is how you get it wrong.
#:
#: Two catalogue keys each: what the menu calls it, and the words the notice
#: ends with. The notice used to be one template per licence, but the year
#: and the name in front of the terms are the same in all of them, so they
#: live in `photographer.notice.detail` and only the tail varies. That is
#: also what makes the notice matchable back to its licence.
LICENCES = (
    (N_("photographer.licence.reserved.label"),
     N_("photographer.licence.reserved.terms")),
    (N_("photographer.licence.cc_by.label"),
     N_("photographer.licence.cc_by.terms")),
    (N_("photographer.licence.cc_by_sa.label"),
     N_("photographer.licence.cc_by_sa.terms")),
    (N_("photographer.licence.cc_by_nc.label"),
     N_("photographer.licence.cc_by_nc.terms")),
    (N_("photographer.licence.cc0.label"),
     N_("photographer.licence.cc0.terms")),
)


class PhotographerDialog(FramedDialog):
    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent, width=460)
        self.setWindowTitle(_("photographer.title"))
        self._settings = settings

        self.name = QtWidgets.QLineEdit(settings.artist)
        self.name.setPlaceholderText(_("photographer.name.placeholder"))

        self.licence = QtWidgets.QComboBox()
        self.licence.addItem(_("photographer.licence.none.label"), "")
        for label, terms in LICENCES:
            self.licence.addItem(_(label), terms)

        self.notice = QtWidgets.QLineEdit(settings.copyright)
        self.notice.setPlaceholderText(
            _("photographer.notice.placeholder"))

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow(_("photographer.name.label"), self.name)
        form.addRow(_("photographer.licence.label"), self.licence)
        form.addRow(_("photographer.notice.label"), self.notice)

        self.note = QtWidgets.QLabel()
        self.note.setProperty("role", "key")
        self.note.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        col = self.content
        col.addLayout(form)
        col.addWidget(self.note)
        col.addStretch(1)
        col.addWidget(buttons)

        self._restore_licence()

        self.licence.currentIndexChanged.connect(self._apply_licence)
        self.name.textChanged.connect(self._describe)
        self.notice.textChanged.connect(self._describe)
        self._describe()
        self.finish()

    def _restore_licence(self) -> None:
        """Match the stored notice back to the licence that wrote it.

        Only the notice itself is persisted, which is the right thing to
        keep -- it is what gets written into the file, and a hand-edited
        one must survive untouched. But the combo then opened on "none"
        every time, so a licence chosen last week looked forgotten.

        Matched on the terms at the end rather than the whole string,
        because the year and the name in front of them change. A notice
        that matches nothing stays on "none", which is honest: it is a
        custom one.
        """
        stored = (self._settings.copyright or "").strip()
        if not stored:
            return
        for index in range(1, self.licence.count()):
            tail = _(self.licence.itemData(index)).strip()
            if tail and stored.endswith(tail):
                self.licence.blockSignals(True)
                self.licence.setCurrentIndex(index)
                self.licence.blockSignals(False)
                return

    def _apply_licence(self) -> None:
        terms = self.licence.currentData()
        if not terms:
            return
        self.notice.setText(
            _("photographer.notice.detail", year=datetime.now().year,
              name=self.name.text().strip()
              or _("photographer.notice.detail.noname"), terms=_(terms)))

    def _describe(self) -> None:
        bits = []
        if self.name.text().strip():
            bits.append(_("photographer.summary.detail.artist",
                          name=self.name.text().strip()))
        if self.notice.text().strip():
            bits.append(_("photographer.summary.detail.copyright",
                          notice=self.notice.text().strip()))
        self.note.setText(
            _("photographer.summary.detail", lines="\n".join(bits))
            if bits else _("photographer.summary.detail.empty"))

    def _save(self) -> None:
        self._settings.artist = self.name.text().strip()
        self._settings.copyright = self.notice.text().strip()
        self._settings.save()
        self.accept()
