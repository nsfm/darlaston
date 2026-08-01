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

from PySide6 import QtGui

# ---- palette --------------------------------------------------------------
BG = "#101210"        # ground, green-grey biased: the inside of an instrument
PANEL = "#191c19"
SUNK = "#0b0d0b"
LINE = "#2a2e29"
INK = "#e4e7e0"
DIM = "#757a72"
BRASS = "#c89b4a"     # accent, and active state
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
    }
    return _families


def stylesheet() -> str:
    fam = load_fonts()
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
        border-color: {BRASS}; color: {BRASS};
    }}
    QPushButton[role="step"] {{
        padding: 2px 9px; font-size: 13px; min-width: 0;
    }}

    QComboBox {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; padding: 4px 6px;
        margin: 1px;
    }}
    QComboBox:focus {{ border-color: {BRASS}; }}
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

    QPushButton[role="wordmark"] {{
        border: 0; margin: 0; padding: 4px 10px; color: {DIM};
        font-family: "{fam['sans']}"; font-size: 14px; font-style: italic;
        font-weight: 700; letter-spacing: 0.3px;
    }}
    QPushButton[role="wordmark"]:hover {{ color: {BRASS}; }}
    """
