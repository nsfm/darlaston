#!/usr/bin/env python3
"""Release notes, from the commits since the previous release.

Written rather than hand-maintained because a changelog nobody updates is
worse than no changelog: it goes stale silently and then misleads. The
commit subjects in this project are already plain descriptions of what
changed, which is what a release note is, so they are reused directly
instead of being duplicated into a second file.

Grouped by what each commit did to the version -- see packaging/version.py
-- so anything that moved the major or minor number appears first, where
somebody deciding whether to upgrade will read it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Run as `python packaging/notes.py` from the repo root, so its sibling is
# not on the path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from version import MARKER, TAG, level_of, tag_for        # noqa: E402

#: Most subjects listed under one heading.
MOST = 25


def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, capture_output=True, text=True,
                          check=True).stdout.strip()


def previous_tag(current: str) -> str | None:
    """The newest release tag that is not the one being released now."""
    tags = [t for t in _git("tag", "--sort=-version:refname").splitlines()
            if TAG.match(t) and t != current]
    return tags[0] if tags else None


def main() -> int:
    try:
        this = tag_for("HEAD")
        previous = previous_tag(this)
        span = f"{previous}..HEAD" if previous else "HEAD"
        raw = _git("log", "--first-parent", span, "--format=%B%x00")
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc}", file=sys.stderr)
        return 1

    messages = [m.strip() for m in raw.split("\0") if m.strip()]
    buckets: dict[str, list[str]] = {"major": [], "minor": [], "patch": []}
    for message in messages:
        subject = message.splitlines()[0].strip()
        if subject.startswith("Merge "):
            continue
        # The marker did its job when the version was worked out. Here it
        # is machinery showing through at the one person the notes are
        # for, who is deciding whether to download something.
        buckets[level_of(message)].append(MARKER.sub("", subject).strip())

    print(f"## darlaston {this}\n")
    for key, heading in (("major", "Breaking"), ("minor", "New"),
                         ("patch", "Fixed and changed")):
        if not buckets[key]:
            continue
        print(f"### {heading}\n")
        # Capped, because the first release covers the whole history and a
        # hundred-odd subjects is a wall rather than a summary. What is cut
        # is said out loud: a list that silently stops is worse than a
        # short one.
        shown = buckets[key][:MOST]
        for subject in shown:
            print(f"- {subject}")
        if len(buckets[key]) > len(shown):
            print(f"- ...and {len(buckets[key]) - len(shown)} more")
        print()

    print("### Downloads\n")
    print("- **Linux**: the `.AppImage`. `chmod +x` it and run it.")
    print("- **macOS**: the `arm64` `.dmg`, for Apple silicon. Intel Macs "
          "are not built here yet -- open an issue if you have one.")
    print("- **Windows**: the `.zip`. Unpack it and run `darlaston.exe`.")
    print()
    print("These builds are unsigned, so macOS and Windows will warn about "
          "them. The disk image carries a note explaining what to click, "
          "and the same text is in `packaging/NOTARISING.md`.")
    print()
    print("The camera SDK is never bundled -- it ships with no licence of "
          "any kind, so darlaston loads yours at runtime. See `SUPPORT.md`.")

    if previous:
        print(f"\n<sub>{len(messages)} commits since {previous}.</sub>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
