"""Test-wide setup.

The suite builds real Qt widgets -- dialogs, the waiting page, the stack
assembly -- and grabs them to check what they render. That needs a Qt
platform plugin, and on a build machine there is no display, so it has
to be the offscreen one. Set here rather than in a Makefile or a CI
script because it has to be true *before* anything imports PySide6, and
because `pytest` typed on its own must work the same as `pytest` in CI.
"""
import os
import sys
from pathlib import Path

# Not setdefault: an empty QT_QPA_PLATFORM counts as "set" to it, and an
# empty value is exactly what a shell leaves behind after `VAR= command`.
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

# The project root, so `tools/` imports the same way everywhere. A couple
# of tests reuse the synthetic scenes from tools/stack_bench.py rather than
# keeping a second copy of them. That worked without this line on a
# developer's machine and nowhere else: an editable install exposes only
# the packages pyproject declares, and `tools` is deliberately not one of
# them, so it was reachable here purely by accident of setuptools version.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# One thread per worker when the suite is forked across processes.
#
# OpenCV and the BLAS underneath numpy each default to one thread per
# core. Sixteen pytest workers on a sixteen-core machine therefore ask
# for two hundred and fifty-six, and the machine spends its time
# switching between them: measured, the load average went to 16.5 and
# tests that wait on real work -- a capture completing, a frame arriving
# -- began timing out at random. The failures looked like flaky tests
# and were not; they were starvation.
#
# This is the same reasoning as `cpu.apply_thread_budget`, which the
# application does to itself for the same reason. Set before anything
# imports numpy or cv2, because both read it once at import.
if os.environ.get("PYTEST_XDIST_WORKER"):
    for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(_var, "1")
    import cv2

    cv2.setNumThreads(1)

import pytest


@pytest.fixture(autouse=True)
def _own_config(tmp_path_factory, monkeypatch):
    """No test reads or writes the developer's real configuration.

    Several tests build a `MainWindow`, which constructs a `Library` and
    a `Settings` from `config_dir()` -- and that is a real directory on a
    real person's machine holding every objective they have entered. The
    tests that knew this redirected `XDG_CONFIG_HOME` and said why; the
    others inherited whatever was there.

    Nothing was being written, so this was latent rather than live. It
    stopped being latent the moment somebody drove a window from a
    throwaway script and left white balance switched on with absurd gains
    in the author's settings, which is exactly the shape of accident this
    prevents. Autouse, because the ones that need it most are the ones
    that never thought about it.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME",
                       str(tmp_path_factory.mktemp("config")))


@pytest.fixture
def window(qapp):
    """A `MainWindow` on the synthetic camera, shut down whatever happens.

    Several tests built one and called `shutdown()` after their
    assertions. On the passing path that is fine; on the failing path the
    call is never reached, and the window is left driving a synthetic
    camera at thirty frames a second for the rest of the run -- so a
    single failure quietly loads the machine underneath every test after
    it, which is a superb way to turn one red test into three.

    Hands back a factory rather than a window, because a couple of tests
    need two, or need one built with particular arguments.
    """
    from darlaston.camera.mock import MockCamera

    made = []

    def build(make_backend=None, **kw):
        from darlaston.ui.main import MainWindow

        win = MainWindow(make_backend or (lambda: MockCamera(fps=30.0)), **kw)
        made.append(win)
        return win

    try:
        yield build
    finally:
        for win in made:
            win.shutdown()


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole run.

    Constructing a QWidget without one does not raise, it segfaults, and Qt
    will not tolerate a second instance either -- so this is session-scoped
    and hands back whatever already exists.
    """
    from PySide6 import QtWidgets

    from darlaston.ui import theme
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Whatever main() sets on the application, tests get too -- otherwise
    # they pass on things the real program would fail.
    theme.install(app)
    # Including the typefaces, and then actually applying one. Registering
    # the families is not enough on its own -- nothing is drawn in them
    # until the stylesheet asks for them, so a widget built in a test was
    # being set in whatever fontconfig happened to offer. That is a
    # property of the machine, and it is why the tests that measure where
    # the dither falls around the letterforms passed on a desktop with a
    # full font set and failed on a CI image with almost none: the glyphs
    # were not the same shape. The application font is set rather than the
    # whole stylesheet, which would drag colour and metrics into every
    # widget test at once.
    from PySide6 import QtGui
    app.setFont(QtGui.QFont(theme.load_fonts()["sans"]))
    yield app
