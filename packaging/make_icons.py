#!/usr/bin/env python3
"""Write the platform icon files, from the same code that draws the mark.

Windows wants a `.ico` and macOS an `.icns`. Both are containers of PNGs
with a small header, so both are written here rather than by shelling out
to ImageMagick or `iconutil` -- neither of which exists on all three build
machines, and one of which is macOS only.

Generated rather than committed as art, so the dock icon, the taskbar
icon, the About window and the window itself cannot drift apart. A test
regenerates them and compares bytes, so a change to the mark that is not
carried into the files fails rather than shipping half applied.

    python packaging/make_icons.py            write them
    python packaging/make_icons.py --check    say whether they are current
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "icons"

#: Windows reads these from an .ico. 256 is the largest it uses and the
#: first size that must be stored as PNG rather than as a bitmap.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: OSType codes for the .icns entries, and the pixel size each holds.
#: Apple's set: `icp*` are the small ones, `ic07` upwards the large, and
#: the `@2x` entries are simply the next size up under a different code.
ICNS_ENTRIES = (
    (b"icp4", 16), (b"icp5", 32), (b"icp6", 64),
    (b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
    (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512),
)


def _png(size: int) -> bytes:
    """The mark at `size`, as PNG bytes."""
    from PySide6 import QtCore, QtWidgets

    from darlaston.ui import theme

    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication([])
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    theme._aperture(size).save(buffer, "PNG")
    return bytes(buffer.data())


def ico(sizes=ICO_SIZES) -> bytes:
    """A Windows icon: a directory of PNGs.

    PNG rather than the older BMP-with-mask form. Everything since Vista
    reads it, it is a third of the size, and the BMP form needs a
    hand-built AND mask that is a reliable source of half-transparent
    edges.
    """
    images = [_png(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory, body = b"", b""
    for size, data in zip(sizes, images):
        # 0 means 256 in this field, which is a byte.
        stored = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32,
                                 len(data), offset)
        offset += len(data)
        body += data
    return header + directory + body


def icns() -> bytes:
    """A macOS icon: typed chunks, big-endian, with the total up front."""
    chunks = b""
    for code, size in ICNS_ENTRIES:
        data = _png(size)
        chunks += code + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(chunks) + 8) + chunks


def write(directory: Path = OUT) -> dict[Path, bytes]:
    directory.mkdir(parents=True, exist_ok=True)
    made = {directory / "darlaston.ico": ico(),
            directory / "darlaston.icns": icns(),
            directory / "darlaston.png": _png(256)}
    for path, data in made.items():
        path.write_bytes(data)
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; report whether the files are current")
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    if args.check:
        stale = []
        for name, data in (("darlaston.ico", ico()),
                           ("darlaston.icns", icns()),
                           ("darlaston.png", _png(256))):
            path = OUT / name
            if not path.exists() or path.read_bytes() != data:
                stale.append(name)
        if stale:
            print("out of date: " + ", ".join(stale), file=sys.stderr)
            print("run: python packaging/make_icons.py", file=sys.stderr)
            return 1
        print("icons are current")
        return 0

    for path, data in write().items():
        print(f"{path.relative_to(ROOT)}  {len(data) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
