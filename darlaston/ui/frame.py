"""A window frame of our own.

Windows draws a caption bar above the window: an icon, the application's
name, and three buttons. On a program that already has a toolbar that is a
second bar saying less, and it is drawn in Windows' dark rather than ours.

macOS had a middle path -- make the title bar transparent and keep the
native traffic lights. Windows has no equivalent. Either you accept the
caption or you take the client area and draw the buttons yourself, and
taking it means owning six things:

  1. `WM_NCCALCSIZE`, to reclaim the caption strip while keeping the
     resize borders.
  2. `DwmExtendFrameIntoClientArea`, so the drop shadow and composition
     survive.
  3. `WM_NCHITTEST`, which is where dragging, double-click to maximise,
     the right-click system menu and Aero Snap all come from.
  4. Caption button *input*, which arrives as non-client messages once
     hit testing claims those areas, so Qt never sees a click there.
  5. Maximised padding: Windows grows the window rect by the resize
     border on every side when maximised, and content near the edge goes
     off screen. An auto-hiding taskbar needs a further pixel on its own
     edge, or it can no longer see the pointer arrive and stops
     revealing itself.
  6. Per-monitor DPI, since the border thickness changes with it and
     changes again when the window is dragged to another screen.

**The geometry is separated from the API on purpose.** `hit_region` below
is a pure function of rectangles, so the piece most likely to be wrong is
testable on any platform, and only the thin layer that talks to Win32
needs a Windows machine to exercise.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache

# ---- what the geometry can decide ------------------------------------------

CLIENT = "client"
CAPTION = "caption"
MINIMISE, MAXIMISE, CLOSE = "minimise", "maximise", "close"
TOP, BOTTOM, LEFT, RIGHT = "top", "bottom", "left", "right"
TOPLEFT, TOPRIGHT = "topleft", "topright"
BOTTOMLEFT, BOTTOMRIGHT = "bottomleft", "bottomright"

#: Regions that resize, and which cursor edge each is.
EDGES = {TOP, BOTTOM, LEFT, RIGHT, TOPLEFT, TOPRIGHT, BOTTOMLEFT, BOTTOMRIGHT}
#: Regions that are one of our own caption buttons.
BUTTONS = {MINIMISE, MAXIMISE, CLOSE}


@dataclass(frozen=True)
class Frame:
    """Everything the geometry needs, in client pixels.

    `buttons` are the three caption buttons left to right, as
    (x, width) pairs; their height is the bar. Passed in rather than
    computed because the toolbar owns its own layout and this must not
    have a second opinion about where the buttons ended up.
    """

    width: int
    height: int
    bar: int                       # height of our toolbar, the drag strip
    border: int                    # resize border thickness, DPI scaled
    buttons: tuple[tuple[int, int], ...] = ()
    maximised: bool = False
    #: Regions of the bar that are *not* draggable, because something
    #: clickable lives there: the wordmark and the menu buttons.
    reserved: tuple[tuple[int, int], ...] = ()


def hit_region(x: int, y: int, frame: Frame) -> str:
    """Which part of the window a client-relative point falls in.

    The order of these tests is the specification, not an implementation
    detail:

    * Resize edges win over everything, including the caption buttons.
      A window whose corner cannot be grabbed because a button is near it
      is a window people describe as broken.
    * A maximised window has no resize edges at all. Windows will happily
      let you drag one and the result is a window that un-maximises to
      somewhere surprising.
    * Buttons beat the caption, or the drag would swallow the click.
    * Anything the toolbar has claimed beats the caption too, or the
      menus stop opening and start moving the window.
    """
    if not frame.maximised:
        near_left, near_right = x < frame.border, x >= frame.width - frame.border
        near_top, near_bottom = y < frame.border, y >= frame.height - frame.border
        if near_top and near_left:
            return TOPLEFT
        if near_top and near_right:
            return TOPRIGHT
        if near_bottom and near_left:
            return BOTTOMLEFT
        if near_bottom and near_right:
            return BOTTOMRIGHT
        if near_top:
            return TOP
        if near_bottom:
            return BOTTOM
        if near_left:
            return LEFT
        if near_right:
            return RIGHT

    if 0 <= y < frame.bar:
        for name, (bx, bw) in zip((MINIMISE, MAXIMISE, CLOSE), frame.buttons):
            if bx <= x < bx + bw:
                return name
        for rx, rw in frame.reserved:
            if rx <= x < rx + rw:
                return CLIENT
        return CAPTION
    return CLIENT


def maximised_insets(border: int, autohide: frozenset = frozenset()
                     ) -> tuple[int, int, int, int]:
    """How far to pull the client area in when maximised, per edge.

    Two separate reasons, and they add up on an edge that has both:

    **The frame.** Windows grows the window rect by the resize border on
    every side when it maximises, expecting a frame to absorb the
    overhang. With no frame it runs off the screen, taking the top of the
    toolbar with it.

    **An auto-hiding taskbar.** It reveals itself when the pointer
    reaches its screen edge, and it cannot see the pointer through a
    window that covers that edge. One pixel is enough to leave it: the
    taskbar's own detection strip is thinner than that, and a single
    line of desktop at one edge of a maximised window is not something
    anybody notices. Nothing at all, and the taskbar simply stops
    working while this program is open, which reads as Windows being
    broken rather than us.
    """
    left = top = right = bottom = border
    if LEFT in autohide:
        left += 1
    if TOP in autohide:
        top += 1
    if RIGHT in autohide:
        right += 1
    if BOTTOM in autohide:
        bottom += 1
    return left, top, right, bottom


# ---- the Win32 half --------------------------------------------------------

#: Hit-test results. HTMAXBUTTON is the one that matters most: Windows 11
#: shows its snap-layouts flyout when the pointer rests on a region that
#: reports it, and a custom frame that does not is one people quietly find
#: worse than the standard one.
_HT = {
    CLIENT: 1, CAPTION: 2, MINIMISE: 8, MAXIMISE: 9, CLOSE: 20,
    LEFT: 10, RIGHT: 11, TOP: 12, TOPLEFT: 13, TOPRIGHT: 14,
    BOTTOM: 15, BOTTOMLEFT: 16, BOTTOMRIGHT: 17,
}

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCMOUSELEAVE = 0x02A2
WM_DPICHANGED = 0x02E0

#: DWMWA_WINDOW_CORNER_PREFERENCE, and DWMWCP_ROUND. Windows 11 rounds a
#: standard frame for you; strip the frame and the rounding goes with it
#: unless it is asked for back.
_DWMWA_CORNER = 33
_DWMWCP_ROUND = 2

#: SHAppBarMessage. ABM_GETAUTOHIDEBAREX asks which window, if any, is the
#: auto-hiding appbar on one edge of one monitor -- the "EX" form is the
#: one that takes a monitor, which matters on a second screen that has no
#: taskbar of its own.
ABM_GETSTATE = 0x00000004
ABM_GETAUTOHIDEBAREX = 0x0000000B
ABS_AUTOHIDE = 0x00000001
#: ABE_LEFT, TOP, RIGHT, BOTTOM, in the order Windows numbers them.
_ABE = (LEFT, TOP, RIGHT, BOTTOM)

SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92


def border_thickness(dpi: int = 96) -> int:
    """Resize border width at a given DPI, in pixels.

    `SM_CXSIZEFRAME` alone is the visible frame and is too thin to grab;
    Windows itself adds `SM_CXPADDEDBORDER`, which is why the grabbable
    edge of a standard window is wider than it looks. Falls back to the
    96-DPI figures where the per-DPI call is unavailable, which is
    Windows 8.1 and earlier.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if hasattr(user32, "GetSystemMetricsForDpi"):
            return (user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi)
                    + user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi))
        return (user32.GetSystemMetrics(SM_CXSIZEFRAME)
                + user32.GetSystemMetrics(SM_CXPADDEDBORDER))
    except Exception:
        return max(4, round(8 * dpi / 96))


def supported() -> bool:
    return sys.platform.startswith("win")


def desktop_draws_it_well() -> bool:
    """Is the desktop's own decoration better left alone?

    Two cases, and they are opposite in character:

      * A tiling window manager draws nothing on purpose. Adding a title
        bar there is arguing with the entire point of it.
      * KDE draws a real decoration in the user's own colour scheme, and
        a KDE user generally wants their applications to look like KDE
        applications.

    Everything else gets ours, which in practice means GNOME under
    Wayland -- where Qt's fallback matches neither GNOME nor us -- and
    the long tail of desktops with no strong opinion.

    Read from the environment because there is no portable way to ask.
    XDG_CURRENT_DESKTOP is set by every desktop session; a tiling window
    manager usually sets nothing at all, which is itself the signal.
    """
    import os

    if sys.platform.startswith("win") or sys.platform == "darwin":
        return False
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if any(name in desktop for name in ("KDE", "PLASMA", "LXQT")):
        return True
    # Sway sets XDG_CURRENT_DESKTOP=sway; i3 usually sets nothing, and
    # neither draws anything we would be improving on.
    if any(name in desktop for name in ("SWAY", "I3", "HYPRLAND", "RIVER")):
        return True
    if not desktop and not os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return True                  # bare window manager, no session
    return False


def wanted(preference: str) -> bool:
    """Should this program draw its own frame?"""
    if preference == "system":
        return False
    if preference == "ours":
        return True
    return not desktop_draws_it_well()


# ---- talking to Windows ----------------------------------------------------

@lru_cache(maxsize=1)
def _structs():
    """The Win32 types this needs, built lazily and once.

    Lazily because ctypes.wintypes does not import on anything but
    Windows, and this module is read on every platform for the geometry
    above.

    Once because ctypes gives every call a *distinct* class: a RECT built
    here is not the RECT built by the next call, and casting a pointer
    between the two is a type error rather than the no-op it looks like.
    Caching makes the identity stable as well as saving the work.
    """
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", RECT * 3), ("lppos", ctypes.c_void_p)]

    class MARGINS(ctypes.Structure):
        _fields_ = [("cxLeftWidth", ctypes.c_int),
                    ("cxRightWidth", ctypes.c_int),
                    ("cyTopHeight", ctypes.c_int),
                    ("cyBottomHeight", ctypes.c_int)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class APPBARDATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uCallbackMessage", wintypes.UINT),
                    ("uEdge", wintypes.UINT), ("rc", RECT),
                    ("lParam", ctypes.c_ssize_t)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    return (ctypes, wintypes, RECT, NCCALCSIZE_PARAMS, MARGINS, POINT,
            APPBARDATA, MONITORINFO)


class WindowsFrame:
    """Owns the window's non-client area.

    Constructed with the window and the toolbar that becomes its title
    bar. `attach` returns whether it took: everything here is best effort,
    and a window that could not be reframed should look like Windows drew
    it rather than like a bug.
    """

    def __init__(self, window, toolbar) -> None:
        self.window = window
        self.toolbar = toolbar
        self.hwnd = 0
        self._hot = ""          # region the pointer is over
        self._down = ""         # region the button went down on

    # -- what the geometry needs to know about right now ------------------

    def frame(self) -> Frame:
        ratio = self.window.devicePixelRatioF() or 1.0
        buttons, reserved = self.toolbar.caption_geometry()
        return Frame(width=self.window.width(), height=self.window.height(),
                     bar=self.toolbar.height(),
                     border=max(1, round(border_thickness(round(96 * ratio))
                                         / ratio)),
                     buttons=buttons, reserved=reserved,
                     maximised=self.window.isMaximized())

    # -- setting it up -----------------------------------------------------

    def attach(self) -> bool:
        if not supported():
            return False
        try:
            ctypes, _w, _R, _NC, MARGINS, _P, _A, _M = _structs()
            self.hwnd = int(self.window.winId())
            if not self.hwnd:
                return False

            # Windows 11 rounds a standard frame for you. Strip the frame
            # and the rounding goes with it, so ask for it back. Fails
            # harmlessly on Windows 10, which has square corners anyway.
            corner = ctypes.c_int(_DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(self.hwnd), ctypes.c_uint(_DWMWA_CORNER),
                ctypes.byref(corner), ctypes.sizeof(corner))

            # One pixel of frame, which is what keeps the drop shadow and
            # the DWM composition. Zero margins lose the shadow and the
            # window reads as flat against whatever is behind it.
            margins = MARGINS(0, 0, 0, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                ctypes.c_void_p(self.hwnd), ctypes.byref(margins))

            # Make Windows recompute the non-client area now, rather than
            # at the next resize, or the caption stays until something
            # moves the window.
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0002, 0x0001, 0x0004
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(self.hwnd), None, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
            return True
        except Exception:
            self.hwnd = 0
            return False

    def detach(self) -> bool:
        """Give the non-client area back to Windows.

        `handle` refuses everything once the handle is cleared, so the
        default window procedure resumes deciding the frame; the call
        below is only to make it recompute now rather than at the next
        resize.
        """
        if not self.hwnd:
            return False
        try:
            ctypes, _w, _R, _NC, MARGINS, _P, _A, _M = _structs()
            hwnd, self.hwnd = self.hwnd, 0
            margins = MARGINS(0, 0, 0, 0)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                ctypes.c_void_p(hwnd), ctypes.byref(margins))
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0002, 0x0001, 0x0004
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        except Exception:
            pass
        self.toolbar.hide_caption_buttons()
        return True

    # -- the messages ------------------------------------------------------

    def handle(self, message: int, wparam: int, lparam: int):
        """Returns (handled, result), or (False, 0) to let Qt have it."""
        if not self.hwnd:
            return False, 0
        if message == WM_NCCALCSIZE:
            return self._calc_size(wparam, lparam)
        if message == WM_NCHITTEST:
            return self._hit_test(lparam)
        if message in (WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP,
                       WM_NCMOUSELEAVE):
            return self._button_input(message, wparam)
        return False, 0

    def _autohide_edges(self) -> frozenset:
        """Which edges of this window's monitor carry an auto-hiding bar.

        Asked per monitor, not globally: a second screen usually has no
        taskbar at all, and pulling a pixel off a window there for one
        that lives on the first screen would be a stripe of desktop
        nobody could explain.
        """
        try:
            ctypes, _w, _R, _NC, _M, _P, APPBARDATA, MONITORINFO = _structs()
            shell32 = ctypes.windll.shell32
            if not (shell32.SHAppBarMessage(ABM_GETSTATE, None)
                    & ABS_AUTOHIDE):
                return frozenset()          # nothing auto-hides anywhere

            MONITOR_DEFAULTTONEAREST = 2
            monitor = ctypes.windll.user32.MonitorFromWindow(
                ctypes.c_void_p(self.hwnd), MONITOR_DEFAULTTONEAREST)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if not ctypes.windll.user32.GetMonitorInfoW(
                    ctypes.c_void_p(monitor), ctypes.byref(info)):
                return frozenset()

            found = set()
            for index, edge in enumerate(_ABE):
                data = APPBARDATA()
                data.cbSize = ctypes.sizeof(APPBARDATA)
                data.uEdge = index
                data.rc = info.rcMonitor
                if shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX,
                                           ctypes.byref(data)):
                    found.add(edge)
            return frozenset(found)
        except Exception:
            return frozenset()

    def _calc_size(self, wparam: int, lparam: int):
        """Reclaim the caption strip, and only the caption strip.

        Returning zero with no adjustment makes the client area the whole
        window, which is what puts our toolbar where the caption was. The
        resize borders then live *inside* the client and are handled by
        `hit_region`, which is why they have to be tested there.

        Maximised is the exception, for two reasons that add up. Windows
        grows the window rect by the resize border on every side when it
        maximises, expecting the frame to absorb it, and with no frame
        that overhang runs off the screen and takes the top of the
        toolbar with it. Separately, an auto-hiding taskbar needs a pixel
        left at its own edge or it can no longer see the pointer arrive.
        `maximised_insets` works out both; it is a pure function, so the
        arithmetic is tested without needing Windows.
        """
        if not wparam:
            return False, 0
        try:
            ctypes, _w, _R, NCCALCSIZE_PARAMS = _structs()[:4]
            if self.window.isMaximized():
                params = ctypes.cast(
                    lparam, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
                left, top, right, bottom = maximised_insets(
                    border_thickness(self.window.logicalDpiX()),
                    self._autohide_edges())
                params.rgrc[0].left += left
                params.rgrc[0].top += top
                params.rgrc[0].right -= right
                params.rgrc[0].bottom -= bottom
            return True, 0
        except Exception:
            return False, 0

    def _hit_test(self, lparam: int):
        try:
            ctypes, _w, _R, _NC, _M, POINT, _A, _Mi = _structs()
            # lParam packs two signed 16-bit screen coordinates.
            x = ctypes.c_short(lparam & 0xFFFF).value
            y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            point = POINT(x, y)
            ctypes.windll.user32.ScreenToClient(ctypes.c_void_p(self.hwnd),
                                                ctypes.byref(point))
            ratio = self.window.devicePixelRatioF() or 1.0
            region = hit_region(round(point.x / ratio), round(point.y / ratio),
                                self.frame())
            return True, _HT[region]
        except Exception:
            return False, 0

    def _button_input(self, message: int, wparam: int):
        """Drive the caption buttons from non-client messages.

        Once hit testing claims those areas, Qt never sees a mouse event
        there: Windows sends WM_NC* instead, and a widget that looks like
        a button but never lights up is worse than no button. So hover and
        press are driven from here, and the toolbar is told what to draw.
        """
        by_code = {v: k for k, v in _HT.items()}
        region = by_code.get(wparam, "")
        try:
            if message == WM_NCMOUSELEAVE:
                self._hot = ""
                self.toolbar.set_caption_state("", "")
                return False, 0                  # Windows still wants it
            if message == WM_NCMOUSEMOVE:
                if region != self._hot:
                    self._hot = region if region in BUTTONS else ""
                    self.toolbar.set_caption_state(self._hot, self._down)
                return False, 0
            if message == WM_NCLBUTTONDOWN and region in BUTTONS:
                self._down = region
                self.toolbar.set_caption_state(self._hot, self._down)
                return True, 0                   # ours; do not let DefWindowProc
            if message == WM_NCLBUTTONUP and region in BUTTONS:
                fired, self._down = self._down, ""
                self.toolbar.set_caption_state(self._hot, "")
                if fired == region:
                    self._activate(region)
                return True, 0
        except Exception:
            return False, 0
        return False, 0

    def _activate(self, region: str) -> None:
        if region == MINIMISE:
            self.window.showMinimized()
        elif region == MAXIMISE:
            (self.window.showNormal() if self.window.isMaximized()
             else self.window.showMaximized())
        elif region == CLOSE:
            self.window.close()


#: Region to the Qt edge flags `startSystemResize` wants. Built lazily so
#: this module still imports where PySide6's enums differ.
def _qt_edges():
    from PySide6 import QtCore

    E = QtCore.Qt.Edge
    return {
        TOP: E.TopEdge, BOTTOM: E.BottomEdge,
        LEFT: E.LeftEdge, RIGHT: E.RightEdge,
        TOPLEFT: E.TopEdge | E.LeftEdge, TOPRIGHT: E.TopEdge | E.RightEdge,
        BOTTOMLEFT: E.BottomEdge | E.LeftEdge,
        BOTTOMRIGHT: E.BottomEdge | E.RightEdge,
    }


class _LazyEdges(dict):
    def __missing__(self, key):
        self.update(_qt_edges())
        return self[key]


_EDGES_TO_QT = _LazyEdges()


# ---- everywhere else -------------------------------------------------------

class SystemFrame:
    """Our own chrome on a plain frameless window.

    Used where the desktop's own decoration is worse than ours or absent:
    GNOME under Wayland refuses server-side decorations as policy, so Qt
    draws a fallback that matches neither GNOME nor this program, and a
    tiling window manager draws nothing at all and never wanted to.

    **Dragging and resizing are handed back to the compositor**, through
    `startSystemMove` and `startSystemResize`, rather than moved by
    setting geometry. That is not a preference:

      * On Wayland a client cannot position itself at all. A frameless
        window that moved by setting its own geometry would simply not
        move.
      * The compositor is where snapping, edge tiling, keyboard resize
        and touch gestures live. Doing it by hand means reimplementing
        each one badly and losing the rest.

    So this is much smaller than the Windows implementation, and shares
    the part that matters: `hit_region` decides, exactly as it does there.
    """

    #: Cursor per resize edge.
    _CURSORS = {
        TOP: "SizeVerCursor", BOTTOM: "SizeVerCursor",
        LEFT: "SizeHorCursor", RIGHT: "SizeHorCursor",
        TOPLEFT: "SizeFDiagCursor", BOTTOMRIGHT: "SizeFDiagCursor",
        TOPRIGHT: "SizeBDiagCursor", BOTTOMLEFT: "SizeBDiagCursor",
    }

    #: Grab margin. Wider than it looks pleasant, because a frameless
    #: window has no visible frame to aim at and a four-pixel target is
    #: one people miss and then complain the window cannot be resized.
    BORDER = 6

    def __init__(self, window, toolbar) -> None:
        self.window = window
        self.toolbar = toolbar
        self._filter = None

    def frame(self) -> Frame:
        buttons, reserved = self.toolbar.caption_geometry()
        return Frame(width=self.window.width(), height=self.window.height(),
                     bar=self.toolbar.height(), border=self.BORDER,
                     buttons=buttons, reserved=reserved,
                     maximised=self.window.isMaximized())

    def attach(self) -> bool:
        from PySide6 import QtCore, QtGui, QtWidgets

        window = self.window
        try:
            was_visible = window.isVisible()
            window.setWindowFlag(
                QtCore.Qt.WindowType.FramelessWindowHint, True)
            self.toolbar.show_caption_buttons()
            for kind, button in self.toolbar.caption.items():
                button.pressed.connect(self._activate)
            window.setMouseTracking(True)

            outer = self

            class _Edges(QtCore.QObject):
                """Watches for the pointer at the window's edges.

                On the application rather than the window: the central
                widget covers the whole window, so events reach children
                first and a filter on the window alone would never see the
                press that starts a resize.
                """

                def eventFilter(self, watched, event):
                    return outer._filter_event(event)

            self._filter = _Edges()
            QtWidgets.QApplication.instance().installEventFilter(self._filter)
            if was_visible:
                window.show()          # flags only take on the next show
            return True
        except Exception:
            return False

    # -- the pointer -------------------------------------------------------

    def _at(self, event) -> str:
        from PySide6 import QtCore

        try:
            local = self.window.mapFromGlobal(
                event.globalPosition().toPoint())
        except AttributeError:
            return CLIENT
        if not self.window.rect().contains(local):
            return CLIENT
        return hit_region(local.x(), local.y(), self.frame())

    def _filter_event(self, event) -> bool:
        from PySide6 import QtCore, QtGui

        kind = event.type()
        if kind not in (QtCore.QEvent.Type.MouseButtonPress,
                        QtCore.QEvent.Type.MouseMove,
                        QtCore.QEvent.Type.MouseButtonDblClick):
            return False
        if not self.window.isVisible():
            return False
        region = self._at(event)

        if kind == QtCore.QEvent.Type.MouseMove:
            # Cursor only. The move is never consumed, or every widget
            # under the pointer would stop seeing it.
            name = self._CURSORS.get(region)
            if name:
                self.window.setCursor(getattr(QtCore.Qt.CursorShape, name))
            else:
                self.window.unsetCursor()
            return False

        handle = self.window.windowHandle()
        if handle is None:
            return False
        if kind == QtCore.QEvent.Type.MouseButtonDblClick and region == CAPTION:
            (self.window.showNormal() if self.window.isMaximized()
             else self.window.showMaximized())
            return True
        if kind != QtCore.QEvent.Type.MouseButtonPress:
            return False
        if event.button() is not QtCore.Qt.MouseButton.LeftButton:
            return False
        if region in EDGES:
            handle.startSystemResize(_EDGES_TO_QT[region])
            return True
        if region == CAPTION:
            handle.startSystemMove()
            return True
        return False

    def _activate(self, kind: str) -> None:
        if kind == MINIMISE:
            self.window.showMinimized()
        elif kind == MAXIMISE:
            (self.window.showNormal() if self.window.isMaximized()
             else self.window.showMaximized())
        else:
            self.window.close()

    def detach(self) -> bool:
        """Hand the frame back to the window manager.

        The reverse of `attach`, in reverse order, and it has to be
        complete: a left-over event filter would keep claiming presses at
        the edges of a window that now has a real border there, and a
        left-over override cursor would be a resize arrow that never goes
        away.
        """
        from PySide6 import QtCore, QtWidgets

        app = QtWidgets.QApplication.instance()
        if self._filter is not None and app is not None:
            app.removeEventFilter(self._filter)
            self._filter = None
        self.window.unsetCursor()
        for button in self.toolbar.caption.values():
            try:
                button.pressed.disconnect(self._activate)
            except (RuntimeError, TypeError):
                pass                   # never connected, or already gone
        self.toolbar.hide_caption_buttons()

        window = self.window
        was_visible = window.isVisible()
        # Geometry does not survive the flag change on every window
        # manager, so put it back by hand. Maximised is carried
        # separately: restoring a maximised window's geometry un-maximises
        # it, which is a state change nobody asked for.
        maximised, geometry = window.isMaximized(), window.geometry()
        window.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, False)
        if was_visible:
            window.show()
            if maximised:
                window.showMaximized()
            else:
                window.setGeometry(geometry)
        return True
