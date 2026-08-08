"""Bounding how fast the depth map may change, which is the halo.

Jacobs, Baek and Levoy, *Focal Stack Compositing for Depth of Field
Control*, Stanford CS technical report 2012-1. Their result: a halo is two
composite pixels integrating the same ray, and that double counting happens
exactly when the slice-selection map changes faster than the blur can
justify. Halo-free composites *are* the Lipschitz-bounded selection maps.

The physics, since it is not obvious that this applies to an all-in-focus
composite at all: at an occlusion boundary the background point's ray cone
is partly blocked by the foreground, and the composite backfills the
blocked part with defocused foreground light. That happens whether or not
a finite aperture was chosen, because the objective has one regardless.
Their own worked example is an all-focus composite at exactly such a
boundary, and they name the price we pay for the fix: "for extended depth
of field composites, this manifests as blurriness near depth edges."

This module is deliberately not applied to `depth.png` or to the mesh. It
smooths real relief as well as artifact, measured at a threefold reduction
in specimen-interior roughness, and Nate judged the meshes better without
it. It earns its place on the photograph and nowhere else.

**The slope is not a derived constant, and that is a finding rather than a
gap.** The bound that looks best depends on how noisy the merge's depth map
was to begin with, which is a property of the subject: crossing diatoms
give genuine steep steps and want a permissive bound near 0.6, while a
smooth pseudoscorpion carapace wants 0.2 to 0.3. Estimating it from the
observed depth gradient in confident regions separates the two stacks by
only 1.1x to 1.8x where 2.4x is needed, so it is exposed as a control
instead. Physical quantities here are derived; this one is a matter of
which photograph looks better, and that has no derivable optimum.
"""
from __future__ import annotations

import cv2
import numpy as np

#: Blur radii to assume beyond what the thin lens model implies. The paper
#: recommends over-estimating halo extent, because real objectives depart
#: from that model at the margins, and halo extent scales with blur radius
#: so twice the extent is half the slope.
SAFETY = 2.0
#: Green light in microns, and the medium between the front element and
#: the coverslip. An oil objective would want 1.515.
LAMBDA = 0.55
N_MEDIUM = 1.0
#: Slices per depth of field. The community rule is three to four.
STEPS_PER_DOF = 3.0
#: Bounds offered to the operator, from the range measured across two very
#: different subjects, with room either side.
SLOPE_MIN, SLOPE_MAX = 0.10, 1.00


def default_slope(slices: int, magnification: float, na: float,
                  pitch_um: float, half_res: bool = True) -> float:
    """A starting bound from the optics, in slices of depth per pixel.

    Depth of field is the wave term plus the detector term; sampling it
    `STEPS_PER_DOF` times gives the z step a well shot stack uses; half
    the stack times that is its worst defocus; and geometric blur opens at
    the marginal ray angle. The map may then change at most the inverse of
    the blur that one slice of defocus produces.

    Every input is known at merge time. The answer is only a starting
    point, not an optimum: it came out at 0.352 for both of Nate's stacks
    while their measured optima were 0.6 and 0.25, which bracket it.
    """
    if not (magnification > 0 and 0 < na < N_MEDIUM and pitch_um > 0):
        return 0.35
    dof = LAMBDA * N_MEDIUM / (na ** 2) + N_MEDIUM * pitch_um / (
        magnification * na)
    step = dof / STEPS_PER_DOF
    worst = max(slices - 1, 1) / 2 * step
    blur_um = worst * na / np.sqrt(N_MEDIUM ** 2 - na ** 2)
    um_per_px = pitch_um / magnification * (2 if half_res else 1)
    per_slice = blur_um / max(um_per_px, 1e-6) / max((slices - 1) / 2, 1)
    return float(np.clip(1.0 / max(per_slice * SAFETY, 1e-6),
                         SLOPE_MIN, SLOPE_MAX))


def clamp(depth: np.ndarray, slope: float) -> np.ndarray:
    """Enforce a Lipschitz bound on `depth`, the paper's Algorithm 1.

    Written as one distance transform per distinct depth rather than as
    their iterative single-pixel dilation. The two are equivalent: each
    pixel joins the growing region exactly once, at its own distance, and
    the permitted interval widens monotonically with distance, so the
    first clamp a pixel receives is the binding one.

    It reduces steep transitions rather than forbidding them, and the
    difference matters if anyone later depends on the bound holding. Each
    depth value is visited once, so a pass can reintroduce a violation
    against a seed set an earlier pass established; the paper writes it
    this way and claims no convergence. On pure noise the residual is
    large. On what actually arrives here -- a map already median filtered
    and bilaterally refined -- the input is nothing like noise, which is
    the regime the measurements were taken in.

    Two details that are not cosmetic, both learned by getting them wrong.
    The seed set is read from the *running* map, because the paper mutates
    its selection in place and re-reads it; seeding from the original input
    lets every starting plateau re-assert itself as an attractor after it
    has legitimately been clamped away, which drags the whole map toward
    the stack mean and produces a clamp that can only destroy. And the
    iteration runs from the far slice inward, which favours the foreground
    at a boundary: reversing it costs 30% on halo and 19% on specimen
    contrast.
    """
    out = depth.astype(np.float64, copy=True)
    for s in sorted(np.unique(np.round(depth)), reverse=True):
        seed = np.round(out) == s
        if not seed.any():
            continue
        d = cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, 5)
        np.clip(out, s - slope * d, s + slope * d, out=out, where=~seed)
    return out.astype(np.float32)
