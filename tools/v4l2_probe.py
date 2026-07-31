"""What a V4L2 camera actually offers, asked directly.

The question behind this is whether darlaston could drive ordinary USB
cameras, and the answer turns entirely on one thing: does the device hand
over *raw Bayer*, or only YUYV and MJPEG? A camera that only offers
decoded output cannot feed a DNG writer, cannot be white-balanced
honestly, and cannot be the sensor of record -- so it is worth knowing
before writing a backend rather than after.

Pure ctypes ioctls: no v4l2 module, no new dependency, and the same three
calls a backend would open with.

    PYTHONPATH=. python tools/v4l2_probe.py
"""
from __future__ import annotations

import fcntl
import glob
import struct

# Raw Bayer fourccs V4L2 defines. The 8- and 16-bit ones reach userspace
# through mainline uvcvideo's format-GUID table -- which a developer at
# The Imaging Source upstreamed in 2014 and 2016, and which is why their
# cameras produce Bayer over plain UVC while nobody else's does. There is
# deliberately no packed-12 entry: no GUID for it exists, so packed 12-bit
# cannot arrive this way at all.
BAYER = {
    "BA81": "SBGGR8", "GBRG": "SGBRG8", "GRBG": "SGRBG8", "RGGB": "SRGGB8",
    "BYR2": "SBGGR16", "BG16": "SBGGR16", "GB16": "SGBRG16",
    "RG16": "SRGGB16", "GR16": "SGRBG16",
    "BG10": "SBGGR10", "GB10": "SGBRG10", "BA10": "SGRBG10",
    "RG10": "SRGGB10",
    "BG12": "SBGGR12", "GB12": "SGBRG12", "BA12": "SGRBG12",
    "RG12": "SRGGB12",
}
MONO_RAW = {"Y16 ", "Y10 ", "Y12 ", "Y16"}


def _iowr(nr: int, size: int, direction: int = 3) -> int:
    return (direction << 30) | (size << 16) | (ord("V") << 8) | nr


VIDIOC_QUERYCAP = _iowr(0, 104, 2)
VIDIOC_ENUM_FMT = _iowr(2, 64)
VIDIOC_ENUM_FRAMESIZES = _iowr(74, 44)

CAP_VIDEO_CAPTURE = 0x00000001


def _fourcc(value: int) -> str:
    return "".join(chr((value >> (8 * k)) & 0xFF) for k in range(4))


def capability(fd) -> tuple[str, str, int]:
    buf = bytearray(104)
    fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
    driver = bytes(buf[0:16]).split(b"\0")[0].decode("ascii", "replace")
    card = bytes(buf[16:48]).split(b"\0")[0].decode("ascii", "replace")
    caps, device_caps = struct.unpack_from("<II", buf, 84)
    return driver, card, (device_caps or caps)


def formats(fd) -> list[tuple[str, str, list[str]]]:
    out = []
    for index in range(64):
        buf = bytearray(64)
        struct.pack_into("<II", buf, 0, index, 1)      # index, VIDEO_CAPTURE
        try:
            fcntl.ioctl(fd, VIDIOC_ENUM_FMT, buf, True)
        except OSError:
            break
        description = bytes(buf[12:44]).split(b"\0")[0].decode(
            "ascii", "replace")
        (pixelformat,) = struct.unpack_from("<I", buf, 44)
        out.append((_fourcc(pixelformat), description,
                    sizes(fd, pixelformat)))
    return out


def sizes(fd, pixelformat: int, limit: int = 4) -> list[str]:
    found = []
    for index in range(limit):
        buf = bytearray(44)
        struct.pack_into("<II", buf, 0, index, pixelformat)
        try:
            fcntl.ioctl(fd, VIDIOC_ENUM_FRAMESIZES, buf, True)
        except OSError:
            break
        (kind,) = struct.unpack_from("<I", buf, 8)
        # V4L2_FRMSIZE_TYPE_DISCRETE is 1, not 0 -- comparing against 0
        # sent every ordinary webcam down the stepwise branch and printed
        # heights of zero.
        if kind == 1:                                  # discrete
            w, h = struct.unpack_from("<II", buf, 12)
            found.append(f"{w}x{h}")
        else:
            _minw, maxw, _sw, _minh, maxh = struct.unpack_from("<IIIII",
                                                               buf, 12)
            found.append(f"up to {maxw}x{maxh}")
            break
    return found


def main() -> int:
    nodes = sorted(glob.glob("/dev/video*"))
    if not nodes:
        print("no /dev/video* devices")
        return 0
    verdict = []
    for node in nodes:
        try:
            fd = open(node, "rb", buffering=0)
        except OSError as exc:
            print(f"{node}: {exc}")
            continue
        with fd:
            try:
                driver, card, caps = capability(fd)
            except OSError as exc:
                print(f"{node}: QUERYCAP failed ({exc})")
                continue
            if not caps & CAP_VIDEO_CAPTURE:
                print(f"{node}: {card} [{driver}] — not a capture node")
                continue
            print(f"\n{node}: {card}  [driver {driver}]")
            raw = []
            for fourcc, description, dims in formats(fd):
                kind = ""
                if fourcc in BAYER:
                    kind = f"  <-- RAW BAYER ({BAYER[fourcc]})"
                    raw.append(fourcc)
                elif fourcc in MONO_RAW:
                    kind = "  <-- raw mono"
                    raw.append(fourcc)
                shown = ", ".join(dims) if dims else "?"
                print(f"    {fourcc!r:8s} {description:28s} {shown}{kind}")
            verdict.append((node, card, raw))

    print("\n--- verdict")
    for node, card, raw in verdict:
        if raw:
            print(f"{node} ({card}): raw available — {', '.join(raw)}")
        else:
            print(f"{node} ({card}): no raw. Decoded output only, so it "
                  "cannot feed a DNG writer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
