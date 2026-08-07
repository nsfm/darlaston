"""Version comparison, which is easy to get subtly and quietly wrong."""
from darlaston.update import Release, Version, latest_release, newer, parse


def test_versions_order_by_number_and_not_by_text():
    """The classic one. As strings, "0.10.0" sorts before "0.9.0", so an
    adopter on the newest build gets told to downgrade -- and it only
    starts happening at the tenth release of anything, long after the
    code looked fine."""
    assert parse("0.9.0") < parse("0.10.0")
    assert parse("0.9.9") < parse("0.10.0")
    assert parse("1.0.0") > parse("0.99.99")
    assert parse("0.8.1") > parse("0.8.0")


def test_a_tag_is_read_the_way_tags_are_written():
    """CI publishes `v0.8.0`; the running program calls itself `0.8.0`.
    A leading v is a tag convention, not part of the version."""
    assert parse("v0.8.0") == parse("0.8.0")
    assert newer("0.7.0", "v0.8.0") is not None


def test_a_build_from_between_releases_is_ahead_of_the_release():
    """setuptools-scm gives working builds a `0.8.0.dev116+g7340561`,
    which comes *after* 0.8.0 and not before it. Read the other way, every
    development build is told to downgrade to the release it was built
    from -- on every launch."""
    dev = parse("0.8.0.dev116+g7340561.d20260802")
    assert dev.development
    assert parse("0.8.0") < dev
    assert newer("0.8.0.dev116+g7340561", "0.8.0") is None, \
        "told a newer build to install an older one"
    # But a genuinely newer release still wins.
    assert newer("0.8.0.dev116+g7340561", "0.9.0") is not None


def test_the_same_version_is_not_an_upgrade():
    assert newer("0.8.0", "0.8.0") is None
    assert newer("0.8.0", "v0.8.0") is None
    assert newer("0.9.0", "0.8.0") is None, "offered an older version"


def test_anything_unreadable_is_silence():
    """Every caller is deciding whether to interrupt somebody. The right
    answer to "I cannot tell" is to say nothing at all."""
    for text in ("", "unknown", "not a version", "0.8", "..", "v", "1"):
        assert parse(text) is None, f"{text!r} was read as a version"
    assert newer("", "0.8.0") is None
    assert newer("0.7.0", "") is None
    assert newer("0.7.0", "garbage") is None


def test_not_knowing_our_own_version_is_also_silence():
    """`darlaston.__version__` falls back to "0.0.0+unknown" when the
    package metadata is missing. That parses perfectly well and sits
    below every real release, so read naively it nags somebody about an
    upgrade on every single launch on the strength of not knowing what
    they already have.

    The history starts at 0.0.0 and the first commit leaves it, so no
    real build is ever 0.0.0.
    """
    from darlaston import __version__

    assert parse("0.0.0+unknown") is not None, "it does parse, that is the trap"
    assert newer("0.0.0+unknown", "0.9.0") is None
    assert newer("0.0.0", "0.9.0") is None
    # And the real one still behaves, whatever it happens to be here.
    assert newer(__version__, "0.0.1") is None


def test_the_release_query_reads_a_real_github_answer():
    """Shape taken from the API's own documented response."""
    import io
    import json

    body = json.dumps({
        "tag_name": "v0.9.0",
        "html_url": "https://github.com/nsfm/darlaston/releases/tag/v0.9.0",
        "body": "Present mode, and a scale bar.",
        "assets": [],
    }).encode()

    def opener(_request, timeout=None):
        class _Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        return _Answer(body)

    found = latest_release(opener=opener)
    assert isinstance(found, Release)
    assert found.version == Version(0, 9, 0)
    assert found.tag == "v0.9.0"
    assert "releases/tag/v0.9.0" in found.page
    assert found.notes.startswith("Present mode")


def test_a_check_that_fails_is_not_an_error():
    """Offline, rate-limited, a proxy in the way, a captive portal
    answering with a login page. The application works exactly as well
    either way, and a message box about a background check nobody asked
    for is worse than no check."""
    def refuses(_request, timeout=None):
        raise OSError("no route to host")

    def lies(_request, timeout=None):
        import io

        class _Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        return _Answer(b"<html>please log in to the hotel wifi</html>")

    assert latest_release(opener=refuses) is None
    assert latest_release(opener=lies) is None


def test_nothing_here_reaches_the_network_by_itself():
    """Importing this module, or comparing two versions, must not open a
    socket. Whether this program talks to a server at all is a decision
    for the person whose name is on it, taken once, somewhere visible --
    not something a helper does on being imported."""
    import inspect

    from darlaston import update

    for name in ("parse", "newer"):
        source = inspect.getsource(getattr(update, name))
        assert "urlopen" not in source and "Request" not in source


# ---- the wiring, which is where a check turns into an interruption --------

def test_a_window_built_by_a_test_never_reaches_the_network(qapp):
    """The watch starts from `main`, not from the constructor. Every test
    in this suite builds a MainWindow; if that asked GitHub anything, the
    suite would be making hundreds of requests and would fail differently
    on a train."""
    import inspect

    from darlaston.ui.main import MainWindow

    source = inspect.getsource(MainWindow.__init__)
    assert "UpdateWatch" not in source
    assert "watch_for_updates" not in source


def test_the_setting_is_obeyed(qapp, window):
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = window()
    win.settings.check_for_updates = False
    win.watch_for_updates()
    assert not hasattr(win, "_update_watch"), \
        "looked anyway, with the setting off"


def test_the_menu_entry_appears_only_when_there_is_something_to_say(qapp, window):
    """Not a dialog on arrival: it would land seconds after launch, over
    whatever somebody had already started doing, for news that keeps."""
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow
    from darlaston.update import Release, Version

    win = window()
    assert not win.update_action.isVisible(), "there before there was news"

    win._update_found(Release(version=Version(9, 9, 9), tag="v9.9.9",
                              page="https://example.invalid/releases",
                              notes="notes"))
    assert win.update_action.isVisible()
    assert not win.update_action.icon().isNull(), "nothing to catch the eye"
    assert win.update_action.font().bold()


def test_the_dialog_says_what_it_will_actually_do(qapp):
    """It opens a web page. It does not install anything, and a person who
    expects an installer and gets a browser has been misled by us."""
    from PySide6 import QtWidgets

    from darlaston.i18n import _
    from darlaston.ui.update_ui import UpdateDialog
    from darlaston.update import Release, Version

    release = Release(version=Version(0, 9, 0), tag="v0.9.0",
                      page="https://example.invalid/releases",
                      notes="Present mode, and a scale bar.")
    dialog = UpdateDialog(release)
    text = " ".join(w.text() for w in dialog.findChildren(QtWidgets.QLabel))
    assert "0.9.0" in text
    assert _("update.body").split(".")[0] in text
    dialog.close()


def test_long_release_notes_are_cut_rather_than_scrolled(qapp):
    """A wall of commit subjects in a dialog is not something anybody
    reads, and a dialog taller than the screen cannot be dismissed."""
    from PySide6 import QtWidgets

    from darlaston.ui.update_ui import UpdateDialog
    from darlaston.update import Release, Version

    huge = "\n".join(f"* a change, number {i}" for i in range(400))
    dialog = UpdateDialog(Release(version=Version(0, 9, 0), tag="v0.9.0",
                                  page="https://example.invalid",
                                  notes=huge))
    shown = max((w.text() for w in dialog.findChildren(QtWidgets.QLabel)),
                key=len)
    assert len(shown) < 800, f"put {len(shown)} characters in a dialog"
    dialog.close()
