"""Focus metrics, and the prefilters that decide whether they work.

The obvious choice is wrong for this subject. Santos et al. 1997 evaluated 13
functions on FISH fluorescence -- sparse bright objects on black, the closest
published analogue to darkfield diatoms -- and Vollath F4 won, with Tenengrad
noted explicitly as performing badly when information content is low. A diatom
in a black field is the low-information case by construction. Mateos-Perez 2012
reproduced the result on fluorescent TB bacteria, different group, 15 years on.

Vollath F4 survives sparsity because it is a difference of two autocorrelation
lags: zero-mean noise cancels between the terms instead of being squared and
accumulated, and because it is multiplicative in intensity, empty background
contributes almost nothing to either term. It is also parameter-free, which
matters for a tool that cannot be tuned per slide.

See DISCOVERY.md 9.
"""
from __future__ import annotations

from enum import Enum

import cv2
import numpy as np


class Metric(str, Enum):
    VOLLATH4 = "vollath4"
    NORM_VARIANCE = "norm_variance"
    LAPV = "lapv"
    TENENGRAD = "tenengrad"


class Prefilter(str, Enum):
    NONE = "none"
    MEDIAN = "median"
    TOPHAT = "tophat"


class Region(str, Enum):
    """Where the focus metric looks.

    Not cosmetic. Field curvature puts the frame edges on a different focal
    plane from the centre, so a full-field score averages together things that
    cannot be sharp at the same time -- it flattens the peak you are trying to
    find. A tight box around one detail gives a far more decisive curve.
    """

    SPOT = "spot"          # central 20% -- one detail, junk elsewhere
    CENTRE = "centre"      # central 50% -- the default
    FULL = "full"          # everything, curvature and all
    CUSTOM = "custom"      # a box the operator drew


#: Fraction of each axis, for the fixed regions.
REGION_FRACTION = {Region.SPOT: 0.20, Region.CENTRE: 0.50, Region.FULL: 1.0}


def region_rect(shape: tuple[int, int], region: Region,
                custom: tuple[float, float, float, float] | None = None
                ) -> tuple[int, int, int, int]:
    """Pixel rect for a region. Custom is normalised (x, y, w, h) in 0..1."""
    h, w = shape[:2]
    if region is Region.CUSTOM and custom is not None:
        x, y, cw, ch = custom
        return (int(x * w), int(y * h),
                max(8, int(cw * w)), max(8, int(ch * h)))
    f = REGION_FRACTION.get(region, 0.5)
    rw, rh = int(w * f), int(h * f)
    return ((w - rw) // 2, (h - rh) // 2, rw, rh)


class Illumination(str, Enum):
    BRIGHTFIELD = "brightfield"
    DARKFIELD = "darkfield"
    PHASE = "phase"


#: Mateos-Perez 2012 measured median filtering *degrading* focus precision on
#: sparse bright objects while white top-hat *improved* it. pyuscope's
#: medianBlur(9) was tuned for brightfield IC dies, which is the opposite case.
DEFAULTS: dict[Illumination, tuple[Metric, Prefilter]] = {
    Illumination.BRIGHTFIELD: (Metric.NORM_VARIANCE, Prefilter.MEDIAN),
    Illumination.DARKFIELD: (Metric.VOLLATH4, Prefilter.TOPHAT),
    Illumination.PHASE: (Metric.VOLLATH4, Prefilter.NONE),
}


def apply_prefilter(g: np.ndarray, which: Prefilter) -> np.ndarray:
    if which is Prefilter.MEDIAN:
        return cv2.medianBlur(g, 5)
    if which is Prefilter.TOPHAT:
        # Suppresses the dark background and isolates small bright structures,
        # which is exactly the darkfield case.
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        return cv2.morphologyEx(g, cv2.MORPH_TOPHAT, k)
    return g


def vollath4(g: np.ndarray) -> float:
    """F4 = sum g(i,j)*g(i+1,j) - sum g(i,j)*g(i+2,j)

    The subtraction is the whole trick: it is a difference of autocorrelation
    lags, so uncorrelated noise cancels rather than accumulating.

    Divided by the squared mean, which is not decoration. F4 is multiplicative
    in intensity and so scales as brightness squared -- raise the gain and the
    raw score quadruples with no change in focus whatsoever. Since exposure and
    gain get adjusted constantly during a session, an un-normalised score makes
    the peak memory meaningless the moment a slider moves.
    """
    f = g.astype(np.float32)
    a = float(np.sum(f[:, :-1] * f[:, 1:]))
    b = float(np.sum(f[:, :-2] * f[:, 2:]))
    mean = float(f.mean())
    return (a - b) / (f.size * max(mean * mean, 1e-6))


def norm_variance(g: np.ndarray) -> float:
    """Squared coefficient of variation, sigma^2 / mu^2.

    A deliberate deviation from Sun et al. 2004's F-11, which is sigma^2 / mu.
    As published it compensates brightness differences *between z-slices* of a
    fixed exposure, but it remains linear in overall brightness -- so raising
    the gain raises the score with no change in focus. Dividing by mu squared
    makes it dimensionless and stable across exposure changes, which is what a
    live trace with peak memory actually needs.
    """
    # One pass over the plane, and no temporaries. The numpy form of
    # this -- astype, subtract the mean, square, average -- materialises
    # three float arrays the size of the frame to answer a question
    # OpenCV answers with two accumulators: 3.49 ms against 0.071 ms at
    # the 2736 preview, which is the largest single saving in the live
    # loop and this is the default metric on brightfield, so it is every
    # frame. It is also the *more* accurate of the two, accumulating in
    # double where the numpy chain worked in float32.
    mean, stddev = cv2.meanStdDev(g)
    m = float(mean[0, 0])
    sigma = float(stddev[0, 0])
    return sigma * sigma / max(m * m, 1e-6)


def lapv(g: np.ndarray) -> float:
    """Variance of the Laplacian -- Pech-Pacheco et al. 2000, whose subject was,
    fittingly, autofocusing diatoms in brightfield.

    Also normalised by the squared mean, for the same reason as vollath4: the
    Laplacian is linear in intensity, so its variance scales as brightness
    squared.
    """
    f = g.astype(np.float32)
    mean = float(f.mean())
    return float(cv2.Laplacian(f, cv2.CV_32F).var() / max(mean * mean, 1e-6))


def tenengrad(g: np.ndarray) -> float:
    """Normalised by the squared mean, as above -- gradients are linear in
    intensity, so their squares are not comparable across exposures."""
    f = g.astype(np.float32)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(f, cv2.CV_32F, 0, 1)
    mean = float(f.mean())
    return float((gx * gx + gy * gy).mean() / max(mean * mean, 1e-6))


_FNS = {
    Metric.VOLLATH4: vollath4,
    Metric.NORM_VARIANCE: norm_variance,
    Metric.LAPV: lapv,
    Metric.TENENGRAD: tenengrad,
}


def measure(gray: np.ndarray, metric: Metric, prefilter: Prefilter,
            roi: tuple[int, int, int, int] | None = None) -> float:
    """Score one frame.

    ROI first, then everything downstream is cheap -- ImSwitch's ordering, and
    the right one: on 20 MP with a sparse subject, whole-frame contrast is
    mostly background.
    """
    g = gray
    if roi is not None:
        x, y, w, h = roi
        g = g[y:y + h, x:x + w]
    return _FNS[metric](apply_prefilter(g, prefilter))


class FocusTrace:
    """Rolling history, normalised against the best of the recent past.

    The peak is the maximum *within the window*, not an all-time high. An
    all-time peak never ages, so a single sharp moment -- or a noise spike --
    early in a session pins everything afterwards to a small fraction of it,
    and the trace becomes unreadable until something resets it. Windowing it
    means the displayed range always spans what you can actually see, and a
    peak you passed a while ago eventually stops dominating.

    At roughly 35 fps a 512-sample window is about fifteen seconds, which is a
    reasonable memory for "did I just go past focus?".
    """

    def __init__(self, length: int = 512) -> None:
        self._buf: list[float] = []
        self._length = length
        self.peak = 0.0

    def push(self, value: float) -> None:
        self._buf.append(value)
        if len(self._buf) > self._length:
            del self._buf[0]
        self.peak = max(self._buf) if self._buf else 0.0

    def reset_peak(self) -> None:
        """Drop the history entirely. Used when the score stops being
        comparable at all -- a new metric, region, or illumination."""
        self._buf = self._buf[-1:]
        self.peak = max(self._buf, default=0.0)

    @property
    def values(self) -> np.ndarray:
        return np.asarray(self._buf, np.float32)

    @property
    def normalised(self) -> np.ndarray:
        v = self.values
        return v / self.peak if self.peak > 0 else v

    @property
    def fraction_of_peak(self) -> float:
        if not self._buf or self.peak <= 0:
            return 0.0
        return self._buf[-1] / self.peak
