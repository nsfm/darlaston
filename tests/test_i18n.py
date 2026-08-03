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


def test_the_platform_icons_match_the_mark_that_is_drawn(qapp):
    """The dock icon, the taskbar icon and the window icon are one design,
    so they are one piece of code.

    Committed rather than generated at build time, because PyInstaller
    wants a path before anything of ours runs. That makes drift possible,
    which is what this catches: change the mark without regenerating and
    the shipped app disagrees with the running one.

    Compared as pixels rather than bytes -- see make_icons -- because two
    processors take different paths through Qt's raster engine and
    disagree in the last bit of a few antialiased edges.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [sys.executable, str(root / "packaging" / "make_icons.py"), "--check"],
        capture_output=True, text=True, cwd=root,
        env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen",
             "PYTHONPATH": str(root)})
    # stdout carries the measured difference whether it passed or not,
    # which is how anybody knows the tolerance is still the right size.
    assert done.returncode == 0, (
        f"the icon files no longer match the mark:\n"
        f"{done.stdout}\n{done.stderr}")


def test_everything_the_build_reads_is_actually_in_the_repository():
    """A packaging input that exists only on the machine that made it is
    not an input, and the failure is a build that works for one person.

    This happened: `.gitignore` excludes `*.png` for photographs, which
    silently swallowed the mark, and the AppImage step referenced a file
    no clone had. `git add` says so and it is easy to miss; a build
    machine says so three minutes later and it is not.
    """
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    needed = ["packaging/icons/darlaston.png",     # the AppImage icon
              "packaging/icons/darlaston.ico",     # Windows
              "packaging/icons/darlaston.icns"]    # macOS
    for name in needed:
        assert (root / name).exists(), f"{name} is missing on disk"

    tracked = subprocess.run(["git", "ls-files", "--", *needed],
                             capture_output=True, text=True, cwd=root)
    if tracked.returncode != 0:
        import pytest
        pytest.skip("not a git checkout")
    have = set(tracked.stdout.split())
    missing = [name for name in needed if name not in have]
    assert not missing, (
        f"on disk but not in the repository: {missing}. "
        f"Probably caught by a .gitignore rule -- check with "
        f"`git check-ignore -v <path>`.")


def test_the_frame_restyle_is_harmless_where_it_does_not_apply(qapp):
    """It reaches past Qt into DWM on Windows and AppKit on macOS, so the
    one thing every platform must agree on is that a frame it could not
    restyle is a cosmetic disappointment and not a crash."""
    import sys

    from PySide6 import QtWidgets

    from darlaston.ui import theme

    window = QtWidgets.QWidget()
    window.show()
    changed = theme.match_frame(window)
    if sys.platform not in ("darwin",) and not sys.platform.startswith("win"):
        assert changed is False, "restyled a frame that belongs to the WM"
    window.close()


def test_the_native_frame_can_be_asked_for(qapp, monkeypatch):
    """An escape hatch, because this reaches into AppKit and a future
    macOS is allowed to disagree with it."""
    from PySide6 import QtWidgets

    from darlaston.ui import theme

    monkeypatch.setenv(theme.NATIVE_FRAME_ENV, "1")
    window = QtWidgets.QWidget()
    window.show()
    assert theme.match_frame(window) is False
    window.close()


def test_the_toolbar_can_step_aside_for_window_controls(qapp):
    """On macOS the traffic lights float over the toolbar once the title
    bar is transparent, so the wordmark has to start to their right."""
    from darlaston.ui.shell import ToolBar

    bar = ToolBar()
    before = bar._row.getContentsMargins()
    bar.inset_for_window_controls(78)
    after = bar._row.getContentsMargins()
    assert after[0] == 78, "left margin did not move"
    assert after[1:] == before[1:], "only the left margin should change"
