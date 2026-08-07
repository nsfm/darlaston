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

import logging

import numpy as np

from ..calib import frames as F
from ..live import balance
from ..calib.store import CalibrationStore, dark_key, flat_key, illumination_key
from ..process import develop, dng, scalebar
from .writer import WriteQueue
from ..process.metadata import from_setup, sensor_pitch
from ..session.settings import Settings, next_sequence

_log = logging.getLogger(__name__)


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
                 pipeline=None, writer: WriteQueue | None = None,
                 on_exposed: Callable[[], None] | None = None) -> None:
        self._session = session
        self._settings = settings
        self._store = store or CalibrationStore()
        self._on_state = on_state or (lambda _s: None)
        self._on_result = on_result or (lambda _r: None)
        #: The exposure is over and the frame is in memory. Distinct from
        #: `on_result`, which now waits for the queue, and the distinction
        #: is the point: whoever was told to hold still is released here,
        #: and whoever needs the file has to wait for the file.
        self._on_exposed = on_exposed or (lambda: None)
        self._pipeline = pipeline
        self._busy = threading.Lock()
        #: Post-shutter work, off this thread. Shared rather than made
        #: here when the window passes one in, so a mosaic tile and a
        #: stack slice queue behind each other rather than racing.
        self._writer = writer if writer is not None else WriteQueue()
        #: Highest sequence handed out per folder, whether or not the file
        #: exists yet. `next_sequence` reads the directory, which was
        #: sound while the write finished before the next capture began
        #: and became a filename collision the moment it did not: two
        #: slices in flight both saw a folder without either of them in
        #: it. Disk still wins on restart -- this is only a floor for
        #: what is in the air right now.
        self._reserved: dict[Path, int] = {}
        self._reserve_lock = threading.Lock()
        #: What the last frame cost, for asking the queue about the next
        #: one before the exposure rather than after.
        self._last_frame_bytes = 0
        #: Write a measured AsShotNeutral into the file. On brightfield and
        #: phase this is what makes a capture open looking like what was on
        #: the screen. Turn it off when the field is not neutral by nature --
        #: polarised-light interference colours, fluorescence, stained
        #: sections -- where any estimate is a guess dressed as a measurement.
        self.white_balance = True
        #: Where the next capture sits in a larger piece of work: which
        #: stack, which slice of it, which tile and where that tile is.
        #: Set by the window before it pulls the trigger, because only the
        #: window knows what is going on around a capture.
        self.context: dict = {}
        #: Set while a mosaic or stack session is open. Those captures are
        #: ingredients rather than photographs -- the stitcher and the merge
        #: read the raws back -- so a "JPEG only" preference must not apply
        #: to them, however the operator has it set.
        self.raw_required = False
        #: Write single frames as packed 12-bit. The sensor is 12-bit, so
        #: this is lossless and saves a quarter of every file: measured
        #: 39.9 MB down to 30.0 MB on a real 20 MP frame. Off writes 16-bit,
        #: which is what every previous capture used.
        self.pack_12bit = True

    @property
    def busy(self) -> bool:
        """The camera. One full-resolution pull at a time, as ever."""
        return self._busy.locked()

    @property
    def writing(self) -> int:
        """Captures exposed but not yet on disk."""
        return self._writer.depth

    @property
    def catching_up(self) -> bool:
        """Past the queue's watermark: the next shutter press may be
        refused, and the operator should hear that from the status strip
        rather than from a button that does nothing."""
        return self._writer.under_pressure

    def progress(self) -> tuple[int, float]:
        """(writes outstanding, how far through the running one). For the
        gauge under the stack window."""
        return self._writer.progress()

    def drain(self, timeout: float = 30.0) -> bool:
        """Wait for every exposed frame to reach disk. Before closing."""
        return self._writer.drain(timeout)

    def trigger(self, setup=None, subject: str = "",
                slide: str = "", frames: int = 1) -> bool:
        """Start a capture. Returns False if one is already running.

        `frames` above one averages a burst into a single file -- the hero
        shot. Noise falls as the square root: sixteen frames buy two stops of
        SNR, which is how this sensor's small pixels beat a camera whose
        single frame is cleaner.
        """
        # Backpressure reaches the shutter, not the disk. Refusing to
        # start is a pause; starting and dropping the result is a lost
        # photograph, and those are not comparable.
        if self._last_frame_bytes and not self._writer.room_for(
                self._last_frame_bytes * 3):
            return False
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
                # A state, not a sentence. The window turns it into words
                # -- this layer has no business writing English, and the
                # string it used to build was assembled past the
                # catalogue and so was never translated and never checked.
                self._on_state("exposing" if frames == 1
                               else f"exposing:{i + 1}:{frames}")
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

            info = backend.info
            # The sensor's own depth, not a constant. ToupTek's range runs
            # 8, 10, 12, 14 and 16 bits across one identical API -- of 244
            # microscopy models, 68 are 8-bit and 26 are 14/16-bit -- so a
            # hardcoded 4095 mislabels an 8-bit sensor as under-exposed by
            # four stops and clips a 16-bit one to a sixteenth of its
            # range. The camera has always reported this; nothing read it.
            #
            # Read here rather than after calibration, because calibration
            # needs it too: it used to clip to a constant 4095 while the
            # tag declared the real level, so on a 14-bit camera the data
            # was crushed two stops down with a hard plateau and the file
            # said 16383. An *uncalibrated* frame from that camera was
            # better than a calibrated one.
            depth = (info.max_bit_depth if info and info.max_bit_depth
                     else 12)
            sensor_white = (1 << depth) - 1

            # ---- the operator is free from here ------------------------
            # Said before the queue, not after it. Deferring the write and
            # then leaving this until the write finished would have freed
            # the camera and left the stack trigger deaf for exactly as
            # long as before: it holds `_pending` from firing until a
            # slice lands, and a slice landing used to mean a file on disk.
            self._on_exposed()
            # The pixels are off the sensor and the guard has had its
            # measurement. Everything below this line is arithmetic and
            # file writing, and none of it needs a hand held still on a
            # focus knob. Measured on an 18-slice stack: 5.4 s per slice,
            # of which this half is most.
            context = dict(self.context)

            def persist() -> None:
                self._persist(
                    context=context,
                    raw=raw, setup=setup, subject=subject, slide=slide,
                    exposure_us=exposure_us, gain_pct=gain_pct, depth=depth,
                    sensor_white=sensor_white, info=info, frames=frames,
                    moved=moved, moved_px=moved_px, position=position,
                    started=started)

            # What the job keeps alive, not what it writes: the raw, the
            # corrected copy, and the developed image the JPEG comes from.
            held = int(raw.nbytes) * 3
            self._last_frame_bytes = int(raw.nbytes)
            if not self._writer.submit(held, persist,
                                       label=subject or "capture"):
                # No room after all -- `room_for` was asked before the
                # exposure and the answer can go stale. Doing it here costs
                # the operator the wait they used to pay every single time;
                # dropping it would cost a photograph. Never the second.
                _log.info("write queue full; writing this one in line")
                persist()
        except Exception as exc:
            self._on_state("idle")
            self._on_result(CaptureResult(
                ok=False, message=_explain(exc),
                elapsed=time.perf_counter() - started))
        finally:
            self._busy.release()


    def _persist(self, *, raw, setup, subject: str, slide: str,
                 context: dict | None = None,
                 exposure_us: int, gain_pct: int, depth: int,
                 sensor_white: int, info, frames: int,
                 moved, moved_px, position, started: float) -> None:
        """Everything after the shutter. Runs on the write queue.

        Split out of `_run` so that the camera, and the operator, are free
        while it happens. It reports through the same callbacks, so from
        the window's side a capture still simply completes -- later than it
        used to relative to the exposure, and far sooner relative to the
        next one.
        """
        try:
            self._on_state("calibrating")
            corrected, applied, neutral, black = self._apply_calibration(
                raw, setup, exposure_us, gain_pct, slide,
                white_level=sensor_white)
            if frames > 1:
                applied.append(f"avg{frames}")

            self._on_state("writing")
            path, sequence, when = self._destination(setup, subject)
            # A monochrome sensor gets no CFA pattern at all. Roughly a
            # quarter of ToupTek's microscopy range is mono, and labelling
            # greyscale data with a Bayer pattern makes every developer
            # demosaic noise into colour.
            mono = info is not None and not info.is_colour
            pattern = None if mono else (info.bayer_pattern if info
                                         else "GBRG")
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
                pixel_um = sensor_pitch(setup, info)
                meta = from_setup(setup, exposure_us=exposure_us,
                                  gain_pct=gain_pct, subject=subject,
                                  slide=slide,
                                  calibration="+".join(applied) or "none",
                                  app_version=_version(),
                                  pixel_um=pixel_um, sequence=sequence,
                                  when=when,
                                  artist=self._settings.artist,
                                  copyright=self._settings.copyright,
                                  unique_id=_fingerprint(corrected),
                                  context=context)

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
            shot = None if (mono or decoded) else (
                neutral or dng.grey_world_neutral(out))

            # What this capture is *for* decides whether the raw is
            # optional. A tile or a slice is an ingredient -- the stitcher
            # and the merge read it back -- so "JPEG only" must never apply
            # to one. Skipping the write rather than deleting afterwards:
            # a setting about file formats should not be able to remove a
            # frame that already exists on disk.
            want = self._settings.image_format
            raw_wanted = want != "jpeg" or self.raw_required
            # Here rather than inside the DNG writer. It was only there, so
            # with image_format="jpeg" -- the one path that never reaches
            # the writer -- nothing created the folder, and every capture
            # into a new date folder failed with a message that did not say
            # why. First shot of the day, every day.
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            written = None
            if raw_wanted:
                preview = dng.make_preview(
                    out, bayer=not (mono or decoded), black=black,
                    white=white, neutral=shot)
                if decoded:
                    # The frame arrives BGR -- that is what OpenCV hands
                    # back, and what the preview and the JPEG below both
                    # expect -- while the DNG's linear planes are R, G, B.
                    # Written straight through, red and blue came out
                    # swapped in the raw and its embedded thumbnail while
                    # the JPEG beside it was correct, so the two files
                    # disagreed about which channel was red.
                    written = dng.write_linear_streamed(
                        path, lambda s, c: out[s:s + c, :, ::-1],
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

            # The photograph, beside the negative, and written second: this
            # is the pleasant half of the output and the raw is the
            # irreplaceable half.
            jpeg = None
            if want in ("both", "jpeg"):
                jpeg = Path(path).with_suffix(".jpg")
                image = develop.develop(
                    out, pattern=None if (mono or decoded) else pattern,
                    black=black, white=white, neutral=shot)
                # On the photograph, never on the negative. `meta` carries
                # the micrometres-per-pixel computed from sensor pitch over
                # total magnification, and `draw` refuses on anything it
                # cannot stand behind.
                if self._settings.scale_bar and meta is not None:
                    scalebar.draw(
                        image, meta.um_per_px,
                        style=self._settings.scale_bar_style,
                        face=self._settings.scale_bar_face,
                        corner=self._settings.scale_bar_corner,
                        size=self._settings.scale_bar_size,
                        label_at=self._settings.scale_bar_label,
                        opacity=self._settings.scale_bar_opacity,
                        plain_units=self._settings.scale_bar_plain_units)
                if not develop.write_jpeg(jpeg, image,
                                          self._settings.jpeg_quality):
                    jpeg = None
                del image
            written = written or (str(jpeg) if jpeg else None)
            if written is None:
                raise RuntimeError("nothing could be written to disk")

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

    def _apply_calibration(self, raw, setup, exposure_us: int, gain_pct: int,
                           slide: str, white_level: int = dng.WHITE_LEVEL):
        """Correct the frame with whatever the store has for this configuration.

        Deliberately partial: a dark with no flat is still worth having, and
        refusing to capture because one product is missing would make the gate
        a wall. What was applied is recorded rather than assumed.

        Black level is written as zero once a dark has been subtracted, because
        it has been -- leaving the original offset in the tag would make a
        developer subtract it twice.

        `white_level` is the *sensor's*, and has to be, because the clip at
        the end of `calibrate` is the frame's ceiling. Passing the 12-bit
        constant here while writing the real level into the tag is how a
        14-bit capture came out two stops dark with its highlights welded
        flat.
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

        # The picked balance used to *replace* the measured one, on the
        # reasoning that the more specific thing the operator said should
        # win. They are not the same kind of statement, so neither wins:
        # they multiply.
        #
        # The flat-measured balance is absolute and lives in the raw's own
        # domain -- sensor, optics and lamp, measured off a blank field of
        # actual Bayer data. The picked one is a small residual trim, and
        # it is picked off the *preview*, which on a ToupTek has been
        # through the camera's ISP: white balance, colour matrix, tone
        # curve. `grab_raw` turns all of that off. So a pick describes
        # what the ISP's output still had wrong, not what the sensor did.
        #
        # Replacing threw away the whole raw-domain correction and kept
        # only the trim. Measured on Nate's stack: the flat said R x1.35,
        # B x3.32; the pick said R x1.21, B x0.91; and the files went out
        # with the pick alone -- three and a third times too little blue,
        # which is a green cast you can see across the room. The preview
        # beside it looked white, because the ISP had already done the
        # part the files were missing.
        picked = balance.sane(getattr(self._settings,
                                      "white_balance_gains", balance.UNITY))
        if picked != balance.UNITY:
            base = ((1.0 / neutral[0], 1.0, 1.0 / neutral[2])
                    if neutral else balance.UNITY)
            gains = balance.sane((base[0] * picked[0], 1.0,
                                  base[2] * picked[2]))
            neutral = (1.0 / max(gains[0], 1e-6), 1.0,
                       1.0 / max(gains[2], 1e-6))
            # Both named, because a file whose provenance has to be
            # inferred is one that will eventually be trusted wrongly.
            applied = [a for a in applied if a != "wb"] + (
                ["wb+picked"] if base != balance.UNITY else ["wb(picked)"])

        if dark is None and flat is None and defects is None:
            return raw, applied, neutral, black

        corrected = F.calibrate(raw, dark=dark, flat=flat, defects=defects,
                                white_level=white_level)
        return corrected.astype(raw.dtype), applied, neutral, black

    def _destination(self, setup, subject: str) -> tuple[Path, int, object]:
        """The path, its sequence number and the moment -- all three, because
        the file's own metadata should say which capture it is and when."""
        when = datetime.now()
        # Sequence comes from what is on disk, so it survives a crash, a manual
        # file move, and two copies of the app running at once.
        folder = self._settings.resolve(setup=setup, seq=1, subject=subject,
                                        when=when).parent
        with self._reserve_lock:
            seq = max(next_sequence(folder, self._settings.filename_pattern),
                      self._reserved.get(folder, 0) + 1)
            self._reserved[folder] = seq
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
    hex -- so string matching could never have recognised one. That is how a
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
