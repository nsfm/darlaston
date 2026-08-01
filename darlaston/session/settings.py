"""Where captures go, and what they are called.

`~/Pictures` is not a guess -- it is the platform convention on all three
targets, and on Linux it is *localised*, so it must be looked up rather than
hardcoded. A German desktop calls it `~/Bilder`, and a user who relocated it
expects us to notice.

Naming follows the tokens a photographer expects from a camera or an importer,
because the alternative is `ov0001.jpg` and a folder you cannot search in six
months.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .model import config_dir


def pictures_dir() -> Path:
    """The platform's pictures directory, resolved rather than assumed."""
    system = platform.system()
    if system == "Linux":
        # freedesktop XDG user dirs. The value is localised and relocatable, so
        # ask rather than guess; fall back only if the tool is absent.
        try:
            out = subprocess.run(["xdg-user-dir", "PICTURES"],
                                 capture_output=True, text=True, timeout=2)
            path = Path(out.stdout.strip())
            if out.returncode == 0 and path.is_absolute():
                return path
        except (OSError, subprocess.SubprocessError):
            pass
        env = os.environ.get("XDG_PICTURES_DIR")
        if env:
            return Path(os.path.expandvars(env.strip('"')))
    return Path.home() / "Pictures"


#: Tokens usable in folder and filename patterns.
TOKENS = {
    "date": "2026-07-27",
    "time": "22-41-08",
    "year": "2026",
    "month": "07",
    "day": "27",
    "seq": "0001",
    "camera": "Big Zeiss cam",
    "scope": "Zeiss Universal",
    "objective": "40x0.75",
    "illumination": "phase",
    "magnification": "50x",
    "subject": "dopamine",
}

_UNSAFE = re.compile(r"[^\w\-.]+")


def _slug(value: str) -> str:
    """Filesystem-safe, but readable. Nobody wants `40%C3%970.75`."""
    value = (value.replace("×", "x").replace("·", "-")
                  .replace("/", "").replace(" ", "-"))
    return _UNSAFE.sub("-", value).strip("-.") or "unknown"


@dataclass
class Settings:
    """Persisted between sessions. Every field is something a user may reasonably
    disagree with, which is the test for whether it belongs here."""

    capture_root: str = ""
    folder_pattern: str = "{date}"
    #: Subject leads after the sequence because it is what you will search for.
    #: Empty tokens collapse, so an unnamed capture is just
    #: `0001_40x0.75_phase.dng` rather than carrying an empty gap.
    filename_pattern: str = "{seq}_{subject}_{objective}_{illumination}"
    raw_format: str = "dng"          # dng | tiff
    #: What a capture leaves on disk: the raw file, a developed JPEG, or
    #: both. Both by default, and deliberately -- no ordinary camera ships
    #: set to raw only, and until now this program had no way at all to
    #: produce a photograph somebody could open, which made every output a
    #: negative.
    #:
    #: "jpeg" removes the raw once the JPEG is safely written, and only for
    #: ordinary captures. Stack slices and mosaic tiles keep their raws
    #: whatever this says, because the merge and the stitcher read them --
    #: deleting those would mean a setting about file formats quietly
    #: destroying the session's actual work.
    image_format: str = "both"       # both | raw | jpeg
    jpeg_quality: int = 95
    #: Framing guides over the live view, as a camera's viewfinder offers.
    #: Kept between sessions because it is a habit rather than a decision.
    framing_grid: str = "none"       # none | thirds | grid
    framing_cross: bool = False
    keep_slices: bool = False        # Z-stack slices after the EDF is built
    sequence_resets_daily: bool = True
    #: Written into every file as EXIF Artist and Copyright. Empty writes
    #: neither -- an empty copyright notice is worse than none, because it
    #: looks like a claim that the work is unowned. These matter the moment
    #: a slide photograph is published, which for this field is often.
    artist: str = ""
    copyright: str = ""
    #: Stack-merge knobs, surfaced in the assembly window's options menu.
    #: Defaults are the measured winners on tools/stack_bench.py; the knobs
    #: exist because the right smoothing is partly a statement about the
    #: subject, and that judgement belongs to the photographer.
    stack_output: str = "bayer"      # bayer | linear (RGB, ~3x the size)
    #: Wigglegram depth polarity. Slice 1 is wherever the operator started
    #: racking, so "near" is a habit, not a fact; this follows the habit.
    wiggle_invert: bool = False
    #: off | light | normal | strong. "light" is the default because it is
    #: the only setting that costs nothing measurable: detail equal to
    #: winner-takes-all, a quarter less glow ring. "normal" trades 2% of
    #: fine detail for much smoother glow, and on a field of scattered
    #: debris it also diffuses away real background specks.
    stack_smoothing: str = "light"
    stack_feather: float = 2.0       # blend feather at depth seams, px
    #: How the live preview is scaled to the window. This is a real trade
    #: and the right answer depends on the subject and the machine, which is
    #: why it is here rather than decided once in the code.
    #:
    #:   full     the frame reduced directly, correctly anti-aliased.
    #:            Sharpest, and 4-9 ms of a 25 ms frame.
    #:   fast     fitted in one bilinear step, several milliseconds less.
    #:            Samples rather than averages, so it can alias detail near
    #:            the preview's resolving limit.
    #:   reduced  reduced by exactly half first, which is the only cheap
    #:            reduction OpenCV has, then fitted. Cheapest and calmest
    #:            under motion, and visibly the softest.
    #:
    #: `fast` is the default on the evidence and on Nate's eye, which agreed.
    #: The theoretical objection to it is real but does not bite here: on a
    #: real diatom at 16x it came within 0.8 of 255 levels of `full`, and
    #: under sub-pixel stage motion it churned 6% more, not the shimmer a
    #: near-Nyquist test pattern had predicted. That pattern was the wrong
    #: test -- it was built to alias, and areolae on a binned preview are
    #: nowhere near the sampling limit. `full` is a click away for anyone
    #: who wants to count pixels.
    preview_quality: str = "fast"    # full | fast | reduced
    #: Threads OpenCV may use. 0 follows the measured default in cpu.py,
    #: which is four; more is available for anyone who would rather spend
    #: the machine than the milliseconds.
    cpu_threads: int = 0

    def __post_init__(self) -> None:
        if not self.capture_root:
            self.capture_root = str(pictures_dir() / "darlaston")

    # ---- resolution ------------------------------------------------------

    def tokens(self, *, setup=None, seq: int = 1, subject: str = "",
               when: datetime | None = None) -> dict[str, str]:
        when = when or datetime.now()
        t = {
            "date": when.strftime("%Y-%m-%d"),
            "time": when.strftime("%H-%M-%S"),
            "year": when.strftime("%Y"),
            "month": when.strftime("%m"),
            "day": when.strftime("%d"),
            "seq": f"{seq:04d}",
            "subject": _slug(subject) if subject else "",
        }
        # Every optics token is always defined, empty when there is no
        # setup to fill it. Leaving them absent made an unrecognised-token
        # rule -- meant to keep a *typo* visible -- print a literal
        # "{objective}_{illumination}" into filenames whenever the camera
        # was driven without a configured stand.
        t.update({"camera": "", "scope": "", "objective": "",
                  "illumination": "", "magnification": ""})
        if setup is not None:
            obj = setup.scope.turret.objective
            total = setup.total_magnification
            t.update({
                "camera": _slug(setup.camera.display),
                "scope": _slug(setup.scope.name),
                "objective": _slug(obj.label) if obj else "",
                "illumination": _slug(setup.illumination.key),
                "magnification": f"{total:g}x" if total else "",
            })
        return {k: v for k, v in t.items()}

    def resolve(self, *, setup=None, seq: int = 1, subject: str = "",
                when: datetime | None = None, suffix: str = "") -> Path:
        """Full path for one capture. Empty tokens collapse rather than leaving
        stray separators, so an unconfigured turret does not produce
        `0001__phase.dng`."""
        t = self.tokens(setup=setup, seq=seq, subject=subject, when=when)
        folder = _fill(self.folder_pattern, t)
        name = _fill(self.filename_pattern, t)
        ext = suffix or ("." + self.raw_format)
        return Path(self.capture_root) / folder / (name + ext)

    # ---- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or (config_dir() / "settings.json")
        if path.exists():
            try:
                return cls(**json.loads(path.read_text()))
            except (ValueError, TypeError):
                pass          # a corrupt file must not stop the app opening
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or (config_dir() / "settings.json")
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


def _fill(pattern: str, tokens: dict[str, str]) -> str:
    out = pattern
    for key, value in tokens.items():
        out = out.replace("{" + key + "}", value)
    # Any token we do not recognise is left visible rather than silently
    # dropped, so a typo in a pattern is obvious in the filename.
    out = re.sub(r"_{2,}", "_", out).strip("_-")
    return out or "capture"


def next_sequence(folder: Path, pattern: str) -> int:
    """Highest existing sequence in the folder, plus one.

    Derived from what is on disk rather than kept in a counter, so it survives
    a crash, a manual file move, and two copies of the app.
    """
    if not folder.exists():
        return 1
    best = 0
    for entry in folder.iterdir():
        for match in re.finditer(r"(\d{4,})", entry.stem):
            best = max(best, int(match.group(1)))
    return best + 1
