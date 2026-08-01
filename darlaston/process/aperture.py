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


#: Blurred layers are computed at this fraction of the linear size. A
#: defocused layer is low-frequency by construction -- that is what
#: defocus means -- so a quarter-size Gaussian carries everything that
#: survives the blur anyway, at a sixteenth of the pixels. Measured
#: against the full-size version on a real stack: 0.25 to 0.47 levels of
#: 255 apart, which is nothing, for roughly half the time.
LAYER_SHRINK = 4


def _layers(img: np.ndarray, aperture: float, count: int) -> list:
    """The image at `count` increasing blur radii, sharpest first.

    Each blur builds on the one before it rather than on the original.
    Gaussians compose -- blurring by a then by b is blurring by
    sqrt(a^2+b^2) -- so every step after the first works from a radius
    that is already most of the way there, which is much cheaper than
    starting over. The sharpest layer stays full size because it is the
    one the eye actually reads.
    """
    base = img.astype(np.float32)
    h, w = base.shape[:2]
    small = cv2.resize(base, (max(1, w // LAYER_SHRINK),
                              max(1, h // LAYER_SHRINK)),
                       interpolation=cv2.INTER_AREA)
    out, prev, cur = [base], 0.0, small
    for k in range(1, count):
        sigma = aperture * k / (count - 1) / LAYER_SHRINK
        step = float(np.sqrt(max(sigma * sigma - prev * prev, 1e-6)))
        cur = cv2.GaussianBlur(cur, (0, 0), step)
        prev = sigma
        out.append(cv2.resize(cur, (w, h), interpolation=cv2.INTER_LINEAR))
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
    # A tent weight per layer: exactly the piecewise-linear interpolation
    # the masked version computed, written without boolean indexing.
    #
    # The masked form looks like it should win, since the masks partition
    # the image and it therefore touches each pixel once against this
    # one's six passes. Measured head to head on a real stack it loses,
    # 253 ms against 130: fancy-indexing six scattered gathers and six
    # scattered writes costs far more than six contiguous multiply-adds.
    # They agree to one level out of 255. I had this backwards first, from
    # inferring the masked cost by subtraction instead of timing it.
    out = np.zeros_like(stack[0])
    for k, layer in enumerate(stack):
        weight = np.clip(1.0 - np.abs(t - k), 0.0, 1.0)
        if not weight.any():
            continue
        out += layer * weight[:, :, None]
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
