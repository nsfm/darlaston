"""How the program looks and where things sit. Setup > Appearance.

The home for choices that are about the person at the bench rather than
the instrument: which side the rail of panels sits on, whether the
window wears our frame or the desktop's, and -- when there is more than
one -- the palette. Each applies the moment it changes, because the only
way to judge a layout is to look at it with the slide you work on.

Nothing here reaches a file. The look is not provenance.
"""
from __future__ import annotations

import sys

from PySide6 import QtWidgets

from ..i18n import _
from .perf_ui import _heading, _note
from .shell import FramedDialog


class AppearanceDialog(FramedDialog):
    """Sidebar side, window frame, and room for the palette."""

    def __init__(self, settings, on_sidebar, parent=None, *,
                 frame_available: bool = False, frame_on: bool = False,
                 set_frame=None) -> None:
        super().__init__(parent, width=460)
        self.setWindowTitle(_("appearance.title"))
        self._settings = settings
        self._on_sidebar = on_sidebar
        self._set_frame = set_frame

        col = self.content
        col.setSpacing(10)

        col.addWidget(_heading(_("appearance.sidebar.heading")))
        self._side = QtWidgets.QButtonGroup(self)
        row = QtWidgets.QHBoxLayout()
        # Both spelled out, so the catalogue's consistency check can see
        # them: a key assembled in a loop is a key nothing appears to name.
        for value, label in (("left", _("appearance.sidebar.left")),
                             ("right", _("appearance.sidebar.right"))):
            button = QtWidgets.QRadioButton(label)
            button.setChecked(settings.sidebar_side == value)
            button.setProperty("value", value)
            self._side.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        col.addLayout(row)
        col.addWidget(_note(_("appearance.sidebar.note")))
        self._side.buttonToggled.connect(self._side_changed)

        # Where the desktop already draws a good title bar this is a
        # matter of taste, and where it draws a bad one or none at all it
        # is the difference between looking finished and not. macOS is
        # left out: there the frame is never ours to take, only to
        # restyle. The row is absent rather than greyed where it does not
        # apply: a switch over a capability that does not exist is worse
        # than no switch.
        self.frame = QtWidgets.QCheckBox(_("appearance.frame.label"))
        self.frame.setChecked(frame_on)
        self.frame.setVisible(frame_available and sys.platform != "darwin")
        if self.frame.isVisibleTo(self):
            col.addSpacing(4)
            col.addWidget(_heading(_("appearance.frame.heading")))
            col.addWidget(self.frame)
            col.addWidget(_note(_("appearance.frame.note")))
            self.frame.toggled.connect(self._frame_changed)

        col.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        col.addWidget(buttons)
        self.finish()

    def _side_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        self._settings.sidebar_side = button.property("value")
        # Saved on every change rather than on close, like the performance
        # page: the way this gets used is to switch, look, and switch back.
        self._settings.save()
        self._on_sidebar()

    def _frame_changed(self, on: bool) -> None:
        if self._set_frame is None:
            return
        took = self._set_frame(bool(on))
        if on and not took:
            # Could not take it. Put the tick back rather than leave a
            # checked box over a window the desktop is still drawing.
            self.frame.blockSignals(True)
            self.frame.setChecked(False)
            self.frame.blockSignals(False)
