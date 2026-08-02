"""Describing the instrument, and choosing which one.

Everything downstream keys on this. The calibration store looks up flats by
objective and relay; the metadata writes the objective where a photographer
reads the lens; turret detection needs the positions in the order they physically
sit. A setup that is wrong here is wrong everywhere, silently.

So the editor is deliberately plain: name the things, list the objectives in
turret order, and see the calibration key it produces. No wizard, because this
is a page you visit twice a year and then forget.

Stands are a *collection*, kept separately from cameras, because that is the
physical arrangement: a camera and its relay travel together between stands
while the objectives stay with the stand. Someone with one microscope never
sees the picker -- a single stand is selected silently.
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


class SetupDialog(QtWidgets.QDialog):
    """Camera, relay, stand, turret, Optovar."""

    def __init__(self, setup: Setup, library, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Microscope setup")
        self.setMinimumWidth(620)
        self._setup = setup
        self._library = library

        # --- camera. Its relay travels with it, not with the stand, because
        # that is what physically happens when a camera moves between scopes.
        self.camera_name = QtWidgets.QLineEdit(setup.camera.name)
        self.relay = QtWidgets.QLineEdit(setup.camera.relay)
        self.relay.setPlaceholderText("AmScope 1–2× C-mount")
        serial = QtWidgets.QLabel(setup.camera.serial)
        serial.setProperty("role", "key")
        serial.setToolTip("Read from the camera. This is how it recognises "
                          "itself between sessions.")

        camera = QtWidgets.QFormLayout()
        self.relay_factor = QtWidgets.QDoubleSpinBox()
        self.relay_factor.setRange(0.1, 10.0)
        self.relay_factor.setDecimals(2)
        self.relay_factor.setSingleStep(0.1)
        self.relay_factor.setPrefix("x ")
        self.relay_factor.setValue(setup.camera.relay_factor or 1.0)
        self.relay_factor.setToolTip(
            "What the relay multiplies by, at the setting you use it.\n\n"
            "It sits between the objective and the sensor, so it multiplies "
            "into\ntotal magnification exactly as the Optovar does. A 1-2x "
            "C-mount\nadapter left at 2x doubles the magnification at the "
            "sensor, which\nchanges the f-number and how much slide each "
            "pixel covers.")

        self.pixel_um = QtWidgets.QDoubleSpinBox()
        self.pixel_um.setRange(0.0, 20.0)
        self.pixel_um.setDecimals(2)
        self.pixel_um.setSingleStep(0.1)
        self.pixel_um.setSuffix(" um")
        self.pixel_um.setSpecialValueText("ask the camera")
        self.pixel_um.setValue(setup.camera.pixel_um or 0.0)
        self.pixel_um.setToolTip(
            "Sensor pixel pitch, from the datasheet.\n\n"
            "Set it and every capture can record how much slide one pixel "
            "covers,\nwhich is the number a scale bar is drawn from. The "
            "SDK is asked\nfirst when this is left at zero, but it reports "
            "0 on some models --\nand a zero pitch means no scale bar "
            "anywhere, quietly. The IMX183\nis 2.4 um.")

        camera.addRow("Name", self.camera_name)
        camera.addRow("Relay / adapter", self.relay)
        camera.addRow("Relay factor", self.relay_factor)
        camera.addRow("Pixel pitch", self.pixel_um)
        camera.addRow("Serial", serial)

        # --- which stand. A camera that travels needs this; a camera that
        # does not never sees it, because one scope selects itself.
        self.picker = QtWidgets.QComboBox()
        self.add_scope = QtWidgets.QPushButton("New…")
        self.add_scope.setFixedWidth(64)
        self.remove_scope = QtWidgets.QPushButton("Remove")
        self.remove_scope.setFixedWidth(72)
        self.add_scope.clicked.connect(self._new_scope)
        self.remove_scope.clicked.connect(self._remove_scope)
        picker_row = QtWidgets.QHBoxLayout()
        picker_row.setSpacing(6)
        picker_row.addWidget(self.picker, 1)
        picker_row.addWidget(self.add_scope)
        picker_row.addWidget(self.remove_scope)

        # --- stand
        self.scope_name = QtWidgets.QLineEdit(setup.scope.name)
        self.condenser = QtWidgets.QLineEdit(setup.scope.condenser)
        self.condenser.setPlaceholderText("phase turret, darkfield stop")
        # Rare enough to be off by default. Zeiss call theirs an Optovar,
        # Leitz, Olympus and Nikon a magnification changer, so the generic
        # name is the label and the trade name is in the tooltip where
        # someone looking for it will find it.
        self.has_changer = QtWidgets.QCheckBox("fitted")
        self.has_changer.setChecked(bool(setup.scope.optovar))
        self.optovar = QtWidgets.QLineEdit(
            " ".join(f"{v:g}" for v in setup.scope.optovar))
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

        self.condenser_na = QtWidgets.QDoubleSpinBox()
        self.condenser_na.setRange(0.0, 1.6)
        self.condenser_na.setDecimals(2)
        self.condenser_na.setSingleStep(0.05)
        self.condenser_na.setSpecialValueText("-- unknown --")
        self.condenser_na.setValue(setup.scope.condenser_na or 0.0)
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
        self.rotation_sign.setCurrentIndex(
            0 if setup.scope.rotation_sign >= 0 else 1)
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
        stand.addRow("Microscope", picker_row)
        stand.addRow("Name", self.scope_name)
        stand.addRow("Condenser", self.condenser)
        stand.addRow("Condenser NA", self.condenser_na)
        stand.addRow("Magnification changer", changer_row)
        stand.addRow("Image handedness", self.rotation_sign)

        # --- turret, in physical order
        self.rows: list[ObjectiveRow] = []
        turret_box = QtWidgets.QVBoxLayout()
        turret_box.setSpacing(4)
        self.slots = QtWidgets.QSpinBox()
        self.slots.setRange(1, MAX_SLOTS)
        self.slots.setSuffix(" positions")
        self.slots.setValue(len(setup.scope.turret.positions) or 4)
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
        turret_box.addLayout(count_row)

        positions = list(setup.scope.turret.positions) + [None] * MAX_SLOTS
        for i in range(MAX_SLOTS):
            row = ObjectiveRow(i)
            row.set_value(positions[i], setup.scope.turret.is_capped(i))
            row.changed.connect(self._refresh)
            self.rows.append(row)
            turret_box.addWidget(row)

        self.key = QtWidgets.QLabel()
        self.key.setProperty("role", "key")
        self.key.setWordWrap(True)
        self.key.setToolTip("What a flat field is filed under. Change any of "
                            "it and the existing flat no longer applies.")

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(14)
        col.addWidget(_label("CAMERA"))
        col.addLayout(camera)
        col.addWidget(_label("STAND"))
        col.addLayout(stand)
        col.addWidget(_label("OBJECTIVES"))
        col.addLayout(turret_box)
        col.addWidget(_label("CALIBRATION KEY"))
        col.addWidget(self.key)
        col.addStretch(1)
        col.addWidget(buttons)

        for w in (self.camera_name, self.relay, self.scope_name, self.optovar):
            w.textChanged.connect(self._refresh)
        self._sync_changer()
        # After self.key exists: both of these refresh the calibration key.
        self._sync_slots()
        self._reload_picker(setup.scope.id)
        self.picker.currentIndexChanged.connect(self._switch_scope)
        self._refresh()
        self.setStyleSheet(theme.stylesheet())

    def _sync_changer(self) -> None:
        on = self.has_changer.isChecked()
        self.optovar.setEnabled(on)
        if not on:
            self.optovar.clear()
        self._refresh()

    def _sync_slots(self) -> None:
        """Show only the positions this turret actually has."""
        for i, row in enumerate(self.rows):
            row.setVisible(i < self.slots.value())
        self._refresh()

    # ---- the collection --------------------------------------------------

    def _reload_picker(self, select: str | None) -> None:
        scopes = self._library.scopes if self._library else {}
        self.picker.blockSignals(True)
        self.picker.clear()
        for sid, scope in sorted(scopes.items(), key=lambda kv: kv[1].name):
            self.picker.addItem(scope.name, sid)
        if not scopes:
            self.picker.addItem(self._setup.scope.name, self._setup.scope.id)
        idx = self.picker.findData(select)
        self.picker.setCurrentIndex(max(idx, 0))
        self.picker.blockSignals(False)
        # One stand needs no choosing, so the picker stays out of the way.
        single = len(scopes) <= 1
        self.picker.setVisible(not single)
        self.remove_scope.setVisible(not single)

    def _new_scope(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New microscope", "Name",
            text="Microscope %d" % (len(self._library.scopes) + 1
                                    if self._library else 1))
        if not ok or not name.strip():
            return
        self._commit_current()
        scope = self._library.add_scope(name.strip())
        self._setup = Setup(camera=self._setup.camera, scope=scope,
                            illumination=self._setup.illumination)
        self._load_scope(scope)
        self._reload_picker(scope.id)
        self._refresh()

    def _remove_scope(self) -> None:
        sid = self.picker.currentData()
        if not sid or not self._library or len(self._library.scopes) <= 1:
            return
        name = self._library.scopes[sid].name
        if QtWidgets.QMessageBox.question(
                self, "Remove microscope",
                f"Remove {name}? Flat fields filed under it stay on disk but "
                f"will no longer be found.") != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._library.remove_scope(sid)
        remaining = next(iter(self._library.scopes.values()))
        self._setup = Setup(camera=self._setup.camera, scope=remaining,
                            illumination=self._setup.illumination)
        self._load_scope(remaining)
        self._reload_picker(remaining.id)
        self._refresh()

    def _switch_scope(self) -> None:
        """Switching stands keeps the edits made to the one being left."""
        sid = self.picker.currentData()
        if not sid or not self._library or sid not in self._library.scopes:
            return
        self._commit_current()
        scope = self._library.scopes[sid]
        self._setup = Setup(camera=self._setup.camera, scope=scope,
                            illumination=self._setup.illumination)
        self._load_scope(scope)
        self._refresh()

    def _commit_current(self) -> None:
        if not self._library:
            return
        built = self._build()
        self._library.scopes[built.scope.id] = built.scope
        if built.camera.serial:                 # see _save
            self._library.cameras[built.camera.serial] = built.camera
        self._library.save()

    def _load_scope(self, scope: ScopeProfile) -> None:
        self.scope_name.setText(scope.name)
        self.condenser.setText(scope.condenser)
        self.optovar.setText(" ".join(f"{v:g}" for v in scope.optovar))
        self.has_changer.blockSignals(True)
        self.has_changer.setChecked(bool(scope.optovar))
        self.has_changer.blockSignals(False)
        self.optovar.setEnabled(bool(scope.optovar))
        self.condenser_na.setValue(scope.condenser_na or 0.0)
        self.rotation_sign.setCurrentIndex(0 if scope.rotation_sign >= 0 else 1)
        self.slots.blockSignals(True)
        self.slots.setValue(len(scope.turret.positions) or 4)
        self.slots.blockSignals(False)
        positions = list(scope.turret.positions) + [None] * MAX_SLOTS
        for i, row in enumerate(self.rows):
            row.blockSignals(True)
            row.set_value(positions[i], scope.turret.is_capped(i))
            row.blockSignals(False)
        self._sync_slots()

    # ---- live feedback ---------------------------------------------------

    def _refresh(self) -> None:
        probe = self._build()
        self.key.setText(probe.calibration_key())

    def _build(self) -> Setup:
        camera = CameraProfile(
            serial=self._setup.camera.serial,
            name=self.camera_name.text().strip() or "Camera",
            model=self._setup.camera.model,
            relay=self.relay.text().strip(),
            relay_factor=self.relay_factor.value(),
            pixel_um=self.pixel_um.value())
        # Exactly as many positions as the turret was said to hold. This
        # used to drop trailing empties on the grounds that they were not
        # gaps, which quietly turned a five-position turret whose last
        # position is empty into a four-position one -- and stepping and
        # detection both work in physical order, so from then on half the
        # ring was one position out.
        count = self.slots.value()
        positions = [row.value() for row in self.rows[:count]]
        capped = [row.is_capped() for row in self.rows[:count]]
        current = min(self._setup.scope.turret.current,
                      max(len(positions) - 1, 0))
        scope = ScopeProfile(
            id=self._setup.scope.id,
            name=self.scope_name.text().strip() or "Microscope",
            turret=Turret(positions=positions, current=current,
                          capped=capped),
            optovar=(_parse_factors(self.optovar.text())
                     if self.has_changer.isChecked() else []),
            optovar_current=self._setup.scope.optovar_current,
            condenser=self.condenser.text().strip(),
            condenser_na=(self.condenser_na.value()
                          if self.condenser_na.value() > 0 else None),
            rotation_sign=int(self.rotation_sign.currentData()),
            tube_length_mm=self._setup.scope.tube_length_mm,
            # Learned brightness belongs to the stand, not to this edit.
            brightness=dict(self._setup.scope.brightness))
        return Setup(camera=camera, scope=scope,
                     illumination=self._setup.illumination)

    def _save(self) -> None:
        self.result_setup = self._build()
        if self._library is not None:
            self._library.scopes[self.result_setup.scope.id] = \
                self.result_setup.scope
            # The serial is the camera's identity, and it is blank when the
            # dialog was opened with nothing plugged in. Filing that would
            # put a nameless profile under the key "" and bind the stand to
            # it, so the stand is saved and the camera half is not.
            if self.result_setup.camera.serial:
                self._library.cameras[self.result_setup.camera.serial] = \
                    self.result_setup.camera
                # Remember which stand this camera is on, so a travelling
                # camera comes back to the right one without being asked.
                self._library.bind(self.result_setup.camera.serial,
                                   self.result_setup.scope.id)
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
