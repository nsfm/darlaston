"""Mapping a microscope setup onto the metadata a photographer expects.

The good mapping, and it is not a stretch: **the objective is the lens.**
Someone opening these files in Darktable or Lightroom should find the objective
where a lens belongs, the gain where ISO belongs, and the exposure where
exposure belongs. Everything with no photographic equivalent -- the stand, the
illumination, the Optovar, the calibration state -- goes into the description
and a structured comment rather than being forced into a field that means
something else.

Recording this at capture is the difference between a folder of images and an
archive. Six months later, "which objective was this?" has an answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureMetadata:
    """Everything worth writing into the file."""

    make: str = "ToupTek"
    model: str = ""
    unique_camera_model: str = ""
    serial: str = ""
    lens_model: str = ""              # the objective
    exposure_seconds: float | None = None
    iso: int | None = None            # analogue gain, expressed photographically
    description: str = ""             # human sentence
    comment: str = ""                 # structured key=value, machine-readable
    software: str = ""

    def as_exif(self) -> dict:
        """Tag names as ExifTool and most DNG writers know them."""
        out = {
            "Make": self.make,
            "Model": self.model,
            "UniqueCameraModel": self.unique_camera_model or self.model,
            "Software": self.software,
            "ImageDescription": self.description,
            "UserComment": self.comment,
        }
        if self.serial:
            out["CameraSerialNumber"] = self.serial
        if self.lens_model:
            out["LensModel"] = self.lens_model
        if self.exposure_seconds is not None:
            out["ExposureTime"] = self.exposure_seconds
        if self.iso is not None:
            out["ISOSpeedRatings"] = self.iso
        return {k: v for k, v in out.items() if v not in ("", None)}


def from_setup(setup, *, exposure_us: int, gain_pct: int,
               slide: str = "", calibration: str = "",
               app_version: str = "") -> CaptureMetadata:
    """Build metadata from the live setup."""
    cam, scope = setup.camera, setup.scope
    obj = scope.turret.objective
    total = setup.total_magnification

    bits = [f"{scope.name}"]
    if obj:
        bits.append(obj.label)
    if scope.optovar and scope.optovar_factor != 1.0:
        bits.append(f"Optovar {scope.optovar_factor:g}×")
    bits.append(setup.illumination.display)
    if total:
        bits.append(f"{total:g}× total")
    description = " · ".join(bits)

    fields = {
        "scope": scope.name,
        "objective": obj.label if obj else "",
        "optovar": f"{scope.optovar_factor:g}",
        "illumination": setup.illumination.key,
        "inverted": "1" if setup.illumination.inverted else "0",
        "relay": cam.relay,
        "total_magnification": f"{total:g}" if total else "",
        "slide": slide,
        "calibration": calibration,
    }
    comment = " ".join(f"{k}={v}" for k, v in fields.items() if v)

    return CaptureMetadata(
        model=cam.model or cam.name,
        unique_camera_model=cam.model,
        serial=cam.serial,
        # The objective, in the field a photographer reads as "lens".
        lens_model=obj.label if obj else "",
        exposure_seconds=exposure_us / 1e6,
        # Gain is a multiplier on an already-collected signal, which is exactly
        # what ISO describes. 100% gain reads as ISO 100.
        iso=int(round(gain_pct)),
        description=description,
        comment=comment,
        software=f"photomicrography {app_version}".strip(),
    )
