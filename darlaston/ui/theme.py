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
        return {"mono": "monospace", "sans": "sans-serif"}
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
#: ask for. Rasterised at each rather than scaled from one, because the
#: mark is a letter and a letter scaled down from 256 to 16 is a smudge.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def app_icon() -> QtGui.QIcon:
    """The mark: the wordmark's own initial, brass on the panel colour.

    Drawn rather than shipped as a file. This is the first letter of the
    name in the face the name is already set in, so it is not a new design
    decision -- and generating it means one source of truth for the mark
    instead of a folder of PNGs that drift from the wordmark the moment
    anybody touches either.
    """
    fam = load_fonts()
    icon = QtGui.QIcon()
    for size in ICON_SIZES:
        image = QtGui.QImage(size, size,
                             QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtGui.QColor(0, 0, 0, 0))
        p = QtGui.QPainter(image)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        # A hairline round the plate, as on every dialog in the program.
        # It is also what stops the mark disappearing into a dark taskbar:
        # the panel colour is four levels off the ground, which is right
        # inside the window and invisible outside it. Dropped below 24 px,
        # where a one-pixel border is a sixteenth of the whole icon.
        stroke = size / 32.0 if size >= 24 else 0.0
        p.setPen(QtGui.QPen(QtGui.QColor(BRASS_DEEP), stroke) if stroke
                 else QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(PANEL))
        inset = stroke / 2.0
        radius = size * 0.22
        p.drawRoundedRect(
            QtCore.QRectF(inset, inset, size - stroke, size - stroke),
            radius, radius)

        font = QtGui.QFont(fam["display"])
        # By pixel size, not point: at 16 px a point size is rounded to
        # whatever the screen's DPI makes of it and the letter lands
        # anywhere.
        font.setPixelSize(max(8, int(size * 0.62)))
        p.setFont(font)
        p.setPen(QtGui.QColor(BRASS))
        # Placed off the letter's own ink rather than off the font's line
        # box. This face carries a tall ascender and a deep descender, so
        # centring the box leaves the "d" riding high with its stem in the
        # border; centring the glyph's actual bounding rectangle puts the
        # mark where the eye expects it at every size.
        box = QtCore.QRectF(0, 0, size, size)
        ink = QtGui.QFontMetricsF(font).tightBoundingRect("d")
        p.drawText(QtCore.QPointF(box.center().x() - ink.center().x(),
                                  box.center().y() - ink.center().y()), "d")
        p.end()
        icon.addPixmap(QtGui.QPixmap.fromImage(image))
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


def match_frame(window) -> bool:
    """Ask the platform for a title bar that matches the program.

    Windows draws the frame in the *system* light or dark setting, not the
    application's, so a near-black program on a light-themed machine gets a
    white title bar across the top of it. One documented DWM attribute
    fixes that. Everywhere else this is a no-op: macOS follows the system
    appearance and offers no supported way to disagree, and X11 and Wayland
    decorations belong to the window manager by design.

    Returns whether anything was actually changed, for the test.
    """
    import sys

    if not sys.platform.startswith("win"):
        return False
    import ctypes

    handle = ctypes.c_void_p(int(window.winId()))
    enabled = ctypes.c_int(1)
    for attribute in _DWM_DARK:
        try:
            ok = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle, ctypes.c_uint(attribute), ctypes.byref(enabled),
                ctypes.sizeof(enabled))
        except (AttributeError, OSError):
            return False                 # no dwmapi: older than we support
        if ok == 0:
            return True
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
