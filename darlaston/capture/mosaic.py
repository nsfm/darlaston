"""Mosaic sessions: tiles, their positions, and the paper trail.

A mosaic is a folder. Tiles are ordinary captures moved in and renamed to
their index, and `manifest.json` records where the tracker said each one was
taken -- in preview pixels, in the tracker's own frame. Those positions are
*constraints* for registration, never the registration itself: dead-reckoning
drifts, and stitching will refine every seam by phase correlation within a
window around them. What the constraint buys is immunity to the confident
wrong match (DISCOVERY.md 10a), which no confidence score can detect alone.

The manifest is rewritten after every change, because a mosaic interrupted
at tile 23 of 40 must still be a mosaic, not a folder of mystery files.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Tile:
    index: int
    filename: str
    #: View centre in tracker preview pixels at the moment of capture.
    #: None when the tracker had no lock -- the tile is still kept, and
    #: registration will have to work harder for it.
    pos: tuple[float, float] | None
    #: Preview frame (w, h) at capture, the units pos is measured against.
    frame: tuple[int, int]
    #: Subfolder name when this tile is a whole Z-stack (a StackSession
    #: living inside the mosaic); `filename` then points at its merged
    #: `stacked.dng`. The stitcher does not care how many exposures a tile
    #: took -- a tile is a position and an image -- which is what lets
    #: single shots and stacks mix freely in one mosaic.
    stack: str | None = None


def overlap_fraction(a: tuple[float, float], b: tuple[float, float],
                     frame: tuple[int, int]) -> float:
    """Overlapping area of two same-size views, as a fraction of one view."""
    w, h = frame
    if w <= 0 or h <= 0:
        return 0.0
    ox = max(0.0, w - abs(a[0] - b[0]))
    oy = max(0.0, h - abs(a[1] - b[1]))
    return (ox * oy) / (w * h)


def best_overlap(pos: tuple[float, float], tiles: list[Tile],
                 frame: tuple[int, int]) -> tuple[float, int | None]:
    """The most the current view overlaps any existing tile, and with which.

    This is the number the operator steers by: stitching wants roughly 15-25%
    -- less risks a seam with nothing to register on, much more is wasted
    slide coverage.
    """
    best, who = 0.0, None
    for t in tiles:
        if t.pos is None:
            continue
        f = overlap_fraction(pos, t.pos, frame)
        if f > best:
            best, who = f, t.index
    return best, who


class MosaicSession:
    """One mosaic: a directory of tiles and a manifest that explains them."""

    def __init__(self, root: Path | str, subject: str = "") -> None:
        stamp = time.strftime("%y%m%d-%H%M%S")
        name = f"{stamp}_{subject or 'mosaic'}_tiles"
        self.dir = Path(root) / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.subject = subject
        self.tiles: list[Tile] = []
        self._meta: dict = {}

    # ---- building --------------------------------------------------------

    def set_meta(self, **meta) -> None:
        """Optics, illumination, exposure -- whatever registration and the
        composite will want to know later."""
        self._meta.update({k: v for k, v in meta.items() if v is not None})
        self._save()

    def adopt(self, capture_path: Path, pos: tuple[float, float] | None,
              frame: tuple[int, int]) -> Tile:
        """Move a finished capture into the session as the next tile."""
        index = len(self.tiles) + 1
        filename = f"tile_{index:03d}.dng"
        target = self.dir / filename
        shutil.move(str(capture_path), target)
        tile = Tile(index=index, filename=filename,
                    pos=(float(pos[0]), float(pos[1])) if pos else None,
                    frame=(int(frame[0]), int(frame[1])))
        self.tiles.append(tile)
        self._save()
        return tile

    def begin_stack_tile(self, subject: str = ""):
        """A StackSession at the next tile's reserved slot.

        Nothing is recorded in the manifest yet -- the folder exists, the
        tile does not. `adopt_stack` makes it real once the operator slides
        away; an abandoned folder is just deleted. Only one can be in
        flight, because every capture routes into it until it seals.
        """
        from .stack import StackSession
        index = len(self.tiles) + 1
        return StackSession.at(self.dir / f"tile_{index:03d}_stack",
                               subject or self.subject)

    def adopt_stack(self, stack, pos: tuple[float, float] | None,
                    frame: tuple[int, int]) -> Tile:
        """Record a sealed stack as the next tile.

        The merged `stacked.dng` may not exist yet -- the merge runs in the
        background while the operator is already racking the next field --
        and that is fine: the stack folder is complete and mergeable by
        construction, so the stitcher can finish any merge it finds undone.
        """
        index = len(self.tiles) + 1
        name = stack.dir.name
        expected = f"tile_{index:03d}_stack"
        if name != expected:
            raise ValueError(f"stack {name} is not the next tile "
                             f"({expected}) — tiles adopted out of order?")
        tile = Tile(index=index, filename=f"{name}/stacked.dng",
                    pos=(float(pos[0]), float(pos[1])) if pos else None,
                    frame=(int(frame[0]), int(frame[1])), stack=name)
        self.tiles.append(tile)
        self._save()
        return tile

    def undo(self) -> Tile | None:
        """Remove the last tile, file and record both. The one mosaic-specific
        mistake is 'that tile was mid-crank'; this is its eraser."""
        if not self.tiles:
            return None
        tile = self.tiles.pop()
        if tile.stack:
            shutil.rmtree(self.dir / tile.stack, ignore_errors=True)
        else:
            (self.dir / tile.filename).unlink(missing_ok=True)
        self._save()
        return tile

    # ---- the paper trail -------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.dir / "manifest.json"

    def _save(self) -> None:
        payload = {
            "darlaston_mosaic": 1,
            "subject": self.subject,
            "meta": self._meta,
            "positions_are": ("view centres in binned-preview pixels, "
                              "tracker frame, dead-reckoned; constraints "
                              "for registration, not registration"),
            "tiles": [{"index": t.index, "file": t.filename,
                       "pos": list(t.pos) if t.pos else None,
                       "frame": list(t.frame),
                       **({"stack": t.stack} if t.stack else {})}
                      for t in self.tiles],
        }
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.manifest_path)

    @classmethod
    def load(cls, directory: Path | str) -> "MosaicSession":
        """Reopen an interrupted session to continue or to stitch."""
        directory = Path(directory)
        data = json.loads((directory / "manifest.json").read_text())
        session = cls.__new__(cls)
        session.dir = directory
        session.subject = data.get("subject", "")
        session._meta = data.get("meta", {})
        session.tiles = [
            Tile(index=t["index"], filename=t["file"],
                 pos=tuple(t["pos"]) if t.get("pos") else None,
                 frame=tuple(t.get("frame", (0, 0))),
                 stack=t.get("stack"))
            for t in data.get("tiles", [])]
        return session
