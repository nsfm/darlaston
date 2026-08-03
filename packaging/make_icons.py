#!/usr/bin/env python3
"""Write the platform icon files, from the same code that draws the mark.

Windows wants a `.ico` and macOS an `.icns`. Both are containers of PNGs
with a small header, so both are written here rather than by shelling out
to ImageMagick or `iconutil` -- neither of which exists on all three build
machines, and one of which is macOS only.

Generated rather than committed as art, so the dock icon, the taskbar
icon, the About window and the window itself cannot drift apart. A test
regenerates them and compares, so a change to the mark that is not
carried into the files fails rather than shipping half applied.

**Compared as pixels, not as bytes.** Byte equality looked like the
strictest possible check and was in fact not portable: Qt's raster
engine takes different SIMD paths on different processors, so the same
Qt, the same code and the same inputs produce PNGs that differ in the
last bit of a few antialiased edge pixels. That failed on a build
machine while passing on the machine that wrote it, which is the worst
way for a check to be strict. The containers are unpacked, each image
decoded, and the difference measured -- a redesign moves whole regions
and reads in the tens, while a rounding difference reads near zero.

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


def read_ico(data: bytes) -> list[bytes]:
    """The PNG payloads out of a .ico, in the order they are stored."""
    count = struct.unpack_from("<H", data, 4)[0]
    out = []
    for i in range(count):
        length, offset = struct.unpack_from("<II", data, 6 + 16 * i + 8)
        out.append(data[offset:offset + length])
    return out


def read_icns(data: bytes) -> list[bytes]:
    """The PNG payloads out of an .icns, in the order they are stored."""
    out, at, end = [], 8, struct.unpack_from(">I", data, 4)[0]
    while at < end:
        length = struct.unpack_from(">I", data, at + 4)[0]
        out.append(data[at + 8:at + length])
        at += length
    return out


def difference(one: bytes, two: bytes) -> tuple[float, int]:
    """Mean and worst per-channel difference between two encoded images.

    Returns (0.0, 0) for identical pixels. Mismatched sizes are reported
    as the worst possible difference rather than raising, since that is
    a real answer: the file no longer holds what it should.
    """
    import cv2
    import numpy as np

    a = cv2.imdecode(np.frombuffer(one, np.uint8), cv2.IMREAD_UNCHANGED)
    b = cv2.imdecode(np.frombuffer(two, np.uint8), cv2.IMREAD_UNCHANGED)
    if a is None or b is None or a.shape != b.shape:
        return 255.0, 255
    gap = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(gap.mean()), int(gap.max())


#: How far a stored image may sit from a freshly drawn one and still be
#: the same mark. Two processors rendering the same path disagree by a
#: level or two on antialiased edges and no more; the smallest deliberate
#: change measured -- a fifth of a percent on one geometry constant --
#: moves the mean thirty times this and the worst pixel to 19. So there
#: is a wide gap to sit in, and this sits near the bottom of it.
TOLERANCE_MEAN = 0.005
TOLERANCE_MAX = 8


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
        for name, fresh, unpack in (
                ("darlaston.ico", ico, read_ico),
                ("darlaston.icns", icns, read_icns),
                ("darlaston.png", lambda: _png(256), lambda d: [d])):
            path = OUT / name
            if not path.exists():
                stale.append(f"{name}: missing")
                continue
            try:
                stored = unpack(path.read_bytes())
                wanted = unpack(fresh())
            except Exception as why:
                stale.append(f"{name}: unreadable ({why})")
                continue
            if len(stored) != len(wanted):
                stale.append(f"{name}: {len(stored)} images, expected "
                             f"{len(wanted)}")
                continue
            worst_mean = worst_max = 0.0
            for have, want in zip(stored, wanted):
                mean, peak = difference(have, want)
                worst_mean, worst_max = max(worst_mean, mean), max(worst_max,
                                                                   peak)
            # Printed on the way past whether or not it fails: the number
            # is how anybody knows the tolerance is still the right size.
            print(f"{name}: mean {worst_mean:.4f}, worst {worst_max:.0f}")
            if worst_mean > TOLERANCE_MEAN or worst_max > TOLERANCE_MAX:
                stale.append(f"{name}: mean {worst_mean:.4f} worst "
                             f"{worst_max:.0f}, over "
                             f"{TOLERANCE_MEAN}/{TOLERANCE_MAX}")
        if stale:
            print("does not match the mark: " + "; ".join(stale),
                  file=sys.stderr)
            print("run: python packaging/make_icons.py", file=sys.stderr)
            return 1
        print("icons are current")
        return 0

    for path, data in write().items():
        print(f"{path.relative_to(ROOT)}  {len(data) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
