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
from types import SimpleNamespace

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


#: Desktop left showing at an edge that carries an auto-hiding taskbar,
#: in physical pixels, so it can still see the pointer arrive. Chromium's
#: shipped `kAutoHideTaskbarThicknessPx`.
AUTOHIDE_GAP = 2


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
    window that covers that edge. Leave nothing and the taskbar simply
    stops working while this program is open, which reads as Windows
    being broken rather than us.

    `AUTOHIDE_GAP` rather than one pixel. The threshold is undocumented;
    Chromium ships two for this exact job on a great many machines, and
    being one pixel short is precisely the failure this exists to
    prevent. Two lines of desktop along one edge of a maximised window
    is still not something anybody notices.
    """
    left = top = right = bottom = border
    if LEFT in autohide:
        left += AUTOHIDE_GAP
    if TOP in autohide:
        top += AUTOHIDE_GAP
    if RIGHT in autohide:
        right += AUTOHIDE_GAP
    if BOTTOM in autohide:
        bottom += AUTOHIDE_GAP
    return left, top, right, bottom


def unpack_point(lparam: int) -> tuple[int, int]:
    """The two coordinates packed into a mouse message's lParam.

    Signed, and that is the whole reason this exists. The documentation
    is explicit that both "represent signed values because they can take
    negative values on systems with multiple monitors" and that the
    LOWORD and HIWORD macros must not be used. A monitor placed left of
    or above the primary one gives negative screen coordinates, and read
    unsigned they come back as roughly 65000 -- so the window behaves
    normally on one screen and reports the pointer somewhere impossible
    on the other.
    """
    import ctypes

    return (ctypes.c_short(lparam & 0xFFFF).value,
            ctypes.c_short((lparam >> 16) & 0xFFFF).value)


def inset_rect(rect: tuple[int, int, int, int],
               insets: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Pull a (left, top, right, bottom) rectangle in by four edge insets.

    Four lines, separated out because they are the four lines that decide
    whether a maximised window fits its screen, and a sign error in them
    is a window that shrinks by twice the border every time it maximises.
    Pure, so the arithmetic is tested without Windows.
    """
    left, top, right, bottom = rect
    dl, dt, dr, db = insets
    return left + dl, top + dt, right - dr, bottom - db


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

#: The same table read backwards, to turn the wParam of a non-client mouse
#: message into a region. Built once, and asserted to be a true inverse in
#: the tests: a duplicated value in `_HT` would silently misroute a button
#: rather than fail.
_REGION_OF = {code: name for name, code in _HT.items()}

WM_ACTIVATE = 0x0006
WM_SETTINGCHANGE = 0x001A
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_MOUSEMOVE = 0x0200
WM_LBUTTONUP = 0x0202
WM_CAPTURECHANGED = 0x0215
WM_NCMOUSELEAVE = 0x02A2
WM_DPICHANGED = 0x02E0
WM_DISPLAYCHANGE = 0x007E

#: TrackMouseEvent flags. TME_NONCLIENT is what makes it report leaving
#: the *non-client* area, which is the only area we are told about.
TME_LEAVE = 0x00000002
TME_NONCLIENT = 0x00000010
HOVER_DEFAULT = 0xFFFFFFFF

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
SM_CXPADDEDBORDER = 92
#: SM_CYSIZEFRAME, the vertical one, is deliberately absent. It is equal
#: to SM_CXSIZEFRAME in every stock Windows theme, `hit_region` carries a
#: single border for both axes, and a constant defined and never read is
#: a promise the code does not keep -- it reads as though the two axes
#: are handled separately when they are not. If a theme ever separates
#: them, `Frame.border` is what has to become two numbers first.


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


#: Set this to anything to leave the frame exactly as the platform drew
#: it. The last way out for someone whose window will not come up, and it
#: has to reach the whole takeover rather than only the restyling: it used
#: to be read in `theme.match_frame` alone, so on Windows -- the one
#: platform where the takeover is total and a tester would most need a
#: normal window back -- setting it did nothing at all.
NATIVE_FRAME_ENV = "DARLASTON_NATIVE_FRAME"


def wanted(preference: str) -> bool:
    """Should this program draw its own frame?"""
    import os

    if os.environ.get(NATIVE_FRAME_ENV):
        return False
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

    class TRACKMOUSEEVENT(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("hwndTrack", wintypes.HWND),
                    ("dwHoverTime", wintypes.DWORD)]

    return SimpleNamespace(
        ctypes=ctypes, wintypes=wintypes, RECT=RECT,
        NCCALCSIZE_PARAMS=NCCALCSIZE_PARAMS, MARGINS=MARGINS, POINT=POINT,
        APPBARDATA=APPBARDATA, MONITORINFO=MONITORINFO,
        TRACKMOUSEEVENT=TRACKMOUSEEVENT)


@lru_cache(maxsize=1)
def _win32():
    """user32 and shell32, with every signature we use declared.

    ctypes defaults an undeclared `restype` to `c_int`. Every handle and
    pointer-width value below is 64 bits, so left undeclared they are
    truncated to a signed 32-bit int and sign-extended back -- which
    happens to survive for the small values these calls return in
    practice, and is exactly the kind of thing that works on the machine
    it was written on.
    """
    import ctypes
    from ctypes import wintypes

    win = _structs()
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32

    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromRect.restype = ctypes.c_void_p
    user32.MonitorFromRect.argtypes = [ctypes.POINTER(win.RECT),
                                       wintypes.DWORD]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(win.MONITORINFO)]
    user32.ScreenToClient.restype = wintypes.BOOL
    user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(win.POINT)]
    user32.SetCapture.restype = wintypes.HWND
    user32.SetCapture.argtypes = [wintypes.HWND]
    user32.ReleaseCapture.restype = wintypes.BOOL
    user32.ReleaseCapture.argtypes = []
    user32.TrackMouseEvent.restype = wintypes.BOOL
    user32.TrackMouseEvent.argtypes = [ctypes.POINTER(win.TRACKMOUSEEVENT)]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    # UINT_PTR, per the documentation. Truncating this one loses nothing
    # today because the states are single bits, but it is the same class
    # of latent wrongness as the handles.
    #
    # These argtypes only match the call sites because `_structs` is
    # cached: ctypes gives every call a distinct class, so an APPBARDATA
    # built from a second `_structs()` would not be *this* APPBARDATA and
    # `byref` of it raises ArgumentError -- swallowed, silently, by the
    # `except Exception` around the caller.
    shell32.SHAppBarMessage.restype = ctypes.c_size_t
    shell32.SHAppBarMessage.argtypes = [wintypes.DWORD,
                                        ctypes.POINTER(win.APPBARDATA)]

    # Windows 8.1 and earlier have neither. Declared only where present so
    # the absence stays a plain AttributeError rather than a bad call.
    if hasattr(user32, "GetDpiForWindow"):
        user32.GetDpiForWindow.restype = wintypes.UINT
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    if hasattr(user32, "GetSystemMetricsForDpi"):
        user32.GetSystemMetricsForDpi.restype = ctypes.c_int
        user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
    return user32, shell32


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
        self._tracking = False  # TrackMouseEvent armed for this entry
        self._edges = None      # cached auto-hiding taskbar edges
        self._monitor = None    # which monitor those edges were asked of
        self._extended = False  # the DWM margin has been applied once up
        self._border = None     # cached resize border, physical pixels

    # -- what the geometry needs to know about right now ------------------

    def dpi(self) -> int:
        """This window's real DPI.

        Not `logicalDpiX`. Qt normalises logical DPI to the base 96 and
        puts the scale in `devicePixelRatio`, so on a 150% display it
        reports 96 and `GetSystemMetricsForDpi` hands back the 96-DPI
        border -- about 8 physical pixels where the true overhang is 12.
        A maximised window then hangs off every edge of the screen by the
        shortfall, cutting the top off the toolbar. Plenty of Windows
        laptops ship at 125% or 150%, so that is close to a default
        configuration.

        `GetDpiForWindow` answers from Windows and needs no correction.
        The fallback reconstructs it from the ratio, which is where Qt
        put the same information.
        """
        try:
            user32, _shell32 = _win32()
            dpi = int(user32.GetDpiForWindow(self.hwnd))
            if dpi:
                return dpi
        except Exception:
            pass
        return max(96, round(96 * (self.window.devicePixelRatioF() or 1.0)))

    def border(self) -> int:
        """Resize border, in *physical* pixels.

        Which is what `WM_NCCALCSIZE` deals in. `frame()` converts it for
        `hit_region`, which works in Qt's logical pixels. One formula, two
        units, and the conversion said out loud -- there used to be two
        formulas in this class that disagreed on scaled displays.

        Cached, because `frame()` is on the hit-test path and that runs
        for every mouse move over the window: uncached this was three
        syscalls per move in a program showing a live preview. Forgotten
        on WM_DPICHANGED, which is the only thing that changes it.
        """
        if self._border is None:
            self._border = border_thickness(self.dpi())
        return self._border

    def maximised(self) -> bool:
        """Ask Windows, not Qt.

        `WM_NCCALCSIZE` is sent *during* the SetWindowPos that maximises
        the window: WS_MAXIMIZE is already set, but Qt derives its own
        window state from WM_SIZE, which arrives afterwards. So Qt still
        says "not maximised" for the recalc that most needs the inset,
        and the window comes up maximised with its toolbar clipped until
        something forces another recalc.
        """
        try:
            user32, _shell32 = _win32()
            return bool(user32.IsZoomed(self.hwnd))
        except Exception:
            return bool(self.window.isMaximized())

    def frame(self) -> Frame:
        ratio = self.window.devicePixelRatioF() or 1.0
        buttons, reserved = self.toolbar.caption_geometry()
        return Frame(width=self.window.width(), height=self.window.height(),
                     bar=self.toolbar.height(),
                     border=max(1, round(self.border() / ratio)),
                     buttons=buttons, reserved=reserved,
                     maximised=self.maximised())

    # -- setting it up -----------------------------------------------------

    def attach(self) -> bool:
        if not supported():
            return False
        try:
            win = _structs()
            ctypes = win.ctypes
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

            self._extend_frame()

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

    def _extend_frame(self) -> bool:
        """One pixel of frame, which is what keeps the DWM composition and
        -- by repute -- the drop shadow.

        Applied at attach and again on the first WM_ACTIVATE. Microsoft's
        own custom-frame sample says why: "Note that the frame extension
        is done within the WM_ACTIVATE message rather than the WM_CREATE
        message. This ensures that frame extension is handled properly
        when the window is at its default size and when it is maximized."
        Since the frame is now taken before the window is shown, attach is
        earlier still than the case they warn about. The call is
        idempotent, so doing both costs nothing and removes the question.

        The HRESULT is checked, because whether this call does anything at
        all is an open question -- see docs/frame-bench.md -- and an
        unchecked return is how it stays open.
        """
        if not self.hwnd:
            return False
        try:
            win = _structs()
            ctypes = win.ctypes
            margins = win.MARGINS(0, 0, 0, 1)
            ok = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                ctypes.c_void_p(self.hwnd), ctypes.byref(margins))
            return ok == 0                   # S_OK
        except Exception:
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
            win = _structs()
            ctypes = win.ctypes
            hwnd, self.hwnd = self.hwnd, 0
            margins = win.MARGINS(0, 0, 0, 0)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                ctypes.c_void_p(hwnd), ctypes.byref(margins))
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0002, 0x0001, 0x0004
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        except Exception:
            pass
        # Toggling the frame off mid-press would otherwise leave the
        # capture held with nothing left to release it.
        self._capture(False)
        self._hot = self._down = ""
        self._tracking = False
        self._edges = None
        self._paint_state()          # nothing left to un-press it later
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
        if message == WM_ACTIVATE and not self._extended:
            self._extended = self._extend_frame()
            return False, 0                  # Qt wants activation
        if message in (WM_SETTINGCHANGE, WM_DISPLAYCHANGE, WM_DPICHANGED):
            self.forget_edges()
            self._border = None
            return False, 0                  # Qt and Windows both want these
        if message in (WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP,
                       WM_NCMOUSELEAVE, WM_MOUSEMOVE, WM_LBUTTONUP,
                       WM_CAPTURECHANGED):
            return self._mouse(message, wparam, lparam)
        return False, 0

    def autohide_edges(self, rect=None) -> frozenset:
        """Which edges of this window's monitor carry an auto-hiding bar.

        Cached, because this is a synchronous `SendMessage` into Explorer
        and the only caller is a message handler. Uncached it ran on
        every recalc while maximised, which means the window blocks for
        as long as Explorer takes to answer -- and if Explorer is wedged,
        for as long as that lasts. Taskbars are reconfigured about never,
        so `forget_edges` on WM_SETTINGCHANGE and WM_DISPLAYCHANGE is
        plenty fresh.

        Asked per monitor, not globally: a second screen usually has no
        taskbar at all, and pulling pixels off a window there for one
        that lives on the first screen would be a stripe of desktop
        nobody could explain.

        `rect` is the proposed client rectangle, passed in during
        WM_NCCALCSIZE. Windows does not reliably report the right monitor
        for an HWND inside that message -- Chromium hit the same thing
        and passes the rectangle for the same reason -- so the rectangle
        is used when there is one and the window otherwise.
        """
        try:
            win = _structs()
            ctypes = win.ctypes
            user32, shell32 = _win32()

            MONITOR_DEFAULTTONEAREST = 2
            if rect is not None:
                monitor = user32.MonitorFromRect(ctypes.byref(rect),
                                                 MONITOR_DEFAULTTONEAREST)
            else:
                monitor = user32.MonitorFromWindow(
                    ctypes.c_void_p(self.hwnd), MONITOR_DEFAULTTONEAREST)
            # Keyed on the monitor rather than just cached. Dragging a
            # maximised window between two screens at the same scale
            # fires none of the messages that forget this, and answering
            # with the other screen's taskbar is exactly what asking per
            # monitor was for.
            if self._edges is not None and monitor == self._monitor:
                return self._edges

            state = win.APPBARDATA()
            state.cbSize = ctypes.sizeof(win.APPBARDATA)
            # A zeroed struct with cbSize set, not NULL. The documentation
            # says "you must specify the cbSize member"; NULL is
            # undocumented, and if shell32 reads it the process dies with
            # an access violation that `except Exception` cannot catch.
            self._monitor = monitor
            if not (shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(state))
                    & ABS_AUTOHIDE):
                self._edges = frozenset()   # nothing auto-hides anywhere
                return self._edges

            info = win.MONITORINFO()
            info.cbSize = ctypes.sizeof(win.MONITORINFO)
            if not user32.GetMonitorInfoW(ctypes.c_void_p(monitor),
                                          ctypes.byref(info)):
                self._edges = frozenset()
                return self._edges

            found = set()
            for index, edge in enumerate(_ABE):
                data = win.APPBARDATA()
                data.cbSize = ctypes.sizeof(win.APPBARDATA)
                data.uEdge = index
                data.rc = info.rcMonitor
                if shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX,
                                           ctypes.byref(data)):
                    found.add(edge)
            self._edges = frozenset(found)
        except Exception:
            # Deliberately not cached. A transient failure used to be
            # written into the cache as "nothing auto-hides", where it
            # stayed for the life of the window.
            return self._edges or frozenset()
        return self._edges

    def forget_edges(self) -> None:
        """Ask again next time. The taskbar moved, or a screen did."""
        self._edges = self._monitor = None

    def _calc_size(self, wparam: int, lparam: int):
        """Reclaim the caption strip, and only the caption strip.

        Returning zero with no adjustment makes the client area the whole
        window, which is what puts our toolbar where the caption was. The
        resize borders then live *inside* the client and are handled by
        `hit_region`, which is why they have to be tested there.

        The rectangles here are in **physical** pixels, so the inset is
        `border()` rather than the logical figure `hit_region` works in.

        Maximised is the exception, for two reasons that add up. Windows
        grows the window rect by the resize border on every side when it
        maximises, expecting the frame to absorb it, and with no frame
        that overhang runs off the screen and takes the top of the
        toolbar with it. Separately, an auto-hiding taskbar needs a pixel
        left at its own edge or it can no longer see the pointer arrive.
        `maximised_insets` works out both; it is a pure function, so the
        arithmetic is tested without needing Windows.
        """
        try:
            win = _structs()
            ctypes = win.ctypes
            if not self.maximised():
                # The client area becomes the whole window, which is the
                # entire point: it is what puts the toolbar where the
                # caption was.
                return True, 0

            # wParam FALSE means lParam is a plain RECT rather than the
            # three-rectangle structure. Rare, and falling through would
            # let DefWindowProc subtract the standard frame -- the
            # opposite of what the other branch does, for the same window.
            if wparam:
                params = ctypes.cast(
                    lparam, ctypes.POINTER(win.NCCALCSIZE_PARAMS)).contents
                rect = params.rgrc[0]
            else:
                rect = ctypes.cast(lparam,
                                   ctypes.POINTER(win.RECT)).contents

            insets = maximised_insets(self.border(),
                                      self.autohide_edges(rect))
            (rect.left, rect.top, rect.right, rect.bottom) = inset_rect(
                (rect.left, rect.top, rect.right, rect.bottom), insets)
            return True, 0
        except Exception:
            return False, 0

    def _hit_test(self, lparam: int):
        try:
            win = _structs()
            ctypes = win.ctypes
            user32, _shell32 = _win32()
            point = win.POINT(*unpack_point(lparam))
            user32.ScreenToClient(self._hwnd_t(), ctypes.byref(point))
            ratio = self.window.devicePixelRatioF() or 1.0
            region = hit_region(round(point.x / ratio), round(point.y / ratio),
                                self.frame())
            return True, _HT[region]
        except Exception:
            return False, 0

    def _track_mouse(self) -> None:
        """Ask to be told when the pointer leaves the non-client area.

        Without this `WM_NCMOUSELEAVE` is *never posted*: the message is
        documented as going to the window "specified in a prior call to
        TrackMouseEvent", and with no such call there is no leave message
        and no way to know the pointer has gone. The close button then
        stays lit bright red after the pointer moves off it into the
        video, which is the common case rather than an edge one.

        Re-armed on every leave, because generating the message cancels
        the tracking that asked for it.
        """
        if self._tracking:
            return
        try:
            win = _structs()
            user32, _shell32 = _win32()
            request = win.TRACKMOUSEEVENT()
            request.cbSize = win.ctypes.sizeof(win.TRACKMOUSEEVENT)
            request.dwFlags = TME_LEAVE | TME_NONCLIENT
            request.hwndTrack = win.wintypes.HWND(self.hwnd)
            request.dwHoverTime = HOVER_DEFAULT
            self._tracking = bool(
                user32.TrackMouseEvent(win.ctypes.byref(request)))
        except Exception:
            self._tracking = False

    def _paint_state(self) -> None:
        self.toolbar.set_caption_state(self._hot, self._down)

    def _mouse(self, message: int, wparam: int, lparam: int):
        """One state machine over every mouse message that can reach a
        caption button.

        The messages arrive two ways and it is *not* the capture that
        decides which. From the documentation: "Whenever a mouse event
        occurs, the system sends a WM_NCHITTEST message to either the
        window that contains the cursor hot spot **or the window that has
        captured the mouse**. The system uses this message to determine
        whether to send a client area or nonclient area mouse message."

        So capture only redirects the hit test to us; our own `hit_region`
        still decides client versus non-client, and a press that started
        on the close button carries on producing *non-client* messages for
        as long as the pointer is over the bar or an edge, and *client*
        ones the moment it is over the video. Both have to clear the
        press, or dragging off the top edge -- which reports HTTOP, not a
        button -- leaves it drawn pressed for good.

        Hence: one method, one release path, and `ReleaseCapture` on every
        button-up regardless of where it landed. It is documented to be
        harmless when the capture is not held.
        """
        try:
            if message == WM_NCMOUSELEAVE:
                self._tracking = False       # generating it cancelled it
                return self._hover("")
            if message == WM_CAPTURECHANGED:
                # Something else took the mouse. Let go of the drawn state
                # or the button stays pressed with nothing driving it.
                # Repainted unconditionally rather than through `_hover`,
                # which only paints when the *hover* moved and would have
                # left the press showing.
                #
                # No ReleaseCapture here: this message arrives *after* the
                # system has given the capture to somebody else, whose
                # handle is in lParam. Releasing now would take it off
                # them.
                if self._down:
                    self._down = self._hot = ""
                    self._paint_state()
                return False, 0

            non_client = message in (WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN,
                                     WM_NCLBUTTONUP)
            if not (non_client or self._hot or self._down):
                # A client-area move or release with nothing lit and
                # nothing held cannot change anything, and working out
                # where it landed costs a hit test. This runs for every
                # mouse move over a window showing a live preview.
                return False, 0
            # A non-client message carries the hit-test result in wParam
            # already. A client one carries client coordinates in lParam.
            region = (_REGION_OF.get(wparam, "") if non_client
                      else self._at_client(lparam))

            if message in (WM_NCMOUSEMOVE, WM_MOUSEMOVE):
                if non_client:
                    self._track_mouse()
                # Mid-press only the pressed button may light, which is
                # what stops dragging from close onto minimise drawing
                # both at once. Never consumed: this filter would
                # otherwise take moves away from every widget under it.
                self._hover(region if (region == self._down if self._down
                                       else region in BUTTONS) else "")
                return False, 0

            if message == WM_NCLBUTTONDOWN and region in BUTTONS:
                self._down = region
                self._paint_state()
                self._capture(True)
                return True, 0                   # ours; not DefWindowProc's

            if message in (WM_NCLBUTTONUP, WM_LBUTTONUP) and self._down:
                self._capture(False)
                fired, self._down = self._down, ""
                self._hot = region if region in BUTTONS else ""
                self._paint_state()
                if fired == region:
                    self._activate(region)
                return True, 0
        except Exception:
            return False, 0
        return False, 0

    def _hwnd_t(self):
        """The window handle as the type `_win32` declared it takes."""
        return _structs().wintypes.HWND(self.hwnd)

    def _at_client(self, lparam: int) -> str:
        """Region under a *client*-coordinate lParam.

        No ScreenToClient: a client-area message already carries client
        coordinates, which is the one thing capture does change about
        them.
        """
        x, y = unpack_point(lparam)
        ratio = self.window.devicePixelRatioF() or 1.0
        return hit_region(round(x / ratio), round(y / ratio), self.frame())

    def _hover(self, region: str):
        if region != self._hot:
            self._hot = region
            self._paint_state()
        return False, 0

    def _capture(self, take: bool) -> None:
        try:
            user32, _shell32 = _win32()
            if take:
                user32.SetCapture(self._hwnd_t())
            else:
                user32.ReleaseCapture()
        except Exception:
            pass

    def _activate(self, region: str) -> None:
        """Do it, but not from here.

        This runs on the stack of a native message. `close()` runs the
        close event synchronously and can destroy the window, after which
        the handler returns a result through a frame belonging to a dead
        HWND; the show calls re-enter the window procedure (WM_NCCALCSIZE,
        WM_SIZE) while this one is still running. Deferring to the next
        turn of the event loop costs nothing and removes the whole class.
        """
        from PySide6 import QtCore

        window = self.window
        if region == MINIMISE:
            QtCore.QTimer.singleShot(0, window.showMinimized)
        elif region == MAXIMISE:
            QtCore.QTimer.singleShot(
                0, lambda: (window.showNormal() if window.isMaximized()
                            else window.showMaximized()))
        elif region == CLOSE:
            QtCore.QTimer.singleShot(0, window.close)


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
        self._shape = ""        # region the cursor is currently shaped for

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
            # Before the window is up, the flag simply applies. After, Qt
            # destroys the native window and builds another, so the
            # geometry has to be put back by hand and the surface the live
            # view draws on is rebuilt. Startup takes the first path; the
            # menu toggle takes the second.
            was_visible = window.isVisible()
            geometry = window.geometry()
            maximised = window.isMaximized()
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
                if maximised:
                    window.showMaximized()
                else:
                    window.setGeometry(geometry)
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
            # Cursor only, and only when it changes. The move is never
            # consumed, or every widget under the pointer would stop
            # seeing it -- this filter is on the application, so that is
            # every widget in the program.
            if region != self._shape:
                self._shape = region
                name = self._CURSORS.get(region)
                if name:
                    self.window.setCursor(
                        getattr(QtCore.Qt.CursorShape, name))
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
        self._shape = ""
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
