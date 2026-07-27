"""Mapping the preview back to raw, so the histogram stops lying.

The live view is the ISP's 8-bit output, and the ISP has to boost blue about
three times to neutralise this sensor. Measured against a raw frame of the same
scene: a naive `preview == 255` test reported **22.26%** of pixels clipped
where the raw had **0.69%**. Blue accounted for nearly all of it -- 62% of blue
sites blown in the preview, and *not one* of them at raw saturation.

The transform is deterministic for a fixed ISP configuration, so it can be
measured once from a single ISP+raw pair and inverted thereafter. That gives an
estimated raw histogram at full frame rate with no extra pulls.

One honest limitation: above each channel's saturation point the inverse is a
**floor**, not a value. Blue at 255 means "raw >= 568", and 568 cannot be
distinguished from 4000. That is exactly enough to answer the clipping question
and not enough to reconstruct the top of the histogram.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Canonical GBRG phases, paired with their channel index in an RGB preview.
_SITES = {0: ((1, 0), "R"), 1: ((0, 0), "G"), 2: ((0, 1), "B")}


@dataclass
class PreviewLUT:
    """Per-channel preview value -> estimated raw value."""

    #: (3, 256) of estimated raw for each preview level.
    table: np.ndarray
    #: *Lowest* raw value that already pins the preview to 255 -- not the mean
    #: of the values that do. The question is whether preview 255 implies a
    #: full sensor, and that turns on where the pinning starts.
    saturation: tuple[int, int, int]
    #: (low, high) preview levels the calibration scene actually exercised.
    #: Outside this the table is extrapolation, not measurement -- a phase
    #: image with a bright background never visits the low end, and the curve
    #: there is a flat guess. Say so rather than quietly returning it.
    valid_range: tuple[tuple[int, int], ...] = ((0, 255),) * 3
    white_level: int = 4095

    def is_measured(self, channel: int, preview: int) -> bool:
        lo, hi = self.valid_range[channel]
        return lo <= preview <= hi

    def estimate_raw(self, channel: int, preview: np.ndarray) -> np.ndarray:
        return self.table[channel][np.clip(preview, 0, 255)]

    @property
    def best_channel(self) -> int:
        """The channel whose pinning says the most about the sensor.

        Whichever saturates latest: it is the one that stays informative
        longest. On this camera that is green -- blue is boosted about three
        times to neutralise the sensor and so pins at 14% of full scale, where
        it says nothing at all.
        """
        return int(np.argmax(self.saturation))

    def headroom(self, preview_rgb: np.ndarray) -> tuple[float, float]:
        """`(fraction pinned, implied raw level as a fraction of full scale)`.

        Two numbers rather than one, because either alone misleads. "4.6% of
        pixels pinned" means nothing without knowing that pinning starts at 80%
        of full scale; "80%" means nothing without knowing how much of the
        frame is there.

        Deliberately not called `clipped`. Above the saturation point the
        inverse is a floor: green at 255 means raw >= 3282, which cannot be
        told from 4095. This warns *before* the damage rather than after,
        which is the more useful moment anyway.
        """
        ch = self.best_channel
        pinned = float((preview_rgb[:, :, 2 - ch] >= 255).mean())
        return pinned, self.saturation[ch] / self.white_level

    def to_dict(self) -> dict:
        return {"table": self.table.tolist(),
                "saturation": list(self.saturation),
                "valid_range": [list(r) for r in self.valid_range],
                "white_level": self.white_level}

    @classmethod
    def from_dict(cls, d: dict) -> "PreviewLUT":
        vr = d.get("valid_range") or [[0, 255]] * 3
        return cls(table=np.asarray(d["table"], np.float32),
                   saturation=tuple(d["saturation"]),
                   valid_range=tuple(tuple(r) for r in vr),
                   white_level=d.get("white_level", 4095))


def build(pairs, *, white_level: int = 4095) -> PreviewLUT:
    """Measure the mapping from ISP+raw pairs of the same scene.

    Accepts a list of `(isp_bgr, raw)` pairs, and wants **several at different
    exposures**. One frame does not exercise the whole preview range: measured
    on a real phase capture, green only ever visited preview 116-255 and blue
    163-255, so everything below was extrapolation. An exposure ramp sweeps the
    range properly.

    Each pair must be captured back to back with nothing touched in between --
    if the stage or the lamp moves, the correspondence is gone.
    """
    if isinstance(pairs, np.ndarray) or (len(pairs) == 2
                                         and isinstance(pairs[0], np.ndarray)
                                         and pairs[0].ndim == 3):
        pairs = [tuple(pairs)]        # tolerate a single pair
    pairs = [p for p in pairs if p[0].shape[:2] == p[1].shape[:2]]
    if not pairs:
        raise ValueError("no usable ISP/raw pairs -- sizes must match")

    table = np.zeros((3, 256), np.float32)
    saturation: list[int] = []
    ranges: list[tuple[int, int]] = []
    for ch, ((dy, dx), _name) in _SITES.items():
        sums = np.zeros(256, np.float64)
        counts = np.zeros(256, np.float64)
        pinned: list[np.ndarray] = []
        for isp_bgr, raw in pairs:
            # Compare each Bayer site against the preview channel it feeds.
            r = raw[dy::2, dx::2].astype(np.int32).ravel()
            p = isp_bgr[dy::2, dx::2, 2 - ch].astype(np.int32).ravel()
            sums += np.bincount(p, weights=r, minlength=256)
            counts += np.bincount(p, minlength=256)
            hit = r[p >= 255]
            if hit.size:
                pinned.append(hit)
        seen = counts > 20
        curve = np.zeros(256, np.float32)
        curve[seen] = (sums[seen] / counts[seen]).astype(np.float32)  # mean raw
        curve = _fill_gaps(curve, seen)
        # Must be monotonic to be invertible; the ISP is, up to noise.
        table[ch] = np.maximum.accumulate(curve)

        # Where the pinning *starts*. A low percentile rather than the
        # minimum, so one noisy pixel cannot set the threshold for the sensor.
        if pinned:
            sat = int(np.percentile(np.concatenate(pinned), 1.0))
        else:
            sat = white_level
        saturation.append(sat)
        idx = np.nonzero(seen)[0]
        ranges.append((int(idx[0]), int(idx[-1])) if idx.size else (0, 0))

    return PreviewLUT(table=table, saturation=tuple(saturation),
                      valid_range=tuple(ranges), white_level=white_level)


def _fill_gaps(curve: np.ndarray, seen: np.ndarray) -> np.ndarray:
    """Interpolate preview levels no pixel happened to land on.

    A single scene does not exercise all 256 levels, and leaving the gaps at
    zero would make the curve non-monotonic in a way that is purely an artefact
    of sampling.
    """
    idx = np.nonzero(seen)[0]
    if idx.size < 2:
        return curve
    return np.interp(np.arange(256), idx, curve[idx]).astype(np.float32)
