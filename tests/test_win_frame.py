"""The Windows frame geometry.

Runs everywhere, deliberately. `hit_region` is the piece of a custom title
bar most likely to be wrong and the piece whose failures are hardest to
report -- "dragging doesn't work", "the corner won't grab", "the snap
thing never appears" -- so it is a pure function of rectangles, tested on
whatever machine happens to be to hand rather than only on Windows.
"""
from darlaston.ui.win_frame import (BOTTOM, BOTTOMLEFT, BOTTOMRIGHT, BUTTONS,
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
    assert hit_region(1121, 2 + 8, WINDOW) == MAXIMISE


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
    full = Frame(width=1920, height=1080, bar=36, border=8,
                 buttons=WINDOW.buttons, reserved=WINDOW.reserved,
                 maximised=True)
    for point in ((2, 2), (960, 2), (1918, 1078), (2, 540)):
        got = hit_region(*point, full)
        assert got not in EDGES, f"{point} offered a resize edge while maximised"
    # The bar still drags, which is how a maximised window is restored.
    assert hit_region(700, 18, full) == CAPTION
    # And the buttons still answer, since they moved with the width.
    assert hit_region(1100, 18, full) == MINIMISE


def test_the_body_of_the_window_is_left_alone():
    """Everything below the bar belongs to Qt. A frame that claims any of
    it is a frame that eats clicks on the live view."""
    for y in (40, 200, 700):
        assert hit_region(600, y, WINDOW) == CLIENT


def test_every_region_maps_to_a_hit_test_code():
    """A region with no code would be returned to Windows as None and
    silently become HTNOWHERE, which is a window that ignores the mouse."""
    from darlaston.ui.win_frame import _HT

    every = EDGES | BUTTONS | {CAPTION, CLIENT}
    assert every <= set(_HT), f"no code for {sorted(every - set(_HT))}"


def test_the_border_is_wider_than_it_looks():
    """SM_CXSIZEFRAME alone is the visible frame and is too thin to grab;
    Windows adds SM_CXPADDEDBORDER, which is why a standard window's edge
    is easier to hit than it appears."""
    from darlaston.ui.win_frame import border_thickness

    at96 = border_thickness(96)
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
    # never swallows a click on them.
    (start, span), = reserved
    assert start == 0 and span >= bar.wordmark.x() + bar.wordmark.width()

    frame = Frame(width=bar.width(), height=800, bar=36, border=8,
                  buttons=buttons, reserved=reserved)
    assert hit_region(bar.wordmark.x() + 4, 18, frame) == CLIENT
    assert hit_region(buttons[2][0] + 4, 18, frame) == CLOSE
    assert hit_region(span + 40, 18, frame) == CAPTION
    bar.close()


def test_the_caption_buttons_take_no_mouse_events(qapp):
    """Windows routes their area through non-client messages once hit
    testing claims it, so a button that also tried to handle clicks would
    be handling ones it never receives, and looking dead."""
    from darlaston.ui.shell import ToolBar
    from PySide6 import QtCore

    bar = ToolBar()
    for button in bar.caption.values():
        assert button.testAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)


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
