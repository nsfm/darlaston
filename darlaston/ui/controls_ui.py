"""The camera controls page: every control the device reports, live.

In the cameras window, beside the profile editor, for the camera that
is open. One row per control, in the device's order and under the
device's names, with a bar, a tick or a menu for its value; an Auto
tick where the camera can run it itself; a reset to the device's
default; and "use as", which routes the control to a slot on the rail.
The heuristic's guess is shown as the placeholder, so a person sees
what we guessed before deciding we guessed wrong.

This is the whole remap tool. No wizard, no detection: the person
drags a control, watches the preview beside the window, and says what
it is. It is also how anyone finds out what Roll and Zoom do.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..camera import roles as roles_
from ..i18n import _
from . import theme
from .widgets import ValueBar


def _elide(text: str, keep: int) -> str:
    return text if len(text) <= keep else text[:keep - 1] + "…"


class ControlsPage(QtWidgets.QWidget):
    """Rows for a live camera's controls, with routing to the rail's slots.

    `live` is whatever offers `controls()`, `set_control(id, value)` and
    `set_auto(id, on)`: the session, in the program. `roles` is the
    profile's dict, edited in place; the dialog that owns the profile
    saves it.
    """

    #: A role changed: (slot or "", control id). The dialog marks the
    #: profile dirty; the window re-resolves the slots on accept.
    role_changed = QtCore.Signal(str, int)

    def __init__(self, live, roles: dict, parent=None) -> None:
        super().__init__(parent)
        self._live = live
        self._roles = roles
        self._rows: list[dict] = []

        self.note = QtWidgets.QLabel(_("controls.note"))
        self.note.setWordWrap(True)
        self.note.setProperty("role", "body")

        self.grid = QtWidgets.QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(5)
        for col, text in enumerate((_("controls.column.control"),
                                    _("controls.column.value"),
                                    _("controls.column.auto"),
                                    _("controls.column.use_as"))):
            head = QtWidgets.QLabel(text.upper())
            head.setProperty("role", "key")
            self.grid.addWidget(head, 0, col)
        # Fixed columns, so a long control name cannot push "use as" off
        # the edge: the name is elided instead.
        self.grid.setColumnMinimumWidth(0, 150)
        self.grid.setColumnMinimumWidth(1, 170)
        self.grid.setColumnMinimumWidth(3, 140)
        self.grid.setColumnStretch(4, 1)

        inner = QtWidgets.QWidget()
        inner_col = QtWidgets.QVBoxLayout(inner)
        inner_col.setContentsMargins(0, 0, 8, 0)
        inner_col.addLayout(self.grid)
        inner_col.addStretch(1)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(360)
        self.scroll.setWidget(inner)

        self.defaults = QtWidgets.QPushButton(_("controls.action.defaults"))
        self.defaults.setToolTip(_("controls.defaults.tooltip"))
        self.defaults.clicked.connect(self._all_defaults)
        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.defaults)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(self.note)
        col.addWidget(self.scroll, 1)
        col.addLayout(foot)
        self.reload()

    # ---- the rows ----------------------------------------------------------

    def reload(self) -> None:
        """Rebuild from the camera's controls as they stand now."""
        for row in self._rows:
            for w in row["widgets"]:
                self.grid.removeWidget(w)
                w.deleteLater()
        self._rows = []
        controls = tuple(self._live.controls()) if self._live is not None else ()
        guesses = {slot: c.id for slot, c in roles_.resolve(controls, {}).items()
                   if c is not None}
        # Slot names spelled out for the catalogue check; "use as" offers
        # them in the rail's order.
        slot_labels = {"focus": _("controls.slot.focus"),
                       "brightness": _("controls.slot.brightness"),
                       "contrast": _("controls.slot.contrast"),
                       "saturation": _("controls.slot.saturation"),
                       "sharpness": _("controls.slot.sharpness"),
                       "gamma": _("controls.slot.gamma"),
                       "hue": _("controls.slot.hue")}
        for i, control in enumerate(controls, start=1):
            widgets = []
            name = QtWidgets.QLabel(_elide(control.name, 22))
            name.setToolTip(f"{control.name}  ({control.id:#010x})")
            name.setProperty("role", "label")
            name.setMaximumWidth(150)
            name.setEnabled(not control.inactive)
            widgets.append(name)
            self.grid.addWidget(name, i, 0)

            value = self._value_widget(control)
            widgets.append(value)
            self.grid.addWidget(value, i, 1)

            auto = QtWidgets.QCheckBox()
            auto.setVisible(control.auto)
            if control.auto:
                auto.setChecked(bool(control.inactive))
                auto.setToolTip(_("controls.auto.tooltip"))
                auto.toggled.connect(lambda on, c=control: self._auto(c, on))
            widgets.append(auto)
            self.grid.addWidget(auto, i, 2)

            use = QtWidgets.QComboBox()
            guessed = next((s for s, cid in guesses.items() if cid == control.id), "")
            placeholder = (_("controls.use_as.guess", slot=slot_labels[guessed])
                           if guessed else _("controls.use_as.nothing"))
            use.addItem(placeholder, "")
            for slot in roles_.SLOTS:
                use.addItem(slot_labels[slot], slot)
            use.addItem(_("controls.use_as.none"), "none")
            current = next((s for s, cid in self._roles.items()
                            if cid == control.id and s in roles_.SLOTS), None)
            if current:
                use.setCurrentIndex(use.findData(current))
            elif any(cid == 0 and s == guessed for s, cid in self._roles.items()):
                use.setCurrentIndex(use.findData("none"))
            use.currentIndexChanged.connect(
                lambda _i, c=control, box=use: self._route(c, box.currentData()))
            use.setToolTip(_("controls.use_as.tooltip"))
            use.setFixedWidth(140)
            widgets.append(use)
            self.grid.addWidget(use, i, 3)
            self._rows.append({"control": control, "widgets": widgets, "value": value})

    def _value_widget(self, control) -> QtWidgets.QWidget:
        if control.kind == "bool":
            box = QtWidgets.QCheckBox()
            box.setChecked(bool(control.value))
            box.toggled.connect(lambda on, c=control: self._set(c, 1 if on else 0))
            box.setEnabled(control.writable)
            return box
        if control.kind == "menu" and control.menu:
            box = QtWidgets.QComboBox()
            for k, entry in enumerate(control.menu):
                if entry:
                    box.addItem(entry, control.min + k)
            if control.value is not None:
                box.setCurrentIndex(max(0, box.findData(control.value)))
            box.currentIndexChanged.connect(
                lambda _i, c=control, b=box: self._set(c, b.currentData()))
            box.setEnabled(control.writable)
            return box
        bar = ValueBar("")
        bar.setFixedHeight(20)
        bar.setFixedWidth(170)
        bar.setRange(int(control.min), int(control.max))
        value = control.value if control.value is not None else control.default
        bar.setValue(int(value))
        bar.set_value_text(str(int(value)))
        bar.setEnabled(control.writable and not control.inactive)
        bar.setToolTip(_("controls.bar.tooltip", lo=control.min, hi=control.max,
                         default=control.default))
        bar.valueChanged.connect(lambda v, c=control, b=bar: (
            b.set_value_text(str(int(v))), self._set(c, int(v))))
        return bar

    # ---- what a row does -----------------------------------------------------

    def _set(self, control, value) -> None:
        if value is None or self._live is None:
            return
        self._live.set_control(int(control.id), int(value))

    def _auto(self, control, on: bool) -> None:
        if self._live is None:
            return
        self._live.set_auto(int(control.id), bool(on))
        # The camera's answer is the truth about who owns what now.
        self.reload()

    def _route(self, control, slot) -> None:
        """Route a control to a slot, or free it. One control per slot,
        one slot per control; the profile's dict says the rest."""
        for s in list(self._roles):
            if self._roles[s] == control.id:
                del self._roles[s]
        if slot == "none":
            guessed = next((s for s, c in roles_.resolve(
                tuple(r["control"] for r in self._rows), {}).items()
                if c is not None and c.id == control.id), None)
            if guessed:
                self._roles[guessed] = 0
            self.role_changed.emit("", int(control.id))
            return
        if slot:
            self._roles[slot] = int(control.id)
            self.role_changed.emit(slot, int(control.id))
            # A dropdown that says the same slot on another row is stale.
            for row in self._rows:
                box = row["widgets"][3]
                if row["control"].id != control.id and box.currentData() == slot:
                    box.blockSignals(True)
                    box.setCurrentIndex(0)
                    box.blockSignals(False)
        else:
            self.role_changed.emit("", int(control.id))

    def _all_defaults(self) -> None:
        for row in self._rows:
            control = row["control"]
            if control.writable and not control.inactive:
                self._set(control, control.default)
        self.reload()
