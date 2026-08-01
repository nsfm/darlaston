"""Shell chrome: the title bar, the waiting screens, the objective stepper.

These are the parts that exist so the application is usable before, during and
after there is a picture. Everything here is presentation only -- no camera is
opened, no state is owned.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..camera.base import CameraState
from ..camera.session import FRAMERATES
from . import icons, theme


class Dot(QtWidgets.QWidget):
    """Connection state, at a glance and without reading."""

    COLOURS = {
        CameraState.DISCONNECTED: theme.DIM,
        CameraState.CONNECTING: theme.BRASS,
        CameraState.READY: theme.BRASS,
        CameraState.STREAMING: theme.GOOD,
        CameraState.ERROR: theme.BAD,
    }

    def __init__(self) -> None:
        super().__init__()
        self._state = CameraState.DISCONNECTED
        self.setFixedSize(14, 14)

    def set_state(self, state: CameraState) -> None:
        self._state = state
        self.update()

    def paintEvent(self, _e) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        c = QtGui.QColor(self.COLOURS.get(self._state, theme.DIM))
        if self._state in (CameraState.STREAMING, CameraState.ERROR):
            glow = QtGui.QColor(c)
            glow.setAlpha(70)
            p.setBrush(glow)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.drawEllipse(1, 1, 12, 12)
        p.setBrush(c)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(4, 4, 6, 6)


class Chip(QtWidgets.QLabel):
    def __init__(self, text: str = "", active: bool = False) -> None:
        super().__init__(text)
        self.setProperty("role", "sub")
        self._active = active
        self._restyle()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._restyle()

    def _restyle(self) -> None:
        colour = theme.BRASS if self._active else theme.DIM
        self.setStyleSheet(
            f"border:1px solid {colour}; border-radius:3px; margin:1px;"
            f"padding:1px 7px; color:{colour}; font-size:11px;")


class ToolBar(QtWidgets.QFrame):
    """The top bar is for things you can do.

    Status moved out of it deliberately: what the instrument *is* belongs with
    what it is *doing*, at the bottom, and mixing the two meant the bar was
    half identity and half chrome with nowhere for tools to go.
    """

    about = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "bar")
        self.setFixedHeight(36)

        self.wordmark = QtWidgets.QPushButton("darlaston")
        self.wordmark.setProperty("role", "wordmark")
        self.wordmark.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.wordmark.setToolTip("About darlaston")
        self.wordmark.clicked.connect(self.about)

        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        divider.setStyleSheet(f"color: {theme.LINE};")
        divider.setFixedHeight(18)

        self.menus: dict[str, QtWidgets.QMenu] = {}
        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(8, 0, 12, 0)
        self._row.setSpacing(2)
        self._row.addWidget(self.wordmark)
        self._row.addSpacing(4)
        self._row.addWidget(divider)
        self._row.addSpacing(4)
        self._row.addStretch(1)

    def add_menu(self, title: str) -> QtWidgets.QMenu:
        """A popup rather than a plain button, so these can nest as more
        arrives without every name having to change."""
        button = QtWidgets.QPushButton(title)
        button.setProperty("role", "menu")
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        menu = QtWidgets.QMenu(button)
        button.setMenu(menu)
        self.menus[title] = menu
        self._row.insertWidget(self._row.count() - 1, button)
        return menu


class StatusBar(QtWidgets.QFrame):
    """What is connected, to what, and what it is doing.

    Everything here is a fact about the current moment, which is why it all
    sits together rather than being split across the window.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"background: {theme.PANEL}; border-top: 1px solid {theme.LINE};")

        self.dot = Dot()
        self.state = QtWidgets.QLabel("--")
        self.state.setProperty("role", "sub")
        self.context = QtWidgets.QLabel("")
        self.context.setProperty("role", "sub")

        # Preview resolution, as a control rather than a label. It used to
        # report the *capture* resolution, which read as though the live view
        # were 20 MP when it has always been the smallest binned mode -- a
        # label that answers a different question than the one it appears to.
        self.preview = QtWidgets.QComboBox()
        self.preview.setProperty("role", "sub")
        # Size to the longest entry, not to the current one: a combo that
        # fits its selection and then elides it is worse than one that is
        # simply wide enough.
        self.preview.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.preview.setMinimumContentsLength(18)
        self.preview.setToolTip(
            "Live preview resolution. Captures are always full resolution;\n"
            "this only trades preview detail for frame rate, and every\n"
            "per-pixel stage of the live loop scales with it.")
        self.preview.setStyleSheet(
            f"QComboBox {{ border: 1px solid {theme.LINE}; border-radius: 3px;"
            f" margin: 1px; padding: 1px 6px; color: {theme.DIM};"
            f" background: transparent; }}"
            f"QComboBox:hover {{ color: {theme.INK}; }}"
            f"QComboBox::drop-down {{ border: 0; width: 16px; }}"
            f"QComboBox::down-arrow {{ image: url("
            f"{icons.path_for('chevron-down', theme.DIM)}); width: 11px;"
            f" height: 11px; }}")

        # Frame rate, beside resolution: the two knobs that trade preview
        # quality against load, and the pair a person reaches for together.
        self.rate = QtWidgets.QComboBox()
        self.rate.setProperty("role", "sub")
        self.rate.setToolTip(
            "How many frames a second to ask the camera for.\n"
            "Frames we cannot analyse in time are still pulled over USB with\n"
            "the driver holding the interpreter, so asking for fewer beats\n"
            "discarding more. Watch the drop percentage to the right.")
        self.rate.setStyleSheet(self.preview.styleSheet())
        for fps in FRAMERATES:
            self.rate.addItem("uncapped" if fps == 0 else f"{fps} fps", fps)

        self.numbers = QtWidgets.QLabel("")
        self.numbers.setProperty("role", "sub")

        # Room left, always on show. A stacked mosaic writes tens of
        # gigabytes without ever mentioning it -- forty tiles at thirty
        # slices is about 47 GB -- and the first time anybody finds that out
        # should not be a session dying halfway through a slide.
        self.disk = QtWidgets.QLabel("")
        self.disk.setProperty("role", "sub")

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(10, 0, 12, 0)
        row.setSpacing(10)
        row.addWidget(self.dot)
        row.addWidget(self.state)
        row.addWidget(self.context)
        row.addStretch(1)
        row.addWidget(self.preview)
        row.addWidget(self.rate)
        row.addWidget(self.disk)
        row.addWidget(self.numbers)
        self._base = ""
        self._note = ""
        self._res_filled = False

    #: Below this, the readout turns brass. A single stacked tile is
    #: comfortably a gigabyte once its slices are counted, so a warning that
    #: waits for the last few hundred megabytes arrives after the point
    #: where anything could have been done about it.
    DISK_LOW_GB = 20.0
    #: And below this it is red: not enough for one more tile.
    DISK_CRITICAL_GB = 2.0

    def set_disk(self, free_bytes: int | None, root: str = "") -> None:
        """Show the room left where captures are being written."""
        if free_bytes is None:
            self.disk.setText("")
            self.disk.setToolTip("")
            return
        gb = free_bytes / 1e9
        text = f"{gb:.0f} GB free" if gb >= 10 else f"{gb:.1f} GB free"
        colour = ""
        if gb < self.DISK_CRITICAL_GB:
            colour = f"color: {theme.BAD};"
        elif gb < self.DISK_LOW_GB:
            colour = f"color: {theme.BRASS};"
        self.disk.setText(text)
        self.disk.setStyleSheet(colour)
        self.disk.setToolTip(
            f"Room left on the volume holding {root or 'your captures'}.\n"
            "A 40-tile mosaic at 30 slices each is about 47 GB of raw frames."
            if root else "Room left where captures are written.")

    def select_rate(self, fps: int) -> None:
        at = self.rate.findData(int(fps))
        if at >= 0 and at != self.rate.currentIndex():
            self.rate.blockSignals(True)
            self.rate.setCurrentIndex(at)
            self.rate.blockSignals(False)

    def select_resolution(self, index: int) -> None:
        """Reflect the session's actual choice without re-firing the signal."""
        at = self.preview.findData(index)
        if at >= 0 and at != self.preview.currentIndex():
            self.preview.blockSignals(True)
            self.preview.setCurrentIndex(at)
            self.preview.blockSignals(False)

    def set_note(self, note: str) -> None:
        self._note = note
        self.state.setText(note or self._base)
        self.state.setStyleSheet(
            f"color: {theme.BRASS};" if note else "")

    def update_status(self, status, setup=None) -> None:
        self.dot.set_state(status.state)
        bits = [status.message.lower()]
        if status.state is CameraState.ERROR and status.next_retry_in:
            bits.append(f"retrying in {status.next_retry_in:.0f}s")
        self._base = "   ".join(bits)
        if not self._note:
            self.state.setText(self._base)

        info = status.info
        parts: list[str] = []
        if setup is not None:
            parts.append(setup.camera.display)
            parts.append(setup.scope.name)
            obj = setup.scope.turret.objective
            if obj:
                parts.append(obj.label)
            parts.append(setup.illumination.display)
        elif info is not None:
            parts.append(info.model)
        self.context.setText(" · ".join(parts))

        if info is not None and info.resolutions and not self._res_filled:
            self._res_filled = True
            self.preview.blockSignals(True)
            self.preview.clear()
            for r in info.resolutions:
                mp = r.width * r.height / 1e6
                self.preview.addItem(f"{r.width}×{r.height}  {mp:.1f} MP",
                                     r.index)
            self.preview.blockSignals(False)

        tail: list[str] = []
        link = status.link
        if link is not None and link.speed_mbps:
            tail.append(link.label)
            self.numbers.setStyleSheet(
                f"color: {theme.BAD};" if link.is_degraded else "")
        self._tail = tail
        self._render_numbers()

    def set_live(self, signals) -> None:
        st = signals.stats
        total = max(st["delivered"] + st["dropped"], 1)
        self._live = (f"{st['analysed_fps']:.0f} fps   "
                      f"dropped {st['dropped']} ({st['dropped'] / total * 100:.0f}%)")
        self._render_numbers()

    def _render_numbers(self) -> None:
        parts = list(getattr(self, "_tail", [])) + \
            ([getattr(self, "_live")] if hasattr(self, "_live") else [])
        self.numbers.setText("   ".join(parts))


#: Width the explanation column wraps at. Comfortable for prose and
#: narrow enough that a numbered list still scans.
_WRAP_W = 440


def _wraps(label: QtWidgets.QLabel) -> None:
    """Make a word-wrapped label claim the height its text actually needs.

    A wrapping QLabel reports the height of *one long line* unless its
    size policy opts into heightForWidth, so a vertical layout hands it a
    single line's worth of space and the rest is drawn over whatever sits
    beneath. Fixing this by hand is why the first-run page had its own
    explanation written across its own instructions.
    """
    label.setFixedWidth(_WRAP_W)
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    policy.setVerticalPolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding)
    label.setSizePolicy(policy)


class WaitingPage(QtWidgets.QWidget):
    """No camera, connecting, or failed to open.

    Not an error screen. The application runs, watches, and connects -- and it
    names the failure people actually hit, which is another program holding the
    device.
    """

    use_synthetic = QtCore.Signal()
    install_sdk_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(f"background:{theme.SUNK};")

        self.pulse = _Pulse()
        self.heading = QtWidgets.QLabel("Waiting for a camera")
        self.heading.setProperty("role", "heading")
        self.body = QtWidgets.QLabel("")
        self.body.setProperty("role", "body")
        self.body.setWordWrap(True)
        self.body.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        _wraps(self.body)
        self.advice = QtWidgets.QLabel("")
        self.advice.setProperty("role", "advice")
        self.advice.setWordWrap(True)
        self.advice.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        _wraps(self.advice)
        self.advice.hide()

        # What to *do*, numbered, left-aligned. This is the whole reason
        # the page exists: almost nobody launches this from a terminal,
        # so an explanation that only reaches a traceback reaches nobody.
        self.steps = QtWidgets.QLabel("")
        self.steps.setProperty("role", "body")
        self.steps.setWordWrap(True)
        self.steps.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.steps.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        _wraps(self.steps)
        self.steps.hide()

        self.synthetic = QtWidgets.QPushButton("Use the synthetic camera instead")
        self.synthetic.clicked.connect(self.use_synthetic)
        # Shown only for the failure it fixes. A button offering to
        # download a 242 MB vendor archive should not be sitting there
        # while the camera is merely unplugged.
        self.install_sdk = QtWidgets.QPushButton("Install camera SDK…")
        self.install_sdk.clicked.connect(self.install_sdk_requested)
        self.install_sdk.hide()
        self.copy_btn = QtWidgets.QPushButton("Copy this message")
        self.copy_btn.setProperty("role", "seg")
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.hide()

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        buttons.addWidget(self.install_sdk)
        buttons.addWidget(self.synthetic)
        buttons.addWidget(self.copy_btn)
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        row.setLayout(buttons)

        col = QtWidgets.QVBoxLayout()
        col.setSpacing(16)
        col.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        for w in (self.pulse, self.heading, self.body, self.steps,
                  self.advice, row):
            col.addWidget(w, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        holder = QtWidgets.QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setMaximumWidth(440)
        holder.setLayout(col)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(holder, 0, QtCore.Qt.AlignmentFlag.AlignCenter)

    _last = ""

    def _copy(self) -> None:
        """Put the whole explanation on the clipboard.

        Someone asking for help should be able to paste what they saw,
        rather than retyping it or photographing their screen."""
        QtWidgets.QApplication.clipboard().setText(self._last)
        self.copy_btn.setText("Copied")
        QtCore.QTimer.singleShot(
            1500, lambda: self.copy_btn.setText("Copy this message"))

    def update_status(self, status) -> None:
        faulted = status.state is CameraState.ERROR
        self.heading.setProperty("role", "fault" if faulted else "heading")
        self.heading.setText(status.message or "Waiting for a camera")
        self.heading.style().unpolish(self.heading)
        self.heading.style().polish(self.heading)

        # Empty when there is nothing to add. A subtitle that only says the
        # heading again, at greater length, is worse than white space.
        self.body.setText(status.detail or "")
        self.body.setVisible(bool(status.detail))

        steps = getattr(status, "steps", ())
        if steps:
            items = "".join(
                f"<tr><td valign='top' style='padding-right:8px'>{i}.</td>"
                f"<td>{step}</td></tr>"
                for i, step in enumerate(steps, 1))
            self.steps.setText(
                f"<div style='margin-top:6px'><b>What to do</b>"
                f"<table style='margin-top:4px'>{items}</table></div>")
            self.steps.show()
        else:
            self.steps.hide()

        self._last = "\n".join(
            [status.message or "", status.detail or ""]
            + [f"{i}. {s}" for i, s in enumerate(steps, 1)]).strip()
        self.copy_btn.setVisible(bool(faulted and self._last))
        self.install_sdk.setVisible(
            getattr(status, "kind", "") in ("sdk-missing", "sdk-old"))
        link = status.link
        if link is not None and link.advice:
            self.advice.setText(link.advice)
            self.advice.show()
        else:
            self.advice.hide()
        self.pulse.set_running(status.state is not CameraState.ERROR)


class _Pulse(QtWidgets.QWidget):
    """A ring that breathes while the app is watching the bus. It stops on a
    fault, because a fault is not a thing that is still trying."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(52, 52)
        self._t = 0.0
        self._running = True
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def set_running(self, running: bool) -> None:
        self._running = running
        self.update()

    def _tick(self) -> None:
        if self._running:
            self._t = (self._t + 0.016) % 1.0
            self.update()

    def paintEvent(self, _e) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        p.setPen(QtGui.QPen(QtGui.QColor(theme.LINE), 1))
        p.drawEllipse(1, 1, 50, 50)

        if self._running:
            c = QtGui.QColor(theme.BRASS)
            c.setAlphaF(max(0.0, 0.55 * (1.0 - self._t)))
            p.setPen(QtGui.QPen(c, 1))
            grow = self._t * 22
            p.drawEllipse(QtCore.QRectF(1 - grow / 2, 1 - grow / 2,
                                        50 + grow, 50 + grow))

        colour = theme.BRASS if self._running else theme.BAD
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(colour))
        p.drawEllipse(22, 22, 8, 8)


class ObjectiveStepper(QtWidgets.QWidget):
    """Next/previous around the turret, because that is what a turret is.

    Also where an automatic detection would surface as a *suggestion* -- see
    live/turret.py. It proposes; this is where a person disposes.
    """

    changed = QtCore.Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._turret = None

        # Drawn, like the close mark and the combo chevrons. The single
        # angle quotation marks were a near miss at every font size: they
        # sit above the optical centre and are lighter than the hairline
        # they are framed by.
        self.prev = QtWidgets.QPushButton()
        self.next = QtWidgets.QPushButton()
        for b, mark in ((self.prev, "chevron-left"), (self.next, "chevron-right")):
            b.setIcon(icons.hover_icon(mark, theme.DIM, theme.INK, 12))
            b.setIconSize(QtCore.QSize(12, 12))
        for b in (self.prev, self.next):
            b.setProperty("role", "step")
        self.label = QtWidgets.QLabel("--")
        self.label.setProperty("role", "value")
        self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.prev.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(+1))

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.prev)
        row.addWidget(self.label, 1)
        row.addWidget(self.next)

        self.suggestion = None
        self._uncertain = False

    def set_turret(self, turret) -> None:
        self._turret = turret
        self._refresh()

    def set_uncertain(self, uncertain: bool) -> None:
        """Say when the recorded objective may no longer be the real one.

        A rotation was detected and never answered, so the application is
        holding a belief it has reason to doubt. Showing that is the honest
        thing: the objective keys every calibration lookup and appears in
        every file, and a quietly wrong one is worse than an obviously
        unsure one.
        """
        if uncertain == self._uncertain:
            return
        self._uncertain = uncertain
        self._refresh()

    def _step(self, delta: int) -> None:
        if self._turret is None:
            return
        self.changed.emit(self._turret.step(delta))
        self._refresh()

    def _refresh(self) -> None:
        obj = self._turret.objective if self._turret else None
        text = obj.label if obj else "--"
        if obj and self._uncertain:
            text += "  ?"
        self.label.setText(text)
        self.label.setStyleSheet(
            f"color: {theme.BRASS};" if self._uncertain else "")
        self.label.setToolTip(
            "A turret rotation was detected and never answered, so this may "
            "no longer be\nthe objective in the light path. Step it, or "
            "answer the next prompt, to be sure."
            if self._uncertain else "")
