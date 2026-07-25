#!/usr/bin/env python3
"""Can these frames be registered at all?

Phase-correlate every consecutive pair of captures. The response value is a
0..1 confidence. Comparing darkfield pairs against phase pairs -- and against
deliberately non-overlapping controls -- tells us whether registration on this
subject is viable before we wire up anyone's stitching library.
"""
import cv2
import numpy as np
from pathlib import Path

D = Path("/home/nate/Pictures/ovshoot/250819_dopamine")
SCALE = 0.25  # downsample; registration runs on small images anyway


def load(p):
    im = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    im = cv2.resize(im, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    return im.astype(np.float32)


def reg(a, b):
    """Phase correlation with a Hanning window. Returns (dx, dy, confidence)."""
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
    return dx / SCALE, dy / SCALE, resp


files = sorted(D.glob("ov*.jpg"))
imgs = {}

# Content fraction separates the darkfield frames (mostly black) from the
# phase/brightfield ones without needing labels.
print(f"{'file':<12}{'mean':>8}{'>16 DN %':>11}{'modality guess':>18}")
print("-" * 50)
for f in files:
    im = load(f)
    imgs[f.name] = im
    frac = (im > 16).sum() / im.size * 100
    guess = "darkfield" if frac < 30 else "bright/phase"
    print(f"{f.name:<12}{im.mean():>8.1f}{frac:>11.1f}{guess:>18}")

print(f"\n{'='*74}")
print("consecutive-pair registration")
print(f"{'='*74}")
print(f"{'pair':<20}{'dx px':>10}{'dy px':>10}{'conf':>9}   verdict")
print("-" * 74)

names = [f.name for f in files]
results = []
for a, b in zip(names, names[1:]):
    dx, dy, c = reg(imgs[a], imgs[b])
    mag = np.hypot(dx, dy)
    if c > 0.10:
        v = "STRONG"
    elif c > 0.03:
        v = "weak"
    else:
        v = "none"
    results.append((a, b, dx, dy, c, mag))
    print(f"{a[2:6]}->{b[2:6]:<14}{dx:>10.0f}{dy:>10.0f}{c:>9.4f}   {v}")

# Control: frames far apart in the sequence should not register. Whatever
# confidence they produce is the noise floor for this metric on this data.
print(f"\n{'='*74}")
print("controls (frames far apart -- should NOT register)")
print(f"{'='*74}")
ctrl = []
for a, b in [(names[0], names[len(names)//2]), (names[2], names[-3]),
             (names[5], names[-8])]:
    dx, dy, c = reg(imgs[a], imgs[b])
    ctrl.append(c)
    print(f"{a[2:6]}->{b[2:6]:<14}{dx:>10.0f}{dy:>10.0f}{c:>9.4f}")

floor = max(ctrl)
print(f"\nnoise floor from controls: {floor:.4f}")

print(f"\n{'='*74}")
print("summary")
print(f"{'='*74}")
dark = [r for r in results if imgs[r[0]].mean() < 30 and imgs[r[1]].mean() < 30]
lit = [r for r in results if imgs[r[0]].mean() >= 30 and imgs[r[1]].mean() >= 30]
for label, group in [("darkfield pairs", dark), ("bright/phase pairs", lit)]:
    if not group:
        print(f"{label:<22} none found")
        continue
    cs = [r[4] for r in group]
    above = sum(1 for c in cs if c > floor * 2)
    print(f"{label:<22} n={len(group):<4} median conf {np.median(cs):.4f}   "
          f"{above}/{len(group)} clear the noise floor by 2x")
