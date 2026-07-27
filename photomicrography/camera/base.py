"""The camera abstraction the rest of the application sees.

Kept deliberately small. Everything vendor-specific -- option codes, call
ordering, the bottom-up raw stream, the Bayer parity shift -- lives behind this
and is never allowed to leak upward.

Backends: ToupTek today, a synthetic one for development without hardware, and
a tethered mirrorless later.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

from .buffers import Frame


class CameraState(Enum):
    """Disconnection is a state, not an exception -- the USB link runs near
    saturation at full resolution and will drop. See ARCHITECTURE.md 5."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    READY = auto()
    STREAMING = auto()
    ERROR = auto()


@dataclass(frozen=True)
class Resolution:
    index: int
    width: int
    height: int
    pixel_um: float

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1e6

    def __str__(self) -> str:
        return f"{self.width}x{self.height} ({self.megapixels:.2f} MP)"


@dataclass(frozen=True)
class CameraInfo:
    model: str
    serial: str
    resolutions: tuple[Resolution, ...]
    max_bit_depth: int
    bayer_pattern: str
    exposure_range_us: tuple[int, int]
    gain_range_pct: tuple[int, int]

    @property
    def is_colour(self) -> bool:
        return bool(self.bayer_pattern)


class CameraBackend(ABC):
    """One camera. Owns no threads; the session owns those."""

    @abstractmethod
    def open(self) -> CameraInfo: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def set_resolution(self, index: int) -> None: ...

    @abstractmethod
    def set_exposure(self, microseconds: int) -> None: ...

    @abstractmethod
    def set_gain(self, percent: int) -> None: ...

    @abstractmethod
    def start_stream(self, on_frame: Callable[[Frame], None]) -> None:
        """Begin delivering preview frames.

        The callback runs on the backend's own thread and must do almost
        nothing -- see ARCHITECTURE.md 3.4. It receives ownership of the Frame.
        """

    @abstractmethod
    def stop_stream(self) -> None: ...

    @abstractmethod
    def grab_raw(self, timeout_ms: int = 8000) -> Frame:
        """Pull one full-resolution raw frame. Blocks. Never drops.

        Returned in canonical orientation with the sensor's own Bayer pattern
        valid -- backends absorb their own quirks.
        """

    @property
    @abstractmethod
    def info(self) -> CameraInfo | None: ...
