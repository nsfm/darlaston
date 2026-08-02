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

Anything with no trailer counts as a patch, so the default is the safe one
and forgetting costs a smaller number rather than a wrong one. `Bump: none`
says this commit is not worth a release at all -- if every commit since the
last tag says that, there is nothing to cut.

Nobody types a version. The first release is FIRST_RELEASE below, and every
one after it is the previous tag plus whatever the commits asked for.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

#: Ordered weakest to strongest; the strongest trailer in the range wins.
#: "none" is weaker than everything: one commit worth releasing carries the
#: whole range, and a range of nothing but "none" is not a release.
LEVELS = ("none", "patch", "minor", "major")

#: What the first ever release is called. Baked in rather than asked for:
#: a version nobody types is a version nobody has to agree on.
FIRST_RELEASE = "0.1.0"

#: Matched on its own line, case-insensitively, the way git reads trailers.
TRAILER = re.compile(r"^\s*bump:\s*(major|minor|patch|none|skip)\s*$",
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
    """The strongest bump any of these commits asked for.

    A commit with no trailer is a patch, so a range is only "none" when
    every commit in it says so explicitly.
    """
    best = 0
    for message in messages:
        found = TRAILER.findall(message)
        asked = max((LEVELS.index("patch" if f.lower() == "skip"
                                  else f.lower()) for f in found),
                    default=LEVELS.index("patch"))
        if found and all(f.lower() in ("none", "skip") for f in found):
            asked = LEVELS.index("none")
        best = max(best, asked)
    return LEVELS[best]


def bump(version: tuple[int, int, int], level: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def next_version(initial: str | None = None) -> tuple[str | None, str, int]:
    """(tag, why, how many commits it covers). Tag is None if there is
    nothing worth releasing."""
    tag = latest_tag()
    messages = commits_since(tag)
    if not messages:
        return None, "nothing new since the last release", 0
    level = level_for(messages)
    if level == "none":
        return None, "nothing since the last release asked for a version", \
            len(messages)
    if tag is None:
        return (f"v{(initial or FIRST_RELEASE).lstrip('v')}",
                "first release", len(messages))
    current = tuple(int(g) for g in TAG.match(tag).groups())
    return ("v%d.%d.%d" % bump(current, level),
            f"{level} bump over {tag}", len(messages))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--initial", metavar="X.Y.Z",
                    help=f"override the first release, normally {FIRST_RELEASE}")
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
    if tag is None:
        return 2                      # nothing to release; not an error
    print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
