"""The working orientation: how the sensor's frame is turned to be looked at.

The companion to `transform` -- that module is what colour the picture
wears, this one is which way up it hangs. Same church: instruments and
the raw data stay in the sensor's own orientation, and everything that
is a picture of the slide turns. An inverted stand hands the operator a
mirrored world; a creator framing a vertical video wants the long axis
upright. Neither changes what the sensor measured.

Unlike colour, geometry touches *interaction*: a rectangle drawn on the
screen has to travel back to the sensor's frame before the pipeline can
use it, and a rectangle the pipeline reports has to travel forward
before it can be drawn. The two rect functions here are that round
trip, and they are exact inverses by construction.

The captured files diverge on purpose. The DNG gets the standard EXIF
Orientation tag -- the raw pixels stay exactly as the sensor read them,
and every raw developer honours the tag. The JPEG gets its pixels
physically turned, because our JPEG carries no EXIF and a photograph
that needs a manual to hang the right way up is not finished.

The display convention everywhere: rotate clockwise first, then mirror
left-right. Mirror-last means "Mirrored" always swaps left and right
*of what the operator sees*, whatever the rotation -- which is the
promise the menu item makes.

Nothing here imports Qt: the capture thread uses it too.
"""
from __future__ import annotations

import cv2
import numpy as np

#: Clockwise display rotations this module implements, in degrees.
ROTATIONS = (0, 90, 180, 270)

_CV_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}

#: EXIF Orientation (tag 274) for each display transform: the code that
#: tells a viewer to apply exactly the rotation-then-mirror this module
#: would. Values 5 and 7 are the transposes; the mapping is pinned by a
#: test against the raw array definitions rather than trusted to memory.
_EXIF = {(0, False): 1, (0, True): 2,
         (90, False): 6, (90, True): 5,
         (180, False): 3, (180, True): 4,
         (270, False): 8, (270, True): 7}


def sane_rotation(rot) -> int:
    """A rotation this module implements, or 0.

    A settings file from the future, or a hand edit, degrades to the
    untouched picture rather than to an exception in the frame loop.
    """
    try:
        rot = int(rot)
    except (TypeError, ValueError):
        return 0
    return rot if rot in ROTATIONS else 0


def frame(image: np.ndarray, rot: int, mirror: bool) -> np.ndarray:
    """The picture hung the chosen way. The input is never written to.

    Returns the input array itself when there is nothing to do, so the
    common case stays free; any active turn returns a fresh array.
    """
    rot = sane_rotation(rot)
    if rot:
        image = cv2.rotate(image, _CV_ROT[rot])
    if mirror:
        image = cv2.flip(image, 1)
    return image


def exif_code(rot: int, mirror: bool) -> int:
    """EXIF Orientation for this display transform, 1 when upright."""
    return _EXIF[(sane_rotation(rot), bool(mirror))]


def rect_to_view(rect, rot: int, mirror: bool):
    """A normalised sensor-space (x, y, w, h) as the display shows it."""
    x, y, w, h = rect
    rot = sane_rotation(rot)
    if rot == 90:
        x, y, w, h = 1.0 - y - h, x, h, w
    elif rot == 180:
        x, y = 1.0 - x - w, 1.0 - y - h
    elif rot == 270:
        x, y, w, h = y, 1.0 - x - w, h, w
    if mirror:
        x = 1.0 - x - w
    return (x, y, w, h)


def rect_to_sensor(rect, rot: int, mirror: bool):
    """A normalised display-space (x, y, w, h) back on the sensor.

    Exact inverse of `rect_to_view`: undo the mirror, then undo the
    rotation.
    """
    x, y, w, h = rect
    rot = sane_rotation(rot)
    if mirror:
        x = 1.0 - x - w
    if rot == 90:
        x, y, w, h = y, 1.0 - x - w, h, w
    elif rot == 180:
        x, y = 1.0 - x - w, 1.0 - y - h
    elif rot == 270:
        x, y, w, h = 1.0 - y - h, x, h, w
    return (x, y, w, h)
