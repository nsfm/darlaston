"""Main window.

The only module that knows about both the session and Qt. Everything below it
emits plain dataclasses; a QObject signal marshals them to the main thread, and
nothing in camera/ or live/ has ever heard of Qt.

    python -m darlaston.ui.main          # real hardware, synthetic offered
    python -m darlaston.ui.main --mock   # synthetic only
"""
from __future__ import annotations

import argparse
import math
import signal
import sys
import threading

from PySide6 import QtCore, QtWidgets

from ..camera.base import CameraBackend, CameraState
from ..camera.session import CameraSession, SessionStatus
from ..calib import (CalibrationService, CalibrationStore, Opportunist,
                     Progress)
from ..calib.store import flat_key
from ..capture import CaptureResult, StillCapture
from ..capture.mosaic import MosaicSession
from ..capture.timelapse import Timelapse, TimelapseStatus
from ..live.focus import Illumination, Region
from ..live.pipeline import LivePipeline, LiveSignals
from ..session.model import (BUILTIN_ILLUMINATION, Library, Objective,
                             ScopeProfile, Setup, Turret)
from ..session.settings import Settings
from . import theme
from .about import AboutDialog
from .calib_ui import CalibrationButton, CalibrationPanel
from .capture_ui import SettingsDialog, ShutterBar, SubjectField
from .map_ui import SlideMapPanel
from .setup_ui import SetupDialog
from .shell import Chip, ObjectiveStepper, StatusBar, ToolBar, WaitingPage
from .stitch_ui import StitchDialog
from .photographer_ui import PhotographerDialog
from .proposal import ProposalBar
from .timelapse_ui import TimelapseDialog
from .floating import FloatingPanel
from .widgets import FocusGroup, Histogram, LiveView

def _provisional_scope() -> ScopeProfile:
    """A plausible stand, for a camera we have never seen on one.

    Built fresh each time rather than shared. As a module-level constant it
    was a mutable singleton that the setup editor wrote through, so the
    placeholder quietly became whatever was last configured -- and, worse,
    kept the id "unconfigured" while doing it.
    """
    return ScopeProfile(
        id="unconfigured", name="Microscope",
        turret=Turret([Objective(10, 0.30), Objective(20, 0.50),
                       Objective(40, 0.75),
                       Objective(100, 1.30, immersion="oil")], current=2),
        optovar=[1.0, 1.25, 1.6, 2.0], optovar_current=0)


class Bridge(QtCore.QObject):
    """Thread hop. Qt's queued connection is the marshalling."""

    signals = QtCore.Signal(object)
    status = QtCore.Signal(object)
    capture_state = QtCore.Signal(str)
    capture_result = QtCore.Signal(object)
    timelapse = QtCore.Signal(object)
    stitch = QtCore.Signal(object)
    calib_progress = QtCore.Signal(object)
    banked = QtCore.Signal(int, int)
    banking = QtCore.Signal(bool)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, make_backend, allow_synthetic: bool = True,
                 presence=None) -> None:
        super().__init__()
        self.setWindowTitle("darlaston")
        self.resize(1340, 860)

        self.library = Library()
        self.settings = Settings.load()
        self.store = CalibrationStore()
        self.setup: Setup | None = None
        # Illumination is selectable before a camera exists, so it is held here
        # and applied when the setup is built rather than being lost.
        self._illumination = BUILTIN_ILLUMINATION[0]
        self._synced = False
        self._make_backend = make_backend
        self._allow_synthetic = allow_synthetic

        self.bridge = Bridge()
        self.bridge.signals.connect(self._on_signals)
        self.bridge.status.connect(self._on_status)
        self.bridge.capture_state.connect(self._on_capture_state)
        self.bridge.capture_result.connect(self._on_capture_result)
        self.bridge.calib_progress.connect(self._on_calib_progress)
        self.bridge.banked.connect(self._on_banked)
        self.bridge.banking.connect(self._on_banking)

        self.pipeline = LivePipeline(self.bridge.signals.emit,
                                     illumination=Illumination.BRIGHTFIELD)
        self.session = CameraSession(make_backend,
                                     self.bridge.status.emit,
                                     self.pipeline.submit,
                                     is_present=presence)

        self.capture = StillCapture(self.session, self.settings,
                                    self.bridge.capture_state.emit,
                                    self.bridge.capture_result.emit,
                                    store=self.store, pipeline=self.pipeline)
        self.timelapse = Timelapse(self.capture, self.bridge.timelapse.emit)
        self.bridge.timelapse.connect(self._on_timelapse)
        self.bridge.stitch.connect(self._on_stitch)
        self.mosaic: MosaicSession | None = None
        self._last_preview = None
        self.calibration = CalibrationService(self.session, self.store,
                                              self.bridge.calib_progress.emit)
        self.opportunist = Opportunist(self.session,
                                       self.bridge.banked.emit,
                                       self.bridge.banking.emit)
        self._build()
        self.pipeline.start()
        self.session.start()

    # ---- layout ----------------------------------------------------------

    def _build(self) -> None:
        self.toolbar = ToolBar()
        self.toolbar.about.connect(lambda: AboutDialog(self).exec())
        # Menus rather than buttons, so these nest as more arrives instead of
        # every name having to change when the third tool appears.
        instrument = self.toolbar.add_menu("Instrument")
        instrument.addAction("Microscope setup…", self._open_setup)
        # Its own menu rather than a corner of Capture: attribution is the
        # one part of a file's provenance the instrument cannot supply, and
        # burying it is how photographs get published uncredited.
        session = self.toolbar.add_menu("Session")
        session.addAction("Photographer…", self._open_photographer)
        capture_menu = self.toolbar.add_menu("Capture")
        capture_menu.addAction("Location and naming…", self._open_settings)
        capture_menu.addAction("Timelapse…", self._open_timelapse)
        capture_menu.addAction("Stitch mosaic…", self._stitch_mosaic)
        capture_menu.addSeparator()
        # In the menu rather than the rail: it is set once per illumination
        # style and then left alone, and the rail is already too full to
        # spend space on a control nobody touches twice in a session.
        self.wb_action = capture_menu.addAction("Write white balance")
        self.wb_action.setCheckable(True)
        self.wb_action.setChecked(True)
        self.wb_action.setToolTip(
            "Off writes a neutral file: the sensor's own channels, untouched.\n"
            "Use for polarised interference colour, fluorescence or stained\n"
            "sections, where no part of the field is neutral by nature.")
        self.wb_action.toggled.connect(
            lambda on: setattr(self.capture, "white_balance", on))

        self.waiting = WaitingPage()
        self.waiting.use_synthetic.connect(self._switch_to_synthetic)
        self.waiting.synthetic.setVisible(self._allow_synthetic)

        self.view = LiveView()
        self.view.region_drawn.connect(self._on_custom_region)
        self.histogram = Histogram()
        self.focus = FocusGroup()
        self.focus.peaking_toggled.connect(self._on_peaking)
        self.focus.sweep_toggled.connect(self._on_sweep)

        # Floating panels over the live view. A map is only meaningful in
        # reference to the image it maps, and calibration is a start-of-
        # session ritual that does not deserve permanent rail residency --
        # both belong here, and both can be dragged out of the way.
        self.slidemap = SlideMapPanel()
        self.slidemap.reset_requested.connect(self.pipeline.reset_tracking)
        self.slidemap.mosaic_requested.connect(self._on_mosaic_requested)
        self.slidemap.undo_tile.connect(self._on_undo_tile)
        self.map_window = FloatingPanel("slide map", self.view)
        self.map_window.set_relative(0.02, 0.58)
        _fill(self.map_window, self.slidemap)

        self.calib_panel = CalibrationPanel()
        self.calib_panel.capture_dark.connect(self._do_dark)
        self.calib_panel.build_flat.connect(self._do_flat)
        self.calib_panel.build_lut.connect(self._do_lut)
        self.calib_window = FloatingPanel("calibration", self.view)
        self.calib_window.set_relative(0.04, 0.06)
        _fill(self.calib_window, self.calib_panel)
        self.calib_window.hide()

        # Turret proposals appear over the image, where the operator's eyes
        # already are when they have just turned the turret.
        self.proposal = ProposalBar(self.view)
        self.proposal.accepted.connect(self._accept_turret)

        self.view.installEventFilter(self)
        self.slidemap.show()
        self.map_window.show()

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.waiting)
        self.stack.addWidget(self.view)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.stack, 1)
        body.addWidget(self._rail())

        self.strip = StatusBar()
        self.strip.preview.currentIndexChanged.connect(self._on_preview_res)
        self.strip.rate.currentIndexChanged.connect(self._on_rate)

        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.toolbar)
        col.addLayout(body, 1)
        col.addWidget(self.strip)

        central = QtWidgets.QWidget()
        central.setLayout(col)
        self.setCentralWidget(central)
        self.setStyleSheet(theme.stylesheet())

    def _rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QFrame()
        rail.setFixedWidth(286)
        rail.setStyleSheet(
            f"background:{theme.PANEL}; border-left:1px solid {theme.LINE};")

        col = QtWidgets.QVBoxLayout(rail)
        col.setContentsMargins(14, 14, 14, 14)
        col.setSpacing(18)

        # One row that is both status and doorway; the panel itself floats.
        self.calib_button = CalibrationButton()
        self.calib_button.clicked.connect(self._toggle_calibration)
        col.addWidget(self.calib_button)

        col.addLayout(_group("exposure", self.histogram))
        col.addWidget(self.focus)

        # Sensor
        self.exposure = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.exposure.setRange(1, 1000)
        self.exposure.setValue(120)
        self.exposure.valueChanged.connect(self._on_exposure)
        self.exposure_value = QtWidgets.QLabel("8.3 ms")
        self.exposure_value.setProperty("role", "value")

        self.gain = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.gain.setRange(100, 2000)
        self.gain.setValue(100)
        self.gain.valueChanged.connect(self._on_gain)
        self.gain_value = QtWidgets.QLabel("1.0×")
        self.gain_value.setProperty("role", "value")

        sensor = _group("sensor", _readout("exposure", self.exposure_value))
        sensor.addWidget(self.exposure)
        sensor.addLayout(_readout("gain", self.gain_value))
        sensor.addWidget(self.gain)
        col.addLayout(sensor)

        # Where the metric looks. Field curvature puts the frame edges on a
        # different focal plane, so a full-field score averages together things
        # that cannot be sharp together -- a tight box gives a decisive curve.
        self.regions = QtWidgets.QButtonGroup(self)
        # Not exclusive: a dragged box is a fourth state in which none of the
        # presets is active, and an exclusive group cannot express that.
        self.regions.setExclusive(False)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        for region, label in ((Region.SPOT, "spot"), (Region.CENTRE, "centre"),
                              (Region.FULL, "full")):
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.setProperty("role", "seg")
            b.setChecked(region is Region.CENTRE)
            b.clicked.connect(lambda _=False, r=region: self._on_region(r))
            self.regions.addButton(b)
            row.addWidget(b)
        self._region_buttons = {r: b for r, b in
                                zip((Region.SPOT, Region.CENTRE, Region.FULL),
                                    self.regions.buttons())}
        measure = _group("measure from", row)
        hint = QtWidgets.QLabel("drag on the image for a custom box")
        hint.setProperty("role", "key")
        measure.addWidget(hint)
        col.addLayout(measure)

        col.addStretch(1)

        # Subject and optics sit closest to the shutter: they are what this
        # particular shot is *of*, and they change more often than anything
        # above them. Everything higher up is instrument state.
        self.subject = SubjectField()
        col.addLayout(_group("subject", self.subject))

        self.objective = ObjectiveStepper()
        self.objective.changed.connect(self._on_objective_stepped)
        self.illumination = QtWidgets.QComboBox()
        for mode in BUILTIN_ILLUMINATION:
            self.illumination.addItem(mode.display, mode)
        self.illumination.currentIndexChanged.connect(self._on_illumination)
        optics = _group("optics", self.objective)
        optics.addWidget(self.illumination)
        col.addLayout(optics)

        # Shutter, averaging and last result as one control. Averaging is a
        # split-button menu because it is chosen rarely and read never; the
        # result appears under the button inside the same frame, so a capture
        # reports where the eye already is.
        self.shutter = ShutterBar()
        self.shutter.triggered.connect(self._on_capture)
        self.shutter.set_available(False)
        col.addWidget(self.shutter)

        return rail

    def eventFilter(self, obj, event) -> bool:
        if obj is self.view and event.type() in (
                QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show):
            self.map_window.place(self.slidemap.preferred_size(self.view))
            self.calib_window.place((330, 190))
            if self.proposal.isVisible():
                self.proposal.place()
        return super().eventFilter(obj, event)

    # ---- session ---------------------------------------------------------

    @QtCore.Slot(object)
    def _on_status(self, status: SessionStatus) -> None:
        info = status.info
        if info is not None and self.setup is None:
            profile = self.library.remember_camera(info.serial, info.model)
            # The stand this camera was last on, if we know it. Without this
            # lookup the setup editor wrote to disk correctly and was then
            # ignored on every subsequent launch -- a configured Zeiss
            # reverting to a placeholder turret every time the app started.
            scope = (self.library.scope_or_default(profile.last_scope)
                     or _provisional_scope())
            self.setup = Setup(camera=profile, scope=scope,
                               illumination=self._illumination)
            self.objective.set_turret(self.setup.scope.turret)
            self._push_turret()

        self.strip.update_status(status, self.setup)
        self.strip.select_resolution(self.session.preview_resolution)
        self.strip.select_rate(self.session.framerate_cap)

        if status.is_live and not self._synced:
            self._adopt_camera_settings()

        self.shutter.set_available(status.is_live and not self.capture.busy)
        if self.setup is not None:
            self.opportunist.set_key(flat_key(self.setup,
                                              self.subject.slide_note))
            self._refresh_calibration()

        if status.is_live:
            self.stack.setCurrentWidget(self.view)
        else:
            self.waiting.update_status(status)
            self.stack.setCurrentWidget(self.waiting)
            self.view.clear_peaking()

    def _adopt_camera_settings(self) -> None:
        """Show what the camera actually has, not what we guessed.

        The sliders were initialised to fixed positions that did not correspond
        to the values the session was sending, so the first nudge of a control
        jumped the exposure by an order of magnitude. The camera is the source
        of truth; the controls follow it.
        """
        actual = self.session.actual_settings()
        if actual is None:
            return
        us, gain = actual
        for widget, value in ((self.exposure, self._us_to_slider(us)),
                              (self.gain, gain)):
            widget.blockSignals(True)
            widget.setValue(max(widget.minimum(), min(widget.maximum(), value)))
            widget.blockSignals(False)
        self.exposure_value.setText(
            f"{us / 1000:.1f} ms" if us < 1_000_000 else f"{us / 1e6:.2f} s")
        self.gain_value.setText(f"{gain / 100:.1f}×")
        self._synced = True

    def _switch_to_synthetic(self) -> None:
        from ..camera.mock import MockCamera
        self.session.stop()
        self._synced = False
        self.session = CameraSession(lambda: MockCamera(fps=30.0),
                                     self.bridge.status.emit,
                                     self.pipeline.submit)  # always present
        self.session.start()

    # ---- controls --------------------------------------------------------

    @staticmethod
    def _slider_to_us(value: int) -> int:
        """Logarithmic: the useful range spans a third of a millisecond to
        several seconds, and a linear slider spends its travel nowhere useful."""
        return int(300 * (10 ** (value / 250.0)))

    @staticmethod
    def _us_to_slider(us: int) -> int:
        return int(round(250.0 * math.log10(max(us, 300) / 300.0)))

    def _on_exposure(self, value: int) -> None:
        us = self._slider_to_us(value)
        self.session.set_exposure(us)
        # Metrics are normalised against intensity, but clipping breaks that,
        # so a peak recorded before a big brightness change is not comparable.
        self.pipeline.reset_focus_peak()
        self.exposure_value.setText(
            f"{us / 1000:.1f} ms" if us < 1_000_000 else f"{us / 1e6:.2f} s")

    def _on_gain(self, value: int) -> None:
        self.session.set_gain(value)
        self.pipeline.reset_focus_peak()
        self.gain_value.setText(f"{value / 100:.1f}×")

    def _on_preview_res(self, at: int) -> None:
        index = self.strip.preview.itemData(at)
        if index is None or not self.session.status.is_live:
            return
        # The scene scale changes with the mode, so tracked positions in the
        # old mode's pixels no longer measure anything.
        self.session.set_preview_resolution(int(index))
        self.pipeline.reset_tracking()
        self.slidemap.clear()
        self.pipeline.reset_focus_peak()

    def _push_turret(self) -> None:
        """Hand the detector the turret, the stand's sign and its signatures."""
        if self.setup is None:
            return
        from ..live.turret import model_signatures
        scope = self.setup.scope
        signatures, learned = scope.signatures(
            self.setup.illumination.kind,
            model_signatures(scope.turret, scope.condenser_na))
        self.pipeline.set_turret(scope.turret, scope.rotation_sign,
                                 signatures, learned)

    def _on_turret_event(self, event) -> None:
        """A rotation was detected. Say what we think and wait to be told."""
        if self.setup is None:
            return
        turret = self.setup.scope.turret
        index = event.suggested_index
        if index is None or not (0 <= index < len(turret.positions)):
            self.strip.set_note("objective changed — position unclear")
            return
        objective = turret.positions[index]
        if objective is None:
            return
        # Even a corroborated reading is offered rather than applied. The
        # objective keys every calibration lookup, so being quietly wrong
        # here would attach the wrong flat to everything that followed.
        # Kept so that correcting the proposal teaches the optical path's
        # sign instead of asking the operator to reason about it.
        self._last_proposal = event
        scope = self.setup.scope

        # Until the handedness is known, the detector is genuinely uncertain
        # between two positions -- the same reading with either sign. Naming
        # both is the honest offer, and it keeps the correction where the
        # eyes already are instead of sending them to the rail.
        choices = [(objective.label, (index, event.level))]
        if not scope.rotation_sign_known and event.raw_direction is not None:
            probe = type(turret)(list(turret.positions), turret.current)
            other = probe.step(event.raw_direction * -scope.rotation_sign)
            alt = turret.positions[other] if other != index else None
            if alt is not None:
                choices.append((alt.label, (other, event.level)))

        if len(choices) > 1:
            self.proposal.propose(
                "Objective changed — which?",
                "the turret moved; naming it once teaches which way it counts",
                choices)
        else:
            self.proposal.propose(
                f"Objective now {objective.label}?",
                f"{event.reason} · {event.confidence * 100:.0f}% sure",
                choices)

    def _accept_turret(self, payload) -> None:
        if self.setup is None:
            return
        index, level = payload
        self._learn_rotation_sign(int(index))
        # A confirmation is the only trustworthy label we ever get, so it is
        # also the moment to learn what that objective looks like.
        if level:
            self.setup.scope.learn_brightness(
                self.setup.illumination.kind, int(index), float(level))
            self.library.scopes[self.setup.scope.id] = self.setup.scope
            self.library.save()
        self.setup.scope.turret.current = int(index)
        self.objective.set_turret(self.setup.scope.turret)
        self._on_objective_changed()

    def _on_objective_stepped(self, index: int) -> None:
        """The operator moved the objective by hand.

        If a proposal was on screen, this is a correction and teaches the
        handedness exactly as pressing a button on the bar would. Nate asked
        for this path specifically: reaching for the stepper is the natural
        motion when the suggestion is wrong.
        """
        if self.setup is None:
            return
        if self.proposal.isVisible():
            self.proposal.hide()
        self._learn_rotation_sign(int(index))
        self.setup.scope.turret.current = int(index)
        self._on_objective_changed()

    def _learn_rotation_sign(self, actual: int) -> None:
        """Work the sign out from a correction instead of asking.

        Every confirmation is a labelled example: we know which side of the
        frame darkened, and now we know which position the turret actually
        reached. If the opposite sign would have predicted it and the current
        one would not, the optical path is the other way round -- which is a
        fact about prisms and how the camera is screwed on, not something the
        operator should have to work out.
        """
        event = getattr(self, "_last_proposal", None)
        self._last_proposal = None
        if event is None or event.raw_direction is None or self.setup is None:
            return
        scope = self.setup.scope
        turret = scope.turret
        here = turret.current

        def step(sign):
            probe = type(turret)(list(turret.positions), here)
            return probe.step(event.raw_direction * sign)

        current_sign = 1 if scope.rotation_sign >= 0 else -1
        if step(current_sign) == actual:
            # Right already, and now confirmed rather than assumed.
            if not scope.rotation_sign_known:
                scope.rotation_sign_known = True
                self.library.scopes[scope.id] = scope
                self.library.save()
            return
        if step(-current_sign) != actual:
            return                                   # neither; learn nothing
        scope.rotation_sign = -current_sign
        scope.rotation_sign_known = True
        self.library.scopes[scope.id] = scope
        self.library.save()
        self.strip.set_note(
            "turret direction learned — proposals should be right from now on")

    def _on_objective_changed(self) -> None:
        """Everything keyed on magnification is now stale."""
        self.strip.update_status(self.session.status, self.setup)
        self.opportunist.set_key(flat_key(self.setup, self.subject.slide_note))
        self._refresh_calibration()
        # Scale changed, so tracked positions no longer measure anything.
        self.pipeline.reset_tracking()
        self.slidemap.clear()
        self.pipeline.reset_focus_peak()
        self._push_turret()

    def _on_rate(self, at: int) -> None:
        fps = self.strip.rate.itemData(at)
        if fps is None:
            return
        self.session.set_framerate_cap(int(fps))

    def _on_region(self, region: Region) -> None:
        self.pipeline.set_focus_region(region)
        for r, b in self._region_buttons.items():
            b.setChecked(r is region)

    def _on_custom_region(self, rect: tuple) -> None:
        """A box dragged on the image wins over any preset."""
        self.pipeline.set_focus_region(Region.CUSTOM, rect)
        for b in self._region_buttons.values():
            b.setChecked(False)

    # ---- calibration -----------------------------------------------------

    def _do_dark(self) -> None:
        self.calibration.capture_dark()

    def _do_flat(self) -> None:
        self.calibration.build_flat(self.setup, self.opportunist.frames,
                                    self.subject.slide_note)

    def _do_lut(self) -> None:
        self.calibration.build_preview_lut(self.setup)

    @QtCore.Slot(object)
    def _on_calib_progress(self, progress: Progress) -> None:
        self.calib_panel.set_progress(progress)
        if progress.finished:
            if progress.stage == "flat":
                self.opportunist.bank.reset()
            self._refresh_calibration()

    @QtCore.Slot(int, int)
    def _on_banked(self, count: int, wanted: int) -> None:
        self._refresh_calibration()

    @QtCore.Slot(bool)
    def _on_banking(self, busy: bool) -> None:
        """The preview freezes for over a second during an opportunistic grab,
        so say so. A tool that stalls unexplained is worse than one that asks."""
        self.strip.set_note("banking a blank field…" if busy else "")

    def _refresh_calibration(self) -> None:
        if self.setup is None:
            return
        actual = self.session.actual_settings() or (8330, 100)
        status = self.store.status(self.setup, actual[0], actual[1],
                                   self.subject.slide_note)
        live = self.session.status.is_live and not self.calibration.busy
        self.calib_panel.set_status(status, self.opportunist.count,
                                    self.opportunist.bank.wanted, live=live)
        self.calib_button.set_status(status, busy=self.calibration.busy)

    def _on_capture(self) -> None:
        if not self.capture.trigger(self.setup, subject=self.subject.subject,
                                    slide=self.subject.slide_note,
                                    frames=self.shutter.frames):
            return
        self.shutter.set_state("exposing")

    @QtCore.Slot(str)
    def _on_capture_state(self, state: str) -> None:
        self.shutter.set_state(state)
        # The notice spans exactly the window where motion does damage. Once
        # the frame is pulled, cranking is harmless and the message would be
        # a lie that teaches people to ignore it.
        self.view.set_notice(f"hold still — {state}"
                             if state.startswith("exposing") else None)
        if state == "idle":
            self.shutter.set_available(self.session.status.is_live)

    @QtCore.Slot(object)
    def _on_capture_result(self, result: CaptureResult) -> None:
        self.shutter.set_result(result.summary,
                                ok=result.ok and not result.moved)
        if result.ok and self.timelapse.running:
            try:
                self.timelapse.note_written(result.path.stat().st_size)
            except OSError:
                pass
        # Not during a timelapse: an unattended run must never park itself
        # behind a modal question. The summary already says it moved.
        discarded = False
        if result.ok and result.moved and not self.timelapse.running:
            discarded = self._offer_retry(result)
        # The moved question comes first: a discarded shot must never
        # become a tile.
        if result.ok and not discarded and self.mosaic is not None:
            self._adopt_tile(result)

    def _adopt_tile(self, result: CaptureResult) -> None:
        h, w = ((self._last_preview.shape[:2])
                if self._last_preview is not None else (0, 0))
        tile = self.mosaic.adopt(result.path, result.position, (w, h))
        self.slidemap.tile_added(result.position, self._last_preview)
        note = "" if result.position is not None else " (no position!)"
        self.shutter.set_result(f"tile {tile.index} · {tile.filename}{note}",
                                ok=result.position is not None)

    def _on_mosaic_requested(self, on: bool) -> None:
        if on:
            self.mosaic = MosaicSession(self.settings.capture_root,
                                        self.subject.subject)
            if self.setup is not None:
                obj = self.setup.scope.turret.objective
                self.mosaic.set_meta(
                    objective=obj.label if obj else None,
                    illumination=self.setup.illumination.display)
        else:
            # The manifest is already current, so closing is just letting go
            # -- but a finished mosaic almost always wants stitching, and
            # making the operator go hunting through a menu for the obvious
            # next step is how a tool feels unfinished.
            done, self.mosaic = self.mosaic, None
            self.slidemap.set_mosaic(False)
            if done is not None and len(done.tiles) >= 2:
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle("Mosaic finished")
                box.setText(f"{len(done.tiles)} tiles in {done.dir.name}.")
                box.setInformativeText("Stitch them now?")
                later = box.addButton("Later",
                                      QtWidgets.QMessageBox.ButtonRole.RejectRole)
                now = box.addButton("Stitch…",
                                    QtWidgets.QMessageBox.ButtonRole.AcceptRole)
                box.setDefaultButton(now)
                box.setStyleSheet(theme.stylesheet())
                box.exec()
                if box.clickedButton() is now:
                    self._stitch_mosaic(done.dir)
            return
        self.slidemap.set_mosaic(on)

    def _on_undo_tile(self) -> None:
        if self.mosaic is None:
            return
        tile = self.mosaic.undo()
        if tile is not None:
            self.slidemap.tile_removed()
            self.shutter.set_result(f"tile {tile.index} deleted")

    def _offer_retry(self, result: CaptureResult) -> bool:
        """The shot is on disk either way; the question is whether to keep it.

        Keep is the default: a dialog that deletes data on its default button
        will eventually delete something irreplaceable. Returns True when the
        operator chose to discard.
        """
        px = result.moved_px or 0.0
        amount = ("more than could be measured" if math.isinf(px)
                  else f"about {px:.0f} pixels")
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Moved during exposure")
        box.setText(f"The view moved {amount} while the frame was being "
                    f"exposed, so it is probably smeared.")
        box.setInformativeText(result.path.name)
        keep = box.addButton("Keep it",
                             QtWidgets.QMessageBox.ButtonRole.RejectRole)
        retry = box.addButton("Discard and reshoot",
                              QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        box.setStyleSheet(theme.stylesheet())
        box.exec()
        if box.clickedButton() is retry:
            result.path.unlink(missing_ok=True)
            self.shutter.set_result("discarded — hold still this time")
            self._on_capture()
            return True
        return False

    def _open_setup(self) -> None:
        if self.setup is None:
            return
        dialog = SetupDialog(self.setup, self.library, self)
        if dialog.exec():
            self.setup = dialog.result_setup
            self.setup.illumination = self._illumination
            self.objective.set_turret(self.setup.scope.turret)
            self._push_turret()
            self.strip.update_status(self.session.status, self.setup)
            # The optical stack changed, so anything banked for the old one is
            # no longer a flat for this one.
            self.opportunist.set_key(flat_key(self.setup,
                                              self.subject.slide_note))
            self._refresh_calibration()
            # And the map's scale changed with it: positions in old-objective
            # pixels no longer measure anything on the new one.
            self.slidemap.clear()

    def _toggle_calibration(self) -> None:
        if self.calib_window.isVisible():
            self.calib_window.hide()
            return
        self.calib_window.place((330, 190))
        self.calib_window.show()
        self.calib_window.raise_()

    def _open_photographer(self) -> None:
        PhotographerDialog(self.settings, self).exec()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.setup, self)
        dialog.exec()

    def _open_timelapse(self) -> None:
        def start(interval: int, count: int) -> bool:
            return self.timelapse.start(
                interval, count, setup=self.setup,
                subject=self.subject.subject, slide=self.subject.slide_note,
                frames=self.shutter.frames)
        TimelapseDialog(self.timelapse, start, self).exec()

    @QtCore.Slot(object)
    def _on_timelapse(self, status: TimelapseStatus) -> None:
        self.strip.set_note(status.summary or status.message)

    def _stitch_mosaic(self, directory=None) -> None:
        """Pick a tile session and stitch it, off the UI thread.

        Defaults to the live session's folder when a mosaic is running --
        stitching mid-lay is a legitimate sanity check.
        """
        if directory is None:
            start = str(self.mosaic.dir if self.mosaic else
                        self.settings.capture_root)
            directory = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Mosaic tile folder", start)
            if not directory:
                return
        StitchDialog(directory, self._run_stitch, self).exec()

    def _run_stitch(self, directory, scale, dialog) -> None:
        def work():
            from ..process.stitch import stitch
            try:
                path, report = stitch(
                    directory, scale=scale,
                    progress=lambda d, t: self.bridge.stitch.emit(
                        ("progress", d, t)))
                self.bridge.stitch.emit(("done", (
                    f"{report['megapixels']:.0f} MP · "
                    f"{report['refined']}/{report['edges']} seams refined"
                    + ("  · levelled" if report["flattened"] else "")
                    + f" → {path.name}"), True))
            except Exception as exc:
                self.bridge.stitch.emit(("done", f"failed — {exc}", False))

        self._stitch_dialog = dialog
        threading.Thread(target=work, daemon=True, name="stitch").start()

    @QtCore.Slot(object)
    def _on_stitch(self, message) -> None:
        dialog = getattr(self, "_stitch_dialog", None)
        if dialog is None:
            return
        if message[0] == "progress":
            dialog.set_progress(message[1], message[2])
        else:
            dialog.finished_with(message[1], message[2])
            self.strip.set_note(message[1])

    def _on_sweep(self, on: bool) -> None:
        if on:
            self.pipeline.start_sweep()
        else:
            self.pipeline.stop_sweep()
            self.focus.set_coverage(None)
            self.view.set_remaining(None, None)

    def _on_peaking(self, on: bool) -> None:
        self.pipeline.set_peaking(on)
        if not on:
            self.view.clear_peaking()

    def _on_illumination(self, index: int) -> None:
        mode = self.illumination.itemData(index)
        if mode is None:
            return
        self._illumination = mode
        if self.setup is not None:
            self.setup.illumination = mode
        # Focus metric and prefilter follow the illumination, per DISCOVERY.md 9.
        self.pipeline.set_illumination(Illumination(mode.kind))
        self.strip.update_status(self.session.status, self.setup)

    # ---- live ------------------------------------------------------------

    @QtCore.Slot(object)
    def _on_signals(self, s: LiveSignals) -> None:
        # Not while a guided routine is running: banking a field mid-routine
        # is startling, and during a dark capture the lamp is off, so anything
        # taken then would be a black frame masquerading as a flat.
        if not self.calibration.busy and not self.capture.busy:
            self.opportunist.observe(s)
        self.view.set_focus_rect(s.focus_rect)
        self.view.set_remaining(s.coverage_remaining, s.focus_rect)
        self.focus.set_coverage(s.coverage, s.coverage_complete)
        self.view.set_frame(s.preview, s.peaking)
        self.slidemap.update_live(s)
        self._last_preview = s.preview
        if s.turret_event is not None:
            self._on_turret_event(s.turret_event)

        # The instruments repaint at a third of the frame rate. A histogram
        # and a focus trace are read as trends, not as individual frames, and
        # at 30 Hz their repaints cost more of the UI thread than the live
        # image itself. The *data* is still folded in every frame -- only the
        # drawing is thinned.
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 3 == 0:
            self.histogram.set_data(s.histogram, s.clipped_fraction,
                                    s.black_fraction, s.channel_clipped)
            self.focus.set_data(s.focus_trace, s.focus_fraction_of_peak)
            self.strip.set_live(s)

    def shutdown(self) -> None:
        """Release the camera. Idempotent, and reached from both the window
        closing and a Ctrl-C, so the device is never left held."""
        if getattr(self, "_shut", False):
            return
        self._shut = True
        self.timelapse.stop()
        self.session.stop()
        self.pipeline.stop()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)


# --------------------------------------------------------------------------

def _fill(panel, widget) -> None:
    """Put a panel's content inside its floating frame."""
    lay = QtWidgets.QVBoxLayout(panel.body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)


def _group(title: str, first: QtWidgets.QWidget | QtWidgets.QLayout):
    col = QtWidgets.QVBoxLayout()
    col.setSpacing(7)
    label = QtWidgets.QLabel(title.upper())
    label.setProperty("role", "label")
    col.addWidget(label)
    col.addLayout(first) if isinstance(first, QtWidgets.QLayout) else col.addWidget(first)
    return col


def _readout(key: str, value_widget: QtWidgets.QLabel) -> QtWidgets.QHBoxLayout:
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    k = QtWidgets.QLabel(key)
    k.setProperty("role", "key")
    row.addWidget(k)
    row.addStretch(1)
    row.addWidget(value_widget)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true",
                    help="use the synthetic camera and do not look for hardware")
    args = ap.parse_args()

    if args.mock:
        from ..camera.mock import MockCamera
        make: callable = lambda: MockCamera(fps=30.0)
        allow_synthetic = False
        presence = None                      # synthetic is always there
    else:
        def make() -> CameraBackend:
            from ..camera.toupcam import ToupcamBackend
            return ToupcamBackend()
        allow_synthetic = True
        from ..camera import usb
        presence = usb.present               # real hardware lives on the bus

    app = QtWidgets.QApplication(sys.argv)
    theme.load_fonts()
    win = MainWindow(make, allow_synthetic=allow_synthetic, presence=presence)
    win.show()

    # Qt blocks in C++, so Python never gets to run its SIGINT handler and
    # Ctrl-C does nothing. A timer that does nothing at all gives the
    # interpreter a slot to run in; the handler then quits the loop cleanly.
    app.aboutToQuit.connect(win.shutdown)
    signal.signal(signal.SIGINT, lambda *_: (
        print("\ninterrupted, releasing the camera", file=sys.stderr),
        app.quit()))
    heartbeat = QtCore.QTimer()
    heartbeat.start(150)
    heartbeat.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
