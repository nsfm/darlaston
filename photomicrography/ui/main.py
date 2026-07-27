"""Main window.

The only place that knows about both the pipeline and Qt. The pipeline emits
plain dataclasses from its analysis thread; a QObject signal marshals them to
the main thread, and nothing below this module has ever heard of Qt.

    python -m photomicrography.ui.main            # synthetic camera
    python -m photomicrography.ui.main --toupcam  # real hardware
"""
from __future__ import annotations

import argparse
import sys

from PySide6 import QtCore, QtWidgets

from ..camera.base import CameraBackend
from ..live.focus import Illumination
from ..live.pipeline import LivePipeline, LiveSignals
from .widgets import FocusTraceView, Histogram, LiveView

STYLE = """
QWidget { background: #141312; color: #e8e6e3;
          font-family: 'Inter', 'Segoe UI', sans-serif; font-size: 12px; }
QGroupBox { border: 1px solid #2a2826; border-radius: 4px;
            margin-top: 14px; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px;
                   color: #8a8681; font-size: 10px;
                   text-transform: uppercase; letter-spacing: 1px; }
QLabel#stat { color: #8a8681; font-family: monospace; font-size: 11px; }
QSlider::groove:horizontal { height: 3px; background: #2a2826; }
QSlider::handle:horizontal { background: #e8e6e3; width: 11px;
                             margin: -5px 0; border-radius: 5px; }
QComboBox, QCheckBox { padding: 3px; }
QComboBox { border: 1px solid #2a2826; border-radius: 3px; }
"""


class SignalBridge(QtCore.QObject):
    """Thread hop. Qt's queued connection is the marshalling, so the pipeline
    never touches a widget -- ARCHITECTURE.md 3.6."""

    arrived = QtCore.Signal(object)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, camera: CameraBackend) -> None:
        super().__init__()
        self.camera = camera
        info = camera.open()
        self.setWindowTitle(f"photomicrography — {info.model}")
        self.resize(1280, 820)

        self.bridge = SignalBridge()
        self.bridge.arrived.connect(self._on_signals)
        self.pipeline = LivePipeline(self.bridge.arrived.emit,
                                     illumination=Illumination.BRIGHTFIELD)

        self.view = LiveView()
        self.histogram = Histogram()
        self.trace = FocusTraceView()
        self._build(info)

        self.pipeline.start()
        camera.set_resolution(2)
        camera.start_stream(self.pipeline.submit)

    # ---- layout ----------------------------------------------------------

    def _build(self, info) -> None:
        side = QtWidgets.QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(self._boxed("exposure", self.histogram))
        side.addWidget(self._boxed("focus", self.trace))
        side.addWidget(self._controls(info))
        side.addStretch(1)
        self.stats = QtWidgets.QLabel(objectName="stat")
        self.stats.setWordWrap(True)
        side.addWidget(self.stats)

        panel = QtWidgets.QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(300)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(10)
        row.addWidget(self.view, 1)
        row.addWidget(panel)

        central = QtWidgets.QWidget()
        central.setLayout(row)
        self.setCentralWidget(central)
        self.setStyleSheet(STYLE)

    @staticmethod
    def _boxed(title: str, widget: QtWidgets.QWidget) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title)
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(widget)
        return box

    def _controls(self, info) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("capture")
        form = QtWidgets.QFormLayout(box)
        form.setContentsMargins(8, 8, 8, 8)

        self.illum = QtWidgets.QComboBox()
        self.illum.addItems([i.value for i in Illumination])
        self.illum.currentTextChanged.connect(
            lambda t: self.pipeline.set_illumination(Illumination(t)))
        form.addRow("illumination", self.illum)

        lo, hi = info.exposure_range_us
        self.exposure = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.exposure.setRange(1, 1000)
        self.exposure.setValue(120)
        self.exposure_label = QtWidgets.QLabel("8.3 ms", objectName="stat")
        self.exposure.valueChanged.connect(self._set_exposure)
        form.addRow("exposure", self.exposure)
        form.addRow("", self.exposure_label)

        self.gain = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.gain.setRange(info.gain_range_pct[0], min(info.gain_range_pct[1], 2000))
        self.gain.setValue(100)
        self.gain_label = QtWidgets.QLabel("1.0x", objectName="stat")
        self.gain.valueChanged.connect(self._set_gain)
        form.addRow("gain", self.gain)
        form.addRow("", self.gain_label)

        self.peaking = QtWidgets.QCheckBox("focus peaking")
        self.peaking.toggled.connect(self._toggle_peaking)
        form.addRow(self.peaking)
        return box

    # ---- control handlers ------------------------------------------------

    def _set_exposure(self, value: int) -> None:
        # Logarithmic: the useful range spans 0.3 ms to several seconds and a
        # linear slider would spend most of its travel somewhere useless.
        us = int(300 * (10 ** (value / 250.0)))
        self.camera.set_exposure(us)
        self.exposure_label.setText(
            f"{us / 1000:.1f} ms" if us < 1_000_000 else f"{us / 1e6:.2f} s")

    def _set_gain(self, value: int) -> None:
        self.camera.set_gain(value)
        self.gain_label.setText(f"{value / 100:.1f}x")

    def _toggle_peaking(self, on: bool) -> None:
        self.pipeline.set_peaking(on)
        if not on:
            self.view.clear_peaking()

    # ---- the one slot that touches widgets -------------------------------

    @QtCore.Slot(object)
    def _on_signals(self, s: LiveSignals) -> None:
        self.view.set_frame(s.preview, s.peaking)
        self.histogram.set_data(s.histogram, s.clipped_fraction, s.black_fraction)
        self.trace.set_data(s.focus_trace, s.focus_fraction_of_peak)
        st = s.stats
        drop_rate = (st["dropped"] / max(st["delivered"] + st["dropped"], 1)) * 100
        self.stats.setText(
            f"{st['analysed_fps']:5.1f} fps analysed\n"
            f"{st['dropped']:>6} dropped ({drop_rate:.0f}%)\n"
            f"seq {s.seq}   conf {s.xy_confidence:.3f}")

    def closeEvent(self, event) -> None:
        self.camera.stop_stream()
        self.pipeline.stop()
        self.camera.close()
        super().closeEvent(event)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--toupcam", action="store_true",
                    help="use real hardware instead of the synthetic camera")
    args = ap.parse_args()

    if args.toupcam:
        from ..camera.toupcam import ToupcamBackend
        camera: CameraBackend = ToupcamBackend()
    else:
        from ..camera.mock import MockCamera
        camera = MockCamera(fps=30.0)

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(camera)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
