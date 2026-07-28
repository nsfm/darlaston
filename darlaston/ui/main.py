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

from PySide6 import QtCore, QtWidgets

from ..camera.base import CameraBackend, CameraState
from ..camera.session import CameraSession, SessionStatus
from ..calib import (CalibrationService, CalibrationStore, Opportunist,
                     Progress)
from ..calib.store import flat_key
from ..capture import CaptureResult, StillCapture
from ..live.focus import Illumination, Region
from ..live.pipeline import LivePipeline, LiveSignals
from ..session.model import (BUILTIN_ILLUMINATION, Library, Objective,
                             ScopeProfile, Setup, Turret)
from ..session.settings import Settings
from . import theme
from .calib_ui import CalibrationPanel
from .capture_ui import SettingsDialog, ShutterButton, SubjectField
from .setup_ui import SetupDialog
from .shell import Chip, ObjectiveStepper, TitleBar, WaitingPage
from .widgets import CoverageMeter, FocusTraceView, Histogram, LiveView

#: Until the setup editor exists, a plausible stand so the chrome has something
#: real to show. Replaced by the library as soon as a camera is recognised.
_PROVISIONAL_SCOPE = ScopeProfile(
    id="unconfigured", name="Microscope",
    turret=Turret([Objective(10, 0.30), Objective(20, 0.50),
                   Objective(40, 0.75), Objective(100, 1.30, immersion="oil")],
                  current=2),
    optovar=[1.0, 1.25, 1.6, 2.0], optovar_current=0)


class Bridge(QtCore.QObject):
    """Thread hop. Qt's queued connection is the marshalling."""

    signals = QtCore.Signal(object)
    status = QtCore.Signal(object)
    capture_state = QtCore.Signal(str)
    capture_result = QtCore.Signal(object)
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
                                    store=self.store)
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
        self.titlebar = TitleBar()

        self.waiting = WaitingPage()
        self.waiting.use_synthetic.connect(self._switch_to_synthetic)
        self.waiting.synthetic.setVisible(self._allow_synthetic)

        self.view = LiveView()
        self.view.region_drawn.connect(self._on_custom_region)
        self.histogram = Histogram()
        self.trace = FocusTraceView()

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.waiting)
        self.stack.addWidget(self.view)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.stack, 1)
        body.addWidget(self._rail())

        self.strip = _StatusStrip()

        col = QtWidgets.QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.titlebar)
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

        # What is on the slide. Only the operator knows it, and it is the
        # difference between an archive and a folder of numbered files.
        self.subject = SubjectField()
        col.addLayout(_group("subject", self.subject))

        self.calib_panel = CalibrationPanel()
        self.calib_panel.capture_dark.connect(self._do_dark)
        self.calib_panel.build_flat.connect(self._do_flat)
        self.calib_panel.build_lut.connect(self._do_lut)
        col.addLayout(_group("calibration", self.calib_panel))

        col.addLayout(_group("exposure", self.histogram))
        col.addLayout(_group("focus", self.trace))

        # Optics — the two things that change several times a sitting.
        self.objective = ObjectiveStepper()
        self.illumination = QtWidgets.QComboBox()
        for mode in BUILTIN_ILLUMINATION:
            self.illumination.addItem(mode.display, mode)
        self.illumination.currentIndexChanged.connect(self._on_illumination)
        optics = _group("optics", self.objective)
        optics.addWidget(self.illumination)
        col.addLayout(optics)

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

        self.peaking = QtWidgets.QCheckBox("focus peaking")
        self.peaking.toggled.connect(self._on_peaking)
        col.addWidget(self.peaking)

        self.sweep = QtWidgets.QCheckBox("Z sweep")
        self.sweep.setToolTip(
            "Accumulates which parts of the frame have been through focus.\n"
            "Rack past focus in both directions; it reads 100% when every "
            "region with something in it has been passed.")
        self.sweep.toggled.connect(self._on_sweep)
        col.addWidget(self.sweep)
        self.coverage = CoverageMeter()
        col.addWidget(self.coverage)

        col.addStretch(1)

        self.shutter = ShutterButton()
        self.shutter.clicked.connect(self._on_capture)
        self.shutter.set_available(False)
        col.addWidget(self.shutter)

        self.last_capture = QtWidgets.QLabel("")
        self.last_capture.setProperty("role", "key")
        self.last_capture.setWordWrap(True)
        col.addWidget(self.last_capture)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)
        scope = QtWidgets.QPushButton("Microscope…")
        scope.clicked.connect(self._open_setup)
        prefs = QtWidgets.QPushButton("Settings…")
        prefs.clicked.connect(self._open_settings)
        buttons.addWidget(scope)
        buttons.addWidget(prefs)
        col.addLayout(buttons)
        return rail

    # ---- session ---------------------------------------------------------

    @QtCore.Slot(object)
    def _on_status(self, status: SessionStatus) -> None:
        info = status.info
        if info is not None and self.setup is None:
            profile = self.library.remember_camera(info.serial, info.model)
            self.setup = Setup(camera=profile, scope=_PROVISIONAL_SCOPE,
                               illumination=self._illumination)
            self.objective.set_turret(self.setup.scope.turret)

        self.titlebar.update_status(status, self.setup)
        self.strip.set_status(status)

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
        self.calib_panel.set_status(status, self.opportunist.count,
                                    self.opportunist.bank.wanted,
                                    live=self.session.status.is_live
                                    and not self.calibration.busy)

    def _on_capture(self) -> None:
        if not self.capture.trigger(self.setup, subject=self.subject.subject,
                                    slide=self.subject.slide_note):
            return
        self.shutter.set_state("exposing")

    @QtCore.Slot(str)
    def _on_capture_state(self, state: str) -> None:
        self.shutter.set_state(state)
        if state == "idle":
            self.shutter.set_available(self.session.status.is_live)

    @QtCore.Slot(object)
    def _on_capture_result(self, result: CaptureResult) -> None:
        self.last_capture.setText(result.summary)
        colour = theme.DIM if result.ok else theme.BAD
        self.last_capture.setStyleSheet(f"color: {colour};")

    def _open_setup(self) -> None:
        if self.setup is None:
            return
        dialog = SetupDialog(self.setup, self.library, self)
        if dialog.exec():
            self.setup = dialog.result_setup
            self.setup.illumination = self._illumination
            self.objective.set_turret(self.setup.scope.turret)
            self.titlebar.update_status(self.session.status, self.setup)
            # The optical stack changed, so anything banked for the old one is
            # no longer a flat for this one.
            self.opportunist.set_key(flat_key(self.setup,
                                              self.subject.slide_note))
            self._refresh_calibration()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.setup, self)
        dialog.exec()

    def _on_sweep(self, on: bool) -> None:
        if on:
            self.pipeline.start_sweep()
        else:
            self.pipeline.stop_sweep()
            self.coverage.set_value(None)
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
        self.titlebar.update_status(self.session.status, self.setup)

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
        self.coverage.set_value(s.coverage, s.coverage_complete)
        self.view.set_frame(s.preview, s.peaking)
        self.histogram.set_data(s.histogram, s.clipped_fraction,
                                s.black_fraction, s.channel_clipped)
        self.trace.set_data(s.focus_trace, s.focus_fraction_of_peak)
        self.strip.set_live(s)

    def shutdown(self) -> None:
        """Release the camera. Idempotent, and reached from both the window
        closing and a Ctrl-C, so the device is never left held."""
        if getattr(self, "_shut", False):
            return
        self._shut = True
        self.session.stop()
        self.pipeline.stop()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)


# --------------------------------------------------------------------------

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


class _StatusStrip(QtWidgets.QFrame):
    """The numbers that tell you whether to trust what you are seeing."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(26)
        self.setStyleSheet(
            f"background:{theme.PANEL}; border-top:1px solid {theme.LINE};")
        self.left = QtWidgets.QLabel("")
        self.left.setProperty("role", "sub")
        self._base = ""
        self._note = ""
        self.right = QtWidgets.QLabel("")
        self.right.setProperty("role", "sub")
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.addWidget(self.left)
        row.addStretch(1)
        row.addWidget(self.right)

    def set_note(self, note: str) -> None:
        self._note = note
        self.left.setText(note or self._base)

    def set_status(self, status: SessionStatus) -> None:
        bits = [status.message.lower()]
        if status.state is CameraState.ERROR and status.next_retry_in:
            bits.append(f"retrying in {status.next_retry_in:.0f}s")
        self._base = "   ".join(bits)
        self.left.setText(getattr(self, "_note", "") or self._base)

    def set_live(self, s: LiveSignals) -> None:
        st = s.stats
        total = max(st["delivered"] + st["dropped"], 1)
        self.right.setText(
            f"{st['analysed_fps']:.1f} fps    "
            f"dropped {st['dropped']} ({st['dropped'] / total * 100:.0f}%)    "
            f"seq {s.seq}")


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
