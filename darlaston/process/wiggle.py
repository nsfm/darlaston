"""Parallax artifacts from a merged stack: the depth map earns its keep.

Every finished stack leaves two files side by side -- `stacked.dng`, the
all-in-focus composite, and `depth.png`, the per-pixel depth that chose it.
Together they are everything depth-image-based rendering needs: shift each
pixel laterally in proportion to its depth and the scene is seen from a
slightly different place. Do it along a little orbit and the subject rocks
in depth (a wigglegram); do it twice, symmetrically, and the two views are
a stereo pair (crossed-eye, or fused into a red/cyan anaglyph).

The synthesis is backward warping with the destination's own depth, which
is an approximation -- strictly, occlusions should reveal background we
never saw. At the small parallax that reads as depth rather than as a
glitch (a percent or two of the width), the approximation shows as slight
edge stretching instead of holes, which suits the medium: these are made
for looking at, not for measuring.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .stitch import _read_ifd, _values, read_bayer_dng, read_white_level

#: Peak parallax as a fraction of image width. Beyond ~1.5% the backward
#: warp's edge stretching starts to read as tearing.
AMPLITUDE = 0.012
#: Frames along one wobble cycle, and their display rate.
FRAMES = 24
FRAME_MS = 55
#: Output width of animated artifacts. Full-resolution wobbles are huge
#: and add nothing at the size these are viewed.
WIGGLE_W = 1400
#: Depth is smoothed before it drives the warp: depth *edges* are exactly
#: where the approximation is worst, and softening them turns tears into
#: leans. Sigma in pixels at output scale.
DEPTH_SOFTEN = 6.0


def _samples_per_pixel(path: Path) -> int:
    import struct
    data = Path(path).read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    ifd = _read_ifd(data, first)
    if 330 in ifd:
        ifd = _read_ifd(data, _values(data, ifd[330])[0])
    return _values(data, ifd[277])[0] if 277 in ifd else 1


def _read_linear(path: Path) -> np.ndarray:
    """A linear RGB DNG we wrote, as BGR float32."""
    import struct
    data = Path(path).read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    ifd = _read_ifd(data, first)
    if 330 in ifd:
        ifd = _read_ifd(data, _values(data, ifd[330])[0])
    w = _values(data, ifd[256])[0]
    h = _values(data, ifd[257])[0]
    offs = _values(data, ifd[273])
    cnts = _values(data, ifd[279])
    blob = b"".join(data[o:o + c] for o, c in zip(offs, cnts))
    rgb = np.frombuffer(blob, np.uint16, count=w * h * 3).reshape(h, w, 3)
    return np.ascontiguousarray(rgb[:, :, ::-1]).astype(np.float32)


def _read_composite(path: Path) -> tuple[np.ndarray, int]:
    """(BGR float image, white level) from either merge output shape."""
    white = read_white_level(path)
    if _samples_per_pixel(path) == 3:         # linear RGB merge output
        return _read_linear(path), white
    raw = read_bayer_dng(path)
    return cv2.cvtColor(raw, cv2.COLOR_BayerGR2BGR).astype(np.float32), white


def _develop(rgb: np.ndarray, white: int) -> np.ndarray:
    """The composite, made displayable: grey-world, headroom, gamma."""
    rgb = rgb / max(float(white), 1.0)
    means = [max(float(rgb[:, :, c].mean()), 1e-6) for c in range(3)]
    for c in range(3):
        rgb[:, :, c] *= means[1] / means[c]
    peak = float(np.percentile(rgb, 99.7)) or 1.0
    return (np.clip(rgb / peak, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def _load(directory: Path | str, width: int = WIGGLE_W,
          invert: bool = False):
    """(image, signed depth) at output scale, from a stack session dir.

    Depth polarity is a convention, not a fact: slice 1 is wherever the
    operator *started* racking, so which end of the stack is "near" depends
    on their habit. The default matches Nate's rack direction (they viewed
    the first attempt and asked for the flip); `invert` is for the other
    habit, and lives in settings because habits persist.
    """
    directory = Path(directory)
    dng = directory / "stacked.dng"
    dmap = directory / "depth.png"
    if not dng.exists() or not dmap.exists():
        raise FileNotFoundError(
            f"{directory} has no stacked.dng + depth.png pair — "
            "finish the stack's merge first")
    rgb, white = _read_composite(dng)
    img = _develop(rgb, white)
    depth = cv2.imread(str(dmap), cv2.IMREAD_GRAYSCALE)
    h, w = img.shape[:2]
    scale = min(1.0, width / w)
    if scale < 1.0:
        img = cv2.resize(img, (round(w * scale), round(h * scale)),
                         interpolation=cv2.INTER_AREA)
    depth = cv2.resize(depth, (img.shape[1], img.shape[0]),
                       interpolation=cv2.INTER_LINEAR).astype(np.float32)
    depth = cv2.GaussianBlur(depth, (0, 0), DEPTH_SOFTEN)
    # Signed, zero at the median depth: the middle of the subject holds
    # still and the extremes counter-rotate, which reads as rocking the
    # subject rather than panning the camera.
    depth -= float(np.median(depth))
    span = max(float(np.abs(depth).max()), 1e-6)
    sign = 1.0 if invert else -1.0
    return img, sign * depth / span


def _view(img: np.ndarray, depth: np.ndarray, shift_px: float,
          lift_px: float = 0.0) -> np.ndarray:
    """The scene from a viewpoint moved sideways by shift_px."""
    h, w = depth.shape
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    return cv2.remap(img,
                     (gx + depth * shift_px).astype(np.float32),
                     (gy + depth * lift_px).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def wigglegram(directory: Path | str, amplitude: float = AMPLITUDE,
               frames: int = FRAMES, invert: bool = False) -> Path:
    """A looping wobble: wiggle.webm (VP9) plus wiggle.webp beside it.

    Both, deliberately: the WebM is the shareable one -- a real video
    file every platform will accept -- with a few cycles baked in because
    video players do not promise to loop; the WebP loops by construction
    and previews inline in more chat clients. Same frames either way.
    """
    directory = Path(directory)
    img, depth = _load(directory, invert=invert)
    peak = amplitude * img.shape[1]
    seq = []
    for k in range(frames):
        # A shallow ellipse: mostly sideways, a breath of vertical. The
        # cosine spends its time near the extremes, where depth reads.
        th = 2 * np.pi * k / frames
        seq.append(_view(img, depth, peak * np.cos(th),
                         0.18 * peak * np.sin(th)))

    target = directory / "wiggle.webm"
    h, w = seq[0].shape[:2]
    writer = cv2.VideoWriter(str(target),
                             cv2.VideoWriter_fourcc(*"VP90"),
                             1000.0 / FRAME_MS, (w, h))
    if writer.isOpened():
        for _cycle in range(4):
            for frame in seq:
                writer.write(frame)
        writer.release()
    else:                                     # no VP9 in this build
        target = directory / "wiggle.webp"

    anim = cv2.Animation()
    anim.loop_count = 0                       # forever
    anim.frames = seq
    anim.durations = [FRAME_MS] * len(seq)
    if not cv2.imwriteanimation(str(directory / "wiggle.webp"), anim,
                                [cv2.IMWRITE_WEBP_QUALITY, 80]):
        raise RuntimeError("animated WebP write failed")
    return target


def stereo(directory: Path | str, amplitude: float = AMPLITUDE,
           invert: bool = False) -> tuple[Path, Path]:
    """A crossed-eye pair and a red/cyan anaglyph, beside the stack.

    Full output width: unlike the wobble, a stereo pair rewards zooming.
    """
    directory = Path(directory)
    img, depth = _load(directory, width=10 ** 9, invert=invert)
    peak = amplitude * img.shape[1]
    left = _view(img, depth, +peak / 2)
    right = _view(img, depth, -peak / 2)

    cross = np.hstack([right, left])          # crossed-eye: right on left
    p_pair = directory / "stereo_pair.png"
    cv2.imwrite(str(p_pair), cross)

    # Anaglyph: red from the left eye, green+blue from the right, on a
    # gently desaturated base so the glasses fight the colours less.
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    base = cv2.addWeighted(img, 0.55,
                           cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR), 0.45, 0)
    lg = _view(base, depth, +peak / 2)
    rg = _view(base, depth, -peak / 2)
    ana = rg.copy()
    ana[:, :, 2] = lg[:, :, 2]                # BGR: red channel from left
    p_ana = directory / "anaglyph.png"
    cv2.imwrite(str(p_ana), ana)
    return p_pair, p_ana


if __name__ == "__main__":
    import sys
    d = sys.argv[1]
    print(wigglegram(d))
    print(*stereo(d))
