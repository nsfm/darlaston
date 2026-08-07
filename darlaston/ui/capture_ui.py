"""Shutter, subject, and settings.

The shutter is deliberately the largest control in the window and never moves.
The subject field sits with it rather than in a preferences dialog, because it
is the one piece of context only the operator knows and it is the difference
between an archive and a folder of numbered files.
"""
from __future__ import annotations

from pathlib import Path

import cv2
from PySide6 import QtCore, QtGui, QtWidgets

from ..i18n import N_, _
from ..session.settings import (TOKENS, Settings, filename_problem,
                                pictures_dir)
from . import theme
from .framed import FramedDialog


#: The style list, spelled out rather than built from `scalebar.STYLES`
#: with an f-string. The catalogue check finds message ids by reading the
#: source for literals, so a key assembled at runtime is a key it cannot
#: see -- it reported all six of these as carried by nobody.
#: Faces, bundled ones first. Spelled out for the same reason the styles
#: are: the catalogue check reads source for literals.
SCALE_BAR_FACES = (
    ("IBM Plex Mono", N_("capture.bar.face.plex_mono")),
    ("IBM Plex Sans", N_("capture.bar.face.plex_sans")),
    ("sans-serif", N_("capture.bar.face.sans")),
    ("serif", N_("capture.bar.face.serif")),
    ("hershey", N_("capture.bar.face.hershey")),
)

SCALE_BAR_CORNERS = (
    ("br", N_("capture.bar.corner.br")),
    ("bl", N_("capture.bar.corner.bl")),
    ("tr", N_("capture.bar.corner.tr")),
    ("tl", N_("capture.bar.corner.tl")),
)

SCALE_BAR_LABELS = (
    ("auto", N_("capture.bar.label.auto")),
    ("above", N_("capture.bar.label.above")),
    ("below", N_("capture.bar.label.below")),
)

SCALE_BAR_SIZES = (
    ("small", N_("capture.bar.size.small")),
    ("medium", N_("capture.bar.size.medium")),
    ("large", N_("capture.bar.size.large")),
)

SCALE_BAR_STYLES = (
    ("adaptive", N_("capture.files.scale_bar.style.adaptive")),
    ("plate", N_("capture.files.scale_bar.style.plate")),
    ("scrim", N_("capture.files.scale_bar.style.scrim")),
    ("ruler", N_("capture.files.scale_bar.style.ruler")),
    ("caps", N_("capture.files.scale_bar.style.caps")),
    ("shadow", N_("capture.files.scale_bar.style.shadow")),
)


class ShutterButton(QtWidgets.QPushButton):
    """Big, bottom right, and honest about what it is doing.

    A capture stops the preview, reconfigures the camera, pulls forty megabytes
    and writes them. That is seconds, not milliseconds, so the control reports
    its stage rather than appearing to hang.
    """

    #: What the capture reports -> the words for it. Keys rather than words,
    #: because the states arrive from the capture thread as identifiers and
    #: are resolved here.
    LABELS = {"idle": N_("capture.shutter.action.capture"),
              "exposing": N_("capture.shutter.state.exposing"),
              "calibrating": N_("capture.shutter.state.calibrating"),
              "writing": N_("capture.shutter.state.writing")}

    def _label(self, state: str) -> str:
        # Burst states arrive as "exposing 3/16". The count is shown, because
        # a sixteen-frame average with a mute progress readout looks exactly
        # like a hang -- but it is split off the identifier first, so the
        # words in front of it still come from the catalogue. `_sep` rather
        # than `_`, which is the catalogue lookup and must not be shadowed.
        name, _sep, progress = state.partition(" ")
        if name == "exposing" and progress:
            return _("capture.shutter.state.exposing.burst", progress=progress)
        key = ShutterButton.LABELS.get(name)
        if key is None:
            return _("capture.shutter.state.other", state=state)
        if name == "idle" and self._average > 1:
            return _("capture.shutter.action.average", n=self._average)
        return _(key)

    def __init__(self) -> None:
        super().__init__(_("capture.shutter.action.capture"))
        self._state = "idle"
        self._average = 1
        self.setMinimumHeight(46)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._restyle()

    def set_average(self, n: int) -> None:
        """Carry the burst count on the button itself, so a sixteen-frame
        capture is never a surprise at the moment of pressing."""
        self._average = n
        if self._state == "idle":
            self.setText(self._label("idle"))

    def set_state(self, state: str) -> None:
        self._state = state
        self.setText(self._label(state))
        self.setEnabled(state == "idle" and self.isEnabled() or state == "idle")
        self._restyle()

    def set_available(self, available: bool) -> None:
        self.setEnabled(available and self._state == "idle")
        self._restyle()

    def _restyle(self) -> None:
        busy = self._state != "idle"
        live = not busy and self.isEnabled()
        # Filled rather than outlined. This is the one thing in the window a
        # person is reaching for, and an outline is the same weight as every
        # border around it; the fill is what makes it findable without
        # reading. Unavailable stays an outline, so the difference between
        # "ready" and "not yet" is a shape and not only a shade.
        fill = theme.BRASS if live else "transparent"
        edge = theme.BRASS if live else theme.LINE
        # Dark on amber, not light: #e4e7e0 on #c89b4a is about 1.9:1, which
        # is under any legibility threshold there is. The near-black ground
        # gives about 7:1 on the same fill.
        text = theme.BG if live else theme.DIM
        # Right side square: the averaging arrow butts against it to make one
        # pill, so the seam between them must not be rounded.
        self.setStyleSheet(
            f"QPushButton {{ border: 1px solid {edge};"
            f" border-top-left-radius: 4px; border-bottom-left-radius: 4px;"
            f" margin: 1px 0 1px 1px;"
            f" color: {text}; font-size: 14px; letter-spacing: 1px;"
            f" font-family: '{theme.load_fonts()['sans']}'; font-weight: 600;"
            f" background: {fill}; }}"
            f"QPushButton:hover:enabled {{ background: {theme.BRASS_LIT}; }}"
            f"QPushButton:pressed:enabled {{ background: {theme.BRASS_DEEP}; }}")


class ShutterBar(QtWidgets.QWidget):
    """The shutter, its averaging menu, and the last result, as one control.

    Three rail rows became one. The averaging choice is a split-button arrow
    rather than a segmented row, because it is picked rarely and read never;
    the result line lives *under the button inside the same frame*, so a
    capture reports where the eye already is instead of somewhere below.
    """

    triggered = QtCore.Signal()
    average_changed = QtCore.Signal(int)

    CHOICES = (1, 4, 16)

    def __init__(self) -> None:
        super().__init__()
        self.button = ShutterButton()
        self.button.clicked.connect(self.triggered)

        self.arrow = QtWidgets.QToolButton()
        self.arrow.setText("▾")
        self.arrow.setFixedSize(26, 46)
        self.arrow.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.arrow.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.arrow.setToolTip(_("capture.average.tooltip"))

        self._menu = QtWidgets.QMenu(self)
        self._actions = {}
        group = QtGui.QActionGroup(self._menu)
        group.setExclusive(True)
        for n in self.CHOICES:
            # Both spelled out rather than one key with a count, because
            # "single frame" is not the {n}=1 form of "average ×{n}" in
            # English and need not be in any other language either.
            act = self._menu.addAction(
                _("capture.average.action.single") if n == 1
                else _("capture.average.action.many", n=n))
            act.setCheckable(True)
            act.setChecked(n == 1)
            group.addAction(act)
            act.triggered.connect(lambda checked=False, n=n: self._choose(n))
            self._actions[n] = act
        self.arrow.setMenu(self._menu)

        self.result = QtWidgets.QLabel("")
        self.result.setProperty("role", "key")
        self.result.setWordWrap(True)
        self.result.setVisible(False)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.button, 1)
        row.addWidget(self.arrow)

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addLayout(row)
        col.addWidget(self.result)

        self._frames = 1
        self._restyle_arrow()

    @property
    def frames(self) -> int:
        return self._frames

    def _choose(self, n: int) -> None:
        self._frames = n
        self.button.set_average(n)
        self._restyle_arrow()
        self.average_changed.emit(n)

    def _restyle_arrow(self) -> None:
        colour = theme.BRASS if self._frames > 1 else theme.DIM
        self.arrow.setStyleSheet(
            f"QToolButton {{ border: 1px solid {colour}; border-left: 0;"
            f" border-top-right-radius: 4px; border-bottom-right-radius: 4px;"
            f" margin: 1px 1px 1px 0; color: {colour};"
            f" background: transparent; }}"
            f"QToolButton::menu-indicator {{ image: none; }}"
            f"QToolButton:hover {{ background: rgba(200,155,74,0.10); }}")

    # ---- state -----------------------------------------------------------

    def set_state(self, state: str) -> None:
        self.button.set_state(state)
        self.arrow.setEnabled(state == "idle")

    def set_available(self, available: bool) -> None:
        self.button.set_available(available)
        self.arrow.setEnabled(available)

    def set_result(self, text: str, ok: bool = True) -> None:
        self.result.setVisible(bool(text))
        self.result.setText(text)
        self.result.setStyleSheet(
            f"color: {theme.DIM if ok else theme.BAD};")


class SubjectField(QtWidgets.QWidget):
    """What is on the slide. Feeds the filename and the metadata."""

    changed = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        # Plain nouns rather than an example. A placeholder is read as a
        # hint about what goes in the box, and "dopamine arrangement" only
        # reads that way to someone who already knows what one is.
        self.setToolTip(_("capture.subject.tooltip"))
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText(_("capture.subject.placeholder"))
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self.changed)
        self.edit.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {theme.LINE}; border-radius: 3px;"
            f" margin: 1px;"
            f" padding: 5px 7px; background: {theme.SUNK}; }}"
            f"QLineEdit:focus {{ border-color: {theme.BRASS}; }}")

        self.slide = QtWidgets.QLineEdit()
        self.slide.setPlaceholderText(_("capture.slide.placeholder"))
        self.slide.setStyleSheet(self.edit.styleSheet())

        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(5)
        col.addWidget(self.edit)
        col.addWidget(self.slide)

    @property
    def subject(self) -> str:
        return self.edit.text().strip()

    @property
    def slide_note(self) -> str:
        return self.slide.text().strip()


class SettingsDialog(FramedDialog):
    """Where captures go and what they are called.

    Live preview of the resulting path, because a filename pattern you cannot
    see the result of is a filename pattern you will get wrong.
    """

    def __init__(self, settings: Settings, setup=None, parent=None) -> None:
        super().__init__(parent, width=560)
        self.setWindowTitle(_("capture.files.title"))
        self._settings = settings
        self._setup = setup

        self.root = QtWidgets.QLineEdit(settings.capture_root)
        browse = QtWidgets.QPushButton(_("capture.files.action.browse"))
        browse.clicked.connect(self._browse)
        root_row = QtWidgets.QHBoxLayout()
        root_row.addWidget(self.root, 1)
        root_row.addWidget(browse)

        self.folder = QtWidgets.QLineEdit(settings.folder_pattern)
        self.filename = QtWidgets.QLineEdit(settings.filename_pattern)
        self.keep_slices = QtWidgets.QCheckBox(
            _("capture.files.keep_slices.label"))
        self.keep_slices.setChecked(settings.keep_slices)
        self.keep_slices.setToolTip(_("capture.files.keep_slices.tooltip"))

        # No ordinary camera ships set to raw only, and until recently this
        # program could not write a photograph at all -- every output was a
        # negative you needed a raw developer to open.
        self.image_format = QtWidgets.QComboBox()
        for value, label in (("both", _("capture.files.format.both.label")),
                             ("raw", _("capture.files.format.raw.label")),
                             ("jpeg", _("capture.files.format.jpeg.label"))):
            self.image_format.addItem(label, value)
        at = self.image_format.findData(settings.image_format)
        self.image_format.setCurrentIndex(at if at >= 0 else 0)
        self.image_format.setToolTip(_("capture.files.format.tooltip"))

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow(_("capture.files.root.label"), root_row)
        form.addRow(_("capture.files.folder.label"), self.folder)
        form.addRow(_("capture.files.filename.label"), self.filename)
        form.addRow(_("capture.files.format.label"), self.image_format)
        form.addRow("", self.keep_slices)

        self.preview = QtWidgets.QLabel()
        self.preview.setProperty("role", "key")
        self.preview.setWordWrap(True)

        tokens = QtWidgets.QLabel("  ".join("{" + k + "}" for k in TOKENS))
        tokens.setProperty("role", "key")
        tokens.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self._save_button = buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Save)

        col = self.content
        col.setSpacing(14)
        col.addLayout(form)
        col.addWidget(_label(_("capture.files.tokens.heading")))
        col.addWidget(tokens)
        col.addWidget(_label(_("capture.files.example.heading")))
        col.addWidget(self.preview)
        col.addStretch(1)
        col.addWidget(buttons)

        for w in (self.root, self.folder, self.filename):
            w.textChanged.connect(self._update_preview)
        self._update_preview()
        self.finish()

    def _browse(self) -> None:
        start = self.root.text() or str(pictures_dir())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, _("capture.files.browse.title"), start)
        if chosen:
            self.root.setText(chosen)

    def _update_preview(self) -> None:
        probe = Settings(capture_root=self.root.text(),
                         folder_pattern=self.folder.text(),
                         filename_pattern=self.filename.text())
        problem = filename_problem(self.filename.text())
        try:
            path = probe.resolve(setup=self._setup, seq=7, subject="dopamine")
            self.preview.setText(_(problem) if problem else str(path))
        except Exception as exc:
            self.preview.setText(
                _("capture.files.preview.invalid", reason=exc))
        # Saying "invalid" and then saving it anyway is the worst of both.
        # A pattern with no {seq} resolves perfectly well and overwrites
        # every capture, so the preview is the only warning there is.
        self._save_button.setEnabled(problem is None)

    def _save(self) -> None:
        if filename_problem(self.filename.text()):
            return
        self._settings.capture_root = self.root.text()
        self._settings.folder_pattern = self.folder.text()
        self._settings.filename_pattern = self.filename.text()
        self._settings.keep_slices = self.keep_slices.isChecked()
        self._settings.image_format = str(self.image_format.currentData())
        self._settings.save()
        self.accept()


def _label(text: str) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setProperty("role", "label")
    return w


class ScaleBarDialog(QtWidgets.QDialog):
    """How the scale bar is dressed, with the picture in front of you.

    Its own window rather than a row in Files, because every choice here
    is a taste judgement and a dropdown of names makes you guess and then
    go and shoot something to find out. The preview is the feature.

    Its source is the live frame the window is already holding, which
    costs nothing and is what the operator is looking at. With no camera
    it falls back to the newest photograph under the capture root, and
    with neither it shows a plain grey card: the bar still has to be
    judged, and a missing preview would leave the dialog useless in
    exactly the situation somebody is most likely to be fiddling with
    settings.
    """

    def __init__(self, settings, parent=None, sample=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._sample = sample if sample is not None else _newest_capture(
            settings.capture_root)
        self.setWindowTitle(_("capture.bar.title"))

        self.shot = QtWidgets.QLabel()
        self.shot.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.shot.setMinimumHeight(210)

        self.style = QtWidgets.QComboBox()
        for value, key in SCALE_BAR_STYLES:
            self.style.addItem(_(key), value)
        at = self.style.findData(settings.scale_bar_style)
        self.style.setCurrentIndex(at if at >= 0 else 0)

        self.face = QtWidgets.QComboBox()
        for value, key in SCALE_BAR_FACES:
            self.face.addItem(_(key), value)
        at = self.face.findData(settings.scale_bar_face)
        self.face.setCurrentIndex(at if at >= 0 else 0)

        self.corner = QtWidgets.QComboBox()
        for value, key in SCALE_BAR_CORNERS:
            self.corner.addItem(_(key), value)
        at = self.corner.findData(settings.scale_bar_corner)
        self.corner.setCurrentIndex(at if at >= 0 else 0)

        self.size = QtWidgets.QComboBox()
        for value, key in SCALE_BAR_SIZES:
            self.size.addItem(_(key), value)
        at = self.size.findData(settings.scale_bar_size)
        self.size.setCurrentIndex(at if at >= 0 else 1)

        self.label_at = QtWidgets.QComboBox()
        for value, key in SCALE_BAR_LABELS:
            self.label_at.addItem(_(key), value)
        at = self.label_at.findData(settings.scale_bar_label)
        self.label_at.setCurrentIndex(at if at >= 0 else 0)

        self.plain = QtWidgets.QCheckBox(_("capture.bar.plain.label"))
        self.plain.setChecked(settings.scale_bar_plain_units)
        self.plain.setToolTip(_("capture.bar.plain.tooltip"))

        for w in (self.style, self.face, self.corner, self.size,
                  self.label_at):
            w.currentIndexChanged.connect(self._repaint)
        self.plain.toggled.connect(self._repaint)

        form = QtWidgets.QFormLayout()
        form.setSpacing(9)
        form.addRow(_("capture.bar.style.label"), self.style)
        form.addRow(_("capture.bar.face.label"), self.face)
        form.addRow(_("capture.bar.size.label"), self.size)
        form.addRow(_("capture.bar.corner.label"), self.corner)
        form.addRow(_("capture.bar.label.label"), self.label_at)
        form.addRow("", self.plain)

        note = QtWidgets.QLabel(_("capture.bar.note"))
        note.setProperty("role", "key")
        note.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(12)
        lay.addWidget(self.shot)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)
        self._repaint()

    def _repaint(self) -> None:
        from ..process import scalebar

        img = self._sample.copy()
        # The preview is a crop of a capture, so a bar drawn at the
        # capture's own scale would be the wrong size for it. Ask for the
        # scale that makes the bar look here as it will look there.
        scalebar.draw(img, self._sample_um(),
                      style=str(self.style.currentData()),
                      face=str(self.face.currentData()),
                      corner=str(self.corner.currentData()),
                      size=str(self.size.currentData()),
                      label_at=str(self.label_at.currentData()),
                      plain_units=self.plain.isChecked())
        h, w = img.shape[:2]
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).copy()
        qi = QtGui.QImage(rgb.data, w, h, 3 * w,
                          QtGui.QImage.Format.Format_RGB888).copy()
        self.shot.setPixmap(QtGui.QPixmap.fromImage(qi))

    def _sample_um(self) -> float:
        """Micrometres per pixel for the preview, not for the capture.

        Chosen so the bar lands at a believable fraction of the frame
        whatever the sample is: this window is about how the bar looks,
        and a real scale on an arbitrary crop would sometimes produce no
        bar at all and teach the operator nothing.
        """
        return 200.0 / max(1, self._sample.shape[1]) * 3.0

    def _save(self) -> None:
        self._settings.scale_bar_style = str(self.style.currentData())
        self._settings.scale_bar_face = str(self.face.currentData())
        self._settings.scale_bar_corner = str(self.corner.currentData())
        self._settings.scale_bar_size = str(self.size.currentData())
        self._settings.scale_bar_label = str(self.label_at.currentData())
        self._settings.scale_bar_plain_units = self.plain.isChecked()
        self._settings.save()
        self.accept()


def _newest_capture(root) -> "np.ndarray":
    """The most recent JPEG under `root`, or a grey card.

    Newest by modification time over the JPEGs only: the raws cannot be
    shown without developing them, which is a second of work for a
    thumbnail. Bounded so a capture root with ten thousand files in it
    does not stall the menu.
    """
    import numpy as np

    try:
        shots = sorted(Path(root).rglob("*.jpg"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        if shots:
            img = cv2.imread(str(shots[0]))
            if img is not None:
                h, w = img.shape[:2]
                side = min(h, w) // 2
                crop = img[h - side:h, w - side * 2:w] if w > side * 2 else img
                return cv2.resize(crop, (470, int(470 * crop.shape[0]
                                                  / crop.shape[1])),
                                  interpolation=cv2.INTER_AREA)
    except (OSError, ValueError):
        pass
    return np.full((210, 470, 3), 178, np.uint8)
