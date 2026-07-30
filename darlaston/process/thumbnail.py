"""Handing our own thumbnails to the desktop.

Every DNG darlaston writes carries a preview in IFD0, exactly where the
spec says a reduced-resolution image belongs -- and on a stock Linux
desktop nothing displays it. Measured on Nate's machine: `glycin`, the
thumbnailer GNOME-derived file managers use, sniffs our files as
`image/x-adobe-dng` and has no loader registered for that type, so it
declines before it ever opens them. The file was never the problem.

Nobody else can read our previews cheaply, but we can read them
perfectly, so this is the thumbnailer: parse IFD0, copy out the strip,
scale, write a PNG. It reads *only the header region* rather than the
whole file, which matters because a stitched composite is gigabytes and
a thumbnailer that slurps one would hang the file manager it was meant
to help.

Install with `python -m darlaston.process.thumbnail --install`, which
writes a `.thumbnailer` entry into the user's data directory. Nothing
system-wide is touched.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import cv2
import numpy as np

#: Enough of the file to hold IFD0 and its preview strip for anything we
#: write. Read in one go; if the preview turns out to live past it, the
#: read widens once rather than growing without bound.
HEADER_BYTES = 4 << 20

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8}


def _ifd(data: bytes, at: int) -> dict[int, tuple[int, int, int]]:
    (count,) = struct.unpack_from("<H", data, at)
    out = {}
    for i in range(count):
        tag, typ, cnt, val = struct.unpack_from("<HHII", data, at + 2 + i * 12)
        out[tag] = (typ, cnt, val)
    return out


def _values(data: bytes, entry) -> list[int]:
    typ, cnt, val = entry
    size = _TYPE_SIZE.get(typ, 1) * cnt
    if size <= 4:
        raw = struct.pack("<I", val)[:size]
    else:
        raw = data[val:val + size]
    fmt = {1: "B", 3: "H", 4: "I"}.get(typ)
    if fmt is None or len(raw) < size:
        return [val]
    return list(struct.unpack_from("<" + fmt * cnt, raw))


def preview(path: Path | str) -> np.ndarray:
    """The embedded preview of a DNG darlaston wrote, as BGR uint8."""
    path = Path(path)
    with path.open("rb") as fh:
        data = fh.read(HEADER_BYTES)
        if data[:4] != b"II\x2a\x00":
            raise ValueError(f"{path.name}: not a TIFF darlaston wrote")
        (first,) = struct.unpack_from("<I", data, 4)
        ifd = _ifd(data, first)
        # IFD0 is the preview by construction (NewSubfileType 1). Refuse
        # anything else rather than quietly rendering a 20 MP raw plane.
        if 254 not in ifd or _values(data, ifd[254])[0] != 1:
            raise ValueError(f"{path.name}: IFD0 is not a preview")
        w = _values(data, ifd[256])[0]
        h = _values(data, ifd[257])[0]
        spp = _values(data, ifd[277])[0] if 277 in ifd else 1
        if (_values(data, ifd[259])[0] if 259 in ifd else 1) != 1:
            raise ValueError(f"{path.name}: preview is compressed")
        offset = _values(data, ifd[273])[0]
        count = _values(data, ifd[279])[0]
        if offset + count > len(data):
            fh.seek(offset)
            blob = fh.read(count)
        else:
            blob = data[offset:offset + count]
    flat = np.frombuffer(blob, np.uint8, count=w * h * spp)
    img = flat.reshape(h, w, spp)
    return img[:, :, ::-1] if spp == 3 else cv2.cvtColor(img[:, :, 0],
                                                         cv2.COLOR_GRAY2BGR)


def write_thumbnail(source: Path | str, target: Path | str,
                    size: int = 256) -> Path:
    img = preview(source)
    h, w = img.shape[:2]
    scale = min(1.0, size / max(w, h))
    if scale < 1.0:
        img = cv2.resize(img, (max(1, round(w * scale)),
                               max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA)
    target = Path(target)
    cv2.imwrite(str(target), img)
    return target


_ENTRY = """[Thumbnailer Entry]
TryExec={python}
Exec={python} {script} %i %o %s
MimeType=image/x-adobe-dng;image/tiff;
"""


def install() -> Path:
    """Register this as the desktop's thumbnailer for DNG files.

    The entry runs this file as a *script*, not as `-m darlaston...`: the
    file manager launches it with no PYTHONPATH and the package is not
    installed into the interpreter, so the module form fails. Everything
    above is import-clean for that reason -- no relative imports, nothing
    from the rest of the package.
    """
    home = Path.home() / ".local/share/thumbnailers"
    home.mkdir(parents=True, exist_ok=True)
    target = home / "darlaston-dng.thumbnailer"
    target.write_text(_ENTRY.format(python=sys.executable,
                                    script=Path(__file__).resolve()))
    return target


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--install":
        path = install()
        print(f"installed {path}\n"
              "Log out and back in, or restart the file manager, and clear\n"
              f"stale entries with: rm -rf {Path.home()}/.cache/thumbnails")
        return 0
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: python -m darlaston.process.thumbnail IN OUT [SIZE]")
        print("       python -m darlaston.process.thumbnail --install")
        return 2
    source, target = argv[0], argv[1]
    size = int(argv[2]) if len(argv) > 2 else 256
    # A URI is what the .thumbnailer contract passes for %i.
    if source.startswith("file://"):
        from urllib.parse import unquote, urlparse
        source = unquote(urlparse(source).path)
    try:
        write_thumbnail(source, target, size)
    except Exception as exc:                  # a thumbnailer must not shout
        print(f"darlaston-thumbnail: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
