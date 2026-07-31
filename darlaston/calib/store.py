"""Calibration products, keyed by how long each stays valid.

The combinatorics only look unmanageable if the three products are treated as
one thing. They are not:

    dark            exposure x gain x sensor temperature   -- reusable across
                                                              all optics
    white balance   illumination (the lamp)                -- barely depends on
                                                              the objective
    preview LUT     illumination (the ISP's own gains)     -- same
    flat            objective x relay x illumination
                    x the actual slide                     -- the only
                                                              genuinely
                                                              combinatorial one

Five objectives times three illuminations is fifteen flats *in principle*. In
practice one subject is shot with one objective under one illumination, so one
is needed -- and §3 of DESIGN.md removes even that from most sessions.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..session.model import config_dir


def calib_dir() -> Path:
    d = config_dir() / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Provenance:
    """What a calibration product was made from. Recorded because a product
    whose origin has to be inferred is one that will eventually be wrong."""

    kind: str
    key: str
    frames: int = 1
    created: float = field(default_factory=time.time)
    exposure_us: int = 0
    gain_pct: int = 0
    notes: str = ""

    @property
    def age_hours(self) -> float:
        return (time.time() - self.created) / 3600.0


@dataclass
class Product:
    provenance: Provenance
    data: np.ndarray | None = None
    values: dict = field(default_factory=dict)


def dark_key(exposure_us: int, gain_pct: int, temp_c: float | None = None) -> str:
    """Darks are keyed on what actually changes them.

    Exposure and gain obviously. Temperature matters because this sensor has no
    TEC, so the offset drifts with the room -- but it is quantised coarsely,
    since a dark is not worth re-shooting for a tenth of a degree.
    """
    t = f"_t{round(temp_c / 2) * 2:g}" if temp_c is not None else ""
    return f"e{int(exposure_us)}_g{int(gain_pct)}{t}"


def flat_key(setup, slide: str = "") -> str:
    """Flats depend on the whole optical stack *and the actual glass*.

    In phase contrast the condenser annulus and objective phase ring are
    conjugate through the specimen plane, so slide thickness, coverslip and
    mountant all participate. A flat shot with no slide at all had a different
    illumination gradient from the subject frame -- measured, not assumed.
    """
    base = setup.calibration_key()
    return f"{base}|slide:{slide}" if slide else base


def illumination_key(setup) -> str:
    """White balance and the preview LUT follow the lamp, not the objective."""
    return f"{setup.camera.serial}|{setup.illumination.kind}"


class CalibrationStore:
    """Products on disk, one directory per kind."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or calib_dir()

    # ---- paths -----------------------------------------------------------

    def _slot(self, kind: str, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        d = self.root / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / safe

    # ---- generic -------------------------------------------------------

    def put(self, kind: str, key: str, provenance: Provenance,
            data: np.ndarray | None = None, values: dict | None = None) -> None:
        slot = self._slot(kind, key)
        meta = {"provenance": asdict(provenance), "values": values or {}}
        if data is not None:
            np.save(slot.with_suffix(".npy"), data)
        slot.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")

    def get(self, kind: str, key: str, *, max_age_hours: float | None = None
            ) -> Product | None:
        slot = self._slot(kind, key)
        meta_path = slot.with_suffix(".json")
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            prov = Provenance(**meta["provenance"])
        except (ValueError, TypeError, KeyError):
            return None
        if max_age_hours is not None and prov.age_hours > max_age_hours:
            return None
        data = None
        npy = slot.with_suffix(".npy")
        if npy.exists():
            data = np.load(npy)
        return Product(provenance=prov, data=data, values=meta.get("values", {}))

    def have(self, kind: str, key: str, *, max_age_hours: float | None = None
             ) -> bool:
        return self.get(kind, key, max_age_hours=max_age_hours) is not None

    def forget(self, kind: str, key: str) -> None:
        slot = self._slot(kind, key)
        for suffix in (".npy", ".json"):
            slot.with_suffix(suffix).unlink(missing_ok=True)

    # ---- what is missing -------------------------------------------------

    def status(self, setup, exposure_us: int, gain_pct: int,
               slide: str = "") -> dict[str, bool]:
        """What exists for the current configuration.

        Drives a nag, never a gate. Sometimes the light is right and the diatom
        is beautiful and calibration can wait.
        """
        return {
            # A dark ages because the sensor has no TEC and the room drifts.
            "dark": self.have("dark", dark_key(exposure_us, gain_pct),
                              max_age_hours=8),
            "flat": self.have("flat", flat_key(setup, slide)),
            "white_balance": self.have("wb", illumination_key(setup)),
            "preview_lut": self.have("lut", illumination_key(setup)),
        }
