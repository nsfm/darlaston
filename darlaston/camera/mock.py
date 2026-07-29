"""A synthetic camera, for development and for testing the threading contract.

Not a stub. It renders a diatom-like field with controllable focus, stage
position, illumination gradient and noise, so that the tracker, the focus
metric, the histogram and flat-field correction can all be exercised
deterministically with no hardware attached.

Drive it from a test or from the UI:

    cam.stage_xy = (120.0, -40.0)   # microns, moves the field
    cam.focus_z  = 3.5              # microns from best focus
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np

from .base import CameraBackend, CameraInfo, Resolution
from .buffers import BufferPool, Frame

_RESOLUTIONS = (
    Resolution(0, 5440, 3648, 2.40),
    Resolution(1, 2736, 1824, 4.80),
    Resolution(2, 1824, 1216, 7.20),
)


def _render_scene(w: int, h: int, seed: int = 7) -> np.ndarray:
    """A field of elongated frustules with fine striae.

    Deliberately similar in character to the real subject: sparse, elongated,
    carrying periodic fine structure near the resolution limit -- because that
    periodic structure is what focus metrics actually key on, and a synthetic
    camera that gets its spatial scale wrong is useless for testing them.

    Sized for the *sensor*, not the preview: striae must sit near Nyquist at
    full resolution, and the binned live view is a downsample of this, which is
    also what binning physically is. Stored as uint8 to keep a canvas this
    large cheap.
    """
    rng = np.random.default_rng(seed)
    canvas = np.full((h, w), 0.62, np.float32)
    for _ in range(120):
        cx, cy = rng.integers(0, canvas.shape[1]), rng.integers(0, canvas.shape[0])
        length, width = rng.integers(160, 520), rng.integers(28, 70)
        angle = rng.uniform(0, np.pi)
        yy, xx = np.mgrid[-length:length, -width:width].astype(np.float32)
        body = (xx / width) ** 2 + (yy / length) ** 2 <= 1.0
        # Striae: the fine periodic detail that vanishes first on defocus.
        striae = 0.5 + 0.5 * np.cos(yy * (2 * np.pi / rng.uniform(5.0, 9.0)))
        patch = np.where(body, 0.30 + 0.16 * striae, np.nan)
        rot = cv2.getRotationMatrix2D((float(width), float(length)),
                                      float(np.degrees(angle)), 1.0)
        patch = cv2.warpAffine(patch, rot, (width * 2, length * 2),
                               borderValue=np.nan)
        y0, x0 = int(cy) - length, int(cx) - width
        y1, x1 = y0 + patch.shape[0], x0 + patch.shape[1]
        if y0 < 0 or x0 < 0 or y1 > canvas.shape[0] or x1 > canvas.shape[1]:
            continue
        target = canvas[y0:y1, x0:x1]
        ok = ~np.isnan(patch)
        target[ok] = patch[ok]
    return np.clip(canvas * 255.0, 0, 255).astype(np.uint8)


class MockCamera(CameraBackend):
    """Synthetic backend. Same contract as the real one, no hardware."""

    def __init__(self, fps: float = 30.0, seed: int = 7) -> None:
        self._fps = fps
        self._seed = seed
        self._info: CameraInfo | None = None
        self._res = _RESOLUTIONS[2]
        self._exposure_us = 8330
        self._gain_pct = 100
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._pool: BufferPool | None = None
        self._seq = 0
        self._scene: np.ndarray | None = None

        # Externally driven, so tests and the UI can move the world.
        self.stage_xy = (0.0, 0.0)   # microns
        self.focus_z = 0.0           # microns from best focus
        #: Focal-plane tilt across the frame, in microns from one edge to the
        #: other. A real slide is never perfectly normal to the axis, and a
        #: thick subject has depth -- so different regions come into focus at
        #: different Z. Without this the synthetic camera cannot exercise
        #: focus coverage at all, because everything focuses at once.
        self.focus_tilt = 0.0

        #: Objective magnification, and the magnification at which the frame
        #: covers the whole available crop. Higher magnification narrows the
        #: field of view by exactly the ratio, which is the signal turret
        #: detection measures by log-polar correlation.
        #:
        #: The reference is the *lowest* objective on purpose, so every other
        #: position zooms in. Zooming out would need a scene larger than the
        #: sensor by the full magnification range, which is hundreds of
        #: megabytes for a test fixture.
        self.magnification = 10.0
        self.mag_reference = 10.0
        #: Turret occlusion, signed. Magnitude is the fraction of the frame
        #: the rotating turret currently blocks; the sign is which edge it
        #: swept in from, positive for the left. That direction is the other
        #: half of what turret detection reads.
        self.occlusion = 0.0

        # One RNG, reused. Constructing a generator per frame is pure waste.
        self._rng = np.random.default_rng()

    # ---- lifecycle -------------------------------------------------------

    def open(self) -> CameraInfo:
        self._info = CameraInfo(
            model="MockCam (synthetic)",
            serial="MOCK-0000000000000000000000000",
            resolutions=_RESOLUTIONS,
            max_bit_depth=12,
            bayer_pattern="GBRG",
            exposure_range_us=(264, 15_000_000),
            gain_range_pct=(100, 5000),
        )
        # Canvas sized past the sensor so the stage has somewhere to travel.
        full = _RESOLUTIONS[0]
        self._scene = _render_scene(int(full.width * 1.25),
                                    int(full.height * 1.25), self._seed)
        return self._info

    def close(self) -> None:
        self.stop_stream()
        self._info = None

    @property
    def info(self) -> CameraInfo | None:
        return self._info

    # ---- settings --------------------------------------------------------

    def set_resolution(self, index: int) -> None:
        self._res = _RESOLUTIONS[index]

    def set_exposure(self, microseconds: int) -> None:
        self._exposure_us = int(microseconds)

    def set_gain(self, percent: int) -> None:
        self._gain_pct = int(percent)

    def get_exposure(self) -> int:
        return self._exposure_us

    def get_gain(self) -> int:
        return self._gain_pct

    # ---- streaming -------------------------------------------------------

    def start_stream(self, on_frame: Callable[[Frame], None]) -> None:
        if self._thread is not None:
            return
        # Live view runs binned: full-resolution raw exceeds the USB3 budget on
        # real hardware, so the mock keeps the same shape.
        res = _RESOLUTIONS[2]
        self._pool = BufferPool((res.height, res.width, 3), np.uint8, count=4)
        self._running.set()
        self._thread = threading.Thread(
            target=self._loop, args=(on_frame, res), daemon=True,
            name="mock-camera")
        self._thread.start()

    def stop_stream(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self, on_frame: Callable[[Frame], None], res: Resolution) -> None:
        interval = 1.0 / self._fps
        while self._running.is_set():
            t0 = time.perf_counter()
            buf = self._pool.acquire()
            if buf is None:
                # Every buffer in flight means the consumer is behind. Drop the
                # frame rather than allocating into a stall.
                time.sleep(interval)
                continue
            self._render_into(buf, res)
            self._seq += 1
            frame = Frame(data=buf, seq=self._seq, timestamp=time.time(),
                          exposure_us=self._exposure_us, gain_pct=self._gain_pct,
                          binned=True, _pool=self._pool)
            on_frame(frame)
            time.sleep(max(0.0, interval - (time.perf_counter() - t0)))

    def _noise(self, shape: tuple[int, int]) -> np.ndarray:
        """Read noise, uncorrelated between consecutive frames.

        Several pre-generated fields cycled per frame, plus a random crop.
        One field alone is not noise to a tracker: the same texture at a
        randomly jumped offset every frame is a *trackable pattern*, and it
        gave phase correlation a confident lock on a featureless scene that
        real per-readout noise could never provide. Generating fresh noise
        each frame is the honest thing and costs ~25 ms; four cycled fields
        cost the memory of four and the honesty of none of it matters to
        consecutive-frame correlation, which is all the tracker measures.
        """
        h, w = shape
        fields = getattr(self, "_noise_fields", None)
        if fields is None or fields[0].shape[0] < h + 64 \
                or fields[0].shape[1] < w + 64:
            fields = [self._rng.standard_normal((h + 64, w + 64))
                      .astype(np.float32) for _ in range(4)]
            self._noise_fields = fields
        field = fields[self._seq % len(fields)]
        dy = int(self._rng.integers(0, 64))
        dx = int(self._rng.integers(0, 64))
        return field[dy:dy + h, dx:dx + w]

    def _defocus(self, img: np.ndarray, scale: float) -> np.ndarray:
        """Blur by |z - z_focus|, where z_focus may vary across the frame.

        A spatially varying Gaussian is expensive, so this renders a few
        discrete blur levels and picks between them per pixel. Crude, but it
        is a synthetic camera -- what matters is that different regions come
        into focus at different Z, which is the situation focus coverage
        exists to handle.
        """
        h, w = img.shape
        if abs(self.focus_tilt) < 1e-6:
            sigma = abs(self.focus_z) * 0.9 * scale
            return cv2.GaussianBlur(img, (0, 0), sigma) if sigma > 0.35 else img

        # Focal plane ramps left to right across the frame.
        ramp = np.linspace(-0.5, 0.5, w, dtype=np.float32) * self.focus_tilt
        local = np.abs(self.focus_z - ramp) * 0.9 * scale        # (w,)

        levels = np.array([0.0, 1.0, 2.0, 3.5, 5.5, 8.0], np.float32)
        pick = np.abs(local[None, :] - levels[:, None]).argmin(axis=0)  # (w,)
        out = np.empty_like(img)
        for i, sigma in enumerate(levels):
            cols = np.nonzero(pick == i)[0]
            if not cols.size:
                continue
            band = (img if sigma <= 0.35
                    else cv2.GaussianBlur(img, (0, 0), float(sigma)))
            out[:, cols] = band[:, cols]
        return out

    def _vignette(self, h: int, w: int) -> np.ndarray:
        cached = getattr(self, "_vig", None)
        if cached is not None and cached.shape == (h, w):
            return cached
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
        self._vig = (1.0 - 0.16 * r2).astype(np.float32)
        return self._vig

    def _render_gray(self, w: int, h: int) -> np.ndarray:
        """Scene window at the requested size, in the physically right order.

        Crop at sensor scale, defocus there, *then* downsample. Defocus happens
        in the optics, before the sensor bins -- and blurring after a resize
        lets interpolation smear the striae regardless of focus, which makes
        the synthetic camera useless for the thing it exists to test.
        """
        full = _RESOLUTIONS[0]
        # Stage position selects a window into the scene, in sensor pixels.
        # The scene is treated as periodic: the window wraps rather than
        # clamping, so the stage can travel indefinitely in any direction --
        # which slide navigation and mosaic work actually exercise, and which
        # a crop that teleports at the canvas edge cannot stand in for.
        px = int(self.stage_xy[0] / full.pixel_um) % self._scene.shape[1]
        py = int(self.stage_xy[1] / full.pixel_um) % self._scene.shape[0]
        view = self._wrap_crop(py, px, full.height, full.width)

        native = (w, h) == (full.width, full.height)

        if native:
            # Full resolution: blur at sensor scale, where the striae live.
            img = view.astype(np.float32) / 255.0
            return self._defocus(img, 1.0)

        # Preview: crop from a scene that is *already* at preview scale.
        #
        # This used to decimate a 20 MP crop per frame, memoised on stage
        # position -- which is free while parked and 61 ms per frame while
        # cranking, exactly when the app is working hardest. That made the
        # synthetic camera the bottleneck in every performance measurement
        # and hid the real numbers behind its own cost.
        #
        # Pre-decimating the whole scene once is also the more honest model:
        # binning *is* decimation at the sensor, so cropping a reduced scene
        # is what the hardware actually does, rather than an optimisation of
        # it. Blur still happens after, at the reduced scale, because at
        # preview resolution the striae are already below Nyquist.
        ratio = w / full.width
        key = (w, h)
        if getattr(self, "_small_key", None) != key:
            self._small_key = key
            sh = int(round(self._scene.shape[0] * ratio))
            sw = int(round(self._scene.shape[1] * ratio))
            self._small_scene = cv2.resize(self._scene, (sw, sh),
                                           interpolation=cv2.INTER_AREA)
        small = self._small_scene
        sx = int(px * ratio) % small.shape[1]
        sy = int(py * ratio) % small.shape[0]

        # Magnification narrows the window into the scene by exactly its
        # ratio to the reference, which is what a real turret rotation does
        # to the field of view.
        zoom = max(1.0, self.magnification / max(self.mag_reference, 1e-6))
        cw = max(8, int(round(w / zoom)))
        ch = max(8, int(round(h / zoom)))
        # Centred on the same point of the slide at every magnification.
        # Cropping from the corner instead would move the optical axis as the
        # turret turns, and log-polar correlation -- which is how the
        # magnification change gets measured -- assumes a shared centre. A
        # real turret is parcentric to within a fraction of a field; cropping
        # from the corner is not remotely.
        cy = (sy + (h - ch) // 2) % small.shape[0]
        cx = (sx + (w - cw) // 2) % small.shape[1]
        crop = self._wrap_crop_of(small, cy, cx, ch, cw)
        if (cw, ch) != (w, h):
            crop = cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)
        img = crop.astype(np.float32) / 255.0
        return self._defocus(img, ratio)

    def _wrap_crop(self, y0: int, x0: int, h: int, w: int) -> np.ndarray:
        return self._wrap_crop_of(self._scene, y0, x0, h, w)

    @staticmethod
    def _wrap_crop_of(s: np.ndarray, y0: int, x0: int,
                      h: int, w: int) -> np.ndarray:
        """Window into an image with wraparound. Zero-copy when the window
        does not cross an edge, which is most frames."""
        sh, sw = s.shape
        rows = (s[y0:y0 + h] if y0 + h <= sh
                else np.concatenate((s[y0:], s[:y0 + h - sh]), axis=0))
        return (rows[:, x0:x0 + w] if x0 + w <= sw
                else np.concatenate((rows[:, x0:], rows[:, :x0 + w - sw]),
                                    axis=1))

    def _render_into(self, buf: np.ndarray, res: Resolution) -> None:
        h, w = res.height, res.width
        img = self._render_gray(w, h)

        # Illumination falloff, so flat-field correction is exercisable.
        # Cached: it depends only on the frame size, and rebuilding an mgrid
        # per frame made the synthetic camera slower than a real one.
        # Vignette, exposure and gain are all constant between frames, so they
        # fold into one cached field: one multiply instead of four passes over
        # 2.2 MP. The synthetic camera exists to stand in for a real one, and a
        # mock three times slower than the hardware misrepresents the app.
        img *= self._scale_field(h, w)
        img += self._noise((h, w)) * (0.004 * np.sqrt(self._gain_pct / 100.0))

        # The turret, mid-rotation: an opaque edge sweeping across the field.
        # Not quite black, because a real turret leaks a little light around
        # the bore and a detector that only works against pure black would
        # not survive contact with glass.
        if self.occlusion:
            covered = int(w * min(abs(self.occlusion), 1.0))
            if covered:
                if self.occlusion > 0:
                    img[:, :covered] *= 0.015
                else:
                    img[:, w - covered:] *= 0.015

        u8 = np.clip(img * 255.0, 0, 255).astype(np.uint8)
        buf[:, :, 0] = u8
        buf[:, :, 1] = u8
        buf[:, :, 2] = u8

    def _scale_field(self, h: int, w: int) -> np.ndarray:
        key = (h, w, self._exposure_us, self._gain_pct)
        if getattr(self, "_scale_key", None) != key:
            self._scale_key = key
            self._scale = (self._vignette(h, w)
                           * (self._exposure_us / 8330.0)
                           * (self._gain_pct / 100.0)).astype(np.float32)
        return self._scale

    def rotate_turret(self, to_magnification: float, direction: int = 1,
                      steps: int = 8):
        """Yield the stages of a turret rotation, for tests and demos.

        A real rotation is: the outgoing objective sweeps out of the light
        path from one side, the field is dark for a moment, then the incoming
        one sweeps in and the field of view has changed. Driving that by hand
        in every test invites each one to simulate it slightly differently.
        """
        for k in range(1, steps + 1):
            self.occlusion = direction * (k / steps)
            yield "closing"
        self.magnification = float(to_magnification)
        for k in range(steps, 0, -1):
            self.occlusion = direction * (k / steps)
            yield "opening"
        self.occlusion = 0.0
        yield "open"

    def grab_raw(self, timeout_ms: int = 8000) -> Frame:
        """Full-resolution 12-bit frame, rendered natively rather than upscaled."""
        res = _RESOLUTIONS[0]
        pool = BufferPool((res.height, res.width), np.uint16, count=1)
        buf = pool.acquire()
        img = self._render_gray(res.width, res.height)
        img = img * self._vignette(res.height, res.width)
        img *= (self._exposure_us / 8330.0) * (self._gain_pct / 100.0)
        buf[:] = np.clip(img * 4095.0, 0, 4095).astype(np.uint16)
        self._seq += 1
        return Frame(data=buf, seq=self._seq, timestamp=time.time(),
                     exposure_us=self._exposure_us, gain_pct=self._gain_pct,
                     binned=False, _pool=pool)
