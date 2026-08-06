"""Entities: cameras, scopes, objectives, illumination.

Three things here are deliberate and were corrected out of an earlier draft:

  * **Cameras are named by their owner.** The serial identifies the hardware;
    nobody wants to read `TP211207110153E2F7CE016F254B9C0` across a session.
  * **Illumination is not part of the microscope.** It changes several times a
    sitting, independently of everything else, so it is a first-class axis
    rather than a property of a stand.
  * **Objectives are an ordered ring**, because that is what a turret is. The
    interaction is next/previous, not a dropdown.

See DESIGN.md 1.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    d = Path(base) / "darlaston"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# Illumination
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class IlluminationMode:
    """A way of lighting the specimen.

    `kind` drives calibration and focus-metric defaults; `inverted` is a
    display and export choice only. Inverting raw data would destroy its
    linearity and with it the point of raw, so the intent is carried as
    metadata and applied downstream.
    """

    key: str
    label: str
    kind: str                    # brightfield | darkfield | phase
    inverted: bool = False

    @property
    def display(self) -> str:
        return f"{self.label} (inverted)" if self.inverted else self.label


BUILTIN_ILLUMINATION: tuple[IlluminationMode, ...] = (
    IlluminationMode("brightfield", "Brightfield", "brightfield"),
    IlluminationMode("brightfield_inv", "Brightfield", "brightfield", inverted=True),
    IlluminationMode("darkfield", "Darkfield", "darkfield"),
    IlluminationMode("phase", "Phase contrast", "phase"),
)


# --------------------------------------------------------------------------
# Optics
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Objective:
    magnification: float
    na: float | None = None
    kind: str = ""               # "Planapo", "Achromat", ...
    immersion: str = ""          # "", "oil", "water", "glycerol"
    #: Who made it. Goes into EXIF LensMake, because a raw developer treats
    #: the objective as the lens and this is where it looks for the maker.
    maker: str = ""              # "Carl Zeiss", "Leitz", ...
    #: Engraved on most objectives, and the only way to tell two otherwise
    #: identical ones apart in an archive.
    serial: str = ""
    #: Free working distance in millimetres. This is EXIF SubjectDistance,
    #: and it is the literal truth: the distance from the front element to
    #: the thing being photographed.
    working_distance_mm: float | None = None

    @property
    def label(self) -> str:
        na = f"/{self.na:g}" if self.na else ""
        oil = f" {self.immersion}" if self.immersion else ""
        return f"{self.magnification:g}×{na}{oil}".strip()


@dataclass
class Turret:
    """An ordered ring of objectives. Empty positions are allowed: real turrets
    have them, and a five-position turret with three objectives should not
    silently renumber itself."""

    positions: list[Objective | None] = field(default_factory=list)
    current: int = 0
    #: Which empty positions have a dust cap in them, parallel to
    #: `positions` and short or absent on older saved libraries.
    #:
    #: Worth distinguishing from an open hole because the two look like
    #: opposite ends of the scale: an open position passes the whole
    #: condenser cone straight through and blows white, a capped one is
    #: black. Detection sees both as "no objective" and can predict
    #: neither without being told which it is.
    capped: list[bool] = field(default_factory=list)

    def is_capped(self, index: int) -> bool:
        return (self.positions[index] is None if 0 <= index < len(self.positions)
                else False) and index < len(self.capped) and bool(
                    self.capped[index])

    def resize(self, count: int) -> None:
        """Set how many positions the turret holds, keeping what fits."""
        count = max(0, count)
        self.positions = (self.positions + [None] * count)[:count]
        self.capped = (self.capped + [False] * count)[:count]
        if self.current >= count:
            self.current = max(0, count - 1)

    def step(self, delta: int) -> int:
        """Advance around the ring, skipping empty positions."""
        n = len(self.positions)
        if n == 0:
            return self.current
        i = self.current
        for _ in range(n):
            i = (i + delta) % n
            if self.positions[i] is not None:
                break
        self.current = i
        return i

    @property
    def objective(self) -> Objective | None:
        if 0 <= self.current < len(self.positions):
            return self.positions[self.current]
        return None

    def ratio_to(self, index: int) -> float | None:
        """Magnification ratio between the current position and another.

        This is what makes turret detection possible: rotating the turret
        changes the field of view by exactly this factor, and log-polar phase
        correlation can measure it. Used as a *suggestion* only -- a silent
        misdetection would poison every calibration lookup downstream.
        """
        a, b = self.objective, self.positions[index] if 0 <= index < len(self.positions) else None
        if a is None or b is None or not a.magnification:
            return None
        return b.magnification / a.magnification


# --------------------------------------------------------------------------
# Hardware
# --------------------------------------------------------------------------

@dataclass
class CameraProfile:
    """Named by its owner. The serial is the key, not the label."""

    serial: str
    name: str = "Camera"
    model: str = ""
    #: Who made it. Written into EXIF as Make, and empty when unknown --
    #: raw processors key their camera profiles on Make and Model, so a
    #: wrong one is worse than none.
    make: str = ""
    #: What the camera says about itself, hashed. The serial is a USB
    #: port, so a *different* camera plugged into the same socket arrives
    #: wearing the previous occupant's identity -- its name, and worse,
    #: its measured geometry and response. This is how that is caught.
    fingerprint: str = ""
    #: What a profiling run measured, or empty until one has been done.
    #:
    #: `geometry` is one entry per resolution mode: its size, whether the
    #: mode crops or also downscales, and where it sits. A mode is not
    #: simply more or fewer pixels of the same view -- measured on one
    #: camera, 640x480 covers *more* slide than 800x600 -- so everything
    #: converting pixels to micrometres needs this rather than arithmetic.
    #:
    #: `response` is (control value, mean level) pairs. The exposure
    #: control's *timing* is honest, but its brightness is not monotonic,
    #: so a slider that promises brightness drives through this table.
    geometry: list = field(default_factory=list)
    response: list = field(default_factory=list)
    relay: str = ""              # travels with the camera, not the scope
    #: What the relay actually multiplies by. It sits between the objective
    #: and the sensor, so it belongs in the magnification chain like the
    #: Optovar does -- and leaving it out understated total magnification by
    #: exactly its factor, which then understated the f-number and overstated
    #: how much slide each pixel covers. A 2x relay is two stops of
    #: difference in the number a raw developer shows.
    relay_factor: float = 1.0
    #: Sensor pixel pitch in microns. The SDK reports it, but silently
    #: returns 0 on some models -- measured on Nate's E3ISPM20000KPA,
    #: where it left every file without the one tag a scale bar needs.
    #: Set here it is authoritative; the datasheet number is the answer
    #: and the camera cannot argue with it.
    pixel_um: float = 0.0
    last_scope: str | None = None

    @property
    def display(self) -> str:
        return self.name or self.model or self.serial[:12]


#: The mock camera's serial begins with this. Nothing that starts with it
#: is a device anybody owns, so nothing that starts with it is filed.
SYNTHETIC_SERIAL_PREFIX = "MOCK-"


def is_synthetic(serial: str) -> bool:
    """Is this the test camera rather than something on a bench?"""
    return bool(serial) and serial.startswith(SYNTHETIC_SERIAL_PREFIX)


def scope_id(name: str, existing: set[str] | None = None) -> str:
    """A stable id from a name, unique within the library.

    Derived from the name rather than random so the calibration key stays
    readable, and fixed at creation so renaming a stand does not orphan every
    flat filed under it.
    """
    base = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    base = "-".join(p for p in base.split("-") if p) or "scope"
    if not existing or base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


@dataclass
class ScopeProfile:
    id: str
    name: str = "Microscope"
    turret: Turret = field(default_factory=Turret)
    optovar: list[float] = field(default_factory=list)
    optovar_current: int = 0
    condenser: str = ""
    #: Mechanical tube length, in millimetres. It is what turns an
    #: objective's magnification into a focal length: f = tube / M. 160 is
    #: the DIN finite-conjugate standard and what a 1960s Zeiss Universal
    #: uses. Infinity-corrected stands have no tube length as such -- their
    #: tube *lens* focal length takes the same place (165 Zeiss, 200 Nikon
    #: and Leica, 180 Olympus), so the field carries that instead.
    tube_length_mm: float = 160.0
    #: Which way a darkening left edge means the turret index moved. Not
    #: derivable -- it depends on how the turret is mounted and the order
    #: the positions were entered -- so it is a property of the stand. A
    #: stand where detection is consistently one position out has this
    #: the wrong way round.
    rotation_sign: int = 1
    #: Has the handedness been confirmed by a correction, or is it still the
    #: default guess? Until it is known the proposal offers *both* candidate
    #: objectives rather than picking one and being wrong half the time.
    rotation_sign_known: bool = False
    #: Working numerical aperture of the condenser. It sets how bright each
    #: objective is, because the *smaller* of the two apertures is what
    #: actually gathers light -- and above about NA 0.5 that is the
    #: condenser, since filling a high-NA objective needs the condenser
    #: oiled to the slide. 0.55 is a reasonable dry setting; None means
    #: assume the objective always wins, which is true only when oiled.
    condenser_na: float | None = 0.55
    #: Learned brightness per turret position, keyed by illumination kind,
    #: normalised by exposure and gain. Learned rather than derived because
    #: the largest real differences come from things no model covers -- an
    #: objective with no phase ring going dark against the phase stop, most
    #: of all.
    brightness: dict = field(default_factory=dict)

    def signatures(self, illumination_kind: str, model_fallback
                   ) -> tuple[list, list]:
        """What each position should look like, and whether we *know*.

        Returns values and a parallel list saying which were learned from a
        confirmed rotation rather than modelled. The distinction matters more
        than it looks: the condenser iris changes the expected brightness
        ratios by more than the objectives do -- 16x to 25x is 1.08 wide open
        and 0.40 stopped down -- so a modelled value is a first guess and a
        learned one is evidence.
        """
        learned = self.brightness.get(illumination_kind) or []
        values, known = [], []
        for i in range(len(self.turret.positions)):
            got = learned[i] if i < len(learned) else None
            if got:
                values.append(got)
                known.append(True)
            else:
                values.append(model_fallback[i]
                              if i < len(model_fallback) else None)
                known.append(False)
        return values, known

    def learn_brightness(self, illumination_kind: str, index: int,
                         level: float) -> None:
        """Record what a confirmed position actually looked like.

        Averaged into any previous reading rather than replacing it, so one
        odd frame -- a bubble, a thick patch of mountant -- does not become
        the signature for that objective.
        """
        if level is None or level <= 0:
            return
        row = self.brightness.setdefault(illumination_kind,
                                         [None] * len(self.turret.positions))
        while len(row) < len(self.turret.positions):
            row.append(None)
        row[index] = level if not row[index] else 0.7 * row[index] + 0.3 * level

    @property
    def optovar_factor(self) -> float:
        if not self.optovar:
            return 1.0
        return self.optovar[min(self.optovar_current, len(self.optovar) - 1)]


@dataclass
class Setup:
    """What is bolted together right now, plus the two things that change often."""

    camera: CameraProfile
    scope: ScopeProfile
    illumination: IlluminationMode = BUILTIN_ILLUMINATION[0]

    @property
    def total_magnification(self) -> float | None:
        """Objective x Optovar x relay -- everything between slide and sensor.

        All three multiply. The relay is easy to forget because it is bought
        separately and screwed on once, but a 1-2x C-mount adapter set to 2x
        doubles the magnification at the sensor as surely as the Optovar does.
        """
        obj = self.scope.turret.objective
        if obj is None:
            return None
        return (obj.magnification * self.scope.optovar_factor
                * max(self.camera.relay_factor, 1e-6))

    def calibration_key(self) -> str:
        """What a flat is valid for. Deliberately excludes the slide, which the
        session supplies -- phase calibration depends on the actual glass."""
        obj = self.scope.turret.objective
        return "|".join([
            self.camera.serial,
            self.camera.relay or "-",
            self.scope.id,
            obj.label if obj else "-",
            f"optovar{self.scope.optovar_factor:g}",
            self.illumination.kind,
        ])


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def _turret_from(d: dict) -> Turret:
    positions = [Objective(**o) if o else None for o in d.get("positions", [])]
    capped = list(d.get("capped", []))[:len(positions)]
    return Turret(
        positions=positions,
        current=d.get("current", 0),
        # Absent from every library written before cap state existed, so it
        # is padded rather than required: an old file means "nothing known
        # to be capped", not a load failure.
        capped=capped + [False] * (len(positions) - len(capped)),
    )


class Library:
    """Known cameras and scopes, on disk. Small enough for one JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "library.json")
        self.cameras: dict[str, CameraProfile] = {}
        self.scopes: dict[str, ScopeProfile] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        self.cameras = {k: CameraProfile(**v) for k, v in raw.get("cameras", {}).items()}
        self.scopes = {}
        for k, v in raw.get("scopes", {}).items():
            v = dict(v)
            v["turret"] = _turret_from(v.get("turret", {}))
            self.scopes[k] = ScopeProfile(**v)

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "cameras": {k: asdict(v) for k, v in self.cameras.items()},
            "scopes": {k: asdict(v) for k, v in self.scopes.items()},
        }, indent=2) + "\n")

    def scope_or_default(self, scope_id: str | None) -> "ScopeProfile | None":
        """The named scope, else the only one, else nothing.

        One camera on one stand should never see a picker; the case that needs
        choosing is a camera that travels.
        """
        if scope_id and scope_id in self.scopes:
            return self.scopes[scope_id]
        if len(self.scopes) == 1:
            return next(iter(self.scopes.values()))
        return None

    def add_scope(self, name: str) -> "ScopeProfile":
        scope = ScopeProfile(id=scope_id(name, set(self.scopes)), name=name)
        self.scopes[scope.id] = scope
        self.save()
        return scope

    def remove_scope(self, scope_id: str) -> None:
        self.scopes.pop(scope_id, None)
        self.save()

    def bind(self, serial: str, scope_id: str) -> None:
        """Remember which stand this camera was last bolted to."""
        if serial in self.cameras:
            self.cameras[serial] = replace(self.cameras[serial],
                                           last_scope=scope_id)
            self.save()

    def file_camera(self, profile: CameraProfile) -> None:
        """Record a camera's settings, if it is a camera worth recording.

        The one way in. `remember_camera` refused the synthetic camera and
        callers then wrote `library.cameras[serial] = profile` straight
        past it -- so stepping an objective under --mock filed MockCam
        anyway, which is how it came to be in a real library. A rule that
        lives in one method and is enforced in another is not a rule.
        """
        if not profile.serial or is_synthetic(profile.serial):
            return
        self.cameras[profile.serial] = profile

    def remove_camera(self, serial: str) -> None:
        """Forget a camera. For the webcam that is not a microscope camera.

        Every device that appears gets filed, because the alternative is
        asking, and a stub the user can correct beats a question they have
        to answer before they can work. Filing everything means being able
        to unfile as well.
        """
        self.cameras.pop(serial, None)
        self.save()

    def remember_camera(self, serial: str, model: str, make: str = "",
                        fingerprint: str = "") -> CameraProfile:
        """First sight of a camera gets a provisional name the user can change."""
        if is_synthetic(serial):
            # The synthetic camera is a test fixture, not a device somebody
            # owns. Filing it put "MockCam (synthetic)" in the library and
            # then offered it as the camera to describe when nothing real
            # was plugged in.
            return CameraProfile(serial=serial, name=model or "Camera",
                                 model=model, make=make)
        if serial not in self.cameras:
            self.cameras[serial] = CameraProfile(
                serial=serial, name=model or "Camera", model=model,
                make=make, fingerprint=fingerprint)
            self.save()
        else:
            known = self.cameras[serial]
            # A library written before fingerprints existed has none, so
            # requiring both to be present let exactly the case this
            # guards against through: the old entry adopted the new
            # camera's fingerprint and reported itself satisfied for
            # ever after. Where there is nothing to compare, the model
            # string is what a legacy entry does have.
            differs = (fingerprint != known.fingerprint
                       if fingerprint and known.fingerprint
                       else bool(model and known.model and model != known.model))
            if differs:
                # Same socket, different camera. Everything filed against
                # this key belongs to the previous occupant -- including
                # its measured geometry and response, which would then be
                # applied to a camera they were never measured on. Start
                # again rather than inherit.
                self.cameras[serial] = CameraProfile(
                    serial=serial, name=model or "Camera", model=model,
                    make=make, fingerprint=fingerprint)
                self.save()
            else:
                changed = {}
                if make and not known.make:
                    changed["make"] = make
                if fingerprint and not known.fingerprint:
                    changed["fingerprint"] = fingerprint
                if model and known.model != model:
                    changed["model"] = model
                if changed:
                    self.cameras[serial] = replace(known, **changed)
                    self.save()
        return self.cameras[serial]

    def rename_camera(self, serial: str, name: str) -> None:
        if serial in self.cameras:
            self.cameras[serial] = replace(self.cameras[serial], name=name)
            self.save()
