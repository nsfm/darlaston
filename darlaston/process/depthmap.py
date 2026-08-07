"""Reading and writing the depth map, at a width that does not throw it away.

`depth.png` is the data half of a merged stack: which slice won at each
pixel, and therefore the geometry a wigglegram, an anaglyph, a DIC render
or a stitched height field is synthesised from.

It was written 8-bit. That was right when the depth was an integer slice
index and there were fewer than 256 of them, so every distinct value the
merge could produce had its own level. The merge interpolates now -- the
winning slice is refined against its neighbours, so depth is continuous --
and 8 bits quantises a continuous surface into 256 terraces. On a 30-slice
stack that is 8.5 output levels per slice: enough to see the slices as
bands in a smooth gradient, which is exactly the terracing the merge work
went to some trouble to remove.

16-bit costs nothing anybody will notice. A 20 MP map is 40 MB before PNG
compression and depth maps compress extremely well, being mostly smooth.

The reading half matters as much as the writing half, and is the reason
this is a module rather than two lines at the call site. `cv2.imread` with
`IMREAD_GRAYSCALE` silently downconverts a 16-bit file to 8, so a wider
file read through the old call would have been quietly narrowed again on
the way back in. And every stack merged before this change has an 8-bit
map on disk that must keep working: `read` normalises by what it actually
found rather than by what it hoped for.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

#: Written as uint16. Not float: PNG has no float form, and the map is a
#: bounded quantity where a fixed point with 65536 steps is finer than the
#: measurement behind it.
FULL = 65535


def write(path: Path | str, depth: np.ndarray, levels: int) -> bool:
    """Write `depth`, a per-pixel slice index in [0, levels-1].

    Normalised to the full range rather than left in slice units, because
    the file is read by tools that know nothing about how many slices this
    stack had. The count goes in the manifest for anyone who needs to get
    back to slice numbers.
    """
    span = max(int(levels) - 1, 1)
    scaled = np.clip(np.asarray(depth, np.float32) / span, 0.0, 1.0)
    return bool(cv2.imwrite(str(path),
                            (scaled * FULL + 0.5).astype(np.uint16)))


def read(path: Path | str) -> np.ndarray | None:
    """The map as float32 in [0, 1], whatever width it was written at.

    Returns None when there is nothing readable there, because every
    caller of this treats a missing depth map as "this tile has no depth"
    rather than as an error.
    """
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        return None
    if raw.ndim == 3:
        # Somebody's editor saved it as RGB. The channels are equal in a
        # greyscale map, so take one rather than refusing.
        raw = raw[..., 0]
    # By what it actually is. A map written before this module existed is
    # uint8, and dividing it by 65535 would put the whole stack in the
    # first four hundredths of the range and read as flat.
    top = float(np.iinfo(raw.dtype).max) if raw.dtype.kind in "ui" else 1.0
    return np.asarray(raw, np.float32) / max(top, 1.0)


def view(depth: np.ndarray, levels: int) -> np.ndarray:
    """The same map dressed for looking at. 8-bit, and that is correct:
    this one is for eyes, and no eye resolves 65536 steps of viridis."""
    span = max(int(levels) - 1, 1)
    scaled = np.clip(np.asarray(depth, np.float32) / span, 0.0, 1.0)
    return cv2.applyColorMap((scaled * 255 + 0.5).astype(np.uint8),
                             cv2.COLORMAP_VIRIDIS)
