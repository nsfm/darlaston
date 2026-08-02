#!/usr/bin/env python3
"""The version of a commit, worked out from the history behind it.

Every commit has its own version, and it is a pure function of the commits
that precede it. Start at 0.0.0 and walk forward:

    [MAJOR] in the subject   ->  major + 1, minor and patch to 0
    [MINOR] in the subject   ->  minor + 1, patch to 0
    anything else            ->  patch + 1

So patches are not marked at all -- most commits are patches, and a
convention that most commits have to remember is a convention that gets
forgotten. Markers go at the front of the subject, where `git log
--oneline` shows them.

Two properties this buys, both deliberate:

  * **No two commits share a version.** A release is always exactly one
    commit, and "which build is this" has one answer.
  * **No hashes in the version.** Deriving from tags means everything
    between tags is a `1.2.1.dev50+g0c8a4f5`, which is a fine thing for a
    developer and a poor thing to hand a microscopist.

Tags are an output here, not an input: CI reads the history, works out the
version, and creates the tag to match. Nobody types a version number.

The older `Bump: minor` body trailer is still read, because commits
already in this history use it and dropping it would quietly change what
they meant. New commits use the subject marker.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

#: At the front of the subject, where `git log --oneline` shows it.
MARKER = re.compile(r"^\s*\[(major|minor)\]", re.IGNORECASE)

#: The older form, on its own line in the body. Still honoured.
TRAILER = re.compile(r"^\s*bump:\s*(major|minor)\s*$",
                     re.IGNORECASE | re.MULTILINE)

#: A version tag. Anything else in the tag namespace is ignored, so a tag
#: like `paper-figures` cannot be mistaken for a version.
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, capture_output=True, text=True,
                          check=True).stdout.strip()


def history(rev: str = "HEAD") -> list[str]:
    """Every commit message reaching `rev`, oldest first.

    `--first-parent` so a merged branch counts as the one commit that
    merged it. Without it a ten-commit branch adds ten patches on the day
    it lands and the version jumps for work that was already counted.
    """
    raw = _git("log", "--first-parent", "--reverse", "--format=%B%x00", rev)
    return [c.strip() for c in raw.split("\0") if c.strip()]


def level_of(message: str) -> str:
    """What one commit does to the version."""
    subject = message.strip().splitlines()[0] if message.strip() else ""
    found = MARKER.match(subject)
    if found:
        return found.group(1).lower()
    trailer = TRAILER.search(message)
    return trailer.group(1).lower() if trailer else "patch"


def version_at(rev: str = "HEAD") -> tuple[int, int, int]:
    """The version of `rev`, from the whole history behind it."""
    major = minor = patch = 0
    for message in history(rev):
        level = level_of(message)
        if level == "major":
            major, minor, patch = major + 1, 0, 0
        elif level == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
    return major, minor, patch


def tag_for(rev: str = "HEAD") -> str:
    return "v%d.%d.%d" % version_at(rev)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rev", nargs="?", default="HEAD",
                    help="commit to version, default HEAD")
    ap.add_argument("--explain", action="store_true",
                    help="say how it got there, on stderr")
    ap.add_argument("--bare", action="store_true",
                    help="print 1.2.3 rather than v1.2.3")
    args = ap.parse_args()

    try:
        messages = history(args.rev)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr or exc}", file=sys.stderr)
        return 1

    if args.explain:
        majors = sum(level_of(m) == "major" for m in messages)
        minors = sum(level_of(m) == "minor" for m in messages)
        print(f"{len(messages)} commits: {majors} major, {minors} minor, "
              f"{len(messages) - majors - minors} patch", file=sys.stderr)

    version = version_at(args.rev)
    print(("%d.%d.%d" if args.bare else "v%d.%d.%d") % version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
