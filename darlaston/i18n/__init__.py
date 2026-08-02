"""Every word a person reads, in one place per language.

The rule is that no user-facing string is written inline. Code names a
key; the words live in a catalogue.

    self.button.setText(_("calib.flat.action.collect"))

**Symbolic keys, not the English text.** gettext's own convention is to
use the source sentence as the identifier, and that is wrong for a project
whose English is still moving: rewording "Collect" invalidates every
translation of it, because the identifier changed. A key decouples them --
English becomes a value you can edit as freely as any other translation,
and nothing downstream notices.

The cost of that choice, stated plainly because it changes how this must
be built: there is no readable fallback. If a key is missing, gettext
hands back the key itself, and the interface says `calib.flat.action` at
somebody. So English is a real catalogue that is always loaded, and a test
fails when the code names a key the catalogue does not define, or the
catalogue defines one nothing uses.

**Format is GNU gettext `.po`.** Chosen because it is what every
translation tool already speaks -- Weblate, Crowdin, Poedit -- because
plural rules are part of the format rather than something invented here,
and because `gettext` is in the standard library, so this adds nothing to
the three runtime dependencies.

`.po` is read directly rather than compiled to `.mo`. The usual reason to
compile is speed on catalogues far larger than this one, and it would put
the `msgfmt` binary in the build path of three platforms to save a few
milliseconds once at startup. The file that ships is then also the file a
translator edits, which is one less thing to get out of step.
"""
from __future__ import annotations

from .catalogue import (Catalogue, available_languages, keys_in_use,
                        missing_keys, set_language, unused_keys)

__all__ = ["_", "n_", "Catalogue", "set_language", "available_languages",
           "keys_in_use", "missing_keys", "unused_keys"]

#: The active catalogue. Replaced by `set_language`; never None, because a
#: program with no words is not a state worth supporting.
_active: Catalogue | None = None


def _current() -> Catalogue:
    global _active
    if _active is None:
        _active = set_language()
    return _active


def _(key: str, **params: object) -> str:
    """The words for `key`, with any named placeholders filled in.

    Placeholders are named rather than positional -- `{count}` rather than
    `{}` -- because word order changes between languages and a translator
    has to be able to move them.
    """
    return _current().text(key, **params)


def n_(key: str, count: int, **params: object) -> str:
    """The plural-aware form of `key`, chosen by `count`.

    English has two forms and several languages have more, so the choice
    belongs to the catalogue rather than to an `if` at the call site.
    `count` is passed through as a placeholder as well, so a message can
    say the number without being handed it twice.
    """
    return _current().plural(key, count, **params)
