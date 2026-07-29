"""Detecting an objective change from the image alone.

Everything here runs against the mock's magnification axis and simulated
turret occlusion, because those are the two signals the detector reads and
there is no other way to produce them on demand.
"""
import numpy as np
import pytest

from darlaston.camera.mock import MockCamera, _RESOLUTIONS
from darlaston.live.turret import TurretDetector
from darlaston.session.model import Objective, Turret

RES = _RESOLUTIONS[2]

#: The mock darkens the left edge for a positive rotation; the detector reads
#: a darkening left edge as -1 before the stand's sign is applied. Which of
#: those means "next objective" is a property of real hardware, so the tests
#: fix a sign and check consistency rather than pretending there is a truth.
SIGN = -1


def _turret(current):
    return Turret([Objective(10, 0.30), Objective(20, 0.50),
                   Objective(40, 0.75), Objective(100, 1.30)], current=current)


def _rotate(from_mag, to_mag, current, direction, sign=SIGN, steps=8):
    """Drive a whole rotation through the mock and return the event."""
    cam = MockCamera()
    cam.open()
    cam.mag_reference = 10.0
    cam.magnification = float(from_mag)
    turret = _turret(current)
    det = TurretDetector(rotation_sign=sign)
    buf = np.empty((RES.height, RES.width, 3), np.uint8)

    def push(n=1):
        got = None
        for _ in range(n):
            cam._render_into(buf, RES)
            event = det.feed(buf[:, :, 1], turret)
            if event is not None:
                got = event
        return got

    push(25)                                   # settle on the old objective
    for _ in cam.rotate_turret(float(to_mag), direction=direction, steps=steps):
        event = push(2)
        if event is not None:
            cam.close()
            return event
    event = push(30)
    cam.close()
    return event


# ---- the measurement itself -------------------------------------------------

def test_scale_ratio_recovers_a_known_zoom():
    import cv2
    rng = np.random.default_rng(5)
    base = cv2.GaussianBlur((rng.random((256, 256)) * 255).astype(np.float32),
                            (0, 0), 2.0)

    def zoom(img, s):
        h, w = img.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, s)
        return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR)

    # Field of view is the reciprocal of how much larger things got.
    for scale in (1.25, 1.5, 2.0, 0.8, 0.5):
        got = TurretDetector._scale_ratio(base, zoom(base, scale))
        assert got is not None
        assert abs(got - 1.0 / scale) / (1.0 / scale) < 0.05


def test_scale_ratio_refuses_a_featureless_field():
    """A blank field correlates with itself perfectly and would report a
    confident 'no scale change' -- which means 'I cannot tell', not 'nothing
    moved'. This application crosses blank glass constantly, so that reading
    would arrive often and be believed."""
    flat = np.full((256, 256), 500.0, np.float32)
    assert TurretDetector._scale_ratio(flat, flat * 1.01) is None

    # Faint structure is still structure; the guard must not swallow a real
    # darkfield frame with a few bright diatoms on black.
    sparse = np.full((256, 256), 20.0, np.float32)
    sparse[100:120, 100:140] = 900.0
    sparse[60:70, 180:200] = 700.0
    assert TurretDetector._scale_ratio(sparse, sparse) is not None


# ---- whole rotations --------------------------------------------------------

@pytest.mark.parametrize("frm,to,current,expect,direction", [
    (20, 40, 1, 2, +1),
    (40, 20, 2, 1, -1),
    (40, 100, 2, 3, +1),
    (10, 20, 0, 1, +1),
    (20, 10, 1, 0, -1),
    (100, 40, 3, 2, -1),
])
def test_single_step_rotations_are_identified(frm, to, current, expect,
                                              direction):
    event = _rotate(frm, to, current, direction)
    assert event is not None, "a rotation must produce a proposal"
    assert event.suggested_index == expect
    assert event.agree, "both signals should corroborate on a clean step"
    assert event.confidence >= 0.9
    # And the measured field-of-view ratio should be near the truth, not
    # merely rank the right answer first.
    assert event.scale_ratio is not None
    assert abs(event.scale_ratio - frm / to) / (frm / to) < 0.15


def test_nothing_is_proposed_when_nothing_moves():
    cam = MockCamera()
    cam.open()
    cam.mag_reference = 10.0
    cam.magnification = 40.0
    det = TurretDetector(rotation_sign=SIGN)
    buf = np.empty((RES.height, RES.width, 3), np.uint8)
    for _ in range(60):
        cam._render_into(buf, RES)
        assert det.feed(buf[:, :, 1], _turret(2)) is None
    cam.close()


def test_a_lamp_dimmed_slowly_is_not_a_rotation():
    """Turning the lamp down is not turning the turret, and the running mean
    tracks slowly so it cannot be mistaken for one."""
    cam = MockCamera()
    cam.open()
    cam.mag_reference = 10.0
    cam.magnification = 40.0
    det = TurretDetector(rotation_sign=SIGN)
    buf = np.empty((RES.height, RES.width, 3), np.uint8)
    for step in range(60):
        cam.set_exposure(int(8330 * (1.0 - step * 0.012)))
        cam._render_into(buf, RES)
        assert det.feed(buf[:, :, 1], _turret(2)) is None
    cam.close()


def test_the_stand_sign_flips_the_proposal():
    """The direction reading is unambiguous; what it means for the index is
    a property of the hardware, so the sign must actually change the answer."""
    a = _rotate(20, 40, 1, +1, sign=+1)
    b = _rotate(20, 40, 1, +1, sign=-1)
    assert a is not None and b is not None
    assert a.direction == -b.direction


def test_an_unknown_turret_still_reports_the_change():
    """Without a turret to match against there is no position to suggest,
    but 'something changed' is still worth saying."""
    cam = MockCamera()
    cam.open()
    cam.mag_reference = 10.0
    cam.magnification = 20.0
    det = TurretDetector(rotation_sign=SIGN)
    buf = np.empty((RES.height, RES.width, 3), np.uint8)

    def push(n=1):
        got = None
        for _ in range(n):
            cam._render_into(buf, RES)
            event = det.feed(buf[:, :, 1], None)
            if event is not None:
                got = event
        return got

    push(25)
    seen = None
    for _ in cam.rotate_turret(40.0, direction=+1):
        seen = push(2) or seen
    seen = push(30) or seen
    cam.close()
    assert seen is not None
    assert seen.suggested_index is None
    assert seen.should_ask


# ---- the library actually being read back -----------------------------------

def test_a_configured_stand_survives_a_restart(tmp_path):
    """The setup editor wrote to disk correctly and the main window ignored
    it, so a configured Zeiss reverted to a placeholder turret on every
    launch. The write was never the problem; nothing read it back."""
    from darlaston.session.model import CameraProfile, Library, ScopeProfile

    path = tmp_path / "library.json"
    lib = Library(path)
    scope = ScopeProfile(
        id="zeiss-universal", name="Zeiss Universal",
        turret=Turret([Objective(6.3, 0.16, kind="Plan"),
                       Objective(40, 1.0, kind="Apo Ph3", immersion="oil")],
                      current=1),
        optovar=[1.2, 1.6, 2.0])
    lib.scopes[scope.id] = scope
    lib.cameras["CAM1"] = CameraProfile(serial="CAM1", name="E3ISPM",
                                        last_scope="zeiss-universal")
    lib.save()

    again = Library(path)
    found = again.scope_or_default(again.cameras["CAM1"].last_scope)
    assert found is not None, "the camera's stand must be found again"
    assert found.name == "Zeiss Universal"
    assert [o.label for o in found.turret.positions] == \
        ["6.3×/0.16", "40×/1 oil"]
    assert found.optovar == [1.2, 1.6, 2.0]


def test_new_optics_fields_survive_a_round_trip(tmp_path):
    """Maker, serial and working distance feed EXIF, and tube length and
    rotation sign feed detection — all of them useless if they do not
    persist."""
    from darlaston.session.model import Library, ScopeProfile

    path = tmp_path / "library.json"
    lib = Library(path)
    lib.scopes["z"] = ScopeProfile(
        id="z", name="Zeiss", tube_length_mm=160.0, rotation_sign=-1,
        turret=Turret([Objective(40, 0.75, maker="Carl Zeiss", serial="4512873",
                                 working_distance_mm=0.6)], current=0))
    lib.save()

    back = Library(path).scopes["z"]
    assert back.rotation_sign == -1
    assert back.tube_length_mm == 160.0
    obj = back.turret.positions[0]
    assert obj.maker == "Carl Zeiss"
    assert obj.serial == "4512873"
    assert obj.working_distance_mm == 0.6
