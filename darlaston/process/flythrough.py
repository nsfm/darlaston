"""A film of a mosaic, because nobody can see one all at once.

A stitched arrangement is fifty megapixels of glass. Opened whole it is a
thumbnail, and opened at full size it is a maze -- the two ways of looking
at it are mutually exclusive, and the thing that makes it worth doing at
all, that these are *real* objects a few hundredths of a millimetre
across, arrives in neither view.

So: start close enough that the areolae are individually visible and the
subject is unreadable, move between a few of them, then pull back until
the whole arrangement resolves at once. It is the oldest trick there is
and it works every time, because the reveal does the explaining.

Deliberately not a measurement, and deliberately not configurable into
uselessness. It picks its own subjects by looking for structure, holds
still where there is something to see, and moves in eased curves rather
than linear ramps because a linear zoom reads as a machine and an eased
one reads as a camera.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from .develop import develop
from .wiggle import _read_composite

#: 16:9, because this is made to be sent to somebody, and 720 rather than
#: 1080 for the same reason. OpenCV's writer ignores every bitrate control
#: it offers -- setting quality from 90 down to 30 produced byte-identical
#: files -- so resolution is the only lever there is, and 1080p came out at
#: 34 MB for thirteen seconds, which is past what a chat client will take.
#: 720p halves both the file and the render.
#:
#: It costs no sharpness where sharpness matters. The tightest shot is
#: pinned to one output pixel per sensor pixel either way; a smaller frame
#: just means it shows a smaller piece of glass at the same crispness.
OUT_W, OUT_H = 1280, 720
FPS = 30

#: Never crop tighter than the output: past 1:1 there is no more picture,
#: only interpolation, and a viewer can tell.
MIN_CROP = OUT_W


def _content_box(grey: np.ndarray) -> tuple[int, int, int, int]:
    """The rectangle the mosaic actually occupies.

    A stitched canvas is the bounding box of an irregular scan, so it
    carries black wedges at the corners. Pulling out to the *canvas*
    frames those; pulling out to the content frames the arrangement.
    """
    lit = grey > 8
    cols = np.flatnonzero(lit.any(axis=0))
    rows = np.flatnonzero(lit.any(axis=1))
    if not len(cols) or not len(rows):
        return 0, 0, grey.shape[1], grey.shape[0]
    return (int(cols[0]), int(rows[0]),
            int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1))


def _on_mountant(image: np.ndarray) -> np.ndarray:
    """Put the arrangement on an even ground, and give it room.

    Two problems with the same answer. A stitched canvas is the bounding
    box of an irregular scan, so it has black wedges where no tile
    reached; and the canvas is near square while this renders 16:9, so
    the widest view either crops the arrangement or frames those wedges.

    Both go away by filling the untouched canvas with the slide's own
    background tone and padding out to the aspect with more of it. The
    reveal then lands on an arrangement floating on mountant, which is
    what it looked like down the eyepiece, instead of on a ragged black
    tile pattern that is an artefact of how it was photographed.

    What counts as untouched canvas is decided by where it *is*, not by
    how dark it is. Unscanned canvas is contiguous with the edge of the
    frame; the dark middle of a diatom is not. Filling every near-black
    pixel instead was measurably wrong -- on a synthetic mosaic it ate
    nearly 3% of the specimen texture, and on a darkfield mosaic, where
    most of a real specimen is nearly black, it would eat the subject.
    """
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    empty = (grey <= 8).astype(np.uint8)
    lit = grey > 8
    if not lit.any():
        return image
    ground = np.array([int(np.median(image[:, :, c][lit]))
                       for c in range(3)], np.uint8)

    count, labels = cv2.connectedComponents(empty, connectivity=4)
    if count > 1:
        edge = np.concatenate([labels[0], labels[-1],
                               labels[:, 0], labels[:, -1]])
        outside = np.unique(edge[edge > 0])
        if outside.size:
            image[np.isin(labels, outside)] = ground
    del labels, empty

    h, w = image.shape[:2]
    need_w = max(w, int(round(h * OUT_W / OUT_H)))
    need_h = max(h, int(round(w * OUT_H / OUT_W)))
    # Only one of these can bind; padding to both would grow it twice.
    if need_w > w:
        need_h = h
    pad_x, pad_y = (need_w - w) // 2, (need_h - h) // 2
    if pad_x or pad_y:
        image = cv2.copyMakeBorder(
            image, pad_y, need_h - h - pad_y, pad_x, need_w - w - pad_x,
            cv2.BORDER_CONSTANT, value=[int(v) for v in ground])
    return image


def _subjects(grey: np.ndarray, scale: float, count: int,
              apart: int) -> list[tuple[int, int]]:
    """Where the interesting things are, in full-resolution coordinates.

    Structure rather than brightness: a diatom is a lot of fine edges on a
    plain ground, so a box filter over the edge magnitude finds specimens
    and ignores the empty glass between them. Picked greedily with a
    minimum separation, or every stop lands on the same big frustule.
    """
    energy = cv2.boxFilter(np.abs(cv2.Laplacian(grey, cv2.CV_32F)), -1,
                           (31, 31))
    picks: list[tuple[int, int]] = []
    work = energy.copy()
    gap = max(1, int(apart * scale))
    for _ in range(count):
        _v, best, _p, (x, y) = cv2.minMaxLoc(work)
        if best <= 0:
            break
        picks.append((int(x / scale), int(y / scale)))
        cv2.circle(work, (x, y), gap, 0.0, -1)
    return picks


def _ease(t: float) -> float:
    """Smoothstep. Starts and stops at rest, which is what a hand does."""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def _frame(image: np.ndarray, cx: float, cy: float, cw: float) -> np.ndarray:
    """One view: a 16:9 crop centred where asked, fitted to the output."""
    h, w = image.shape[:2]
    cw = float(min(max(cw, MIN_CROP), w, h * OUT_W / OUT_H))
    ch = cw * OUT_H / OUT_W
    x = int(round(min(max(cx - cw / 2, 0), w - cw)))
    y = int(round(min(max(cy - ch / 2, 0), h - ch)))
    crop = image[y:y + int(round(ch)), x:x + int(round(cw))]
    # INTER_AREA is the right reduction and a poor magnifier; at the
    # tightest stop the crop is already output size and neither applies.
    mode = cv2.INTER_AREA if crop.shape[1] > OUT_W else cv2.INTER_LINEAR
    return cv2.resize(crop, (OUT_W, OUT_H), interpolation=mode)


def _path(stops: list[tuple[int, int]], box: tuple[int, int, int, int],
          hold: float, travel: float, pull: float) -> list[tuple[float, ...]]:
    """Keyframes as (centre x, centre y, crop width, seconds to get there).

    Ends on the whole arrangement rather than starting there: the picture
    is the payoff, and a reveal that opens with the answer is a diagram.
    """
    bx, by, bw, bh = box
    wide = max(bw, bh * OUT_W / OUT_H)
    keys = [(float(stops[0][0]), float(stops[0][1]), float(MIN_CROP), 0.0)]
    for x, y in stops[1:]:
        keys.append((float(x), float(y), float(MIN_CROP), travel))
    keys.append((bx + bw / 2, by + bh / 2, wide, pull))
    return keys, hold


def flythrough(directory: Path | str, *, stops: int = 3, hold: float = 1.0,
               travel: float = 2.2, pull: float = 5.0) -> Path:
    """Render `flythrough.webm` beside a stitched composite.

    Returns the path written. Needs nothing but the composite: no depth
    map, no calibration, no scale -- which is deliberate, because this is
    the output that should work on the first mosaic somebody ever shoots.
    """
    directory = Path(directory)
    source = directory / "composite.dng"
    if not source.exists():
        raise FileNotFoundError(f"no composite.dng in {directory}")

    rgb, white = _read_composite(source)
    # Grey-world, as the composite's own sidecar does. Without a neutral a
    # raw microscope field renders as a green rectangle -- correct, and
    # useless to look at. A mosaic of diatoms on plain mountant is exactly
    # the case the assumption holds for: the field really is neutral on
    # average, because most of it is mountant.
    step = max(1, max(rgb.shape[:2]) // 400)
    thumb = rgb[::step, ::step]
    means = [float(thumb[:, :, c][thumb[:, :, c] > 0].mean() or 1.0)
             for c in range(3)]
    neutral = tuple(m / max(means[1], 1e-6) for m in means)
    image = develop(rgb, pattern=None, white=white, neutral=neutral,
                    rgb_input=True)
    del rgb, thumb
    image = _on_mountant(image)
    h, w = image.shape[:2]

    # Survey small. Finding subjects and the content box are both questions
    # about where things are, not what they look like.
    scale = min(1.0, 1400 / max(w, h))
    small = cv2.cvtColor(cv2.resize(image, None, fx=scale, fy=scale,
                                    interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY)
    bx, by, bw, bh = _content_box(small)
    box = (int(bx / scale), int(by / scale),
           int(bw / scale), int(bh / scale))
    picks = _subjects(small, scale, stops, apart=max(w, h) // 6)
    if not picks:                              # a blank mosaic, somehow
        picks = [(w // 2, h // 2)]

    keys, hold = _path(picks, box, hold, travel, pull)

    def views():
        """Every view in order, one at a time.

        A generator rather than a list: at 1080p a fourteen-second film is
        four hundred frames and two and a half gigabytes if they are all
        held at once, which on a smaller machine is the difference between
        rendering and swapping.
        """
        for i, (cx, cy, cw, seconds) in enumerate(keys):
            if i:
                px, py, pw, _ = keys[i - 1]
                steps = max(1, int(seconds * FPS))
                for k in range(1, steps + 1):
                    t = _ease(k / steps)
                    # Zoom interpolates geometrically. Linearly, the first
                    # half of a long pull-out eats most of the
                    # magnification and the move looks like it is
                    # decelerating into a wall.
                    yield _frame(image,
                                 px + (cx - px) * t,
                                 py + (cy - py) * t,
                                 pw * math.exp(math.log(cw / pw) * t))
            for _k in range(int(hold * FPS)):
                yield _frame(image, cx, cy, cw)

    # VP8 rather than the VP9 the wigglegram uses, measured on real frames
    # from a real mosaic rather than on noise, which flatters nobody
    # honestly: VP9 took 990 ms a frame, VP8 226 ms, and they produced the
    # same file size at 39.0 dB against 38.8 dB. Four times the speed for
    # two tenths of a decibel, on a render that is four hundred frames
    # long. (MPEG-4 is another twenty times faster again and a whole
    # decibel worse, which is the trade this does not need to make.)
    target = directory / "flythrough.webm"
    writer = None
    for fourcc in ("VP80", "VP90"):
        writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*fourcc),
                                 float(FPS), (OUT_W, OUT_H))
        if writer.isOpened():
            break
        writer.release()
        writer = None
    if writer is None:
        raise RuntimeError("no WebM encoder in this OpenCV build")
    last = None
    for frame in views():
        writer.write(frame)
        last = frame
    writer.release()
    # The last view is the whole arrangement, which is also the still
    # anybody will want as a thumbnail for the video.
    if last is not None:
        cv2.imwrite(str(directory / "flythrough.jpg"), last,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return target
