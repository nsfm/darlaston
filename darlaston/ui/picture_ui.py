"""The Picture row: the in-camera pipeline, on the rail, behind a disclosure.

A camera that finishes its own picture carries the knobs that finish
it: brightness, contrast, saturation, sharpness, gamma, hue. On such a
camera these are the whole image pipeline, and there is no DNG to fix
it in later, so hiding them is leaving the only knobs on the floor. But
they are set once per camera, not once per slide, so they sit behind a
header that opens: closed, it is one line of rail; open, a few slim
bars with the device's own range and default. No summary line on the
closed header. Playing teaches faster than reading.

The row shows nothing of its own: it is given the controls that fill
its slots and emits what a person moves. Who fills the slots and where
the values are kept is the window's business.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..camera.roles import PICTURE_SLOTS
from ..i18n import _
from . import theme
from .widgets import ValueBar


class PictureRow(QtWidgets.QWidget):
    """A disclosure over the picture controls the camera has."""

    #: (slot, control id, value) as a bar is moved.
    changed = QtCore.Signal(str, int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._controls: dict[str, object] = {}
        self._bars: dict[str, ValueBar] = {}

        self.header = QtWidgets.QToolButton()
        self.header.setText(_("shell.group.picture").upper())
        self.header.setCheckable(True)
        self.header.setChecked(False)
        self.header.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.header.setProperty("role", "disclosure")
        self.header.setToolTip(_("shell.picture.tooltip"))
        self.header.toggled.connect(self._toggle)
        theme.dress(self.header, self._header_sheet)

        self.body = QtWidgets.QWidget()
        self._body_col = QtWidgets.QVBoxLayout(self.body)
        self._body_col.setContentsMargins(0, 0, 0, 0)
        self._body_col.setSpacing(6)
        # Spelled out, one key per slot, so the catalogue check can see
        # them; the order is the order of PICTURE_SLOTS.
        labels = {"brightness": _("shell.picture.brightness"),
                  "contrast": _("shell.picture.contrast"),
                  "saturation": _("shell.picture.saturation"),
                  "sharpness": _("shell.picture.sharpness"),
                  "gamma": _("shell.picture.gamma"),
                  "hue": _("shell.picture.hue")}
        for slot in PICTURE_SLOTS:
            bar = ValueBar(labels[slot])
            bar.setFixedHeight(20)
            bar.valueChanged.connect(lambda value, s=slot: self._moved(s, value))
            bar.setVisible(False)
            self._bars[slot] = bar
            self._body_col.addWidget(bar)
        self.body.setVisible(False)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(7)
        col.addWidget(self.header)
        col.addWidget(self.body)
        self._chevron()

    # ---- what the camera has -------------------------------------------------

    def set_controls(self, slots: dict) -> None:
        """The controls filling the picture slots, by slot; a slot with
        None has no bar. The row hides itself when nothing fills any."""
        self._controls = {s: c for s, c in slots.items() if c is not None}
        for slot, bar in self._bars.items():
            control = self._controls.get(slot)
            bar.setVisible(control is not None)
            if control is None:
                continue
            bar.blockSignals(True)
            bar.setRange(int(control.min), int(control.max))
            value = control.value if control.value is not None else control.default
            bar.setValue(int(value))
            bar.blockSignals(False)
            bar.set_value_text(self._text(control, int(value)))
            bar.setEnabled(bool(control.writable) and not control.inactive)
            bar.setToolTip(_("shell.picture.owned") if control.inactive
                           else _("shell.picture.bar.tooltip", name=control.name,
                                  default=control.default))
        self.setVisible(bool(self._controls))

    def show_value(self, slot: str, value: int) -> None:
        """A value the camera reports back, without emitting it again."""
        bar = self._bars.get(slot)
        control = self._controls.get(slot)
        if bar is None or control is None:
            return
        bar.blockSignals(True)
        bar.setValue(int(value))
        bar.blockSignals(False)
        bar.set_value_text(self._text(control, int(value)))

    @property
    def open(self) -> bool:
        return self.header.isChecked()

    def set_open(self, on: bool) -> None:
        self.header.setChecked(bool(on))

    # ---- inside --------------------------------------------------------------

    def _moved(self, slot: str, value: int) -> None:
        control = self._controls.get(slot)
        if control is None:
            return
        self._bars[slot].set_value_text(self._text(control, int(value)))
        self.changed.emit(slot, int(control.id), int(value))

    @staticmethod
    def _text(control, value: int) -> str:
        if control.kind == "menu" and control.menu:
            k = value - control.min
            if 0 <= k < len(control.menu) and control.menu[k]:
                return control.menu[k]
        return str(value)

    def _toggle(self, on: bool) -> None:
        self.body.setVisible(bool(on))
        self._chevron()

    def _chevron(self) -> None:
        from . import icons
        name = "chevron-up" if self.header.isChecked() else "chevron-down"
        self.header.setIcon(QtGui.QIcon(icons.path_for(name, theme.DIM)))
        self.header.setIconSize(QtCore.QSize(10, 10))

    @staticmethod
    def _header_sheet() -> str:
        return (f"QToolButton {{ border: 0; padding: 0; background: transparent;"
                f" color: {theme.DIM}; font-size: 9px; letter-spacing: 1.2px; }}"
                f"QToolButton:hover {{ color: {theme.INK}; }}")
