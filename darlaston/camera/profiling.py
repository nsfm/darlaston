"""Measuring what a camera actually does, once, so the rest can trust it.

Two things are worth measuring per camera, and they want opposite
fields, which is why this is a sequence rather than a single sweep.

**Geometry.** A camera's resolution modes are not simply more or fewer
pixels of the same view. Measured on one here: 1280x720 is a centre crop
of the 1920x1080 field at 1:1, while 640x480 is a *2x downscale* and
then a crop -- so it covers a wider field than 800x600 does, which
nothing about the pixel counts would tell you. Everything that converts
pixels to micrometres depends on knowing which. Needs a field with
detail in it: a slide in focus, or the field diaphragm closed down to
put a sharp edge in view.

**Response.** How a control value maps to brightness. Not the same as
how it maps to *time*: the frame-rate knee on this camera showed the
exposure control is honest 100 microsecond units, while the brightness
response has hard discontinuities -- at 32 and 48 it drops and jumps,
reproducibly, in both sweep directions. So the camera is switching
something else at those points, and a slider that promises brightness
has to go through a measured table rather than a formula. Needs a blank
field: defocus the slide, so the reading is photometry and not whatever
happens to be in frame.

Nothing here opens a camera or touches the interface. It is handed
functions that grab and set, so it can be tested without hardware and
run against anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModeGeometry:
    """What one resolution mode really shows, relative to the largest."""

    width: int
    height: int
    #: How much the sensor image was shrunk before cropping. 1.0 is a
    #: pure crop; 2.0 means the field was halved in each axis first.
    scale: float = 1.0
    #: Where the crop sits in the full field, in full-field pixels.
    left: int = 0
    top: int = 0
    #: How confidently the match was made. Below `TRUSTED` the numbers
    #: are not usable and should be treated as unmeasured rather than
    #: quietly believed -- a blank field will happily return a confident
    #: answer to a question it cannot see.
    confidence: float = 0.0

    @property
    def trusted(self) -> bool:
        return self.confidence >= TRUSTED

    @property
    def field_fraction(self) -> float:
        """How much of the full field this mode covers, by area."""
        return (self.width * self.scale) * (self.height * self.scale)


#: Below this a template match is not telling us anything. Chosen from
#: measurement: real modes scored 0.99, 0.98 and 0.80, while a mode that
#: matched nothing scored 0.44.
TRUSTED = 0.60

#: How much variation a frame needs before matching it means anything.
#: A normalised correlation against a field with no variance is
#: degenerate and comes back as a confident 1.0 -- so the confidence
#: score cannot catch a blank field, and checking for texture first is
#: the only thing that can. Sensor noise measured about 1.4 levels here,
#: so this sits well above noise and well below any real structure.
MIN_TEXTURE = 5.0

#: Scales worth trying. Cameras downsample by simple ratios, so this is
#: a short list rather than a search -- and anything not on it comes back
#: as untrusted rather than as a confident wrong answer.
SCALES = (1.0, 1.25, 1.5, 5 / 3, 1.875, 2.0, 2.4, 3.0, 4.0)


def measure_geometry(reference, frames: dict) -> list[ModeGeometry]:
    """Where each mode sits inside the largest one.

    `reference` is the full-resolution frame; `frames` maps (w, h) to a
    frame from that mode. Both greyscale.

    Every mode is tried at every plausible scale and the best match
    wins. Trying only 1:1 would call a downscaled mode "no match" -- and
    that is exactly what happened before this looked for scales at all.
    """
    import cv2
    import numpy as np

    out = []
    flat = float(np.std(reference)) < MIN_TEXTURE
    for (w, h), frame in sorted(frames.items(), reverse=True):
        best = ModeGeometry(width=w, height=h)
        if flat or float(np.std(frame)) < MIN_TEXTURE:
            # Nothing to match on. Reported as unmeasured rather than
            # matched, because the score here would be a confident 1.0
            # and believing it would put a wrong micrometres-per-pixel
            # into every file taken afterwards.
            out.append(best)
            continue
        for scale in SCALES:
            shrunk = (reference if scale == 1.0 else
                      cv2.resize(reference, None, fx=1 / scale, fy=1 / scale,
                                 interpolation=cv2.INTER_AREA))
            if shrunk.shape[0] < h or shrunk.shape[1] < w:
                continue
            found = cv2.matchTemplate(shrunk, frame, cv2.TM_CCOEFF_NORMED)
            _lo, score, _at, where = cv2.minMaxLoc(found)
            if score > best.confidence:
                best = ModeGeometry(
                    width=w, height=h, scale=scale,
                    left=int(where[0] * scale), top=int(where[1] * scale),
                    confidence=float(score))
        out.append(best)
    return out


@dataclass(frozen=True)
class Response:
    """How a control value maps to brightness, measured rather than assumed."""

    #: (control value, mean level) in the order they were measured.
    points: tuple = ()

    @property
    def usable(self) -> tuple:
        """The longest run that only ever goes up.

        A slider has to be monotonic or it is not a slider. This camera's
        brightness drops at 32 and jumps at 48, so the honest thing is to
        find the longest stretch that behaves and drive from that, rather
        than expose a control that goes backwards in the middle.
        """
        best = run = []
        for point in self.points:
            if run and point[1] <= run[-1][1]:
                best, run = (best if len(best) >= len(run) else run), [point]
            else:
                run.append(point)
        return tuple(best if len(best) >= len(run) else run)

    def value_for(self, level: float) -> int | None:
        """The lowest control value that reaches this brightness.

        The lowest, deliberately: where a camera reaches the same level
        at more than one setting, the shorter exposure is the one that
        moves less and reads out sooner.
        """
        for value, got in self.points:
            if got >= level:
                return int(value)
        return int(self.points[-1][0]) if self.points else None


def measure_response(read_level, set_value, values) -> Response:
    """Sweep a control and record what the picture did.

    `read_level` returns the mean level of a settled frame; `set_value`
    sets the control and is expected to have flushed whatever frames
    still carry the old setting -- two, on the camera this was written
    against.

    Wants a blank field. On anything with structure the mean moves when
    the specimen does, and a stage that drifts a little during a sweep
    looks exactly like a camera that is not linear.
    """
    points = []
    for value in values:
        set_value(value)
        points.append((value, float(read_level())))
    return Response(points=tuple(points))
