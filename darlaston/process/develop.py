"""Raw to a photograph somebody can actually open.

Until now every output of this program was a negative. A capture, a merged
stack and a stitched mosaic all came out as DNG, and the only images it
wrote that a person could double-click were the depth maps and the
wigglegram. That is a strange place for a tool whose stated goal is a
photograph rather than a measurement: you could shoot a beautiful stacked
mosaic and not be able to send it to anyone.

This is not a raw developer and is not trying to be one. It is the
equivalent of the JPEG a camera writes beside its raw file: one honest
rendering, made from what the instrument already knows -- the black and
white levels it recorded, and the white balance it measured -- so that
opening the DNG properly later agrees with it rather than contradicting
it.

Distinct from `tiff.make_preview`, which is deliberately crude: that one
skips demosaicing entirely and exists to be recognisable in a file
manager. This one is the picture.
"""
from __future__ import annotations

import numpy as np

import cv2

#: Same mapping as the stitcher's. OpenCV names its constants for the
#: *second* row's phase, so every one of these looks off by one against the
#: sensor's own description and is not.
DEMOSAIC = {"GBRG": cv2.COLOR_BayerGR2BGR, "GRBG": cv2.COLOR_BayerGB2BGR,
            "RGGB": cv2.COLOR_BayerBG2BGR, "BGGR": cv2.COLOR_BayerRG2BGR}


def develop(image: np.ndarray, *, pattern: str | None = "GBRG",
            black: int = 0, white: int = 4095,
            neutral: tuple[float, float, float] | None = None,
            stretch: bool = True, rgb_input: bool = False) -> np.ndarray:
    """One rendering of a raw frame, as BGR uint8.

    `neutral` is the DNG's AsShotNeutral: the *reciprocal* of the gains
    that neutralise the sensor, which is how the tag is defined and how
    the capture path already has it. Passing the measured one matters --
    deriving white balance from the image instead assumes the field is
    neutral on average, and a diatom on a coloured mount is not.

    `stretch` sets both ends of the range from the image's own
    percentiles. Without it a brightfield frame renders pale, because it
    is mostly bright background and scaling from zero crowds everything
    into the top of the scale. With it, a frame that was genuinely
    underexposed comes out looking correct, which is a lie of a different
    kind -- so it is a flag, and the capture path can decide.
    """
    a = np.asarray(image)
    if a.ndim == 2 and pattern:
        a = cv2.cvtColor(a, DEMOSAIC.get(pattern, cv2.COLOR_BayerGR2BGR))
    elif a.ndim == 2:
        a = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    elif rgb_input:
        # A merged stack and a stitched composite are held as linear RGB,
        # because that is what they are written to the DNG as. Demosaiced
        # frames arrive BGR. Getting this wrong tints the whole photograph
        # consistently enough to read as a camera fault rather than a bug,
        # which is exactly how it presented: a neutral tile and a blue
        # mosaic built out of those same tiles.
        a = a[:, :, ::-1]

    # Working in BGR from here, and AsShotNeutral is RGB, hence the reversal.
    gains = [1.0, 1.0, 1.0]
    if neutral:
        gains = [1.0 / n if n > 1e-6 else 1.0 for n in reversed(neutral)]

    span = max(float(white) - float(black), 1.0)
    lo, hi = 0.0, 1.0
    if stretch:
        # Percentiles rather than min and max, so one hot pixel cannot set
        # the white point for a whole photograph. Taken from a subsample:
        # this is an estimate of a distribution over tens of millions of
        # pixels, and every sixteenth row and column says the same thing
        # about it for a 256th of the work and the memory.
        s = a[::16, ::16].astype(np.float32)
        s = (s - float(black)) / span
        for c in range(3):
            s[:, :, c] *= gains[c]
        lo = float(np.percentile(s, 0.5))
        hi = float(np.percentile(s, 99.7))

    # Applied band by band. A stitched mosaic is tens of megapixels, and
    # promoting the whole canvas to float at once wants gigabytes for a
    # step that only ever looks at one pixel at a time.
    out = np.empty(a.shape, np.uint8)
    reach = max(hi - lo, 1e-6)
    rows = max(1, (8 << 20) // max(a.shape[1] * 3 * 4, 1))
    for y in range(0, a.shape[0], rows):
        band = a[y:y + rows].astype(np.float32)
        band -= float(black)
        band /= span
        for c in range(3):
            if gains[c] != 1.0:
                band[:, :, c] *= gains[c]
        if stretch:
            band -= lo
            band /= reach
        np.clip(band, 0.0, 1.0, out=band)
        np.power(band, 1 / 2.2, out=band)
        band *= 255.0
        out[y:y + rows] = (band + 0.5).astype(np.uint8)
    return out


def write_jpeg(path, image: np.ndarray, quality: int = 95) -> bool:
    """Write a developed BGR image. Returns whether it landed.

    Never raises. This runs after the raw file is already safely on disk,
    and losing the photograph is a disappointment where losing the capture
    would be a disaster -- so a failure here must not take the DNG with
    it, or leave the caller believing the capture failed.
    """
    try:
        return bool(cv2.imwrite(str(path), image,
                                [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]))
    except Exception:
        return False
