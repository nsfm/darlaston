#!/usr/bin/env python3
"""Develop a raw Bayer capture into a viewable image.

With raw, white balance stops being a capture decision. It is a develop-time
parameter, changeable forever, non-destructively, per illumination mode. This
uses the vendor's own TempTint2Gain conversion so the Kelvin numbers mean the
same thing they do in ToupLite.

    develop.py <raw_bayer.tif> [options]

    --temp K        colour temperature (default 6503, the SDK's neutral)
    --tint N        tint, 200..2500 (default 1000 = neutral)
    --dark FILE     dark frame to subtract
    --flat FILE     flat field to divide by (per-Bayer-phase normalised)
    --gamma G       display gamma (default 2.2; 1.0 = linear)
    --black N       manual black point in raw counts
    --grid          contact sheet across a range of temperatures instead
    --out FILE      output path (default alongside the input)
"""
import argparse
import ctypes
import sys
from pathlib import Path

import cv2
import numpy as np

MAXVAL = 4095.0


def load_sdk():
    """Only needed for TempTint2Gain -- optional, with a fallback."""
    for root in reversed(sorted(Path.home().glob("toup/sdk-*"))):
        lib, binding = root / "linux/x64/libtoupcam.so", root / "python"
        if lib.exists() and (binding / "toupcam.py").exists():
            ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            sys.path.insert(0, str(binding))
            return __import__("toupcam")
    return None


def temp_tint_gains(toupcam, temp, tint):
    """RGB multipliers for a colour temperature, normalised to green."""
    if toupcam is None:  # crude fallback if the SDK is absent
        k = temp / 6503.0
        return np.array([1.0 / k, 1.0, k], np.float32)
    r, g, b = toupcam.Toupcam.TempTint2Gain(int(temp), int(tint))
    v = np.array([r, g, b], np.float32)
    return v / v[1]


def to_rgb(raw):
    """Bottom-up Bayer buffer -> linear float RGB in 0..1.

    The raw stream arrives bottom-up while the ISP path does not. Flipping an
    even-height frame shifts the Bayer row parity, which is why the delivered
    buffer looks like RGGB while get_RawFormat correctly reports GBRG. Flip
    first, then the sensor's pattern applies -- and OpenCV's naming is offset
    one pixel from the sensor convention, so GBRG needs COLOR_BayerGR2BGR.
    """
    f = np.ascontiguousarray(cv2.flip(raw, 0))
    bgr = cv2.cvtColor(f, cv2.COLOR_BayerGR2BGR).astype(np.float32)
    return bgr[:, :, ::-1] / MAXVAL


def bayer_norm(flat):
    """Normalise a flat per Bayer phase.

    A single scalar is wrong on undemosaiced data: the four phases differ in
    sensitivity, so one norm bakes a 2x2 checkerboard into every frame.
    """
    out = flat.astype(np.float32).copy()
    for dy in (0, 1):
        for dx in (0, 1):
            p = out[dy::2, dx::2]
            out[dy::2, dx::2] = p / max(p.mean(), 1e-6)
    return out


def develop(raw, args, toupcam, dark=None, flat=None):
    x = raw.astype(np.float32)
    if dark is not None:
        x = np.maximum(x - dark.astype(np.float32), 0)
    elif args.black:
        x = np.maximum(x - args.black, 0)
    if flat is not None:
        x = x / np.maximum(bayer_norm(flat), 1e-3)
    rgb = to_rgb(np.clip(x, 0, MAXVAL).astype(np.uint16))
    rgb = np.clip(rgb * temp_tint_gains(toupcam, args.temp, args.tint), 0, 1)
    return (np.power(rgb, 1.0 / args.gamma) * 255).astype(np.uint8)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("raw")
    p.add_argument("--temp", type=int, default=6503)
    p.add_argument("--tint", type=int, default=1000)
    p.add_argument("--dark")
    p.add_argument("--flat")
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--black", type=float, default=0)
    p.add_argument("--grid", action="store_true")
    p.add_argument("--out")
    args = p.parse_args()

    raw = cv2.imread(args.raw, cv2.IMREAD_UNCHANGED)
    if raw is None:
        sys.exit(f"could not read {args.raw}")
    toupcam = load_sdk()
    dark = cv2.imread(args.dark, cv2.IMREAD_UNCHANGED) if args.dark else None
    flat = cv2.imread(args.flat, cv2.IMREAD_UNCHANGED) if args.flat else None

    src = Path(args.raw)
    if args.grid:
        temps = [2500, 3200, 4000, 5000, 6503, 8000]
        tiles = []
        for t in temps:
            args.temp = t
            im = develop(raw, args, toupcam, dark, flat)
            im = cv2.resize(im[:, :, ::-1], None, fx=0.16, fy=0.16,
                            interpolation=cv2.INTER_AREA)
            cv2.rectangle(im, (0, 0), (im.shape[1], 34), (0, 0, 0), -1)
            cv2.putText(im, f"{t}K", (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                        0.75, (255, 255, 255), 2)
            tiles.append(im)
        rows = [np.hstack(tiles[i:i + 3]) for i in (0, 3)]
        out = args.out or str(src.with_name(src.stem + "_wb_grid.jpg"))
        cv2.imwrite(out, np.vstack(rows))
        print(f"wrote {out}  ({', '.join(f'{t}K' for t in temps)})")
        return

    im = develop(raw, args, toupcam, dark, flat)
    out = args.out or str(src.with_name(src.stem + f"_{args.temp}K.jpg"))
    cv2.imwrite(out, im[:, :, ::-1])
    g = temp_tint_gains(toupcam, args.temp, args.tint)
    print(f"wrote {out}")
    print(f"  temp {args.temp}K tint {args.tint} -> gains "
          f"R{g[0]:.3f} G{g[1]:.3f} B{g[2]:.3f}")
    print(f"  dark {'yes' if dark is not None else 'no'}, "
          f"flat {'yes' if flat is not None else 'no'}, gamma {args.gamma}")


if __name__ == "__main__":
    main()
