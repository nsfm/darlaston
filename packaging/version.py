#!/usr/bin/env python3
"""What the next release should be called, read out of the commit history.

The version itself comes from git tags via setuptools-scm -- tags are the
single source of truth and nothing is ever hand-edited. This decides what
the *next* tag should be, so cutting a release does not involve anybody
remembering whether the last few weeks were a patch or a minor.

**The convention is a marker at the front of the subject:**

    [MINOR] Split setup into Microscopes and Cameras windows

`[MAJOR]`, `[MINOR]`, `[PATCH]`, `[NONE]`, case-insensitive. It sits where
`git log --oneline` shows it, so what a commit did to the version is
visible while reading the history rather than only to this script.

Anything unmarked counts as a patch, so the default is the safe one and
forgetting costs a smaller number rather than a wrong one. `[NONE]` says
this commit is not worth a release; if every commit since the last tag says
that, there is nothing to cut.

A `Bump: minor` trailer in the body means the same thing and is still read.
Commits already in this history use it, and dropping support would quietly
change what they meant.

Nobody types a version. The first release is 0.0.0 plus whatever the
commits asked for -- so one `[MAJOR]` in the range makes it 1.0.0 -- and
every release after that is the previous tag plus the same.
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

#: At the front of the subject, where `git log --oneline` shows it.
MARKER = re.compile(r"^\s*\[(major|minor|patch|none|skip)\]", re.IGNORECASE)

#: The older form, on its own line in the body, the way git reads trailers.
#: Still honoured: commits already in this history use it.
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
        subject = message.strip().splitlines()[0] if message.strip() else ""
        found = [m.group(1) for m in [MARKER.match(subject)] if m]
        found += TRAILER.findall(message)
        found = [f.lower() for f in found]
        if not found:
            asked = LEVELS.index("patch")          # unmarked is a patch
        elif all(f in ("none", "skip") for f in found):
            asked = LEVELS.index("none")
        else:
            asked = max(LEVELS.index(f) for f in found
                        if f not in ("none", "skip"))
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
        if initial:
            return f"v{initial.lstrip('v')}", "first release", len(messages)
        # From nothing, by the same rule as everything else: a [MAJOR] in
        # the range makes the first release 1.0.0, a [MINOR] makes it
        # 0.1.0. No constant to keep in step with anybody's intent.
        return ("v%d.%d.%d" % bump((0, 0, 0), level),
                f"first release, {level}", len(messages))
    current = tuple(int(g) for g in TAG.match(tag).groups())
    return ("v%d.%d.%d" % bump(current, level),
            f"{level} bump over {tag}", len(messages))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--initial", metavar="X.Y.Z",
                    help="override the first release entirely")
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
