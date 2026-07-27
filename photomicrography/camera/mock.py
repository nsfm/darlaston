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
        px = int(self.stage_xy[0] / full.pixel_um) % (self._scene.shape[1] - full.width)
        py = int(self.stage_xy[1] / full.pixel_um) % (self._scene.shape[0] - full.height)
        view = self._scene[py:py + full.height, px:px + full.width]

        sigma = abs(self.focus_z) * 0.9
        native = (w, h) == (full.width, full.height)

        if native:
            # Full resolution: blur at sensor scale, where the striae live.
            img = view.astype(np.float32) / 255.0
            if sigma > 0.35:
                img = cv2.GaussianBlur(img, (0, 0), sigma)
            return img

        # Preview: downsample on uint8 first, then blur at the reduced scale.
        # Strictly the optics blur before the sensor samples, but at preview
        # resolution the striae are already below Nyquist, so the difference is
        # invisible -- and doing it this way avoids converting 20 MP to float
        # thirty times a second.
        #
        # Memoised on stage position, because the decimation of a 20 MP crop is
        # the whole cost and the stage is stationary for most frames. Focus,
        # exposure and gain then operate on the small array for free.
        key = (px, py, w, h)
        if getattr(self, "_crop_key", None) != key:
            self._crop_key = key
            self._crop = cv2.resize(view, (w, h), interpolation=cv2.INTER_AREA)
        img = self._crop.astype(np.float32) / 255.0
        s = sigma * (w / full.width)
        if s > 0.35:
            img = cv2.GaussianBlur(img, (0, 0), s)
        return img

    def _render_into(self, buf: np.ndarray, res: Resolution) -> None:
        h, w = res.height, res.width
        img = self._render_gray(w, h)

        # Illumination falloff, so flat-field correction is exercisable.
        # Cached: it depends only on the frame size, and rebuilding an mgrid
        # per frame made the synthetic camera slower than a real one.
        img *= self._vignette(h, w)

        # Exposure and gain behave the way the handoff in DESIGN.md 4 assumes:
        # both scale the signal, only gain amplifies the noise with it.
        level = img * (self._exposure_us / 8330.0) * (self._gain_pct / 100.0)
        # Gain amplifies read noise along with signal; exposure does not. That
        # asymmetry is the whole basis of the exposure handoff, so the synthetic
        # camera has to honour it or the feature cannot be tested here.
        noise_scale = 0.004 * np.sqrt(self._gain_pct / 100.0)
        level += self._rng.normal(0, noise_scale, level.shape).astype(np.float32)

        u8 = np.clip(level * 255.0, 0, 255).astype(np.uint8)
        buf[:, :, 0] = u8
        buf[:, :, 1] = u8
        buf[:, :, 2] = np.clip(u8.astype(np.int16) + 6, 0, 255).astype(np.uint8)

    # ---- capture ---------------------------------------------------------

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
