"""Test-wide setup.

The suite builds real Qt widgets -- dialogs, the waiting page, the stack
assembly -- and grabs them to check what they render. That needs a Qt
platform plugin, and on a build machine there is no display, so it has
to be the offscreen one. Set here rather than in a Makefile or a CI
script because it has to be true *before* anything imports PySide6, and
because `pytest` typed on its own must work the same as `pytest` in CI.
"""
import os

# Not setdefault: an empty QT_QPA_PLATFORM counts as "set" to it, and an
# empty value is exactly what a shell leaves behind after `VAR= command`.
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole run.

    Constructing a QWidget without one does not raise, it segfaults, and Qt
    will not tolerate a second instance either -- so this is session-scoped
    and hands back whatever already exists.
    """
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
