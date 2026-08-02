"""The catalogue mechanism, and the checks that keep it honest.

The last test in this file is the important one: it fails when the code
names a key the catalogue does not define. Without it, a typo in a key is
invisible until somebody opens that dialog, and what they see is the key.
"""
from pathlib import Path

import pytest

from darlaston.i18n.catalogue import Catalogue, CatalogueError

PACKAGE = Path(__file__).resolve().parent.parent / "darlaston"

HEADER = '''msgid ""
msgstr ""
"Language: xx\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

'''


def parse(body: str) -> Catalogue:
    return Catalogue.parse(HEADER + body, "xx")


def test_a_key_carries_its_words():
    cat = parse('msgid "a.b.c"\nmsgstr "Some words"\n')
    assert cat.text("a.b.c") == "Some words"
    assert "a.b.c" in cat


def test_words_can_run_across_several_lines():
    """Long tooltips are written as a run of quoted lines, and the
    newlines inside them are layout that has to survive."""
    cat = parse('msgid "a.tip"\n'
                'msgstr ""\n'
                '"First line.\\n"\n'
                '"Second line."\n')
    assert cat.text("a.tip") == "First line.\nSecond line."


def test_placeholders_are_named_so_a_translator_can_move_them():
    cat = parse('msgid "a.count"\nmsgstr "{n} of {total}"\n')
    assert cat.text("a.count", n=2, total=4) == "2 of 4"
    # Word order is a translator's to change, and named placeholders are
    # what makes that possible. Positional ones could not be reordered.
    other = parse('msgid "a.count"\nmsgstr "{total} contains {n}"\n')
    assert other.text("a.count", n=2, total=4) == "4 contains 2"


def test_plurals_come_from_the_catalogue_not_from_an_if():
    cat = parse('msgid "a.slice"\n'
                'msgid_plural "a.slice"\n'
                'msgstr[0] "{count} slice"\n'
                'msgstr[1] "{count} slices"\n')
    assert cat.plural("a.slice", 1) == "1 slice"
    assert cat.plural("a.slice", 0) == "0 slices"
    assert cat.plural("a.slice", 7) == "7 slices"


def test_a_language_with_one_form_gets_one_form():
    cat = Catalogue.parse(
        'msgid ""\nmsgstr ""\n"Language: ja\\n"\n'
        '"Plural-Forms: nplurals=1; plural=0;\\n"\n\n'
        'msgid "a.slice"\nmsgid_plural "a.slice"\n'
        'msgstr[0] "{count} slices, always"\n', "ja")
    assert cat.plural("a.slice", 1) == "1 slices, always"
    assert cat.plural("a.slice", 9) == "9 slices, always"


def test_a_missing_key_says_which_key():
    """It is the only useful thing it can say. With symbolic keys there is
    no readable fallback, so silence here puts `a.b.c` on the screen."""
    cat = parse('msgid "a.b.c"\nmsgstr "Words"\n')
    with pytest.raises(CatalogueError, match="a.b.missing"):
        cat.text("a.b.missing")


def test_a_missing_placeholder_says_which_key_wanted_it():
    """str.format would name only the placeholder, which in a catalogue of
    hundreds is not enough to find it by."""
    cat = parse('msgid "a.b"\nmsgstr "{here} and {there}"\n')
    with pytest.raises(CatalogueError, match="a.b"):
        cat.text("a.b", here="this")


def test_a_catalogue_that_half_parses_is_refused():
    """Worse than one that fails: the failure is a stack trace and the half
    is a window with keys written on it."""
    for broken, why in (
        ('msgid "a.b\nmsgstr "x"\n', "unterminated quote"),
        ('msgstr "orphan"\n', "msgstr before msgid"),
        ('msgid "a.b"\nmsgstr "\\q"\n', "unknown escape"),
        ('nonsense\n', "not PO at all"),
    ):
        with pytest.raises(CatalogueError):
            parse(broken)


def test_an_unknown_plural_rule_is_refused_rather_than_guessed():
    """Falling back to English plurals would be wrong in a way nobody
    would notice until a speaker of that language complained."""
    with pytest.raises(CatalogueError, match="Plural-Forms"):
        Catalogue.parse(
            'msgid ""\nmsgstr ""\n"Language: ar\\n"\n'
            '"Plural-Forms: nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : 5);\\n"\n',
            "ar")


def test_the_machine_preference_is_read_the_way_gettext_reads_it(monkeypatch,
                                                                 tmp_path):
    from darlaston.i18n.catalogue import set_language

    for lang in ("en", "de"):
        folder = tmp_path / lang
        folder.mkdir()
        (folder / "words.po").write_text(HEADER.replace("xx", lang)
                                         + 'msgid "a.b"\nmsgstr "x"\n')

    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert set_language(directory=tmp_path).language == "de"

    # A region we have no catalogue for falls back to its base language.
    monkeypatch.setenv("LC_ALL", "de_AT.UTF-8")
    assert set_language(directory=tmp_path).language == "de"

    # And a language we have nothing for falls back to the source, quietly.
    monkeypatch.setenv("LC_ALL", "fi_FI.UTF-8")
    assert set_language(directory=tmp_path).language == "en"

    # LANGUAGE wins, and carries a list.
    monkeypatch.setenv("LANGUAGE", "fi:de")
    assert set_language(directory=tmp_path).language == "de"


def test_a_key_defined_twice_is_refused(tmp_path):
    """Last-one-wins would mean the entry you got depended on the order the
    directory happened to list, and the two definitions would differ."""
    folder = tmp_path / "en"
    folder.mkdir()
    (folder / "one.po").write_text(HEADER.replace("xx", "en")
                                   + 'msgid "a.b"\nmsgstr "first"\n')
    (folder / "two.po").write_text(HEADER.replace("xx", "en")
                                   + 'msgid "a.b"\nmsgstr "second"\n')
    with pytest.raises(CatalogueError, match="a.b"):
        Catalogue.load("en", tmp_path)


def test_every_key_the_code_names_exists_in_the_catalogue():
    """The check that makes the whole scheme safe.

    A typo in a key is otherwise invisible until somebody opens that
    dialog, and what they see is the key.
    """
    from darlaston.i18n.catalogue import missing_keys

    english = Catalogue.load("en")
    missing = missing_keys(PACKAGE, english)
    assert not missing, (
        f"{len(missing)} key(s) named in the code with no entry in the "
        f"English catalogue: {sorted(missing)[:10]}")


def test_the_catalogue_carries_nothing_nobody_asks_for():
    """A rename that half happened leaves the old key behind, and it reads
    as a string somebody forgot to use rather than one that moved."""
    from darlaston.i18n.catalogue import unused_keys

    english = Catalogue.load("en")
    stale = unused_keys(PACKAGE, english)
    assert not stale, (
        f"{len(stale)} entr(ies) in the English catalogue that nothing "
        f"names: {sorted(stale)[:10]}")
