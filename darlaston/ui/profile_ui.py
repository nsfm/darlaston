"""Measuring a camera, once, with somebody watching.

Two stages that want opposite fields, which is the whole reason this is
a guided sequence rather than a button. Geometry needs detail to match
on; response needs a blank field so the reading is photometry rather
than whatever is in frame. Asking for both at once would get one of them
wrong, silently.

Nothing here decides anything. It explains what each stage needs, runs
it when told, and shows what came back so a person can see whether it
looks like their camera.
"""
from __future__ import annotations

import threading

from PySide6 import QtCore, QtWidgets

from ..camera.profiling import sweep_geometry, sweep_response
from ..i18n import _
from . import theme
from .framed import FramedDialog


class ProfileDialog(FramedDialog):
    """Walks the two stages, and reports rather than asserts."""

    progressed = QtCore.Signal(str)
    finished_stage = QtCore.Signal(str, object)

    def __init__(self, session, profile, parent=None) -> None:
        super().__init__(parent, width=560)
        self.setWindowTitle(_("profile.title"))
        self._session = session
        self.profile = profile
        self.geometry_result = None
        self.response_result = None
        self._busy = False

        heading = QtWidgets.QLabel(_("profile.heading"))
        heading.setProperty("role", "heading")
        body = QtWidgets.QLabel(_("profile.body"))
        body.setWordWrap(True)
        body.setProperty("role", "body")

        self.steps = QtWidgets.QLabel(_("profile.stage.geometry"))
        self.steps.setWordWrap(True)
        self.steps.setProperty("role", "body")

        self.note = QtWidgets.QLabel("")
        self.note.setWordWrap(True)
        self.note.setProperty("role", "key")

        self.run = QtWidgets.QPushButton(_("profile.action.geometry"))
        self.run.setProperty("role", "primary")
        self.run.clicked.connect(self._next)

        close = QtWidgets.QPushButton(_("profile.action.close"))
        close.clicked.connect(self.accept)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(close)
        row.addStretch(1)
        row.addWidget(self.run)

        col = self.content
        col.addWidget(heading)
        col.addWidget(body)
        col.addSpacing(10)
        col.addWidget(self.steps)
        col.addWidget(self.note)
        col.addSpacing(14)
        col.addLayout(row)
        self.finish()

        self.progressed.connect(self.note.setText)
        self.finished_stage.connect(self._landed)
        self._stage = "geometry"

    # -- running ----------------------------------------------------------

    def _next(self) -> None:
        if self._busy:
            return
        backend = self._session.backend
        if backend is None or not hasattr(backend, "snapshot"):
            self.note.setText(_("profile.unavailable"))
            return
        self._busy = True
        self.run.setEnabled(False)
        stage = self._stage
        threading.Thread(target=self._work, args=(backend, stage),
                         daemon=True, name="camera-profile").start()

    def _work(self, backend, stage: str) -> None:
        """On a thread: this takes tens of seconds and moves the camera.

        A mode change costs about a second on this hardware and the
        response sweep is dozens of settling waits, so doing it on the
        interface thread would look exactly like a hang.
        """
        try:
            streaming = getattr(backend, "_running", None)
            was = bool(streaming and streaming.is_set())
            if was:
                backend.stop_stream()
            try:
                if stage == "geometry":
                    got = sweep_geometry(
                        backend, lambda t: self.progressed.emit(
                            _("profile.measuring", what=t)))
                else:
                    got = sweep_response(
                        backend, progress=lambda t: self.progressed.emit(
                            _("profile.measuring", what=t)))
            finally:
                if was:
                    backend.start_stream(backend._on_frame)
        except Exception as why:
            self.finished_stage.emit(stage, why)
            return
        self.finished_stage.emit(stage, got)

    def _landed(self, stage: str, got) -> None:
        self._busy = False
        self.run.setEnabled(True)
        if isinstance(got, Exception):
            self.note.setText(_("profile.failed", why=str(got)))
            return

        if stage == "geometry":
            self.geometry_result = got
            trusted = [g for g in got if g.trusted]
            if not trusted:
                # The failure that matters, and the one a blank field
                # causes: nothing to match on, so nothing was learned.
                self.note.setText(_("profile.geometry.nothing"))
                return
            self.note.setText(_("profile.geometry.done", n=len(trusted),
                                total=len(got)))
            self._stage = "response"
            self.steps.setText(_("profile.stage.response"))
            self.run.setText(_("profile.action.response"))
        else:
            self.response_result = got
            usable = got.usable
            if len(usable) < 3:
                self.note.setText(_("profile.response.nothing"))
                return
            self.note.setText(_("profile.response.done",
                                low=usable[0][0], high=usable[-1][0],
                                n=len(usable), total=len(got.points)))
            self.run.setEnabled(False)
