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

import re
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


def _render(name: str, colour: str, size: int) -> QtGui.QPixmap:
    """Rasterise one mark, onto a surface we control.

    Through QSvgRenderer and an explicitly transparent ARGB image rather
    than QPixmap.loadFromData: that path returns whatever the platform's
    pixmap format happens to be, and where it comes back without an alpha
    channel every transparent pixel resolves to opaque black -- which is
    a mark that reads as a black square on any light surface it is drawn
    against. Reported on real hardware, not reproducible offscreen, so it
    is fixed by not depending on the answer.
    """
    from PySide6 import QtSvg

    # Hold the stroke at a constant *device* width. The art is drawn on a
    # 16px grid at 1.2, so at 12px it lands at 0.9 of a pixel, antialiases
    # to about seventy per cent alpha, and reads as faint rather than as
    # thin -- which is half of why these looked invisible.
    source = tinted(name, colour).decode("utf-8")
    scale = 16.0 / max(size, 1)
    source = re.sub(r'stroke-width="([\d.]+)"',
                    lambda m: f'stroke-width="{float(m.group(1)) * scale:.3f}"',
                    source)

    image = QtGui.QImage(size, size,
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(source.encode("utf-8")))
    if not renderer.isValid():
        return QtGui.QPixmap()
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QtGui.QPixmap.fromImage(image)


@lru_cache(maxsize=64)
def icon(name: str, colour: str, size: int = 16) -> QtGui.QIcon:
    """A QIcon of `name`, stroked in `colour`."""
    pix = _render(name, colour, size)
    return QtGui.QIcon() if pix.isNull() else QtGui.QIcon(pix)


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
        pix = _render(name, shade, size)
        if pix.isNull():
            return QtGui.QIcon()
        both.addPixmap(pix, mode)
    return both
