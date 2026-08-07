

def _editor(scope):
    from darlaston.ui.setup_ui import ScopeEditor
    editor = ScopeEditor()
    editor.load(scope)
    return editor


def test_a_turret_keeps_the_positions_it_was_said_to_have(qapp):
    """Trailing empties used to be dropped as "not gaps", which turned a
    five-position turret whose last position is empty into a four-position
    one. Stepping and detection both work in physical order, so half the ring
    was one position out from then on."""
    from darlaston.session.model import Objective, ScopeProfile, Turret

    editor = _editor(ScopeProfile(
        id="s", name="Stand",
        turret=Turret([Objective(10, 0.3), Objective(40, 0.75)])))

    editor.slots.setValue(5)
    built = editor.build()
    assert len(built.turret.positions) == 5
    assert built.turret.positions[2:] == [None, None, None]

    # Only the positions that exist are on screen. isHidden rather than
    # isVisible: a child of a window that was never shown is not visible
    # whatever its own flag says.
    assert [r.isHidden() for r in editor.rows] == [False] * 5 + [True] * 2

    editor.slots.setValue(3)
    assert len(editor.build().turret.positions) == 3


def test_empty_positions_can_be_marked_capped(qapp):
    """An open position and a capped one are opposite ends of the scale, and
    both read as "no objective" unless the difference is recorded."""
    from darlaston.session.model import Objective, ScopeProfile, Turret

    editor = _editor(ScopeProfile(
        id="s", turret=Turret([Objective(10, 0.3), None, None])))

    # Only offered where it means something.
    assert not editor.rows[0].capped.isEnabled(), \
        "a position with an objective in it cannot be capped"
    assert editor.rows[1].capped.isEnabled()

    editor.rows[1].capped.setChecked(True)
    built = editor.build()
    assert built.turret.is_capped(1)
    assert not built.turret.is_capped(2)
    assert not built.turret.is_capped(0)


def test_capped_and_open_positions_are_predicted_apart():
    """The whole point of recording the difference."""
    from darlaston.live.turret import model_signatures
    from darlaston.session.model import Objective, Turret

    turret = Turret([Objective(10, 0.3), None, None],
                    capped=[False, True, False])
    capped, open_slot = model_signatures(turret, condenser_na=0.55)[1:]
    assert capped == 0.0, "a dust cap is black"
    assert open_slot > model_signatures(turret, 0.55)[0] * 100, \
        "an open position passes the whole cone and blows white"


def test_the_magnification_changer_is_off_unless_fitted(qapp):
    """Most stands have none. The four Zeiss Optovar factors that used to be
    the default were one microscope's, offered to everybody."""
    from darlaston.session.model import ScopeProfile

    editor = _editor(ScopeProfile(id="s"))
    assert not editor.has_changer.isChecked()
    assert not editor.optovar.isEnabled()
    assert editor.build().optovar == []

    editor.has_changer.setChecked(True)
    editor.optovar.setText("1 1.25 1.6")
    assert editor.build().optovar == [1.0, 1.25, 1.6]

    # Unticking discards the factors rather than leaving them to be saved.
    editor.has_changer.setChecked(False)
    assert editor.build().optovar == []


def test_a_stand_without_a_changer_says_nothing_about_one():
    from darlaston.process.metadata import from_setup
    from darlaston.session.model import (CameraProfile, Objective,
                                         ScopeProfile, Setup, Turret)

    scope = ScopeProfile(id="s", name="Stand",
                         turret=Turret([Objective(40, 0.75)]))
    meta = from_setup(Setup(camera=CameraProfile(serial="x"), scope=scope),
                      exposure_us=1000, gain_pct=100)
    fitted = from_setup(
        Setup(camera=CameraProfile(serial="x"),
              scope=ScopeProfile(id="s2", turret=Turret([Objective(40, 0.75)]),
                                 optovar=[1.0, 1.6], optovar_current=1)),
        exposure_us=1000, gain_pct=100)
    # The comment drops empty values, so a stand with no changer says
    # nothing about one rather than recording it as sitting at unity.
    assert "optovar=" not in meta.comment
    assert "Optovar" not in meta.description
    assert "optovar=1.6" in fitted.comment
    assert "Optovar 1.6" in fitted.description


def _window(qapp, tmp_path, monkeypatch):
    """A main window whose library is a scratch file.

    `MainWindow` builds its own `Library` from the user's config directory,
    so without redirecting XDG_CONFIG_HOME a test would file cameras into
    whatever is really on the machine and then assert against it.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    return MainWindow(lambda: MockCamera(fps=30.0))


def _camera_arrives(win, scope=None):
    """The moment a camera is recognised, which is when the setup is built.

    Driven directly rather than waited for: the session emits its status
    from its own thread through a queued connection, so nothing is
    delivered until an event loop runs, and a test that spins one is a test
    that can hang.
    """
    from darlaston.camera.base import CameraInfo, CameraState
    from darlaston.camera.session import SessionStatus

    if scope is not None:
        win.library.scopes = {scope.id: scope}
    info = CameraInfo(model="E3ISPM20000KPA", serial="TP2112071101",
                      resolutions=(), max_bit_depth=12, bayer_pattern="GBRG",
                      exposure_range_us=(100, 1000000),
                      gain_range_pct=(100, 2000))
    win._on_status(SessionStatus(CameraState.READY, info=info))


def _stand(optovar):
    from darlaston.session.model import Objective, ScopeProfile, Turret

    return ScopeProfile(id="z", name="Stand",
                        turret=Turret([Objective(40, 0.75)]),
                        optovar=list(optovar))


def test_the_changer_is_only_offered_on_a_stand_that_has_one(
        qapp, tmp_path, monkeypatch):
    """Rare glass. A control for a piece of the optical path that is not
    fitted is a question its owner cannot answer, and one factor is a stand
    with nothing to choose between."""
    win = _window(qapp, tmp_path, monkeypatch)
    try:
        _camera_arrives(win, _stand([]))
        assert win.optovar.isHidden(), "no changer, and it was offered anyway"

        # A different stand, as the setup window hands one over.
        win.setup.scope = _stand([1.0])
        win._sync_optovar()
        assert win.optovar.isHidden(), "one factor is not a choice"

        win.setup.scope = _stand([1.0, 1.25, 1.6, 2.0])
        win._sync_optovar()
        assert not win.optovar.isHidden()
        assert [win.optovar.itemText(i) for i in range(win.optovar.count())] \
            == ["1× changer", "1.25× changer", "1.6× changer", "2× changer"]

        # And it shows where the changer already is, not the top of the list.
        win.setup.scope = _stand([1.0, 1.6])
        win.setup.scope.optovar_current = 1
        win._sync_optovar()
        assert win.optovar.currentIndex() == 1
    finally:
        win.shutdown()


def test_choosing_a_changer_factor_reaches_the_magnification_and_the_file(
        qapp, tmp_path, monkeypatch):
    """The whole reason it needed a control: the factor was pinned at the
    first position, so anybody using the 1.6 setting got a total
    magnification and a scale understated by exactly that much -- silently,
    and in the file rather than only on screen."""
    from darlaston.process.metadata import from_setup

    win = _window(qapp, tmp_path, monkeypatch)
    try:
        _camera_arrives(win, _stand([1.0, 1.25, 1.6, 2.0]))
        assert win.setup.total_magnification == 40.0
        was = win.opportunist._key

        win.optovar.setCurrentIndex(2)
        assert win.setup.scope.optovar_current == 2
        assert win.setup.total_magnification == 64.0

        meta = from_setup(win.setup, exposure_us=1000, gain_pct=100)
        assert "optovar=1.6" in meta.comment
        assert "total_magnification=64" in meta.comment
        # Folded to ASCII on the way out, as every description is.
        assert "Optovar 1.6x" in meta.description
        assert "64x total" in meta.description

        # A flat is only valid for the stack it was shot through, and the
        # changer is part of that stack.
        assert win.opportunist._key != was
        assert "optovar1.6" in win.opportunist._key
    finally:
        win.shutdown()


def test_the_changer_position_survives_a_reload(qapp, tmp_path, monkeypatch):
    """It is a property of the stand, so it belongs on disk with it. Moving
    the changer used to reach nothing at all; reaching only the running
    session would be the same bug one launch later."""
    from darlaston.session.model import Library

    win = _window(qapp, tmp_path, monkeypatch)
    try:
        _camera_arrives(win, _stand([1.0, 1.25, 1.6, 2.0]))
        win.optovar.setCurrentIndex(3)
        library_path = win.library.path
    finally:
        win.shutdown()

    saved = Library(library_path).scopes["z"]
    assert saved.optovar_current == 3
    assert saved.optovar_factor == 2.0


def test_a_first_camera_makes_a_stand_with_no_invented_objectives(
        qapp, tmp_path, monkeypatch):
    """A plausible turret -- 10x/0.30, 20x/0.50, 40x/0.75, 100x/1.30 oil --
    used to be invented for a camera we had never seen on a stand. It is a
    claim about hardware the owner may not have, and it reached the status
    bar, the filename tokens and the EXIF of every frame shot before anybody
    found the setup window."""
    from darlaston.session.model import Library

    win = _window(qapp, tmp_path, monkeypatch)
    try:
        assert win.library.scopes == {}, "a scratch library was not scratch"
        _camera_arrives(win)

        scope = win.setup.scope
        assert scope.turret.positions == [None] * 4, \
            f"objectives invented from nowhere: {scope.turret.positions}"
        assert scope.turret.capped == [False] * 4
        assert scope.turret.objective is None
        assert win.setup.total_magnification is None
        assert scope.optovar == []

        # A real stand in the library rather than a placeholder passing
        # through, so describing it in the setup window has something to
        # write to and the calibration key is not "unconfigured".
        assert win.library.scopes == {scope.id: scope}
        assert Library(win.library.path).scopes[scope.id].name == "Microscope"

        # And the rail says so in words, because "--" is what it also shows
        # when parked on an empty detent of a turret somebody did describe.
        assert win.objective.label.text() == "no objectives yet"
    finally:
        win.shutdown()


def test_the_microscope_window_opens_with_no_camera_attached(qapp, tmp_path):
    """It used to return silently when nothing was plugged in, so the menu
    item did nothing at all -- which is exactly when somebody sits down to
    describe their microscope."""
    from darlaston.session.model import Library
    from darlaston.ui.setup_ui import MicroscopeDialog

    library = Library(tmp_path / "library.json")
    assert library.cameras == {} and library.scopes == {}

    dialog = MicroscopeDialog(library, None, None)
    # An empty library is not a state this window can show, so it makes the
    # first stand rather than presenting nothing to edit.
    assert dialog.selected is not None
    dialog.editor.name.setText("Zeiss Universal")
    dialog.editor.slots.setValue(5)
    dialog._save()

    reloaded = Library(tmp_path / "library.json")
    saved = next(iter(reloaded.scopes.values()))
    assert saved.name == "Zeiss Universal"
    assert len(saved.turret.positions) == 5
    assert reloaded.cameras == {}, "a camera was invented from nowhere"


def test_the_synthetic_camera_is_never_filed(qapp, tmp_path):
    """It is a test fixture, not a device anybody owns. Filing it put
    "MockCam (synthetic)" in the library and then offered it as the camera to
    describe when nothing real was plugged in."""
    from darlaston.session.model import Library

    library = Library(tmp_path / "library.json")
    got = library.remember_camera("MOCK-0000000000000000000000000",
                                  "MockCam (synthetic)")
    assert got.serial.startswith("MOCK-"), "still usable in the session"
    assert library.cameras == {}, f"filed the mock camera: {library.cameras}"

    library.remember_camera("TP2112071101", "E3ISPM20000KPA")
    assert list(library.cameras) == ["TP2112071101"]


def test_a_camera_can_be_forgotten(qapp, tmp_path):
    """Everything that appears gets filed, because the alternative is asking.
    That means a built-in webcam ends up in the list, so it has to be
    possible to take one out again."""
    from darlaston.session.model import Library
    from darlaston.ui.setup_ui import CameraDialog

    library = Library(tmp_path / "library.json")
    library.remember_camera("WEBCAM-1", "Integrated Camera")
    library.remember_camera("TP2112071101", "E3ISPM20000KPA")

    dialog = CameraDialog(library, "TP2112071101", None)
    assert dialog.list.count() == 2
    assert dialog.editor.isHidden() is False

    # Through the dialog's own copy, which is what the button touches.
    dialog._library.remove_camera("WEBCAM-1")
    dialog._reload("TP2112071101")
    assert dialog.list.count() == 1
    dialog._save()
    assert list(Library(tmp_path / "library.json").cameras) == ["TP2112071101"]
    assert list(library.cameras) == ["TP2112071101"], (
        "the window is still holding the old library after Save")


def test_cancelling_the_camera_window_keeps_the_camera(qapp, tmp_path):
    """Remove reached straight into the application's own Library. It wrote
    no file itself, which made it look safe -- but the object is shared, so
    Cancel left the removal in memory and the next save from anywhere
    committed it. A stand taken this way goes with its turret, its learned
    brightness signatures and the calibration key every stored flat is
    filed under."""
    from darlaston.session.model import Library
    from darlaston.ui.setup_ui import CameraDialog

    library = Library(tmp_path / "library.json")
    library.remember_camera("WEBCAM-1", "Integrated Camera")
    library.remember_camera("TP2112071101", "E3ISPM20000KPA")

    dialog = CameraDialog(library, "TP2112071101", None)
    dialog._library.remove_camera("WEBCAM-1")
    dialog.reject()

    assert list(library.cameras) == ["WEBCAM-1", "TP2112071101"], (
        "a cancelled removal survived in memory")
    # And the next save from anywhere at all must not commit it either.
    library.save()
    assert list(Library(tmp_path / "library.json").cameras) == [
        "WEBCAM-1", "TP2112071101"]


def test_an_unplugged_camera_is_still_editable(qapp, tmp_path):
    """The one on the microscope should be describable while it is sitting on
    the bench."""
    from darlaston.session.model import Library
    from darlaston.ui.setup_ui import CameraDialog

    library = Library(tmp_path / "library.json")
    library.remember_camera("TP2112071101", "E3ISPM20000KPA")

    dialog = CameraDialog(library, None, None)      # nothing plugged in
    dialog.editor.name.setText("Scope cam")
    dialog.editor.pixel_um.setValue(2.4)
    dialog.editor.relay_factor.setValue(0.5)
    dialog._save()

    saved = Library(tmp_path / "library.json").cameras["TP2112071101"]
    assert saved.name == "Scope cam"
    assert saved.pixel_um == 2.4
    assert saved.relay_factor == 0.5


def test_the_camera_window_says_so_when_none_has_been_seen(qapp, tmp_path):
    from darlaston.session.model import Library
    from darlaston.ui.setup_ui import CameraDialog

    dialog = CameraDialog(Library(tmp_path / "library.json"), None, None)
    assert dialog.empty.isHidden() is False
    assert dialog.editor.isHidden() is True
    assert dialog.list.isHidden() is True


def test_the_mock_camera_cannot_get_into_the_library_by_any_route(qapp, tmp_path):
    """remember_camera() refused it and callers then wrote
    library.cameras[serial] = profile straight past the refusal, so stepping
    an objective under --mock filed MockCam anyway. A rule that lives in one
    method and is enforced in another is not a rule."""
    from darlaston.session.model import CameraProfile, Library

    library = Library(tmp_path / "library.json")
    mock = CameraProfile(serial="MOCK-0000000000000000000000000",
                         name="MockCam (synthetic)")

    library.remember_camera(mock.serial, mock.name)
    library.file_camera(mock)
    library.bind(mock.serial, "some-scope")
    library.save()

    assert library.cameras == {}
    assert Library(tmp_path / "library.json").cameras == {}

    # And a camera that has never identified itself is not filed either.
    library.file_camera(CameraProfile(serial="", name="Camera"))
    assert library.cameras == {}

    real = CameraProfile(serial="TP2112071101", name="Scope cam")
    library.file_camera(real)
    library.save()
    assert list(Library(tmp_path / "library.json").cameras) == ["TP2112071101"]


def test_passing_a_camera_over_survives_a_restart(qapp, tmp_path, monkeypatch):
    """The whole point of the flag: say it once, not every morning."""
    import types

    from darlaston.camera.discovery import Camera
    from darlaston.session.model import Library
    from darlaston.session.settings import Settings
    from darlaston.ui import setup_ui
    from darlaston.ui.setup_ui import CameraDialog

    library = Library(tmp_path / "library.json")
    library.remember_camera("built-in", "Integrated Camera")
    here = {"built-in": Camera(key="v4l2:built-in", kind="v4l2",
                               name="Integrated Camera", fingerprint="w")}
    monkeypatch.setattr(CameraDialog, "_attached", lambda self: here)

    settings = Settings(capture_root=str(tmp_path))
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "save", lambda: Settings.save(settings, path))

    dialog = CameraDialog(library, "built-in", None, settings=settings)
    assert dialog.ignore.isEnabled(), "the camera is attached and selected"
    assert not dialog.ignore.isChecked()

    dialog.ignore.setChecked(True)
    dialog._save()
    assert Settings.load(path).ignored_cameras == {"v4l2:built-in": "w"}

    # And it comes back ticked, rather than needing to be said again.
    again = CameraDialog(library, "built-in", None,
                         settings=Settings.load(path))
    assert again.ignore.isChecked()


def test_cancelling_does_not_pass_a_camera_over(qapp, tmp_path, monkeypatch):
    from darlaston.camera.discovery import Camera
    from darlaston.session.model import Library
    from darlaston.session.settings import Settings
    from darlaston.ui.setup_ui import CameraDialog

    library = Library(tmp_path / "library.json")
    library.remember_camera("built-in", "Integrated Camera")
    monkeypatch.setattr(
        CameraDialog, "_attached",
        lambda self: {"built-in": Camera(key="v4l2:built-in", kind="v4l2",
                                         name="Integrated", fingerprint="w")})

    settings = Settings(capture_root=str(tmp_path))
    dialog = CameraDialog(library, "built-in", None, settings=settings)
    dialog.ignore.setChecked(True)
    dialog.reject()
    assert settings.ignored_cameras == {}
