"""Dialogs that carry their own frame, and the panels that float."""
import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from darlaston.session.settings import Settings
from darlaston.ui import theme
from darlaston.ui.about import AboutDialog
from darlaston.ui.capture_ui import SettingsDialog
from darlaston.ui.perf_ui import PerformanceDialog
from darlaston.ui.photographer_ui import PhotographerDialog


def _dialogs(settings):
    return {
        "About": AboutDialog(),
        "Photographer": PhotographerDialog(settings),
        "Files": SettingsDialog(settings),
        "Performance": PerformanceDialog(settings, lambda: None),
    }


@pytest.fixture
def settings(tmp_path):
    s = Settings(capture_root=str(tmp_path))
    s.save = lambda *a, **k: None
    return s


def test_framed_dialogs_drag_from_anywhere_including_the_words(qapp, settings):
    """The reported fault: the grabby-hand cursor appears over the text but
    the window will not move, because a QLabel eats the press. It reads as
    the window being stuck rather than the label being in the way."""
    for name, d in _dialogs(settings).items():
        d.show()
        qapp.processEvents()
        labels = d.findChildren(QtWidgets.QLabel)
        assert labels, f"{name} has no labels to test"
        for label in labels:
            assert label.testAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
                f"{name}: a label still swallows the drag"

        # And pressing over one really does arm the drag.
        start = d.pos()
        d.mousePressEvent(QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(40, 60),
            QtCore.QPointF(start.x() + 40, start.y() + 60),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier))
        assert d._drag_from is not None, f"{name} did not arm the drag"
        d.mouseReleaseEvent(QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease, QtCore.QPointF(40, 60),
            QtCore.QPointF(start.x() + 40, start.y() + 60),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier))
        assert d._drag_from is None
        d.deleteLater()


def test_framed_dialogs_are_frameless_and_bordered(qapp, settings):
    for name, d in _dialogs(settings).items():
        assert d.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint, name
        assert d.frame.property("role") == "panel", \
            f"{name} has no bordered panel to be its frame"
        d.deleteLater()


def test_the_photographer_remembers_which_licence_it_wrote(qapp, tmp_path):
    """Only the notice is persisted, which is right -- it is what goes in
    the file. But the combo opened on "none" every time, so a licence
    chosen last week looked forgotten."""
    s = Settings(capture_root=str(tmp_path), artist="Nate Dube",
                 copyright="2026 Nate Dube. CC BY 4.0.")
    d = PhotographerDialog(s)
    assert d.name.text() == "Nate Dube"
    assert d.notice.text() == "2026 Nate Dube. CC BY 4.0."
    assert d.licence.currentText() == "CC BY 4.0"
    d.deleteLater()

    # Every offered licence round-trips.
    from darlaston.ui.photographer_ui import LICENCES
    for label, template in LICENCES:
        notice = template.format(year=2026, name="Nate Dube")
        s2 = Settings(capture_root=str(tmp_path), artist="Nate Dube",
                      copyright=notice)
        d2 = PhotographerDialog(s2)
        assert d2.licence.currentText() == label, f"{label} was not restored"
        d2.deleteLater()

    # A hand-written notice is not claimed by any of them.
    s3 = Settings(capture_root=str(tmp_path), copyright="ask me first")
    d3 = PhotographerDialog(s3)
    assert d3.licence.currentText() == "-- none --"
    d3.deleteLater()


def test_text_fields_are_ours_rather_than_the_platforms():
    """Unstyled QLineEdits drew a bright platform-blue focus ring: the only
    saturated colour in the application, on the widget you look at while
    typing."""
    css = theme.stylesheet()
    assert "QLineEdit {" in css
    focus = css[css.index("QLineEdit:focus"):]
    assert theme.BRASS in focus[:focus.index("}")]


def test_a_floating_panel_keeps_the_size_it_was_given(qapp):
    """Closing the slide map and opening it again handed back whatever
    default was computed before the window had been laid out, so it came
    back smaller every time."""
    from darlaston.ui.floating import FloatingPanel

    host = QtWidgets.QWidget()
    host.resize(900, 700)
    panel = FloatingPanel("slide map", host)
    host.show()
    qapp.processEvents()

    panel.place((300, 240))
    assert (panel.width(), panel.height()) == (300, 240)

    # The operator makes it bigger, then closes and reopens it.
    panel.resize(420, 330)
    panel.hide()
    panel.place((300, 240))
    assert (panel.width(), panel.height()) == (420, 330), \
        "the panel forgot the size it was given"

    # It can still be resized: no fixed size was imposed.
    assert panel.maximumWidth() > 1000
    assert panel.minimumWidth() <= 300
    host.deleteLater()


def test_dialog_buttons_carry_no_system_icons(qapp, settings):
    """Standard buttons pull an icon from the *system* icon theme -- a
    black tick or cross on a desktop whose icons were drawn for a light
    one. Whether they do is a platform style hint, so it was invisible
    under the offscreen platform and plain on a real desktop.
    """
    css = theme.stylesheet()
    assert "dialogbuttonbox-buttons-have-icons: 0" in css

    for name, d in _dialogs(settings).items():
        d.show()
        qapp.processEvents()
        boxes = d.findChildren(QtWidgets.QDialogButtonBox)
        assert boxes, f"{name} has no button box"
        for box in boxes:
            for button in box.buttons():
                assert button.icon().isNull(), \
                    f"{name}: {button.text()!r} still carries a system icon"
                assert button.text(), "a button with neither icon nor words"
        d.deleteLater()
