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

from PySide6 import QtCore, QtWidgets

from ..session.model import (CameraProfile, Objective, ScopeProfile, Setup,
                             Turret)
from . import theme

_IMMERSION = ["", "oil", "water", "glycerol"]

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
        self.mag.setSpecialValueText("empty")
        self.mag.setFixedWidth(84)

        self.na = QtWidgets.QDoubleSpinBox()
        self.na.setRange(0.0, 1.6)
        self.na.setDecimals(2)
        self.na.setSingleStep(0.05)
        self.na.setPrefix("NA ")
        self.na.setSpecialValueText("--")
        self.na.setFixedWidth(78)

        self.kind = QtWidgets.QLineEdit()
        self.kind.setPlaceholderText("Planapo")
        self.kind.setMinimumWidth(70)

        self.immersion = QtWidgets.QComboBox()
        self.immersion.addItems(["dry", "oil", "water", "glycerol"])
        self.immersion.setFixedWidth(84)

        # Only meaningful on an empty position, so it is enabled by the
        # magnification going to zero and not otherwise.
        self.capped = QtWidgets.QCheckBox("capped")
        self.capped.setFixedWidth(72)
        self.capped.setToolTip(
            "There is a dust cap in this position rather than an open hole.\n\n"
            "The two look like opposite ends of the scale: an open position "
            "passes\nthe whole condenser cone straight through and blows "
            "white, a capped\none is black. Both are simply 'no objective' "
            "unless you say which.")

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
            immersion=_IMMERSION[self.immersion.currentIndex()])

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
        self.immersion.setCurrentIndex(
            _IMMERSION.index(objective.immersion)
            if objective.immersion in _IMMERSION else 0)


class ScopeEditor(QtWidgets.QWidget):
    """Everything about one stand: its optics and its turret."""

    changed = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self._scope: ScopeProfile | None = None

        self.name = QtWidgets.QLineEdit()
        self.condenser = QtWidgets.QLineEdit()
        self.condenser.setPlaceholderText("phase turret, darkfield stop")

        # Rare enough to be off by default. Zeiss call theirs an Optovar;
        # Leitz, Olympus and Nikon call it a magnification changer, so the
        # generic name is the label and the trade name is in the tooltip
        # where someone looking for it will find it.
        self.has_changer = QtWidgets.QCheckBox("fitted")
        self.optovar = QtWidgets.QLineEdit()
        self.optovar.setPlaceholderText("1 1.25 1.6 2")
        changer_row = QtWidgets.QHBoxLayout()
        changer_row.setSpacing(6)
        changer_row.addWidget(self.has_changer)
        changer_row.addWidget(self.optovar, 1)
        hint = ("An intermediate magnification changer, between the "
                "objective and\nthe tube lens. Zeiss call theirs an Optovar; "
                "Leitz, Olympus and\nNikon call it a magnification changer. "
                "Most stands have none.\n\n"
                "Its factors, space separated. It multiplies into total "
                "magnification\nexactly as the relay does, and changes "
                "magnification without moving\nthe optical axis, which is "
                "what makes an overview frame cheap.")
        self.has_changer.setToolTip(hint)
        self.optovar.setToolTip(hint)
        self.has_changer.toggled.connect(self._sync_changer)

        self.tube_length = QtWidgets.QDoubleSpinBox()
        self.tube_length.setRange(100.0, 300.0)
        self.tube_length.setDecimals(0)
        self.tube_length.setSuffix(" mm")
        self.tube_length.setToolTip(
            "What turns an objective's magnification into a focal length:\n"
            "f = tube / M, which is the focal length written into every "
            "capture.\n\n"
            "160 is the DIN finite standard and what most pre-1990 stands "
            "use --\nit is engraved on the objective, under the "
            "magnification. An infinity\nstand has no tube length as such, "
            "so put its tube *lens* focal length\nhere instead: 165 Zeiss, "
            "180 Olympus, 200 Nikon and Leica.")

        self.condenser_na = QtWidgets.QDoubleSpinBox()
        self.condenser_na.setRange(0.0, 1.6)
        self.condenser_na.setDecimals(2)
        self.condenser_na.setSingleStep(0.05)
        self.condenser_na.setSpecialValueText("-- unknown --")
        self.condenser_na.setToolTip(
            "The condenser's *working* aperture, not the number engraved on "
            "it.\nAn NA 1.4 condenser only reaches 1.4 with oil between it "
            "and the slide;\ndry, air caps it below 1.0, and the iris puts "
            "it anywhere below that.\n\nIt sets how bright each objective "
            "is, because the smaller of the two\napertures gathers the "
            "light -- and above about NA 0.5 that is the\ncondenser, not the "
            "objective. Used only as a first guess; a confirmed\nrotation "
            "teaches the real value.")

        self.rotation_sign = QtWidgets.QComboBox()
        self.rotation_sign.addItem("normal", 1)
        self.rotation_sign.addItem("inverted", -1)
        self.rotation_sign.setToolTip(
            "How the image's handedness relates to the turret. Nothing to do "
            "with\nwhether your turret is conventional.\n\nBetween the "
            "turret moving and a pixel darkening, the handedness\npasses "
            "through the objective (which inverts the image), the head and "
            "any\nphoto tube, however the camera is screwed onto its "
            "C-mount, and the\nraw stream arriving bottom-up. Four stages, "
            "each able to flip a sign.\n\nYou should not need this: "
            "correcting a wrong proposal once teaches it.")

        stand = QtWidgets.QFormLayout()
        stand.addRow("Name", self.name)
        stand.addRow("Tube length", self.tube_length)
        stand.addRow("Condenser", self.condenser)
        stand.addRow("Condenser NA", self.condenser_na)
        stand.addRow("Magnification changer", changer_row)
        stand.addRow("Image handedness", self.rotation_sign)

        # --- turret, in physical order
        self.slots = QtWidgets.QSpinBox()
        self.slots.setRange(1, MAX_SLOTS)
        self.slots.setSuffix(" positions")
        self.slots.setToolTip(
            "How many positions the turret holds, including any that are "
            "empty.\n\nFour and five are usual; six and seven exist. It has "
            "to be the real\ncount, because stepping and turret detection "
            "both work in physical\norder -- a five-position turret "
            "described as four is one position out\nfor half the ring.")
        self.slots.valueChanged.connect(self._sync_slots)

        header = QtWidgets.QLabel(
            "In turret order -- the order matters for stepping and detection")
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
        col.addWidget(_label("OBJECTIVES"))
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
        self.relay.setPlaceholderText("AmScope 1-2x C-mount")

        self.serial = QtWidgets.QLabel()
        self.serial.setProperty("role", "key")
        self.serial.setToolTip("Read from the camera. This is how it "
                               "recognises itself between sessions.")

        self.relay_factor = QtWidgets.QDoubleSpinBox()
        self.relay_factor.setRange(0.1, 10.0)
        self.relay_factor.setDecimals(2)
        self.relay_factor.setSingleStep(0.1)
        self.relay_factor.setPrefix("x ")
        self.relay_factor.setToolTip(
            "What the relay multiplies by, at the setting you use it.\n\n"
            "It sits between the objective and the sensor, so it multiplies "
            "into\ntotal magnification exactly as a magnification changer "
            "does. A 1-2x\nC-mount adapter left at 2x doubles the "
            "magnification at the sensor,\nwhich changes the f-number and "
            "how much slide each pixel covers.")

        self.pixel_um = QtWidgets.QDoubleSpinBox()
        self.pixel_um.setRange(0.0, 20.0)
        self.pixel_um.setDecimals(2)
        self.pixel_um.setSingleStep(0.1)
        self.pixel_um.setSuffix(" um")
        self.pixel_um.setSpecialValueText("ask the camera")
        self.pixel_um.setToolTip(
            "Sensor pixel pitch, from the datasheet.\n\n"
            "Set it and every capture can record how much slide one pixel "
            "covers,\nwhich is the number a scale bar is drawn from. The "
            "SDK is asked\nfirst when this is left at zero, but it reports "
            "0 on some models --\nand a zero pitch means no scale bar "
            "anywhere, quietly. The IMX183\nis 2.4 um.")

        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Name", self.name)
        form.addRow("Relay / adapter", self.relay)
        form.addRow("Relay factor", self.relay_factor)
        form.addRow("Pixel pitch", self.pixel_um)
        form.addRow("Serial", self.serial)

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
            self.serial.setText(camera.serial or "-- not seen yet --")
        finally:
            for w in widgets:
                w.blockSignals(False)

    def build(self) -> CameraProfile:
        base = self._camera or CameraProfile(serial="")
        return CameraProfile(
            serial=base.serial,
            name=self.name.text().strip() or "Camera",
            model=base.model,
            relay=self.relay.text().strip(),
            relay_factor=self.relay_factor.value(),
            pixel_um=self.pixel_um.value(),
            last_scope=base.last_scope)


class _LibraryDialog(QtWidgets.QDialog):
    """A list of the things you own, and an editor for the selected one.

    Selecting a different entry commits the one being left, so an edit is
    never lost by clicking away from it -- which is what a list beside a
    form has to do to be usable at all.
    """

    def __init__(self, library, parent=None, width: int = 680) -> None:
        super().__init__(parent)
        self._library = library
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

    def _save(self) -> None:
        raise NotImplementedError


class MicroscopeDialog(_LibraryDialog):
    """The stands you own. Objectives stay with the stand they screw into."""

    def __init__(self, library, current: str | None = None,
                 parent=None) -> None:
        super().__init__(library, parent)
        self.setWindowTitle("Microscopes")

        self.editor = ScopeEditor()
        self.editor.changed.connect(self._refresh)

        self.add = QtWidgets.QPushButton("New…")
        self.remove = QtWidgets.QPushButton("Remove")
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
        self.key.setToolTip("What a flat field is filed under. Change any of "
                            "it and the existing flat no longer applies.")

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
        right.addWidget(_label("CALIBRATION KEY"))
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
            self, "New microscope", "Name",
            text="Microscope %d" % (len(self._library.scopes) + 1))
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
                self, "Remove microscope",
                f"Remove {name}? Flat fields filed under it stay on disk but "
                f"will no longer be found.") != \
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
        self._library.save()
        self.accept()


class CameraDialog(_LibraryDialog):
    """The cameras you own.

    There is no picker here on purpose. Which camera is in front of you is
    not a preference -- it is whichever one is plugged in, matched by its
    serial when it arrives. This is where you say what is bolted to each.
    """

    def __init__(self, library, current: str | None = None,
                 parent=None) -> None:
        super().__init__(library, parent, width=520)
        self.setWindowTitle("Cameras")

        self.editor = CameraEditor()
        self.editor.changed.connect(self._refresh)

        self.empty = QtWidgets.QLabel(
            "No camera has been seen yet.\n\nOne is added the first time it "
            "is plugged in, recognised by its serial, so there is nothing to "
            "describe until then.")
        self.empty.setWordWrap(True)
        self.empty.setProperty("role", "body")

        self.bound = QtWidgets.QLabel()
        self.bound.setProperty("role", "key")
        self.bound.setWordWrap(True)

        # Everything that appears gets filed, because the alternative is
        # asking, and a stub you can correct beats a question you have to
        # answer before you can work. That means a built-in webcam ends up
        # in the list, so it has to be possible to take one out again.
        self.remove = QtWidgets.QPushButton("Remove")
        self.remove.setToolTip(
            "Forget this camera. It comes back if it is plugged in again.")
        self.remove.clicked.connect(self._remove)
        list_buttons = QtWidgets.QHBoxLayout()
        list_buttons.setSpacing(6)
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

    def _remove(self) -> None:
        if not self._selected:
            return
        name = self._library.cameras[self._selected].display
        if QtWidgets.QMessageBox.question(
                self, "Remove camera",
                f"Forget {name}? It will be added again the next time it is "
                f"plugged in.") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._library.remove_camera(self._selected)
        self._selected = None
        self._reload(None)

    def _reload(self, select: str | None) -> None:
        cameras = sorted(self._library.cameras.values(),
                         key=lambda c: c.display)
        self.list.blockSignals(True)
        self.list.clear()
        for camera in cameras:
            item = QtWidgets.QListWidgetItem(camera.display)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, camera.serial)
            self.list.addItem(item)
        if cameras:
            row = next((i for i, c in enumerate(cameras)
                        if c.serial == select), 0)
            self.list.setCurrentRow(row)
        self.list.blockSignals(False)

        known = bool(cameras)
        for w in (self.list, self.editor, self.bound, self.remove):
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
            self._library.cameras[self._selected] = self.editor.build()

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
        if not self._selected:
            self.bound.clear()
            return
        camera = self.editor.build()
        scope = self._library.scopes.get(camera.last_scope or "")
        self.bound.setText(f"Last used on {scope.name}." if scope
                           else "Not yet used on a microscope.")
        row = self.list.currentRow()
        if row >= 0 and self.list.item(row).text() != camera.display:
            self.list.item(row).setText(camera.display)

    def _save(self) -> None:
        self._commit()
        self._library.save()
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
