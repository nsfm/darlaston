"""Is there a newer darlaston than this one?

Deliberately only that. Nothing here downloads anything, installs
anything, or decides when to ask -- those are separate questions with
separate answers, and at least one of them (does this program make a
network request at all, and by default?) belongs to the person whose
name is on it rather than to the code.

What this does is compare two version strings correctly and, given
permission from a caller, ask GitHub what the latest release is.

The comparison is the part worth writing carefully. Our versions come
from `packaging/version.py`, which walks the commit history, so a
release is a plain `0.8.0` and a build from between releases is a
`0.8.0.dev116+g7340561.d20260802`. Sorting those as strings gets
`0.10.0 < 0.9.0`, which is the single most common version bug there is.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

#: Where releases are published. Read from here rather than from the
#: repository so a moved fork does not silently keep pointing at ours.
RELEASES_API = "https://api.github.com/repos/nsfm/darlaston/releases/latest"
RELEASES_PAGE = "https://github.com/nsfm/darlaston/releases/latest"

#: Leading `v` is a tag convention, not part of the version. The three
#: numbers are all that ordering depends on; anything after them marks a
#: build that is *between* releases and is handled separately.
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$")


@dataclass(frozen=True)
class Version:
    """A version that can be compared with another one."""

    major: int = 0
    minor: int = 0
    patch: int = 0
    #: True for a build made after a release and before the next one --
    #: `0.8.0.dev116+g7340561`. It is *ahead* of 0.8.0, not behind it,
    #: which matters because otherwise every developer build is told to
    #: downgrade to the release it was built after.
    development: bool = False

    @property
    def release(self) -> tuple[int, int, int]:
        return self.major, self.minor, self.patch

    def __lt__(self, other: "Version") -> bool:
        if self.release != other.release:
            return self.release < other.release
        # Same three numbers: a dev build is later than the release.
        return self.development < other.development

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        return text + ".dev" if self.development else text


def parse(text: str) -> Version | None:
    """A version, or None if it is not one.

    None rather than an exception, and never a partial guess: everything
    that calls this is deciding whether to bother somebody about an
    upgrade, and the right answer to "I cannot tell" is silence.
    """
    if not text:
        return None
    found = _VERSION.match(text.strip())
    if not found:
        return None
    major, minor, patch, rest = found.groups()
    return Version(int(major), int(minor), int(patch),
                   development=bool(rest.strip(".")))


def newer(current: str, latest: str) -> Version | None:
    """The later version, if `latest` really is later than `current`.

    None when it is not, when either is unreadable, or when they are the
    same. Only a genuine upgrade is worth a word.
    """
    here, there = parse(current), parse(latest)
    if here is None or there is None:
        return None
    if here.release == (0, 0, 0):
        # We do not know what we are. `darlaston.__version__` falls back
        # to "0.0.0+unknown" when the package metadata is missing, which
        # parses perfectly well and is below every real release -- so
        # this would nag somebody about an upgrade on every launch on the
        # strength of not knowing what they already have. The history
        # starts at 0.0.0 and the first commit leaves it, so a real build
        # is never this.
        return None
    return there if here < there else None


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    page: str
    notes: str = ""


def latest_release(timeout: float = 5.0, url: str = RELEASES_API,
                   opener=urllib.request.urlopen) -> Release | None:
    """Ask GitHub what the newest published release is.

    **Callers decide whether this is ever called.** It makes a network
    request, and a program that quietly talks to a server on startup
    should do so because somebody chose that, not because a library
    happened to offer it.

    Returns None for anything that goes wrong -- offline, rate-limited,
    a proxy in the way, a response that is not what we expected. Failing
    to check for an update is not a problem worth reporting: the
    application works exactly as well either way, and an error box about
    a background check is worse than no check.

    `opener` is injected so the parsing can be tested without a network.
    """
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json",
                          "User-Agent": "darlaston"})
        with opener(request, timeout=timeout) as answer:
            body = json.loads(answer.read().decode("utf-8"))
        tag = str(body.get("tag_name") or "")
        version = parse(tag)
        if version is None:
            return None
        return Release(version=version, tag=tag,
                       page=str(body.get("html_url") or RELEASES_PAGE),
                       notes=str(body.get("body") or ""))
    except Exception:
        return None
