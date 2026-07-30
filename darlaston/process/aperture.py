"""Choosing the focal plane after the shot.

A focus stack throws away the one thing a photograph normally cannot
recover: which plane was sharp. Having kept it -- an all-in-focus
composite plus per-pixel depth -- the choice becomes reversible. Blur each
pixel by how far its depth sits from a chosen plane and the image is
re-focused; widen the blur per unit of depth and the aperture opens. This
is what a light-field camera sells, and what a microscope operator has
never been offered, because no microscopy tool keeps the depth map.

The two artifacts are a still at any chosen plane, and a **focus pull** --
the plane drifting slowly through the subject on video, which is a
cinematographer's shot rather than a scientist's, and the most direct way
to show a stranger that the depth is real.

Rendering is layered rather than per-pixel: the image is blurred at a few
fixed radii once, and each pixel reads from the layer matching its own
defocus, interpolating between neighbouring layers. Blurring per pixel
would be exact and unusably slow; this is a handful of Gaussians and a
lookup, and the seams are invisible because the layers are themselves
smooth. Occlusion is not modelled -- a defocused foreground should bleed
over its background and here it merely softens -- which is the same
honest limit the parallax renders carry.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .wiggle import FRAME_MS, WIGGLE_W, load_pair

#: Widest blur, in output pixels, at maximum distance from the plane.
#: This is the "aperture": larger throws the rest of the stack further out
#: of focus. 12 reads as a fast lens on a 1400 px frame.
APERTURE = 12.0
#: Blur layers. More is smoother and slower; the differences above six
#: are not visible because each layer is already a smooth field.
LAYERS = 6
#: Frames in a focus pull, and how far past the subject's depth range the
#: plane travels so the pull starts and ends fully soft.
PULL_FRAMES = 90
PULL_MARGIN = 0.15


def _layers(img: np.ndarray, aperture: float, count: int) -> list:
    """The image at `count` increasing blur radii, sharpest first."""
    out = [img.astype(np.float32)]
    for k in range(1, count):
        sigma = aperture * k / (count - 1)
        out.append(cv2.GaussianBlur(img.astype(np.float32), (0, 0), sigma))
    return out


def refocus(img: np.ndarray, depth: np.ndarray, plane: float,
            aperture: float = APERTURE, layers: list | None = None,
            count: int = LAYERS) -> np.ndarray:
    """Re-render `img` focused at `plane` (in the depth's own units).

    `depth` is signed and normalised to [-1, 1] by load_pair, so a plane
    of 0 focuses the median depth and +-1 the extremes.
    """
    stack = layers if layers is not None else _layers(img, aperture, count)
    # Distance from the chosen plane, mapped onto the layer index.
    t = np.clip(np.abs(depth - plane), 0.0, 1.0) * (count - 1)
    lo = np.clip(np.floor(t), 0, count - 2).astype(np.int32)
    frac = (t - lo)[:, :, None].astype(np.float32)
    out = np.empty_like(stack[0])
    for k in range(count - 1):
        m = lo == k
        if not m.any():
            continue
        out[m] = stack[k][m] * (1 - frac[m]) + stack[k + 1][m] * frac[m]
    return np.clip(out, 0, 255).astype(np.uint8)


def still(directory: Path | str, plane: float = 0.0,
          aperture: float = APERTURE, invert: bool = False,
          name: str = "refocus.png") -> Path:
    """One re-focused still, at full output width."""
    directory = Path(directory)
    img, depth = load_pair(directory, width=10 ** 9, invert=invert)
    out = refocus(img, depth, plane, aperture)
    target = directory / name
    cv2.imwrite(str(target), out)
    return target


def focus_pull(directory: Path | str, aperture: float = APERTURE,
               frames: int = PULL_FRAMES, invert: bool = False) -> Path:
    """The plane drifting through the subject, as `focus_pull.webm`.

    Eased at both ends rather than linear: a constant-rate pull reads as
    a machine sweeping, and a cinematographer's pull settles.
    """
    directory = Path(directory)
    img, depth = load_pair(directory, width=WIGGLE_W, invert=invert)
    stack = _layers(img, aperture, LAYERS)

    lo = float(depth.min()) - PULL_MARGIN
    hi = float(depth.max()) + PULL_MARGIN
    seq = []
    for k in range(frames):
        u = k / max(frames - 1, 1)
        eased = u * u * (3 - 2 * u)                  # smoothstep, both ends
        seq.append(refocus(img, depth, lo + (hi - lo) * eased,
                           aperture, layers=stack))
    # Back again, so the file loops without a jump cut.
    seq += seq[-2:0:-1]

    target = directory / "focus_pull.webm"
    h, w = seq[0].shape[:2]
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"VP90"),
                             1000.0 / FRAME_MS, (w, h))
    if not writer.isOpened():
        raise RuntimeError("no VP9 encoder available for the focus pull")
    for frame in seq:
        writer.write(frame)
    writer.release()
    return target


if __name__ == "__main__":
    import sys
    d = sys.argv[1]
    print(focus_pull(d))
    print(still(d, plane=-0.6, name="refocus_near.png"))
    print(still(d, plane=0.6, name="refocus_far.png"))
