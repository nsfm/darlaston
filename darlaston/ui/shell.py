"""Shell chrome: the title bar, the waiting screens, the objective stepper.

These are the parts that exist so the application is usable before, during and
after there is a picture. Everything here is presentation only -- no camera is
opened, no state is owned.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..camera.base import CameraState
from ..camera.session import FRAMERATES
from ..i18n import _
from . import icons, theme
from .framed import FramedDialog


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
        with QtGui.QPainter(self) as p:
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


class CaptionButton(QtWidgets.QWidget):
    """Minimise, maximise or close, drawn rather than set in a font.

    Windows' own are glyphs from Segoe MDL2 Assets, which is present on
    every Windows and on nothing else, so a build that used it would look
    right on the target and be untestable anywhere. These are three
    strokes each.

    They work two ways, because the platforms deliver the pointer
    differently. On a frameless Qt window they are ordinary widgets and
    handle their own mouse. On Windows, once hit testing claims their
    area, the pointer arrives as non-client messages and Qt never sees a
    click there at all -- so `deafen` turns the mouse handling off and
    `set_state` becomes how they learn they are being hovered or pressed.
    See frame.WindowsFrame._button_input.
    """

    pressed = QtCore.Signal(str)

    #: Windows' own caption buttons are 46 x 32 at 100%. Matching the
    #: width matters more than the height: it is the target size people
    #: have muscle memory for, including the flick into the top-right
    #: corner that closes a maximised window.
    WIDTH = 46

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.hot = False
        self.down = False
        self.setFixedWidth(self.WIDTH)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)

    def deafen(self) -> None:
        """Stop taking the mouse, because something else is delivering it."""
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def enterEvent(self, _event) -> None:
        self.set_state(True, self.down)

    def leaveEvent(self, _event) -> None:
        self.set_state(False, False)

    def mousePressEvent(self, event) -> None:
        # Left only, and anything else goes back where it came from. A
        # swallowed right-click here is a window menu that works
        # everywhere along the bar except over three buttons.
        if event.button() is not QtCore.Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.set_state(True, True)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() is not QtCore.Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        fired = self.down
        inside = self.rect().contains(event.position().toPoint())
        self.set_state(inside, False)
        if fired and inside:
            self.pressed.emit(self.kind)

    def set_state(self, hot: bool, down: bool) -> None:
        if (hot, down) != (self.hot, self.down):
            self.hot, self.down = hot, down
            self.update()

    def paintEvent(self, _event) -> None:
        with QtGui.QPainter(self) as p:
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            if self.hot or self.down:
                # Red for close, as every Windows application does. Getting
                # this wrong is more jarring than having no highlight at all.
                if self.kind == "close":
                    wash = QtGui.QColor(0xC4, 0x2B, 0x1C, 255 if self.down else 220)
                else:
                    wash = QtGui.QColor(255, 255, 255, 38 if self.down else 24)
                p.fillRect(self.rect(), wash)
            ink = (QtGui.QColor(theme.INK) if self.kind != "close" or not
                   (self.hot or self.down) else QtGui.QColor("#ffffff"))
            p.setPen(QtGui.QPen(ink, 1.1))
            cx, cy, arm = self.width() / 2, self.height() / 2, 5.0
            if self.kind == "minimise":
                p.drawLine(QtCore.QPointF(cx - arm, cy), QtCore.QPointF(cx + arm, cy))
            elif self.kind == "maximise":
                p.drawRect(QtCore.QRectF(cx - arm, cy - arm, arm * 2, arm * 2))
            else:
                p.drawLine(QtCore.QPointF(cx - arm, cy - arm),
                           QtCore.QPointF(cx + arm, cy + arm))
                p.drawLine(QtCore.QPointF(cx + arm, cy - arm),
                           QtCore.QPointF(cx - arm, cy + arm))


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

        self.wordmark = QtWidgets.QPushButton(_("shell.wordmark.label"))
        self.wordmark.setProperty("role", "wordmark")
        self.wordmark.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.wordmark.setToolTip(_("shell.wordmark.tooltip"))
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
        # Menus go in here, before the stretch. Counted rather than
        # inserted at `count - 1`: that meant "just before the last item",
        # which was the stretch until the caption strip was added after it
        # and quietly started putting the menus on the far right.
        self._menu_slot = self._row.count()
        self._row.addStretch(1)

        # Only ever shown on Windows, and only once the native frame has
        # been taken. Built here regardless so the geometry the frame asks
        # for is the geometry that is really on screen.
        self.caption = {kind: CaptionButton(kind)
                        for kind in ("minimise", "maximise", "close")}
        # In their own strip with no spacing and no margin. The row has
        # both, and either one leaves a dead pixel column between two
        # buttons or a gap between the last button and the corner -- and
        # the corner is where people flick to close a maximised window
        # without aiming.
        self._spare_margin: int | None = None
        self._geometry = None
        self._caption_strip = QtWidgets.QWidget()
        self._caption_strip.setProperty("role", "caption")
        strip = QtWidgets.QHBoxLayout(self._caption_strip)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(0)
        for kind in ("minimise", "maximise", "close"):
            strip.addWidget(self.caption[kind])
        self._caption_strip.hide()
        self._row.addWidget(self._caption_strip)

    def show_caption_buttons(self) -> None:
        """Draw our own minimise, maximise and close.

        Only after the frame has actually been taken: showing them beside
        a native caption would be six buttons for three jobs.
        """
        left, top, right, bottom = self._row.getContentsMargins()
        # Remembered on the way past, not recomputed on the way back: a
        # second call would otherwise record the zero this one sets, and
        # the margin would be gone for good.
        if self._spare_margin is None:
            self._spare_margin = right
        self._row.setContentsMargins(left, top, 0, bottom)
        self._caption_strip.show()
        self.forget_geometry()

    def hide_caption_buttons(self) -> None:
        """Give the corner back, for when the platform draws them again."""
        left, top, _right, bottom = self._row.getContentsMargins()
        self._row.setContentsMargins(left, top, self._spare_margin or 0,
                                     bottom)
        self._caption_strip.hide()
        self.forget_geometry()

    def set_caption_state(self, hot: str, down: str) -> None:
        """Told from outside, because these get no mouse events."""
        for kind, button in self.caption.items():
            button.set_state(kind == hot, kind == down)

    def caption_geometry(self):
        """Where the buttons are, and what else in the bar takes clicks.

        Both in the bar's own coordinates, which are the window's, since
        the bar sits at its top left. Returned rather than assumed so the
        hit testing and the layout cannot disagree about where anything
        is: the frame asks, the layout answers.

        Cached, because the callers are hit testing -- once per mouse
        move over the window on Windows, and on Linux from an
        application-wide event filter, which is every mouse move
        anywhere. Twenty microseconds of tree walk is not much and it is
        also not something a program showing a live camera preview should
        spend on every pointer twitch. `forget_geometry` is called from
        the three things that can move any of it.
        """
        if self._geometry is None:
            self._geometry = self._measure()
        return self._geometry

    def forget_geometry(self) -> None:
        """Something moved. Measure again when next asked."""
        self._geometry = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.forget_geometry()

    def event(self, event) -> bool:
        # A LayoutRequest is Qt saying a child's size hint changed, which
        # is how a longer translated menu label reaches this without
        # anything having to remember to say so.
        if event.type() == QtCore.QEvent.Type.LayoutRequest:
            self.forget_geometry()
        return super().event(event)

    def _measure(self):
        # Mapped into the bar's own coordinates rather than read off
        # `x()`, which is relative to whatever the button was parented
        # into. The strip is a nesting level deep.
        # All three, always, in the order `hit_region` expects. A button
        # that is not on screen contributes a zero-width span, which can
        # never match a coordinate -- rather than being dropped, which
        # would shift the ones after it onto the wrong names. And
        # `isVisibleTo`, not `isHidden`: hiding the strip does not mark
        # its children hidden, so `isHidden` said the buttons were still
        # there after the frame had given them back to the platform.
        buttons = tuple(
            (b.mapTo(self, QtCore.QPoint(0, 0)).x(),
             b.width() if b.isVisibleTo(self) else 0)
            for b in (self.caption["minimise"], self.caption["maximise"],
                      self.caption["close"]))
        # Everything from the wordmark to the end of the last menu is
        # clickable, so the drag must not claim it. From the wordmark and
        # not from zero: the layout's own left margin is not a control,
        # and reserving it made the first few pixels of the bar the one
        # part of it a window cannot be dragged by.
        left = self.wordmark.x()
        rightmost = left + self.wordmark.width()
        for menu_button in self.findChildren(QtWidgets.QPushButton):
            if menu_button.property("role") == "menu":
                rightmost = max(rightmost,
                                menu_button.x() + menu_button.width())
        return buttons, ((left, rightmost - left),)

    def inset_for_window_controls(self, pixels: int) -> None:
        """Start the toolbar to the right of the platform's own buttons.

        Only reached on macOS, and only once the title bar has actually
        been made transparent. The traffic lights then float over this
        bar rather than sitting in a strip above it, so without this the
        wordmark is underneath them.

        Applied here rather than at construction because it depends on
        whether the restyle worked, which is not known until the window
        has a native handle.
        """
        left, top, right, bottom = self._row.getContentsMargins()
        self._row.setContentsMargins(pixels, top, right, bottom)
        self.forget_geometry()

    def add_menu(self, name: str, title: str) -> QtWidgets.QMenu:
        """A popup rather than a plain button, so these can nest as more
        arrives without every name having to change.

        `name` identifies the menu and `title` is what it says. They used
        to be one string, which made the registry key change with the
        language -- so `menus["Setup"]` would have found nothing the
        moment anything was translated.
        """
        button = QtWidgets.QPushButton(title)
        button.setProperty("role", "menu")
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        menu = QtWidgets.QMenu(button)
        button.setMenu(menu)
        self.menus[name] = menu
        self._row.insertWidget(self._menu_slot, button)
        self._menu_slot += 1
        return menu


class StatusBar(QtWidgets.QFrame):
    """What is connected, to what, and what it is doing.

    Everything here is a fact about the current moment, which is why it all
    sits together rather than being split across the window.
    """

    #: Emitted once, when the camera has told us what modes it offers.
    #: The window then decides which to open in, which it cannot do any
    #: earlier because the list is the camera's answer rather than ours.
    resolutions_ready = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(28)
        self.setStyleSheet(
            f"background: {theme.PANEL}; border-top: 1px solid {theme.LINE};")

        self.dot = Dot()
        self.state = QtWidgets.QLabel(_("shell.state.idle"))
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
        self.preview.setToolTip(_("shell.preview.tooltip"))
        self.preview.setStyleSheet(
            f"QComboBox {{ border: 1px solid {theme.LINE}; border-radius: 3px;"
            f" margin: 1px; padding: 1px 6px; color: {theme.DIM};"
            f" background: transparent; }}"
            f"QComboBox:hover {{ color: {theme.INK}; }}"
            f"QComboBox::drop-down {{ border: 0; width: 16px; }}"
            f"QComboBox::down-arrow {{ image: url("
            f"{icons.path_for('chevron-down', theme.INK)}); width: 11px;"
            f" height: 11px; }}")

        # Frame rate, beside resolution: the two knobs that trade preview
        # quality against load, and the pair a person reaches for together.
        self.rate = QtWidgets.QComboBox()
        self.rate.setProperty("role", "sub")
        self.rate.setToolTip(_("shell.rate.tooltip"))
        self.rate.setStyleSheet(self.preview.styleSheet())
        for fps in FRAMERATES:
            self.rate.addItem(_("shell.rate.uncapped") if fps == 0
                              else _("shell.rate.fps", fps=fps), fps)

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
        # Shown only when white balance is *off*. A checkable menu entry
        # is fine when you can see its effect -- the framing guides are
        # visible on the picture -- but this one changes every file
        # written and is discoverable nowhere else. Off is the unusual
        # state, so the chip appears only then.
        self.wb_off = Chip(_("shell.wb_off.label"), active=True)
        self.wb_off.setToolTip(_("shell.wb_off.tooltip"))
        self.wb_off.hide()

        row.addWidget(self.dot)
        row.addWidget(self.state)
        row.addWidget(self.context)
        row.addWidget(self.wb_off)
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
        # One decimal only when it is running out, where the difference
        # between 1.2 and 1.8 GB is the difference between one more tile
        # and none.
        text = (_("shell.disk.free", gb=f"{gb:.0f}") if gb >= 10
                else _("shell.disk.free", gb=f"{gb:.1f}"))
        colour = ""
        if gb < self.DISK_CRITICAL_GB:
            colour = f"color: {theme.BAD};"
        elif gb < self.DISK_LOW_GB:
            colour = f"color: {theme.BRASS};"
        self.disk.setText(text)
        self.disk.setStyleSheet(colour)
        self.disk.setToolTip(_("shell.disk.tooltip", root=root) if root
                             else _("shell.disk.tooltip.unknown"))

    def set_white_balance(self, on: bool) -> None:
        """Say so when files are going out unbalanced."""
        self.wb_off.setVisible(not on)

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
            bits.append(_("shell.state.retrying",
                              seconds=f"{status.next_retry_in:.0f}"))
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
                self.preview.addItem(
                    _("shell.preview.resolution", w=r.width, h=r.height,
                      mp=f"{mp:.1f}"),
                                     r.index)
            self.preview.blockSignals(False)
            self.resolutions_ready.emit(list(info.resolutions))

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
        self._live = _("shell.numbers.live",
                       fps=f"{st['analysed_fps']:.0f}", dropped=st["dropped"],
                       percent=f"{st['dropped'] / total * 100:.0f}")
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


class TroubleDialog(FramedDialog):
    """Why a camera might not be showing up, when somebody asks.

    The advice is read from `NoCameraFound` rather than written again
    here. That error is the one place this project says what to try when
    nothing answers, and a second copy in a dialog is a second copy to
    keep in step -- which, in practice, means one of them going stale and
    nobody knowing which.
    """

    def __init__(self, link=None, parent=None) -> None:
        super().__init__(parent, width=430)
        self.setWindowTitle(_("shell.trouble.title"))

        from ..camera.errors import NoCameraFound

        problem = NoCameraFound(_("shell.trouble.what"))

        heading = QtWidgets.QLabel(_("shell.trouble.title"))
        heading.setProperty("role", "heading")

        body = QtWidgets.QLabel(problem.detail)
        body.setWordWrap(True)
        body.setProperty("role", "body")

        items = "".join(
            f"<tr><td valign='top' style='padding-right:8px'>{i}.</td>"
            f"<td style='padding-bottom:6px'>{step}</td></tr>"
            for i, step in enumerate(problem.steps, 1))
        steps = QtWidgets.QLabel(f"<table>{items}</table>")
        steps.setWordWrap(True)
        steps.setTextFormat(QtCore.Qt.TextFormat.RichText)
        steps.setProperty("role", "body")

        col = self.content
        col.addWidget(heading)
        col.addWidget(body)
        col.addSpacing(4)
        col.addWidget(steps)

        # Only when there is a camera on the bus and it came up slow --
        # which is a different problem from not finding one at all, and
        # the one people spend longest blaming the camera for.
        if link is not None and getattr(link, "advice", None):
            slow = QtWidgets.QLabel(link.advice)
            slow.setWordWrap(True)
            slow.setProperty("role", "key")
            col.addSpacing(6)
            col.addWidget(slow)

        close = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.reject)
        col.addSpacing(12)
        col.addWidget(close)
        self.finish()


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
        self.heading = QtWidgets.QLabel(_("shell.waiting.heading"))
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

        self.synthetic = QtWidgets.QPushButton(_("shell.waiting.synthetic"))
        self.synthetic.clicked.connect(self.use_synthetic)
        # Shown only for the failure it fixes. A button offering to
        # download a 242 MB vendor archive should not be sitting there
        # while the camera is merely unplugged.
        self.install_sdk = QtWidgets.QPushButton(_("shell.waiting.install"))
        self.install_sdk.clicked.connect(self.install_sdk_requested)
        self.install_sdk.hide()
        # Asked for, not offered. The advice used to arrive as a fault
        # -- red heading, numbered steps -- for the ordinary state of
        # having nothing plugged in yet, which is how macOS came to show
        # an error screen where Linux showed a calm one. Behind a
        # question, the calm screen stays calm and the advice is still
        # a click away for the person who needs it.
        self.trouble = QtWidgets.QPushButton(_("shell.waiting.trouble"))
        # Styled like the button beside it, deliberately. Both are quiet
        # secondary things offered while nothing is wrong, and giving the
        # question its own look would make it the loudest thing on a
        # screen whose whole job is to be calm.
        self.trouble.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.trouble.clicked.connect(self._troubleshoot)

        self.copy_btn = QtWidgets.QPushButton(_("shell.waiting.copy"))
        self.copy_btn.setProperty("role", "seg")
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.hide()

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        buttons.addWidget(self.install_sdk)
        buttons.addWidget(self.synthetic)
        buttons.addWidget(self.trouble)
        buttons.addWidget(self.copy_btn)
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        row.setLayout(buttons)

        col = QtWidgets.QVBoxLayout()
        col.setSpacing(16)
        col.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        for w in (self.pulse, self.heading, self.body, self.steps,
                  self.advice):
            col.addWidget(w, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

        holder = QtWidgets.QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setMaximumWidth(_WRAP_W)
        holder.setLayout(col)

        # The buttons sit *outside* the prose column, because they are wider
        # than it: with no SDK installed there are three side by side
        # needing 535 px against the 440 the text is wrapped to, and they
        # were clipped at both ends. Widening the column instead fixed the
        # buttons and broke the instructions -- the layout then handed the
        # steps label 159 px of the 180 its text needs, and quietly dropped
        # the last line. So the text keeps the width it was written for and
        # only the buttons get more.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(16)
        outer.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(holder, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(row, 0, QtCore.Qt.AlignmentFlag.AlignCenter)

    _last = ""

    def _copy(self) -> None:
        """Put the whole explanation on the clipboard.

        Someone asking for help should be able to paste what they saw,
        rather than retyping it or photographing their screen."""
        QtWidgets.QApplication.clipboard().setText(self._last)
        self.copy_btn.setText(_("shell.waiting.copied"))
        QtCore.QTimer.singleShot(
            1500, lambda: self.copy_btn.setText(_("shell.waiting.copy")))

    def _troubleshoot(self) -> None:
        TroubleDialog(self._link, self).exec()

    #: The last link report, so the dialog can add the cable advice when
    #: there is a camera on the bus and it came up slow.
    _link = None

    def update_status(self, status) -> None:
        faulted = status.state is CameraState.ERROR
        self.heading.setProperty("role", "fault" if faulted else "heading")
        self.heading.setText(status.message or _("shell.waiting.heading"))
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
                f"<div style='margin-top:6px'>"
                f"<b>{_('shell.waiting.what_to_do')}</b>"
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
        self._link = link
        # Only while waiting. On a fault the steps are already on screen,
        # and a button offering to explain what is written above it is
        # a button that makes the screen look less trustworthy.
        self.trouble.setVisible(not faulted and not steps)


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
        with QtGui.QPainter(self) as p:
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
            b.setIcon(icons.hover_icon(mark, theme.INK, theme.BRASS, 12))
            b.setIconSize(QtCore.QSize(12, 12))
        for b in (self.prev, self.next):
            b.setProperty("role", "step")
        self.label = QtWidgets.QLabel(_("objective.none"))
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
        positions = getattr(self._turret, "positions", None) or []
        hint = ""
        if obj:
            text = obj.label + (_("objective.uncertain")
                                if self._uncertain else "")
        elif positions and not any(positions):
            # A turret nobody has described yet, which is not the same thing
            # as being parked on an empty detent of one that was -- and "--"
            # said both. A new stand now starts empty rather than being given
            # four invented objectives, so this is the first thing a stranger
            # sees here and it should say what to do about it.
            text = _("objective.unconfigured")
            hint = _("objective.unconfigured.tooltip")
        else:
            text = _("objective.none")
        self.label.setText(text)
        self.label.setStyleSheet(
            f"color: {theme.BRASS};" if self._uncertain else "")
        self.label.setToolTip(
            _("objective.uncertain.tooltip") if self._uncertain else hint)
