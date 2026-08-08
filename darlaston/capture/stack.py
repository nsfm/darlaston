"""Z-stacks: the knob is the interface.

A stack is captured by racking the fine focus, pausing, and racking again.
Nothing is clicked between slices: the trigger watches the live stream,
notices focus-moved-then-held, and fires the capture itself. The operator's
hands never leave the microscope, which is the whole point -- a stack is a
physical activity happening at the instrument, and the software's job is to
keep up.

Detecting "the focus moved" is less obvious than it sounds. Racking changes
the image without translating it, so the xy tracker sees nothing; and near
the metric's peak the scalar focus number barely moves while fine structure
swaps focal planes underneath it. The *sharpness field* is the honest
witness: it reshapes as the focal plane moves and stops reshaping when the
hand stops. Distances are measured on mean-removed fields (Pearson, not raw
cosine -- the fields share a large common component that drowns the change),
and thresholds calibrate themselves from each pause, because the noise floor
belongs to the hardware, not to this file.

The session mirrors MosaicSession deliberately: a folder of ordinary
captures, adopted in and renamed, with a manifest rewritten on every change
so an interrupted stack is still a stack. One day a mosaic tile will *be*
one of these folders; keeping the shapes identical is what will make that
composition boring to build.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .files import claim, discard


@dataclass
class Slice:
    index: int
    filename: str
    #: The scalar focus metric at capture, recorded because it sequences the
    #: slices through the stack even though no absolute Z exists.
    metric: float
    #: Coverage fraction when this slice was taken -- the progress bar the
    #: operator was watching, kept for the record.
    coverage: float | None = None


class StackSession:
    """One Z-stack: a directory of slices and a manifest that explains it."""

    def __init__(self, root: Path | str, subject: str = "") -> None:
        stamp = time.strftime("%y%m%d-%H%M%S")
        name = f"{stamp}_{subject or 'stack'}_slices"
        self.dir = Path(root) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.subject = subject
        self.slices: list[Slice] = []
        self._meta: dict = {}
        # Empty, but written: see MosaicSession.__init__ for why.
        self._save()

    @classmethod
    def at(cls, directory: Path | str, subject: str = "") -> "StackSession":
        """A stack at an exact directory, for living inside a mosaic.

        The stamped name in __init__ suits a free-standing stack; a stack
        that IS a mosaic tile is named by the mosaic (`tile_007_stack/`),
        because its identity comes from its position in the larger work.
        """
        session = cls.__new__(cls)
        session.dir = Path(directory)
        session.dir.mkdir(parents=True, exist_ok=True)
        session.subject = subject
        session.slices = []
        session._meta = {}
        # The mosaic does not record this tile until `adopt_stack` seals it,
        # but the folder should still describe itself: the stitcher merges
        # any stack it finds undone, and merging goes through `load`.
        session._save()
        return session

    def set_meta(self, **meta) -> None:
        self._meta.update({k: v for k, v in meta.items() if v is not None})
        self._save()

    def adopt(self, capture_path: Path, metric: float,
              coverage: float | None = None) -> Slice:
        index = len(self.slices) + 1
        # `claim`, not a bare move: the derived name is only free while the
        # manifest and the directory agree, and the move used to happen
        # before the save that keeps them in step.
        target = claim(capture_path, self.dir / f"slice_{index:03d}.dng")
        filename = target.name
        piece = Slice(index=index, filename=filename, metric=float(metric),
                      coverage=coverage)
        self.slices.append(piece)
        self._save()
        return piece

    def undo(self) -> Slice | None:
        if not self.slices:
            return None
        piece = self.slices.pop()
        discard(self.dir / piece.filename)
        self._save()
        return piece

    def release_slices(self, merged: Path) -> tuple[int, int]:
        """Delete the slice files a verified merge has consumed.

        Returns (files removed, bytes freed). The setting behind this says
        "keep individual Z-stack slices after stacking", and a 40-tile
        mosaic at 30 slices each is about 47 GB of raw frames, so there is
        a real reason somebody would turn it off.

        Verified means the thing that replaces them exists and is not a
        stub: every slice is checked off against the manifest, the merged
        file has to be there, and it has to be at least as large as a
        single slice -- a merge that wrote a 4 kB header before failing
        must never be grounds for deleting its inputs.

        The manifest stays, and so does the folder. What was shot, in what
        order, at what focus, is the record of the session and it costs
        nothing to keep. Only the pixels go.
        """
        if not self.slices or not merged.exists():
            return (0, 0)
        files = [self.dir / s.filename for s in self.slices]
        present = [f for f in files if f.exists()]
        if len(present) != len(files):
            # Somebody has been in here. Deleting the rest of a set that is
            # already incomplete is the wrong response to a surprise.
            return (0, 0)
        smallest = min(f.stat().st_size for f in present)
        if merged.stat().st_size < smallest:
            return (0, 0)

        freed = removed = 0
        for path in present:
            for target in (path, path.with_suffix(".jpg")):
                if not target.exists():
                    continue
                size = target.stat().st_size
                try:
                    target.unlink()
                except OSError:
                    continue
                removed += 1
                freed += size
        return (removed, freed)

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    def _save(self) -> None:
        payload = {
            "darlaston_stack": 1,
            "subject": self.subject,
            "meta": self._meta,
            "slices": [{"index": s.index, "file": s.filename,
                        "metric": s.metric, "coverage": s.coverage}
                       for s in self.slices],
        }
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.manifest_path)

    @classmethod
    def load(cls, directory: Path | str) -> "StackSession":
        directory = Path(directory)
        data = json.loads((directory / "manifest.json").read_text())
        session = cls.__new__(cls)
        session.dir = directory
        session.subject = data.get("subject", "")
        session._meta = data.get("meta", {})
        session.slices = [Slice(index=s["index"], filename=s["file"],
                                metric=s.get("metric", 0.0),
                                coverage=s.get("coverage"))
                          for s in data.get("slices", [])]
        return session


# --------------------------------------------------------------------------
# The trigger
# --------------------------------------------------------------------------

def field_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson distance between two small sharpness fields.

    Mean-removed on purpose: the raw fields share a large common component
    (everything blurred still has some sharpness everywhere), and cosine
    distance on the raw fields buried a focal-plane move in it. Measured on
    the synthetic stage, mean removal roughly doubles the separation between
    racking and holding still.
    """
    a = a - a.mean()
    b = b - b.mean()
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-12 or nb < 1e-12:
        return 1.0
    return 1.0 - float(np.dot(a.ravel(), b.ravel()) / (na * nb))


class StackTrigger:
    """Watches the live stream and captures a slice at each pause.

    States: WAIT_MOVE (the focus has not left the last slice's plane yet),
    WAIT_SETTLE (it moved, the hand is still racking), then fire and back to
    WAIT_MOVE. The first slice fires on arming, at wherever the focus is.

    Thresholds are self-calibrating. The frame-to-frame distance during a
    confirmed pause is the hardware's own noise floor, and every pause
    updates it; "moved" means several times that floor, "settled" means back
    within a few multiples of it. Absolute constants tuned on the mock would
    be wrong on the first real sensor.
    """

    #: Small square the sharpness field is reduced to for comparison.
    SIZE = 64
    #: "Moved" is this many times the measured noise floor, cumulative since
    #: the last slice.
    MOVE_OVER_FLOOR = 6.0
    #: "Settled" is frame-to-frame distance falling below this fraction of
    #: the motion's own peak. Relative on purpose: an absolute threshold sat
    #: above the noise of *slow* racking and declared a pause mid-motion --
    #: measured, and the reason two tests failed. Quiet only means anything
    #: compared to how loud the moving was.
    SETTLE_BELOW_PEAK = 0.25
    #: ...and also within this many times the measured pause floor. The
    #: relative test alone is not enough: at constant knob speed the field
    #: changes fast mid-blur and gently near focus, so a rack that naturally
    #: quietens read as a pause. Traced on the synthetic stage -- the ratio
    #: fell to 0.44 of peak while the knob was still turning. Quiet has to
    #: mean quiet compared to the motion *and* quiet in absolute terms
    #: against what this hardware's genuine stillness looks like.
    SETTLE_OVER_FLOOR = 3.0
    #: Five fields is ~0.4 s at 24 fps. A deliberate pause is longer than
    #: that; a brief quiet dip mid-rack is not. Near focus, fine racking
    #: passes the distance gates for a field or two at a time -- duration is
    #: the discriminator that stays honest there.
    SETTLE_FRAMES = 5
    #: Never two slices within this many seconds regardless of the signals.
    #: Two was chosen when a capture took 5.4 s anyway, so it never bound.
    #: With the write queued the shortest gap Nate managed was 1.90 s --
    #: this constant, not the disk, and a guard that has become the limit
    #: is no longer a guard.
    MIN_INTERVAL = 1.0
    #: A starting floor before anything is measured. Deliberately generous;
    #: the first pause replaces it.
    DEFAULT_FLOOR = 2e-4

    def __init__(self, fire: Callable[[], bool]) -> None:
        self._fire = fire
        self.armed = False
        self._at_slice: np.ndarray | None = None
        self._prev: np.ndarray | None = None
        self._floor = self.DEFAULT_FLOOR
        self._quiet = 0
        self._moved = False
        self._last_fire = 0.0
        self._pause_ds: list[float] = []
        #: The loudest frame-to-frame distance since the last slice, decayed
        #: gently so an early jolt does not set the bar for a later gentle
        #: rack. "Settled" is measured against this.
        self._motion_peak = 0.0
        #: A capture is in flight. Between firing and the slice landing the
        #: trigger must hold its tongue: the conditions that fired it are
        #: still true (the operator is holding still, which is the point),
        #: and without this it re-fired on every settle re-count.
        self._pending = False

    def arm(self) -> None:
        self.armed = True
        self._at_slice = None
        self._prev = None
        self._moved = False
        self._quiet = 0
        self._last_fire = 0.0
        self._pending = False

    def disarm(self) -> None:
        self.armed = False

    def capture_failed(self) -> None:
        """The in-flight capture died; go back to watching."""
        self._pending = False

    def slice_landed(self, field_small: np.ndarray | None = None) -> None:
        """Called when a capture completed: baseline moves to this slice."""
        if field_small is not None:
            self._at_slice = field_small
        else:
            self._at_slice = self._prev
        self._moved = False
        self._quiet = 0
        self._motion_peak = 0.0
        self._pending = False

    def observe(self, signals) -> bool:
        """One frame of live signals. Returns True when a capture was fired."""
        if not self.armed or self._pending:
            return False
        field = signals.sharpness_field
        if field is None:
            return False                      # the field arrives at half rate
        small = cv2.resize(field, (self.SIZE, self.SIZE),
                           interpolation=cv2.INTER_AREA)

        prev, self._prev = self._prev, small
        if prev is None:
            return False

        d = field_distance(prev, small)

        # The very first slice: fire as soon as the view is steady at all,
        # because wherever the operator starts is the first plane.
        if self._at_slice is None:
            if signals.settled and not signals.looks_blank:
                return self._try_fire()
            return False

        moved_from_slice = field_distance(self._at_slice, small)
        self._motion_peak = max(d, self._motion_peak * 0.98)

        if not self._moved:
            if moved_from_slice > self._floor * self.MOVE_OVER_FLOOR:
                self._moved = True
                self._quiet = 0
            return False

        # Moved; now waiting for the hand to hold still, judged against how
        # vigorous the motion itself was.
        if (d < self._motion_peak * self.SETTLE_BELOW_PEAK
                and d < self._floor * self.SETTLE_OVER_FLOOR):
            self._quiet += 1
            self._pause_ds.append(d)
        else:
            self._quiet = 0
            self._pause_ds.clear()

        if self._quiet >= self.SETTLE_FRAMES:
            # The xy gate: panning also reshapes the sharpness field, so
            # without this a pan-then-pause would capture a slice of a
            # different part of the slide into the stack. `settled` is the
            # tracker's stillness, which racking does not disturb.
            if not signals.settled or signals.looks_blank:
                return False
            # This pause's own quietness becomes the new noise floor --
            # the hardware measuring itself.
            if self._pause_ds:
                # The floor may fall freely but rise only slowly: quiet
                # moments during gentle racking sneak into the sample, and a
                # floor that chases them upward lets the next gentle rack
                # read as stillness. Traced: the floor more than doubled
                # during one continuous rack and a false slice followed.
                measured = float(np.median(self._pause_ds))
                self._floor = max(min(measured, self._floor * 1.5), 1e-6)
                self._pause_ds.clear()
            return self._try_fire()
        return False

    def _try_fire(self) -> bool:
        now = time.monotonic()
        if now - self._last_fire < self.MIN_INTERVAL:
            return False
        if self._fire():
            self._last_fire = now
            self._quiet = 0
            self._pending = True
            return True
        return False
