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

import pytest


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
