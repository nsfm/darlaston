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
    relay: str = ""              # travels with the camera, not the scope
    last_scope: str | None = None

    @property
    def display(self) -> str:
        return self.name or self.model or self.serial[:12]


@dataclass
class ScopeProfile:
    id: str
    name: str = "Microscope"
    turret: Turret = field(default_factory=Turret)
    optovar: list[float] = field(default_factory=list)
    optovar_current: int = 0
    condenser: str = ""

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
        obj = self.scope.turret.objective
        return obj.magnification * self.scope.optovar_factor if obj else None

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
    return Turret(
        positions=[Objective(**o) if o else None for o in d.get("positions", [])],
        current=d.get("current", 0),
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

    def remember_camera(self, serial: str, model: str) -> CameraProfile:
        """First sight of a camera gets a provisional name the user can change."""
        if serial not in self.cameras:
            self.cameras[serial] = CameraProfile(
                serial=serial, name=model or "Camera", model=model)
            self.save()
        return self.cameras[serial]

    def rename_camera(self, serial: str, name: str) -> None:
        if serial in self.cameras:
            self.cameras[serial] = replace(self.cameras[serial], name=name)
            self.save()
