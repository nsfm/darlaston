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

Two further measured stages (see tools/stack_bench.py, where every one of
these earns its place): a log-parabolic fit through the winner's z-neighbours
makes depth *continuous* -- integer depth costs ~0.29 RMSE on a continuously
sloping surface, sub-slice depth measures ~0.10 -- and push-pull diffusion
re-interpolates pixels whose winning slice has no fine-scale energy, which
is what removes the terracing Nate reported in glow aprons: a defocused
edge's rings have gradients (they fool Tenengrad confidence) but no fine
detail (they cannot fool a barely-pooled Laplacian).
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
#: Feather of the blend at depth boundaries, half-res px: each slice's
#: territory mask is blurred this much, so seams cross-fade instead of
#: stepping.
FEATHER_SIGMA = 2.0
#: How many slices the blend draws on, per pixel, from confidence.
#:
#: This is the answer to two of Nate's complaints at once, and neither was
#: what I first assumed. NARROW below 1 means a trusted pixel takes its own
#: slice outright instead of averaging with a neighbour: the plain tent
#: blend measured 1.06-1.07 fine detail against one-hot's 1.27-1.31, and
#: that gap *is* the striae he saw the older merge keep. WIDE is the other
#: half: cross-fading two defocused slices does not produce intermediate
#: defocus, it produces both of their glow rings at partial opacity --
#: measured, the tent blend manufactures MORE glow structure (0.58-0.61)
#: than one-hot (0.74-0.78 is worse still, but tent was meant to fix it and
#: made it worse in kind). Averaging many slices smears the rings into the
#: smooth field the glow physically is: 0.24-0.26 at these settings, while
#: detail rises to 1.17-1.18. Better than the tent on both axes, on both of
#: Nate's real 25x stacks. POWER shapes the transition; higher keeps more
#: pixels narrow, and 20 sits at the knee.
BLEND_NARROW = 0.5
BLEND_WIDE = 9.0
BLEND_POWER = 20.0
#: Dilating the trust field before use spares a pixel that merely sits
#: *near* confident detail. Faint striae score low against a global
#: percentile that the specimen's bright edges set, and were visibly
#: softened without this; glow aprons are large and uniformly untrusted,
#: so they are unaffected. Measured, it interpolates between "off" and
#: "normal" rather than beating both -- there is no free lunch here, only
#: a dial, and radius 5 is the point where detail is indistinguishable
#: from winner-takes-all.
TRUST_DILATE = 5
#: Diffusion gate: fine-scale (Laplacian) energy of the winning slice,
#: as a smoothstep between these fractions of its own 90th percentile.
#: Placed by measurement on the terrace scene: misassigned glow pixels sit
#: at ~0.01-0.09, genuine focused texture at ~0.16+. Tenengrad confidence
#: cannot make this cut -- the misassigned pixels measure *more* confident,
#: which is exactly why the terraces existed.
DIFFUSE_LO = 0.10
DIFFUSE_HI = 0.35


def _luma_half(raw: np.ndarray) -> np.ndarray:
    f = raw.astype(np.float32)
    return (f[0::2, 0::2] + f[0::2, 1::2] + f[1::2, 0::2] + f[1::2, 1::2]) / 4


def _sharpness(luma: np.ndarray) -> np.ndarray:
    """Pooled Tenengrad, Weber-normalised by local mean intensity.

    The normalisation is measured, not decorative (tools/stack_bench.py:
    glow-band composite error 100 -> 66): gradient magnitude scales with
    absolute brightness, so a bright smooth glow outvotes dim in-focus
    texture. Relative contrast does not. (Sum-modified-Laplacian, the
    literature's favourite, measured *worse* here -- twice as much glow-band
    error -- so Tenengrad stays.)
    """
    gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1)
    mag = cv2.GaussianBlur(gx * gx + gy * gy, (0, 0), POOL_SIGMA)
    return mag / (cv2.boxFilter(luma, -1, (15, 15)) + 1.0)


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


def _push_pull(depth: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Fill low-weight regions by pyramid diffusion from high-weight ones.

    Confident pixels keep their depth; unconfident ones take a smooth
    interpolation of the nearest confident neighbourhood. No luma is
    involved, which is the point: the glow's own ring structure cannot
    terrace what never consults it.
    """
    levels = []
    dw = (depth * weight).astype(np.float32)
    w = weight.astype(np.float32)
    while min(w.shape) > 4:
        levels.append((dw, w))
        dw, w = cv2.pyrDown(dw), cv2.pyrDown(w)
    filled = dw / np.maximum(w, 1e-9)
    for dw, w in reversed(levels):
        filled = cv2.pyrUp(filled, dstsize=(w.shape[1], w.shape[0]))
        alpha = np.clip(w, 0.0, 1.0)
        filled = alpha * (dw / np.maximum(w, 1e-9)) + (1 - alpha) * filled
    return filled


#: Presets for the glow-smoothing knob: (diffusion gate, widest blend,
#: trust dilation, transition power). Three measured points on a real
#: trade-off, not a quality ladder -- which is why the knob exists.
#: Measured end to end through merge() on Nate's real 25x stack, as fine
#: detail (against the best single slice) and manufactured glow structure
#: (against a typical slice):
#:
#:   off      0.753 / 0.716   winner takes all, no diffusion. Crisp, and
#:                            it keeps the rings. What the merge did before.
#:   light    0.752 / 0.549   diffusion, but only where nothing nearby is
#:                            trusted. Detail indistinguishable from off,
#:                            a quarter less ring: the free one.
#:   normal   0.736 / 0.287   diffusion everywhere it is warranted. 60%
#:                            less ring structure for 2% of the striae.
#:   strong   0.699 / 0.171   plus wide blending where nothing is in focus.
#:                            Another 40% off the rings, another 5% of the
#:                            striae -- worth it only when the apron is the
#:                            subject of the complaint.
#:
#: The ordering matters more than it looks: the *depth diffusion* does
#: nearly all the glow work, and blending across many slices -- which
#: sounds like the obvious way to smear rings away, and measured well on a
#: half-resolution proxy -- turns out to buy little and cost real detail
#: once it goes through the full-resolution upsample and feather. The
#: proxy is not trustworthy for glow; only end-to-end merges are.
SMOOTHING = {
    "off": (None, BLEND_NARROW, 0, 1.0),
    "light": ((DIFFUSE_LO, DIFFUSE_HI), BLEND_NARROW, TRUST_DILATE, 1.0),
    "normal": ((DIFFUSE_LO, DIFFUSE_HI), BLEND_NARROW, 0, 1.0),
    "strong": ((0.15, 0.45), BLEND_WIDE, 9, 3.0),
}


def _depth_map(lumas: np.ndarray, progress=None, total: int = 0,
               diffuse: tuple[float, float] | None = (DIFFUSE_LO,
                                                      DIFFUSE_HI),
               wide: float = BLEND_WIDE, dilate: int = TRUST_DILATE,
               power: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """Aligned half-res lumas (n, h2, w2) -> continuous depth map (float32).

    Four measured stages, each earning its place on tools/stack_bench.py:
    small-pooled Tenengrad argmax (accurate, speckled); joint-bilateral
    weighted-median refinement (halo); log-parabolic sub-slice interpolation
    through the winner's z-neighbours (quantisation -- integer depth costs
    ~0.29 RMSE on a continuous surface, this measures ~0.10-0.13); and
    push-pull diffusion over pixels whose winning slice has no fine-scale
    energy (terracing -- defocused glow rings have gradients but no fine
    detail, so they are interpolated over instead of believed).
    """
    n = len(lumas)
    h2, w2 = lumas[0].shape
    depth = np.zeros((h2, w2), np.uint8)
    conf = np.full((h2, w2), -1.0, np.float32)
    left = np.full((h2, w2), -1.0, np.float32)     # field at winner - 1
    right = np.full((h2, w2), -1.0, np.float32)    # field at winner + 1
    prev = None
    for i in range(n):
        field = _sharpness(lumas[i])
        better = field > conf
        depth[better] = i
        left[better] = prev[better] if prev is not None else -1.0
        right[better] = -1.0
        if i > 0:
            landed = (depth == i - 1) & ~better
            right[landed] = field[landed]
        np.maximum(conf, field, out=conf)
        prev = field
        if progress:
            progress("measuring", i + 1, total or n)

    raw = depth.copy()
    guide = np.take_along_axis(lumas, depth[None].astype(np.int64), 0)[0]
    depth = _refine_depth(depth, guide, conf)
    depth = cv2.medianBlur(depth, DEPTH_MEDIAN)

    # Sub-slice offset where the refinement kept the argmax winner --
    # elsewhere the fields describe a peak the refinement rejected.
    l0 = np.log(conf + 1e-9)
    ll = np.log(np.maximum(left, 0) + 1e-9)
    lr = np.log(np.maximum(right, 0) + 1e-9)
    denom = ll - 2 * l0 + lr
    with np.errstate(divide="ignore", invalid="ignore"):
        dz = 0.5 * (ll - lr) / denom
    ok = ((depth == raw) & (left >= 0) & (right >= 0)
          & (denom < -1e-9) & (raw > 0) & (raw < n - 1))
    out = depth.astype(np.float32) + np.where(
        ok, np.clip(np.nan_to_num(dz), -0.5, 0.5), 0.0).astype(np.float32)

    # Fine-scale energy of the slice the *refined* depth actually selects
    # (measured: gathering at the raw argmax instead costs 8.5% -> 14.2%
    # wrong in the glow band). It answers "does this pixel's winner carry
    # real detail, or is it a defocused edge's ring?" -- a question
    # Tenengrad confidence gets backwards -- and both the diffusion and the
    # blend width hang off it.
    lo, hi = diffuse if diffuse else (DIFFUSE_LO, DIFFUSE_HI)
    win = np.take_along_axis(lumas, depth[None].astype(np.int64), 0)[0]
    lap = cv2.Laplacian(win, cv2.CV_32F, ksize=3)
    fine = cv2.GaussianBlur(lap * lap, (0, 0), 1.0)
    t = fine / (np.percentile(fine, 90) + 1e-9)
    t = np.clip((t - lo) / (hi - lo), 0.0, 1.0)
    trust = t * t * (3 - 2 * t)

    # Dilating the trust before it is used keeps a pixel next to confident
    # detail out of the interpolation: faint striae score low against a
    # global percentile that the specimen's bright edges set, and were
    # visibly softened without this. Glow aprons are large and uniformly
    # untrusted, so they still diffuse.
    if dilate:
        trust = cv2.dilate(trust, np.ones((dilate, dilate), np.uint8))
    if diffuse is not None:
        out = _push_pull(out, trust)
    width = BLEND_NARROW + (wide - BLEND_NARROW) * (1 - trust) ** power
    return out, width.astype(np.float32)


def _register(lumas: list[np.ndarray]) -> list[tuple[float, float]]:
    """Cumulative shift of each slice relative to the first, half-res px."""
    shifts = [(0.0, 0.0)]
    hann = None
    for a, b in zip(lumas, lumas[1:]):
        if hann is None:
            hann = cv2.createHanningWindow((a.shape[1], a.shape[0]),
                                           cv2.CV_32F)
        # OpenCV 5's phaseCorrelate applies the window to its inputs IN
        # PLACE. Without the copies, middle slices got windowed twice (once
        # as `a`, once as `b`) and end slices once, poisoning every
        # across-z comparison downstream -- found when the terrace scene
        # measured 6.4% wrong through the bench and 70% through merge().
        (dx, dy), response = cv2.phaseCorrelate(a.copy(), b.copy(), hann)
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


def merge(directory: Path | str, progress=None, output: str = "bayer",
          smoothing: str = "normal",
          feather: float = FEATHER_SIGMA) -> tuple[Path, dict]:
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

    # Align the lumas in place, then hand the stack to the depth pipeline.
    # tools/stack_bench.py drives the same function on synthetic scenes, so
    # what is measured there is what runs here.
    h2, w2 = lumas[0].shape
    aligned = np.empty((n, h2, w2), np.float32)
    for i, (luma, (sx, sy)) in enumerate(zip(lumas, shifts)):
        if abs(sx) > 0.01 or abs(sy) > 0.01:
            m = np.float32([[1, 0, -sx], [0, 1, -sy]])
            luma = cv2.warpAffine(luma, m, (w2, h2),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
        aligned[i] = luma
    del lumas
    gate, wide, dil, power = SMOOTHING.get(smoothing, SMOOTHING["normal"])
    depth, width = _depth_map(aligned, progress=progress, total=n,
                              diffuse=gate, wide=wide, dilate=dil,
                              power=power)
    del aligned

    # Blend at full resolution, one slice at a time: demosaic, align, add.
    # Weights follow the *refined* depth, not the raw sharpness fields, or
    # every halo the refinement removed would sneak back in through the
    # blend. Each slice gets a hat around its own index, of a per-pixel
    # width: narrow where the winner carries real detail (so a sharp
    # feature comes from its own slice, not an average with its
    # neighbour), wide where nothing is in focus (so the glow becomes the
    # smooth superposition it physically is, instead of two slices' rings
    # at partial opacity). Variable width means the hats no longer sum to
    # one on their own, so normalise -- once, up front, at half res.
    h, w = raws[0].shape
    norm = np.zeros_like(depth)
    for i in range(n):
        norm += np.clip(1.0 - np.abs(depth - i) / width, 0.0, 1.0)
    np.maximum(norm, 1e-6, out=norm)

    acc = np.zeros((h, w, 3), np.float32)
    for i, (raw, (sx, sy)) in enumerate(zip(raws, shifts)):
        hat = np.clip(1.0 - np.abs(depth - i) / width, 0.0, 1.0) / norm
        if not (hat > 1e-4).any():
            if progress:
                progress("blending", i + 1, n)
            continue
        wgt = cv2.GaussianBlur(hat, (0, 0), feather) if feather > 0 else hat
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
    dmap = np.clip(depth / max(n - 1, 1) * 255, 0, 255).astype(np.uint8)
    cv2.imwrite(str(session.dir / "depth.png"), dmap)
    cv2.imwrite(str(session.dir / "depth_view.png"),
                cv2.applyColorMap(dmap, cv2.COLORMAP_VIRIDIS))

    report = {
        "output": output,
        "slices": n,
        "width": w, "height": h,
        "max_breathing_px": round(max(abs(v) for s in shifts for v in s) * 2,
                                  1),
        "depth_levels": int(len(np.unique(np.round(depth)))),
    }
    return written, report


if __name__ == "__main__":
    path, report = merge(sys.argv[1])
    print(f"{path}  {report}")
