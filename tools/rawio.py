"""Shared raw I/O: canonical orientation, manifests, calibration.

The raw stream arrives bottom-up. Flipping an even-height frame shifts the
Bayer row parity, so an unflipped buffer looks like RGGB while get_RawFormat
correctly reports GBRG. Storing frames in mixed orientations with nothing
recording which is which is a silent, expensive mistake -- so every capture
gets a manifest, and every read verifies the orientation from the data itself.
"""
import json
from pathlib import Path

import cv2
import numpy as np

WHITE_LEVEL = 4095
MANIFEST = "capture.json"


def phase_means(a):
    """Mean of each Bayer phase, in (0,0) (0,1) (1,0) (1,1) order."""
    return [float(a[dy::2, dx::2].mean()) for dy in (0, 1) for dx in (0, 1)]


def is_canonical(a):
    """True if the frame is right-way-up, i.e. GBRG with greens on the diagonal.

    Self-verifying: the two green sites see the same spectrum, so their means
    track each other far more closely than either tracks red or blue. Returns
    None when the frame is too uniform to tell (a dark, typically), where the
    caller should fall back to the manifest.
    """
    p = phase_means(a)
    diag = abs(p[0] - p[3])
    anti = abs(p[1] - p[2])
    spread = max(p) - min(p)
    if spread < 1e-6 or min(diag, anti) > 0.25 * spread:
        return None
    return diag < anti


def to_canonical(a, manifest=None):
    """Return the frame right-way-up, flipping only if it is not already."""
    c = is_canonical(a)
    if c is None:
        c = bool(manifest.get("canonical", False)) if manifest else False
    return a if c else np.ascontiguousarray(cv2.flip(a, 0))


def read_raw(path):
    """Read a raw Bayer frame in canonical orientation, with its manifest."""
    path = Path(path)
    a = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if a is None:
        raise FileNotFoundError(path)
    m = read_manifest(path.parent)
    return to_canonical(a, m), m


def read_manifest(d):
    p = Path(d) / MANIFEST
    if p.exists():
        return json.loads(p.read_text())
    return {}


def write_manifest(d, **fields):
    """Record what was applied, so a later read never has to guess."""
    p = Path(d) / MANIFEST
    m = read_manifest(d)
    m.update(fields)
    p.write_text(json.dumps(m, indent=2) + "\n")
    return m


def bayer_norm(flat):
    """Normalise a flat per Bayer phase.

    A single scalar is wrong on undemosaiced data: the phases differ in
    sensitivity, so one norm bakes a 2x2 checkerboard into every frame.
    """
    out = flat.astype(np.float32).copy()
    for dy in (0, 1):
        for dx in (0, 1):
            p = out[dy::2, dx::2]
            out[dy::2, dx::2] = p / max(float(p.mean()), 1e-6)
    return out


def calibrate(raw, dark=None, flat=None):
    """(raw - dark) / flat, with the flat normalised per Bayer phase.

    All inputs must already be canonical. Returns float32 in raw counts.
    """
    x = raw.astype(np.float32)
    if dark is not None:
        x = np.maximum(x - dark.astype(np.float32), 0)
    if flat is not None:
        f = flat.astype(np.float32)
        if dark is not None:
            f = np.maximum(f - dark.astype(np.float32), 0)
        x = x / np.maximum(bayer_norm(f), 1e-3)
    return x


def white_balance_from_flat(flat, dark=None):
    """Gains that neutralise a blank field, normalised to green.

    A featureless illuminated field is neutral by definition, so this is a
    measurement rather than an estimate. Canonical GBRG: (0,0) and (1,1) are
    green, (0,1) is blue, (1,0) is red.
    """
    f = flat.astype(np.float32)
    if dark is not None:
        f = np.maximum(f - dark.astype(np.float32), 0)
    g = (f[0::2, 0::2].mean() + f[1::2, 1::2].mean()) / 2
    b = f[0::2, 1::2].mean()
    r = f[1::2, 0::2].mean()
    return float(g / max(r, 1e-6)), 1.0, float(g / max(b, 1e-6))
