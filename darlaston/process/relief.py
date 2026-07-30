"""Relief rendering from a stack's depth map: differential contrast, faked.

Differential interference contrast puts a shear between two sheared copies
of the wavefront, so the image brightness follows the *gradient of optical
path length* along one azimuth: a grey field where flat regions vanish and
every slope lights on one side and shadows on the other. It looks like
raking light across a landscape, and on a 1960s stand it requires a Wollaston
prism, a polariser, an analyser and a strain-free objective.

A focus stack already measures a surface. The depth map is where each pixel
came into focus, which for a diatom frustule is its top face, so its gradient
along an azimuth is the same *kind* of quantity DIC displays -- and shading
it produces the same read. This is a render, not a measurement: it is
labelled as one, and the physics differs (true DIC responds to refractive
index through the whole thickness, this responds to where focus peaked).

Two things are combined, because depth alone renders as smooth putty. The
coarse relief comes from the depth map, which knows the frustule's shape but
resolves nothing finer than a slice; the fine relief comes from the composite
image's own high frequencies, standing in for the small path-length
variations that make striae visible in a real DIC image. The mixture is a
knob, and at zero detail the render is honest depth-only relief.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .wiggle import load_pair

#: Shear azimuth in degrees, measured like a compass bearing on the image:
#: 45 puts the light at the top-left, which is the convention every
#: relief map and most published DIC uses, because human perception
#: assumes light from above and flips the interpretation otherwise.
AZIMUTH = 45.0
#: Weight of the fine (image texture) contribution against the coarse
#: (depth) one. Only the *ratio* matters -- the result is normalised on a
#: robust scale afterwards -- so depth relief is fixed at 1 and this is
#: the single mixing knob. At 0 the render is honest depth-only relief,
#: which has the frustule's form and no striae at all (the depth map
#: cannot resolve finer than a slice); past ~0.2 the depth stops
#: contributing and it becomes an embossed photograph. 0.07 is where
#: both read, chosen by sweeping against a real 13-slice stack.
RELIEF = 1.0
DETAIL = 0.07
#: Scale of the high-pass that extracts fine structure, in output pixels.
DETAIL_SIGMA = 3.0
#: Softening of the depth before differentiating. The depth map is
#: piecewise-smooth by construction and its residual slice steps would
#: otherwise render as terraced cliffs.
DEPTH_SIGMA = 9.0


def _shaded(depth: np.ndarray, luma: np.ndarray, azimuth: float,
            relief: float, detail: float) -> np.ndarray:
    """The DIC-like signal: a directional derivative of a mixed surface."""
    coarse = cv2.GaussianBlur(depth.astype(np.float32), (0, 0), DEPTH_SIGMA)
    coarse /= max(float(np.abs(coarse).max()), 1e-6)

    fine = luma.astype(np.float32) / 255.0
    fine = fine - cv2.GaussianBlur(fine, (0, 0), DETAIL_SIGMA)
    fine /= max(float(np.percentile(np.abs(fine), 99.5)), 1e-6)

    surface = relief * coarse + detail * fine
    gx = cv2.Sobel(surface, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(surface, cv2.CV_32F, 0, 1, ksize=3)
    rad = np.deg2rad(azimuth)
    g = np.cos(rad) * gx - np.sin(rad) * gy
    # Normalise on a robust scale: a few blown highlights on the frustule
    # rim must not flatten everything else.
    return g / max(float(np.percentile(np.abs(g), 99.0)), 1e-6)


#: Bias colours for the tinted mode, as BGR. Classic Nomarski with a
#: quarter-wave bias lands somewhere near this: a warm shadow, a cool
#: highlight, and a desaturated middle where the field is flat.
_SHADOW = np.float32([26, 52, 118])
_MID = np.float32([116, 124, 116])
_LIGHT = np.float32([236, 240, 206])
#: The signal is normalised on a robust scale, so most of it sits near
#: zero and a linear ramp renders as almost-flat grey. This expands the
#: middle without clipping the extremes.
_TINT_GAMMA = 0.55


def dic(directory: Path | str, azimuth: float = AZIMUTH,
        relief: float = RELIEF, detail: float = DETAIL,
        invert: bool = False, tint: bool = True,
        width: int = 10 ** 9) -> tuple[Path, Path]:
    """Render `dic.png` (grey) and `dic_tinted.png` beside the stack."""
    directory = Path(directory)
    img, depth = load_pair(directory, width=width, invert=invert)
    luma = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = _shaded(depth, luma, azimuth, relief, detail)

    grey = np.clip(0.5 + 0.5 * g, 0, 1)
    p_grey = directory / "dic.png"
    cv2.imwrite(str(p_grey), (grey * 255).astype(np.uint8))

    t = np.clip(g, -1, 1)
    t = np.sign(t) * np.abs(t) ** _TINT_GAMMA
    t = t[:, :, None]
    warm = _SHADOW + (_MID - _SHADOW) * (1 + t)          # t in [-1, 0]
    cool = _MID + (_LIGHT - _MID) * t                    # t in [0, 1]
    tinted = np.where(t < 0, warm, cool)
    p_tint = directory / "dic_tinted.png"
    cv2.imwrite(str(p_tint), np.clip(tinted, 0, 255).astype(np.uint8))
    return p_grey, p_tint


if __name__ == "__main__":
    import sys
    print(*dic(sys.argv[1]))
