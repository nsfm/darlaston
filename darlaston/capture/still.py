"""Taking one photograph.

Runs off the UI thread: a full-resolution pull stops the preview, reconfigures
the camera, grabs, restores, and then writes forty megabytes. None of that
belongs in an event loop.

The capture path never drops. A live frame lost is invisible; a capture lost is
gone -- see ARCHITECTURE.md 3.1.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from ..calib import frames as F
from ..calib.store import CalibrationStore, dark_key, flat_key, illumination_key
from ..process import dng
from ..process.metadata import from_setup
from ..session.settings import Settings, next_sequence


@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    path: Path | None = None
    message: str = ""
    elapsed: float = 0.0
    width: int = 0
    height: int = 0
    clipped_fraction: float = 0.0
    #: Which calibration products were actually applied. Recorded and reported,
    #: because a file whose provenance has to be inferred is one that will
    #: eventually be trusted wrongly.
    applied: tuple[str, ...] = ()
    #: Did the view move during the exposure? None when the guard could not
    #: measure (no tracker lock -- a blank field, say). The file is written
    #: regardless: data on disk until someone explicitly discards it beats a
    #: guard that throws away exposures on its own judgement.
    moved: bool | None = None
    #: Measured displacement in preview pixels. math.inf when the view changed
    #: by more than phase correlation can measure.
    moved_px: float | None = None
    #: Tracker position at the moment of capture, for mosaic tiles. Preview
    #: pixels, tracker frame; None when the tracker never locked.
    position: tuple[float, float] | None = None

    @property
    def summary(self) -> str:
        if not self.ok:
            return self.message
        mp = self.width * self.height / 1e6
        bits = [self.path.name, f"{mp:.1f} MP", f"{self.elapsed:.1f}s"]
        bits.append("+".join(self.applied) if self.applied else "uncalibrated")
        if self.clipped_fraction > 0.0005:
            bits.append(f"{self.clipped_fraction * 100:.1f}% clipped")
        if self.moved:
            bits.append("moved during exposure")
        return "   ".join(bits)


class StillCapture:
    """One capture at a time, on its own thread.

    Refusing to start a second while one is running is deliberate: the camera
    cannot serve two full-resolution pulls, and a queue of shutter presses is
    never what anybody meant.
    """

    #: Displacement across the exposure beyond which the frame is presumed
    #: smeared. Hand tremor on a manual stage measures under two pixels
    #: per frame; a deliberate crank is tens. Preview pixels.
    HOLD_STILL_PX = 8.0

    def __init__(self, session, settings: Settings,
                 on_state: Callable[[str], None] | None = None,
                 on_result: Callable[[CaptureResult], None] | None = None,
                 store: CalibrationStore | None = None,
                 pipeline=None) -> None:
        self._session = session
        self._settings = settings
        self._store = store or CalibrationStore()
        self._on_state = on_state or (lambda _s: None)
        self._on_result = on_result or (lambda _r: None)
        self._pipeline = pipeline
        self._busy = threading.Lock()
        #: Write a measured AsShotNeutral into the file. On brightfield and
        #: phase this is what makes a capture open looking like what was on
        #: the screen. Turn it off when the field is not neutral by nature --
        #: polarised-light interference colours, fluorescence, stained
        #: sections -- where any estimate is a guess dressed as a measurement.
        self.white_balance = True
        #: Write single frames as packed 12-bit. The sensor is 12-bit, so
        #: this is lossless and saves a quarter of every file: measured
        #: 39.9 MB down to 30.0 MB on a real 20 MP frame. Off writes 16-bit,
        #: which is what every previous capture used.
        self.pack_12bit = True

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def trigger(self, setup=None, subject: str = "",
                slide: str = "", frames: int = 1) -> bool:
        """Start a capture. Returns False if one is already running.

        `frames` above one averages a burst into a single file -- the hero
        shot. Noise falls as the square root: sixteen frames buy two stops of
        SNR, which is how this sensor's small pixels beat a camera whose
        single frame is cleaner.
        """
        if not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, args=(setup, subject, slide, frames),
                         daemon=True, name="capture").start()
        return True

    # ---- the work --------------------------------------------------------

    def _run(self, setup, subject: str, slide: str = "",
             frames: int = 1) -> None:
        started = time.perf_counter()
        frames = max(1, int(frames))
        try:
            backend = self._session.backend
            if backend is None:
                raise RuntimeError("No camera connected.")

            # The hold-still guard: position before the pull, measured again
            # across the preview gap once the stream resumes. Only armed when
            # the tracker is locked on the scene -- on a blank field there is
            # nothing to measure against and the guard stays quiet. For a
            # burst it spans the whole run, because motion between frames
            # ghosts the average just as surely as motion during one.
            guard = (self._pipeline.guard_begin()
                     if self._pipeline is not None else None)
            position = (self._pipeline.stage_position()
                        if self._pipeline is not None else None)

            acc: np.ndarray | None = None
            exposure_us = gain_pct = 0
            for i in range(frames):
                self._on_state("exposing" if frames == 1
                               else f"exposing {i + 1}/{frames}")
                frame = backend.grab_raw()
                with frame:
                    if acc is None:
                        # float32 holds sums of 12-bit frames exactly to
                        # N=4096; drift starts about nine stops past caring.
                        acc = frame.data.astype(np.float32)
                        exposure_us = frame.exposure_us
                        gain_pct = frame.gain_pct
                    else:
                        acc += frame.data
            raw = acc / frames if frames > 1 else acc.astype(np.uint16)

            moved: bool | None = None
            moved_px: float | None = None
            if guard is not None:
                moved_px = self._pipeline.guard_measure(guard)
                if moved_px is not None:
                    moved = moved_px > self.HOLD_STILL_PX

            self._on_state("calibrating")
            corrected, applied, neutral, black = self._apply_calibration(
                raw, setup, exposure_us, gain_pct, slide)
            if frames > 1:
                applied.append(f"avg{frames}")

            self._on_state("writing")
            path, sequence, when = self._destination(setup, subject)
            info = backend.info
            # A monochrome sensor gets no CFA pattern at all. Roughly a
            # quarter of ToupTek's microscopy range is mono, and labelling
            # greyscale data with a Bayer pattern makes every developer
            # demosaic noise into colour.
            mono = info is not None and not info.is_colour
            pattern = None if mono else (info.bayer_pattern if info
                                         else "GBRG")
            # The sensor's own depth, not a constant. ToupTek's range runs
            # 8, 10, 12, 14 and 16 bits across one identical API -- of 244
            # microscopy models, 68 are 8-bit and 26 are 14/16-bit -- so a
            # hardcoded 4095 mislabels an 8-bit sensor as under-exposed by
            # four stops and clips a 16-bit one to a sixteenth of its
            # range. The camera has always reported this; nothing read it.
            depth = (info.max_bit_depth if info and info.max_bit_depth
                     else 12)
            sensor_white = (1 << depth) - 1

            meta = None
            if not self.white_balance:
                # Neutral means "do not touch the channels": the raw arrives
                # in a developer exactly as the sensor recorded it. Grey-world
                # assumes the scene averages to grey, which a polarised-light
                # interference field or a stained section flatly is not -- and
                # a wrong AsShotNeutral is not a suggestion, it is a colour
                # cast baked into how the file opens.
                neutral = (1.0, 1.0, 1.0)
                applied = [a for a in applied if a != "wb"]

            if setup is not None:
                # The sensor pitch comes from the SDK and is the file's only
                # link to real-world scale: pitch over magnification is how
                # much slide one pixel covers.
                # The profile wins over the SDK: get_PixelSize returns 0
                # on some models, and a zero pitch silently drops the one
                # tag a scale bar can be computed from.
                pixel_um = setup.camera.pixel_um or None
                if not pixel_um and info and info.resolutions:
                    full = min(info.resolutions, key=lambda r: r.index)
                    pixel_um = full.pixel_um or None
                meta = from_setup(setup, exposure_us=exposure_us,
                                  gain_pct=gain_pct, subject=subject,
                                  slide=slide,
                                  calibration="+".join(applied) or "none",
                                  app_version=_version(),
                                  pixel_um=pixel_um, sequence=sequence,
                                  when=when,
                                  artist=self._settings.artist,
                                  copyright=self._settings.copyright,
                                  unique_id=_fingerprint(corrected))

            if frames > 1:
                # The mean carries real precision below one 12-bit LSB.
                # Scaled into 16 bits, the file keeps the SNR the burst just
                # paid for; rounded back to 12, it would be quantised away --
                # so an averaged frame is never packed.
                # Scale into the 16-bit container by whatever headroom the
                # sensor's depth leaves, so the averaged file uses the range
                # it declares rather than a fixed x16.
                gain = (1 << (16 - depth)) if depth < 16 else 1
                white = sensor_white * gain
                out = np.clip(corrected * float(gain) + 0.5, 0, white) \
                        .astype(np.uint16)
                bits = 16
            else:
                white = sensor_white
                out = corrected
                # A single frame is the sensor's own data and nothing about
                # widening it adds information: packing is lossless here and
                # saves a quarter of every file. Verified readable by
                # darktable 5.4.1. Only 12-bit data has a packed form we
                # write, so anything else goes out at 16.
                bits = 12 if (self.pack_12bit and depth == 12) else 16

            # The thumbnail gets a balance even when the file does not: with
            # white balance turned off, or before a flat exists, the measured
            # neutral is absent -- and a raw microscope field rendered flat
            # is a green rectangle. The estimate is for the preview only and
            # changes nothing about the data or the tags.
            # A camera that already demosaiced -- any ordinary UVC
            # device -- gives three channels and has no CFA to declare.
            # It gets a linear DNG, which says exactly that, rather than
            # a file claiming a Bayer pattern it does not have.
            decoded = out.ndim == 3
            preview = dng.make_preview(
                out, bayer=not (mono or decoded), black=black, white=white,
                neutral=None if (mono or decoded)
                else (neutral or dng.grey_world_neutral(out)))
            if decoded:
                written = dng.write_linear_streamed(
                    path, lambda s, c: out[s:s + c],
                    out.shape[0], out.shape[1], preview=preview,
                    black=black, white=white,
                    neutral=neutral or (1.0, 1.0, 1.0), meta=meta)
            else:
                written = dng.write_bayer_streamed(
                    path, lambda s, c: out[s:s + c],
                    out.shape[0], out.shape[1],
                    preview=preview, pattern=pattern, black=black,
                    white=white, neutral=neutral or (1.0, 1.0, 1.0),
                    meta=meta, bits=bits)

            clipped = float((raw >= sensor_white).sum()) / raw.size
            self._on_state("idle")
            self._on_result(CaptureResult(
                ok=True, path=written, elapsed=time.perf_counter() - started,
                width=raw.shape[1], height=raw.shape[0],
                clipped_fraction=clipped, applied=tuple(applied),
                moved=moved, moved_px=moved_px, position=position))
        except Exception as exc:
            self._on_state("idle")
            self._on_result(CaptureResult(
                ok=False, message=_explain(exc),
                elapsed=time.perf_counter() - started))
        finally:
            self._busy.release()

    def _apply_calibration(self, raw, setup, exposure_us: int, gain_pct: int,
                           slide: str):
        """Correct the frame with whatever the store has for this configuration.

        Deliberately partial: a dark with no flat is still worth having, and
        refusing to capture because one product is missing would make the gate
        a wall. What was applied is recorded rather than assumed.

        Black level is written as zero once a dark has been subtracted, because
        it has been -- leaving the original offset in the tag would make a
        developer subtract it twice.
        """
        applied: list[str] = []
        dark = flat = defects = None
        neutral = None
        black = 0

        got = self._store.get("dark", dark_key(exposure_us, gain_pct),
                              max_age_hours=8)
        if got is not None and got.data is not None:
            dark = got.data
            applied.append("dark")
        else:
            black = 0

        if setup is not None:
            got = self._store.get("flat", flat_key(setup, slide))
            if got is not None and got.data is not None:
                flat = got.data
                applied.append("flat")

            got = self._store.get("defects", dark_key(exposure_us, gain_pct))
            if got is not None and got.data is not None and got.data.size:
                defects = got.data
                applied.append(f"defects({len(defects)})")

            got = self._store.get("wb", illumination_key(setup))
            if got is not None and got.values.get("gains"):
                gr, _gg, gb = got.values["gains"]
                # DNG wants the reciprocal of the neutralising gains.
                neutral = (1.0 / max(gr, 1e-6), 1.0, 1.0 / max(gb, 1e-6))
                applied.append("wb")

        if dark is None and flat is None and defects is None:
            return raw, applied, neutral, black

        corrected = F.calibrate(raw, dark=dark, flat=flat, defects=defects,
                                white_level=dng.WHITE_LEVEL)
        return corrected.astype(raw.dtype), applied, neutral, black

    def _destination(self, setup, subject: str) -> tuple[Path, int, object]:
        """The path, its sequence number and the moment -- all three, because
        the file's own metadata should say which capture it is and when."""
        when = datetime.now()
        # Sequence comes from what is on disk, so it survives a crash, a manual
        # file move, and two copies of the app running at once.
        folder = self._settings.resolve(setup=setup, seq=1, subject=subject,
                                        when=when).parent
        seq = next_sequence(folder, self._settings.filename_pattern)
        return (self._settings.resolve(setup=setup, seq=seq, subject=subject,
                                       when=when), seq, when)


def _fingerprint(raw) -> str:
    """A stable identity for the pixels.

    Hashes a strided sample rather than forty megabytes: two captures of the
    same field differ in noise everywhere, so a sample is as decisive as the
    whole and does not cost a second per file. This tells a renamed copy from
    a genuinely different frame, which is what EXIF ImageUniqueID is for.
    """
    import hashlib
    a = raw[::16, ::16]
    h = hashlib.md5(np.ascontiguousarray(a).tobytes())
    h.update(str(raw.shape).encode())
    return h.hexdigest()


def _version() -> str:
    from .. import __version__
    return __version__


def _explain(exc: Exception) -> str:
    """Say what to do, not just what broke.

    SDK failures are decoded from their numeric code rather than matched on
    their text: the vendor's Linux exception carries no message at all, and
    what `str()` produces is the *signed* form of a code the SDK documents in
    hex — so string matching could never have recognised one. That is how a
    real capture failure once reached the operator as "-2147417825".
    """
    from ..camera.errors import hresult_of
    if hresult_of(exc) is not None:
        from ..camera.errors import explain as explain_sdk
        return explain_sdk(exc)
    text = str(exc).lower()
    if "no camera" in text:
        return "No camera connected."
    if "space" in text or "no space" in text:
        return "The disk is full. A full-resolution frame needs about 40 MB."
    if "permission" in text:
        return "Cannot write there. Check the capture folder in Settings."
    return str(exc) or exc.__class__.__name__
