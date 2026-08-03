"""The Windows frame geometry.

Runs everywhere, deliberately. `hit_region` is the piece of a custom title
bar most likely to be wrong and the piece whose failures are hardest to
report -- "dragging doesn't work", "the corner won't grab", "the snap
thing never appears" -- so it is a pure function of rectangles, tested on
whatever machine happens to be to hand rather than only on Windows.
"""
from darlaston.ui.frame import (BOTTOM, BOTTOMLEFT, BOTTOMRIGHT, BUTTONS,
                                    CAPTION, CLIENT, CLOSE, EDGES, LEFT,
                                    MAXIMISE, MINIMISE, RIGHT, TOP, TOPLEFT,
                                    TOPRIGHT, Frame, hit_region)

#: A window like the real one: 1200x800, a 36 px toolbar, an 8 px border,
#: three 40 px buttons at the right, and the wordmark plus menus reserved
#: along the left.
WINDOW = Frame(width=1200, height=800, bar=36, border=8,
               buttons=((1080, 40), (1120, 40), (1160, 40)),
               reserved=((0, 330),))


def test_the_toolbar_drags_the_window():
    """Everything that makes a title bar a title bar comes from this one
    answer: dragging, double-click to maximise, the right-click system
    menu, and Aero Snap."""
    assert hit_region(700, 18, WINDOW) == CAPTION


def test_the_menus_still_open():
    """The wordmark and the menus sit in the drag strip. If the drag wins
    there, clicking Setup moves the window instead."""
    for x in (10, 140, 300):
        assert hit_region(x, 18, WINDOW) == CLIENT, f"x={x} was swallowed"
    # And immediately past them, dragging resumes.
    assert hit_region(340, 18, WINDOW) == CAPTION


def test_each_caption_button_reports_itself():
    for x, expected in ((1100, MINIMISE), (1140, MAXIMISE), (1180, CLOSE)):
        assert hit_region(x, 18, WINDOW) == expected
    # The maximise button specifically: Windows 11 shows its snap-layouts
    # flyout only over a region that reports HTMAXBUTTON, and a custom
    # frame without it is quietly worse than the standard one.
    #
    # Asked below the resize border, and that is a decision rather than a
    # convenience: the top `border` pixels of these buttons report the
    # top edge, exactly as they do on a standard window, where the caption
    # buttons sit below the frame rather than at y=0. It costs the flyout
    # on the top few pixels of the button and keeps the top edge grabbable
    # along its whole length.
    assert hit_region(1121, WINDOW.border, WINDOW) == MAXIMISE
    assert hit_region(1121, WINDOW.border - 1, WINDOW) == TOP


def test_the_resize_edges_beat_everything():
    """A corner that cannot be grabbed because a button is near it is a
    window people describe as broken."""
    assert hit_region(1195, 2, WINDOW) == TOPRIGHT      # over the close button
    assert hit_region(1195, 18, WINDOW) == RIGHT        # beside it
    assert hit_region(2, 2, WINDOW) == TOPLEFT          # over the wordmark
    assert hit_region(600, 2, WINDOW) == TOP
    assert hit_region(600, 798, WINDOW) == BOTTOM
    assert hit_region(2, 798, WINDOW) == BOTTOMLEFT
    assert hit_region(1198, 798, WINDOW) == BOTTOMRIGHT
    assert hit_region(2, 400, WINDOW) == LEFT


def test_a_maximised_window_has_no_edges_to_grab():
    """Windows will let you drag one, and the window then un-maximises to
    somewhere the person did not ask for."""
    # Buttons at the right of *this* width. The fixture's are at 1080..1200
    # in a 1200-wide window; reusing them here would leave a 720 px gap to
    # the corner and quietly test the wrong thing in the one state where
    # the corner matters most.
    full = Frame(width=1920, height=1080, bar=36, border=8,
                 buttons=((1800, 40), (1840, 40), (1880, 40)),
                 reserved=WINDOW.reserved, maximised=True)
    for point in ((2, 2), (960, 2), (1918, 1078), (2, 540)):
        got = hit_region(*point, full)
        assert got not in EDGES, f"{point} offered a resize edge while maximised"
    # The bar still drags, which is how a maximised window is restored.
    assert hit_region(700, 18, full) == CAPTION
    assert hit_region(1820, 18, full) == MINIMISE
    # The flick: with no edges in the way, the very corner closes. This is
    # how a maximised window is closed without aiming, and it only works
    # because the maximised branch skips the edge tests entirely.
    assert hit_region(1919, 0, full) == CLOSE
    assert hit_region(1919, 35, full) == CLOSE


def test_the_body_of_the_window_is_left_alone():
    """Everything below the bar belongs to Qt. A frame that claims any of
    it is a frame that eats clicks on the live view."""
    for y in (40, 200, 700):
        assert hit_region(600, y, WINDOW) == CLIENT


def test_every_region_maps_to_a_hit_test_code():
    """A region with no code would be returned to Windows as None and
    silently become HTNOWHERE, which is a window that ignores the mouse."""
    from darlaston.ui.frame import _HT

    every = EDGES | BUTTONS | {CAPTION, CLIENT}
    assert every <= set(_HT), f"no code for {sorted(every - set(_HT))}"


def test_the_border_is_wider_than_it_looks():
    """SM_CXSIZEFRAME alone is the visible frame and is too thin to grab;
    Windows adds SM_CXPADDEDBORDER, which is why a standard window's edge
    is easier to hit than it appears.

    Off Windows there is no metric to read and this exercises the
    fallback, so the fallback is what it asserts -- by value. It used to
    assert only `>= 4` and monotonicity, both of which the fallback
    satisfies on its own, which meant that on every machine that runs
    these tests it asserted that a constant was a constant.
    """
    import sys

    from darlaston.ui.frame import border_thickness

    at96 = border_thickness(96)
    if not sys.platform.startswith("win"):
        assert at96 == 8, "the 96-DPI fallback"
        assert border_thickness(192) == 16, "and it scales"
        assert border_thickness(48) == 4, "with a floor, so it stays grabbable"
        return
    assert at96 >= 4, at96
    # Scales with DPI, so a 4K screen does not get a two-pixel target.
    assert border_thickness(192) >= at96


def test_the_toolbar_and_the_frame_agree_on_where_things_are(qapp):
    """The frame asks the layout rather than assuming.

    Two opinions about where the buttons ended up is exactly the bug that
    makes a close button that highlights but does not close, or a drag
    strip with a dead patch in it.
    """
    from darlaston.ui.shell import CaptionButton, ToolBar

    bar = ToolBar()
    bar.add_menu("Setup", "Setup")
    bar.add_menu("Capture", "Capture")
    bar.resize(1200, 36)
    bar.show_caption_buttons()
    bar.show()
    qapp.processEvents()

    buttons, reserved = bar.caption_geometry()
    assert len(buttons) == 3, "three buttons, or the geometry is guessing"

    # In order, adjacent, and at the right-hand end.
    xs = [x for x, _w in buttons]
    assert xs == sorted(xs), "buttons reported out of order"
    assert buttons[-1][0] + buttons[-1][1] == bar.width(), \
        "the close button does not reach the corner, so the flick-to-close "\
        "gesture would miss"
    for (x, w), (nx, _nw) in zip(buttons, buttons[1:]):
        assert x + w == nx, "a gap between buttons is a dead pixel column"

    # The reserved strip covers the wordmark and every menu, so dragging
    # never swallows a click on them. It starts *at* the wordmark: the
    # layout margin to its left is not a control, and claiming it made the
    # first few pixels of the bar the one part a window cannot be dragged
    # by.
    (start, span), = reserved
    assert start == bar.wordmark.x() > 0
    assert start + span >= bar.wordmark.x() + bar.wordmark.width()

    frame = Frame(width=bar.width(), height=800, bar=36, border=8,
                  buttons=buttons, reserved=reserved)
    assert hit_region(bar.wordmark.x() + 4, 18, frame) == CLIENT
    assert hit_region(buttons[2][0] + 4, 18, frame) == CLOSE
    assert hit_region(start + span + 40, 18, frame) == CAPTION
    # The margin left of the wordmark drags like the rest of the bar. Only
    # visible where the resize border is narrower than the margin -- at a
    # border of 8 the whole margin is edge anyway, which is why this asks
    # at 2.
    thin = Frame(width=bar.width(), height=800, bar=36, border=2,
                 buttons=buttons, reserved=reserved)
    assert hit_region(start - 1, 18, thin) == CAPTION, \
        "the layout margin should drag the window like the rest of the bar"
    bar.close()


def test_the_caption_buttons_handle_their_own_mouse_until_told_not_to(qapp):
    """The two platforms deliver the pointer differently.

    On a frameless Qt window these are ordinary widgets. On Windows, once
    hit testing claims their area, the pointer arrives as non-client
    messages and Qt never sees a click there -- so a button still trying
    to handle its own would be waiting for events that never come, and
    would look dead.
    """
    from PySide6 import QtCore

    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    transparent = QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
    for button in bar.caption.values():
        assert not button.testAttribute(transparent), \
            "deaf before anything asked it to be"
    for button in bar.caption.values():
        button.deafen()
        assert button.testAttribute(transparent)


def test_a_caption_button_reports_which_one_it_was(qapp):
    """One signal carrying the kind, rather than three connections, so the
    frame can route them without knowing the layout."""
    from PySide6 import QtCore, QtGui

    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    bar.resize(600, 36)
    seen = []
    for button in bar.caption.values():
        button.pressed.connect(seen.append)

    close = bar.caption["close"]
    close.resize(46, 36)
    middle = QtCore.QPointF(20, 18)
    for kind in (QtCore.QEvent.Type.MouseButtonPress,
                 QtCore.QEvent.Type.MouseButtonRelease):
        close.mousePressEvent(QtGui.QMouseEvent(
            kind, middle, middle, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)) if kind == \
            QtCore.QEvent.Type.MouseButtonPress else close.mouseReleaseEvent(
            QtGui.QMouseEvent(
                kind, middle, middle, QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier))
    assert seen == ["close"]


def test_who_draws_the_frame_follows_the_desktop_and_the_setting(monkeypatch):
    """Two desktops are left alone for opposite reasons: a tiling window
    manager draws nothing on purpose, and KDE draws a real decoration in
    the user's own colours. Everything else gets ours."""
    from darlaston.ui.frame import wanted

    def under(desktop):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
        monkeypatch.delenv("GNOME_DESKTOP_SESSION_ID", raising=False)
        return wanted("auto")

    assert under("KDE") is False
    assert under("sway") is False
    assert under("") is False, "a bare window manager draws none on purpose"
    assert under("GNOME") is True, "Qt's Wayland fallback is worse than ours"
    assert under("ubuntu:GNOME") is True, "the list form is what GNOME sets"
    assert under("XFCE") is True

    # And the setting overrides the guess, both ways.
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert wanted("ours") is True
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert wanted("system") is False


def test_hover_and_press_are_told_from_outside(qapp):
    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    bar.set_caption_state("close", "")
    assert bar.caption["close"].hot and not bar.caption["close"].down
    assert not bar.caption["minimise"].hot
    bar.set_caption_state("close", "close")
    assert bar.caption["close"].down
    bar.set_caption_state("", "")
    assert not any(b.hot or b.down for b in bar.caption.values())


def test_a_maximised_window_leaves_room_for_an_autohiding_taskbar():
    """It reveals itself when the pointer reaches its screen edge, and it
    cannot see the pointer through a window covering that edge.

    Without the gap, the taskbar simply stops working while this program
    is open, which reads as Windows being broken rather than as us."""
    from darlaston.ui.frame import AUTOHIDE_GAP, maximised_insets

    gap = AUTOHIDE_GAP
    assert gap >= 2, "Chromium ships two for this; one has no evidence"

    plain = maximised_insets(8)
    assert plain == (8, 8, 8, 8), "the frame overhang is per edge"

    at_bottom = maximised_insets(8, frozenset({BOTTOM}))
    assert at_bottom == (8, 8, 8, 8 + gap)
    # Only the edge that has one, so three edges do not lose pixels for a
    # taskbar that is not there.
    assert at_bottom[:3] == plain[:3]

    # A taskbar on the left, which plenty of people run.
    assert maximised_insets(8, frozenset({LEFT})) == (8 + gap, 8, 8, 8)
    # And more than one, which is unusual but possible with several bars.
    assert maximised_insets(8, frozenset({TOP, RIGHT})) == \
        (8, 8 + gap, 8 + gap, 8)


def test_the_caption_strip_is_the_same_colour_as_the_bar(qapp):
    """The blanket `QWidget` background rule reaches into the strip, and
    painted it the window's colour rather than the bar's -- a darker patch
    behind the window buttons, on every platform that draws its own."""
    from darlaston.ui import theme
    from darlaston.ui.shell import ToolBar

    qapp.setStyleSheet(theme.stylesheet())
    bar = ToolBar()
    bar.add_menu("Setup", "Setup")
    bar.show_caption_buttons()
    bar.resize(560, 36)
    image = bar.grab().toImage()

    # A row above the glyphs, so this reads background and not ink.
    left = image.pixelColor(200, 4).getRgb()[:3]
    right = image.pixelColor(image.width() - 4, 4).getRgb()[:3]
    assert left == right, f"strip {right} does not match bar {left}"


def test_the_mac_titlebar_asks_qt_rather_than_appkit(qapp):
    """The two settings Qt derives from window flags have to be set as
    window flags.

    On Qt 6.9 and later `QCocoaWindow::windowStyleMask` computes
    `NSWindowStyleMaskFullSizeContentView` from `ExpandedClientAreaHint`
    and no longer preserves a hand-set bit, and `setWindowFlags`
    reassigns `titlebarAppearsTransparent` from
    `NoTitleBarBackgroundHint`. Both fullscreen handlers call
    `setWindowFlags`, so anything set through AppKit instead survives
    until the first green button and no longer.

    Runs everywhere: these are Qt flags, and only the title-text call
    underneath them is macOS-only.
    """
    from PySide6 import QtCore, QtWidgets

    from darlaston.ui import theme

    window = QtWidgets.QWidget()
    window.show()
    assert theme._mac_unify_titlebar(window)

    flags = window.windowHandle().flags()
    assert flags & QtCore.Qt.WindowType.ExpandedClientAreaHint
    assert flags & QtCore.Qt.WindowType.NoTitleBarBackgroundHint
    # Qt 6.8 gave macOS safe areas, and a layout that respects them puts
    # the content straight back below the title bar it was run under.
    assert not window.testAttribute(
        QtCore.Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea)
    window.close()


def test_the_traffic_light_inset_goes_away_in_fullscreen(qapp):
    """macOS moves the lights into the drop-down overlay in fullscreen, so
    an inset that stayed would be a hole with nothing in it."""
    from PySide6 import QtCore, QtWidgets

    from darlaston.ui import theme
    from darlaston.ui.shell import ToolBar

    window = QtWidgets.QWidget()
    bar = ToolBar()
    QtWidgets.QVBoxLayout(window).addWidget(bar)
    window.show()
    theme.follow_window_controls(window, bar)
    assert bar._row.getContentsMargins()[0] == theme.MACOS_LIGHTS

    window.setWindowState(QtCore.Qt.WindowState.WindowFullScreen)
    qapp.processEvents()
    assert bar._row.getContentsMargins()[0] == 0, "inset outlived the lights"

    window.setWindowState(QtCore.Qt.WindowState.WindowNoState)
    qapp.processEvents()
    assert bar._row.getContentsMargins()[0] == theme.MACOS_LIGHTS
    window.close()


def test_a_packed_point_is_read_as_two_signed_numbers():
    """A monitor left of or above the primary one gives negative screen
    coordinates, and the documentation says so in as many words: both
    fields "represent signed values because they can take negative values
    on systems with multiple monitors". Read unsigned they come back near
    65000, so the window works on one screen and puts the pointer
    somewhere impossible on the other."""
    from darlaston.ui.frame import unpack_point

    assert unpack_point(0) == (0, 0)
    assert unpack_point((300 << 16) | 100) == (100, 300)
    # -1 in both halves, which is the all-ones word an unsigned read
    # turns into 65535.
    assert unpack_point(0xFFFFFFFF) == (-1, -1)
    # A second monitor to the left: x negative, y positive.
    assert unpack_point((50 << 16) | (0x10000 - 200)) == (-200, 50)
    # The extremes of the range.
    assert unpack_point((0x8000 << 16) | 0x8000) == (-32768, -32768)
    assert unpack_point((0x7FFF << 16) | 0x7FFF) == (32767, 32767)


def test_the_maximised_rectangle_is_pulled_in_and_not_pushed_out():
    """The four lines that decide whether a maximised window fits its
    screen. A sign error here is a window that shrinks by twice the
    border every time it is maximised, or one that hangs off every edge."""
    from darlaston.ui.frame import AUTOHIDE_GAP, BOTTOM, inset_rect, \
        maximised_insets

    screen = (0, 0, 1920, 1080)
    assert inset_rect(screen, (8, 8, 8, 8)) == (8, 8, 1912, 1072)
    # Each edge independently, so a transposition cannot hide.
    assert inset_rect(screen, (1, 2, 3, 4)) == (1, 2, 1917, 1076)
    # A monitor that does not start at the origin: the insets are
    # relative to the rectangle, not to the desktop.
    assert inset_rect((-1920, 0, 0, 1080), (8, 8, 8, 8)) == \
        (-1912, 8, -8, 1072)
    # And end to end, the way _calc_size uses it.
    insets = maximised_insets(8, frozenset({BOTTOM}))
    assert inset_rect(screen, insets) == (8, 8, 1912, 1080 - 8 - AUTOHIDE_GAP)


def test_the_hit_test_codes_are_the_numbers_windows_uses():
    """Pinned by value, not by presence. A typo turning HTCLOSE into 21
    is HTHELP, which a presence check passes and which puts a question
    mark cursor over the close button."""
    from darlaston.ui.frame import _HT, _REGION_OF, BOTTOM, BOTTOMLEFT, \
        BOTTOMRIGHT, CAPTION, CLIENT, CLOSE, LEFT, MAXIMISE, MINIMISE, \
        RIGHT, TOP, TOPLEFT, TOPRIGHT

    assert _HT == {
        CLIENT: 1,          # HTCLIENT
        CAPTION: 2,         # HTCAPTION
        MINIMISE: 8,        # HTMINBUTTON
        MAXIMISE: 9,        # HTMAXBUTTON -- the snap-layouts one
        LEFT: 10,           # HTLEFT
        RIGHT: 11,          # HTRIGHT
        TOP: 12,            # HTTOP
        TOPLEFT: 13,        # HTTOPLEFT
        TOPRIGHT: 14,       # HTTOPRIGHT
        BOTTOM: 15,         # HTBOTTOM
        BOTTOMLEFT: 16,     # HTBOTTOMLEFT
        BOTTOMRIGHT: 17,    # HTBOTTOMRIGHT
        CLOSE: 20,          # HTCLOSE
    }
    # And the reverse map loses nothing, which it would if two regions
    # ever shared a code -- silently, by misrouting a button press.
    assert len(_REGION_OF) == len(_HT)
    assert all(_REGION_OF[code] == name for name, code in _HT.items())


def test_the_whole_bar_drags_when_there_are_no_buttons():
    """The fallback: before the frame is taken, and on any platform that
    never takes it, the bar is a drag strip with nothing claimed out of
    its right-hand end."""
    from darlaston.ui.frame import CAPTION, CLIENT, Frame, hit_region

    bare = Frame(width=1200, height=800, bar=36, border=8, buttons=(),
                 reserved=((8, 300),))
    assert hit_region(1150, 18, bare) == CAPTION   # where a button was
    assert hit_region(600, 18, bare) == CAPTION
    assert hit_region(100, 18, bare) == CLIENT      # still a menu there
    assert hit_region(600, 400, bare) == CLIENT


def test_a_reserved_run_that_reaches_a_button_does_not_win():
    """A longer translated menu label pushes the reserved run to the
    right. Buttons are tested first, so the button keeps the pixel -- and
    that ordering is the thing being pinned, since the alternative is a
    close button that stops closing in one language."""
    from darlaston.ui.frame import CLOSE, Frame, hit_region

    crowded = Frame(width=1200, height=800, bar=36,
                    border=8, buttons=((1062, 46), (1108, 46), (1154, 46)),
                    reserved=((8, 1192),))       # runs under all three
    assert hit_region(1160, 18, crowded) == CLOSE


def test_a_menu_added_after_the_caption_strip_still_lands_on_the_left(qapp):
    """The strip goes in after the stretch, so an insertion point of
    "just before the last item" put every later menu on the far right,
    past the window buttons. That happened once; nothing guarded it."""
    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    bar.add_menu("Setup", "Setup")
    bar.show_caption_buttons()
    bar.add_menu("Capture", "Capture")      # the order that broke it
    bar.resize(1200, 36)
    bar.show()
    qapp.processEvents()

    buttons, ((start, span),) = bar.caption_geometry()
    later = bar.menus["Capture"].menuAction()
    for widget in bar.findChildren(type(bar.wordmark)):
        if widget.text() == "Capture":
            assert widget.x() + widget.width() <= buttons[0][0], \
                "a menu ended up under the window buttons"
            assert start <= widget.x() < start + span, \
                "a menu ended up outside the strip that protects it"
            break
    else:
        raise AssertionError("the menu button is not in the bar at all")
    assert later is not None
    bar.close()


def test_a_caption_button_dragged_off_does_not_fire(qapp):
    """The universal escape. Press, change your mind, drag away, release
    -- and nothing happens. Losing it means a close button that cannot be
    backed out of."""
    from PySide6 import QtCore, QtGui

    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    bar.show_caption_buttons()
    close = bar.caption["close"]
    close.resize(46, 36)
    fired = []
    close.pressed.connect(fired.append)

    def event(kind, x, y, buttons):
        point = QtCore.QPointF(x, y)
        return QtGui.QMouseEvent(kind, point, point,
                                 QtCore.Qt.MouseButton.LeftButton, buttons,
                                 QtCore.Qt.KeyboardModifier.NoModifier)

    press = QtCore.QEvent.Type.MouseButtonPress
    release = QtCore.QEvent.Type.MouseButtonRelease
    left = QtCore.Qt.MouseButton.LeftButton
    none = QtCore.Qt.MouseButton.NoButton

    close.mousePressEvent(event(press, 20, 18, left))
    assert close.down
    close.mouseReleaseEvent(event(release, 20, 200, none))   # dragged off
    assert fired == [], "released outside the button and it fired anyway"
    assert not close.down and not close.hot

    # And the ordinary case still does.
    close.mousePressEvent(event(press, 20, 18, left))
    close.mouseReleaseEvent(event(release, 20, 18, none))
    assert fired == ["close"]


def test_a_right_click_on_a_caption_button_is_not_swallowed(qapp):
    """It has to reach the window, or the system menu works everywhere
    along the bar except over three buttons."""
    from PySide6 import QtCore, QtGui

    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    bar.show_caption_buttons()
    close = bar.caption["close"]
    close.resize(46, 36)
    fired = []
    close.pressed.connect(fired.append)

    point = QtCore.QPointF(20, 18)
    right = QtCore.Qt.MouseButton.RightButton
    event = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress, point,
                              point, right, right,
                              QtCore.Qt.KeyboardModifier.NoModifier)
    event.accept()
    close.mousePressEvent(event)
    assert not event.isAccepted(), "the right click stopped here"
    assert not close.down and fired == []


# ---- the Windows button state machine -------------------------------------
#
# The half of the frame no machine here can run. It is a pure state machine
# over (message, wParam, lParam) with two dependencies -- the Win32 calls
# and the toolbar -- so with both faked the whole press/drag/release matrix
# pins on any platform. This is where the two stuck-state bugs lived, and
# reading the code is what failed to catch them.

class _FakeUser32:
    """Records the calls that matter and answers the rest plausibly."""

    def __init__(self):
        self.captured = False
        self.calls = []

    def SetCapture(self, _hwnd):
        self.captured = True
        self.calls.append("SetCapture")
        return 0

    def ReleaseCapture(self):
        self.calls.append("ReleaseCapture")
        released, self.captured = self.captured, False
        return int(released)

    def TrackMouseEvent(self, _request):
        self.calls.append("TrackMouseEvent")
        return 1

    def GetDpiForWindow(self, _hwnd):
        return 96

    def IsZoomed(self, _hwnd):
        return 0


class _FakeBar:
    """Just enough toolbar: the geometry and the drawn state."""

    #: Three 46px buttons ending at 1200, and the menus along the left.
    GEOMETRY = (((1062, 46), (1108, 46), (1154, 46)), ((8, 330),))

    def __init__(self):
        self.state = ("", "")
        self.hidden = False

    def caption_geometry(self):
        return self.GEOMETRY

    def set_caption_state(self, hot, down):
        self.state = (hot, down)

    def height(self):
        return 36

    def hide_caption_buttons(self):
        self.hidden = True


def _machine(monkeypatch):
    """A WindowsFrame with the platform faked out from under it."""
    from darlaston.ui import frame as F

    user32 = _FakeUser32()
    monkeypatch.setattr(F, "_win32", lambda: (user32, object()))

    class _Window:
        def width(self):
            return 1200

        def height(self):
            return 800

        def devicePixelRatioF(self):
            return 1.0

        def isMaximized(self):
            return False

    bar = _FakeBar()
    frame = F.WindowsFrame(_Window(), bar)
    frame.hwnd = 0x1234
    monkeypatch.setattr(frame, "_hwnd_t", lambda: 0x1234)
    monkeypatch.setattr(frame, "_track_mouse",
                        lambda: user32.TrackMouseEvent(None))
    fired = []
    monkeypatch.setattr(frame, "_activate", fired.append)
    return frame, bar, user32, fired


def _nc(frame, message, region):
    from darlaston.ui.frame import _HT

    return frame.handle(message, _HT[region], 0)


def _client(frame, message, x, y):
    return frame.handle(message, 0, ((y & 0xFFFF) << 16) | (x & 0xFFFF))


def test_a_caption_button_press_and_release_closes_and_lets_go(monkeypatch):
    from darlaston.ui.frame import CLOSE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP

    frame, bar, user32, fired = _machine(monkeypatch)

    assert _nc(frame, WM_NCLBUTTONDOWN, CLOSE) == (True, 0)
    assert bar.state == ("", CLOSE)
    assert user32.captured, "the press did not take the capture"

    assert _nc(frame, WM_NCLBUTTONUP, CLOSE) == (True, 0)
    assert fired == [CLOSE]
    assert bar.state == (CLOSE, "")
    assert not user32.captured, \
        "the capture leaked -- every successful click would leak one"


def test_a_press_dragged_off_an_edge_does_not_stick(monkeypatch):
    """The one that reading the code missed.

    Capture does not turn non-client messages into client ones: the hit
    test still decides, and it is ours. So dragging up off the top edge
    reports HTTOP and the release arrives as WM_NCLBUTTONUP with a region
    that is not a button at all. A guard of `region in BUTTONS` on the up
    message drops it, and the button draws pressed for ever.
    """
    from darlaston.ui.frame import (CLOSE, TOP, WM_NCLBUTTONDOWN,
                                    WM_NCLBUTTONUP, WM_NCMOUSEMOVE)

    for escape in (TOP, "right", "bottom", "topright"):
        frame, bar, user32, fired = _machine(monkeypatch)
        _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
        _nc(frame, WM_NCMOUSEMOVE, escape)
        assert bar.state == ("", CLOSE), \
            f"dragged onto {escape} and the button stayed lit"

        _nc(frame, WM_NCLBUTTONUP, escape)
        assert fired == [], f"released on {escape} and it fired anyway"
        assert bar.state == ("", ""), f"stuck pressed after {escape}"
        assert not user32.captured, f"capture leaked via {escape}"


def test_a_press_dragged_into_the_video_does_not_stick(monkeypatch):
    """The other direction: over the client area the messages *are*
    client ones, in client coordinates, and they have to clear it too."""
    from darlaston.ui.frame import (CLOSE, WM_LBUTTONUP, WM_MOUSEMOVE,
                                    WM_NCLBUTTONDOWN)

    frame, bar, user32, fired = _machine(monkeypatch)
    _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
    _client(frame, WM_MOUSEMOVE, 600, 400)
    assert bar.state == ("", CLOSE)

    _client(frame, WM_LBUTTONUP, 600, 400)
    assert fired == []
    assert bar.state == ("", "") and not user32.captured


def test_dragging_back_onto_the_button_still_fires(monkeypatch):
    """The press was cancelled by leaving, not destroyed. Coming back and
    releasing is a click, which is what every other button does."""
    from darlaston.ui.frame import (CLOSE, WM_LBUTTONUP, WM_MOUSEMOVE,
                                    WM_NCLBUTTONDOWN, WM_NCLBUTTONUP,
                                    WM_NCMOUSEMOVE)

    frame, bar, _user32, fired = _machine(monkeypatch)
    _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
    _client(frame, WM_MOUSEMOVE, 600, 400)      # off into the video
    assert bar.state == ("", CLOSE)
    _nc(frame, WM_NCMOUSEMOVE, CLOSE)           # and back
    assert bar.state == (CLOSE, CLOSE)
    _nc(frame, WM_NCLBUTTONUP, CLOSE)
    assert fired == [CLOSE]


def test_only_the_pressed_button_lights_while_pressed(monkeypatch):
    """Dragging from close onto minimise used to draw minimise hot and
    close pressed at the same time: two buttons lit for one press."""
    from darlaston.ui.frame import (CLOSE, MINIMISE, WM_NCLBUTTONDOWN,
                                    WM_NCMOUSEMOVE)

    frame, bar, _user32, _fired = _machine(monkeypatch)
    _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
    _nc(frame, WM_NCMOUSEMOVE, MINIMISE)
    assert bar.state == ("", CLOSE), "two buttons lit for one press"


def test_hover_arrives_and_leaves(monkeypatch):
    from darlaston.ui.frame import (CAPTION, CLOSE, WM_NCMOUSELEAVE,
                                    WM_NCMOUSEMOVE)

    frame, bar, user32, _fired = _machine(monkeypatch)
    _nc(frame, WM_NCMOUSEMOVE, CLOSE)
    assert bar.state == (CLOSE, "")
    assert "TrackMouseEvent" in user32.calls, \
        "nothing asked to be told when the pointer leaves"

    # Off the button but still in the bar.
    _nc(frame, WM_NCMOUSEMOVE, CAPTION)
    assert bar.state == ("", "")

    # And straight out of the non-client area entirely.
    _nc(frame, WM_NCMOUSEMOVE, CLOSE)
    assert bar.state == (CLOSE, "")
    assert frame.handle(WM_NCMOUSELEAVE, 0, 0) == (False, 0), \
        "Windows still wants this message"
    assert bar.state == ("", "")
    assert not frame._tracking, "the request has to be re-armed after a leave"


def test_a_move_over_the_video_clears_a_stale_hover(monkeypatch):
    """Belt and braces for the leave message. If WM_NCMOUSELEAVE ever did
    not arrive on the crossing from HTCLOSE to HTCLIENT inside one window
    -- which is inference rather than documentation -- an ordinary client
    move still puts the button out."""
    from darlaston.ui.frame import CLOSE, WM_MOUSEMOVE, WM_NCMOUSEMOVE

    frame, bar, _user32, _fired = _machine(monkeypatch)
    _nc(frame, WM_NCMOUSEMOVE, CLOSE)
    assert bar.state == (CLOSE, "")
    _client(frame, WM_MOUSEMOVE, 600, 400)
    assert bar.state == ("", "")


def test_losing_the_capture_lets_go_of_the_button(monkeypatch):
    from darlaston.ui.frame import CLOSE, WM_CAPTURECHANGED, WM_NCLBUTTONDOWN

    frame, bar, _user32, fired = _machine(monkeypatch)
    _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
    assert frame.handle(WM_CAPTURECHANGED, 0, 0) == (False, 0)
    assert bar.state == ("", "") and fired == []


def test_a_move_is_never_swallowed(monkeypatch):
    """This runs inside the window procedure for every mouse move over the
    window. Consuming one takes it away from whatever is underneath."""
    from darlaston.ui.frame import (CLOSE, WM_MOUSEMOVE, WM_NCLBUTTONDOWN,
                                    WM_NCMOUSEMOVE)

    frame, _bar, _user32, _fired = _machine(monkeypatch)
    assert _nc(frame, WM_NCMOUSEMOVE, CLOSE)[0] is False
    assert _client(frame, WM_MOUSEMOVE, 600, 400)[0] is False
    _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
    assert _client(frame, WM_MOUSEMOVE, 600, 400)[0] is False, \
        "swallowed mid-press too"


def test_the_capture_is_released_on_every_path_that_clears_the_press(
        monkeypatch):
    """The invariant, stated once. Every way a press can end must leave
    the capture let go, or it leaks into whatever Qt does next."""
    from darlaston.ui.frame import (CLOSE, TOP, WM_CAPTURECHANGED,
                                    WM_LBUTTONUP, WM_NCLBUTTONDOWN,
                                    WM_NCLBUTTONUP)

    def ends_with(finish):
        frame, bar, user32, _fired = _machine(monkeypatch)
        _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
        assert user32.captured
        finish(frame)
        assert not frame._down, "the press outlived its ending"
        assert bar.state[1] == "", "still drawn pressed"
        return user32.captured

    assert not ends_with(lambda f: _nc(f, WM_NCLBUTTONUP, CLOSE))
    assert not ends_with(lambda f: _nc(f, WM_NCLBUTTONUP, TOP))
    assert not ends_with(lambda f: _client(f, WM_LBUTTONUP, 600, 400))
    assert not ends_with(lambda f: f.detach())

    # WM_CAPTURECHANGED is the exception, and it is an exception about
    # what the message means rather than about the invariant: it arrives
    # *after* the system has handed the capture to the window named in
    # lParam. Releasing then would take it off them. So the capture is
    # already gone by the time this runs -- which is what the fake is
    # made to do here -- and all that is left is to stop drawing pressed.
    def lose_it(f):
        user32.captured = False
        f.handle(WM_CAPTURECHANGED, 0, 0)

    frame, bar, user32, _fired = _machine(monkeypatch)
    _nc(frame, WM_NCLBUTTONDOWN, CLOSE)
    lose_it(frame)
    assert not frame._down and bar.state == ("", "")
    assert "ReleaseCapture" not in user32.calls, \
        "took the capture back off whoever the system just gave it to"


def test_an_idle_client_move_costs_nothing(monkeypatch):
    """This runs inside the window procedure for every mouse move over a
    window showing a live preview. With nothing lit and nothing held, a
    move in the client area cannot change anything, so it must not pay
    for a hit test to find that out."""
    from darlaston.ui.frame import CLOSE, WM_MOUSEMOVE, WM_NCMOUSEMOVE

    frame, _bar, _user32, _fired = _machine(monkeypatch)
    asked = []
    real = frame._at_client
    monkeypatch.setattr(frame, "_at_client",
                        lambda lp: (asked.append(lp), real(lp))[1])

    _client(frame, WM_MOUSEMOVE, 600, 400)
    assert asked == [], "worked out where an idle move landed"

    # But once something is lit it has to look, or the hover never clears.
    _nc(frame, WM_NCMOUSEMOVE, CLOSE)
    _client(frame, WM_MOUSEMOVE, 600, 400)
    assert len(asked) == 1
