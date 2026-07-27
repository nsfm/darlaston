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

    def __init__(self, structure_limit: float = 0.012) -> None:
        self._limit = structure_limit

    def looks_blank(self, gray: np.ndarray) -> bool:
        small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA)
        f = small.astype(np.float32)
        mean = float(f.mean())
        if mean < 12 or mean > 245:
            return False              # black or blown tells us nothing
        # High-pass relative to local level: real structure, not shading.
        low = cv2.GaussianBlur(f, (0, 0), 12)
        detail = float(np.abs(f - low).mean() / max(mean, 1e-6))
        return detail < self._limit
