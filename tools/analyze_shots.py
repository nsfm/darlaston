#!/usr/bin/env python3
"""Measure what ToupLite's output pipeline actually costs.

Looks for clipping, posterisation, chroma damage and illumination falloff in
existing JPEG captures.
"""
import os
import sys

import numpy as np
from PIL import Image

# A folder of captures to look at. These were run against one session's
# shots; point it at your own with the first argument or $SHOTS.
D = (sys.argv[1] if len(sys.argv) > 1
     else os.environ.get("SHOTS", "captures/")).rstrip("/") + "/"
FILES = [("ov0027.jpg", "darkfield"), ("ov0016.jpg", "phase")]


def analyse(path, label):
    im = Image.open(path)
    a = np.asarray(im).astype(np.uint8)
    h, w, _ = a.shape
    print(f"\n{'='*66}\n{label.upper():<12} {path.split('/')[-1]}   {w}x{h}\n{'='*66}")

    names = "RGB"
    print(f"{'chan':<6}{'min':>5}{'max':>5}{'mean':>8}{'levels':>8}{'@0 %':>9}{'@255 %':>9}")
    for c in range(3):
        ch = a[:, :, c]
        n = ch.size
        lv = len(np.unique(ch))
        z = (ch == 0).sum() / n * 100
        s = (ch == 255).sum() / n * 100
        print(f"{names[c]:<6}{ch.min():>5}{ch.max():>5}{ch.mean():>8.1f}"
              f"{lv:>8}{z:>9.2f}{s:>9.2f}")

    # Luma-domain shadow occupancy: how much of the image lives in the bottom
    # of the range, where 8 bits has the fewest levels to spend.
    lum = (0.2126*a[:, :, 0] + 0.7152*a[:, :, 1] + 0.0722*a[:, :, 2])
    for lo, hi in [(0, 4), (0, 8), (0, 16), (0, 32), (0, 64)]:
        pct = ((lum >= lo) & (lum < hi)).sum() / lum.size * 100
        print(f"  luma in [{lo:>3},{hi:>3}) : {pct:6.2f}%")

    # Posterisation proxy: distinct luma values actually present vs available
    # in the occupied range.
    occupied = int(lum.max()) - int(lum.min()) + 1
    used = len(np.unique(lum.round().astype(np.uint8)))
    print(f"  luma range spans {occupied} levels, {used} distinct values present")

    # Illumination falloff: median radial profile, normalised to centre.
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h/2, w/2
    r = np.sqrt(((yy-cy)/(h/2))**2 + ((xx-cx)/(w/2))**2)
    print("  radial luma profile (median, normalised to centre):")
    prof = []
    for i in range(8):
        m = (r >= i*0.125) & (r < (i+1)*0.125)
        if m.sum():
            prof.append(np.median(lum[m]))
    if prof and prof[0] > 0:
        cells = "  ".join(f"{p/prof[0]:.2f}" for p in prof)
        print(f"    r=0.0 -> 1.0 :  {cells}")

    # Chroma resolution damage from 4:2:0 subsampling: compare each chroma
    # plane against its own 2x-downsample-then-upsample. Zero difference means
    # the detail was already destroyed at encode time.
    ycc = np.asarray(im.convert("YCbCr")).astype(np.float32)
    for ci, cn in [(1, "Cb"), (2, "Cr")]:
        plane = ycc[:, :, ci]
        small = plane[::2, ::2]
        up = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)[:h, :w]
        resid = np.abs(plane - up).mean()
        print(f"  {cn} detail lost to 4:2:0 : mean residual {resid:.3f} "
              f"({'already destroyed' if resid < 0.5 else 'some detail survives'})")

    # Noise floor in the flattest tiles, as a proxy for read noise + JPEG blur.
    g = ycc[:, :, 0]
    tiles = [g[y:y+64, x:x+64] for y in range(0, h-64, 128) for x in range(0, w-64, 128)]
    stds = sorted(t.std() for t in tiles)
    print(f"  flattest-decile tile sigma : {np.mean(stds[:max(1,len(stds)//10)]):.2f} DN")


for f, lbl in FILES:
    analyse(D + f, lbl)

# The manual composite: how many tiles went into it, roughly, and does it show
# seam-brightness structure?
print(f"\n{'='*66}\nMANUAL COMPOSITE\n{'='*66}")
for name in ["dopamine_darkfield_6x_square.jpg", "dopamine_darkfield_6x.jpg"]:
    im = Image.open(D + name)
    a = np.asarray(im.convert("L")).astype(np.float32)
    h, w = a.shape
    tile_w, tile_h = 2736, 1824
    print(f"\n{name}  {w}x{h}  ({w*h/1e6:.1f} MP)")
    print(f"  = {w/tile_w:.2f} x {h/tile_h:.2f} binned frames "
          f"({w*h/(tile_w*tile_h):.1f} frames of area, before overlap)")
    print(f"  = {w/5440:.2f} x {h/3648:.2f} FULL-RES frames "
          f"({w*h/(5440*3648):.1f} frames of area)")
