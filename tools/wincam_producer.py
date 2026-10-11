"""A virtual camera for the Windows CI runner, fed with a known picture.

Runs for ever, painting four coloured quadrants with a stripe that
changes every frame, through `pyvirtualcam` into the Unity Capture
DirectShow filter -- which the DirectShow backend then enumerates,
opens and reads like any other camera. The first argument is a file to
write once the camera exists, so the test run can wait for it.

Not part of the application. Lives in tools/ with the other things that
exist to measure the program rather than to be it.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pyvirtualcam
from pyvirtualcam import PixelFormat

#: 640x480 is in Unity Capture's own capability list, so a consumer that
#: asks for it gets the picture unscaled.
W, H, FPS = 640, 480, 30

#: BGR, one colour per quadrant. The tests check these by mean.
QUADRANTS = {
    "top_left": (60, 20, 220),
    "top_right": (140, 230, 240),
    "bottom_left": (50, 205, 50),
    "bottom_right": (238, 130, 238),
}


def picture() -> np.ndarray:
    base = np.empty((H, W, 3), np.uint8)
    base[:H // 2, :W // 2] = QUADRANTS["top_left"]
    base[:H // 2, W // 2:] = QUADRANTS["top_right"]
    base[H // 2:, :W // 2] = QUADRANTS["bottom_left"]
    base[H // 2:, W // 2:] = QUADRANTS["bottom_right"]
    return base


def main() -> int:
    ready = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    base = picture()
    with pyvirtualcam.Camera(W, H, FPS, fmt=PixelFormat.BGR,
                             backend="unitycapture",
                             device="Unity Video Capture") as cam:
        if ready is not None:
            ready.write_text(cam.device)
        n = 0
        while True:
            frame = base.copy()
            frame[:8, :] = n % 256          # a live stripe: frames are moving
            cam.send(frame)
            cam.sleep_until_next_frame()
            n += 1


if __name__ == "__main__":
    sys.exit(main())
