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


def packaged() -> Path:
    """The executable inside the artifact a person would actually download.

    Tested separately from the PyInstaller output because the wrapping is
    its own opportunity to lose something: an AppImage with a broken
    AppRun, a disk image built from a staging directory that was missing a
    symlink, a zip written with the wrong root. All of those leave the
    directory build perfectly intact.
    """
    if sys.platform.startswith("linux"):
        found = sorted(OUT.glob("*.AppImage"))
        if not found:
            raise SystemExit(f"no AppImage in {OUT}")
        found[0].chmod(0o755)
        return found[0]

    if sys.platform == "darwin":
        found = sorted(OUT.glob("*.dmg"))
        if not found:
            raise SystemExit(f"no disk image in {OUT}")
        mount = ROOT / "build" / "mnt"
        mount.mkdir(parents=True, exist_ok=True)
        subprocess.run(("hdiutil", "attach", str(found[0]), "-mountpoint",
                        str(mount), "-nobrowse", "-quiet"), check=True)
        return mount / "darlaston.app" / "Contents" / "MacOS" / "darlaston"

    found = sorted(OUT.glob("*.zip"))
    if not found:
        raise SystemExit(f"no zip in {OUT}")
    unpacked = ROOT / "build" / "unzipped"
    if unpacked.exists():
        shutil.rmtree(unpacked)
    with zipfile.ZipFile(found[0]) as z:
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
