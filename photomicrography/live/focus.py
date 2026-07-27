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
    """
    f = g.astype(np.float32)
    a = float(np.sum(f[:, :-1] * f[:, 1:]))
    b = float(np.sum(f[:, :-2] * f[:, 2:]))
    return (a - b) / f.size


def norm_variance(g: np.ndarray) -> float:
    """Sun et al. 2004's F-11. The /mean compensates brightness differences
    between z-slices, which raw variance does not."""
    f = g.astype(np.float32)
    m = float(f.mean())
    return float(((f - m) ** 2).mean() / max(m, 1e-6))


def lapv(g: np.ndarray) -> float:
    """Variance of the Laplacian -- Pech-Pacheco et al. 2000, whose subject was,
    fittingly, autofocusing diatoms in brightfield."""
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def tenengrad(g: np.ndarray) -> float:
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
    return float((gx * gx + gy * gy).mean())


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
    """Rolling history with peak memory.

    Absolute metric values are meaningless across metrics and subjects, so the
    UI needs a normalised trace and a distance-from-peak, not a number.
    """

    def __init__(self, length: int = 256) -> None:
        self._buf: list[float] = []
        self._length = length
        self.peak = 0.0

    def push(self, value: float) -> None:
        self._buf.append(value)
        if len(self._buf) > self._length:
            del self._buf[0]
        self.peak = max(self.peak, value)

    def reset_peak(self) -> None:
        self.peak = max(self._buf[-1:], default=0.0)

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
