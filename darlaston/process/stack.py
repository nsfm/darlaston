"""Merging a Z-stack into one all-in-focus image.

Depth-map merging, deliberately: for each pixel, find which slice is
sharpest there and take it from that slice, with soft transitions where
neighbouring depths meet. The alternative family (wavelet or Laplacian-pyramid
fusion) blends frequency bands across slices and produces beautiful results
and untraceable artefacts; a depth map can be *looked at*, saved beside the
composite, and someday reused -- it is a measurement of the subject's shape,
not just an intermediate.

Alignment first, because focus breathing is real: racking the fine focus
shifts and very slightly rescales the image, more so when objectives are not
perfectly parfocal. Slices are registered by phase correlation to their
neighbour -- constrained, never searched, the same doctrine as the mosaic --
and the shifts are cumulative from the first slice.

Sharpness is the same Tenengrad-plus-Gaussian-pooling field the live peaking
uses. The pooling is what makes a depth *map* rather than per-pixel speckle:
sharpness is only measurable where there is texture, and pooling spreads each
edge's verdict over its neighbourhood.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from ..capture.stack import StackSession
from . import dng
from .stitch import read_bayer_dng, read_metadata

#: Per-slice alignment beyond this many half-res pixels is not focus
#: breathing, it is the stage having been bumped; the shift is refused and
#: that slice keeps its neighbour's alignment.
MAX_BREATHING = 40.0
#: Depth-map median filter, in half-res pixels. Kills single-pixel depth
#: speckle without moving real depth boundaries.
DEPTH_MEDIAN = 5
#: Softness of the blend at depth boundaries: sharpness is raised to this
#: power before being used as a weight, so the winner mostly takes all but
#: seams between depths feather instead of stepping.
WEIGHT_POWER = 4.0


def _luma_half(raw: np.ndarray) -> np.ndarray:
    f = raw.astype(np.float32)
    return (f[0::2, 0::2] + f[0::2, 1::2] + f[1::2, 0::2] + f[1::2, 1::2]) / 4


def _sharpness(luma: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1)
    mag = gx * gx + gy * gy
    return cv2.GaussianBlur(mag, (0, 0), 4.0)


def _register(lumas: list[np.ndarray]) -> list[tuple[float, float]]:
    """Cumulative shift of each slice relative to the first, half-res px."""
    shifts = [(0.0, 0.0)]
    hann = None
    for a, b in zip(lumas, lumas[1:]):
        if hann is None:
            hann = cv2.createHanningWindow((a.shape[1], a.shape[0]),
                                           cv2.CV_32F)
        (dx, dy), response = cv2.phaseCorrelate(a, b, hann)
        ok = (response > 0.03 and abs(dx) < MAX_BREATHING
              and abs(dy) < MAX_BREATHING)
        last = shifts[-1]
        if ok:
            shifts.append((last[0] + dx, last[1] + dy))
        else:
            # A slice that cannot be registered inherits its neighbour's
            # alignment rather than importing a wild guess into the stack.
            shifts.append(last)
    return shifts


def merge(directory: Path | str, progress=None,
          output: str = "bayer") -> tuple[Path, dict]:
    """The whole run: load, align, map depth, blend, write. (path, report).

    `output` is "bayer" or "linear", and bayer is the default because of an
    observation Nate made sound obvious: the composite can be *re-mosaicked*.
    Demosaic was only ever forced by alignment -- a Bayer pattern cannot
    survive a sub-pixel warp -- but nothing stops sampling the blended RGB
    back onto the grid afterwards. And bilinear demosaic passes each site's
    native value through untouched, so the round trip recovers exactly the
    per-site blend of aligned raw values: nothing meaningful is lost, the
    file is a quarter the size, and the developer's own demosaic -- better
    than ours -- does the final interpolation. Linear stays for the cases
    that want it.
    """
    session = StackSession.load(directory)
    n = len(session.slices)
    if n < 2:
        raise ValueError("a stack needs at least two slices")

    raws, lumas = [], []
    for i, piece in enumerate(session.slices):
        raw = read_bayer_dng(session.dir / piece.filename)
        raws.append(raw)
        lumas.append(_luma_half(raw))
        if progress:
            progress("reading", i + 1, n)

    shifts = _register(lumas)

    # Sharpness fields, computed on *aligned* lumas so the depth map and the
    # pixels it selects agree about where everything is.
    fields = []
    h2, w2 = lumas[0].shape
    for i, (luma, (sx, sy)) in enumerate(zip(lumas, shifts)):
        if abs(sx) > 0.01 or abs(sy) > 0.01:
            m = np.float32([[1, 0, -sx], [0, 1, -sy]])
            luma = cv2.warpAffine(luma, m, (w2, h2),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
        fields.append(_sharpness(luma))
        if progress:
            progress("measuring", i + 1, n)
    del lumas

    stack_f = np.stack(fields)                     # (n, h2, w2)
    del fields
    depth = np.argmax(stack_f, axis=0).astype(np.uint8)
    depth = cv2.medianBlur(depth, DEPTH_MEDIAN)

    # Weights: winner-mostly-takes-all with feathered seams. Normalised per
    # pixel across slices; the power sets how hard the winner wins.
    weights = np.power(np.maximum(stack_f, 1e-12), WEIGHT_POWER)
    # Textureless regions have no sharpness in any slice; the sum there is
    # zero and dividing by it wrote NaN into the composite. Nothing to
    # choose between means an even blend, not a hole.
    total = weights.sum(axis=0, keepdims=True)
    flat = total <= 1e-30
    weights = np.where(flat, 1.0 / len(raws), weights / np.maximum(total, 1e-30))
    del stack_f

    # Blend at full resolution, one slice at a time: demosaic, align, add.
    h, w = raws[0].shape
    acc = np.zeros((h, w, 3), np.float32)
    for i, (raw, (sx, sy)) in enumerate(zip(raws, shifts)):
        rgb = cv2.cvtColor(raw, cv2.COLOR_BayerGR2BGR).astype(np.float32)
        if abs(sx) > 0.01 or abs(sy) > 0.01:
            m = np.float32([[1, 0, -sx * 2], [0, 1, -sy * 2]])
            rgb = cv2.warpAffine(rgb, m, (w, h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
        wfull = cv2.resize(weights[i], (w, h),
                           interpolation=cv2.INTER_LINEAR)
        acc += rgb * wfull[:, :, None]
        del rgb, wfull
        if progress:
            progress("blending", i + 1, n)

    # The blend of 12-bit slices lands back in 0..4095, and writing that
    # against a 65535 white level shipped the composite four stops under --
    # found by Nate having to push every stack +3 EV in post. Scaled x16
    # into the declared range instead, which also keeps the sub-LSB
    # precision the weighted blend just created, exactly as the
    # frame-averaging path does.
    if output == "bayer":
        # Sample the blended RGB back onto the GBRG grid. acc is BGR.
        mosaic = np.empty((h, w), np.float32)
        mosaic[0::2, 0::2] = acc[0::2, 0::2, 1]      # G
        mosaic[0::2, 1::2] = acc[0::2, 1::2, 0]      # B
        mosaic[1::2, 0::2] = acc[1::2, 0::2, 2]      # R
        mosaic[1::2, 1::2] = acc[1::2, 1::2, 1]      # G
        result = np.clip(mosaic * 16.0 + 0.5, 0, 65520).astype(np.uint16)
    else:
        result = np.clip(acc * 16.0 + 0.5,
                         0, 65520).astype(np.uint16)[:, :, ::-1]
    del acc

    neutral = dng.grey_world_neutral(raws[len(raws) // 2])
    # Provenance rides along: the middle slice's own tags -- photographer,
    # optics, exposure -- become the composite's. It is one photograph made
    # of many exposures, and it should say who took it and through what.
    try:
        meta = read_metadata(
            session.dir / session.slices[len(raws) // 2].filename)
    except Exception:
        meta = None                    # provenance is a bonus, never a gate
    del raws

    target = session.dir / "stacked.dng"
    if output == "bayer":
        preview = dng.make_preview(result, bayer=True, white=4095 * 16,
                                   neutral=neutral)
        written = dng.write_bayer_streamed(
            target, lambda s, c: result[s:s + c], h, w,
            preview=preview, neutral=neutral, white=4095 * 16, meta=meta,
            bits=16)
    else:
        step = max(1, h // 400, w // 400)
        thumb = result[::step, ::step]
        means = [float(thumb[:, :, c][thumb[:, :, c] > 0].mean() or 1.0)
                 for c in range(3)]
        grey = tuple(m / max(means[1], 1e-6) for m in means)
        preview = dng.make_preview(thumb, bayer=False, neutral=grey,
                                   white=int(max(result.max(), 1)))
        written = dng.write_linear_streamed(
            target, lambda s, c: result[s:s + c], h, w,
            preview=preview, neutral=neutral, white=4095 * 16, meta=meta)

    # The depth map, twice. depth.png is the *data*: plain grayscale, near
    # slices dark and far slices light, the conventional encoding every
    # depth-consuming tool expects -- this is what a stereo pair or a
    # wigglegram will be synthesised from. depth_view.png is the same map
    # dressed for looking at, and stays because it earned it.
    dmap = (depth.astype(np.float32) / max(n - 1, 1) * 255).astype(np.uint8)
    cv2.imwrite(str(session.dir / "depth.png"), dmap)
    cv2.imwrite(str(session.dir / "depth_view.png"),
                cv2.applyColorMap(dmap, cv2.COLORMAP_VIRIDIS))

    report = {
        "output": output,
        "slices": n,
        "width": w, "height": h,
        "max_breathing_px": round(max(abs(v) for s in shifts for v in s) * 2,
                                  1),
        "depth_levels": int(len(np.unique(depth))),
    }
    return written, report


if __name__ == "__main__":
    path, report = merge(sys.argv[1])
    print(f"{path}  {report}")
