"""Focus coverage: knowing when a Z sweep is finished.

The question a hand-cranked stack cannot currently answer is *have I taken
enough slices?* It is a feel, it differs at every tile, and inconsistent stack
depth between tiles is a large part of why stacking and stitching refuse to
compose.

Two ideas make it answerable without any calibration.

**A pixel is covered once you have passed through its focal plane.** Not once
its sharpness exceeds some threshold -- sharpness has no meaningful scale across
subjects, magnifications or illumination modes, so any absolute threshold is a
constant that will be wrong somewhere. Instead: track each pixel's running
maximum, and mark it done when its current sharpness has *risen and then
fallen*. That is scale-free and it means exactly what it should.

**The denominator is pixels that contain something.** On darkfield, three
quarters of a frame is empty and will never be sharp at any Z. Counting it
would mean coverage could never reach 100% and the number would be useless. So
the floor is set relative to the sharpest thing seen so far, and pixels below it
are "nothing here" rather than "not yet covered".

Two consequences worth knowing. Racking up and stopping at focus reads as
incomplete -- correct, since you have not passed through and may not hold the
best slice. And **the percentage alone is not a stop signal**: regions that
begin far enough out of focus to look like empty field only join the
denominator once they come into range, so coverage can read 100% having seen
half the frame and then fall back. Completion therefore also requires that the
structured area has stopped growing.
"""
from __future__ import annotations

import cv2
import numpy as np


class FocusCoverage:
    """Accumulates which parts of the frame have been through focus."""

    #: A pixel counts as passed once it falls this far below its own peak.
    FALL = 0.8
    #: Structure floor, as a fraction of the sharpest thing seen anywhere.
    #: Below this a pixel is empty field rather than an unvisited subject.
    FLOOR = 0.15

    #: Frames the structured area must hold steady before completion is
    #: believed. Roughly a second at the rate the sharpness field is computed.
    SETTLE_FRAMES = 10

    def __init__(self, shape: tuple[int, int] | None = None) -> None:
        self._shape = shape
        self._peak: np.ndarray | None = None
        self._passed: np.ndarray | None = None
        self._area = 0
        self._steady = 0
        self.frames = 0
        # Derived once per update and reused. Recomputing `structure` on
        # every read cost three extra full-frame passes per frame, and
        # `_passed[has].mean()` allocated a compacted copy on top of that --
        # coverage measured 13.7 ms per frame against a 33 ms budget.
        self._structure: np.ndarray | None = None
        self._fraction = 0.0

    def reset(self) -> None:
        self._peak = None
        self._passed = None
        self._area = 0
        self._steady = 0
        self.frames = 0
        self._structure = None
        self._fraction = 0.0

    @property
    def active(self) -> bool:
        return self._peak is not None

    def update(self, sharpness: np.ndarray) -> float:
        """Fold one sharpness field in. Returns coverage in 0..1."""
        s = np.asarray(sharpness, np.float32)
        if self._peak is None or self._peak.shape != s.shape:
            self._peak = s.copy()
            self._passed = np.zeros(s.shape, bool)
            self._structure = None
            self._fraction = 0.0
            self.frames = 1
            return 0.0

        np.maximum(self._peak, s, out=self._peak)
        # Risen, then fallen: we have gone past this pixel's focal plane.
        self._passed |= s < self._peak * self.FALL
        self.frames += 1

        # Structure, computed once here and cached for every reader.
        top = float(self._peak.max())
        has = self._peak > top * self.FLOOR if top > 0 else None
        self._structure = has

        # Watch the denominator. Structure appearing means the frame still has
        # more to show, whatever the percentage currently says.
        if has is None:
            area = 0
            self._fraction = 0.0
        else:
            area = int(np.count_nonzero(has))
            # count_nonzero on a temporary beats mask-indexing into a
            # compacted copy: same answer, no allocation of the subset.
            self._fraction = (float(np.count_nonzero(self._passed & has)) / area
                              if area else 0.0)
        # A small tolerance, so noise at the floor does not reset the counter.
        if area > self._area * 1.02 + 8:
            self._steady = 0
        else:
            self._steady += 1
        self._area = max(self._area, area)
        return self._fraction

    # ---- readings --------------------------------------------------------

    @property
    def structure(self) -> np.ndarray | None:
        """Where there is anything worth focusing on.

        Recomputed from the current peak rather than accumulated, so it
        self-corrects: a pixel that looked like structure early, before
        anything sharp had been seen, drops out once the scale is established.
        """
        if self._structure is not None:
            return self._structure
        if self._peak is None:
            return None
        top = float(self._peak.max())
        if top <= 0:
            return None
        self._structure = self._peak > top * self.FLOOR
        return self._structure

    @property
    def fraction(self) -> float:
        return self._fraction

    @property
    def settled(self) -> bool:
        """Has the structured area stopped growing?

        Without this, coverage reads complete the moment everything it has
        *noticed* has been passed -- which early in a sweep may be a fraction
        of the frame.
        """
        return self._steady >= self.SETTLE_FRAMES

    @property
    def complete(self) -> bool:
        return self.fraction >= 0.999 and self.settled

    @property
    def remaining_mask(self) -> np.ndarray | None:
        """Pixels that have something in them and have not been through focus.

        This is the useful overlay: it says *where* to keep racking, rather
        than only that you are not done.
        """
        has = self.structure
        if has is None:
            return None
        return has & ~self._passed

    def overlay(self, size: tuple[int, int]) -> np.ndarray | None:
        """Remaining regions as an 8-bit mask at the requested (h, w)."""
        mask = self.remaining_mask
        if mask is None or not mask.any():
            return None
        out = (mask.astype(np.uint8) * 255)
        if out.shape != size:
            out = cv2.resize(out, (size[1], size[0]),
                             interpolation=cv2.INTER_NEAREST)
        # Soften, so the overlay reads as a region rather than as speckle.
        return cv2.morphologyEx(
            out, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
