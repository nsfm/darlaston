"""Which control fills which slot: the profile first, the guesses
second, and a focus motor behind any control a person routes there."""
from __future__ import annotations

import pytest

from darlaston.camera import roles
from darlaston.camera.base import Control


def _controls():
    return (
        Control(id=0x00980900, name="Brightness", min=-64, max=64, default=0, value=0),
        Control(id=0x00980901, name="Contrast", min=0, max=64, default=32, value=32),
        Control(id=0x0098091b, name="Sharpness", min=0, max=6, default=3, value=3),
        Control(id=0x0098091c, name="Backlight Compensation", min=0, max=854, default=288, value=288),
        Control(id=0x009a090c, name="Focus, Automatic Continuous", kind="bool", min=0, max=1, default=1, value=1),
        Control(id=0x009a090a, name="Focus, Absolute", min=0, max=250, step=5, default=0, value=0,
                auto_id=0x009a090c),
    )


def test_the_guesses_match_by_name_and_skip_the_switches():
    slots = roles.resolve(_controls(), {})
    assert slots["brightness"].name == "Brightness"
    assert slots["sharpness"].name == "Sharpness"
    assert slots["focus"].name == "Focus, Absolute", "the bool beside it is the switch"
    assert slots["gamma"] is None and slots["hue"] is None
    assert slots["saturation"] is None


def test_a_role_wins_over_the_guess_and_a_zero_empties_the_slot():
    # The X5FCAM: the stepper is behind Backlight Compensation.
    slots = roles.resolve(_controls(), {"focus": 0x0098091c})
    assert slots["focus"].name == "Backlight Compensation"
    assert slots["brightness"].name == "Brightness", "the others keep their guesses"
    # A Brightness that only lifts the blacks: nothing on the rail, not even the guess.
    slots = roles.resolve(_controls(), {"brightness": 0})
    assert slots["brightness"] is None
    # A role naming a control the device no longer reports falls back to the guess.
    slots = roles.resolve(_controls(), {"focus": 0x12345678})
    assert slots["focus"].name == "Focus, Absolute"


def test_one_control_fills_one_slot():
    slots = roles.resolve(_controls(), {"contrast": 0x00980900})
    assert slots["contrast"].name == "Brightness", "a role claims it whatever the slot order"
    assert slots["brightness"] is None, "and the guess cannot have it too"


def test_a_routed_control_is_a_focus_motor_through_the_generic_api():
    class Backend:
        def __init__(self): self.writes = []; self.autos = []
        def set_control(self, cid, value): self.writes.append((cid, value)); return True
        def set_auto(self, cid, on): self.autos.append((cid, on)); return True

    backend = Backend()
    backlight = _controls()[3]
    axis = roles.ControlFocusAxis(backend, backlight)
    assert (axis.range().lo, axis.range().hi, axis.range().step) == (0, 854, 1)
    assert axis.get() == 288, "starts where the camera reported it"
    axis.set(900)
    assert axis.get() == 854 and backend.writes == [(0x0098091c, 854)]
    axis.set_auto(True)
    assert backend.autos == [], "no switch on this control"
    focus = _controls()[5]
    roles.ControlFocusAxis(backend, focus).set_auto(True)
    assert backend.autos == [(0x009a090a, True)]


# ---- the Picture row ---------------------------------------------------------

def test_the_picture_row_shows_what_fills_its_slots_and_emits_moves(qapp):
    from darlaston.ui.picture_ui import PictureRow

    row = PictureRow()
    row.show()
    got = []
    row.changed.connect(lambda slot, cid, value: got.append((slot, cid, value)))
    slots = roles.resolve(_controls(), {})
    row.set_controls({s: slots[s] for s in roles.PICTURE_SLOTS})
    assert row.isVisible() and not row.open, "closed by default"
    assert not row._bars["brightness"].isHidden() and row._bars["gamma"].isHidden()
    assert row.body.isHidden()
    row.set_open(True)
    assert not row.body.isHidden()
    row._bars["sharpness"].setValue(0)
    assert got == [("sharpness", 0x0098091b, 0)]
    row.show_value("sharpness", 6)
    assert row._bars["sharpness"].value() == 6 and len(got) == 1, "shown, not re-emitted"
    row.set_controls({})
    assert not row.isVisible(), "nothing to show, no row"


def test_an_inactive_control_s_bar_is_greyed_and_says_whose_it_is(qapp):
    from darlaston.ui.picture_ui import PictureRow
    import dataclasses

    row = PictureRow()
    held = dataclasses.replace(_controls()[0], inactive=True)
    row.set_controls({"brightness": held})
    assert not row._bars["brightness"].isEnabled()
    assert "automatic owns" in row._bars["brightness"].toolTip()


# ---- the controls page -----------------------------------------------------------

class _Live:
    def __init__(self, controls):
        self._controls = controls
        self.writes: list = []
        self.autos: list = []

    def controls(self):
        return self._controls

    def set_control(self, cid, value):
        self.writes.append((cid, value)); return True

    def set_auto(self, cid, on):
        self.autos.append((cid, on)); return True


def test_the_controls_page_lists_every_control_and_routes_one(qapp):
    from darlaston.ui.controls_ui import ControlsPage

    live = _Live(_controls())
    profile_roles: dict = {}
    page = ControlsPage(live, profile_roles)
    assert len(page._rows) == len(_controls())
    names = [r["control"].name for r in page._rows]
    assert names[3] == "Backlight Compensation"
    # The guess shows as the placeholder on the control it picked.
    use_focus = page._rows[5]["widgets"][3]
    assert "focus" in use_focus.itemText(0)
    use_backlight = page._rows[3]["widgets"][3]
    assert "nothing" in use_backlight.itemText(0)
    # Route Backlight to focus: the profile says so, and the row that
    # held focus by guess is not told it holds it still.
    told = []
    page.role_changed.connect(lambda slot, cid: told.append((slot, cid)))
    use_backlight.setCurrentIndex(use_backlight.findData("focus"))
    assert profile_roles == {"focus": 0x0098091c}
    assert told == [("focus", 0x0098091c)]
    # Moving a bar writes through to the camera.
    page._rows[2]["value"].setValue(1)
    assert (0x0098091b, 1) in live.writes
    # Device defaults sends every writable, unowned control its default.
    page.defaults.click()
    assert (0x0098091b, 3) in live.writes and (0x00980900, 0) in live.writes


def test_emptying_a_slot_records_a_zero_role(qapp):
    from darlaston.ui.controls_ui import ControlsPage

    live = _Live(_controls())
    profile_roles: dict = {}
    page = ControlsPage(live, profile_roles)
    use_brightness = page._rows[0]["widgets"][3]
    use_brightness.setCurrentIndex(use_brightness.findData("none"))
    assert profile_roles == {"brightness": 0}
    assert roles.resolve(_controls(), profile_roles)["brightness"] is None


# ---- the session remembers --------------------------------------------------------

def test_the_session_hands_a_camera_its_controls_back_after_a_start():
    from darlaston.camera.session import CameraSession

    class Backend:
        info = None
        def __init__(self): self.writes = []
        def set_exposure(self, us): pass
        def set_gain(self, pct): pass
        def set_control(self, cid, value): self.writes.append((cid, value)); return True

    session = CameraSession.__new__(CameraSession)
    import threading
    session._lock = threading.Lock(); session._backend = None
    session._exposure_us = 8000; session._gain_pct = 100
    session._auto_exposure = None; session._control_values = {}; session._auto_values = {}
    session.framerate_cap = 0
    assert not session.set_control(0x00980900, 10, remember=True), "nothing open yet, remembered all the same"
    backend = Backend()
    session._restore_controls(backend)
    assert backend.writes == [(0x00980900, 10)]
    session.forget_controls()
    backend.writes.clear()
    session._restore_controls(backend)
    assert backend.writes == []


# ---- what the reviews found ----------------------------------------------------

def test_the_route_bookkeeping_undoes_a_none_and_keeps_another_s_route(qapp):
    from darlaston.ui.controls_ui import ControlsPage

    profile_roles: dict = {}
    page = ControlsPage(_Live(_controls()), profile_roles)
    brightness = page._rows[0]["widgets"][3]
    backlight = page._rows[3]["widgets"][3]
    # Brightness -> nothing, then the guess picked again: the zero goes.
    brightness.setCurrentIndex(brightness.findData("none"))
    assert profile_roles == {"brightness": 0}
    brightness.setCurrentIndex(0)
    assert profile_roles == {}
    # Backlight routed to brightness, then Brightness -> nothing: the
    # route stands, since it is another control's, not the guess.
    backlight.setCurrentIndex(backlight.findData("brightness"))
    brightness.setCurrentIndex(brightness.findData("none"))
    assert profile_roles == {"brightness": 0x0098091c}


def test_device_defaults_leave_what_the_rail_owns_alone(qapp):
    from darlaston.ui.controls_ui import ControlsPage

    live = _Live(_controls())
    page = ControlsPage(live, {"focus": 0x0098091c})
    page.defaults.click()
    touched = {cid for cid, _ in live.writes}
    assert 0x00980900 in touched and 0x0098091b in touched, "picture controls reset"
    assert 0x009a090a not in touched, "the focus motor stays where it is"
    assert 0x0098091c not in touched, "and so does a control routed to focus"
    assert 0x009a090c not in touched, "a switch is the camera's"


def test_only_int_controls_can_fill_a_slot(qapp):
    from darlaston.ui.controls_ui import ControlsPage

    assert roles.resolve(_controls(), {"hue": 0x009a090c})["hue"] is None, "a bool is no bar"
    page = ControlsPage(_Live(_controls()), {})
    assert page._rows[4]["widgets"][3].isHidden(), "no use-as on a switch"


def test_a_route_reaches_library_json_and_cancel_discards(qapp, tmp_path, monkeypatch):
    import json
    from darlaston.session.model import Library
    from darlaston.ui.setup_ui import CameraDialog

    monkeypatch.setattr("darlaston.camera.discovery.look", lambda: [])
    lib = Library(tmp_path / "library.json")
    lib.remember_camera("AAA", "Open"); lib.remember_camera("BBB", "Other"); lib.save()
    d = CameraDialog(lib, "AAA", None, live=_Live(_controls()))
    assert d.tabs.isTabEnabled(1)
    box = d._controls_page._rows[3]["widgets"][3]
    box.setCurrentIndex(box.findData("focus"))
    rows = {d.list.item(i).data(256): i for i in range(d.list.count())}
    d.list.setCurrentRow(rows["BBB"])
    assert not d.tabs.isTabEnabled(1), "the page is the open camera's only"
    d.list.setCurrentRow(rows["AAA"])
    assert d.tabs.isTabEnabled(1)
    d._save()
    saved = json.loads((tmp_path / "library.json").read_text())
    assert saved["cameras"]["AAA"]["roles"] == {"focus": 0x0098091c}
    d2 = CameraDialog(lib, "AAA", None, live=_Live(_controls()))
    b = d2._controls_page._rows[0]["widgets"][3]
    b.setCurrentIndex(b.findData("none"))
    d2.reject()
    assert lib.cameras["AAA"].roles == {"focus": 0x0098091c}


def test_the_session_replays_only_what_it_was_asked_to_remember():
    from darlaston.camera.session import CameraSession
    import threading

    class Backend:
        info = None
        def __init__(self): self.writes = []; self.autos = []
        def set_exposure(self, us): pass
        def set_gain(self, pct): pass
        def set_control(self, cid, value): self.writes.append((cid, value)); return True
        def set_auto(self, cid, on): self.autos.append((cid, on)); return True

    session = CameraSession.__new__(CameraSession)
    session._lock = threading.Lock(); session._backend = Backend()
    session._exposure_us = 8000; session._gain_pct = 100
    session._auto_exposure = None; session._control_values = {}; session._auto_values = {}
    session.framerate_cap = 0
    session.set_control(0x009a0903, 1)                 # the page: not remembered
    session.set_control(0x00980900, 10, remember=True)  # the row: remembered
    session.set_auto(0x0098091a, True, remember=True)
    fresh = Backend()
    session._restore_controls(fresh)
    assert fresh.writes == [(0x00980900, 10)]
    assert fresh.autos == [(0x0098091a, True)]
