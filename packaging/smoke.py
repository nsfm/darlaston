#!/usr/bin/env python3
"""Start the frozen build and make it prove it can reach its own resources.

A bundle that was never launched is a file, not a program. The failure
modes here all look identical from the build log -- a missing Qt platform
plugin, a data file left out of the collection, a hidden import the
analyser could not see -- and every one of them produces a clean build and
a dead launch. The wheel shipped without its fonts and icons once for
exactly this reason, and nobody noticed because everybody testing it had a
source checkout where the files are present whether packaged or not.

So this runs the real binary, in a subprocess, and reads what it says.
`--list-cameras` is used as the probe because it exercises the whole
import graph -- Qt, OpenCV, numpy, the SDK loader -- and then exits,
rather than opening a window and waiting for somebody to close it.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "pyi"
OUT = ROOT / "dist" / "release"


def binary() -> Path:
    """Where this platform's build put the executable."""
    if sys.platform == "darwin":
        app = DIST / "darlaston.app" / "Contents" / "MacOS" / "darlaston"
        if app.exists():
            return app
    name = "darlaston.exe" if sys.platform.startswith("win") else "darlaston"
    return DIST / "darlaston" / name


def _this_version(pattern: str) -> Path:
    """The artifact for the version in this checkout. Never another one.

    This used to take `sorted(glob(...))[0]`, which is the alphabetically
    first file rather than the one just built. On a CI runner that is
    harmless, because the machine starts empty and there is only ever one.
    On anybody's own machine it is not: build twice, and it verifies the
    older artifact and reports success -- so a broken build passes its own
    smoke test while an unrelated file sitting in `dist/release` takes the
    exam for it. Found by running the packaging chain locally for the
    first time, which is what `make package` exists for.

    Matching the version rather than taking the newest, because "newest"
    is still a guess and a mtime can be anything. If the artifact for this
    version is not there, that is worth saying out loud rather than
    quietly examining a neighbour.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from bundle import version           # the same spelling bundle writes

    want = version()
    found = [p for p in sorted(OUT.glob(pattern)) if want in p.name]
    if not found:
        others = ", ".join(p.name for p in sorted(OUT.glob(pattern))) or "nothing"
        raise SystemExit(
            f"no {pattern} for version {want} in {OUT} -- found {others}. "
            f"Build it first, or clear out what is stale.")
    if len(found) > 1:
        raise SystemExit(
            f"{len(found)} artifacts claim version {want}: "
            + ", ".join(p.name for p in found))
    return found[0]


def packaged() -> Path:
    """The executable inside the artifact a person would actually download.

    Tested separately from the PyInstaller output because the wrapping is
    its own opportunity to lose something: an AppImage with a broken
    AppRun, a disk image built from a staging directory that was missing a
    symlink, a zip written with the wrong root. All of those leave the
    directory build perfectly intact.
    """
    if sys.platform.startswith("linux"):
        found = _this_version("*.AppImage")
        found.chmod(0o755)
        return found

    if sys.platform == "darwin":
        found = _this_version("*.dmg")
        mount = ROOT / "build" / "mnt"
        mount.mkdir(parents=True, exist_ok=True)
        subprocess.run(("hdiutil", "attach", str(found), "-mountpoint",
                        str(mount), "-nobrowse", "-quiet"), check=True)
        return mount / "darlaston.app" / "Contents" / "MacOS" / "darlaston"

    found = _this_version("*.zip")
    unpacked = ROOT / "build" / "unzipped"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    with zipfile.ZipFile(found) as z:
        z.extractall(unpacked)
    return unpacked / "darlaston" / "darlaston.exe"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packaged", action="store_true",
                    help="test the finished artifact rather than the "
                         "directory PyInstaller produced")
    args = ap.parse_args()

    exe = packaged() if args.packaged else binary()
    if not exe.exists():
        print(f"no build at {exe}", file=sys.stderr)
        print("what is there:", file=sys.stderr)
        for p in sorted(DIST.rglob("*"))[:40]:
            print("   ", p.relative_to(DIST), file=sys.stderr)
        return 1

    print(f"running {exe}")
    try:
        done = subprocess.run([str(exe), "--list-cameras"],
                              capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print("the build hung on --list-cameras", file=sys.stderr)
        return 1

    print(done.stdout)
    if done.stderr.strip():
        print("stderr:", done.stderr, file=sys.stderr)
    if done.returncode != 0:
        print(f"exited {done.returncode}", file=sys.stderr)
        return 1

    # It has to say *something* about cameras. An empty run would pass a
    # bare returncode check while having done nothing at all.
    if "camera" not in done.stdout.lower() and "bus" not in done.stdout.lower():
        print("ran, but said nothing about cameras", file=sys.stderr)
        return 1

    # And it has to be able to build its interface, which is the part that
    # needs the bundled fonts and icons. Asked of the frozen binary rather
    # than of this interpreter, because this interpreter has the source
    # tree and would pass on files the bundle never received.
    print("--selftest:")
    try:
        done = subprocess.run([str(exe), "--selftest"],
                              capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print("the build hung on --selftest", file=sys.stderr)
        return 1
    print(done.stdout.rstrip())
    if done.returncode != 0:
        print(done.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
