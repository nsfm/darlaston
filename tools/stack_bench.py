"""Measured fence-posts for the stack merge.

Synthetic scenes with known ground truth, each modelling a failure a real
stack session actually produced, and a table of candidates x metrics. Every
change to the merge must move these numbers before it ships; that rule has
already killed two plausible fixes (guided-filter pooling of the sharpness
field, joint-bilateral pooling) that measured worse than what they replaced.

Scenes:
  halo     -- bright textured disk over fine background, two slices, glow
              spreading past the boundary. The original halo case.
  terrace  -- the same disk across many slices over weakly textured glass.
              Nate's field report: stepped ridges in the glow band, one per
              exposure -- integer depth quantisation made visible by the
              defocused edge's rings.
  slope    -- a textured plane tilting continuously through the whole stack.
              Ground-truth depth is a ramp; integer depth costs ~0.29 RMSE
              (uniform quantisation), sub-slice methods should beat it.
  sparse   -- textured spots on near-featureless glass. Tests whether
              unreliable regions adopt their neighbourhood's depth instead
              of noise.

Run: PYTHONPATH=. .venv/bin/python tools/stack_bench.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from darlaston.process.stack import (_depth_map, _luma_half, _refine_depth,
                                     _sharpness, DEPTH_MEDIAN)

WHITE = 4095.0
BLUR_PER_SLICE = 3.0        # defocus sigma per slice of z distance


# ---- scene construction -----------------------------------------------------

def _texture(rng, shape, scale, lo, hi):
    t = cv2.GaussianBlur(rng.normal(size=shape).astype(np.float32),
                         (0, 0), scale)
    t = (t - t.min()) / (np.ptp(t) + 1e-9)
    return lo + t * (hi - lo)


def _render(planes, n):
    """Composite [(content, alpha, z), ...] far-to-near into n slices.

    Each slice k blurs every plane by BLUR_PER_SLICE * |k - z|, alpha
    premultiplied, so a bright defocused edge spreads glow exactly the way
    a phase-contrast subject does.
    """
    slices = []
    for k in range(n):
        acc = None
        for content, alpha, z in planes:
            sigma = BLUR_PER_SLICE * abs(k - z)
            if sigma > 0.01:
                pre = cv2.GaussianBlur(content * alpha, (0, 0), sigma)
                a = cv2.GaussianBlur(alpha, (0, 0), sigma)
            else:
                pre, a = content * alpha, alpha
            acc = pre.copy() if acc is None else pre + (1 - a) * acc
        slices.append(np.clip(acc, 0, WHITE))
    return slices


def scene_halo():
    rng = np.random.default_rng(7)
    H, W = 600, 800
    bg = _texture(rng, (H, W), 1.2, 500, 1100)
    disk = _texture(rng, (H, W), 2.0, 2600, 3600)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dist = np.sqrt((xx - 400) ** 2 + (yy - 300) ** 2)
    alpha = np.clip(130 + 1.5 - dist, 0, 1).astype(np.float32)
    slices = _render([(bg, np.ones_like(bg), 0.0), (disk, alpha, 1.0)], 2)
    gt = np.where(cv2.resize(alpha, (W // 2, H // 2),
                             interpolation=cv2.INTER_AREA) > 0.5, 1.0, 0.0)
    d2 = cv2.resize(dist, (W // 2, H // 2),
                    interpolation=cv2.INTER_AREA) / 2
    band = (gt < 0.5) & (d2 < 65 + 20)
    rim = (gt > 0.5) & (d2 > 65 - 20)
    return slices, gt, {"halo band": band, "subject rim": rim}


def scene_terrace(n=6):
    rng = np.random.default_rng(11)
    H, W = 600, 800
    glass = _texture(rng, (H, W), 1.0, 700, 950)      # weak, like clean glass
    disk = _texture(rng, (H, W), 2.0, 2800, 3800)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dist = np.sqrt((xx - 400) ** 2 + (yy - 300) ** 2)
    alpha = np.clip(120 + 1.5 - dist, 0, 1).astype(np.float32)
    z_glass, z_disk = 1.0, float(n - 2)
    slices = _render([(glass, np.ones_like(glass), z_glass),
                      (disk, alpha, z_disk)], n)
    a2 = cv2.resize(alpha, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
    d2 = cv2.resize(dist, (W // 2, H // 2), interpolation=cv2.INTER_AREA) / 2
    gt = np.where(a2 > 0.5, z_disk, z_glass).astype(np.float32)
    band = (a2 < 0.5) & (d2 < 60 + 45)                # the glow apron
    return slices, gt, {"glow band": band}


def scene_slope(n=8):
    rng = np.random.default_rng(13)
    H, W = 480, 800
    tex = _texture(rng, (H, W), 1.0, 600, 3200)
    ramp = np.linspace(0, n - 1, W, dtype=np.float32)
    # Piecewise-constant approximation of continuously varying defocus:
    # narrow vertical bands, each blurred for its own z, feather-joined.
    bands = 32
    edges = np.linspace(0, W, bands + 1).astype(int)
    slices = []
    for k in range(n):
        out = np.empty((H, W), np.float32)
        for b in range(bands):
            x0, x1 = edges[b], edges[b + 1]
            z = float(ramp[(x0 + x1) // 2])
            sigma = BLUR_PER_SLICE * abs(k - z)
            src = cv2.GaussianBlur(tex, (0, 0), sigma) if sigma > 0.01 \
                else tex
            out[:, x0:x1] = src[:, x0:x1]
        slices.append(out)
    gt = np.tile(cv2.resize(ramp[None], (W // 2, 1))[0], (H // 2, 1))
    interior = np.zeros((H // 2, W // 2), bool)
    interior[:, 20:-20] = True
    return slices, gt, {"ramp": interior}


def scene_sparse(n=5):
    rng = np.random.default_rng(17)
    H, W = 600, 800
    glass = _texture(rng, (H, W), 1.5, 780, 860)      # nearly featureless
    z_glass = 1.0
    planes = [(glass, np.ones_like(glass), z_glass)]
    gt = np.full((H // 2, W // 2), z_glass, np.float32)
    spots = np.zeros((H // 2, W // 2), bool)
    for i, (cx, cy, z) in enumerate([(150, 150, 3.0), (600, 200, 2.0),
                                     (300, 450, 0.0), (650, 480, 3.5)]):
        tex = _texture(rng, (H, W), 1.0, 1500, 3000)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        a = np.clip(45 + 1.5 - d, 0, 1).astype(np.float32)
        planes.append((tex, a, z))
        a2 = cv2.resize(a, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
        gt = np.where(a2 > 0.5, z, gt)
        spots |= a2 > 0.5
    slices = _render(planes, n)
    gaps = ~cv2.dilate(spots.astype(np.uint8),
                       np.ones((25, 25), np.uint8)).astype(bool)
    return slices, gt, {"spots": spots, "glass gaps": gaps}


def scene_drift(n=6):
    """The terrace scene with +-3%% per-slice exposure drift -- terracing's
    third mechanism (after quantisation and glow), named by both Zerene
    (corrects it by default) and Helicon (blames it for uniform-background
    banding). A level step becomes visible wherever a depth territory
    boundary crosses a smooth region."""
    rng = np.random.default_rng(23)
    slices, gt, regions = scene_terrace(n)
    gains = 1.0 + rng.uniform(-0.03, 0.03, n)
    return [np.clip(s * g, 0, WHITE) for s, g in zip(slices, gains)], \
        gt, regions


def scene_slope_noisy(n=8):
    """The slope with photon-ish noise, because the sub-slice fit's failure
    mode is noise on a broad peak (focus-stack's Guo fit exists for this)."""
    rng = np.random.default_rng(29)
    slices, gt, regions = scene_slope(n)
    out = []
    for s in slices:
        noise = rng.normal(0, 1, s.shape).astype(np.float32) \
            * np.sqrt(np.maximum(s, 1)) * 1.5
        out.append(np.clip(s + noise, 0, WHITE))
    return out, gt, regions


SCENES = {
    "halo": scene_halo,
    "terrace": scene_terrace,
    "drift": scene_drift,
    "slope": scene_slope,
    "slope-noisy": scene_slope_noisy,
    "sparse": scene_sparse,
}


# ---- candidates -------------------------------------------------------------
# Each takes the slice lumas (half-res already: the bench feeds full-res
# scene images through _luma_half) and returns a float depth map.

def _fields(lumas):
    return np.stack([_sharpness(l) for l in lumas])


def cand_argmax(lumas):
    """No refinement at all -- the floor."""
    f = _fields(lumas)
    return cv2.medianBlur(np.argmax(f, 0).astype(np.uint8),
                          DEPTH_MEDIAN).astype(np.float32)


def cand_shipped(lumas):
    """What merge() does today: argmax, weighted median, median."""
    f = _fields(lumas)
    depth = np.argmax(f, 0).astype(np.uint8)
    conf = f.max(0)
    guide = np.take_along_axis(np.stack(lumas), depth[None].astype(int),
                               0)[0]
    depth = _refine_depth(depth, guide, conf)
    return cv2.medianBlur(depth, DEPTH_MEDIAN).astype(np.float32)


def _parabolic_dz(fields, depth_int):
    """Sub-slice offset: log-parabola (Gaussian interpolation) through the
    focus measure at the winner and its two z-neighbours. Flat or inverted
    fits, and winners at either end of the stack, keep dz = 0."""
    n = fields.shape[0]
    k = depth_int.astype(np.int64)
    kl = np.clip(k - 1, 0, n - 1)
    kr = np.clip(k + 1, 0, n - 1)
    take = lambda idx: np.take_along_axis(fields, idx[None], 0)[0]
    l0 = np.log(take(k) + 1e-9)
    ll = np.log(take(kl) + 1e-9)
    lr = np.log(take(kr) + 1e-9)
    denom = ll - 2 * l0 + lr
    with np.errstate(divide="ignore", invalid="ignore"):
        dz = 0.5 * (ll - lr) / denom
    dz = np.where(denom < -1e-9, dz, 0.0)
    dz[(k == 0) | (k == n - 1)] = 0.0
    return np.clip(dz, -0.5, 0.5).astype(np.float32)


def _fine_confidence(lumas, depth_int):
    """Fine-scale energy of the *winning* slice: |Laplacian|^2, barely
    pooled. A genuinely focused texture has energy at the finest scale; a
    defocused edge's ring -- however strong its broad gradient -- does not.
    This is the discriminator the glow band needs: Tenengrad conf believes
    the rings, this does not."""
    lum = np.stack(lumas)
    win = np.take_along_axis(lum, depth_int[None].astype(int), 0)[0]
    lap = cv2.Laplacian(win, cv2.CV_32F, ksize=3)
    return cv2.GaussianBlur(lap * lap, (0, 0), 1.0)


def _push_pull(depth, weight):
    """Fill low-weight regions by pyramid diffusion from high-weight ones.
    Confident pixels keep their depth; unconfident ones take a smooth
    interpolation of the nearest confident neighbourhood -- no luma
    involved, so glow structure cannot terrace it."""
    levels = []
    dw, w = (depth * weight).astype(np.float32), weight.astype(np.float32)
    while min(w.shape) > 4:
        levels.append((dw, w))
        dw, w = cv2.pyrDown(dw), cv2.pyrDown(w)
    filled = dw / np.maximum(w, 1e-9)
    for dw, w in reversed(levels):
        filled = cv2.pyrUp(filled, dstsize=(w.shape[1], w.shape[0]))
        alpha = np.clip(w, 0.0, 1.0)
        filled = alpha * (dw / np.maximum(w, 1e-9)) + (1 - alpha) * filled
    return filled


def cand_parabolic(lumas):
    """Shipped + sub-slice offset where the refinement agreed with the
    argmax (elsewhere the fields describe a peak the refinement rejected)."""
    f = _fields(lumas)
    raw = np.argmax(f, 0).astype(np.uint8)
    conf = f.max(0)
    guide = np.take_along_axis(np.stack(lumas), raw[None].astype(int), 0)[0]
    refined = _refine_depth(raw, guide, conf)
    refined = cv2.medianBlur(refined, DEPTH_MEDIAN)
    dz = _parabolic_dz(f, refined) * (refined == raw)
    return refined.astype(np.float32) + dz


def _diffusion_weight(fine, lo=0.05, hi=0.25):
    """Smoothstep of fine-scale energy between `lo` and `hi` of its own
    90th percentile. Placed by measurement: on the terrace scene the
    misassigned glow pixels sit at fine/p90 ~ 0.01-0.09 and genuine
    texture at ~0.16+, so the ramp straddles the gap. (Tenengrad
    confidence cannot make this cut at all -- the wrong pixels measure
    *more* confident.)"""
    t = fine / (np.percentile(fine, 90) + 1e-9)
    t = np.clip((t - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def cand_diffused(lumas):
    """Shipped, then push-pull diffusion over pixels whose *winning slice*
    has no fine-scale energy (glow, glass): their depth is interpolated
    from confident neighbours instead of believed."""
    base = cand_shipped(lumas)
    fine = _fine_confidence(lumas, np.round(base))
    return _push_pull(base, _diffusion_weight(fine))


def cand_full(lumas):
    """Parabolic sub-slice depth + fine-confidence diffusion."""
    base = cand_parabolic(lumas)
    fine = _fine_confidence(lumas, np.round(base))
    return _push_pull(base, _diffusion_weight(fine))


def cand_production(lumas):
    """darlaston.process.stack._depth_map -- the pipeline merge() runs.
    If this ever measures worse than a research candidate above, that
    candidate is the next port."""
    return _depth_map(np.stack(lumas))


def _sml(luma, pool=1.5):
    """Sum-modified-Laplacian (Nayar & Nakagawa): |Ixx| + |Iyy|, pooled.
    A defocused bright edge is a broad near-linear ramp -- huge gradient,
    near-zero second derivative -- so unlike Tenengrad this measure is not
    seduced by glow. The canonical shape-from-focus operator."""
    kx = np.float32([[-1, 2, -1]])
    ml = (np.abs(cv2.filter2D(luma, cv2.CV_32F, kx))
          + np.abs(cv2.filter2D(luma, cv2.CV_32F, kx.T)))
    return cv2.GaussianBlur(ml * ml, (0, 0), pool)


def _weber(luma, field, k=15):
    """Normalise a focus measure by local mean intensity, so dim in-focus
    texture is not outvoted by bright smooth glow."""
    return field / (cv2.boxFilter(luma, -1, (k, k)) + 1.0)


def _pipeline_with_measure(lumas, measure):
    """The full production pipeline with a swapped-in focus measure."""
    f = np.stack([measure(l) for l in lumas])
    raw = np.argmax(f, 0).astype(np.uint8)
    conf = f.max(0)
    guide = np.take_along_axis(np.stack(lumas), raw[None].astype(int), 0)[0]
    refined = _refine_depth(raw, guide, conf)
    refined = cv2.medianBlur(refined, DEPTH_MEDIAN)
    dz = _parabolic_dz(f, refined) * (refined == raw)
    base = refined.astype(np.float32) + dz
    fine = _fine_confidence(lumas, refined)
    return _push_pull(base, _diffusion_weight(fine, 0.1, 0.35))


def cand_sml(lumas):
    return _pipeline_with_measure(lumas, _sml)


def cand_sml_weber(lumas):
    return _pipeline_with_measure(lumas, lambda l: _weber(l, _sml(l)))


def cand_ten_weber(lumas):
    return _pipeline_with_measure(lumas, lambda l: _weber(l, _sharpness(l)))


def _bandpass_measure(l, cutoff=25.0):
    """Tenengrad+Weber on a band-passed luma: the phase-contrast glow skirt
    is a smooth low-frequency pedestal (Yin/Kanade/Chen 2012), so remove
    it before the measure ever sees it."""
    band = l - cv2.GaussianBlur(l, (0, 0), cutoff)
    gx = cv2.Sobel(band, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(band, cv2.CV_32F, 0, 1)
    mag = cv2.GaussianBlur(gx * gx + gy * gy, (0, 0), 1.5)
    return mag / (cv2.boxFilter(l, -1, (15, 15)) + 1.0)


def cand_bandpass(lumas):
    return _pipeline_with_measure(lumas, _bandpass_measure)


def _guo_mu(fields):
    """Whole-curve Gaussian fit over z (Guo's weighted linear LSQ on the
    log, weights y^2) -- what PetteriAimonen/focus-stack ships. Returns
    (mu, ok): continuous peak position and a validity mask. Streaming
    accumulators; z centred and fm normalised for float32 safety."""
    n = fields.shape[0]
    fm = fields / (fields.max() + 1e-12)
    zc = np.arange(n, dtype=np.float32) - (n - 1) / 2
    S = [np.zeros(fields.shape[1:], np.float32) for _ in range(5)]
    T = [np.zeros(fields.shape[1:], np.float32) for _ in range(3)]
    for i in range(n):
        y = np.maximum(fm[i], 1e-6)
        y2 = y * y
        ln = np.log(y)
        z = zc[i]
        for p in range(5):
            S[p] += y2 * z ** p
        for p in range(3):
            T[p] += y2 * ln * z ** p
    s0, s1, s2, s3, s4 = S
    t0, t1, t2 = T
    # Cramer's rule for [[s0,s1,s2],[s1,s2,s3],[s2,s3,s4]] [a,b,c] = [t]
    det = (s0 * (s2 * s4 - s3 * s3) - s1 * (s1 * s4 - s3 * s2)
           + s2 * (s1 * s3 - s2 * s2))
    det = np.where(np.abs(det) < 1e-20, 1e-20, det)
    b = (s0 * (t1 * s4 - t2 * s3) - t0 * (s1 * s4 - s3 * s2)
         + s2 * (s1 * t2 - s2 * t1)) / det
    c = (s0 * (s2 * t2 - s3 * t1) - s1 * (s1 * t2 - s3 * t0)
         + t0 * (s1 * s3 - s2 * s2)) / det
    with np.errstate(divide="ignore", invalid="ignore"):
        mu = -b / (2 * c) + (n - 1) / 2
    ok = (c < -1e-5) & np.isfinite(mu)
    return np.nan_to_num(mu), ok


def cand_guo(lumas):
    """Production pipeline with the Guo fit supplying the sub-slice offset
    instead of the 3-point log-parabola."""
    f = np.stack([_weber(l, _sharpness(l)) for l in lumas])
    raw = np.argmax(f, 0).astype(np.uint8)
    conf = f.max(0)
    guide = np.take_along_axis(np.stack(lumas), raw[None].astype(int), 0)[0]
    refined = _refine_depth(raw, guide, conf)
    refined = cv2.medianBlur(refined, DEPTH_MEDIAN)
    mu, ok = _guo_mu(f)
    dz = np.where(ok & (np.abs(mu - refined) <= 1.0),
                  np.clip(mu - refined, -0.5, 0.5), 0.0).astype(np.float32)
    base = refined.astype(np.float32) + dz
    fine = _fine_confidence(lumas, refined)
    return _push_pull(base, _diffusion_weight(fine, 0.1, 0.35))


CANDIDATES = {
    "argmax+median": cand_argmax,
    "wm refine only": cand_shipped,
    "parabolic": cand_parabolic,
    "diffused": cand_diffused,
    "parabolic+diffused": cand_full,
    "production": cand_production,
    "sml measure": cand_sml,
    "sml+weber": cand_sml_weber,
    "tenengrad+weber": cand_ten_weber,
    "bandpass+ten+weber": cand_bandpass,
    "guo fit": cand_guo,
}


# ---- metrics ----------------------------------------------------------------

def measure(depth, gt, regions, lumas):
    """Per-region: depth RMSE, terrace count, and composite RMSE vs oracle."""
    out = {}
    lum = np.stack(lumas)

    def hat_composite(d):
        acc = np.zeros_like(lumas[0])
        for i in range(len(lumas)):
            w = np.clip(1.0 - np.abs(d - i), 0.0, 1.0)
            acc += lum[i] * w
        return acc

    comp = hat_composite(depth)
    oracle = hat_composite(gt)
    for name, region in regions.items():
        err = np.abs(depth[region] - gt[region])
        rmse = float(np.sqrt(np.mean(err ** 2)))
        wrong = float((err > 0.5).mean()) * 100
        levels = len(np.unique(np.round(depth[region] * 4) / 4))
        crmse = float(np.sqrt(np.mean((comp[region] - oracle[region]) ** 2)))
        out[name] = (rmse, wrong, levels, crmse)
    return out


def main(names=None):
    for scene_name, build in SCENES.items():
        slices, gt, regions = build()
        lumas = [_luma_half(np.clip(s, 0, WHITE).astype(np.uint16))
                 for s in slices]
        print(f"\n== {scene_name} ({len(slices)} slices) ==")
        print(f"{'candidate':<26s} {'region':<12s} "
              f"{'depth rmse':>10s} {'wrong%':>7s} {'levels':>7s} "
              f"{'comp rmse':>10s}")
        for cand_name, fn in CANDIDATES.items():
            if names and cand_name not in names:
                continue
            depth = fn(lumas)
            for region, (rmse, wrong, levels, crmse) in measure(
                    depth, gt, regions, lumas).items():
                print(f"{cand_name:<26s} {region:<12s} "
                      f"{rmse:>10.3f} {wrong:>6.1f}% {levels:>7d} "
                      f"{crmse:>10.1f}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
