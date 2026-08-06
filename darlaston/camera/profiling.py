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

#: How much the picture has to move before a sweep is telling us
#: anything at all. Deliberately small: this is a noise floor, not a
#: quality bar. It was 40, which refused a sweep whose brightness
#: visibly changed on screen -- a dim field can climb from 3 to 35 and
#: be perfectly measurable.
MIN_SWING = 8.0

#: How well the level has to follow the control. This is the real
#: question, and the one an absolute swing cannot answer: an unsettled
#: sweep moves plenty and in no order, while a dim but honest one moves
#: little and in perfect order.
MIN_AGREEMENT = 0.80

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

    @property
    def trustworthy(self) -> bool:
        """Did this sweep actually see a camera respond?

        Three ways it does not, all of which produced confident-looking
        tables before this existed:

        * the field is dark, so every reading is the noise floor;
        * the field is clipped, so every reading is the ceiling;
        * the readings move but not with the control, which is what a
          sweep looks like when the exposure has not settled between
          steps -- measured once as 151, 26, 24, 74 across four
          consecutive settings.
        """
        if len(self.points) < 6:
            return False
        levels = [l for _v, l in self.points]
        if max(levels) - min(levels) < MIN_SWING:
            return False
        return self.agreement >= MIN_AGREEMENT

    @property
    def agreement(self) -> float:
        """How closely the level follows the control, from -1 to 1.

        Rank correlation rather than a straight one, because the response
        is not expected to be a straight line -- only to go the right
        way. A camera with a kink in it still passes; a sweep that was
        not settling does not.
        """
        if len(self.points) < 3:
            return 0.0
        order = sorted(range(len(self.points)),
                       key=lambda i: self.points[i][1])
        ranks = [0] * len(order)
        for rank, index in enumerate(order):
            ranks[index] = rank
        n = len(ranks)
        mean = (n - 1) / 2.0
        top = sum((i - mean) * (r - mean) for i, r in enumerate(ranks))
        bottom = sum((i - mean) ** 2 for i in range(n))
        return top / bottom if bottom else 0.0

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


# ---- driving a real camera -------------------------------------------------

def sweep_geometry(backend, progress=None) -> list[ModeGeometry]:
    """Photograph the same field in every mode and work out the shape.

    The largest mode is the reference, so every other is described
    relative to what a capture actually records.
    """
    info = backend.info
    modes = list(info.resolutions) if info else []
    if not modes:
        return []
    # Same for the geometry pass, which walks every resolution mode.
    before_mode = getattr(backend, "_current", None)
    reference = backend.snapshot(0)
    frames = {}
    for res in modes[1:]:
        if progress:
            progress(f"{res.width}x{res.height}")
        frames[(res.width, res.height)] = backend.snapshot(res.index)
    got = measure_geometry(reference, frames)

    # The reference belongs in the list. It is measured against itself,
    # so its answer is trivially "no scale, no offset" -- but leaving it
    # out means anything reading the geometry cannot find the mode a
    # capture actually uses, and has to assume what it should be told.
    first = modes[0]
    got.insert(0, ModeGeometry(width=first.width, height=first.height,
                               scale=1.0, left=0, top=0, confidence=1.0))

    # And a warning worth having: if any mode covers *more* field than
    # the reference, the reference is itself a crop of the sensor and
    # everything measured against it understates the true field. Not seen
    # on any camera yet, and not detectable any other way from inside.
    widest = max((g.width * g.scale for g in got if g.trusted), default=0)
    if widest > first.width * 1.02:
        got[0] = ModeGeometry(width=first.width, height=first.height,
                              scale=1.0, confidence=0.0)
    if before_mode is not None:
        try:
            backend.set_resolution(before_mode)
        except Exception:
            pass
    return got


def plan_sweep(backend, span, points: int = 48) -> list:
    """Find the exposures worth measuring on *this* field.

    Sweeping a fixed range regardless of the illumination is what
    produced a table whose first fourteen entries were all the noise
    floor. So probe coarsely first, find where the picture actually
    moves between dark and clipped, and spend the samples there.

    Returns an empty list when the field never responds, which is the
    honest answer for a lamp that is off.
    """
    low, high = max(1, span[0] // 100), max(2, span[1] // 100)
    high = min(high, 2000)

    # A logarithmic probe, so a field needing 3 and a field needing 900
    # both get found in a handful of reads.
    probe, value = [], low
    while value <= high and len(probe) < 14:
        backend.set_exposure(int(value) * 100)
        probe.append((int(value), float(backend.level())))
        value = max(value + 1, value * 2)

    levels = [l for _v, l in probe]
    if max(levels) - min(levels) < MIN_SWING:
        return []                       # nothing responded; do not pretend

    # The useful span runs from the last setting still near the floor to
    # the first one that has clearly stopped climbing.
    floor, ceiling = min(levels), max(levels)
    start = probe[0][0]
    stop = probe[-1][0]
    for value, level in probe:
        if level <= floor + (ceiling - floor) * 0.05:
            start = value
        if level >= floor + (ceiling - floor) * 0.95:
            stop = value
            break
    if stop <= start:
        start, stop = probe[0][0], probe[-1][0]

    step = max(1, (stop - start) // points)
    return list(range(start, stop + 1, step))


def sweep_response(backend, values=None, progress=None) -> Response:
    """Step the exposure and record what the picture did.

    Values are the device's own control units, not microseconds: the
    point is to learn the mapping, so assuming one would defeat it.
    """
    info = backend.info
    span = info.exposure_range_us if info else None
    if span is None:
        return Response()
    if values is None:
        values = plan_sweep(backend, span)
        if not values:
            return Response()

    def set_value(v):
        if progress:
            progress(str(v))
        backend.set_exposure(int(v) * 100)

    # Put it back afterwards. A measurement that leaves the camera at
    # whatever exposure it happened to finish on has changed the thing it
    # was measuring, and the interface goes on showing the old number --
    # so the slider and the picture disagree and neither is wrong.
    before = getattr(backend, "_exposure_us", None)
    try:
        return measure_response(backend.level, set_value, values)
    finally:
        if before is not None:
            backend.set_exposure(before)
