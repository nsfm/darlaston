"""A rendering choice applied to what is shown and what is published.

Inverted brightfield, a red-blue swap, greyscale: each is a way of
*looking* at the slide, not a fact about it, so none of them may reach
the data. The raw stays what the sensor recorded; the histogram, the
focus metrics and the slide map keep reading sensor levels, for the same
reason the white balance machinery already does -- an instrument that
reports through the operator's chosen look is an instrument that lies.
What does carry the look is everything that is a picture of the slide
rather than a measurement of it: the live view, the presentation window,
the stream, the sidecar JPEG and the DNG's embedded preview, with a
`rendering=` key in the structured comment saying so.

The invert runs on tone-mapped 8-bit values, deliberately. Negating
linear sensor data produces something with no radiometric meaning and no
place to be stored; negating the developed picture is what "inverted"
has meant since the darkroom.

All three were measured at the full 1824x1216 preview: 0.9 ms for the
invert, 0.5 ms for the swap, 1.0 ms for greyscale. Paid per displayed
frame only while a mode is active, and never by the analysis loop.

Nothing here imports Qt: the capture thread uses it too.
"""
from __future__ import annotations

import cv2
import numpy as np

#: The identifiers, as the settings file and the `rendering=` comment key
#: carry them. "none" first because it is the default and the only mode
#: that costs nothing.
MODES = ("none", "invert", "swap", "grey")


def sane(mode) -> str:
    """A mode this module actually implements, or "none".

    A hand-edited settings file, or one written by a newer version with
    modes this build has never heard of, must degrade to the untouched
    picture rather than to an exception in the frame loop.
    """
    return mode if mode in MODES else "none"


def applied(image: np.ndarray, mode: str, order: str = "bgr") -> np.ndarray:
    """The picture wearing the mode. The input is never written to.

    Returns the input array itself when there is nothing to do, so the
    common case stays free; every active mode returns a fresh array.

    `order` names the channel order of `image`. The live path carries
    BGR; the DNG's embedded preview is built RGB. The invert and the
    swap cannot tell the difference, but greyscale weights the channels
    by what they are, and green's weight is nearly six times blue's.
    """
    mode = sane(mode)
    if mode == "none":
        return image
    if mode == "invert":
        return cv2.bitwise_not(image)
    if image.ndim != 3:
        # A mono frame: there are no channels to swap or to weigh.
        return image
    if mode == "swap":
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY if order == "bgr"
                        else cv2.COLOR_RGB2GRAY)
    # Back to three channels: every consumer downstream takes a colour
    # frame, and a greyscale picture is a colour picture that happens to
    # be grey.
    return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
