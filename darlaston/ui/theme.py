"""Palette, type and stylesheet.

Three decisions, each with a reason:

  * **Brass accent.** It never appears in a micrograph -- phase backgrounds are
    cyan, darkfield is neutral on black -- so the interface cannot compete with
    the image it exists to serve.
  * **Red means exactly one thing:** clipping and faults. A palette that spends
    red on decoration cannot warn with it.
  * **Dark only.** Used in a dark room beside a bright eyepiece. A light theme
    here would be a decision made for the screenshot, not the session.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

# ---- palette --------------------------------------------------------------
BG = "#101210"        # ground, green-grey biased: the inside of an instrument
PANEL = "#191c19"
SUNK = "#0b0d0b"
LINE = "#2a2e29"
INK = "#e4e7e0"
DIM = "#757a72"
BRASS = "#c89b4a"     # accent, and active state
BRASS_LIT = "#d7ad60"  # the same, under the pointer
BRASS_DEEP = "#ab8339"  # and pressed
GOOD = "#6fa96f"      # in focus, healthy link
BAD = "#d0605e"       # clipping and faults, nothing else

FONT_DIR = Path(__file__).parent / "fonts"

_families: dict[str, str] = {}


def load_fonts() -> dict[str, str]:
    """Register the bundled faces. Falls back to system stacks if absent.

    IBM Plex, OFL 1.1. Chosen because it was drawn for an engineering company's
    technical material -- slightly mechanical, excellent tabular figures -- and
    because it is not the face every generated interface reaches for.
    """
    global _families
    if _families:
        return _families
    # QFontDatabase segfaults without a QApplication rather than raising, so
    # refuse politely instead of taking the process down.
    from PySide6 import QtWidgets
    if QtWidgets.QApplication.instance() is None:
        # Every role, including display. Returning a short dict here made
        # this an ordering trap rather than a fallback: app_icon() reads
        # families["display"], so it raised KeyError when it happened to
        # run before anything had built a QApplication, and passed in any
        # order where something had.
        return {"mono": "monospace", "sans": "sans-serif",
                "display": "sans-serif"}
    wanted = {
        "mono": ["IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf"],
        "sans": ["IBMPlexSans-Regular.ttf", "IBMPlexSans-SemiBold.ttf"],
        # The wordmark only. One face, used once, so it can afford to be
        # a period display letter where nothing else could.
        "display": ["Wordmark.ttf"],
    }
    found: dict[str, str] = {}
    for role, files in wanted.items():
        for name in files:
            path = FONT_DIR / name
            if not path.exists():
                continue
            fid = QtGui.QFontDatabase.addApplicationFont(str(path))
            if fid < 0:
                continue
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid)
            if fams and role not in found:
                found[role] = fams[0]
    _families = {
        "mono": found.get("mono", "monospace"),
        "sans": found.get("sans", "sans-serif"),
        # Falls back to the body face rather than to a system serif: an
        # unpredictable substitute in the one place the name appears is
        # worse than the name simply set in the face we already ship.
        "display": found.get("display", found.get("sans", "sans-serif")),
    }
    return _families


class _OurIconsOnly(QtWidgets.QProxyStyle):
    """Refuses the platform's icons on standard dialog buttons.

    Whether a QDialogButtonBox decorates its buttons is a style *hint*,
    answered by the platform: off under the offscreen platform, on under
    GNOME and KDE, where it contributes icons drawn for a light desktop.
    A black cross on a dark button.

    The stylesheet has a property for this, but it only reaches dialogs
    that apply our stylesheet. Answering the hint reaches every one of
    them, including any written later by somebody who forgets to.
    """

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QtWidgets.QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        return super().styleHint(hint, option, widget, returnData)


def install(app) -> None:
    """Apply what has to be set on the application rather than a widget."""
    app.setStyle(_OurIconsOnly(app.style()))


#: Sizes a window manager, taskbar, dock or alt-tab switcher is likely to
#: ask for, plus what an .icns wants. Rasterised at each rather than scaled
#: from one: the slots are thin at the small end and a resample turns them
#: to speckle.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)

#: The aperture: a brass annulus cut by five slots around a pentagonal
#: opening. It is a diatom valve and a lens iris at the same time, which is
#: the subject and the instrument in one shape.
#:
#: Every number here was chosen against a 16 px render magnified with
#: nearest-neighbour, because that is where icons are actually judged and
#: where the obvious version of this dies. What was learned:
#:
#:   * Detail has to live in *negative space*. Twelve stroked ribs, which
#:     is what a diatom really looks like, alias into noise below 32 px.
#:     Solid areas survive a resample; thin lines do not.
#:   * Five, not six. A hexagon at 32 px reads as a nut or a bolt head.
#:     Odd symmetry reads as nothing else in a dock, which is the whole
#:     job of an icon at that size.
#:   * The slots are phase-offset by half a step so no slot lines up with
#:     a vertex of the opening. Aligned, the two dark shapes merge into
#:     one smear small.
BLADES = 5
#: Radii as a fraction of the disc, and the slot width in degrees.
OPENING = 0.46
SLOT_DEGREES = 10.0
DISC = 0.36


def _aperture(size: int) -> QtGui.QImage:
    """One rasterisation of the mark, at `size` square."""
    image = QtGui.QImage(size, size,
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setPen(QtCore.Qt.PenStyle.NoPen)

    # The plate. A rounded square rather than a full-bleed one because
    # macOS expects the art to *be* that shape -- it applies no mask of
    # its own -- and Windows is happy either way.
    p.setBrush(QtGui.QColor(PANEL))
    radius = size * 0.225
    p.drawRoundedRect(QtCore.QRectF(0, 0, size, size), radius, radius)

    centre = size / 2.0
    disc = size * DISC
    p.setBrush(QtGui.QColor(BRASS))
    p.drawEllipse(QtCore.QPointF(centre, centre), disc, disc)

    # Slots, as filled wedges rather than strokes: a stroked line has a
    # width that has to survive scaling and a filled shape does not.
    p.setBrush(QtGui.QColor(PANEL))
    inner, outer = disc * (OPENING + 0.08), disc * 1.02
    for i in range(BLADES):
        angle = 360.0 * i / BLADES + 180.0 / BLADES
        wedge = QtGui.QPainterPath()
        out_box = QtCore.QRectF(centre - outer, centre - outer,
                                2 * outer, 2 * outer)
        in_box = QtCore.QRectF(centre - inner, centre - inner,
                               2 * inner, 2 * inner)
        wedge.arcMoveTo(out_box, angle - SLOT_DEGREES / 2)
        wedge.arcTo(out_box, angle - SLOT_DEGREES / 2, SLOT_DEGREES)
        wedge.arcTo(in_box, angle + SLOT_DEGREES / 2, -SLOT_DEGREES)
        wedge.closeSubpath()
        p.drawPath(wedge)

    # The opening, point up.
    opening = QtGui.QPainterPath()
    for i in range(BLADES):
        angle = math.radians(360.0 * i / BLADES - 90.0)
        point = QtCore.QPointF(centre + math.cos(angle) * disc * OPENING,
                               centre + math.sin(angle) * disc * OPENING)
        opening.moveTo(point) if i == 0 else opening.lineTo(point)
    opening.closeSubpath()
    p.drawPath(opening)
    p.end()
    return image


def app_icon() -> QtGui.QIcon:
    """The mark, at every size a platform asks for.

    Drawn rather than shipped as a folder of PNGs, so the dock, the
    taskbar, the About window and the installers cannot drift apart. The
    files the platforms need are generated from this same function; see
    packaging/make_icons.py.
    """
    # Not strictly needed now the mark carries no type, but kept: a
    # QApplication is required for QImage on some platforms and this is
    # called early during startup.
    if QtWidgets.QApplication.instance() is None:
        return QtGui.QIcon()
    icon = QtGui.QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QtGui.QPixmap.fromImage(_aperture(size)))
    return icon


def identify(app) -> None:
    """Say who we are, everywhere a platform asks.

    Qt's default is the literal string "PySideApp", and with no icon set at
    all every title bar, taskbar, dock and alt-tab switcher on a platform
    that draws them shows the interpreter's own mark. None of that is
    visible on a window manager that draws no decorations, which is exactly
    why it survived this long.

    The desktop file name is the freedesktop hook: under Wayland it is the
    only way the compositor can match a window to an installed icon, and
    without it the window gets a generic placeholder however good the icon
    we set here is.
    """
    app.setApplicationName("darlaston")
    app.setApplicationDisplayName("darlaston")
    app.setDesktopFileName("darlaston")
    app.setWindowIcon(app_icon())


#: DWM's dark-frame attribute. 20 from Windows 10 build 18985 onwards, 19
#: on the builds just before it, and the call simply fails on anything
#: older -- which is the whole error handling this needs.
_DWM_DARK = (20, 19)


#: How far the traffic lights reach across the top left of a macOS
#: window, plus a little air. The toolbar is inset by this so the wordmark
#: does not sit under them once the title bar is transparent.
MACOS_LIGHTS = 78

#: NSWindowStyleMaskFullSizeContentView, and NSWindowTitleHidden.
_NS_FULL_SIZE_CONTENT = 1 << 15
_NS_TITLE_HIDDEN = 1

#: Set this to anything to leave the frame exactly as the platform drew
#: it. An escape hatch, because this reaches past Qt into AppKit and a
#: future macOS is allowed to disagree with it.
NATIVE_FRAME_ENV = "DARLASTON_NATIVE_FRAME"


def _mac_unify_titlebar(window) -> bool:
    """Make the title bar transparent and run the content up under it.

    macOS draws a grey strip above the window saying the application's
    name, which on a program with its own toolbar is a second bar saying
    less. The usual answer is to go frameless, and the usual cost is
    reimplementing everything the frame does: full screen, zoom on double
    click, snapping, the window menu, and the traffic lights themselves.

    This is the other answer, and it is what every Mac application with a
    custom toolbar actually does. The window keeps its real title bar and
    every gesture that comes with it; the bar is simply made transparent,
    its title hidden, and the content view told it may extend the whole
    height. The traffic lights stay exactly where a Mac user reaches for
    them.

    Qt has no API for any of it, so it is three Objective-C messages
    through ctypes. No new dependency: libobjc is part of the system, and
    `winId()` on macOS is already an NSView pointer.
    """
    import ctypes
    import ctypes.util

    path = ctypes.util.find_library("objc")
    if not path:
        return False
    objc = ctypes.cdll.LoadLibrary(path)

    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    def send(receiver, selector, *args, restype=ctypes.c_void_p, argtypes=()):
        """One objc_msgSend call, with its signature declared.

        The signature has to be set per call rather than once: objc_msgSend
        is variadic, and on arm64 a variadic call passes arguments
        differently from a normal one. Declaring the real types is what
        makes the same code correct on both architectures.
        """
        fn = objc.objc_msgSend
        fn.restype = restype
        fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
        return fn(ctypes.c_void_p(receiver),
                  ctypes.c_void_p(objc.sel_registerName(selector)), *args)

    view = int(window.winId())
    if not view:
        return False
    ns_window = send(view, b"window")
    if not ns_window:
        return False

    send(ns_window, b"setTitlebarAppearsTransparent:", ctypes.c_bool(True),
         restype=None, argtypes=[ctypes.c_bool])
    send(ns_window, b"setTitleVisibility:", ctypes.c_long(_NS_TITLE_HIDDEN),
         restype=None, argtypes=[ctypes.c_long])
    mask = send(ns_window, b"styleMask", restype=ctypes.c_ulong)
    send(ns_window, b"setStyleMask:",
         ctypes.c_ulong(mask | _NS_FULL_SIZE_CONTENT),
         restype=None, argtypes=[ctypes.c_ulong])
    return True


def match_frame(window) -> bool:
    """Ask the platform for a title bar that matches the program.

    **Windows** draws the frame in the *system* light or dark setting
    rather than the application's, so a near-black program on a
    light-themed machine gets a white strip across the top of it. One
    documented DWM attribute settles that.

    **macOS** gets its title bar made transparent, with the content run up
    underneath -- see `_mac_unify_titlebar`. The window keeps its traffic
    lights and every native gesture.

    **X11 and Wayland** are left alone: decorations belong to the window
    manager there by design, and taking them over would be arguing with
    the desktop rather than fitting into it.

    Returns whether anything was changed. Never raises: a frame that could
    not be restyled is a cosmetic disappointment, and taking the window
    down over it would not be.
    """
    import os
    import sys

    if os.environ.get(NATIVE_FRAME_ENV):
        return False
    try:
        if sys.platform == "darwin":
            return _mac_unify_titlebar(window)
        if not sys.platform.startswith("win"):
            return False
        import ctypes

        handle = ctypes.c_void_p(int(window.winId()))
        enabled = ctypes.c_int(1)
        for attribute in _DWM_DARK:
            ok = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle, ctypes.c_uint(attribute), ctypes.byref(enabled),
                ctypes.sizeof(enabled))
            if ok == 0:
                return True
    except Exception:
        return False
    return False


def stylesheet() -> str:
    fam = load_fonts()
    from . import icons
    chevron = icons.path_for("chevron-down", INK)
    chevron_off = icons.path_for("chevron-down", LINE)
    return f"""
    QWidget {{
        background: {BG}; color: {INK};
        font-family: "{fam['sans']}"; font-size: 12px;
    }}
    QLabel, QCheckBox {{ background: transparent; }}
    QLabel[role="mono"], QLabel[role="value"], QLabel[role="key"] {{
        font-family: "{fam['mono']}";
    }}
    QLabel[role="key"] {{ color: {DIM}; }}
    QLabel[role="value"] {{ color: {INK}; }}
    QLabel[role="label"] {{
        font-family: "{fam['mono']}"; color: {DIM};
        font-size: 10px; letter-spacing: 1.4px;
    }}
    QLabel[role="title"] {{ font-family: "{fam['mono']}"; color: {INK}; }}
    QLabel[role="sub"] {{ font-family: "{fam['mono']}"; color: {DIM}; }}
    QLabel[role="heading"] {{ font-size: 15px; font-weight: 600; color: {INK}; }}
    QLabel[role="body"] {{ color: {DIM}; font-size: 12.5px; }}
    QLabel[role="advice"] {{ color: {BRASS}; font-size: 12px; }}
    QLabel[role="fault"] {{ font-size: 15px; font-weight: 600; color: {BAD}; }}

    /* Bordered widgets are inset by a pixel throughout.
       On a fractional device pixel ratio -- any HiDPI display with
       non-integer scaling -- a border flush against the widget's clip
       boundary loses its outermost device pixel, and the left edge simply
       does not draw. Measured: at 1.5x the left column rendered at 151
       against 236 on the right; inset by one pixel it renders at 239.
       A thicker border does not help, because the problem is the boundary
       rather than the weight. */
    QFrame[role="panel"] {{
        background: {PANEL}; border: 1px solid {LINE}; border-radius: 4px;
        margin: 1px;
    }}
    QFrame[role="bar"] {{
        background: {PANEL}; border: 0; border-bottom: 1px solid {LINE};
    }}
    /* The caption strip sits inside the bar and has to let it through.
       Without this the blanket QWidget rule above paints it the window's
       colour, and the window buttons sit in a darker patch. */
    QWidget[role="caption"] {{ background: transparent; }}

    QPushButton {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; margin: 1px;
        padding: 6px 12px; color: {INK}; background: transparent;
    }}
    QPushButton:hover {{ border-color: {DIM}; }}
    QPushButton:focus {{ border-color: {BRASS}; outline: none; }}
    QPushButton[role="primary"] {{ border-color: {BRASS}; color: {BRASS}; }}
    QPushButton[role="seg"] {{
        padding: 3px 0; font-size: 11px; color: {DIM};
    }}
    QPushButton[role="seg"]:checked {{
        border-color: {BRASS}; color: {INK}; background: {BRASS};
    }}
    QPushButton[role="seg"]:checked:hover {{ background: {BRASS_LIT}; }}
    QPushButton[role="step"] {{
        padding: 2px 9px; font-size: 13px; min-width: 0;
    }}

    /* Standard dialog buttons pull an icon from the *system* icon theme
       -- a black tick or cross on a desktop whose icons were drawn for a
       light one. It does not appear on every platform, which is why it
       was invisible from here: the style hint is off under the offscreen
       platform and on under GNOME and KDE. Turned off rather than
       replaced, because "Close" is already a word. */
    QDialogButtonBox {{ dialogbuttonbox-buttons-have-icons: 0; }}

    /* Text fields were never styled at all, so every one of them drew a
       bright platform-blue focus ring -- the only saturated colour in the
       application, on the one widget you look at while typing. */
    QLineEdit {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; margin: 1px;
        padding: 5px 7px; background: {SUNK}; color: {INK};
        selection-background-color: {LINE}; selection-color: {INK};
    }}
    QLineEdit:focus {{ border-color: {BRASS}; }}
    QLineEdit:disabled {{ color: {LINE}; border-color: {LINE}; }}
    QLineEdit[readOnly="true"] {{ color: {DIM}; background: transparent; }}

    QComboBox {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; padding: 4px 6px;
        margin: 1px;
    }}
    QComboBox:focus {{ border-color: {BRASS}; }}
    /* Qt's default is a filled triangle, which was the heaviest mark on
       screen and attached to the least important control. */
    QComboBox::drop-down {{ border: 0; width: 16px; }}
    QComboBox::down-arrow {{ image: url({chevron}); width: 12px;
                             height: 12px; }}
    QComboBox::down-arrow:disabled {{ image: url({chevron_off}); }}
    QComboBox QAbstractItemView {{
        background: {PANEL}; border: 1px solid {LINE};
        selection-background-color: {LINE};
    }}
    QCheckBox {{ font-family: "{fam['mono']}"; spacing: 7px; }}
    QCheckBox::indicator {{
        width: 12px; height: 12px; border: 1px solid {LINE}; border-radius: 2px;
        margin: 1px;
    }}
    QCheckBox::indicator:checked {{ background: {BRASS}; border-color: {BRASS}; }}
    /* Radios were unstyled, which on this background left the unchosen ones
       all but invisible -- a set of options you cannot see is not a choice. */
    QRadioButton {{ font-family: "{fam['mono']}"; spacing: 7px;
                    background: transparent; }}
    QRadioButton::indicator {{
        width: 12px; height: 12px; border: 1px solid {LINE}; border-radius: 7px;
        margin: 1px;
    }}
    QRadioButton::indicator:checked {{ background: {BRASS};
                                       border-color: {BRASS}; }}
    QRadioButton::indicator:hover {{ border-color: {BRASS}; }}

    QSlider::groove:horizontal {{ height: 3px; background: {SUNK};
                                  border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {INK}; width: 11px;
                                  margin: -5px 0; border-radius: 5px; }}
    QSlider::handle:horizontal:hover {{ background: {BRASS}; }}

    /* Nothing was styled for being disabled, so a control that had gone
       dead looked exactly like one that worked. With no camera attached
       the rail sat fully lit -- sliders for an exposure that did not
       exist, toggles for a focus nothing was measuring -- and read as an
       application that was broken rather than one that was waiting. */
    QSlider::groove:horizontal:disabled {{ background: {SUNK}; }}
    QSlider::handle:horizontal:disabled {{ background: {LINE}; }}
    QPushButton:disabled {{ color: {LINE}; border-color: {LINE}; }}
    QPushButton[role="seg"]:disabled {{ color: {LINE};
                                        border-color: {LINE}; }}
    QPushButton[role="seg"]:checked:disabled {{ color: {LINE};
                                                border-color: {LINE};
                                                background: transparent; }}
    QLabel:disabled {{ color: {LINE}; }}
    QCheckBox:disabled {{ color: {LINE}; }}
    QCheckBox:disabled::indicator {{ border-color: {LINE}; }}

    /* Qt's default tooltip is black on grey, which is unreadable against a
       dark interface and looks like it belongs to a different application. */
    QToolTip {{
        background: {PANEL}; color: {INK};
        border: 1px solid {BRASS}; border-radius: 3px; margin: 1px;
        padding: 6px 8px; font-family: "{fam['sans']}"; font-size: 12px;
        opacity: 240;
    }}

    QMenu {{
        background: {PANEL}; border: 1px solid {LINE};
        padding: 4px; font-family: "{fam['mono']}"; font-size: 12px;
    }}
    QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 2px; }}
    QMenu::item:selected {{ background: {LINE}; color: {BRASS}; }}

    /* Lists of things you own -- microscopes, cameras. Without these the
       selection is the platform's own blue, which is the one colour in the
       window that belongs to somebody else's design language. */
    QListWidget {{
        background: {SUNK}; border: 1px solid {LINE}; border-radius: 3px;
        padding: 3px;
    }}
    QListWidget::item {{ padding: 5px 8px; border-radius: 2px; }}
    QListWidget::item:hover {{ color: {INK}; }}
    QListWidget::item:selected {{ background: {LINE}; color: {BRASS}; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}

    QPushButton[role="menu"] {{
        border: 0; margin: 0; padding: 5px 10px; color: {DIM};
        font-family: "{fam['mono']}"; font-size: 12px;
    }}
    QPushButton[role="menu"]:hover {{ color: {INK}; }}

    /* The name sits in brass and goes white under the pointer, rather
       than the other way about: it is the one place the application is
       allowed to be a little proud of itself, and brass is the accent
       chosen precisely because it never appears in a micrograph. */
    QPushButton[role="wordmark"] {{
        border: 0; margin: 0; padding: 4px 10px; color: {BRASS};
        font-family: "{fam['display']}"; font-size: 17px;
        letter-spacing: 0.3px;
    }}
    QPushButton[role="wordmark"]:hover {{ color: {INK}; }}
    """
