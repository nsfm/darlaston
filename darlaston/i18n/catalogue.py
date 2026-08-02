"""Reading a `.po` catalogue, and choosing which one.

Small on purpose. This parses the subset of the PO format the project
actually uses -- `msgid`, `msgstr`, `msgid_plural`, `msgstr[n]`, comments,
and the header's `Plural-Forms` -- and refuses anything it does not
understand rather than guessing. A catalogue that half-loads is worse than
one that fails, because the failure is a stack trace and the half is a
window with `setup.scope.name` written on it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

LOCALE_DIR = Path(__file__).parent / "locale"

#: The language used when nothing else is available, and the one the keys
#: are written against. Always loaded, because a symbolic key has no
#: readable fallback -- see the package docstring.
SOURCE = "en"

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class CatalogueError(Exception):
    """A catalogue could not be read, and guessing would be worse."""


def _unquote(text: str, where: str) -> str:
    """The contents of a PO quoted string, with escapes resolved."""
    text = text.strip()
    if len(text) < 2 or not text.startswith('"') or not text.endswith('"'):
        raise CatalogueError(f"{where}: expected a quoted string, got {text!r}")
    out, i, body = [], 0, text[1:-1]
    while i < len(body):
        char = body[i]
        if char == "\\":
            i += 1
            if i >= len(body):
                raise CatalogueError(f"{where}: string ends in a backslash")
            escape = body[i]
            if escape not in _ESCAPES:
                raise CatalogueError(f"{where}: unknown escape \\{escape}")
            out.append(_ESCAPES[escape])
        else:
            out.append(char)
        i += 1
    return "".join(out)


class Catalogue:
    """One language's words, by key."""

    def __init__(self, language: str, singular: dict[str, str],
                 plurals: dict[str, list[str]], header: dict[str, str]) -> None:
        self.language = language
        self._singular = singular
        self._plurals = plurals
        self.header = header
        self._nplurals, self._rule = _plural_rule(header, language)

    # ---- reading ---------------------------------------------------------

    @classmethod
    def load(cls, language: str, directory: Path | None = None) -> "Catalogue":
        path = (directory or LOCALE_DIR) / f"{language}.po"
        if not path.exists():
            raise CatalogueError(f"no catalogue for {language!r} at {path}")
        return cls.parse(path.read_text(encoding="utf-8"), language, str(path))

    @classmethod
    def parse(cls, text: str, language: str, where: str = "<string>"
              ) -> "Catalogue":
        singular: dict[str, str] = {}
        plurals: dict[str, list[str]] = {}
        header: dict[str, str] = {}

        key = None
        plural_key = None
        target: list[str] | None = None
        forms: dict[int, list[str]] = {}

        def finish() -> None:
            nonlocal key, plural_key, target, forms
            if key is None:
                return
            if plural_key is not None:
                plurals[key] = ["".join(forms[i]) for i in sorted(forms)]
            else:
                singular[key] = "".join(target or [])
            key = plural_key = target = None
            forms = {}

        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            spot = f"{where}:{number}"
            if not line or line.startswith("#"):
                if not line:
                    finish()
                continue
            if line.startswith("msgid_plural"):
                if key is None:
                    raise CatalogueError(f"{spot}: msgid_plural before msgid")
                plural_key = _unquote(line[len("msgid_plural"):], spot)
                target = None
                continue
            if line.startswith("msgid"):
                finish()
                key = _unquote(line[len("msgid"):], spot)
                target = []
                continue
            if line.startswith("msgstr["):
                close = line.index("]")
                index = int(line[len("msgstr["):close])
                forms[index] = [_unquote(line[close + 1:], spot)]
                target = forms[index]
                continue
            if line.startswith("msgstr"):
                target = [_unquote(line[len("msgstr"):], spot)]
                if plural_key is None:
                    if key is None:
                        raise CatalogueError(f"{spot}: msgstr before msgid")
                    singular[key] = ""      # replaced by finish()
                continue
            if line.startswith('"'):
                if target is None:
                    raise CatalogueError(f"{spot}: continuation with nothing "
                                         f"to continue")
                target.append(_unquote(line, spot))
                continue
            raise CatalogueError(f"{spot}: cannot read {line!r}")
        finish()

        # The header is the entry with an empty key, in RFC822 style.
        for field in singular.pop("", "").splitlines():
            if ":" in field:
                name, _, value = field.partition(":")
                header[name.strip()] = value.strip()
        return cls(language, singular, plurals, header)

    # ---- using -----------------------------------------------------------

    def __contains__(self, key: str) -> bool:
        return key in self._singular or key in self._plurals

    def keys(self) -> set[str]:
        return set(self._singular) | set(self._plurals)

    def text(self, key: str, **params: object) -> str:
        try:
            words = self._singular[key]
        except KeyError:
            raise CatalogueError(
                f"no entry for {key!r} in the {self.language} catalogue"
            ) from None
        return _fill(words, key, params)

    def plural(self, key: str, count: int, **params: object) -> str:
        try:
            forms = self._plurals[key]
        except KeyError:
            raise CatalogueError(
                f"no plural entry for {key!r} in the {self.language} catalogue"
            ) from None
        index = min(self._rule(count), len(forms) - 1)
        return _fill(forms[index], key, {"count": count, **params})


def _fill(words: str, key: str, params: dict) -> str:
    """Substitute named placeholders, and say which key was wrong if not.

    A KeyError from str.format here would name the *placeholder* and
    nothing else, which in a program with 282 of these is not enough to
    find it by.
    """
    if not params and "{" not in words:
        return words
    try:
        return words.format(**params)
    except (KeyError, IndexError) as exc:
        raise CatalogueError(
            f"{key!r} wants a placeholder that was not given: {exc}") from None


#: Plural-Forms expressions are a tiny C-like language. Rather than
#: evaluate arbitrary C, the handful that real languages use are matched
#: and mapped to Python. Anything else is refused, loudly, instead of
#: being silently treated as English.
_RULES: dict[str, tuple[int, object]] = {
    "nplurals=1; plural=0;": (1, lambda n: 0),
    "nplurals=2; plural=(n != 1);": (2, lambda n: int(n != 1)),
    "nplurals=2; plural=(n > 1);": (2, lambda n: int(n > 1)),
}


def _plural_rule(header: dict[str, str], language: str):
    raw = header.get("Plural-Forms", "").strip()
    if not raw:
        return _RULES["nplurals=2; plural=(n != 1);"]
    squashed = re.sub(r"\s+", " ", raw).strip()
    if squashed in _RULES:
        return _RULES[squashed]
    raise CatalogueError(
        f"the {language} catalogue uses a Plural-Forms rule this does not "
        f"know: {raw!r}. Add it to _RULES rather than letting it fall back "
        f"to English, which would be wrong in a way nobody would notice.")


# ---- choosing ------------------------------------------------------------

def available_languages(directory: Path | None = None) -> list[str]:
    where = directory or LOCALE_DIR
    return sorted(p.stem for p in where.glob("*.po"))


def _preferred() -> list[str]:
    """Languages this machine asks for, best first.

    LANGUAGE, then LC_ALL, LC_MESSAGES and LANG, which is the order
    gettext itself uses. `en_GB.UTF-8` contributes both `en_GB` and `en`.
    """
    wanted: list[str] = []
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        for piece in value.split(":"):
            tag = piece.split(".")[0].split("@")[0].strip()
            if not tag or tag in ("C", "POSIX"):
                continue
            if tag not in wanted:
                wanted.append(tag)
            base = tag.split("_")[0]
            if base and base not in wanted:
                wanted.append(base)
    return wanted


def set_language(language: str | None = None,
                 directory: Path | None = None) -> Catalogue:
    """Load a catalogue: the one asked for, the machine's, or the source.

    Falling back to the source language is not a failure worth reporting
    to the person using the program -- most of the world's locales will
    never have a catalogue here, and English is a working answer.
    """
    have = available_languages(directory)
    for candidate in ([language] if language else _preferred()):
        if candidate in have:
            return Catalogue.load(candidate, directory)
    return Catalogue.load(SOURCE, directory)


# ---- checking ------------------------------------------------------------

#: Matches `_("area.thing.role")` and `n_("area.thing.role", ...)`, which
#: is how the code names a string. A regex over the source rather than an
#: import of every module, because this has to run in a test without
#: building a QApplication or opening a camera.
#:
#: At least one dot is required. Without it this also matched ordinary
#: single-word arguments to any function ending in `_(`, and reported them
#: as keys that were missing from the catalogue.
_CALL = re.compile(r"""(?:^|[^\w.])n?_\(\s*["']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["']""",
                   re.MULTILINE)


def keys_in_use(root: Path) -> dict[str, set[str]]:
    """Every key the code names, and which files name it.

    The i18n package itself is skipped: it is the mechanism rather than a
    consumer, and its docstrings carry example keys that are not real
    call sites.
    """
    found: dict[str, set[str]] = {}
    # Resolved on both sides: `root` is usually relative and __file__ is
    # not, so comparing them raw never matched and the mechanism scanned
    # its own examples.
    here = Path(__file__).resolve().parent
    for path in sorted(root.rglob("*.py")):
        if path.resolve().parent == here:
            continue
        for key in _CALL.findall(path.read_text(encoding="utf-8")):
            found.setdefault(key, set()).add(str(path))
    return found


def missing_keys(root: Path, catalogue: Catalogue) -> set[str]:
    """Named by the code, absent from the catalogue. Always a bug."""
    return set(keys_in_use(root)) - catalogue.keys()


def unused_keys(root: Path, catalogue: Catalogue) -> set[str]:
    """In the catalogue, named by nothing. Usually a rename that half
    happened, occasionally a string reached in a way the regex cannot
    see."""
    return catalogue.keys() - set(keys_in_use(root))
