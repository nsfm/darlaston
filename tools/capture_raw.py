#!/usr/bin/env python3
"""Capture the same field twice -- once through the ISP, once as 12-bit raw.

This is the experiment the whole project rests on. Everything downstream
assumes raw is a real improvement over what ToupLite writes; this produces the
two files to compare, plus the numbers.

    capture_raw.py [--exposure US] [--gain PCT] [--res N] [--out DIR]

Writes to DIR (default ./spike):
    isp_rgb.png       what the hardware pipeline produces (ToupLite-equivalent)
    raw_bayer.tif     16-bit container, 12 significant bits, undemosaiced
    raw_demosaic_*.png   all four Bayer interpretations, to settle the pattern

Requires the ToupTek SDK on the Python path. See DISCOVERY.md 4c -- it is
never vendored here.
"""
import argparse
import ctypes
import os
import sys
from pathlib import Path

import numpy as np


def load_sdk():
    """Import the vendor's toupcam bindings from a user-installed SDK.

    The official toupcam.py calls LoadLibrary('libtoupcam.so') with a bare
    name, so it only works if the library is already on the loader path.
    Preloading by absolute path with RTLD_GLOBAL works because the library's
    SONAME is exactly 'libtoupcam.so' -- the later bare-name dlopen then
    resolves to the copy already in the process.
    """
    sdks = sorted(Path.home().glob("toup/sdk-*"))
    roots = [Path(os.environ["TOUPCAM_SDK"])] if os.environ.get("TOUPCAM_SDK") \
        else list(reversed(sdks))
    for root in roots:
        lib = root / "linux/x64/libtoupcam.so"
        binding = root / "python"
        if lib.exists() and (binding / "toupcam.py").exists():
            ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            sys.path.insert(0, str(binding))
            return __import__("toupcam"), root
    # Fall back to a system-installed library.
    try:
        return __import__("toupcam"), None
    except ImportError:
        sys.exit("ToupTek SDK not found. Unpack it under ~/toup/sdk-*/ or set "
                 "TOUPCAM_SDK to its root. See README.")


toupcam, _sdk_root = load_sdk()

try:
    import cv2
except ImportError:
    sys.exit("opencv-python required")


RAW_FORMATS = {
    toupcam.TOUPCAM_PIXELFORMAT_RAW8: "RAW8",
    toupcam.TOUPCAM_PIXELFORMAT_RAW10: "RAW10",
    toupcam.TOUPCAM_PIXELFORMAT_RAW11: "RAW11",
    toupcam.TOUPCAM_PIXELFORMAT_RAW12: "RAW12",
    toupcam.TOUPCAM_PIXELFORMAT_RAW14: "RAW14",
    toupcam.TOUPCAM_PIXELFORMAT_RAW16: "RAW16",
}

# OpenCV's Bayer naming is offset from the sensor's own by one pixel, and the
# mapping is a reliable source of confusion. Emit all four and let the image
# decide rather than asserting which is correct.
BAYER_CODES = {
    "BG": cv2.COLOR_BayerBG2BGR, "GB": cv2.COLOR_BayerGB2BGR,
    "RG": cv2.COLOR_BayerRG2BGR, "GR": cv2.COLOR_BayerGR2BGR,
}


def opt(cam, name, code, value):
    """Set an option and report whether it stuck."""
    try:
        cam.put_Option(code, value)
        got = cam.get_Option(code)
        ok = "ok" if got == value else f"REJECTED (reads {got})"
        print(f"  {name:<14} -> {value:<4} {ok}")
        return got == value
    except toupcam.HRESULTException as ex:
        print(f"  {name:<14} -> {value:<4} failed 0x{ex.hr & 0xffffffff:08x}")
        return False


def stats(label, a):
    """Occupancy statistics -- the numbers that matter for shadow detail."""
    mx = float(a.max())
    lo4 = (a <= mx * 4 / 256).sum() / a.size * 100
    print(f"  {label:<22} min {a.min():>6}  max {a.max():>6}  "
          f"mean {a.mean():>8.1f}  distinct {len(np.unique(a)):>6}  "
          f"bottom 1.5% of range: {lo4:5.1f}% of pixels")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--exposure", type=int, default=None, help="microseconds")
    p.add_argument("--gain", type=int, default=100, help="percent, 100 = 1x")
    p.add_argument("--res", type=int, default=0, help="resolution index, 0 = full")
    p.add_argument("--out", default="spike")
    a = p.parse_args()

    out = Path(a.out)
    out.mkdir(exist_ok=True)

    devs = toupcam.Toupcam.EnumV2()
    if not devs:
        sys.exit("no camera found")
    cam = toupcam.Toupcam.Open(devs[0].id)
    if not cam:
        sys.exit("failed to open camera")

    try:
        print(f"camera: {devs[0].displayname}")
        cam.put_eSize(a.res)
        w, h = cam.get_Size()
        print(f"resolution index {a.res}: {w} x {h} ({w*h/1e6:.2f} MP)\n")

        # Fixed exposure and gain. Auto-exposure would make the two captures
        # incomparable, which is the entire point of the experiment.
        cam.put_AutoExpoEnable(0)
        if a.exposure:
            cam.put_ExpoTime(a.exposure)
        cam.put_ExpoAGain(a.gain)
        expo = cam.get_ExpoTime()
        print(f"exposure {expo} us ({expo/1000:.1f} ms), gain {a.gain}%\n")

        # Software trigger: one frame on demand, no callback threading.
        cam.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, 1)

        # --- pass 1: through the hardware ISP, as ToupLite delivers it -------
        print("pass 1 -- hardware ISP (ToupLite-equivalent)")
        opt(cam, "RAW", toupcam.TOUPCAM_OPTION_RAW, 0)
        opt(cam, "BITDEPTH", toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
        cam.StartPullModeWithCallback(None, None)
        buf = bytes(w * h * 3)
        cam.TriggerSync(5000, buf, 24, 0, None)
        isp = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        cv2.imwrite(str(out / "isp_rgb.png"), isp)
        cam.Stop()
        print(f"  wrote {out/'isp_rgb.png'}")
        stats("ISP output (8-bit)", isp)

        # --- pass 2: raw Bayer, ISP bypassed ---------------------------------
        print("\npass 2 -- 12-bit raw Bayer")
        opt(cam, "RAW", toupcam.TOUPCAM_OPTION_RAW, 1)
        opt(cam, "BITDEPTH", toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
        # ISP 0 already auto-disables in RAW mode; -1 forces it off explicitly.
        opt(cam, "ISP", toupcam.TOUPCAM_OPTION_ISP, -1)
        opt(cam, "LINEAR", toupcam.TOUPCAM_OPTION_LINEAR, 0)
        opt(cam, "CURVE", toupcam.TOUPCAM_OPTION_CURVE, 0)
        opt(cam, "COLORMATIX", toupcam.TOUPCAM_OPTION_COLORMATIX, 0)

        fourcc, bpp = cam.get_RawFormat()
        pattern = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
        try:
            pf = cam.get_Option(toupcam.TOUPCAM_OPTION_PIXEL_FORMAT)
            pfname = RAW_FORMATS.get(pf, f"0x{pf:02x}")
        except toupcam.HRESULTException:
            pfname = "?"
        print(f"  bayer pattern {pattern}, {bpp} bits/pixel, format {pfname}")

        cam.StartPullModeWithCallback(None, None)
        buf = bytes(w * h * 2)
        cam.TriggerSync(5000, buf, 16, w * 2, None)
        raw = np.frombuffer(buf, np.uint16).reshape(h, w)
        cam.Stop()

        cv2.imwrite(str(out / "raw_bayer.tif"), raw)
        print(f"  wrote {out/'raw_bayer.tif'}")
        stats("raw Bayer (12-bit)", raw)

        for name, code in BAYER_CODES.items():
            rgb = cv2.cvtColor(raw, code)
            cv2.imwrite(str(out / f"raw_demosaic_{name}.png"),
                        (rgb >> 4).astype(np.uint8))
        print(f"  wrote 4 demosaic candidates -- pick the one with sane colour")

        # --- the comparison --------------------------------------------------
        print("\ntonal resolution in the shadows")
        isp_l = isp.mean(axis=2)
        raw_f = raw.astype(np.float32)
        for label, arr, depth in [("ISP 8-bit", isp_l, 255),
                                  ("raw 12-bit", raw_f, 4095)]:
            frac = (arr <= depth * 4 / 256).sum() / arr.size * 100
            levels = len(np.unique(arr[arr <= depth * 4 / 256]))
            print(f"  {label:<12} bottom 1.5% of range holds {frac:5.1f}% of "
                  f"pixels, spread over {levels:>4} distinct levels")

    finally:
        cam.Close()
        print("\nclosed.")


if __name__ == "__main__":
    main()
