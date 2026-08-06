"""Somewhere for a failure to go.

The package had no logging at all, and the frozen build sets
`console=False` -- so every `except Exception: pass` and every
`traceback.print_exc()` in it wrote to a handle that does not exist in
the thing people actually download. That is not a tidiness problem. It
is the reason a report can only ever be "it did not work": the program
knew exactly what went wrong, said so, and said it into nothing.

Deliberately small. One rotating file, and the console too when there is
one. No configuration, no third-party dependency, and nothing that can
itself fail loudly enough to matter -- a logger that stops the
application starting would be worse than the silence it replaces.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

#: Three files of a megabyte. Enough to hold a long session and the one
#: before it, small enough to paste the tail of into a bug report.
MAX_BYTES = 1_000_000
KEEP = 3

_started = False


def state_dir() -> Path:
    """Where things the program writes about itself live.

    Separate from `config_dir`, which holds what the *operator* wrote --
    the objectives, the stands, the settings. Losing a log is nothing;
    losing that is a session's worth of typing, and the two should not
    share a directory anybody might be tempted to clear out.
    """
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    d = Path(base) / "darlaston"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path() -> Path:
    """The log file, so the interface can tell somebody where to look."""
    return state_dir() / "darlaston.log"


def setup(level: int = logging.INFO) -> Path | None:
    """Install handlers on the root logger. Safe to call more than once.

    Returns the file being written to, or None if one could not be
    opened -- a read-only home directory is a real situation and is not
    a reason to refuse to start.
    """
    global _started
    if _started:
        return path() if any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in logging.getLogger().handlers) else None
    _started = True

    root = logging.getLogger()
    root.setLevel(level)
    shape = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    written: Path | None = None
    try:
        target = path()
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8")
        handler.setFormatter(shape)
        root.addHandler(handler)
        written = target
    except OSError:
        pass          # no home to write to; the console handler still helps

    # Only when there is one. In the frozen build `sys.stderr` can be None
    # outright, and handing that to StreamHandler makes every log call
    # raise -- which would turn the fix into a new fault.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(shape)
        root.addHandler(console)

    # Qt's own warnings are worth having, and they arrive through a
    # separate channel that otherwise goes to stderr and vanishes.
    try:
        from PySide6 import QtCore

        QtCore.qInstallMessageHandler(_from_qt)
    except Exception:
        logging.getLogger(__name__).debug(
            "no Qt message handler installed", exc_info=True)

    # And the ones nobody caught. These are the failures most worth having
    # and the ones least likely to be seen: the default hooks write a
    # traceback to stderr, which in the frozen build is not there. Every
    # long job in this program is a thread, so both hooks matter.
    sys.excepthook = _unhandled
    threading.excepthook = _unhandled_in_thread

    return written


def _unhandled(kind, value, tb) -> None:
    logging.getLogger("unhandled").critical(
        "unhandled exception", exc_info=(kind, value, tb))
    sys.__excepthook__(kind, value, tb)


def _unhandled_in_thread(args) -> None:
    # A thread ending on an exception is how a stitch, a merge or a
    # timelapse stops without anything on screen changing.
    logging.getLogger("unhandled").critical(
        "unhandled exception in thread %s",
        getattr(args.thread, "name", "?"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


_QT_LEVELS = {0: logging.DEBUG, 1: logging.WARNING, 2: logging.WARNING,
              3: logging.ERROR, 4: logging.INFO}


def _from_qt(mode, context, message) -> None:
    logging.getLogger("qt").log(_QT_LEVELS.get(int(mode), logging.INFO),
                                "%s", message)
