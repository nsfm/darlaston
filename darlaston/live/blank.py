"""Deciding whether a frame is empty slide.

Lives here rather than in calib/ because the live pipeline computes it
every frame, and live/ must not depend on anything heavier than itself.
"""
from __future__ import annotations

import cv2
import numpy as np


class BlankDetector:
    """Decides whether a preview frame is empty slide.

    Deliberately conservative: a false positive silently poisons the flat with
    a diatom, and a flat with a subject baked into it stamps an inverse ghost
    on every frame it ever corrects.
    """

    #: A single patch this far above its surroundings vetoes blankness,
    #: however quiet the rest of the frame is. Calibrated on synthetic
    #: fields with optical shading: empty glass reads 0.046 whether or not
    #: it carries dust, and one specimen reads 0.156. The threshold sits
    #: between them with room on both sides.
    PATCH_LIMIT = 0.09
    #: Side of the pooling window, in the 256-square working image.
    PATCH = 16

    def __init__(self, structure_limit: float = 0.012,
                 patch_limit: float | None = None) -> None:
        self._limit = structure_limit
        self._patch_limit = (self.PATCH_LIMIT if patch_limit is None
                             else patch_limit)

    def looks_blank(self, gray: np.ndarray) -> bool:
        small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA)
        f = small.astype(np.float32)
        mean = float(f.mean())
        if mean < 12 or mean > 245:
            return False              # black or blown tells us nothing
        # High-pass relative to local level: real structure, not shading.
        low = cv2.GaussianBlur(f, (0, 0), 12)
        detail = np.abs(f - low) / max(mean, 1e-6)
        if float(detail.mean()) >= self._limit:
            return False

        # And then, separately: no *patch* of the frame may carry structure.
        #
        # The test above is a frame-wide mean, which dilutes a few specimens
        # on a lot of empty ground into nothing -- and a mounted arrangement
        # is mostly empty ground with specimens on it. Measured on synthetic
        # fields: four obvious diatoms covering 0.9% of the frame averaged
        # out to 0.0084 against a 0.012 limit and were declared empty slide.
        #
        # It matters more than a wasted grab. A flat with a subject baked
        # into it stamps that subject's inverse onto every frame it ever
        # corrects, and nothing downstream can tell that happened.
        pooled = cv2.blur(detail, (self.PATCH, self.PATCH))
        return float(pooled.max()) < self._patch_limit
