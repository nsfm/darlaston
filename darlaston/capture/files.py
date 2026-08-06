"""Moving finished captures into a session, and taking them back out.

Shared by mosaics and stacks because both had the same two mistakes, and
because either one destroys a frame that cannot be retaken: by the time
anybody notices, the stage has moved, the lamp has drifted a degree, and
the specimen has been under it for another ten minutes.
"""
from __future__ import annotations

import shutil
from pathlib import Path

#: The photograph written beside every raw. It travels with the negative
#: and is deleted with it, so a session folder describes itself and an
#: undone frame does not survive as a picture.
SIDECAR = ".jpg"


def claim(source: Path, target: Path) -> Path:
    """Move a capture into a session, never over anything already there.

    Returns the path actually used, which the caller must record: it is
    not always `target`.

    Both sessions derive the next name from the number of entries in the
    manifest -- `tile_007.dng` for the seventh tile -- and then moved
    onto it with no check at all. That name is only free while the
    manifest and the directory agree, and they come apart easily: the
    move happens before the save, so anything interrupting the pair
    leaves a file on disk that nothing knows about. The next capture then
    computes the same index and `shutil.move` silently writes over a real
    frame.

    Stepping to the next free name instead keeps both. A duplicate is a
    puzzle somebody can solve later; an overwrite is not.

    The sidecar comes along. Left behind, the photograph stayed in the
    day's folder with a name matching nothing, and `undo` -- which only
    ever looks inside the session -- could not reach it, so an undone
    tile survived as a picture of a frame that officially never happened.
    """
    source = Path(source)
    target = _free(Path(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    beside = source.with_suffix(SIDECAR)
    if beside.exists():
        shutil.move(str(beside), str(target.with_suffix(SIDECAR)))
    return target


def discard(path: Path) -> None:
    """Delete a capture and the photograph beside it."""
    path = Path(path)
    path.unlink(missing_ok=True)
    path.with_suffix(SIDECAR).unlink(missing_ok=True)


def _free(target: Path) -> Path:
    """`target`, or the first `name_2`, `name_3`... that is not taken."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(2, 1000):
        candidate = target.with_name(f"{stem}_{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(
        f"{target} and 998 names after it are all taken")
