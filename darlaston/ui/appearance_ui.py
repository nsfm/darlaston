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
from .perf_ui import _heading
from .shell import FramedDialog


class AppearanceDialog(FramedDialog):
    """Sidebar side, window frame, and room for the palette."""

    def __init__(self, settings, on_sidebar, parent=None, *,
                 frame_available: bool = False, frame_on: bool = False,
                 set_frame=None, on_palette=None, on_marks=None) -> None:
        super().__init__(parent, width=460)
        self.setWindowTitle(_("appearance.title"))
        self._settings = settings
        self._on_sidebar = on_sidebar
        self._set_frame = set_frame
        self._on_palette = on_palette
        self._on_marks = on_marks

        col = self.content
        col.setSpacing(10)

        # The palettes, every one named for something seen down the tube.
        # Spelled out, so the catalogue check can see the keys; the order
        # is the order in the theme.
        col.addWidget(_heading(_("appearance.palette.heading")))
        self._palette = QtWidgets.QButtonGroup(self)
        labels = {"pseudoscorpion": _("appearance.palette.pseudoscorpion"),
                  "blepharisma": _("appearance.palette.blepharisma"),
                  "stentor": _("appearance.palette.stentor"),
                  "diatom": _("appearance.palette.diatom"),
                  "blepharisma-light": _("appearance.palette.blepharisma_light"),
                  "noctiluca": _("appearance.palette.noctiluca")}
        from . import theme
        for name in theme.PALETTES:
            button = QtWidgets.QRadioButton(labels.get(name, name))
            button.setChecked(settings.palette == name)
            button.setProperty("value", name)
            self._palette.addButton(button)
            col.addWidget(button)
        self._palette.buttonToggled.connect(self._palette_changed)
        col.addSpacing(4)

        # The marks over the picture: the focus box, peaking, the ring.
        # Their own choice, because the palette dresses the chrome and
        # these have to be seen against whatever is on the stage. Auto
        # reads the picture; the rest are inks no common mountant is.
        col.addWidget(_heading(_("appearance.marks.heading")))
        self._marks = QtWidgets.QButtonGroup(self)
        for value, label in (("auto", _("appearance.marks.auto")),
                             ("accent", _("appearance.marks.accent"))):
            button = QtWidgets.QRadioButton(label)
            button.setChecked(settings.marks == value)
            button.setProperty("value", value)
            self._marks.addButton(button)
            col.addWidget(button)
        row = QtWidgets.QHBoxLayout()
        for value, label in (("gold", _("appearance.marks.gold")),
                             ("green", _("appearance.marks.green")),
                             ("cyan", _("appearance.marks.cyan")),
                             ("magenta", _("appearance.marks.magenta"))):
            button = QtWidgets.QRadioButton(label)
            button.setChecked(settings.marks == value)
            button.setProperty("value", value)
            self._marks.addButton(button)
            row.addWidget(button)
        row.addStretch(1)
        col.addLayout(row)
        self._marks.buttonToggled.connect(self._marks_changed)
        col.addSpacing(4)

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

    def _palette_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        self._settings.palette = button.property("value")
        self._settings.save()
        if self._on_palette is not None:
            self._on_palette()
        # This page too: its sheet was built at construction, and the
        # person is looking straight at it.
        from . import theme
        self.setStyleSheet(theme.stylesheet())

    def _marks_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        self._settings.marks = button.property("value")
        self._settings.save()
        if self._on_marks is not None:
            self._on_marks()

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
