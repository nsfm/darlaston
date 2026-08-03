#!/usr/bin/env python3
"""Wrap the frozen build in whatever this platform expects to be handed.

PyInstaller produces a directory. What a person can be given differs:

  * **Linux: an AppImage.** One executable file, no installation, no
    package manager. Chosen over Flatpak because the two hard requirements
    are raw USB access to the camera and loading a vendor SDK out of the
    user's home directory, and a sandbox makes both awkward at once.
  * **macOS: a .app in a .dmg.** The .app is what macOS understands as a
    program; the .dmg is what a download is expected to be, and it carries
    the drag-to-Applications gesture everybody already knows.
  * **Windows: a zip.** An installer would be better and needs a code
    signing certificate to not be worse -- an unsigned NSIS installer
    trips SmartScreen harder than an unsigned executable does.

Everything here is unsigned. See NOTARISING.md for what that costs the
person downloading it, and what it would take to fix.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "pyi"
OUT = ROOT / "dist" / "release"

#: Pinned to a tagged release rather than to `continuous`: a release job
#: that silently changes its own tooling is one that will eventually
#: produce something nobody can reproduce.
#:
#: From AppImage/appimagetool, which is where the tool lives now. The old
#: AppImageKit URL 404s -- release 13's assets were renamed with an
#: `obsolete-` prefix rather than removed, so the failure is a plain 404
#: with nothing saying why.
APPIMAGE_VERSION = "1.9.1"
APPIMAGETOOL = ("https://github.com/AppImage/appimagetool/releases/download"
                f"/{APPIMAGE_VERSION}/appimagetool-x86_64.AppImage")


def version() -> str:
    """The version, made safe to put in a filename.

    setuptools-scm produces things like `0.1.0.dev116+g7340561.d20260802`.
    The local part after `+` is meaningful and worth keeping -- it names
    the exact commit a build came from, which is the first thing anybody
    wants when a stranger reports a bug -- but `+` is awkward in a URL and
    the whole thing is illegal in a Windows filename if it ever gained a
    colon. So it is kept, with the separator swapped.
    """
    from darlaston import __version__
    return __version__.replace("+", "-")


def _run(*args: str, **kw) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run(args, check=True, **kw)


# ---- Linux ---------------------------------------------------------------

def appimage(label: str) -> Path:
    """A single self-contained executable file."""
    appdir = ROOT / "build" / "darlaston.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr").mkdir(parents=True)
    shutil.copytree(DIST / "darlaston", appdir / "usr" / "bin")

    # AppImage wants a desktop entry, an icon and an AppRun. The desktop
    # entry's name has to match the icon's basename or appimagetool
    # refuses, which is the kind of thing that only says "failed".
    (appdir / "darlaston.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=darlaston\n"
        "Comment=Capture and mosaicking for photomicrography\n"
        "Exec=darlaston\n"
        "Icon=darlaston\n"
        "Categories=Graphics;Science;\n"
        "Terminal=false\n")

    _write_icon(appdir / "darlaston.png")

    apprun = appdir / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        'exec "$HERE/usr/bin/darlaston" "$@"\n')
    apprun.chmod(0o755)

    tool = ROOT / "build" / "appimagetool"
    if not tool.exists():
        urllib.request.urlretrieve(APPIMAGETOOL, tool)
        tool.chmod(0o755)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"darlaston-{version()}-{label}.AppImage"
    env = dict(os.environ, ARCH="x86_64")
    # --appimage-extract-and-run because the runner has no FUSE mount
    # available inside a container, and appimagetool is itself an AppImage.
    _run(str(tool), "--appimage-extract-and-run", "--no-appstream",
         str(appdir), str(target), env=env)
    target.chmod(0o755)
    return target


def _write_icon(path: Path) -> None:
    """The application mark, from the file the other platforms also use."""
    shutil.copy(ROOT / "packaging" / "icons" / "darlaston.png", path)


# ---- macOS ---------------------------------------------------------------

def dmg(label: str) -> Path:
    """The .app, in the disk image a download is expected to be."""
    app = DIST / "darlaston.app"
    if not app.exists():
        raise SystemExit(f"no bundle at {app}")

    # Ad-hoc signature. It does NOT make Gatekeeper happy -- only a
    # Developer ID and notarisation do that -- but an unsigned arm64
    # binary will not launch on Apple silicon at all, whereas an ad-hoc
    # signed one launches after the user allows it. The difference is
    # "annoying" versus "impossible".
    _run("codesign", "--force", "--deep", "--sign", "-",
         "--timestamp=none", str(app))
    _run("codesign", "--verify", "--deep", "--verbose=2", str(app))

    staging = ROOT / "build" / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / "darlaston.app", symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    # Read alongside the app in the mounted image, where somebody who has
    # just been told "unidentified developer" is actually looking.
    shutil.copy(ROOT / "packaging" / "NOTARISING.md",
                staging / "Why does macOS warn about this.txt")

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"darlaston-{version()}-{label}.dmg"
    if target.exists():
        target.unlink()
    _run("hdiutil", "create", "-volname", "darlaston", "-srcfolder",
         str(staging), "-ov", "-format", "UDZO", str(target))
    return target


# ---- Windows -------------------------------------------------------------

def windows_zip(label: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"darlaston-{version()}-{label}.zip"
    source = DIST / "darlaston"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                z.write(path, Path("darlaston") / path.relative_to(source))
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True,
                    help="platform label, e.g. linux-x86_64")
    args = ap.parse_args()

    if sys.platform.startswith("linux"):
        made = appimage(args.label)
    elif sys.platform == "darwin":
        made = dmg(args.label)
    else:
        made = windows_zip(args.label)

    size = made.stat().st_size / 1048576
    print(f"\n{made.name}  {size:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
