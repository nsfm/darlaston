"""Describing the instrument, and choosing which one.

Everything downstream keys on this. The calibration store looks up flats by
objective and relay; the metadata writes the objective where a photographer
reads the lens; turret detection needs the positions in the order they physically
sit. A setup that is wrong here is wrong everywhere, silently.

So the editors are deliberately plain: name the things, list the objectives in
turret order, and see the calibration key it produces. No wizard, because this
is a page you visit twice a year and then forget.

**Two libraries, two windows.** Microscopes and cameras are separate
collections because that is the physical arrangement: a camera and its relay
travel together between stands while the objectives stay with the stand. Each
window is a list of the things you own and an editor for the selected one.

Which camera is in front of you is not a choice -- it is whichever one is
plugged in, matched by serial when it arrives -- so the camera window is a
place to describe them rather than to pick between them.
"""
from __future__ import annotations

import copy

from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import N_, _
from ..session.model import (CameraProfile, Objective, ScopeProfile, Setup,
                             Turret)
from . import theme

#: What sits between the front element and the slide: the value that gets
#: stored, and the key for the word the menu shows.
#:
#: The left-hand string is an identifier, not a word. It reaches
#: `Objective.immersion`, which is where the objective's label, the
#: calibration key and the EXIF all read it from, so it must not move when
#: the interface is translated. Dry is the empty string because that is
#: what the model has always meant by "nothing between them".
#:
#: Paired here rather than kept as two parallel lists matched by position:
#: the words are about to stop being recognisable next to the values they
#: belong to, and nothing would have noticed the two lists drifting apart.
_IMMERSION = (
    ("", N_("setup.objective.immersion.dry.option")),
    ("oil", N_("setup.objective.immersion.oil.option")),
    ("water", N_("setup.objective.immersion.water.option")),
    ("glycerol", N_("setup.objective.immersion.glycerol.option")),
)

#: Most turret positions anyone fits. Four and five are usual.
MAX_SLOTS = 7


class ObjectiveRow(QtWidgets.QWidget):
    """One turret position. Empty is a legitimate state -- real turrets have
    gaps, and a five-position turret with three objectives must not silently
    renumber itself."""

    changed = QtCore.Signal()

    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index

        self.position = QtWidgets.QLabel(f"{index + 1}")
        self.position.setProperty("role", "key")
        self.position.setFixedWidth(14)

        self.mag = QtWidgets.QDoubleSpinBox()
        self.mag.setRange(0.0, 250.0)
        self.mag.setDecimals(1)
        self.mag.setSuffix("×")
        self.mag.setSpecialValueText(_("setup.objective.mag.state.empty"))
        self.mag.setFixedWidth(84)

        self.na = QtWidgets.QDoubleSpinBox()
        self.na.setRange(0.0, 1.6)
        self.na.setDecimals(2)
        self.na.setSingleStep(0.05)
        self.na.setPrefix("NA ")
        self.na.setSpecialValueText(_("setup.objective.na.state.unknown"))
        self.na.setFixedWidth(78)

        self.kind = QtWidgets.QLineEdit()
        self.kind.setPlaceholderText(_("setup.objective.kind.placeholder"))
        self.kind.setMinimumWidth(70)

        self.immersion = QtWidgets.QComboBox()
        for value, label in _IMMERSION:
            self.immersion.addItem(_(label), value)
        self.immersion.setFixedWidth(84)

        # Only meaningful on an empty position, so it is enabled by the
        # magnification going to zero and not otherwise.
        self.capped = QtWidgets.QCheckBox(_("setup.objective.capped.label"))
        self.capped.setFixedWidth(72)
        self.capped.setToolTip(_("setup.objective.capped.tooltip"))

        for w in (self.mag, self.na):
            w.valueChanged.connect(self.changed)
        self.kind.textChanged.connect(self.changed)
        self.immersion.currentIndexChanged.connect(self.changed)
        self.capped.toggled.connect(self.changed)
        self.mag.valueChanged.connect(self._sync_capped)
        self._sync_capped()

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for w in (self.position, self.mag, self.na, self.kind, self.immersion,
                  self.capped):
            row.addWidget(w)

    def _sync_capped(self) -> None:
        empty = self.mag.value() <= 0
        self.capped.setEnabled(empty)
        if not empty and self.capped.isChecked():
            self.capped.setChecked(False)

    def is_capped(self) -> bool:
        return self.mag.value() <= 0 and self.capped.isChecked()

    def value(self) -> Objective | None:
        if self.mag.value() <= 0:
            return None
        return Objective(
            magnification=self.mag.value(),
            na=self.na.value() or None,
            kind=self.kind.text().strip(),
            # The stored identifier off the item, not the word on it.
            immersion=self.immersion.currentData())

    def set_value(self, objective: Objective | None,
                  capped: bool = False) -> None:
        """Blocks the children, because blockSignals on the row itself does
        not stop a spin box emitting on its own behalf."""
        widgets = (self.mag, self.na, self.kind, self.immersion, self.capped)
        for w in widgets:
            w.blockSignals(True)
        try:
            self._set_value(objective)
            self.capped.setChecked(bool(capped) and objective is None)
        finally:
            for w in widgets:
                w.blockSignals(False)
        self._sync_capped()

    def _set_value(self, objective: Objective | None) -> None:
        self.kind.clear()
        self.na.setValue(0.0)
        self.immersion.setCurrentIndex(0)
        if objective is None:
            self.mag.setValue(0.0)
            return
        self.mag.setValue(objective.magnification)
        self.na.setValue(objective.na or 0.0)
        self.kind.setText(objective.kind)
        # An immersion this build does not offer falls back to dry rather
        # than to whatever happens to sit at that index.
        at = self.immersion.findData(objective.immersion)
        self.immersion.setCurrentIndex(at if at >= 0 else 0)


class ScopeEditor(QtWidgets.QWidget):
    """Everything about one stand: its optics and its turret."""

    changed = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self._scope: ScopeProfile | None = None

        self.name = QtWidgets.QLineEdit()
        self.condenser = QtWidgets.QLineEdit()
        self.condenser.setPlaceholderText(
            _("setup.scope.condenser.placeholder"))

        # Rare enough to be off by default. Zeiss call theirs an Optovar;
        # Leitz, Olympus and Nikon call it a magnification changer, so the
        # generic name is the label and the trade name is in the tooltip
        # where someone looking for it will find it.
        self.has_changer = QtWidgets.QCheckBox(
            _("setup.scope.changer.fitted.label"))
        self.optovar = QtWidgets.QLineEdit()
        # Not a catalogue entry, deliberately. `_parse_factors` reads these
        # with float() and treats a comma as a separator, so a placeholder
        # written "1 1,25" in a locale that uses a decimal comma would be an
        # example of input this field cannot accept.
        self.optovar.setPlaceholderText("1 1.25 1.6 2")
        changer_row = QtWidgets.QHBoxLayout()
        changer_row.setSpacing(6)
        changer_row.addWidget(self.has_changer)
        changer_row.addWidget(self.optovar, 1)
        hint = _("setup.scope.changer.tooltip")
        self.has_changer.setToolTip(hint)
        self.optovar.setToolTip(hint)
        self.has_changer.toggled.connect(self._sync_changer)

        self.tube_length = QtWidgets.QDoubleSpinBox()
        self.tube_length.setRange(100.0, 300.0)
        self.tube_length.setDecimals(0)
        self.tube_length.setSuffix(" mm")
        self.tube_length.setToolTip(_("setup.scope.tube.tooltip"))

        self.condenser_na = QtWidgets.QDoubleSpinBox()
        self.condenser_na.setRange(0.0, 1.6)
        self.condenser_na.setDecimals(2)
        self.condenser_na.setSingleStep(0.05)
        self.condenser_na.setSpecialValueText(
            _("setup.scope.condenser_na.state.unknown"))
        self.condenser_na.setToolTip(_("setup.scope.condenser_na.tooltip"))

        self.rotation_sign = QtWidgets.QComboBox()
        self.rotation_sign.addItem(
            _("setup.scope.handedness.normal.option"), 1)
        self.rotation_sign.addItem(
            _("setup.scope.handedness.inverted.option"), -1)
        self.rotation_sign.setToolTip(_("setup.scope.handedness.tooltip"))

        stand = QtWidgets.QFormLayout()
        stand.addRow(_("setup.scope.name.label"), self.name)
        stand.addRow(_("setup.scope.tube.label"), self.tube_length)
        stand.addRow(_("setup.scope.condenser.label"), self.condenser)
        stand.addRow(_("setup.scope.condenser_na.label"), self.condenser_na)
        stand.addRow(_("setup.scope.changer.label"), changer_row)
        stand.addRow(_("setup.scope.handedness.label"), self.rotation_sign)

        # --- turret, in physical order
        self.slots = QtWidgets.QSpinBox()
        self.slots.setRange(1, MAX_SLOTS)
        self.slots.setSuffix(_("setup.turret.slots.detail"))
        self.slots.setToolTip(_("setup.turret.slots.tooltip"))
        self.slots.valueChanged.connect(self._sync_slots)

        header = QtWidgets.QLabel(_("setup.turret.order.detail"))
        header.setProperty("role", "key")
        count_row = QtWidgets.QHBoxLayout()
        count_row.setSpacing(8)
        count_row.addWidget(self.slots)
        count_row.addWidget(header, 1)

        self.rows: list[ObjectiveRow] = []
        turret_box = QtWidgets.QVBoxLayout()
        turret_box.setSpacing(4)
        turret_box.addLayout(count_row)
        for i in range(MAX_SLOTS):
            row = ObjectiveRow(i)
            row.changed.connect(self.changed)
            self.rows.append(row)
            turret_box.addWidget(row)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(12)
        col.addLayout(stand)
        col.addWidget(_label(_("setup.turret.objectives.heading")))
        col.addLayout(turret_box)
        col.addStretch(1)

        for w in (self.name, self.condenser, self.optovar):
            w.textChanged.connect(self.changed)
        for w in (self.condenser_na, self.tube_length):
            w.valueChanged.connect(self.changed)
        self.rotation_sign.currentIndexChanged.connect(self.changed)
        self._sync_slots()

    def _sync_changer(self) -> None:
        on = self.has_changer.isChecked()
        self.optovar.setEnabled(on)
        if not on:
            self.optovar.clear()
        self.changed.emit()

    def _sync_slots(self) -> None:
        """Show only the positions this turret actually has."""
        for i, row in enumerate(self.rows):
            row.setVisible(i < self.slots.value())
        self.changed.emit()

    def load(self, scope: ScopeProfile) -> None:
        self._scope = scope
        widgets = (self.name, self.condenser, self.optovar, self.has_changer,
                   self.condenser_na, self.tube_length, self.rotation_sign,
                   self.slots)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.name.setText(scope.name)
            self.condenser.setText(scope.condenser)
            self.optovar.setText(" ".join(f"{v:g}" for v in scope.optovar))
            self.has_changer.setChecked(bool(scope.optovar))
            self.optovar.setEnabled(bool(scope.optovar))
            self.condenser_na.setValue(scope.condenser_na or 0.0)
            self.tube_length.setValue(scope.tube_length_mm or 160.0)
            self.rotation_sign.setCurrentIndex(
                0 if scope.rotation_sign >= 0 else 1)
            self.slots.setValue(len(scope.turret.positions) or 4)
            positions = list(scope.turret.positions) + [None] * MAX_SLOTS
            for i, row in enumerate(self.rows):
                row.blockSignals(True)
                row.set_value(positions[i], scope.turret.is_capped(i))
                row.blockSignals(False)
        finally:
            for w in widgets:
                w.blockSignals(False)
        for i, row in enumerate(self.rows):
            row.setVisible(i < self.slots.value())

    def build(self) -> ScopeProfile:
        """The stand as edited. Keeps its id and anything it has learned."""
        base = self._scope or ScopeProfile(id="unconfigured")
        # Exactly as many positions as the turret was said to hold. This
        # used to drop trailing empties on the grounds that they were not
        # gaps, which quietly turned a five-position turret whose last
        # position is empty into a four-position one -- and stepping and
        # detection both work in physical order, so from then on half the
        # ring was one position out.
        count = self.slots.value()
        positions = [row.value() for row in self.rows[:count]]
        capped = [row.is_capped() for row in self.rows[:count]]
        return ScopeProfile(
            id=base.id,
            # Not a catalogue entry: this is the model's own default for a
            # stand nobody has named, it is written to the library file, and
            # `scope_id` derives the id -- and through it the calibration
            # key -- from it. A name that changed with the interface
            # language would file this session's flats somewhere the next
            # one could not find them.
            name=self.name.text().strip() or "Microscope",
            turret=Turret(positions=positions,
                          current=min(base.turret.current,
                                      max(len(positions) - 1, 0)),
                          capped=capped),
            optovar=(_parse_factors(self.optovar.text())
                     if self.has_changer.isChecked() else []),
            optovar_current=base.optovar_current,
            condenser=self.condenser.text().strip(),
            condenser_na=(self.condenser_na.value()
                          if self.condenser_na.value() > 0 else None),
            rotation_sign=int(self.rotation_sign.currentData()),
            rotation_sign_known=base.rotation_sign_known,
            tube_length_mm=self.tube_length.value(),
            # Learned brightness belongs to the stand, not to this edit.
            brightness=dict(base.brightness))


class CameraEditor(QtWidgets.QWidget):
    """One camera and the relay bolted to it.

    The relay travels with the camera rather than with the stand, because
    that is what physically happens when a camera moves between scopes.
    """

    changed = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self._camera: CameraProfile | None = None

        self.name = QtWidgets.QLineEdit()
        self.relay = QtWidgets.QLineEdit()
        self.relay.setPlaceholderText(_("setup.camera.relay.placeholder"))

        self.serial = QtWidgets.QLabel()
        self.serial.setProperty("role", "key")
        self.serial.setToolTip(_("setup.camera.serial.tooltip"))

        self.relay_factor = QtWidgets.QDoubleSpinBox()
        self.relay_factor.setRange(0.1, 10.0)
        self.relay_factor.setDecimals(2)
        self.relay_factor.setSingleStep(0.1)
        self.relay_factor.setPrefix("x ")
        self.relay_factor.setToolTip(_("setup.camera.relay_factor.tooltip"))

        self.pixel_um = QtWidgets.QDoubleSpinBox()
        self.pixel_um.setRange(0.0, 20.0)
        self.pixel_um.setDecimals(2)
        self.pixel_um.setSingleStep(0.1)
        self.pixel_um.setSuffix(" um")
        self.pixel_um.setSpecialValueText(_("setup.camera.pixel.state.ask"))
        self.pixel_um.setToolTip(_("setup.camera.pixel.tooltip"))

        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow(_("setup.camera.name.label"), self.name)
        form.addRow(_("setup.camera.relay.label"), self.relay)
        form.addRow(_("setup.camera.relay_factor.label"), self.relay_factor)
        form.addRow(_("setup.camera.pixel.label"), self.pixel_um)
        form.addRow(_("setup.camera.serial.label"), self.serial)

        for w in (self.name, self.relay):
            w.textChanged.connect(self.changed)
        for w in (self.relay_factor, self.pixel_um):
            w.valueChanged.connect(self.changed)

    def load(self, camera: CameraProfile) -> None:
        self._camera = camera
        widgets = (self.name, self.relay, self.relay_factor, self.pixel_um)
        for w in widgets:
            w.blockSignals(True)
        try:
            self.name.setText(camera.name)
            self.relay.setText(camera.relay)
            self.relay_factor.setValue(camera.relay_factor or 1.0)
            self.pixel_um.setValue(camera.pixel_um or 0.0)
            self.serial.setText(camera.serial
                                or _("setup.camera.serial.state.unseen"))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def build(self) -> CameraProfile:
        """The edited camera: what was typed, over everything else.

        `replace` rather than a fresh `CameraProfile`, and that is the
        whole point. Listing the fields to keep meant every field this
        editor does not show was silently reset -- the manufacturer, the
        fingerprint that stops one camera inheriting another's identity,
        and the measured geometry and exposure response. Clicking Save,
        or merely selecting a different row, threw away a ten-minute
        profiling run. Anything added to `CameraProfile` in future would
        have joined them without a word.
        """
        from dataclasses import replace

        base = self._camera or CameraProfile(serial="")
        return replace(
            base,
            # English on purpose, as the stand's default name is: it is the
            # model's own default and it goes to the library file, where a
            # camera would otherwise be named after whatever language
            # happened to be set the day it was last edited.
            name=self.name.text().strip() or "Camera",
            relay=self.relay.text().strip(),
            relay_factor=self.relay_factor.value(),
            pixel_um=self.pixel_um.value())


class _LibraryDialog(QtWidgets.QDialog):
    """A list of the things you own, and an editor for the selected one.

    Selecting a different entry commits the one being left, so an edit is
    never lost by clicking away from it -- which is what a list beside a
    form has to do to be usable at all.
    """

    def __init__(self, library, parent=None, width: int = 680) -> None:
        super().__init__(parent)
        # Edited on a copy, and merged back only by Save.
        #
        # Remove and New used to reach straight into the application's own
        # Library. Neither wrote the file itself, which made it look safe
        # -- but the object is shared, so Cancel left the change in memory
        # and the next save from anywhere at all committed it. A removed
        # stand takes its turret, its learned brightness signatures, and
        # the calibration key every stored flat is filed under, so the
        # thing being quietly kept was not a name in a list.
        self._real = library
        self._library = copy.deepcopy(library)
        self._selected: str | None = None
        self.setMinimumWidth(width)

        self.list = QtWidgets.QListWidget()
        self.list.setFixedWidth(170)
        self.list.currentRowChanged.connect(self._switch)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)

    def _switch(self, row: int) -> None:
        raise NotImplementedError

    def _commit_to_library(self) -> None:
        """Move the edited copy back onto the real one, and write it.

        In place, on the object the window and everything it built are
        holding -- rebinding `self._real` here would leave every one of
        them pointing at the copy nobody merged into.
        """
        self._real.scopes = self._library.scopes
        self._real.cameras = self._library.cameras
        self._real.save()
        # From here on the dialog is looking at what was saved, so a
        # caller reading `selected` after `exec()` gets the live object
        # rather than a copy that will not be updated again.
        self._library = self._real

    def _save(self) -> None:
        raise NotImplementedError


class MicroscopeDialog(_LibraryDialog):
    """The stands you own. Objectives stay with the stand they screw into."""

    def __init__(self, library, current: str | None = None,
                 parent=None) -> None:
        super().__init__(library, parent)
        self.setWindowTitle(_("setup.scopes.title"))

        self.editor = ScopeEditor()
        self.editor.changed.connect(self._refresh)

        self.add = QtWidgets.QPushButton(_("setup.scopes.action.new"))
        self.remove = QtWidgets.QPushButton(_("setup.scopes.action.remove"))
        self.add.clicked.connect(self._new)
        self.remove.clicked.connect(self._remove)
        list_buttons = QtWidgets.QHBoxLayout()
        list_buttons.setSpacing(6)
        list_buttons.addWidget(self.add)
        list_buttons.addWidget(self.remove)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self.list, 1)
        left.addLayout(list_buttons)

        self.key = QtWidgets.QLabel()
        self.key.setProperty("role", "key")
        self.key.setWordWrap(True)
        self.key.setToolTip(_("setup.scopes.key.tooltip"))

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # Vertical only. Allowed to scroll sideways the turret rows simply
        # ran off the edge and took the cap boxes with them, which is the
        # one control on the row a person has to be able to see to use.
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.editor)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(scroll, 1)
        right.addWidget(_label(_("setup.scopes.key.heading")))
        right.addWidget(self.key)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(left)
        body.addLayout(right, 1)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(14)
        col.addLayout(body, 1)
        col.addWidget(self.buttons)

        self._reload(current)
        self.setStyleSheet(theme.stylesheet())

    # ---- the collection --------------------------------------------------

    def _reload(self, select: str | None) -> None:
        scopes = sorted(self._library.scopes.values(), key=lambda s: s.name)
        if not scopes:
            # An empty library is not a state this window can show, so it
            # makes the first stand rather than presenting nothing to edit.
            # Named in English for the same reason `build` is: the name
            # reaches the library file and the calibration key.
            scopes = [self._library.add_scope("Microscope")]
        self.list.blockSignals(True)
        self.list.clear()
        for scope in scopes:
            item = QtWidgets.QListWidgetItem(scope.name)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, scope.id)
            self.list.addItem(item)
        row = next((i for i, s in enumerate(scopes) if s.id == select), 0)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self._selected = scopes[row].id
        self.editor.load(scopes[row])
        self.remove.setEnabled(len(scopes) > 1)
        self._refresh()

    def _commit(self) -> None:
        """Write the entry being left back into the library."""
        if self._selected and self._selected in self._library.scopes:
            self._library.scopes[self._selected] = self.editor.build()

    def _switch(self, row: int) -> None:
        if row < 0:
            return
        self._commit()
        sid = self.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        if sid in self._library.scopes:
            self._selected = sid
            self.editor.load(self._library.scopes[sid])
        self._refresh()

    def _new(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, _("setup.scopes.new.title"), _("setup.scopes.new.label"),
            text=_("setup.scopes.new.detail",
                   number=len(self._library.scopes) + 1))
        if not ok or not name.strip():
            return
        self._commit()
        scope = self._library.add_scope(name.strip())
        self._reload(scope.id)

    def _remove(self) -> None:
        if len(self._library.scopes) <= 1 or not self._selected:
            return
        name = self._library.scopes[self._selected].name
        if QtWidgets.QMessageBox.question(
                self, _("setup.scopes.remove.title"),
                _("setup.scopes.remove.detail", name=name)) != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._library.remove_scope(self._selected)
        self._selected = None
        self._reload(next(iter(self._library.scopes), None))

    # ---- live feedback ---------------------------------------------------

    def _refresh(self) -> None:
        scope = self.editor.build()
        camera = next(iter(self._library.cameras.values()),
                      None) or CameraProfile(serial="")
        self.key.setText(Setup(camera=camera, scope=scope).calibration_key())
        row = self.list.currentRow()
        if row >= 0 and self.list.item(row).text() != scope.name:
            self.list.item(row).setText(scope.name)

    @property
    def selected(self) -> ScopeProfile | None:
        """The stand that was being edited when Save was pressed."""
        return self._library.scopes.get(self._selected or "")

    def _save(self) -> None:
        self._commit()
        self._commit_to_library()
        self.accept()


def _present_dot() -> QtGui.QIcon:
    """The mark for a camera that is plugged in right now."""
    size = 10
    pip = QtGui.QPixmap(size, size)
    pip.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(pip)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    glow = QtGui.QColor(theme.BRASS)
    glow.setAlpha(70)
    p.setBrush(glow)
    p.drawEllipse(0, 0, size, size)
    p.setBrush(QtGui.QColor(theme.BRASS))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QtGui.QIcon(pip)


class CameraDialog(_LibraryDialog):
    """The cameras you own, which of them to use, and what they do.

    This used to say there was no picker here on purpose: which camera is
    in front of you is not a preference, it is whichever one is plugged
    in. That was true of a bench with one camera on it. It stops being
    true on a laptop with a built-in camera, an infrared sensor beside
    it, a USB camera on the microscope and a machine-vision camera in a
    drawer -- all attached at once, all real, only one of them the answer.

    So description and selection live together, because a person thinking
    "camera" should not have to know which of those two ideas they want.
    The list says which are attached; choosing one that is picks it.
    """

    def __init__(self, library, current: str | None = None,
                 parent=None) -> None:
        super().__init__(library, parent, width=520)
        self.setWindowTitle(_("setup.cameras.title"))
        #: Which camera the session actually has open, as opposed to
        #: merely attached. Measuring needs to drive it.
        self._open_serial = current

        self.editor = CameraEditor()
        self.editor.changed.connect(self._refresh)

        self.empty = QtWidgets.QLabel(_("setup.cameras.empty.detail"))
        self.empty.setWordWrap(True)
        self.empty.setProperty("role", "body")

        self.bound = QtWidgets.QLabel()
        self.bound.setProperty("role", "key")
        self.bound.setWordWrap(True)

        # Everything that appears gets filed, because the alternative is
        # asking, and a stub you can correct beats a question you have to
        # answer before you can work. That means a built-in webcam ends up
        # in the list, so it has to be possible to take one out again.
        self.remove = QtWidgets.QPushButton(_("setup.cameras.action.remove"))
        self.remove.setToolTip(_("setup.cameras.remove.tooltip"))
        self.remove.clicked.connect(self._remove)

        # Only useful for a camera that is actually attached, so it says
        # so rather than failing when pressed.
        self.use = QtWidgets.QPushButton(_("setup.cameras.action.use"))
        self.use.setToolTip(_("setup.cameras.use.tooltip"))
        self.use.clicked.connect(self._use)
        self.use.setEnabled(False)

        # Only for the camera that is actually open, since measuring
        # means driving it.
        self.profile = QtWidgets.QPushButton(
            _("setup.cameras.action.profile"))
        self.profile.setToolTip(_("setup.cameras.profile.tooltip"))
        self.profile.clicked.connect(self.measure_requested)
        self.profile.setEnabled(False)

        list_buttons = QtWidgets.QHBoxLayout()
        list_buttons.setSpacing(6)
        list_buttons.addWidget(self.use)
        list_buttons.addWidget(self.profile)
        list_buttons.addWidget(self.remove)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self.list, 1)
        left.addLayout(list_buttons)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self.empty)
        right.addWidget(self.editor)
        right.addWidget(self.bound)
        right.addStretch(1)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(left)
        body.addLayout(right, 1)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(14)
        col.addLayout(body, 1)
        col.addWidget(self.buttons)

        self._reload(current)
        self.setStyleSheet(theme.stylesheet())

    #: Emitted when somebody asks to measure the open camera. The window
    #: owns the session, so it runs the measurement; this only asks.
    measure_requested = QtCore.Signal()

    #: Set when a camera is chosen, and read by the caller afterwards.
    #: This dialog does not open cameras: it is a dialog.
    picked: str = ""

    def _attached(self) -> dict:
        """Which of these are plugged in right now, under the same key.

        Empty on anything that goes wrong -- a library that cannot say
        what is attached is still a perfectly good library.
        """
        try:
            from ..camera.discovery import look
            return {c.key.split(":", 1)[1]: c
                    for c in look() if ":" in c.key}
        except Exception:
            return {}

    def _use(self) -> None:
        found = self._attached().get(self._selected or "")
        if found is None:
            return
        self.picked = found.key
        self._save()

    def _remove(self) -> None:
        if not self._selected:
            return
        name = self._library.cameras[self._selected].display
        if QtWidgets.QMessageBox.question(
                self, _("setup.cameras.remove.title"),
                _("setup.cameras.remove.detail", name=name)) != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._library.remove_camera(self._selected)
        self._selected = None
        self._reload(None)

    def _reload(self, select: str | None) -> None:
        cameras = sorted(self._library.cameras.values(),
                         key=lambda c: c.display)
        self.list.blockSignals(True)
        self.list.clear()
        attached = self._attached()
        for camera in cameras:
            item = QtWidgets.QListWidgetItem(camera.display)
            if camera.serial in attached:
                # A brass dot, which is already what this program means
                # by "state, at a glance". The list is 170 px wide and
                # the word "attached" does not fit in it; a dot does, and
                # is read faster anyway.
                item.setIcon(_present_dot())
                item.setToolTip(_("setup.cameras.attached.tooltip"))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, camera.serial)
            self.list.addItem(item)
        if cameras:
            row = next((i for i, c in enumerate(cameras)
                        if c.serial == select), 0)
            self.list.setCurrentRow(row)
        self.list.blockSignals(False)

        known = bool(cameras)
        for w in (self.list, self.editor, self.bound, self.remove,
                  self.use, self.profile):
            w.setVisible(known)
        self.empty.setVisible(not known)
        if known:
            chosen = cameras[max(self.list.currentRow(), 0)]
            self._selected = chosen.serial
            self.editor.load(chosen)
        else:
            self._selected = None
        self._refresh()

    def _commit(self) -> None:
        if self._selected and self._selected in self._library.cameras:
            self._library.file_camera(self.editor.build())

    def _switch(self, row: int) -> None:
        if row < 0:
            return
        self._commit()
        serial = self.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        if serial in self._library.cameras:
            self._selected = serial
            self.editor.load(self._library.cameras[serial])
        self._refresh()

    def _refresh(self) -> None:
        here = bool(self._selected) and self._selected in self._attached()
        self.use.setEnabled(here)
        # Measuring drives the camera, so only the one that is open.
        self.profile.setEnabled(here and self._selected == self._open_serial)
        if not self._selected:
            self.bound.clear()
            return
        camera = self.editor.build()
        scope = self._library.scopes.get(camera.last_scope or "")
        self.bound.setText(
            _("setup.cameras.bound.detail", name=scope.name) if scope
            else _("setup.cameras.bound.detail.unused"))
        row = self.list.currentRow()
        if row >= 0 and self.list.item(row).text() != camera.display:
            self.list.item(row).setText(camera.display)

    def _save(self) -> None:
        self._commit()
        self._commit_to_library()
        self.accept()


def _parse_factors(text: str) -> list[float]:
    out = []
    for token in text.replace(",", " ").split():
        try:
            value = float(token.rstrip("x×"))
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return out


def _label(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setProperty("role", "label")
    return w
