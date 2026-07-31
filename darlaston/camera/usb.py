"""USB link diagnostics.

A 20 MP frame is 39.7 MB. At full resolution the link runs close to the USB 3.0
ceiling, so a marginal cable does not fail cleanly -- it renegotiates down to
USB 2.0 and everything simply becomes slow and unreliable. That is worth
naming out loud, because people spend a long time blaming the camera.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

#: USB vendor IDs the ToupTek family appears under. ToupTek has no VID
#: of its own -- everything rides Cypress/Anchor Chips IDs -- and the
#: rebadges add their own. Verified against each brand's shipped udev
#: rules: every library answers on exactly the VIDs its rules list.
#: 04b4 is the un-programmed EZ-USB bootloader state, which is worth
#: recognising because a camera stuck there looks absent otherwise.
TOUPTEK_VENDORS = ("0547", "04b4", "16d0", "9745", "0549")
#: Kept for callers that want the common case.
TOUPTEK_VENDOR = TOUPTEK_VENDORS[0]

#: Speeds as reported by sysfs, in Mbps.
SUPERSPEED_PLUS = 10000
SUPERSPEED = 5000
HIGH_SPEED = 480


@dataclass(frozen=True)
class LinkInfo:
    speed_mbps: int | None
    port: str | None
    vendor: str | None = None
    product: str | None = None

    @property
    def is_degraded(self) -> bool:
        """True when the link negotiated below USB 3.0."""
        return self.speed_mbps is not None and self.speed_mbps < SUPERSPEED

    @property
    def label(self) -> str:
        if self.speed_mbps is None:
            return "unknown"
        if self.speed_mbps >= SUPERSPEED_PLUS:
            return f"{self.speed_mbps} Mbps (USB 3.1)"
        if self.speed_mbps >= SUPERSPEED:
            return f"{self.speed_mbps} Mbps (USB 3.0)"
        if self.speed_mbps >= HIGH_SPEED:
            return f"{self.speed_mbps} Mbps (USB 2.0)"
        return f"{self.speed_mbps} Mbps"

    @property
    def advice(self) -> str | None:
        """What to actually do about it."""
        if self.speed_mbps is None:
            return None
        if self.speed_mbps <= HIGH_SPEED:
            return ("The link negotiated USB 2.0. Full-resolution capture needs "
                    "USB 3.0, so this is usually a cable or a front-panel port -- "
                    "try a shorter, well-shielded cable directly into a rear "
                    "USB 3 socket.")
        return None


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def probe(vendor: str | tuple[str, ...] = TOUPTEK_VENDORS) -> LinkInfo:
    """Find the camera on the bus and report how fast the link came up.

    Linux only for now; other platforms report unknown rather than guessing,
    because a wrong number here would send someone chasing the wrong fault.
    """
    wanted = (vendor,) if isinstance(vendor, str) else tuple(vendor)
    if not sys.platform.startswith("linux"):
        return LinkInfo(speed_mbps=None, port=None)

    for dev in sorted(Path("/sys/bus/usb/devices").glob("*")):
        found = _read(dev / "idVendor")
        if found not in wanted:
            continue
        speed = _read(dev / "speed")
        return LinkInfo(
            speed_mbps=int(float(speed)) if speed else None,
            port=dev.name,
            vendor=found,
            product=_read(dev / "product"),
        )
    return LinkInfo(speed_mbps=None, port=None, vendor=wanted[0])


def present(vendor: str | tuple[str, ...] = TOUPTEK_VENDORS) -> bool:
    """Is anything from this vendor on the bus at all?

    Cheap enough to poll, and it distinguishes "unplugged" from "plugged in but
    held by another program" -- which is the failure people actually hit.
    """
    wanted = (vendor,) if isinstance(vendor, str) else tuple(vendor)
    if not sys.platform.startswith("linux"):
        return False
    return any(_read(d / "idVendor") in wanted
               for d in Path("/sys/bus/usb/devices").glob("*"))
