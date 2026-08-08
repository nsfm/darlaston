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

from ..i18n import N_
from .model import config_dir, known_fields, write_atomically


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
    #: Burn a scale bar into the sidecar JPEG. Off by default: it is a
    #: mark on the photograph and that is the operator's call, not ours.
    #: Never touches the raw, whatever this says -- the DNG stays what the
    #: sensor recorded and a bar can always be drawn later from um_per_px.
    scale_bar: bool = False
    #: Which dressing. The measurement is identical in all of them; only
    #: the furniture differs, and furniture is taste.
    scale_bar_style: str = "adaptive"
    #: The face the label is set in. Bundled first: IBM Plex was chosen
    #: for this application because it was drawn for technical material,
    #: and a measurement is the most technical thing on the picture.
    scale_bar_face: str = "IBM Plex Mono"
    #: Write "um" instead of the micro sign, for downstream tooling that
    #: cannot take the character. Off, because the sign is correct.
    scale_bar_plain_units: bool = False
    #: Which corner, and how large the furniture is. Neither changes the
    #: measurement: the length still comes from the 1-2-5 ladder.
    scale_bar_corner: str = "br"
    scale_bar_size: str = "medium"
    #: Which side of the rule the label sits on. "auto" is the inside:
    #: below in a top corner, above in a bottom one.
    scale_bar_label: str = "auto"
    #: How solid the mark is, 0.15 to 1. A bar is a reference rather than
    #: a caption, so it can afford to sit under the picture a little.
    scale_bar_opacity: float = 1.0
    #: And separately for the live view, which is a different job. On the
    #: photograph the bar is part of the finished thing; on screen it is a
    #: reminder sitting over the subject being examined, so it defaults
    #: fainter than the one that gets published.
    scale_bar_live_opacity: float = 0.5

    def bar_style(self, live: bool = False) -> dict:
        """The scale bar's settings, as `scalebar.draw` takes them.

        One place, because there are four callers now -- the capture, the
        live view, the plate and the style window -- and the plate had
        already drifted, drawing the module's defaults while the operator
        had chosen something else next door.
        """
        return {
            "style": self.scale_bar_style,
            "face": self.scale_bar_face,
            "corner": self.scale_bar_corner,
            "size": self.scale_bar_size,
            "label_at": self.scale_bar_label,
            "plain_units": self.scale_bar_plain_units,
            "opacity": (self.scale_bar_live_opacity if live
                        else self.scale_bar_opacity),
        }
    #: Draw the bar over the live view as well, so what is on screen is
    #: what the photograph will carry.
    scale_bar_live: bool = False
    #: Framing guides over the live view, as a camera's viewfinder offers.
    #: Kept between sessions because it is a habit rather than a decision.
    #: Who draws the window's title bar and buttons: auto | ours | system.
    #:
    #: Auto means ours everywhere except where the desktop already does it
    #: well. KDE draws a real decoration in the desktop's own colours and
    #: a tiling window manager deliberately draws none, so both are left
    #: alone; GNOME under Wayland refuses server-side decorations and Qt
    #: falls back to something that matches neither GNOME nor this
    #: program, so there we draw our own.
    window_frame: str = "auto"       # auto | ours | system
    #: Ask GitHub, once per launch, whether a newer release exists. On by
    #: default, because an adopter running a version with a fixed bug in
    #: it is the failure this exists to prevent -- and switchable, because
    #: a program used on an institution's network should never be the
    #: reason somebody has to account for an outbound request they did not
    #: choose to make.
    check_for_updates: bool = True
    #: Which camera to open, as a `discovery.Camera.key`. Empty means
    #: nobody has said, and the application picks the likeliest and shows
    #: which -- rather than asking before it has done anything useful.
    camera_choice: str = ""
    #: The chosen camera's fingerprint, so a cable moved to another
    #: socket is recognised rather than treated as a stranger.
    camera_fingerprint: str = ""
    #: Cameras the operator has said are not the microscope, as
    #: `key -> fingerprint`. A laptop's own webcam and the infrared sensor
    #: beside it are on the bus at every launch, and on a machine with no
    #: other camera attached the likeliest-first rule opens one of them.
    #:
    #: This is a *preference*, not a rule the program worked out, and the
    #: distinction is the whole reason it is allowed to exist:
    #: `discovery.look` deliberately hides nothing, because a heuristic
    #: confident enough to hide a device will one day hide the right one.
    #: An operator saying "not this one" is not a heuristic. It is still
    #: never a filter -- the picker lists every ignored camera, says so,
    #: and lets it be taken back -- it only stops one being opened by
    #: default.
    #:
    #: Stored per machine because it describes a bench, not a camera: the
    #: same model may be somebody else's microscope camera.
    #:
    #: The fingerprint rides along so this cannot silently hide a
    #: *different* device that later appears at the same port. A port is a
    #: socket; ignoring a socket is not what anybody meant.
    ignored_cameras: dict[str, str] = field(default_factory=dict)
    #: The working white balance. Kept between sessions on purpose:
    #: somebody who looks at the same kind of specimen under the same lamp
    #: starts nearer where they want to be, and undoing it is one click.
    #: Gains are R, G, B with green at one.
    #:
    #: No separate on/off. Unity *is* off, so there is one thing to store
    #: and no way for a flag and a value to disagree about whether a
    #: correction is in force.
    #:
    #: Distinct from the flat's measured balance, which lives in the
    #: calibration store keyed to the optical configuration. This one is
    #: per bench and per habit; that one is per objective and per lamp.
    white_balance_gains: list = field(default_factory=lambda: [1.0, 1.0, 1.0])
    framing_grid: str = "none"       # none | thirds | grid
    framing_cross: bool = False
    #: Keep the individual slices after a stack is merged. Default changed
    #: from False to True when the setting was first made to do anything:
    #: it had never been read, so every stack ever shot kept its slices,
    #: and shipping the feature with the old default would have started
    #: deleting photographs on upgrade for people who had never touched it.
    #: A switch that destroys data defaults to not destroying data.
    keep_slices: bool = True         # Z-stack slices after the EDF is built
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
    #: Flatten the featureless background to one plane in `depth.png` and
    #: therefore in the mesh. Where nothing was ever in focus the merge is
    #: choosing between identical-looking slices on sensor noise, and the
    #: composite does not care but the geometry does: background depth
    #: standard deviation measured 86.5 and 51.9 falling to zero, with
    #: specimen depth unchanged to two decimals. It touches nothing but
    #: the depth map, deliberately.
    stack_mask_background: bool = True
    #: Bound on how fast the composite's depth map may change, in slices
    #: per pixel. Zero is off. This is what removes the halo: a selection
    #: map that changes faster than the blur can justify integrates the
    #: same ray twice, which is what a halo is.
    #:
    #: A real control rather than a constant, and the only one here that
    #: is. Everything else in this pipeline is derived from the optics,
    #: but the bound that looks best depends on the subject: crossing
    #: diatoms make genuine steep steps and want about 0.6, a smooth
    #: carapace wants 0.2 to 0.3, and estimating it from the depth map
    #: separated those two cases by far too little to trust. The objective
    #: here is which photograph looks better, and that has no derivable
    #: optimum. Zero until the operator turns it on; the window offers the
    #: optics-derived starting point.
    stack_clamp_slope: float = 0.0
    #: How that bound gets chosen: off, last, or choose.
    #:
    #: "choose" shows a few crops mid-merge and takes the one picked. It
    #: can only exist because of where it sits: depth and mask are done,
    #: the blend has not started, and a variant on a crop costs
    #: milliseconds against minutes for the merge, so the question is
    #: cheap exactly there and nowhere else.
    #:
    #: A mosaic never asks, whatever this says. Forty tiles is forty
    #: questions, and worse, forty different answers stitched together.
    stack_halo_mode: str = "off"
    #: Hold the picture at a sensible brightness on its own. On by
    #: default, and silently inert on a camera that offers neither
    #: control, which `CameraInfo` already reports as None rather than as
    #: a zero width range.
    auto_exposure: bool = True
    #: Move gain only, leaving exposure wherever the operator put it.
    #: Worth having for its own sake -- a fixed exposure is one less
    #: variable across a session -- and necessary on a camera whose
    #: exposure response is not monotonic, where a loop that hunts on
    #: exposure never settles.
    auto_exposure_lock_time: bool = False
    #: How much of the remaining error the loop takes per step, as a
    #: multiplier on the measured damping. Below one is calmer, above is
    #: more eager. Exposed because how twitchy a picture is allowed to
    #: look while somebody is working under it is a matter of taste, and
    #: because a lamp on a rheostat and one on a stable supply want
    #: different answers.
    auto_exposure_reactivity: float = 1.0
    #: Frame rate the live view is expected to hold. Exposure is spent
    #: freely up to this, and gain takes over past it -- except where the
    #: mode cannot reach it anyway, in which case exposure is free up to
    #: the frame period the sensor does manage.
    auto_exposure_target_fps: float = 30.0
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
    #: The presentation window's overlays. Each answers something a
    #: visitor at an event actually asks, and each is separate because a
    #: table at a society meeting and a screen in a hallway want
    #: different amounts said. The scale bar and the magnification are on
    #: by default: "how big is that" and "what magnification is this" are
    #: the two questions every audience opens with.
    present_scale_bar: bool = True
    present_magnification: bool = True
    #: What is on the slide, as typed in the rail. On by default because
    #: it costs nothing when the boxes are empty and is exactly the label
    #: a passer-by needs when they are not.
    present_subject: bool = True
    #: The operator's own two lines, top left: a society name and its
    #: website, an exhibit note, whatever the table wants said. The text
    #: is kept even while the toggle is off, so hiding it for a moment
    #: does not mean retyping it.
    present_header: bool = False
    present_header_title: str = ""
    present_header_subtitle: str = ""
    #: A small mark that says the picture is happening now. Off by
    #: default; it earns its place at events, where a live microscope
    #: is routinely mistaken for a looping video.
    present_live: bool = False
    #: Crop the frame to fill the presentation window rather than
    #: letterboxing it. Projectors are wider than the sensor, and two
    #: black bars are a lot of screen to spend on the frame's edges.
    present_fill: bool = False
    #: Caption type scale and ink opacity: small | normal | large, and
    #: solid | soft | faint. A tabletop monitor and a hall projector are
    #: read from different distances, and how far the words may sink
    #: into the picture is taste.
    present_caption_size: str = "normal"
    present_caption_opacity: str = "solid"
    #: The measured width of the audience's picture, in whichever unit
    #: it was taken -- projector screens are quoted in feet or metres,
    #: desktop displays in inches or centimetres. Zero means unmeasured.
    #: With it the window can state how large things really appear on
    #: that screen, which is the honest answer to "what magnification
    #: is this" -- the figure depends on the screen, which is exactly
    #: the explanation operators keep giving.
    present_screen_width: float = 0.0
    present_screen_unit: str = "cm"  # cm | in | m | ft
    #: Where the network stream listens when it is turned on. The
    #: switch itself is deliberately not remembered: a program used on
    #: an institution's network must never open a port because of what
    #: happened last week. Turning it on is one menu click, per session,
    #: on purpose.
    present_stream_port: int = 8089
    #: Send the stream bare, with no captions, for streamers who build
    #: their own titles in OBS. The projector keeps its captions either
    #: way; this describes only the wire.
    present_stream_clean: bool = False
    #: Where the presentation window last sat, as Qt geometry bytes in
    #: hex. At an event the second screen is the same projector all day,
    #: and a window that reopens on it is one less thing to arrange in
    #: front of an audience.
    present_geometry: str = ""

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
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()      # a corrupt file must not stop the app opening
        if not isinstance(raw, dict):
            return cls()
        try:
            # Only the keys this version knows. Handing the whole file to
            # the constructor made every schema change a reset: one key
            # added by a newer build, or removed by a later one, raised
            # TypeError and *every* setting went back to its default --
            # capture folder, filename pattern, artist, the lot. Catching a
            # corrupt file is deliberate; catching schema drift the same
            # way was not.
            return cls(**known_fields(cls, raw))
        except (ValueError, TypeError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or (config_dir() / "settings.json")
        write_atomically(path, json.dumps(asdict(self), indent=2) + "\n")


def _fill(pattern: str, tokens: dict[str, str]) -> str:
    out = pattern
    for key, value in tokens.items():
        out = out.replace("{" + key + "}", value)
    # Any token we do not recognise is left visible rather than silently
    # dropped, so a typo in a pattern is obvious in the filename.
    out = re.sub(r"_{2,}", "_", out).strip("_-")
    return out or "capture"


def filename_problem(pattern: str) -> str | None:
    """The message key for why this pattern would lose captures, or None.

    `{seq}` is not decoration. Every other token is constant across a run
    -- same subject, same objective, same illumination, same date -- so a
    pattern without it resolves to one name and each capture silently
    overwrites the one before. At speed, during a timelapse, that is a
    whole night's work landing in a single file.

    A key rather than a sentence, because this is shown in the interface
    and everything shown there is translated.
    """
    if not pattern.strip():
        return N_("capture.files.filename.empty")
    if "{seq}" not in pattern:
        return N_("capture.files.filename.needs_seq")
    return None


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
