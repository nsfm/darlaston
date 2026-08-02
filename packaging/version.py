#!/usr/bin/env python3
"""What the next release should be called, read out of the commit history.

The version itself comes from git tags via setuptools-scm -- tags are the
single source of truth and nothing is ever hand-edited. This decides what
the *next* tag should be, so cutting a release does not involve anybody
remembering whether the last few weeks were a patch or a minor.

**The convention is a git trailer**, not a subject-line prefix:

    Split setup into Microscopes and Cameras windows

    The stand and camera libraries are separate windows now.

    Bump: minor

`Bump: major`, `Bump: minor`, `Bump: patch`. Trailers were chosen over
Conventional Commits' `feat:`/`fix:` prefixes for one reason: this project
writes plain descriptive subjects, and a machine-readable prefix would put
the changelog's needs ahead of the reader's. A trailer sits at the bottom
of the body, is invisible in `git log --oneline`, and is a format git
itself already understands.

Anything with no trailer counts as a patch, so the default is the safe
one and forgetting costs a smaller number rather than a wrong one.

Before the first tag exists everything is measured from 0.0.0, which makes
the first release 0.0.1 unless a commit asks for more. Pass --initial to
override that for the first cut.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

#: Ordered weakest to strongest; the strongest trailer in the range wins.
LEVELS = ("patch", "minor", "major")

#: Matched on its own line, case-insensitively, the way git reads trailers.
TRAILER = re.compile(r"^\s*bump:\s*(major|minor|patch)\s*$",
                     re.IGNORECASE | re.MULTILINE)

#: A release tag. Anything else in the tag namespace is ignored, so a tag
#: like `paper-figures` cannot be mistaken for a version.
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, capture_output=True, text=True,
                          check=True).stdout.strip()


def latest_tag() -> str | None:
    """The newest release tag by version order, or None before the first.

    Sorted by `version:refname` rather than by date: a tag applied late to
    an old commit must not be able to claim it is the latest release.
    """
    tags = [t for t in _git("tag", "--sort=-version:refname").splitlines()
            if TAG.match(t)]
    return tags[0] if tags else None


def commits_since(tag: str | None) -> list[str]:
    """Full commit messages since `tag`, newest first."""
    span = f"{tag}..HEAD" if tag else "HEAD"
    raw = _git("log", span, "--format=%B%x00")
    return [c.strip() for c in raw.split("\0") if c.strip()]


def level_for(messages: list[str]) -> str:
    """The strongest bump any of these commits asked for."""
    best = 0
    for message in messages:
        for found in TRAILER.findall(message):
            best = max(best, LEVELS.index(found.lower()))
    return LEVELS[best]


def bump(version: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def next_version(initial: str | None = None) -> tuple[str, str, int]:
    """(tag, why, how many commits it covers)."""
    tag = latest_tag()
    messages = commits_since(tag)
    if not messages:
        return (tag or "v0.0.0"), "nothing new since the last release", 0
    level = level_for(messages)
    if tag is None and initial:
        return f"v{initial.lstrip('v')}", "first release", len(messages)
    current = (0, 0, 0) if tag is None else tuple(
        int(g) for g in TAG.match(tag).groups())
    return ("v%d.%d.%d" % bump(current, level),
            f"{level} bump over {tag or 'nothing'}", len(messages))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--initial", metavar="X.Y.Z",
                    help="version to use when no release tag exists yet")
    ap.add_argument("--explain", action="store_true",
                    help="say why, on stderr, as well as printing the tag")
    args = ap.parse_args()

    try:
        tag, why, count = next_version(args.initial)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr or exc}", file=sys.stderr)
        return 1

    if args.explain:
        print(f"{why}, covering {count} commit(s)", file=sys.stderr)
    print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
