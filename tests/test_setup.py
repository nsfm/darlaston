

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
    from darlaston.ui.main import _provisional_scope

    assert _provisional_scope().optovar == []

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

    library.remove_camera("WEBCAM-1")
    dialog._reload("TP2112071101")
    assert dialog.list.count() == 1
    assert list(Library(tmp_path / "library.json").cameras) == ["TP2112071101"]


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
