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

Sharpness is Tenengrad with a small Gaussian pool, and the depth map is then
*refined* with a joint-bilateral weighted median, guided by the winning
slice's own luma. The split matters and was measured, not guessed: pooling
the sharpness field wide (the old sigma-4 Gaussian) let a bright in-focus
edge push its verdict far past the subject's boundary -- the halo, 54%% of a
synthetic glow band misassigned. Pooling *edge-aware* does not help, because
a sharpness ridge sits exactly on the luma edge and contains every luma level
along the ramp; guided-filter pooling measured strictly worse. What is
actually piecewise-constant with respect to the image is the depth map
itself, so: pool small (accurate but speckled), then let each pixel take the
weighted median depth of its luma-similar neighbours, votes weighted by their
own sharpness evidence. On the synthetic halo case this took the band from
54%% to 3%% misassigned and the depth boundary from +-11.5 px to +-0.9.
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
#: Sharpness pooling, in half-res pixels. Small on purpose: accuracy comes
#: from here, cleanliness from the refinement. Sigma 4 was the halo.
POOL_SIGMA = 1.5
#: Refinement neighbourhood (box radius, half-res px), luma bins for the
#: bilateral weights, and the range kernel as a fraction of the guide's
#: luma range. The probe showed halo 1-7%% across a 4x sweep of all three,
#: so none of them is delicate.
REFINE_RADIUS = 8
REFINE_BINS = 8
REFINE_RANGE = 0.15
#: Feather of the blend at depth boundaries, half-res px: the winning
#: slice's one-hot mask is blurred this much, so seams cross-fade instead
#: of stepping.
FEATHER_SIGMA = 2.0


def _luma_half(raw: np.ndarray) -> np.ndarray:
    f = raw.astype(np.float32)
    return (f[0::2, 0::2] + f[0::2, 1::2] + f[1::2, 0::2] + f[1::2, 1::2]) / 4


def _sharpness(luma: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1)
    mag = gx * gx + gy * gy
    return cv2.GaussianBlur(mag, (0, 0), POOL_SIGMA)


def _refine_depth(depth: np.ndarray, guide: np.ndarray,
                  conf: np.ndarray) -> np.ndarray:
    """Joint-bilateral weighted median of the depth map.

    Each pixel takes the weighted median depth of its neighbours, where a
    neighbour's vote is its bilateral luma similarity (against `guide`, the
    winning slice's luma -- an approximate all-in-focus image) times its own
    sharpness evidence `conf`. Textureless pixels thereby defer to textured
    neighbours that look like them, and a bright subject's votes stop at its
    own boundary. Implemented with the range-binning trick: box filters per
    luma bin, interpolated per pixel, cumulative over depth to find the
    median -- O(bins * slices) box filters, no per-pixel windows.
    """
    n = int(depth.max()) + 1
    if n <= 1:
        return depth
    conf = np.clip(conf / (np.percentile(conf, 90) + 1e-9),
                   0.02, 1.0).astype(np.float32)
    lmin, lmax = float(guide.min()), float(guide.max())
    sr = REFINE_RANGE * max(lmax - lmin, 1e-6)
    k = (2 * REFINE_RADIUS + 1,) * 2
    wgts = [(np.exp(-0.5 * ((guide - c) / sr) ** 2) * conf)
            .astype(np.float32)
            for c in np.linspace(lmin, lmax, REFINE_BINS)]
    den = np.stack([cv2.boxFilter(w, -1, k) for w in wgts])

    pos = (guide - lmin) / (lmax - lmin + 1e-9) * (REFINE_BINS - 1)
    i0 = np.clip(pos.astype(np.int64), 0, REFINE_BINS - 2)
    frac = (pos - i0).astype(np.float32)

    def at_pixel_luma(binned: np.ndarray) -> np.ndarray:
        lo = np.take_along_axis(binned, i0[None], 0)[0]
        hi = np.take_along_axis(binned, (i0 + 1)[None], 0)[0]
        return lo * (1 - frac) + hi * frac

    half = at_pixel_luma(den) * 0.5
    acc = [np.zeros(depth.shape, np.float32) for _ in wgts]
    out = np.zeros(depth.shape, np.uint8)
    settled = np.zeros(depth.shape, bool)
    for d in range(n):
        sel = (depth == d).astype(np.float32)
        for i, w in enumerate(wgts):
            acc[i] += w * sel
        cum = at_pixel_luma(np.stack([cv2.boxFilter(a, -1, k) for a in acc]))
        hit = (~settled) & (cum >= half)
        out[hit] = d
        settled |= hit
    out[~settled] = depth[~settled]           # no votes at all: keep your own
    return out


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

    # Sharpness on *aligned* lumas, so the depth map and the pixels it
    # selects agree about where everything is. A running argmax keeps only
    # three half-res planes -- winner index, winner's sharpness, winner's
    # luma -- instead of the whole field stack.
    h2, w2 = lumas[0].shape
    depth = np.zeros((h2, w2), np.uint8)
    conf = np.full((h2, w2), -1.0, np.float32)
    guide = np.zeros((h2, w2), np.float32)
    for i, (luma, (sx, sy)) in enumerate(zip(lumas, shifts)):
        if abs(sx) > 0.01 or abs(sy) > 0.01:
            m = np.float32([[1, 0, -sx], [0, 1, -sy]])
            luma = cv2.warpAffine(luma, m, (w2, h2),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
        field = _sharpness(luma)
        better = field > conf
        depth[better] = i
        guide[better] = luma[better]
        np.maximum(conf, field, out=conf)
        if progress:
            progress("measuring", i + 1, n)
    del lumas

    depth = _refine_depth(depth, guide, conf)
    depth = cv2.medianBlur(depth, DEPTH_MEDIAN)
    del guide, conf

    # Blend at full resolution, one slice at a time: demosaic, align, add.
    # Weights follow the *refined* depth -- a blurred one-hot of each
    # slice's territory -- not the raw sharpness fields, or every halo the
    # refinement removed would sneak straight back in through the blend.
    # Blurring a partition of unity keeps it one, so no normalising pass.
    h, w = raws[0].shape
    acc = np.zeros((h, w, 3), np.float32)
    for i, (raw, (sx, sy)) in enumerate(zip(raws, shifts)):
        won = (depth == i)
        if not won.any():
            if progress:
                progress("blending", i + 1, n)
            continue
        wgt = cv2.GaussianBlur(won.astype(np.float32), (0, 0),
                               FEATHER_SIGMA)
        rgb = cv2.cvtColor(raw, cv2.COLOR_BayerGR2BGR).astype(np.float32)
        if abs(sx) > 0.01 or abs(sy) > 0.01:
            m = np.float32([[1, 0, -sx * 2], [0, 1, -sy * 2]])
            rgb = cv2.warpAffine(rgb, m, (w, h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_REPLICATE)
        wfull = cv2.resize(wgt, (w, h), interpolation=cv2.INTER_LINEAR)
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
