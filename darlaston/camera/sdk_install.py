"""Fetching a camera vendor's SDK, with the user's knowledge and consent.

"Download this other thing first" is where most people quit, and for a
ToupTek-family camera there is no way around needing the vendor's
library. So this fetches it -- from the vendor's own server, straight to
the user's machine.

Two lines this deliberately does not cross.

**We never mirror.** Downloads come from the manufacturer's own URL. The
GPL section 7 permission in LICENSE.EXCEPTION covers *linking* against
these libraries; it says nothing about redistributing them, and it could
not -- the ToupTek SDK ships with no licence, no EULA and no copyright
notice, so nobody can say what redistribution it would permit. Hosting a
copy would be exactly the thing that posture exists to avoid. Fetching
direct from vendor to user is the pattern Debian's
`ttf-mscorefonts-installer` has used for twenty years.

**We never do it silently.** It is a large download of a proprietary
binary from a third party. The caller must show what will be fetched and
from where, and get a click. Nothing here starts on its own.

Verification is structural rather than a pinned checksum: vendors update
these archives in place under the same URL, so a pinned hash would break
on their schedule rather than ours and teach everyone to ignore it.
Instead the unpacked library is asked whether it exports the functions
darlaston actually calls -- the same check the loader applies -- which is
the property we care about and which a corrupted or wrong download
fails.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: Where fetched SDKs land. Under the user's data directory rather than
#: inside the installation, so it survives upgrades and needs no
#: privileges.
INSTALL_ROOT = Path(
    os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")
) / "darlaston/sdk"

#: A manifest may be refreshed from here so a vendor moving a URL can be
#: fixed without cutting a release. Failure to reach it is not an error;
#: the built-in table below is the fallback, and is what ships.
MANIFEST_URL = ("https://raw.githubusercontent.com/nsfm/darlaston/"
                "main/sdk-sources.json")

#: How long to wait on the vendor before giving up, in seconds.
TIMEOUT = 30


@dataclass(frozen=True)
class Source:
    """One vendor's SDK, and how to get it."""

    brand: str                  # matches BRANDS in toupcam.py
    label: str                  # what a person calls it
    page: str                   # the human download page, always usable
    url: str = ""               # direct archive, when one is known
    approx_mb: int = 0
    note: str = ""

    @property
    def automatic(self) -> bool:
        """Can we fetch this one, or must the user visit the page?"""
        return bool(self.url)


#: Only ToupTek's direct URL is included, because it is the only one
#: verified to serve the archive to a plain request. The rest carry their
#: download page instead of a guessed link: sending someone to a URL that
#: 404s is worse than sending them to a page that works.
SOURCES: tuple[Source, ...] = (
    Source(brand="toupcam", label="ToupTek",
           page="https://www.touptekphotonics.com/download/?category=SDK",
           url="https://www.touptekphotonics.com/downloads/software/"
               "download.php?soft=toupcamsdk",
           approx_mb=242,
           note="Also works for AmScope, Bresser, Omegon, Orion, "
                "RisingCam, TS-Optics and SVBony SC715C cameras."),
    Source(brand="altaircam", label="Altair Astro",
           page="https://www.altairastro.com/software-drivers/",
           note="Choose this for Altair cameras."),
    Source(brand="mallincam", label="MallinCam",
           page="https://www.mallincam.net/downloads.html",
           note="Choose this for MallinCam SkyRaider cameras."),
    Source(brand="meadecam", label="Meade",
           page="https://www.meade.com/support/downloads",
           note="Choose this for Meade LPI-G cameras."),
    Source(brand="ogmacam", label="OGMAVision",
           page="https://www.getogma.com/pages/downloads",
           note="Choose this for OGMA AP and GP cameras."),
)


def sources(refresh: bool = False) -> tuple[Source, ...]:
    """The download table, optionally refreshed from the published one."""
    if not refresh:
        return SOURCES
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=TIMEOUT) as fh:
            data = json.loads(fh.read().decode("utf-8"))
        found = tuple(Source(**entry) for entry in data["sources"])
        return found or SOURCES
    except Exception:
        # A vendor URL that has moved is worth fixing without a release;
        # not being able to check is not worth failing over.
        return SOURCES


def find(brand: str) -> Source | None:
    return next((s for s in SOURCES if s.brand == brand), None)


def _safe_extract(archive: zipfile.ZipFile, into: Path) -> None:
    """Unpack, refusing any member that would escape the destination.

    A zip may contain `../` components or absolute paths, and a naive
    extractall will happily write outside the directory it was given.
    This is somebody else's archive, so it gets checked.
    """
    into = into.resolve()
    for member in archive.infolist():
        target = (into / member.filename).resolve()
        if not target.is_relative_to(into):
            raise ValueError(
                f"archive member {member.filename!r} would be written "
                "outside the install directory; refusing it")
    archive.extractall(into)


def download(source: Source, on_progress: Callable[[int, int], None] | None
             = None, into: Path | None = None) -> Path:
    """Fetch and unpack one SDK. Returns its root. Verifies before keeping.

    `on_progress` receives (bytes so far, total or 0 when unknown).
    """
    if not source.automatic:
        raise ValueError(
            f"{source.label} does not publish a direct download; open "
            f"{source.page} and unpack it into {INSTALL_ROOT / source.brand}")

    root = (into or INSTALL_ROOT) / source.brand
    staging = Path(tempfile.mkdtemp(prefix="darlaston-sdk-"))
    try:
        archive = staging / "sdk.zip"
        request = urllib.request.Request(
            source.url,
            # Some vendor endpoints refuse an unadorned urllib agent.
            headers={"User-Agent": "darlaston/sdk-installer"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as fh:
            total = int(fh.headers.get("Content-Length") or 0)
            done = 0
            with archive.open("wb") as out:
                while chunk := fh.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)

        unpacked = staging / "unpacked"
        unpacked.mkdir()
        with zipfile.ZipFile(archive) as zf:
            _safe_extract(zf, unpacked)

        found = _verify(unpacked, source.brand)
        # Move into place only once it is known good, so a failed or
        # interrupted download never leaves a half-SDK that the loader
        # would then find and trust.
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            shutil.rmtree(root)
        shutil.move(str(found), str(root))
        return root
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _verify(unpacked: Path, brand: str) -> Path:
    """Find the SDK root inside what was unpacked, and prove it works.

    Vendors wrap their archives in a version-named directory, so the root
    is discovered rather than assumed. The library is then asked for the
    functions darlaston calls; a wrong, truncated or ancient archive
    fails here rather than at the first capture.
    """
    import ctypes

    from .toupcam import BRANDS, REQUIRED, library_dirs, library_name

    entry = next((b for b in BRANDS if b[0] == brand), None)
    if entry is None:
        raise ValueError(f"unknown brand {brand!r}")
    _module, soname, cls, _prefix = entry

    name = library_name(soname)
    wanted = library_dirs()
    for lib in sorted(unpacked.rglob(name)):
        # The exact platform subdirectory, not merely a path containing
        # something that looks like one. The archive ships android/x64
        # too, which sorts first and is a perfectly valid ELF for the
        # wrong operating system -- dlopen then fails on its dependencies
        # with a message about libm having an invalid ELF header, which
        # names nothing that helps. Matched from the end so the SDK's own
        # wrapper directory does not have to be guessed.
        parts = lib.parent.parts
        match = next((d for d in wanted
                      if tuple(d.split("/")) == parts[-len(d.split("/")):]),
                     None)
        if match is None:
            continue
        handle = ctypes.CDLL(str(lib), mode=ctypes.RTLD_LOCAL)
        missing = [fn for fn in REQUIRED if not hasattr(handle, f"{cls}_{fn}")]
        if missing:
            raise RuntimeError(
                f"the downloaded {name} is missing "
                f"{', '.join(missing)} -- it is not a current SDK")
        # Up out of the platform subdirectory, however deep it was, to the
        # root the loader expects to be handed.
        root = lib.parents[len(match.split("/"))]
        if not (root / "python").is_dir():
            raise RuntimeError(
                f"the archive has no python bindings beside {lib}")
        return root
    raise RuntimeError(
        f"no {name} for {'/'.join(wanted)} was found in the "
        f"downloaded archive")


def installed() -> list[str]:
    """Brands already present under the install root."""
    if not INSTALL_ROOT.is_dir():
        return []
    return sorted(p.name for p in INSTALL_ROOT.iterdir() if p.is_dir())
