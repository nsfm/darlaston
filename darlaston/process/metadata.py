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

import unicodedata
from dataclasses import dataclass


def ascii_safe(text: str) -> str:
    """EXIF ASCII tags are ASCII, and writers refuse anything else.

    Our natural strings are full of · and ×, so transliterate the ones that
    have obvious equivalents and strip the rest rather than letting a capture
    fail over a multiplication sign.
    """
    if not text:
        return text
    swap = {"×": "x", "·": "-", "--": "-", "–": "-", "’": "'",
            "“": '"', "”": '"', "µ": "u", "°": "deg"}
    for a, b in swap.items():
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


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
    #: The objective's own focal length, tube length / magnification. A real
    #: physical property of the lens, and the field a raw editor shows most
    #: prominently after exposure. Any "35mm equivalent" an editor computes
    #: from it will be nonsense, but that is the editor inferring, not us
    #: lying: a 40x objective on a 160 mm stand really is a 4 mm lens.
    focal_length_mm: float | None = None
    #: Image-side working f-number, M / (2·NA).
    #:
    #: This is the one that earns its place. The light cone reaching the
    #: sensor has NA_image = NA_objective / M, so a 40x/0.75 delivers f/26.7
    #: -- and that number, not the objective's NA, is what sets the
    #: diffraction limit at the pixel. Seeing f/27 in a raw editor is the
    #: honest explanation for why more magnification stops adding detail.
    f_number: float | None = None
    #: Sensor pixels per millimetre, for EXIF FocalPlaneX/YResolution.
    #:
    #: The most useful number in the file and the one we had been throwing
    #: away: the SDK reports the pitch, and pitch over magnification is the
    #: object-space scale. At 40x with 2.40 um pixels a capture samples the
    #: slide every 0.060 um, and without this nothing in the file says how
    #: big anything is.
    focal_plane_per_mm: float | None = None
    #: Free working distance in metres, for EXIF SubjectDistance. Literally
    #: what the tag means: how far the front element is from the subject.
    subject_distance_m: float | None = None
    lens_make: str = ""
    lens_serial: str = ""
    #: Capture sequence within its folder, for EXIF ImageNumber.
    image_number: int | None = None
    #: Fractional part of the capture time. A timelapse at one frame a
    #: second orders wrongly without it.
    subsec: str = ""
    #: EXIF LightSource. Every mode on this stand is the same halogen lamp,
    #: so this is Tungsten and not a guess.
    light_source: int | None = None
    #: A stable identity for the pixels, so a file can be recognised after
    #: being renamed, and two copies can be told apart from two captures.
    unique_id: str = ""
    artist: str = ""
    copyright: str = ""
    description: str = ""             # human sentence
    comment: str = ""                 # structured key=value, machine-readable
    software: str = ""

    def __post_init__(self) -> None:
        for f in ("make", "model", "unique_camera_model", "serial",
                  "lens_model", "description", "comment", "software",
                  "lens_make", "lens_serial", "artist", "copyright",
                  "unique_id", "subsec"):
            object.__setattr__(self, f, ascii_safe(getattr(self, f)))

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


#: EXIF LightSource: 3 is Tungsten. Every illumination mode on a classical
#: stand is the same halogen lamp behind different optics -- darkfield and
#: phase change the path, not the emitter -- so this is a fact rather than a
#: default. LED and fluorescence stands would need their own values, which is
#: why it hangs off the illumination mode rather than being hard-coded here.
_LIGHT_SOURCE_TUNGSTEN = 3


def from_setup(setup, *, exposure_us: int, gain_pct: int,
               subject: str = "", slide: str = "", calibration: str = "",
               app_version: str = "", pixel_um: float | None = None,
               sequence: int | None = None, when=None,
               artist: str = "", copyright: str = "",
               unique_id: str = "") -> CaptureMetadata:
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
    # The subject leads, since it is what a person searching their archive
    # actually remembers. A slide may carry several.
    description = " · ".join(([subject] if subject else []) + bits)

    fields = {
        "scope": scope.name,
        "objective": obj.label if obj else "",
        "optovar": f"{scope.optovar_factor:g}",
        "illumination": setup.illumination.key,
        "inverted": "1" if setup.illumination.inverted else "0",
        "relay": cam.relay,
        "total_magnification": f"{total:g}" if total else "",
        # The number a scale bar is drawn from: how much *slide* one
        # pixel covers. Sensor pitch over total magnification. Written
        # explicitly because it cannot be recovered from EXIF alone --
        # FocalPlaneXResolution is a scale at the sensor, and dividing it
        # by a magnification nobody recorded is how wrong scale bars get
        # published.
        "um_per_px": (f"{pixel_um / total:.4g}"
                      if pixel_um and total else ""),
        "subject": subject,
        "slide": slide,
        "calibration": calibration,
    }
    comment = " ".join(f"{k}={v}" for k, v in fields.items() if v)

    # Optics, in the fields a photographer reads. Both are derived rather
    # than assumed: focal length needs the stand's tube length, f-number
    # needs the objective's NA, and either being absent means we write
    # nothing rather than a plausible guess.
    focal = None
    fnum = None
    if obj and obj.magnification:
        focal = scope.tube_length_mm / obj.magnification
        if obj.na and total:
            # NA at the image is NA_obj / M_total, and f-number is the
            # reciprocal of twice that.
            fnum = total / (2.0 * obj.na)

    # Sensor scale. EXIF wants pixels per unit rather than a pitch, and the
    # unit that keeps the number readable for a 2.40 um pixel is the
    # millimetre: 416.67 px/mm.
    per_mm = (1000.0 / pixel_um) if pixel_um else None

    subsec = ""
    if when is not None:
        subsec = f"{when.microsecond // 10000:02d}"

    return CaptureMetadata(
        model=cam.model or cam.name,
        unique_camera_model=cam.model,
        serial=cam.serial,
        # The objective, in the field a photographer reads as "lens".
        lens_model=obj.label if obj else "",
        lens_make=obj.maker if obj else "",
        lens_serial=obj.serial if obj else "",
        subject_distance_m=((obj.working_distance_mm / 1000.0)
                            if obj and obj.working_distance_mm else None),
        focal_plane_per_mm=per_mm,
        image_number=sequence,
        subsec=subsec,
        light_source=_LIGHT_SOURCE_TUNGSTEN,
        unique_id=unique_id,
        artist=artist,
        copyright=copyright,
        exposure_seconds=exposure_us / 1e6,
        # Gain is a multiplier on an already-collected signal, which is exactly
        # what ISO describes. 100% gain reads as ISO 100.
        iso=int(round(gain_pct)),
        focal_length_mm=focal,
        f_number=fnum,
        description=description,
        comment=comment,
        software=f"darlaston {app_version}".strip(),
    )
