"""How the program looks and where things sit. Setup > Appearance.

The home for choices that are about the person at the bench rather than
the instrument: the palette, the ink for the marks over the picture,
which side the rail of panels sits on, and whether the window wears our
frame or the desktop's. Each applies the moment it changes, because the
only way to judge a look is to look at it with the slide you work on.

Nothing here reaches a file. The look is not provenance.
"""
from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import _
from . import theme
from .perf_ui import _heading
from .shell import FramedDialog


class PaletteChip(QtWidgets.QAbstractButton):
    """One organism, as a tile: its ground, its panel, a stroke of its
    ink, its accent as a dot, and its name underneath.

    Painted rather than styled, because it shows its *own* colours
    whatever the page is wearing; and shown in the variant the page is
    in where the organism has it, with the contrast switch applied, so
    the row previews what a click would give. Checked, it wears a ring
    in the current accent.
    """

    TILE = QtCore.QSize(72, 46)

    def __init__(self, organism: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.organism = organism
        self.label = label
        self.setCheckable(True)
        self.setProperty("value", organism)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self._variant = "dark"
        self._high = False

    def preview(self, variant: str, high: bool) -> None:
        self._variant, self._high = variant, high
        self.update()

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(84, self.TILE.height() + 30)

    def paintEvent(self, _event) -> None:
        shown = theme.resolve(theme.key_for(self.organism, self._variant),
                              self._high)
        with QtGui.QPainter(self) as p:
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            frame = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
            if self.isChecked():
                p.setPen(QtGui.QPen(QtGui.QColor(theme.BRASS), 1))
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(frame, 4, 4)
            tile = QtCore.QRectF((self.width() - self.TILE.width()) / 2, 7,
                                 self.TILE.width(), self.TILE.height())
            path = QtGui.QPainterPath()
            path.addRoundedRect(tile, 4, 4)
            p.save()
            p.setClipPath(path)
            p.fillRect(tile, QtGui.QColor(shown.bg))
            p.fillRect(QtCore.QRectF(tile.left(), tile.bottom() - 18,
                                     tile.width(), 18), QtGui.QColor(shown.panel))
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(shown.ink))
            p.drawRoundedRect(QtCore.QRectF(tile.left() + 9, tile.top() + 11,
                                            30, 3), 1.5, 1.5)
            p.setBrush(QtGui.QColor(shown.accent))
            p.drawEllipse(QtCore.QRectF(tile.right() - 20, tile.top() + 8, 11, 11))
            p.restore()
            p.setPen(QtGui.QPen(QtGui.QColor(shown.line), 1))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(tile, 4, 4)

            font = QtGui.QFont(self.font())
            font.setPixelSize(9)
            font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 0.4)
            p.setFont(font)
            p.setPen(QtGui.QColor(theme.INK if self.isChecked() else theme.DIM))
            p.drawText(QtCore.QRectF(0, tile.bottom() + 4, self.width(), 18),
                       QtCore.Qt.AlignmentFlag.AlignHCenter
                       | QtCore.Qt.AlignmentFlag.AlignTop,
                       self.label)


def _pill_sheet() -> str:
    return (f"QPushButton {{ border: 1px solid {theme.LINE}; border-radius: 10px;"
            f" padding: 3px 12px; font-size: 10px; color: {theme.INK};"
            " background: transparent; }"
            f"QPushButton:hover {{ border-color: {theme.BRASS}; }}"
            f"QPushButton:checked {{ color: {theme.ON_BRASS}; background: {theme.BRASS};"
            f" border-color: {theme.BRASS}; }}"
            f"QPushButton:disabled {{ color: {theme.LINE}; border-color: transparent; }}")


class AppearanceDialog(FramedDialog):
    """Palette by organism and variant, the marks, the sidebar, the frame."""

    def __init__(self, settings, on_sidebar, parent=None, *,
                 frame_available: bool = False, frame_on: bool = False,
                 set_frame=None, on_palette=None, on_marks=None) -> None:
        super().__init__(parent, width=560)
        self.setWindowTitle(_("appearance.title"))
        self._settings = settings
        self._on_sidebar = on_sidebar
        self._set_frame = set_frame
        self._on_palette = on_palette
        self._on_marks = on_marks

        col = self.content
        col.setSpacing(10)

        # Two axes. The organism is a chip; the variant a pill, greyed
        # where the organism has no such palette; high contrast a switch
        # over either. Fifteen palettes would be fifteen radios on one
        # axis, and a sixth organism adds a chip here, not three rows.
        # The names are spelled out so the catalogue check can see them;
        # an organism from palettes.json is shown by its key.
        col.addWidget(_heading(_("appearance.palette.heading")))
        names = {"pseudoscorpion": _("appearance.palette.pseudoscorpion"),
                 "blepharisma": _("appearance.palette.blepharisma"),
                 "stentor": _("appearance.palette.stentor"),
                 "diatom": _("appearance.palette.diatom"),
                 "noctiluca": _("appearance.palette.noctiluca")}
        self._palette = QtWidgets.QButtonGroup(self)
        self._chips: list[PaletteChip] = []
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        for organism in theme.organisms():
            chip = PaletteChip(organism, names.get(organism, organism))
            self._palette.addButton(chip)
            self._chips.append(chip)
            row.addWidget(chip)
        row.addStretch(1)
        col.addLayout(row)
        self._palette.buttonToggled.connect(self._organism_changed)

        self._variants = QtWidgets.QButtonGroup(self)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        for value, label in (("dark", _("appearance.variant.dark")),
                             ("light", _("appearance.variant.light"))):
            pill = QtWidgets.QPushButton(label)
            pill.setCheckable(True)
            pill.setProperty("value", value)
            pill.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            theme.dress(pill, _pill_sheet)
            self._variants.addButton(pill)
            row.addWidget(pill)
        row.addSpacing(10)
        self.high = QtWidgets.QCheckBox(_("appearance.high_contrast"))
        row.addWidget(self.high)
        row.addStretch(1)
        self._chosen = QtWidgets.QLabel("")
        self._chosen.setProperty("role", "key")
        row.addWidget(self._chosen)
        col.addLayout(row)
        self._variants.buttonToggled.connect(self._variant_changed)
        self.high.toggled.connect(self._high_changed)
        self._show_choice()
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

    # ---- the palette -----------------------------------------------------

    def _show_choice(self) -> None:
        """The chips, pills, switch and readout, from the settings."""
        chosen = theme.PALETTES.get(self._settings.palette,
                                    theme.PALETTES[theme.DEFAULT_PALETTE])
        have = theme.variants_of(chosen.organism)
        high = bool(self._settings.high_contrast)
        for group, value in ((self._palette, chosen.organism),
                             (self._variants, chosen.variant)):
            for button in group.buttons():
                button.blockSignals(True)
                button.setChecked(button.property("value") == value)
                button.blockSignals(False)
        for pill in self._variants.buttons():
            pill.setEnabled(pill.property("value") in have)
        self.high.blockSignals(True)
        self.high.setChecked(high)
        self.high.blockSignals(False)
        for chip in self._chips:
            chip.preview(chosen.variant, high)
        readout = next((chip.label for chip in self._chips
                        if chip.isChecked()), chosen.organism)
        if chosen.variant != "dark":
            variant = next((b.text() for b in self._variants.buttons()
                            if b.isChecked()), chosen.variant)
            readout = f"{readout} · {variant}"
        self._chosen.setText(readout)

    def _choose(self, key: str, high: bool) -> None:
        self._settings.palette = key
        self._settings.high_contrast = high
        # Saved on every change rather than on close, like the performance
        # page: the way this gets used is to switch, look, and switch back.
        self._settings.save()
        if self._on_palette is not None:
            self._on_palette()
        # This page too: its sheet was built at construction, and the
        # person is looking straight at it.
        self.setStyleSheet(theme.stylesheet())
        self._show_choice()

    def _organism_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        chosen = theme.PALETTES.get(self._settings.palette)
        variant = chosen.variant if chosen else "dark"
        self._choose(theme.key_for(button.property("value"), variant),
                     bool(self._settings.high_contrast))

    def _variant_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        chosen = theme.PALETTES.get(self._settings.palette,
                                    theme.PALETTES[theme.DEFAULT_PALETTE])
        self._choose(theme.key_for(chosen.organism, button.property("value")),
                     bool(self._settings.high_contrast))

    def _high_changed(self, on: bool) -> None:
        self._choose(self._settings.palette, bool(on))

    # ---- the rest ----------------------------------------------------------

    def _side_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        self._settings.sidebar_side = button.property("value")
        self._settings.save()
        self._on_sidebar()

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
