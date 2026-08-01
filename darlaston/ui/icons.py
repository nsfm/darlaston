"""The small marks: close, chevrons, a pin, a tick.

Drawn rather than borrowed. Qt's defaults are a filled triangle for a
combo box and whatever glyph the fallback font has for a multiplication
sign, and both are heavier than anything else on the screen -- this
interface is built out of one-pixel hairlines, and a solid arrowhead
attached to the least important control was the loudest thing in the
window.

One file per mark, stroked in `currentColor`, tinted here. That keeps
the colours in `theme` where the rest of them live: a second copy of
"dim is #757a72" inside an SVG is a copy that will not be updated.

Qt stylesheets cannot tint an image, so anything reached from CSS --
`QComboBox::drop-down`, mainly -- needs a real file in the colour it
will be drawn in. Those are written once into a temporary directory and
referenced by path. Everything reachable from Python gets a QIcon and no
file at all.
"""
from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6 import QtCore, QtGui

ICON_DIR = Path(__file__).parent / "icons"

_cache_dir: Path | None = None


def _source(name: str) -> str:
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"no icon named {name!r} in {ICON_DIR}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=64)
def tinted(name: str, colour: str) -> bytes:
    """The SVG source with its stroke set to `colour`."""
    return _source(name).replace("currentColor", colour).encode("utf-8")


@lru_cache(maxsize=64)
def path_for(name: str, colour: str) -> str:
    """A file of `name` in `colour`, for a stylesheet `url()`.

    Written to a temporary directory rather than beside the source, so a
    themed copy is never mistaken for an original and nothing stale
    survives into the next run.
    """
    global _cache_dir
    if _cache_dir is None:
        _cache_dir = Path(tempfile.mkdtemp(prefix="darlaston-icons-"))
    out = _cache_dir / f"{name}-{colour.lstrip('#')}.svg"
    if not out.exists():
        out.write_bytes(tinted(name, colour))
    # Qt wants forward slashes inside url(), on every platform.
    return out.as_posix()


@lru_cache(maxsize=64)
def icon(name: str, colour: str, size: int = 16) -> QtGui.QIcon:
    """A QIcon of `name`, stroked in `colour`.

    Rendered to a pixmap at the device ratio rather than handed to Qt as
    an SVG: a QIcon built from SVG data re-rasterises at whatever size it
    is asked for, and these are drawn at one size, at one weight, on
    purpose.
    """
    renderer = QtGui.QPixmap()
    renderer.loadFromData(tinted(name, colour), "SVG")
    if renderer.isNull():                    # no SVG plugin in this build
        return QtGui.QIcon()
    return QtGui.QIcon(renderer.scaled(
        size, size, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        QtCore.Qt.TransformationMode.SmoothTransformation))


def available() -> list[str]:
    """Every mark that exists, for tests and for anybody adding one."""
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))


@lru_cache(maxsize=32)
def hover_icon(name: str, colour: str, hover: str,
               size: int = 16) -> QtGui.QIcon:
    """One icon carrying both its resting and its pointed-at colour.

    Qt draws Mode.Active when the pointer is over the widget, so a button
    can change colour on hover without anything connecting a signal --
    which is how the text version of these worked, and it should not
    regress to something worse just because it became a picture.
    """
    both = QtGui.QIcon()
    for mode, shade in ((QtGui.QIcon.Mode.Normal, colour),
                        (QtGui.QIcon.Mode.Active, hover),
                        (QtGui.QIcon.Mode.Selected, hover)):
        pix = QtGui.QPixmap()
        pix.loadFromData(tinted(name, shade), "SVG")
        if pix.isNull():
            return QtGui.QIcon()
        both.addPixmap(pix.scaled(
            size, size, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation), mode)
    return both
