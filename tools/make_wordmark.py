#!/usr/bin/env python3
"""Write the README's title art, from the same font and colours as the app.

Generated rather than drawn, for the same reason the platform icons are:
the wordmark on the toolbar, the one in the About box and the one at the
top of the README are one design, so they should be one piece of code. A
title image that drifts from the running program is worse than no title
image.

    python tools/make_wordmark.py

Three files, all with transparent backgrounds so they sit on GitHub's
light theme and its dark one without a plate around them:

  * `wordmark.png`  -- the word alone
  * `lockup.png`    -- the aperture mark and the word, side by side
  * `mark.png`      -- the aperture alone, for a favicon or a corner

Brass on transparent is the one palette choice that survives both
themes. The ink grey does not: it is chosen to sit on the instrument
ground and it disappears on white.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

#: Rendered at this cap height in pixels. Large enough that GitHub's
#: 2x displays have pixels to spare, since the README scales it down
#: with a width attribute rather than up.
SIZE = 320

#: Space between the mark and the word in the lockup, as a fraction of
#: the mark's size.
GAP = 0.28


def _app():
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication([])
    return app


def _font(size: int):
    from PySide6 import QtGui

    from darlaston.ui import theme

    families = theme.load_fonts()
    font = QtGui.QFont(families["display"])
    font.setPixelSize(size)
    # Hinting off: this is being scaled down by the browser, and hinted
    # stems at one size are wrong at every other size.
    font.setHintingPreference(QtGui.QFont.HintingPreference.PreferNoHinting)
    return font


def wordmark(size: int = SIZE):
    """The word, in the display face, in brass, on nothing."""
    from PySide6 import QtCore, QtGui

    from darlaston.ui import theme

    text = "darlaston"
    font = _font(size)
    metrics = QtGui.QFontMetrics(font)
    # tightBoundingRect, not boundingRect: the latter includes the font's
    # line spacing, which here is a band of transparent pixels above and
    # below the word that nobody can see and everybody has to align
    # around.
    bounds = metrics.tightBoundingRect(text)
    pad = max(2, size // 24)          # room for the descender's overshoot

    image = QtGui.QImage(bounds.width() + pad * 2, bounds.height() + pad * 2,
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
    p.setFont(font)
    p.setPen(QtGui.QColor(theme.BRASS))
    p.drawText(QtCore.QPoint(pad - bounds.left(), pad - bounds.top()), text)
    p.end()
    return image


def mark(size: int = SIZE):
    """The aperture, from the code that draws the application icon."""
    from darlaston.ui import theme

    return theme._aperture(size)


def lockup(size: int = SIZE):
    """The mark and the word, side by side and the same height.

    The mark is sized from the *rendered* word rather than from the
    nominal point size, because the two are not the same number: the word
    is measured with a tight bounding box, so its height is the ink and
    not the font's line box. Sizing the badge to the point size instead
    happened to land within a few pixels here, which is the kind of
    agreement that stops the moment anybody changes the face.
    """
    from PySide6 import QtGui

    word = wordmark(size)
    badge = mark(word.height())
    gap = int(badge.width() * GAP)

    height = max(word.height(), badge.height())
    image = QtGui.QImage(badge.width() + gap + word.width(), height,
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage(0, (height - badge.height()) // 2, badge)
    p.drawImage(badge.width() + gap, (height - word.height()) // 2, word)
    p.end()
    return image


def write(directory: Path = OUT, size: int = SIZE) -> dict[Path, tuple]:
    directory.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, image in (("wordmark.png", wordmark(size)),
                        ("lockup.png", lockup(size)),
                        ("mark.png", mark(size))):
        path = directory / name
        image.save(str(path), "PNG")
        made[path] = (image.width(), image.height())
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--size", type=int, default=SIZE,
                    help=f"cap height in pixels (default {SIZE})")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    _app()
    from darlaston.ui import theme
    theme.load_fonts()
    for path, (w, h) in write(size=args.size).items():
        print(f"{path.relative_to(ROOT)}  {w}x{h}  "
              f"{path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
