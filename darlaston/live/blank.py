"""Deciding whether a frame is empty slide.

Lives here rather than in calib/ because the live pipeline computes it
every frame, and live/ must not depend on anything heavier than itself.
"""
from __future__ import annotations

import cv2
import numpy as np


def as_field(frame: np.ndarray) -> np.ndarray:
    """One sensitivity group of a frame, as something blankness can read.

    A raw Bayer frame cannot go into `looks_blank` as it stands. Its four
    phases differ by up to three times on this sensor -- that is what
    `normalise_flat` exists to handle -- so the mosaic itself reads as an
    enormous amount of structure in every 2x2 cell, and every frame looks
    busy. Taking a single phase removes it without interpolating
    anything, at half resolution in each axis.

    A demosaiced frame gives its green channel, which carries most of the
    detail, for the same reason the focus metric reads green.
    """
    a = np.asarray(frame)
    if a.ndim == 3:
        return a[:, :, 1]
    return a[0::2, 0::2]


class BlankDetector:
    """Decides whether a preview frame is empty slide.

    Deliberately conservative: a false positive silently poisons the flat with
    a diatom, and a flat with a subject baked into it stamps an inverse ghost
    on every frame it ever corrects.
    """

    #: Pooling windows in the 256-square working image, each with the value
    #: that vetoes blankness, however quiet the rest of the frame is.
    #:
    #: Two of them, because one window only sees subjects near its own
    #: size. Pooling dilutes a specimen by the ratio of its area to the
    #: window's, so a 16-wide window buries anything much smaller than
    #: itself: measured on synthetic fields with optical shading, at 16 a
    #: lone specimen of radius 20 px in the preview reads 0.091 to 0.139
    #: against this 0.09 -- it is only just seen -- and one of radius 10
    #: reads 0.036 to 0.041, which is empty glass. The detector could not
    #: see a small subject at all.
    #:
    #: Halving the window recovers a factor of two in the smallest subject
    #: found. Over 60 synthetic fields with dust of varying density and
    #: contrast: at a window of 8, dust reaches 0.071 and a lone radius-10
    #: specimen starts at 0.081. That gap is narrower than the one at 16,
    #: so the limit sits at the *dust* end of it rather than the middle --
    #: a field wrongly called busy costs one skipped opportunity to bank a
    #: flat, and a field wrongly called blank bakes a specimen into the
    #: flat and stamps its inverse on every frame thereafter. Those are not
    #: comparable, so the threshold does not sit between them.
    #:
    #: Both numbers are synthetic and want confirming on the bench. Neither
    #: window sees a specimen of radius 6, or a faint one: at 8 both read
    #: within the dust range. This is better than it was, not solved.
    PATCHES = ((16, 0.09), (8, 0.070))

    def __init__(self, structure_limit: float = 0.012,
                 patch_limit: float | None = None) -> None:
        self._limit = structure_limit
        # An override scales every window's limit together, so a caller
        # loosening or tightening the detector keeps the relationship the
        # measurements above established between the two.
        scale = (1.0 if patch_limit is None
                 else patch_limit / self.PATCHES[0][1])
        self._patches = tuple((w, lim * scale) for w, lim in self.PATCHES)

    def looks_blank(self, gray: np.ndarray, white: int = 255) -> bool:
        """Is this empty slide?

        `white` is full scale for the data given. Everything below works
        on detail relative to the local level, which is scale-free, but
        the too-dark and too-bright guards are not -- and a 12-bit raw
        frame has a mean around 1000, so with the 8-bit numbers hard-coded
        every raw frame was refused as "blown" before any of the real work
        ran.
        """
        small = cv2.resize(gray, (256, 256), interpolation=cv2.INTER_AREA)
        f = small.astype(np.float32)
        mean = float(f.mean())
        if mean < white * 0.047 or mean > white * 0.961:
            return False              # black or blown tells us nothing
        # High-pass relative to local level: real structure, not shading.
        low = cv2.GaussianBlur(f, (0, 0), 12)
        detail = np.abs(f - low) / max(mean, 1e-6)
        if float(detail.mean()) >= self._limit:
            return False

        # And then, separately: no *patch* of the frame may carry structure,
        # at any of the sizes we look at.
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
        for window, limit in self._patches:
            if float(cv2.blur(detail, (window, window)).max()) >= limit:
                return False
        return True
