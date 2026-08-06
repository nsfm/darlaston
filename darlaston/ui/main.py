"""Main window.

The only module that knows about both the session and Qt. Everything below it
emits plain dataclasses; a QObject signal marshals them to the main thread, and
nothing in camera/ or live/ has ever heard of Qt.

    python -m darlaston.ui.main          # real hardware
    python -m darlaston.ui.main --mock   # synthetic, for development
"""
from __future__ import annotations

import argparse
import math
import shutil
import signal
import sys
import threading
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..camera.base import CameraBackend, CameraState
from ..camera.session import CameraSession, SessionStatus
from ..calib import (CalibrationService, CalibrationStore, Opportunist,
                     Progress)
from ..calib.store import flat_key
from ..capture import CaptureResult, StillCapture
from ..capture.mosaic import MosaicSession
from ..capture.stack import StackSession, StackTrigger
from ..capture.timelapse import Timelapse, TimelapseStatus
from ..cpu import apply_thread_budget
from ..live.focus import Illumination, Region
from ..live.pipeline import (INSTRUMENT_DIVISOR, LivePipeline,
                             LiveSignals)
from ..session.model import (BUILTIN_ILLUMINATION, CameraProfile, Library,
                             ScopeProfile, Setup, Turret)
from ..i18n import _, n_
from ..session.settings import Settings
from . import theme
from .about import AboutDialog
from .calib_ui import CalibrationButton, CalibrationPanel
from .capture_ui import SettingsDialog, ShutterBar, SubjectField
from .map_ui import SlideMapPanel
from .perf_ui import PerfPanel, PerformanceDialog
from .setup_ui import CameraDialog, MicroscopeDialog
from .frame import SystemFrame, TitleDrag, WindowsFrame
from .update_ui import UpdateDialog, UpdateWatch, mark_as_update
from .frame import wanted as frame_wanted
from .sdk_ui import SdkDialog
from .shell import (Chip, ObjectiveStepper, StatusBar, ToolBar,
                    WaitingPage)
from .darkroom_ui import (RENDERS, ArrangeDialog, PlateDialog,
                          RenderDialog)
from .stack_ui import StackAssembly
from .stitch_ui import StitchDialog
from .photographer_ui import PhotographerDialog
from .proposal import ProposalBar
from .timelapse_ui import TimelapseDialog
from .floating import FloatingPanel
from .widgets import FocusGroup, Histogram, LiveView, ValueBar


class Bridge(QtCore.QObject):
    """Thread hop. Qt's queued connection is the marshalling."""

    signals = QtCore.Signal(object)
    status = QtCore.Signal(object)
    capture_state = QtCore.Signal(str)
    capture_result = QtCore.Signal(object)
    timelapse = QtCore.Signal(object)
    stitch = QtCore.Signal(object)
    stack_merge = QtCore.Signal(object)
    tile_merge = QtCore.Signal(object)
    wiggle = QtCore.Signal(object)
    calib_progress = QtCore.Signal(object)
    banked = QtCore.Signal(int, int)
    banking = QtCore.Signal(bool)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, make_backend, allow_synthetic: bool = False,
                 presence=None) -> None:
        super().__init__()
        self.setWindowTitle(_("app.title"))
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
        self.bridge.stack_merge.connect(self._on_stack_merge)
        self.bridge.tile_merge.connect(self._on_tile_merge)
        self.bridge.wiggle.connect(self._on_wiggle_done)
        self.mosaic: MosaicSession | None = None
        self.stack_session: StackSession | None = None
        self.stack_trigger = StackTrigger(self._fire_stack_slice)
        # Stacked-mosaic state: the current tile's anchor position, frame
        # dims, a preview for the minimap, and the background merge queue.
        self._tile_anchor: tuple[float, float] | None = None
        self._tile_frame: tuple[int, int] = (0, 0)
        self._tile_preview = None
        self._tile_merges: list[tuple[int, Path]] = []
        self._tile_merging: int | None = None
        self._last_preview = None
        self.calibration = CalibrationService(self.session, self.store,
                                              self.bridge.calib_progress.emit)
        self.opportunist = Opportunist(self.session,
                                       self.bridge.banked.emit,
                                       self.bridge.banking.emit)
        self._build()
        # After the widgets exist and before the first frame arrives, so the
        # preview is never briefly drawn at a quality nobody chose.
        self._apply_performance()
        self._refresh_disk()
        self._disk_timer = QtCore.QTimer(self)
        self._disk_timer.timeout.connect(self._refresh_disk)
        self._disk_timer.start(15000)
        self.pipeline.start()
        self.session.start()

    # ---- layout ----------------------------------------------------------

    def _build(self) -> None:
        self.toolbar = ToolBar()
        self.toolbar.about.connect(lambda: AboutDialog(self).exec())
        # Menus rather than buttons, so these nest as more arrives instead of
        # every name having to change when the third tool appears.
        # Three menus, on the one axis that decides every entry: before a
        # session, during it, or after it. Every menu is built in one
        # place, because the last entry to be added was appended to a menu
        # three screens above and that is how the next one lands in the
        # wrong group.
        #
        # An ellipsis means a further choice is needed before anything
        # happens -- a dialog or a file chooser. No ellipsis means it
        # happens on release, and that includes every toggle.

        # Setup: what belongs to the machine rather than to the run. The
        # test is whether it survives a restart unchanged and whether you
        # can set it before ever seeing a slide.
        setup = self.toolbar.add_menu("Setup", _("menu.setup"))
        setup.addAction(_("menu.setup.microscopes"), self._open_microscopes)
        # One entry, because description and selection are one question
        # to the person asking it: which of these things is my camera,
        # and what is it bolted to.
        setup.addAction(_("menu.setup.cameras"), self._open_cameras)
        # Attribution is the one part of a file's provenance the instrument
        # cannot supply. It used to have a menu to itself to keep it
        # visible, which does not work -- an entry is invisible until the
        # menu is opened, so a menu of one advertises nothing.
        setup.addAction(_("menu.setup.photographer"), self._open_photographer)
        setup.addAction(_("menu.setup.files"), self._open_settings)
        setup.addAction(_("menu.setup.performance"), self._open_performance)
        # Where the desktop already draws a good title bar this is a
        # matter of taste, and where it draws a bad one or none at all it
        # is the difference between looking finished and not. macOS is
        # left out: there the frame is never ours to take, only to restyle.
        if sys.platform != "darwin":
            self.chrome_action = setup.addAction(_("menu.setup.chrome"))
            self.chrome_action.setCheckable(True)
            self.chrome_action.setChecked(self._frame is not None)
            self.chrome_action.toggled.connect(self._set_window_frame)
        # Absent until there is something to say. An "up to date" line
        # that is always there is a line nobody reads and a menu one
        # entry longer for ever.
        self.update_action = setup.addAction(_("menu.setup.update"))
        self.update_action.setVisible(False)
        self.update_action.triggered.connect(self._show_update)
        self._update_release = None

        setup.addSeparator()
        # Once per computer, not once per session.
        setup.addAction(_("menu.setup.install_sdk"), self._install_sdk)
        setup.addAction(_("menu.setup.install_thumbnailer"),
                        self._install_thumbnailer)

        # Capture: things that belong to the run in progress. Everything
        # here is meaningless with the camera still in its box.
        capture_menu = self.toolbar.add_menu("Capture", _("menu.capture"))
        capture_menu.addAction(_("menu.capture.timelapse"), self._open_timelapse)
        capture_menu.addSeparator()
        # Framing guides, as a viewfinder offers them: set while shooting,
        # and out of the rail because the rail is already too full to spend
        # space on a habit.
        guides = capture_menu.addMenu(_("menu.capture.guides"))
        self._guide_actions = {}
        group = QtGui.QActionGroup(self)
        group.setExclusive(True)
        # The value is the identifier and the label is what it says. Only
        # the label goes through the catalogue.
        for value, label in (("none", _("menu.capture.guides.none")),
                             ("thirds", _("menu.capture.guides.thirds")),
                             ("grid", _("menu.capture.guides.grid"))):
            act = guides.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.settings.framing_grid == value)
            act.triggered.connect(
                lambda _c=False, v=value: self._set_framing(grid=v))
            group.addAction(act)
            self._guide_actions[value] = act
        guides.addSeparator()
        self.cross_action = guides.addAction(_("menu.capture.guides.cross"))
        self.cross_action.setCheckable(True)
        self.cross_action.setChecked(self.settings.framing_cross)
        self.cross_action.setToolTip(_("menu.capture.guides.cross.tooltip"))
        self.cross_action.toggled.connect(
            lambda on: self._set_framing(cross=on))
        # In the menu rather than the rail: it is set once per illumination
        # style and then left alone. Its *off* state shows in the status
        # bar, because unlike the guides you cannot see this one by looking
        # at the picture, and off is the unusual state.
        self.wb_action = capture_menu.addAction(_("menu.capture.white_balance"))
        self.wb_action.setCheckable(True)
        self.wb_action.setChecked(True)
        self.wb_action.setToolTip(_("menu.capture.white_balance.tooltip"))
        self.wb_action.toggled.connect(self._set_white_balance)

        # One route for every panel that is a *view*. There were four of
        # these summoned four different ways, and the slide map had no
        # route at all -- closing it lost it until the next launch.
        # A plain rule rather than addSection: Qt draws a section header as
        # a second separator under this stylesheet, so the label never
        # appeared and the group gained a doubled line instead.
        capture_menu.addSeparator()
        self.calib_action = capture_menu.addAction(_("menu.capture.calibration"))
        self.map_action = capture_menu.addAction(_("menu.capture.slide_map"))
        self.perf_action = capture_menu.addAction(_("menu.capture.performance"))
        self.perf_action.setToolTip(_("menu.capture.performance.tooltip"))

        # After: acts on files already on disk, and never touches the
        # camera. Grouped by what each entry consumes -- a mosaic folder, a
        # depth map, finished pictures -- so a new one has a home rather
        # than a position to argue about.
        darkroom = self.toolbar.add_menu("Darkroom", _("menu.darkroom"))
        darkroom.addAction(_("menu.darkroom.stitch"), self._stitch_mosaic)
        darkroom.addAction(_("menu.darkroom.flythrough"), self._flythrough)
        darkroom.addSeparator()
        # Depth is the input, not the output, and since the stitcher levels
        # depth across a whole mosaic it is no longer only stacks.
        darkroom.addAction(_("menu.darkroom.depth"), self._wiggle_dialog)
        darkroom.addSeparator()
        darkroom.addAction(_("menu.darkroom.plate"), self._plate_dialog)
        darkroom.addAction(_("menu.darkroom.arrange"), self._arrange_dialog)

        self.waiting = WaitingPage()
        self.waiting.use_synthetic.connect(self._switch_to_synthetic)
        self.waiting.synthetic.setVisible(self._allow_synthetic)
        self.waiting.install_sdk_requested.connect(self._install_sdk)

        self.view = LiveView()
        self.view.region_drawn.connect(self._on_custom_region)
        self.histogram = Histogram()
        self.focus = FocusGroup()
        self.focus.peaking_toggled.connect(self._on_peaking)
        self.focus.sweep_toggled.connect(self._on_sweep)
        self.focus.stack_toggled.connect(self._on_stack_toggled)

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
        self.calib_panel.collect_flat.connect(self._collect_flat)
        self.calib_panel.build_lut.connect(self._do_lut)
        # Performance, in a floating panel like the others. Off by
        # default: it is a diagnostic, and a permanent cost table is a
        # thing you stop seeing.
        self.perf = PerfPanel()
        self.perf_window = FloatingPanel("performance", self.view)
        self.perf_window.set_relative(0.30, 0.06)
        _fill(self.perf_window, self.perf)
        self.perf_window.hide()

        self.calib_window = FloatingPanel("calibration", self.view)
        self.calib_window.set_relative(0.04, 0.06)
        _fill(self.calib_window, self.calib_panel)
        self.calib_window.hide()

        # The stack assembling live: the fun part, and also the working
        # feedback -- soft regions in this composite are regions the stack
        # has not visited yet.
        self.assembly = StackAssembly()
        self.assembly.finish_requested.connect(self._finish_stack)
        self.assembly.discard_requested.connect(self._discard_stack)
        self.assembly.wiggle_requested.connect(self._on_wiggle)
        self.stack_window = FloatingPanel("stack, assembling", self.view)
        self.assembly.close_requested.connect(self.stack_window.hide)
        self.assembly.configure(self.settings)
        self.stack_window.set_relative(0.55, 0.55)
        _fill(self.stack_window, self.assembly)
        self.stack_window.hide()
        self.stack_window.closed.connect(
            lambda: self.focus.stack.setChecked(False))

        # Turret proposals appear over the image, where the operator's eyes
        # already are when they have just turned the turret.
        self.proposal = ProposalBar(self.view)
        self.proposal.accepted.connect(self._accept_turret)
        # Ignoring a prompt is allowed, but it leaves a belief we have reason
        # to doubt -- so say so rather than carry on as though we know.
        self.proposal.dismissed.connect(self._on_proposal_gone)

        self.view.installEventFilter(self)
        self.slidemap.show()
        self.map_window.show()

        # Now that the panels exist, give each its one route.
        self._bind_panel(self.calib_action, self.calib_window, (330, 190))
        self._bind_panel(self.map_action, self.map_window,
                         lambda: self.slidemap.preferred_size(self.view))
        self._bind_panel(
            self.perf_action, self.perf_window, (330, 320),
            before_show=lambda: self.perf.set_budget(self.settings_rate()))

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
        # Named, and the rule qualified to that name. An unqualified
        # `background:` on a container is applied to every child as well,
        # which is why a checked button inside the rail could never be
        # filled: the rail was repainting it panel-coloured underneath.
        rail.setObjectName("rail")
        rail.setStyleSheet(
            f"QFrame#rail {{ background:{theme.PANEL};"
            f" border-left:1px solid {theme.LINE}; }}")

        col = QtWidgets.QVBoxLayout(rail)
        col.setContentsMargins(14, 14, 14, 14)
        col.setSpacing(18)

        # One row that is both status and doorway; the panel itself floats.
        self.calib_button = CalibrationButton()
        self.calib_button.clicked.connect(self._toggle_calibration)
        col.addWidget(self.calib_button)

        # Exposure: what the sensor was given, and the two controls that
        # give it. The histogram is the reading and these are the knobs
        # that move it, so they belong under one heading rather than in a
        # separate group called "sensor" three inches further down.
        #
        # "time" rather than "exposure": under a heading that already says
        # exposure, the long word only repeats it, and the two bars then
        # read as the pair they are -- time and gain.
        self.exposure = ValueBar("time")
        self.exposure.setRange(1, 1000)
        self.exposure.setValue(120)
        self.exposure.set_value_text("8.3 ms")
        self.exposure.valueChanged.connect(self._on_exposure)

        self.gain = ValueBar("gain")
        self.gain.setRange(100, 2000)
        self.gain.setValue(100)
        self.gain.set_value_text("1.0×")
        self.gain.valueChanged.connect(self._on_gain)

        exposure = _group("exposure", self.histogram)
        exposure.addWidget(self.exposure)
        exposure.addWidget(self.gain)
        col.addLayout(exposure)
        col.addWidget(self.focus)

        # Where the metric looks. Field curvature puts the frame edges on a
        # different focal plane, so a full-field score averages together things
        # that cannot be sharp together -- a tight box gives a decisive curve.
        self.regions = QtWidgets.QButtonGroup(self)
        # Not exclusive: a dragged box is a fourth state in which none of the
        # presets is active, and an exclusive group cannot express that.
        self.regions.setExclusive(False)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        for region, label in ((Region.SPOT, "spot"), (Region.CENTRE, "center"),
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
        #: What the metric is actually restricted to. The buttons show it;
        #: they are not where it is kept.
        self._region = Region.CENTRE
        # Where the metric looks belongs with the metric, under Focus --
        # and the box it draws is labelled on the image itself, so the
        # line telling people a box could be dragged is one they will find
        # by dragging.
        measure = _group("measure from", row)
        col.addLayout(measure)

        col.addStretch(1)

        # Subject and optics sit closest to the shutter: they are what this
        # particular shot is *of*, and they change more often than anything
        # above them. Everything higher up is instrument state.
        self.subject = SubjectField()
        col.addLayout(_group("subject", self.subject, self.subject.toolTip()))

        self.objective = ObjectiveStepper()
        self.objective.changed.connect(self._on_objective_stepped)
        # Directly under the objective because it multiplies into the same
        # number, and hidden unless the stand has one -- see _sync_optovar.
        # Zeiss call theirs an Optovar; Leitz, Olympus and Nikon call it a
        # magnification changer, so the generic name is on screen and the
        # trade name is in the tooltip, exactly as in the setup editor.
        self.optovar = QtWidgets.QComboBox()
        self.optovar.setToolTip(
            "Where the magnification changer is set. It sits between the "
            "objective\nand the tube lens and multiplies into total "
            "magnification exactly as the\nrelay does, so it goes into every "
            "file and into the flat's key.\n\nZeiss call theirs an Optovar.")
        self.optovar.currentIndexChanged.connect(self._on_optovar)
        self.optovar.hide()
        self.illumination = QtWidgets.QComboBox()
        for mode in BUILTIN_ILLUMINATION:
            self.illumination.addItem(mode.display, mode)
        self.illumination.currentIndexChanged.connect(self._on_illumination)
        optics = _group("optics", self.objective)
        optics.addWidget(self.optovar)
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
            self.stack_window.place((420, 330))
            if self.proposal.isVisible():
                self.proposal.place()
        return super().eventFilter(obj, event)

    # ---- session ---------------------------------------------------------

    def _first_scope(self) -> ScopeProfile:
        """A real stand in the library, for a camera we have never seen on one.

        This used to invent one: a four-position turret of 10x/0.30,
        20x/0.50, 40x/0.75 and 100x/1.30 oil, which is a plausible set and a
        claim about hardware the owner may not have. It reached the status
        bar, the filename tokens and the EXIF of every frame shot before
        anybody found the setup window, and a wrong objective in an archived
        file is not recoverable later.

        So the turret is created empty instead. Four positions because that
        is the commonest count and the number of *detents* is a fact about
        the stand rather than about the objectives in it, and every one of
        them says "nothing here" until somebody says otherwise.
        """
        scope = self.library.add_scope("Microscope")
        scope.turret = Turret(positions=[None] * 4, capped=[False] * 4)
        self.library.save()
        return scope

    @QtCore.Slot(object)
    def _on_status(self, status: SessionStatus) -> None:
        info = status.info
        if info is not None and self.setup is None:
            profile = self.library.remember_camera(info.serial, info.model,
                                                  make=info.brand or "")
            # The stand this camera was last on, if we know it. Without this
            # lookup the setup editor wrote to disk correctly and was then
            # ignored on every subsequent launch -- a configured Zeiss
            # reverting to a placeholder turret every time the app started.
            scope = (self.library.scope_or_default(profile.last_scope)
                     or self._first_scope())
            self.setup = Setup(camera=profile, scope=scope,
                               illumination=self._illumination)
            self.objective.set_turret(self.setup.scope.turret)
            self._sync_optovar()
            self._push_turret()

        if info is not None:
            self._fit_controls_to(info)
        self.strip.update_status(status, self.setup)
        self.strip.select_resolution(self.session.preview_resolution)
        self.strip.select_rate(self.session.framerate_cap)

        if status.is_live and not self._synced:
            self._adopt_camera_settings()

        self.shutter.set_available(status.is_live and not self.capture.busy)
        self._set_shooting_enabled(status.is_live)
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
        self.exposure.set_value_text(
            f"{us / 1000:.1f} ms" if us < 1_000_000 else f"{us / 1e6:.2f} s")
        self.gain.set_value_text(f"{gain / 100:.1f}×")
        self._synced = True

    def _use_camera(self, key: str) -> None:
        """Remember a choice and act on it."""
        from ..camera.discovery import look

        if not key or key == self.settings.camera_choice:
            return
        self.settings.camera_choice = key
        for camera in look():
            if camera.key == key:
                # Kept alongside the port, so moving the cable is
                # recoverable rather than a forgetting.
                self.settings.camera_fingerprint = camera.fingerprint
                self.settings.save()
                self._open_camera(camera)
                return
        self.settings.save()

    def _open_camera(self, camera) -> None:
        """Swap to a different camera without a restart.

        The same shape as switching to the synthetic one: stop, rebuild,
        start. Everything downstream reads from the session, so nothing
        else has to know it changed.
        """
        from ..camera.discovery import backend_for

        self.session.stop()
        self._synced = False
        self._make_backend = lambda: backend_for(camera)
        self.session = CameraSession(self._make_backend,
                                     self.bridge.status.emit,
                                     self.pipeline.submit)
        self.session.start()

    def _fit_controls_to(self, info) -> None:
        """Show only the controls this camera really has.

        A slider over a control that does not exist is worse than no
        slider, because it moves and nothing happens -- and the person
        reasonably concludes the program is broken rather than the
        camera. Measured on two cameras in one machine: one exposes
        sixteen controls, the other none whatsoever.

        Where the control exists, the widget takes the device's own
        range. It used to assert 100..2000 on everything, which on a
        camera whose gain runs 0..100 meant our floor was its ceiling:
        the slider pinned gain at maximum and every movement clamped to
        the same place, looking for all the world like a dead control.
        """
        for widget, span, why in (
                (self.exposure, info.exposure_range_us, _("rail.no_exposure")),
                (self.gain, info.gain_range_pct, _("rail.no_gain"))):
            if span is None:
                widget.setEnabled(False)
                widget.setToolTip(why)
                continue
            widget.setEnabled(True)
            widget.setToolTip("")
            low, high = span
            if widget is self.gain:
                widget.setRange(int(low), int(high))

    def _switch_to_synthetic(self) -> None:
        from ..camera.mock import MockCamera

        self._synced = False
        # In place. Building a new session here left capture, calibration
        # and the opportunist holding the old one, so the preview ran and
        # a capture answered "no camera connected".
        self.session.retarget(lambda: MockCamera(fps=30.0))

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
        self.exposure.set_value_text(
            f"{us / 1000:.1f} ms" if us < 1_000_000 else f"{us / 1e6:.2f} s")

    def _on_gain(self, value: int) -> None:
        self.session.set_gain(value)
        self.pipeline.reset_focus_peak()
        self.gain.set_value_text(f"{value / 100:.1f}×")

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
            self.strip.set_note(_("note.objective_changed"))
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

        # Every position, always, so a wrong guess can be corrected rather
        # than merely refused. A refused proposal used to leave the recorded
        # objective stale, and the next rotation then stepped from the wrong
        # place -- one unanswered prompt poisoned every prompt after it.
        offered = {i for i, _level in (c[1] for c in choices)}
        others = [(o.label, (i, event.level))
                  for i, o in enumerate(turret.positions)
                  if o is not None and i not in offered]

        if len(choices) > 1:
            self.proposal.propose(
                _("proposal.which.heading"),
                _("proposal.which.detail"),
                choices, others)
        else:
            self.proposal.propose(
                _("proposal.now.heading", objective=objective.label),
                _("proposal.now.detail", reason=event.reason,
                  percent=f"{event.confidence * 100:.0f}"),
                choices, others)

    def _on_proposal_gone(self) -> None:
        """Dismissed or expired.

        'Didn't switch' is an answer and leaves the recorded objective
        trustworthy. A timeout is not an answer, and the objective is then
        a guess -- mark it, because it keys every calibration lookup and
        goes into every file.
        """
        if self.proposal.answered_explicitly:
            self.objective.set_uncertain(False)
        else:
            self.objective.set_uncertain(True)

    def _accept_turret(self, payload) -> None:
        if self.setup is None:
            return
        self.objective.set_uncertain(False)
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
            "turret direction learned. Proposals should be right from now on")

    def _remember_objective(self) -> None:
        """Persist which objective is in the light path.

        The schema always carried it; nothing reliably wrote it. Position
        changes only reached disk when something *else* happened to save --
        a brightness signature being learned, or the handedness being
        settled -- so stepping the objective by hand usually did not stick,
        and the next launch came back on whatever was last written. It is
        the best guess there is for the starting state, so it is worth a
        write every time it moves.
        """
        if self.setup is None:
            return
        self.library.scopes[self.setup.scope.id] = self.setup.scope
        self.library.file_camera(self.setup.camera)
        self.library.bind(self.setup.camera.serial, self.setup.scope.id)
        self.library.save()

    def _on_objective_changed(self) -> None:
        """Everything keyed on magnification is now stale."""
        self._remember_objective()
        self.strip.update_status(self.session.status, self.setup)
        self.opportunist.set_key(flat_key(self.setup, self.subject.slide_note))
        self._refresh_calibration()
        # Scale changed, so tracked positions no longer measure anything.
        self.pipeline.reset_tracking()
        self.slidemap.clear()
        self.pipeline.reset_focus_peak()
        self._push_turret()

    def _sync_optovar(self) -> None:
        """Offer the magnification changer only on a stand that has one.

        Hidden rather than disabled, and hidden at one factor as well as at
        none: a changer is rare glass, and a control for a piece of the
        optical path that is not fitted is a question its owner cannot
        answer. A one-factor list is somebody having ticked the box and
        typed a single number, which is a stand with nothing to choose.

        Rebuilt from scratch every time, because the scope underneath can be
        replaced wholesale -- by the setup editor, or by a camera arriving
        and bringing the stand it was last on.
        """
        factors = list(self.setup.scope.optovar) if self.setup else []
        self.optovar.blockSignals(True)
        try:
            self.optovar.clear()
            for i, factor in enumerate(factors):
                self.optovar.addItem(f"{factor:g}× changer", i)
            if factors:
                # Clamped: a saved position can outlive the list it indexed
                # if the factors were edited, and `optovar_factor` clamps the
                # same way, so the control must agree with it rather than
                # showing a position the scope is not using.
                self.optovar.setCurrentIndex(
                    min(self.setup.scope.optovar_current, len(factors) - 1))
        finally:
            self.optovar.blockSignals(False)
        self.optovar.setVisible(len(factors) > 1)

    def _on_optovar(self, index: int) -> None:
        """The changer was moved, so say so before the next frame is written.

        Nothing recorded this before: the factor was pinned at the first
        position, so anybody using the 1.6 setting got a total magnification
        and a µm-per-pixel figure understated by exactly that much, silently
        and in the file rather than only on screen. It is the same kind of
        event as the objective changing -- one term of the same product --
        so it takes the same path, which also re-keys the flat, since
        `Setup.calibration_key()` carries the factor.
        """
        if self.setup is None or index < 0:
            return
        if index == self.setup.scope.optovar_current:
            return
        self.setup.scope.optovar_current = index
        self._on_objective_changed()

    def _on_rate(self, at: int) -> None:
        fps = self.strip.rate.itemData(at)
        if fps is None:
            return
        self.session.set_framerate_cap(int(fps))

    def _on_region(self, region: Region) -> None:
        """Choose where the metric looks, or stop restricting it.

        These are a restriction and `full` is the absence of one, so
        clicking the active button again releases it rather than doing
        nothing. Clicking `full` while it is active has nowhere to go.

        Read from `self._region` rather than from the buttons, because Qt
        toggles a checkable button *before* `clicked` reaches us: asking
        the buttons what was active returns the state after the click, so
        every button looked like the one already chosen and every click
        collapsed to `full`.
        """
        if region is self._region:
            region = Region.FULL
        self._region = region
        self.pipeline.set_focus_region(region)
        for r, b in self._region_buttons.items():
            b.setChecked(r is region)

    def _on_custom_region(self, rect: tuple) -> None:
        """A box dragged on the image wins over any preset."""
        self._region = Region.CUSTOM
        self.pipeline.set_focus_region(Region.CUSTOM, rect)
        for b in self._region_buttons.values():
            b.setChecked(False)

    # ---- calibration -----------------------------------------------------

    def _do_dark(self) -> None:
        self.calibration.capture_dark()

    @QtCore.Slot(bool)
    def _collect_flat(self, on: bool) -> None:
        """Start or stop gathering blank fields.

        A mode rather than a background habit: each frame freezes the
        preview for about a second, and a flat is only valid at the
        illumination it was shot under -- which nothing here can read, so
        the operator saying "now" is the only reliable signal there is.
        """
        self.opportunist.enabled = on
        self.strip.set_note(
            "collecting blank fields -- move to empty glass" if on else "")
        self._refresh_calibration()

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
        self.strip.set_note(_("note.banking") if busy else "")

    def _refresh_calibration(self) -> None:
        if self.setup is None:
            return
        actual = self.session.actual_settings() or (8330, 100)
        status = self.store.status(self.setup, actual[0], actual[1],
                                   self.subject.slide_note)
        live = self.session.status.is_live and not self.calibration.busy
        self.calib_panel.set_status(status, self.opportunist.count,
                                    self.opportunist.bank.wanted, live=live,
                                    collecting=self.opportunist.enabled)
        self.calib_button.set_status(status, busy=self.calibration.busy)

    def _on_capture(self) -> None:
        self._sync_raw_requirement()
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
        self.view.set_notice(f"hold still -- {state}"
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
        if result.ok and self.stack_session is not None:
            self._adopt_slice(result)
        elif result.ok and not discarded and self.mosaic is not None:
            self._adopt_tile(result)
        elif not result.ok and self.stack_session is not None:
            self.stack_trigger.capture_failed()

    def _adopt_slice(self, result: CaptureResult) -> None:
        """A slice landed. Adopt it, advance the trigger, feed the assembly.

        A moved slice is discarded automatically rather than asked about:
        the operator's hands are on the fine focus and the plane is easy to
        revisit -- a dialog would cost more than the retake. The trigger goes
        back to watching, and the same pause will fire it again.
        """
        if result.moved:
            result.path.unlink(missing_ok=True)
            self.stack_trigger.capture_failed()
            self.strip.set_note(_("note.slice_discarded"))
            return
        s = self.stack_session.adopt(result.path, metric=0.0)
        self.stack_trigger.slice_landed()
        if self.mosaic is not None and self._tile_anchor is None:
            # First slice anchors the tile: its position is the tile's
            # position, and moving ~a third of a field from here is the
            # gesture that seals it.
            self._tile_anchor = result.position
            h, w = ((self._last_preview.shape[:2])
                    if self._last_preview is not None else (0, 0))
            self._tile_frame = (w, h)
            self._tile_preview = (self._last_preview.copy()
                                  if self._last_preview is not None else None)
        try:
            from ..process.stitch import read_bayer_dng
            self.assembly.add_slice(
                read_bayer_dng(self.stack_session.dir / s.filename)[::2, ::2])
        except Exception:
            pass                       # the preview must never block capture
        if self.mosaic is not None:
            self.strip.set_note(_("note.slice_landed.first", n=s.index))
        else:
            self.strip.set_note(_("note.slice_landed", n=s.index))

    def _adopt_tile(self, result: CaptureResult) -> None:
        h, w = ((self._last_preview.shape[:2])
                if self._last_preview is not None else (0, 0))
        tile = self.mosaic.adopt(result.path, result.position, (w, h))
        self.slidemap.tile_added(result.position, self._last_preview)
        note = "" if result.position is not None else " (no position!)"
        self.shutter.set_result(f"tile {tile.index} · {tile.filename}{note}",
                                ok=result.position is not None)

    def _on_stack_toggled(self, on: bool) -> None:
        if on:
            if self.mosaic is not None:
                # Stacked mosaic. No session yet: each field's stack is
                # created lazily at its first slice and sealed by the
                # slide to the next field, so the whole mosaic is one
                # rhythm -- rack, pause, rack, pause, slide -- with no
                # button between fields.
                self.assembly.reset()
                self.stack_window.place((420, 330))
                self.stack_window.show()
                self.stack_trigger.arm()
                self.strip.set_note(_("note.stacked_mosaic"))
                return
            self.stack_session = StackSession(self.settings.capture_root,
                                      self.subject.subject)
            if self.setup is not None:
                obj = self.setup.scope.turret.objective
                self.stack_session.set_meta(
                    objective=obj.label if obj else None,
                    illumination=self.setup.illumination.display)
            self.assembly.reset()
            self.stack_window.place((420, 330))
            self.stack_window.show()
            self.stack_trigger.arm()
            self.strip.set_note(_("note.stack_armed"))
        else:
            self.stack_trigger.disarm()
            if self.mosaic is not None:
                # Chip off mid-mosaic keeps what was racked: the current
                # field's slices seal into their tile right now.
                self._seal_tile_stack()
                self.stack_window.hide()
                return
            # The chip going off without Finish being pressed keeps the
            # slices and skips the merge -- the folder is complete and can
            # be merged later from its manifest. Finish and Discard are the
            # explicit endings, and they live in the window.
            if not self._stack_ending:
                self.stack_window.hide()
                self.stack_session = None

    _stack_ending = False

    def _finish_stack(self) -> None:
        """Finish & merge, from the window. The window stays: the progress
        bar and the result belong where the operator was already looking."""
        if self.stack_session is None or len(self.stack_session.slices) < 2:
            self.assembly.set_merging(None, None,
                                      "need at least two slices")
            return
        self.stack_trigger.disarm()
        done, self.stack_session = self.stack_session, None
        self._stack_ending = True
        self.focus.stack.setChecked(False)
        self._stack_ending = False
        self.assembly.finish.setEnabled(False)
        self.assembly.discard.setEnabled(False)
        self._wiggle_dir = done.dir
        self._run_stack_merge(done.dir)

    def _discard_stack(self) -> None:
        """Delete the stack, slices and all. Confirmed, because this is the
        one button in the window that destroys data."""
        if self.stack_session is None:
            return
        n = len(self.stack_session.slices)
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(_("stack.discard.title"))
        box.setText(n_("stack.discard.question", n))
        keep = box.addButton(_("stack.discard.keep"),
                             QtWidgets.QMessageBox.ButtonRole.RejectRole)
        kill = box.addButton(_("stack.discard.confirm"),
                             QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep)
        box.setStyleSheet(theme.stylesheet())
        box.exec()
        if box.clickedButton() is not kill:
            return
        import shutil
        done, self.stack_session = self.stack_session, None
        self.stack_trigger.disarm()
        shutil.rmtree(done.dir, ignore_errors=True)
        self._stack_ending = True
        self.focus.stack.setChecked(False)
        self._stack_ending = False
        self.stack_window.hide()
        self.strip.set_note(_("note.stack_discarded"))

    def _fire_stack_slice(self) -> bool:
        """The trigger pulling the shutter. Runs on the UI thread, from the
        signal stream; StillCapture does the work off it.

        In a stacked mosaic the first rack-pause at a new field is also
        what opens that tile's stack -- lazily, so panning across fields
        without racking never litters the mosaic with empty folders.
        """
        if self.capture.busy:
            return False
        if self.stack_session is None:
            if self.mosaic is None or not self.focus.stack.isChecked():
                return False
            self.stack_session = self.mosaic.begin_stack_tile(
                self.subject.subject)
            if self.setup is not None:
                obj = self.setup.scope.turret.objective
                self.stack_session.set_meta(
                    objective=obj.label if obj else None,
                    illumination=self.setup.illumination.display)
            self._tile_anchor = None
            self._tile_preview = None
            self.assembly.reset()
        self._sync_raw_requirement()
        return self.capture.trigger(self.setup, subject=self.subject.subject,
                                    slide=self.subject.slide_note)

    #: Sliding this fraction of a field away from a tile's anchor seals it.
    #: Past deliberate-move territory (jitter is a few percent), well short
    #: of the ~80% slide to the next tile position.
    SEAL_FRACTION = 0.35

    def _maybe_seal_tile(self, s) -> None:
        """The slide-away gesture, watched from the live stream."""
        if (self.stack_session is None or self.mosaic is None
                or self._tile_anchor is None or s.stage_pos is None
                or self.capture.busy):
            return
        ax, ay = self._tile_anchor
        w, h = self._tile_frame
        if (abs(s.stage_pos[0] - ax) > self.SEAL_FRACTION * w
                or abs(s.stage_pos[1] - ay) > self.SEAL_FRACTION * h):
            self._seal_tile_stack()

    def _seal_tile_stack(self) -> None:
        """Whatever was racked at this field becomes the tile.

        Nothing was clicked: sliding to the next field is the gesture.
        One slice is not a stack -- the exposure itself becomes an
        ordinary tile; two or more queue a background merge, and by the
        time the operator finishes the next field the previous tile is
        usually done. Zero slices means the operator just passed through.
        """
        done, self.stack_session = self.stack_session, None
        anchor, self._tile_anchor = self._tile_anchor, None
        preview, self._tile_preview = self._tile_preview, None
        if done is None or self.mosaic is None:
            return
        import shutil
        n = len(done.slices)
        frame = self._tile_frame
        if preview is None:
            preview = self._last_preview
        if n == 0:
            shutil.rmtree(done.dir, ignore_errors=True)
            return
        if n == 1:
            src = done.dir / done.slices[0].filename
            tile = self.mosaic.adopt(src, anchor, frame)
            shutil.rmtree(done.dir, ignore_errors=True)
            self.slidemap.tile_added(anchor, preview)
            self.strip.set_note(_("note.tile_sealed.single", n=tile.index))
        else:
            tile = self.mosaic.adopt_stack(done, anchor, frame)
            self.slidemap.tile_added(anchor, preview, state="merging",
                                     label=f"×{n}")
            self._queue_tile_merge(tile.index, done.dir)
            self.strip.set_note(n_("note.tile_sealed.stack", n,
                                   tile=tile.index))
        self.assembly.reset()

    def _queue_tile_merge(self, index: int, directory) -> None:
        """One worker, tiles in order, the map as the status surface."""
        self._tile_merges.append((index, Path(directory)))
        self._next_tile_merge()

    def _next_tile_merge(self) -> None:
        if self._tile_merging is not None or not self._tile_merges:
            return
        index, directory = self._tile_merges.pop(0)
        self._tile_merging = index
        smoothing = self.settings.stack_smoothing
        feather = self.settings.stack_feather

        def work():
            from ..process.stack import merge
            try:
                # Always bayer, whatever the free-standing preference: the
                # stitcher reads bayer tiles.
                merge(directory, output="bayer", smoothing=smoothing,
                      feather=feather)
                self.bridge.tile_merge.emit((index, True, ""))
            except Exception as exc:
                self.bridge.tile_merge.emit((index, False, str(exc)))

        threading.Thread(target=work, daemon=True,
                         name=f"tile-merge-{index}").start()

    @QtCore.Slot(object)
    def _on_tile_merge(self, message) -> None:
        index, ok, note = message
        self._tile_merging = None
        self.slidemap.set_tile_state(index, "ok" if ok else "failed")
        if not ok:
            self.strip.set_note(_("note.tile_merge_failed", n=index,
                                  reason=note))
        self._next_tile_merge()

    _wiggle_dir = None

    def _wiggle_dialog(self) -> None:
        """Depth renders from any finished stack on disk."""
        start = str(self._wiggle_dir or self.settings.capture_root)
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Stack folder (with stacked.dng + depth.png)", start)
        if directory:
            RenderDialog(Path(directory), self._run_wiggle, self).exec()

    def _on_wiggle(self) -> None:
        if self._wiggle_dir is not None:
            self.assembly.wiggle_btn.setEnabled(False)
            RenderDialog(self._wiggle_dir, self._run_wiggle, self).exec()
            self.assembly.wiggle_btn.setEnabled(True)

    def _run_wiggle(self, directory: Path, wanted=None) -> None:
        wanted = set(wanted or [k for k, _l, _h, on in RENDERS if on])
        invert = self.settings.wiggle_invert
        self.strip.set_note(_("note.synthesising"))

        def work():
            from ..process.aperture import focus_pull
            from ..process.mesh import export_ply, turntable
            from ..process.relief import dic
            from ..process.wiggle import autostereogram, stereo, wigglegram
            jobs = [("wiggle", wigglegram), ("stereo", stereo),
                    ("dic", dic), ("mesh", export_ply),
                    ("sirds", autostereogram), ("pull", focus_pull),
                    ("turntable", turntable)]
            made, failed = [], []
            for key, fn in jobs:
                if key not in wanted:
                    continue
                try:
                    fn(directory, invert=invert)
                    made.append(key)
                except Exception as exc:
                    # One artifact failing must not cost the others: they
                    # are independent renders of the same two files.
                    failed.append(f"{key} ({exc})")
            note = f"{len(made)} rendered → {directory.name}"
            if failed:
                note += " · failed: " + ", ".join(failed)
            self.bridge.wiggle.emit((note, not failed))

        threading.Thread(target=work, daemon=True, name="wiggle").start()

    @QtCore.Slot(object)
    def _on_wiggle_done(self, message) -> None:
        note, ok = message
        self.strip.set_note(note)
        self.assembly.wiggle_btn.setEnabled(True)

    def _plate_dialog(self) -> None:
        start = Path(self._wiggle_dir or self.settings.capture_root)
        PlateDialog(start, self._run_plate, self).exec()

    def _run_plate(self, sources, target, columns, title, footer) -> None:
        self.strip.set_note(_("note.arranging_plate"))

        def work():
            from ..process.plate import plate
            try:
                path = plate(sources, target, columns=columns,
                             title=title, footer=footer)
                self.bridge.wiggle.emit((f"plate → {path.name}", True))
            except Exception as exc:
                self.bridge.wiggle.emit((f"plate failed -- {exc}", False))

        threading.Thread(target=work, daemon=True, name="plate").start()

    def _flythrough(self) -> None:
        """Make a film of a stitched mosaic.

        No dialog: there is nothing to ask. It reads the composite, picks
        its own subjects by looking for structure, and everything else is
        a decision about pacing that a number in a box would not improve.
        """
        start = str(self._wiggle_dir or self.settings.capture_root)
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Stitched mosaic to fly through", start)
        if not directory:
            return
        self.strip.set_note(_("note.flythrough"))

        def work():
            from ..process.flythrough import flythrough
            try:
                path = flythrough(Path(directory))
                self.bridge.wiggle.emit((f"flythrough → {path.name}", True))
            except Exception as exc:
                self.bridge.wiggle.emit(
                    (f"flythrough failed -- {exc}", False))

        threading.Thread(target=work, daemon=True, name="flythrough").start()

    def _arrange_dialog(self) -> None:
        start = str(self._wiggle_dir or self.settings.capture_root)
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Capture to take specimens from", start)
        if directory:
            ArrangeDialog(Path(directory), self._run_arrange, self).exec()

    def _run_arrange(self, directory, target, style, title) -> None:
        self.strip.set_note(_("note.finding_specimens"))

        def work():
            from ..process.arrange import arrangement
            try:
                path, found = arrangement(directory, target, style=style,
                                          title=title)
                self.bridge.wiggle.emit(
                    (f"{found} specimens arranged → {path.name}", True))
            except Exception as exc:
                self.bridge.wiggle.emit((f"arrangement failed -- {exc}",
                                         False))

        threading.Thread(target=work, daemon=True, name="arrange").start()

    def _install_sdk(self) -> None:
        """Fetch a vendor SDK, then let the session try again at once."""
        SdkDialog(self).exec()
        # The loader caches whichever brand it found; clear it so a newly
        # installed one is picked up without restarting.
        from ..camera import toupcam
        toupcam._vendor = None

    def _install_thumbnailer(self) -> None:
        """Teach the file manager to show our DNGs.

        It cannot already: the desktop's thumbnailer sniffs our files as
        image/x-adobe-dng and has no loader for that type, so the preview
        every one of them carries is never reached.
        """
        from ..process.thumbnail import install
        try:
            path = install()
        except Exception as exc:
            self.strip.set_note(_("thumbnailer.failed", reason=exc))
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(_("thumbnailer.title"))
        box.setText(f"Wrote {path.name}.")
        box.setInformativeText(
            "Restart the file manager (or log out and in), then clear the "
            "old empty entries:\n\n    rm -rf ~/.cache/thumbnails")
        box.setStyleSheet(theme.stylesheet())
        box.exec()

    def _run_stack_merge(self, directory) -> None:
        opts = dict(output=self.settings.stack_output,
                    smoothing=self.settings.stack_smoothing,
                    feather=self.settings.stack_feather)

        def work():
            from ..process.stack import merge
            try:
                path, report = merge(
                    directory, **opts,
                    progress=lambda stage, i, n: self.bridge.stack_merge.emit(
                        ("progress", stage, i, n)))
                self.bridge.stack_merge.emit(("done", (
                    f"stacked {report['slices']} slices · "
                    f"{report['depth_levels']} depth levels → {path.name}"),
                    True))
            except Exception as exc:
                self.bridge.stack_merge.emit(("done",
                                              f"merge failed -- {exc}", False))

        threading.Thread(target=work, daemon=True, name="stack-merge").start()

    @QtCore.Slot(object)
    def _on_stack_merge(self, message) -> None:
        if message[0] == "progress":
            _kind, stage, i, n = message
            # Three stages of n slices each, as one bar: honest and smooth.
            offset = {"reading": 0, "measuring": 1, "blending": 2}.get(stage, 0)
            self.assembly.set_merging(offset * n + i, 3 * n,
                                      f"{stage} {i}/{n}…")
            return
        _kind, note, ok = message
        # The session is already gone either way -- Finish and Discard have
        # nothing left to act on -- so the buttons give way to Close. On
        # failure the folder is still on disk, mergeable later. Success
        # also unlocks the depth map's party trick.
        self.assembly.set_finished(note, wiggle=ok)
        self.strip.set_note(note)

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
            # A field still being stacked seals first -- ending the mosaic
            # is as much a "done with this field" gesture as sliding away.
            self._seal_tile_stack()
            # The manifest is already current, so closing is just letting go
            # -- but a finished mosaic almost always wants stitching, and
            # making the operator go hunting through a menu for the obvious
            # next step is how a tool feels unfinished.
            done, self.mosaic = self.mosaic, None
            self.slidemap.set_mosaic(False)
            if done is not None and len(done.tiles) >= 2:
                pending = len(self._tile_merges) + (
                    1 if self._tile_merging is not None else 0)
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle(_("mosaic.finished.title"))
                box.setText(f"{len(done.tiles)} tiles in {done.dir.name}."
                            + (f" {pending} still merging -- stitching will "
                               "wait for them." if pending else ""))
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
        # A field mid-stack: undo means "scrap what I racked here", before
        # any recorded tile is touched.
        if self.stack_session is not None:
            import shutil
            n = len(self.stack_session.slices)
            shutil.rmtree(self.stack_session.dir, ignore_errors=True)
            self.stack_session = None
            self._tile_anchor = None
            self._tile_preview = None
            self.assembly.reset()
            self.shutter.set_result(f"{n} slice{'s' if n != 1 else ''} "
                                    "scrapped -- field reset")
            return
        last = self.mosaic.tiles[-1] if self.mosaic.tiles else None
        if last is not None and self._tile_merging == last.index:
            self.strip.set_note(_("note.tile_merging"))
            return
        tile = self.mosaic.undo()
        if tile is not None:
            self._tile_merges = [(i, d) for i, d in self._tile_merges
                                 if i != tile.index]
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
        box.setWindowTitle(_("moved.title"))
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
            self.shutter.set_result("discarded -- hold still this time")
            self._on_capture()
            return True
        return False

    def _set_white_balance(self, on: bool) -> None:
        """Both the capture and the status bar, from one switch."""
        self.capture.white_balance = on
        self.strip.set_white_balance(on)

    def _bind_panel(self, action, window, size, before_show=None) -> None:
        """Wire a menu entry and a floating panel to one truth.

        The check mark has to follow the panel rather than remember what
        was last asked of it: these can be dragged closed by their own
        corner, and a menu insisting a panel is open when it is not is
        worse than no check mark at all.
        """
        action.setCheckable(True)
        # isHidden, not isVisible: a child of a window that has not been
        # shown yet reports invisible however it was set up, which had the
        # slide map -- shown at startup -- opening with its entry unticked,
        # and therefore un-closable, because unticking an unticked box
        # emits nothing.
        action.setChecked(not window.isHidden())

        def toggled(on: bool) -> None:
            if on:
                if before_show is not None:
                    before_show()
                # Called, not captured: a default worked out at bind time
                # comes from a view that has not been laid out yet.
                window.place(size() if callable(size) else size)
                window.show()
                window.raise_()
            else:
                window.hide()

        action.toggled.connect(toggled)
        # Closing by the panel's own corner must not leave the entry ticked.
        window.closed.connect(lambda: action.setChecked(False))

    def _toggle_calibration(self) -> None:
        """The rail button, which is a second view of the same truth.

        isHidden rather than isVisible, for the same reason the binding
        uses it: a child of an unshown window always reports invisible, so
        isVisible made this button open-only.
        """
        self.calib_action.setChecked(self.calib_window.isHidden())

    def _refresh_disk(self) -> None:
        """Room left where captures land.

        Polled on a timer rather than folded into the frame signal: it is a
        filesystem call, it changes on the scale of a capture rather than a
        frame, and the capture folder can be moved or unplugged between two
        of them -- so a failure here reports nothing rather than raising on
        the UI thread.
        """
        root = self.settings.capture_root
        try:
            # The folder may not exist until the first capture creates it,
            # so ask about the nearest parent that does.
            probe = Path(root)
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            free = shutil.disk_usage(probe).free
        except OSError:
            free = None
        self.strip.set_disk(free, root)

    def _set_shooting_enabled(self, live: bool) -> None:
        """Grey out the controls that need a camera to mean anything.

        With nothing connected the rail used to sit there fully lit --
        exposure and gain sliders, focus toggles, a measurement region --
        every one of them a control over a thing that does not exist. It
        read as an application that was broken rather than one that was
        waiting, which is the first thing a stranger sees.

        Deliberately partial. The subject and slide notes, the optics and
        the menus stay live: those are things somebody can reasonably set
        while walking back to the bench with the camera still in its box,
        and disabling them would trade one wrong impression for another.
        """
        for w in (self.exposure, self.gain, self.focus, self.calib_button):
            w.setEnabled(live)
        for button in self.regions.buttons():
            button.setEnabled(live)

    def _sync_raw_requirement(self) -> None:
        """Tell the capture whether this frame is a photograph or a part.

        A tile or a slice gets read back by the stitcher and the merge, so
        it must keep its raw however the format preference is set. Checked
        at each shutter press rather than tracked, because a session can
        open or close between any two of them.
        """
        self.capture.raw_required = (self.mosaic is not None
                                     or self.stack_session is not None
                                     or self.focus.stack.isChecked())

    def _set_framing(self, grid: str | None = None,
                     cross: bool | None = None) -> None:
        """Change a framing guide and remember it.

        Saved immediately rather than on some later confirmation, because
        this is a habit rather than a decision -- somebody who wants thirds
        wants thirds tomorrow too.
        """
        if grid is not None:
            self.settings.framing_grid = grid
        if cross is not None:
            self.settings.framing_cross = bool(cross)
        self.view.framing_grid = self.settings.framing_grid
        self.view.framing_cross = self.settings.framing_cross
        self.view.update()
        self.settings.save()

    def _open_performance(self) -> None:
        PerformanceDialog(self.settings, self._apply_performance, self).exec()

    def _apply_performance(self) -> None:
        """Push the performance preferences at the things that obey them.

        Called at startup and again on every change in the dialog, so a
        setting can be judged against the slide on the stage rather than
        against a memory of the last one.
        """
        self.view.preview_quality = self.settings.preview_quality
        self.view.framing_grid = self.settings.framing_grid
        self.view.framing_cross = self.settings.framing_cross
        apply_thread_budget(self.settings.cpu_threads)

    def settings_rate(self) -> int:
        """The frame cap the loop is currently trying to hit."""
        return int(self.session.framerate_cap or 40)

    def _open_microscopes(self) -> None:
        """The stand library. Openable with no camera attached.

        `self.setup` is only built when a camera arrives, and this used to
        return silently without one -- so the menu item did nothing at all
        on a machine with nothing plugged in, which is exactly when somebody
        sits down to describe their stand.
        """
        current = self.setup.scope.id if self.setup else None
        dialog = MicroscopeDialog(self.library, current, self)
        if not dialog.exec():
            return
        scope = dialog.selected
        if self.setup is None or scope is None:
            # No camera yet, so there is no live setup to re-point. The
            # library has the edits; the camera-arrival path picks them up.
            return
        self.setup.scope = scope
        self.objective.set_turret(scope.turret)
        # The changer belongs to the stand, so a different stand may have a
        # different one, or none at all.
        self._sync_optovar()
        self._push_turret()
        self.strip.update_status(self.session.status, self.setup)
        # The optical stack changed, so anything banked for the old one is
        # no longer a flat for this one.
        self.opportunist.set_key(flat_key(self.setup, self.subject.slide_note))
        self._refresh_calibration()
        # And the map's scale changed with it: positions in old-objective
        # pixels no longer measure anything on the new one.
        self.slidemap.clear()

    def _open_cameras(self) -> None:
        """The camera library.

        Every camera that has ever been plugged in is here, whether or not
        it is plugged in now, so the one on the microscope can be described
        while it is sitting on the bench.
        """
        current = self.setup.camera.serial if self.setup else None
        dialog = CameraDialog(self.library, current, self)
        accepted = dialog.exec()
        # Selection first: a different camera means a different profile,
        # and reloading the old one over the top of it would undo the
        # thing that was just asked for.
        if accepted and dialog.picked:
            self._use_camera(dialog.picked)
            return
        if not accepted or self.setup is None:
            return
        updated = self.library.cameras.get(self.setup.camera.serial)
        if updated is not None:
            self.setup.camera = updated
            self.strip.update_status(self.session.status, self.setup)
            self._refresh_calibration()

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
        # Stitching the live mosaic is a "done with this field" gesture
        # like any other: a field still being stacked seals first, or the
        # freshest tile -- the one the operator is probably most curious
        # about -- would be the one missing from the composite.
        if (self.mosaic is not None and self.stack_session is not None
                and Path(directory) == self.mosaic.dir):
            self._seal_tile_stack()
        if self._tile_merging is not None or self._tile_merges:
            # Wait for the background tile merges rather than letting the
            # stitcher's own merge-on-demand race the queue over the same
            # folder. Poll and reopen; the merges are seconds each.
            pending = len(self._tile_merges) + 1
            # A plural picked by the catalogue rather than by an inline
            # conditional, which only ever had an English answer.
            self.strip.set_note(n_("note.waiting_on_merges", pending))
            QtCore.QTimer.singleShot(
                700, lambda: self._stitch_mosaic(directory))
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
                self.bridge.stitch.emit(("done", f"failed -- {exc}", False))

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
        self.stack_trigger.observe(s)
        self._maybe_seal_tile(s)
        self._last_preview = s.preview
        if s.turret_event is not None:
            self._on_turret_event(s.turret_event)

        # The instruments repaint at a fraction of the frame rate. A
        # histogram and a focus trace are read as trends, not as individual
        # frames, and at 30 Hz their repaints cost more of the UI thread
        # than the live image itself. The focus trace still accumulates
        # every frame and only its drawing is thinned; the levels are now
        # computed at this same divisor, which is why it is shared.
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % INSTRUMENT_DIVISOR == 0:
            if self.perf_window.isVisible():
                backend = self.session.backend
                self.perf.set_driver_dropped(
                    backend.driver_dropped() if backend is not None else None)
                self.perf.update_costs(s.costs, s.stats)
            self.histogram.set_data(s.histogram, s.clipped_fraction,
                                    s.channel_clipped)
            self.focus.set_data(s.focus_trace, s.focus_fraction_of_peak)
            self.strip.set_live(s)

    #: Whoever is drawing the frame: a `WindowsFrame`, a `SystemFrame`, or
    #: None where the platform draws its own.
    _frame: object | None = None

    def nativeEvent(self, kind, message):
        """Non-client messages, when we own the frame.

        Only reached on Windows, and only after `WindowsFrame.attach` has
        succeeded -- the `SystemFrame` used everywhere else answers Qt
        events instead and has no `handle`. Anything not claimed falls
        through to Qt exactly as before, so a message this does not
        understand behaves the way it always did.
        """
        if (kind == b"windows_generic_MSG"
                and hasattr(self._frame, "handle")):
            try:
                import ctypes
                from ctypes import wintypes

                msg = ctypes.cast(int(message),
                                  ctypes.POINTER(wintypes.MSG)).contents
                handled, result = self._frame.handle(
                    msg.message, int(msg.wParam), int(msg.lParam))
                if handled:
                    return True, result
            except Exception:
                pass          # a frame that misbehaves must not eat the app
        return super().nativeEvent(kind, message)

    def watch_for_updates(self) -> None:
        """Ask once, quietly, if the setting allows it.

        Started from `main` rather than the constructor so that a window
        built by a test never reaches the network.
        """
        if not self.settings.check_for_updates:
            return
        self._update_watch = UpdateWatch(self)
        self._update_watch.found.connect(self._update_found)
        self._update_watch.start()

    def _update_found(self, release) -> None:
        """One entry appears in Setup. Nothing else happens.

        No dialog on arrival: it would land four seconds after launch, on
        top of whatever somebody had already started doing, for news that
        keeps.
        """
        self._update_release = release
        self.update_action.setVisible(True)
        mark_as_update(self.update_action)

    def _show_update(self) -> None:
        if self._update_release is None:
            return
        UpdateDialog(self._update_release, self).exec()

    def _set_window_frame(self, ours: bool) -> None:
        """Swap between our chrome and the platform's, without a restart.

        Saved as `ours` or `system` rather than back to `auto`: once
        somebody has touched the switch, the guess about their desktop
        has been overruled and should stay overruled.
        """
        if ours == (self._frame is not None):
            return
        if ours:
            took = self.take_native_frame()
        else:
            took = self._frame.detach()
            self._frame = None
            # The caption coming back is the platform's, drawn in the
            # system's light or dark setting rather than ours. The mirror
            # of what take_native_frame does on the way in.
            theme.match_frame(self)
        if not took and ours:
            # Could not take it. Say so by putting the tick back rather
            # than leaving a checked box over a window that Windows or
            # the window manager is still drawing.
            self.chrome_action.setChecked(False)
            return
        self.settings.window_frame = "ours" if ours else "system"
        self.settings.save()

    def take_native_frame(self) -> bool:
        """Draw our own title bar, where that is ours to draw.

        Two implementations, because the platforms differ in what has to
        be taken. Windows hands out the whole non-client area and a pile
        of messages to answer about it. Everywhere else it is a frameless
        window and two calls that hand dragging back to the compositor,
        which is mandatory under Wayland rather than merely polite.

        macOS is neither: it keeps its frame and gets restyled, in
        `theme`.

        Returns whether it took. It is deliberately possible for this to
        fail and leave a perfectly ordinary window: every part of it
        reaches past Qt into the platform, and a future Windows is allowed
        to disagree.
        """
        if sys.platform.startswith("win"):
            frame = WindowsFrame(self, self.toolbar)
            if not frame.attach():
                return False
            # Windows 11 still draws a thin DWM border around the window,
            # in the *system* light or dark setting rather than ours. On a
            # light-themed machine that is a pale hairline around a
            # near-black program, and it is the one piece of the frame
            # this does not paint itself.
            theme.match_frame(self)
            self.toolbar.show_caption_buttons()
            # Once hit testing claims the strip, the pointer arrives as
            # non-client messages and Qt never sees a click there.
            for button in self.toolbar.caption.values():
                button.deafen()
        else:
            frame = SystemFrame(self, self.toolbar)
            if not frame.attach():
                return False
        self._frame = frame
        return True

    def shutdown(self) -> None:
        """Release the camera. Idempotent, and reached from both the window
        closing and a Ctrl-C, so the device is never left held."""
        if getattr(self, "_shut", False):
            return
        self._shut = True
        # A field mid-stack at quit still becomes its tile: the manifest
        # write is what matters -- the merge can wait for the stitcher's
        # merge-on-demand another day.
        if self.mosaic is not None and self.stack_session is not None:
            try:
                self._seal_tile_stack()
            except Exception:
                pass               # closing must never be blocked
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


def _group(title: str, first: QtWidgets.QWidget | QtWidgets.QLayout,
           hint: str | None = None):
    col = QtWidgets.QVBoxLayout()
    col.setSpacing(7)
    label = QtWidgets.QLabel(title.upper())
    label.setProperty("role", "label")
    if hint:
        # On the header as well as the content: the header is the part
        # someone points at when they are asking what a section is for.
        label.setToolTip(hint)
    col.addWidget(label)
    col.addLayout(first) if isinstance(first, QtWidgets.QLayout) else col.addWidget(first)
    return col



def _list_cameras() -> int:
    """Everything on this machine darlaston could drive, and how well."""
    import sys as _sys

    from ..camera import usb

    linux = _sys.platform.startswith("linux")
    link = usb.probe()
    if link.port or not linux:
        if link.port:
            print(f"ToupTek family: {link.product or 'camera'} on {link.port} "
                  f"(vendor {link.vendor}, link {link.speed_mbps or '?'} Mbps)")
        else:
            # No sysfs to read, so the bus cannot be surveyed. Ask the SDK
            # instead of reporting "none", which off Linux would be a
            # statement about this code rather than about the machine.
            print("ToupTek family: cannot survey the bus on this platform, "
                  "asking the driver directly")
        try:
            from ..camera.toupcam import ToupcamBackend
            cam = ToupcamBackend()
            info = cam.open()
            print(f"    {info.model}  [{info.brand}]  {info.max_bit_depth}-bit"
                  f"  {'colour ' + info.bayer_pattern if info.is_colour else 'mono'}"
                  f"{'  cooled' if info.cooled else ''}")
            print(f"    {len(info.resolutions)} resolutions, largest "
                  f"{info.resolutions[0]}")
            cam.close()
        except Exception as exc:
            print(f"    could not open it: {exc}")
    else:
        print("ToupTek family: none on the bus")

    if not linux:
        print("\nV4L2 / UVC: Linux only. On this platform an ordinary "
              "webcam is not reachable this way.")
        return 0
    from ..camera.v4l2 import enumerate_cameras
    found = enumerate_cameras()
    print(f"\nV4L2 / UVC: {len(found)} capture device"
          f"{'' if len(found) == 1 else 's'}")
    for cam in found:
        w, h = cam["sizes"][0]
        raw = (", raw: " + ", ".join(cam["raw"])) if cam["raw"] else \
            ", no raw (linear DNG only)"
        print(f"    {cam['node']}  {cam['card']}  [{cam['driver']}]  "
              f"{w}x{h}  {'/'.join(cam['formats'])}{raw}")
    if found:
        print("\n    run with --usb to use one")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    # Kept, and kept out of --help. The synthetic camera is how this gets
    # developed and tested without a microscope on the desk, and it is of
    # no use to somebody who owns one: it renders invented diatoms. An
    # option that cannot help you is noise in the manual.
    ap.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--usb", action="store_true",
                    help="use an ordinary V4L2/UVC camera (/dev/video*) "
                         "instead of a ToupTek-family one. No raw: these "
                         "cameras demosaic on the bridge chip and captures "
                         "are written as linear DNGs")
    ap.add_argument("--list-cameras", action="store_true",
                    help="show every camera darlaston can see, and exit")
    args = ap.parse_args()

    if args.list_cameras:
        return _list_cameras()

    if args.mock:
        from ..camera.mock import MockCamera
        make: callable = lambda: MockCamera(fps=30.0)
        allow_synthetic = False
        presence = None                      # synthetic is always there
    elif args.usb:
        if not sys.platform.startswith("linux"):
            # cv2 can open a webcam here through the platform's own
            # framework, but everything that finds one and describes it is
            # V4L2 ioctls against /dev/video. Saying so beats connecting to
            # nothing in silence.
            print("--usb needs V4L2, which is Linux only. A ToupTek-family "
                  "camera works on this platform; an ordinary webcam does "
                  "not, yet.")
            return 2
        def make() -> CameraBackend:
            from ..camera.v4l2 import V4L2Backend
            return V4L2Backend()
        allow_synthetic = False
        from ..camera.v4l2 import enumerate_cameras
        presence = lambda: bool(enumerate_cameras())
    else:
        # No flag. Look at what is actually attached, across every access
        # model, and open the likeliest -- or the one chosen last time.
        # The ToupTek path is no longer the default by assumption; it is
        # one of the things `look()` can find.
        from ..camera.discovery import backend_for, choose, look

        seen = look()
        _kept = Settings.load()
        picked = choose(seen, _kept.camera_choice, _kept.camera_fingerprint)
        if picked is None and seen:
            # Several attached and nobody has said which. Open the
            # likeliest, so there is a picture to look at, and let the
            # menu offer the rest. An empty window and a question, before
            # the application has done anything useful, is a worse
            # greeting than a picture and a way to change it.
            picked = seen[0]

        if picked is not None:
            make: callable = lambda: backend_for(picked)
            presence = lambda: bool(look())
        else:
            # Nothing found at all. The ToupTek path is still the right
            # thing to try: its own errors explain a missing SDK or an
            # absent camera far better than silence would.
            def make() -> CameraBackend:
                from ..camera.toupcam import ToupcamBackend
                return ToupcamBackend()
            from ..camera import usb
            presence = usb.present
        allow_synthetic = False

    # Before anything opens a camera. Measured to be the right setting for
    # the batch jobs too, not just the preview -- see cpu.py.
    apply_thread_budget()

    app = QtWidgets.QApplication(sys.argv)
    theme.install(app)
    theme.load_fonts()
    theme.identify(app)                  # after the fonts: the mark is a letter
    win = MainWindow(make, allow_synthetic=allow_synthetic, presence=presence)
    # Before show(), where the platform allows it. Changing a window flag
    # on a window that is already up destroys the native window and makes
    # a new one, which is a visible flash and, more to the point, tears
    # down and rebuilds the surface the live view draws on. `winId()`
    # creates the handle these need without showing anything.
    #
    # macOS is the exception and has to wait: its NSWindow does not exist
    # until the window is shown, so `[view window]` before that is nil.
    if sys.platform != "darwin" and frame_wanted(win.settings.window_frame):
        # Ours, or the platform's, depending on the desktop and the
        # setting. Falling back is silent and safe: a window with the
        # system's own frame is what every version until now shipped.
        if not win.take_native_frame():
            theme.match_frame(win)
        # The menu was built before the window had a handle to reframe,
        # so the tick catches up here.
        win.chrome_action.setChecked(win._frame is not None)
    elif sys.platform != "darwin":
        theme.match_frame(win)

    win.show()

    if sys.platform == "darwin":
        # macOS keeps its own frame and its own traffic lights; only the
        # bar is restyled, and the toolbar steps aside for the lights.
        if theme.match_frame(win):
            theme.follow_window_controls(win, win.toolbar)
            # Running the content up under the title bar puts our toolbar
            # exactly where AppKit's drag region was, so the window stops
            # being draggable by the one strip everybody grabs first.
            # Handing the drag back is the whole fix.
            drag = TitleDrag(win, win.toolbar, lights=theme.MACOS_LIGHTS)
            if drag.attach():
                win._frame = drag

    # Last, and only if the setting allows it. Nothing above this line
    # touches the network.
    win.watch_for_updates()

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
