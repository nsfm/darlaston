"""Describing the instrument.

Everything downstream keys on this. The calibration store looks up flats by
objective and relay; the metadata writes the objective where a photographer
reads the lens; turret detection needs the positions in the order they physically
sit. A setup that is wrong here is wrong everywhere, silently.

So the editor is deliberately plain: name the things, list the objectives in
turret order, and see the calibration key it produces. No wizard, because this
is a page you visit twice a year and then forget.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..session.model import (CameraProfile, Objective, ScopeProfile, Setup,
                             Turret)
from . import theme

_IMMERSION = ["", "oil", "water", "glycerol"]


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
        self.na.setSpecialValueText("—")
        self.na.setFixedWidth(78)

        self.kind = QtWidgets.QLineEdit()
        self.kind.setPlaceholderText("Planapo")

        self.immersion = QtWidgets.QComboBox()
        self.immersion.addItems(["dry", "oil", "water", "glycerol"])
        self.immersion.setFixedWidth(84)

        for w in (self.mag, self.na):
            w.valueChanged.connect(self.changed)
        self.kind.textChanged.connect(self.changed)
        self.immersion.currentIndexChanged.connect(self.changed)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for w in (self.position, self.mag, self.na, self.kind, self.immersion):
            row.addWidget(w)

    def value(self) -> Objective | None:
        if self.mag.value() <= 0:
            return None
        return Objective(
            magnification=self.mag.value(),
            na=self.na.value() or None,
            kind=self.kind.text().strip(),
            immersion=_IMMERSION[self.immersion.currentIndex()])

    def set_value(self, objective: Objective | None) -> None:
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
        camera.addRow("Name", self.camera_name)
        camera.addRow("Relay / adapter", self.relay)
        camera.addRow("Serial", serial)

        # --- stand
        self.scope_name = QtWidgets.QLineEdit(setup.scope.name)
        self.condenser = QtWidgets.QLineEdit(setup.scope.condenser)
        self.condenser.setPlaceholderText("phase turret, darkfield stop")
        self.optovar = QtWidgets.QLineEdit(
            " ".join(f"{v:g}" for v in setup.scope.optovar))
        self.optovar.setPlaceholderText("1 1.25 1.6 2      (blank if none)")
        self.optovar.setToolTip(
            "Intermediate magnification factors, space separated.\n"
            "Changes magnification without moving the optical axis, which is "
            "what makes an overview frame cheap.")

        stand = QtWidgets.QFormLayout()
        stand.addRow("Name", self.scope_name)
        stand.addRow("Condenser", self.condenser)
        stand.addRow("Optovar", self.optovar)

        # --- turret, in physical order
        self.rows: list[ObjectiveRow] = []
        turret_box = QtWidgets.QVBoxLayout()
        turret_box.setSpacing(4)
        header = QtWidgets.QLabel(
            "In turret order — the order matters for stepping and detection")
        header.setProperty("role", "key")
        turret_box.addWidget(header)
        positions = list(setup.scope.turret.positions) + [None] * 6
        for i in range(6):
            row = ObjectiveRow(i)
            row.set_value(positions[i])
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
        self._refresh()
        self.setStyleSheet(theme.stylesheet())

    # ---- live feedback ---------------------------------------------------

    def _refresh(self) -> None:
        probe = self._build()
        self.key.setText(probe.calibration_key())

    def _build(self) -> Setup:
        camera = CameraProfile(
            serial=self._setup.camera.serial,
            name=self.camera_name.text().strip() or "Camera",
            model=self._setup.camera.model,
            relay=self.relay.text().strip())
        positions = [row.value() for row in self.rows]
        while positions and positions[-1] is None:
            positions.pop()                     # trailing blanks are not gaps
        current = min(self._setup.scope.turret.current,
                      max(len(positions) - 1, 0))
        scope = ScopeProfile(
            id=self._setup.scope.id,
            name=self.scope_name.text().strip() or "Microscope",
            turret=Turret(positions=positions, current=current),
            optovar=_parse_factors(self.optovar.text()),
            optovar_current=self._setup.scope.optovar_current,
            condenser=self.condenser.text().strip())
        return Setup(camera=camera, scope=scope,
                     illumination=self._setup.illumination)

    def _save(self) -> None:
        self.result_setup = self._build()
        if self._library is not None:
            self._library.cameras[self.result_setup.camera.serial] = \
                self.result_setup.camera
            self._library.scopes[self.result_setup.scope.id] = \
                self.result_setup.scope
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
