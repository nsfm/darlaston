#!/usr/bin/env python3
"""Release notes, from the commits since the previous release.

Written rather than hand-maintained because a changelog nobody updates is
worse than no changelog: it goes stale silently and then misleads. The
commit subjects in this project are already written as plain descriptions
of what changed, which is exactly what a release note is, so they are
reused directly instead of being duplicated into a second file.

Commits are grouped by the `Bump:` trailer that decided the version -- see
packaging/version.py -- so anything called out as a minor or major change
appears first, where somebody deciding whether to upgrade will read it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Run as `python packaging/notes.py` from the repo root, so its sibling is
# not on the path by default.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from version import TAG, TRAILER, commits_since        # noqa: E402


def _subject(message: str) -> str:
    return message.strip().splitlines()[0].strip()


def _level(message: str) -> str:
    found = TRAILER.findall(message)
    return found[0].lower() if found else "patch"


def main() -> int:
    try:
        tags = [t for t in subprocess.run(
            ("git", "tag", "--sort=-version:refname"),
            capture_output=True, text=True, check=True).stdout.splitlines()
            if TAG.match(t)]
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc}", file=sys.stderr)
        return 1

    # The tag being released is HEAD's; the range is from the one before.
    this = tags[0] if tags else None
    previous = tags[1] if len(tags) > 1 else None
    messages = commits_since(previous)

    buckets: dict[str, list[str]] = {"major": [], "minor": [], "patch": []}
    for message in messages:
        subject = _subject(message)
        if subject.startswith("Merge "):
            continue
        buckets[_level(message)].append(subject)

    print(f"## darlaston {this or 'unreleased'}\n")
    headings = (("major", "Breaking"), ("minor", "New"), ("patch", "Fixed and changed"))
    for key, heading in headings:
        if not buckets[key]:
            continue
        print(f"### {heading}\n")
        for subject in buckets[key]:
            print(f"- {subject}")
        print()

    print("### Downloads\n")
    print("- **Linux**: the `.AppImage`. `chmod +x` it and run it.")
    print("- **macOS**: the `.dmg` for your processor -- `arm64` for Apple "
          "silicon, `x86_64` for Intel.")
    print("- **Windows**: the `.zip`. Unpack it and run `darlaston.exe`.")
    print()
    print("These builds are unsigned, so macOS and Windows will warn about "
          "them. The disk image contains a note explaining what to click, "
          "and the same text is in `packaging/NOTARISING.md`.")
    print()
    print("The camera SDK is never bundled -- it ships with no licence of "
          "any kind, so darlaston loads yours at runtime. See `SUPPORT.md`.")

    if previous:
        print(f"\n<sub>{len(messages)} commits since {previous}.</sub>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
