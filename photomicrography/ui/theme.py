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

    QFrame[role="panel"] {{
        background: {PANEL}; border: 1px solid {LINE}; border-radius: 4px;
    }}
    QFrame[role="bar"] {{
        background: {PANEL}; border: 0; border-bottom: 1px solid {LINE};
    }}

    QPushButton {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px;
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
    }}
    QComboBox:focus {{ border-color: {BRASS}; }}
    QComboBox QAbstractItemView {{
        background: {PANEL}; border: 1px solid {LINE};
        selection-background-color: {LINE};
    }}
    QCheckBox {{ font-family: "{fam['mono']}"; spacing: 7px; }}
    QCheckBox::indicator {{
        width: 12px; height: 12px; border: 1px solid {LINE}; border-radius: 2px;
    }}
    QCheckBox::indicator:checked {{ background: {BRASS}; border-color: {BRASS}; }}

    QSlider::groove:horizontal {{ height: 3px; background: {SUNK};
                                  border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {INK}; width: 11px;
                                  margin: -5px 0; border-radius: 5px; }}
    QSlider::handle:horizontal:hover {{ background: {BRASS}; }}
    """
