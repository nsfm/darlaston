

def test_a_turret_keeps_the_positions_it_was_said_to_have(qapp):
    """Trailing empties used to be dropped as "not gaps", which turned a
    five-position turret whose last position is empty into a four-position
    one. Stepping and detection both work in physical order, so from then on
    half the ring was one position out."""
    from darlaston.session.model import (CameraProfile, Objective,
                                         ScopeProfile, Setup, Turret)
    from darlaston.ui.setup_ui import SetupDialog

    scope = ScopeProfile(id="s", name="Stand",
                         turret=Turret([Objective(10, 0.3), Objective(40, 0.75)]))
    setup = Setup(camera=CameraProfile(serial="x"), scope=scope)
    dialog = SetupDialog(setup, None)

    dialog.slots.setValue(5)
    built = dialog._build()
    assert len(built.scope.turret.positions) == 5
    assert built.scope.turret.positions[2:] == [None, None, None]

    # Only the positions that exist are on screen. isHidden rather than
    # isVisible: a child of a window that was never shown is not visible
    # whatever its own flag says.
    assert [r.isHidden() for r in dialog.rows] == [False] * 5 + [True] * 2

    dialog.slots.setValue(3)
    assert len(dialog._build().scope.turret.positions) == 3


def test_empty_positions_can_be_marked_capped(qapp):
    """An open position and a capped one are opposite ends of the scale, and
    both read as "no objective" unless the difference is recorded."""
    from darlaston.session.model import (CameraProfile, Objective,
                                         ScopeProfile, Setup, Turret)
    from darlaston.ui.setup_ui import SetupDialog

    scope = ScopeProfile(id="s", turret=Turret([Objective(10, 0.3), None, None]))
    dialog = SetupDialog(Setup(camera=CameraProfile(serial="x"), scope=scope),
                         None)
    dialog.slots.setValue(3)

    # Only offered where it means something.
    assert not dialog.rows[0].capped.isEnabled(), \
        "a position with an objective in it cannot be capped"
    assert dialog.rows[1].capped.isEnabled()

    dialog.rows[1].capped.setChecked(True)
    built = dialog._build()
    assert built.scope.turret.is_capped(1)
    assert not built.scope.turret.is_capped(2)
    assert not built.scope.turret.is_capped(0)


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
    from darlaston.session.model import CameraProfile, ScopeProfile, Setup
    from darlaston.ui.main import _provisional_scope
    from darlaston.ui.setup_ui import SetupDialog

    assert _provisional_scope().optovar == []

    setup = Setup(camera=CameraProfile(serial="x"),
                  scope=ScopeProfile(id="s"))
    dialog = SetupDialog(setup, None)
    assert not dialog.has_changer.isChecked()
    assert not dialog.optovar.isEnabled()
    assert dialog._build().scope.optovar == []

    dialog.has_changer.setChecked(True)
    dialog.optovar.setText("1 1.25 1.6")
    assert dialog._build().scope.optovar == [1.0, 1.25, 1.6]

    # Unticking discards the factors rather than leaving them to be saved.
    dialog.has_changer.setChecked(False)
    assert dialog._build().scope.optovar == []


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
    assert "optovar=1.6" in fitted.comment
    assert "Optovar 1.6" in fitted.description
    # The comment drops empty values, so a stand with no changer says
    # nothing about one rather than recording it as sitting at unity.
    assert "optovar=" not in meta.comment
    assert "Optovar" not in meta.description


def test_the_setup_editor_opens_with_no_camera_attached(qapp, tmp_path):
    """It used to return silently when nothing was plugged in, so the menu
    item did nothing at all -- which is exactly when somebody sits down to
    describe their microscope."""
    from darlaston.session.model import Library
    from darlaston.ui.main import MainWindow

    opened = []

    class Fake(MainWindow):
        def __init__(self):                     # no camera, no window
            pass

    win = Fake()
    win.setup = None
    win.library = Library(tmp_path / "library.json")
    win._illumination = None

    import darlaston.ui.main as main_mod

    class Dialog:
        def __init__(self, setup, library, parent):
            opened.append(setup)
            self.result_setup = setup

        def exec(self):
            return 0                            # cancelled

    real, main_mod.SetupDialog = main_mod.SetupDialog, Dialog
    try:
        MainWindow._open_setup(win)
    finally:
        main_mod.SetupDialog = real

    assert opened, "the editor did not open without a camera"
    setup = opened[0]
    assert setup.camera.serial == "", "a camera was invented from nowhere"
    assert setup.scope is not None


def test_a_camera_with_no_serial_is_never_filed(qapp, tmp_path):
    """The serial is the camera's identity and it is blank when the editor is
    opened with nothing plugged in. Filing that puts a nameless profile under
    the key "" and binds a stand to it."""
    from darlaston.session.model import (CameraProfile, Library, ScopeProfile,
                                         Setup)
    from darlaston.ui.setup_ui import SetupDialog

    library = Library(tmp_path / "library.json")
    scope = library.add_scope("Bench stand")
    setup = Setup(camera=CameraProfile(serial=""), scope=scope)

    dialog = SetupDialog(setup, library, None)
    dialog.scope_name.setText("Bench stand")
    dialog._save()

    assert library.cameras == {}, f"filed a serial-less camera: {library.cameras}"
    assert scope.id in library.scopes, "the stand was not saved either"

    reloaded = Library(tmp_path / "library.json")
    assert reloaded.cameras == {}
    assert reloaded.scopes[scope.id].name == "Bench stand"
