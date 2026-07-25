#!/usr/bin/env python3
"""Interrogate a ToupTek camera through libtoupcam.so.

Read-only reconnaissance: what bit depth, what raw format, what options the
firmware actually honours. Nothing here changes persistent camera state --
options are set, read back, then restored.
"""

import ctypes as C
import os
import sys
from pathlib import Path

# Search order: explicit argument, environment, then common install locations.
# The SDK is never vendored -- see DISCOVERY.md 4c.
CANDIDATES = [
    "/usr/lib/libtoupcam.so",
    "/usr/local/lib/libtoupcam.so",
    "/lib/x86_64-linux-gnu/libtoupcam.so",
    str(Path.home() / "toup/libtoupcam.so"),
]


def find_lib():
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("TOUPCAM_LIB"):
        return os.environ["TOUPCAM_LIB"]
    # Prefer the newest SDK unpacked under ~/toup, if there is one.
    sdks = sorted(Path.home().glob("toup/sdk-*/linux/x64/libtoupcam.so"))
    if sdks:
        return str(sdks[-1])
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    sys.exit("libtoupcam.so not found. Pass a path, or set TOUPCAM_LIB.")


LIB = find_lib()

# --- option constants (from toupcam.h) ---------------------------------------
OPTIONS = {
    "RAW":             0x04,
    "BITDEPTH":        0x06,
    "TEC":             0x08,
    "LINEAR":          0x09,
    "CURVE":           0x0a,
    "TRIGGER":         0x0b,
    "RGB":             0x0c,
    "COLORMATIX":      0x0d,
    "TECTARGET":       0x0f,
    "FRAMERATE":       0x11,
    "DEMOSAIC":        0x12,
    "DEMOSAIC_VIDEO":  0x13,
    "DEMOSAIC_STILL":  0x14,
    "BINNING":         0x17,
    "CG":              0x19,   # conversion gain: LCG / HCG / HDR
    "PIXEL_FORMAT":    0x1a,
    "FFC":             0x1b,
    "DFC":             0x1d,
    "HEAT":            0x37,
    "FLUSH":           0x3d,
    "HIGH_FULLWELL":   0x55,
    "ISP":             0x5f,
    "FPNC":            0x67,
}

CG_NAMES = {0: "LCG (low conversion gain)", 1: "HCG (high conversion gain)", 2: "HDR"}
TRIGGER_NAMES = {0: "video/free-run", 1: "software", 2: "external", 3: "external+software"}


def hr(v):
    """Render an HRESULT: >=0 is success, negative is a failure code."""
    return "ok" if v >= 0 else f"fail 0x{v & 0xFFFFFFFF:08x}"


def fourcc(v):
    s = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
    return s if s.isprintable() else f"0x{v:08x}"


def main():
    try:
        lib = C.CDLL(LIB)
    except OSError as e:
        sys.exit(f"could not load {LIB}: {e}")

    lib.Toupcam_Version.restype = C.c_char_p
    print(f"library            : {LIB}")
    print(f"SDK version        : {lib.Toupcam_Version().decode()}")

    # EnumV2 fills an array of device structs; over-allocate rather than model it.
    buf = C.create_string_buffer(65536)
    lib.Toupcam_EnumV2.restype = C.c_uint
    n = lib.Toupcam_EnumV2(buf)
    print(f"cameras found      : {n}")
    if n == 0:
        sys.exit("\nno camera on the bus. plug it in and re-run.")

    lib.Toupcam_OpenByIndex.restype = C.c_void_p
    lib.Toupcam_OpenByIndex.argtypes = [C.c_uint]
    h = lib.Toupcam_OpenByIndex(0)
    if not h:
        sys.exit("open failed -- check the udev rule is installed in /etc/udev/rules.d/")
    h = C.c_void_p(h)

    def call(name, *args, restype=C.c_int):
        fn = getattr(lib, name)
        fn.restype = restype
        return fn(h, *args)

    try:
        # --- identity ---------------------------------------------------------
        print("\n--- identity ---")
        for fname, label, size in [
            ("Toupcam_get_SerialNumber", "serial", 64),
            ("Toupcam_get_FwVersion", "firmware", 32),
            ("Toupcam_get_HwVersion", "hardware", 32),
            ("Toupcam_get_FpgaVersion", "fpga", 32),
            ("Toupcam_get_ProductionDate", "prod date", 32),
        ]:
            b = C.create_string_buffer(size)
            r = call(fname, b)
            print(f"{label:<18} : {b.value.decode(errors='replace') if r >= 0 else hr(r)}")

        rev = C.c_ushort()
        call("Toupcam_get_Revision", C.byref(rev))
        print(f"{'revision':<18} : {rev.value}")

        mono = call("Toupcam_get_MonoMode")
        print(f"{'mono sensor':<18} : {'yes' if mono == 0 else 'no (colour/bayer)'}")

        # --- the headline question: bit depth and raw format -------------------
        print("\n--- raw capability ---")
        mbd = call("Toupcam_get_MaxBitDepth")
        print(f"{'max bit depth':<18} : {mbd if mbd >= 0 else hr(mbd)}")

        fcc, bpp = C.c_uint(), C.c_uint()
        r = call("Toupcam_get_RawFormat", C.byref(fcc), C.byref(bpp))
        if r >= 0:
            print(f"{'raw fourcc':<18} : {fourcc(fcc.value)}  (bayer pattern)")
            print(f"{'raw bits/pixel':<18} : {bpp.value}")
        else:
            print(f"{'raw format':<18} : {hr(r)}")

        # --- resolutions ------------------------------------------------------
        print("\n--- resolutions ---")
        nres = call("Toupcam_get_ResolutionNumber")
        for i in range(max(nres, 0)):
            w, ht = C.c_int(), C.c_int()
            call("Toupcam_get_Resolution", C.c_uint(i), C.byref(w), C.byref(ht))
            px, py = C.c_float(), C.c_float()
            call("Toupcam_get_PixelSize", C.c_uint(i), C.byref(px), C.byref(py))
            mp = w.value * ht.value / 1e6
            print(f"  [{i}] {w.value:>5} x {ht.value:<5} ({mp:5.2f} MP)  "
                  f"pixel {px.value:.2f} x {py.value:.2f} um")

        # --- exposure / gain envelope ----------------------------------------
        print("\n--- exposure envelope ---")
        lo, hi, df = C.c_uint(), C.c_uint(), C.c_uint()
        if call("Toupcam_get_ExpTimeRange", C.byref(lo), C.byref(hi), C.byref(df)) >= 0:
            print(f"{'exposure us':<18} : {lo.value} .. {hi.value}  (default {df.value})")
            print(f"{'exposure sec':<18} : {lo.value/1e6:.6f} .. {hi.value/1e6:.2f}")
        glo, ghi, gdf = C.c_ushort(), C.c_ushort(), C.c_ushort()
        if call("Toupcam_get_ExpoAGainRange", C.byref(glo), C.byref(ghi), C.byref(gdf)) >= 0:
            print(f"{'analog gain %':<18} : {glo.value} .. {ghi.value}  (default {gdf.value})")

        # --- which options does this firmware actually honour? ----------------
        print("\n--- supported options ---")
        val = C.c_int()
        supported = {}
        for name, code in OPTIONS.items():
            r = call("Toupcam_get_Option", C.c_uint(code), C.byref(val))
            if r >= 0:
                supported[name] = val.value
                extra = ""
                if name == "CG":
                    extra = f"  <- {CG_NAMES.get(val.value, '?')}"
                elif name == "TRIGGER":
                    extra = f"  <- {TRIGGER_NAMES.get(val.value, '?')}"
                print(f"  {name:<16} 0x{code:02x}  = {val.value}{extra}")
            else:
                print(f"  {name:<16} 0x{code:02x}    unsupported ({hr(r)})")

        # --- can we actually turn raw on? ------------------------------------
        print("\n--- raw mode write test ---")
        for name, code, want in [("RAW", 0x04, 1), ("BITDEPTH", 0x06, 1)]:
            if name not in supported:
                print(f"  {name}: not supported, skipping")
                continue
            before = supported[name]
            w = call("Toupcam_put_Option", C.c_uint(code), C.c_int(want))
            call("Toupcam_get_Option", C.c_uint(code), C.byref(val))
            got = val.value
            print(f"  {name}: {before} -> set {want} -> reads {got}   [{hr(w)}]"
                  f"{'  ** accepted **' if got == want else '  ** REJECTED **'}")
            call("Toupcam_put_Option", C.c_uint(code), C.c_int(before))  # restore

        if "CG" in supported:
            print("\n--- conversion gain write test ---")
            before = supported["CG"]
            for mode in (0, 1, 2):
                call("Toupcam_put_Option", C.c_uint(0x19), C.c_int(mode))
                call("Toupcam_get_Option", C.c_uint(0x19), C.byref(val))
                ok = "accepted" if val.value == mode else "rejected"
                print(f"  CG={mode} ({CG_NAMES[mode]:<28}) {ok}")
            call("Toupcam_put_Option", C.c_uint(0x19), C.c_int(before))

    finally:
        lib.Toupcam_Close.argtypes = [C.c_void_p]
        lib.Toupcam_Close(h)
        print("\nclosed.")


if __name__ == "__main__":
    main()
