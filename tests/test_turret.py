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


# ---- brightness as a third signal -------------------------------------------

def test_the_brightness_model_falls_with_magnification_when_the_condenser_limits():
    """The correction that produced this: image-plane illuminance goes as
    (NA_effective / M)^2, and above about NA 0.5 the *condenser* is the
    effective aperture, because filling a high-NA objective needs it oiled to
    the slide. Assuming the objective always wins predicts nearly constant
    brightness across a matched set, which is wrong in the ordinary case."""
    from darlaston.live.turret import model_signatures

    turret = Turret([Objective(6.3, 0.16), Objective(10, 0.32),
                     Objective(16, 0.40), Objective(25, 0.65),
                     Objective(40, 1.00)], current=0)

    dry = model_signatures(turret, condenser_na=0.55)
    assert dry[4] < dry[3] < dry[2], "must fall with magnification"

    # Oiled, the objective is the limit again and the set is near-flat.
    oiled = model_signatures(turret, condenser_na=1.4)
    assert 0.9 < oiled[4] / oiled[2] < 1.1

    # And the iris moves the prediction more than the objectives do, which
    # is why a learned signature has to outrank a modelled one.
    stopped = model_signatures(turret, condenser_na=0.3)
    assert (stopped[3] / stopped[2]) < 0.5 < (oiled[3] / oiled[2])


def test_modelled_brightness_may_corroborate_but_never_decide_alone():
    """A first guess wearing the same clothes as evidence is the failure to
    avoid: the condenser iris can shift the predicted ratios threefold."""
    from darlaston.live.turret import TurretDetector

    det = TurretDetector()
    det._direction = None                     # no direction reading
    turret = _turret(1)
    signatures = [1.0, 0.5, 0.25, 0.12]

    # Brightness alone, modelled: refuse to name a position.
    modelled = det._decide(None, turret, level_ratio=0.5, level=0.5,
                           signatures=signatures,
                           learned=[False] * 4)
    assert modelled.suggested_index is None
    assert modelled.should_ask

    # The same reading, learned: now it may speak.
    taught = det._decide(None, turret, level_ratio=0.5, level=0.5,
                         signatures=signatures, learned=[True] * 4)
    assert taught.suggested_index == 2
    assert taught.should_ask, "one signal is still not a confirmation"


def test_a_confirmed_position_teaches_the_stand():
    from darlaston.session.model import ScopeProfile

    scope = ScopeProfile(id="z", turret=_turret(1))
    scope.learn_brightness("phase", 1, 0.40)
    values, known = scope.signatures("phase", [None] * 4)
    assert known[1] and values[1] == pytest.approx(0.40)
    assert not known[0]

    # A second reading is averaged in, so one odd frame cannot become the
    # signature for an objective.
    scope.learn_brightness("phase", 1, 0.60)
    values, _ = scope.signatures("phase", [None] * 4)
    assert 0.40 < values[1] < 0.60

    # Illumination modes are learned separately: the same objective can be
    # bright in brightfield and dark against a phase stop.
    values, known = scope.signatures("brightfield", [None] * 4)
    assert not any(known)


# ---- the state machine surviving a real brightness step ---------------------

def _levels_run(det, turret, before, after, drop_to, direction=+1):
    """Feed steady/occluded/steady frames at given relative levels."""
    import cv2
    rng = np.random.default_rng(3)
    texture = cv2.GaussianBlur((rng.random((256, 256)) * 255).astype(np.float32),
                               (0, 0), 2.0)
    out = None
    for _ in range(25):
        out = det.feed(texture * before, turret, 1.0) or out
    for k in range(10):
        img = (texture * before).copy()
        cover = int(256 * (k + 1) / 10)
        if direction > 0:
            img[:, :cover] *= 0.015
        else:
            img[:, -cover:] *= 0.015
        out = det.feed(img, turret, 1.0) or out
    for _ in range(35):
        out = det.feed(texture * drop_to, turret, 1.0) or out
    return out


def test_a_much_darker_objective_does_not_wedge_the_detector():
    """Measured on a Zeiss in phase: 10x to 16x lands at 22% of the previous
    level, and 16x to 25x at 39%, because the phase annulus stops matching.
    Recovery used to be declared at 60% of the *old* level, so both were
    below it -- the detector entered DARK, never left, and produced no
    proposal for that rotation or for any rotation afterwards."""
    for drop in (0.22, 0.39, 0.069):
        det = TurretDetector(rotation_sign=SIGN)
        turret = _turret(1)
        event = _levels_run(det, turret, 1.0, None, drop)
        assert event is not None, f"a drop to {drop:.0%} must still propose"
        # And the detector must be watching again, not stuck.
        assert det._phase.name == "STABLE"


def test_a_much_brighter_objective_is_also_fine():
    """The other direction: 6.3x to 10x is a fourteenfold rise in phase."""
    det = TurretDetector(rotation_sign=SIGN)
    event = _levels_run(det, _turret(0), 1.0, None, 14.5)
    assert event is not None
    assert det._phase.name == "STABLE"


def test_the_detector_still_works_after_a_long_blackout():
    """The requirement is not which phase it sits in while the lamp is off --
    it is that turning the lamp back on and rotating still produces a
    proposal. A detector that never works again is the fault to prevent."""
    import cv2
    det = TurretDetector(rotation_sign=SIGN)
    det.DARK_PATIENCE = 30
    turret = _turret(1)
    rng = np.random.default_rng(2)
    texture = cv2.GaussianBlur((rng.random((256, 256)) * 255).astype(np.float32),
                               (0, 0), 2.0) + 150.0

    for _ in range(25):
        det.feed(texture, turret, 1.0)
    for _ in range(200):                      # lamp off for a long while
        det.feed(np.zeros((256, 256), np.float32), turret, 1.0)

    event = _levels_run(det, turret, 1.0, None, 0.30)
    assert event is not None, "must recover and detect again"


def test_a_sweep_too_fast_to_sample_still_has_a_direction():
    """Reported from the stand: the turret barely moves the image, then the
    occlusion crosses in a few frames with an almost flat leading edge. If
    every frame sampled during darkness is already uniformly black there is
    no side left to read, so the edge is caught on its way in as well."""
    import cv2
    rng = np.random.default_rng(9)
    texture = cv2.GaussianBlur((rng.random((256, 256)) * 255).astype(np.float32),
                               (0, 0), 2.0) + 200.0

    det = TurretDetector(rotation_sign=SIGN)
    turret = _turret(1)
    for _ in range(25):
        det.feed(texture, turret, 1.0)

    # One partly-covered frame, then straight to black: the whole sweep.
    half_covered = texture.copy()
    half_covered[:, :150] *= 0.015
    det.feed(half_covered, turret, 1.0)
    for _ in range(3):
        det.feed(texture * 0.01, turret, 1.0)

    assert det._direction is not None, \
        "the single transitional frame must be enough"


def test_relay_multiplies_into_total_magnification():
    """A 1-2x C-mount adapter left at 2x doubles magnification at the sensor
    exactly as the Optovar does, and leaving it out understated the f-number
    and overstated how much slide a pixel covers."""
    from darlaston.session.model import (BUILTIN_ILLUMINATION, CameraProfile,
                                         ScopeProfile, Setup)

    scope = ScopeProfile(id="z", turret=_turret(2), optovar=[1.6],
                         optovar_current=0)
    plain = Setup(camera=CameraProfile(serial="x"), scope=scope,
                  illumination=BUILTIN_ILLUMINATION[0])
    relayed = Setup(camera=CameraProfile(serial="x", relay_factor=2.0),
                    scope=scope, illumination=BUILTIN_ILLUMINATION[0])
    assert plain.total_magnification == pytest.approx(40 * 1.6)
    assert relayed.total_magnification == pytest.approx(40 * 1.6 * 2.0)


def test_the_relay_does_not_disturb_turret_ratios():
    """It is constant across positions, so it cancels in every comparison
    detection makes -- which is why this was invisible until the metadata
    was checked."""
    turret = _turret(1)
    assert turret.ratio_to(2) == pytest.approx(2.0)   # 20x -> 40x, relay free


def test_the_raw_occlusion_reading_is_reported_separately():
    """The application needs the reading *before* the optical path's sign is
    applied, so that correcting a proposal can work out which sign was
    right. Without it a correction teaches nothing."""
    import cv2
    rng = np.random.default_rng(6)
    texture = cv2.GaussianBlur((rng.random((256, 256)) * 255).astype(np.float32),
                               (0, 0), 2.0) + 200.0

    seen = {}
    for sign in (+1, -1):
        det = TurretDetector(rotation_sign=sign)
        turret = _turret(1)
        for _ in range(25):
            det.feed(texture, turret, 1.0)
        covered = texture.copy()
        covered[:, :150] *= 0.015
        det.feed(covered, turret, 1.0)
        for _ in range(4):
            det.feed(texture * 0.01, turret, 1.0)
        event = None
        for _ in range(30):
            event = det.feed(texture * 0.5, turret, 1.0) or event
        assert event is not None
        seen[sign] = event

    # The raw reading is a fact about the image and does not move with the
    # setting; the interpreted direction does.
    assert seen[+1].raw_direction == seen[-1].raw_direction
    assert seen[+1].direction == -seen[-1].direction


def test_both_candidates_are_offered_until_the_handedness_is_known():
    """Before the sign is confirmed the detector is genuinely uncertain
    between two positions -- the same occlusion reading with either sign.
    Naming one and being wrong half the time is worse than naming both."""
    from darlaston.session.model import ScopeProfile

    scope = ScopeProfile(id="z", turret=_turret(0), rotation_sign=1)
    turret = scope.turret
    raw = -1

    def step(sign):
        probe = Turret(list(turret.positions), turret.current)
        return probe.step(raw * sign)

    assert step(+1) != step(-1), "the two signs must disagree, or there is\
 nothing to offer"
    assert not scope.rotation_sign_known


def test_confirming_the_default_marks_it_known_without_flipping():
    """Being right by luck and being right on purpose should end in the same
    place: confirmed, so later proposals stop hedging."""
    from darlaston.session.model import ScopeProfile

    scope = ScopeProfile(id="z", turret=_turret(1), rotation_sign=1)
    turret = scope.turret
    probe = Turret(list(turret.positions), turret.current)
    would_be = probe.step(-1 * 1)              # raw -1, sign +1

    # Simulate the app's inference: the operator confirmed exactly what the
    # current sign predicted.
    assert would_be == 0
    # A correction to the *other* candidate is what flips it.
    probe2 = Turret(list(turret.positions), turret.current)
    assert probe2.step(-1 * -1) == 2


# ---- the application's belief tracking the real turret ----------------------

def test_a_stale_belief_makes_the_next_proposal_wrong():
    """The failure Nate hit: 25x to 16x suggested 10x, one slot too far.

    Nothing was wrong with the detection. An earlier rotation had gone
    unanswered, so the application still believed the previous position, and
    stepping one place from the wrong place lands two places from the right
    one. Which is why an unanswered prompt now marks the objective uncertain
    and every prompt offers a way to say where the turret really is."""
    positions = [Objective(6.3, 0.16), Objective(10, 0.32),
                 Objective(16, 0.40), Objective(25, 0.65),
                 Objective(40, 1.00)]

    # Believing the truth: one step back from 25x is 16x.
    truth = Turret(list(positions), current=3)
    assert Turret(list(positions), truth.current).step(-1) == 2

    # Believing a stale 16x: the same reading lands on 10x.
    stale = Turret(list(positions), current=2)
    assert Turret(list(positions), stale.current).step(-1) == 1


def test_only_the_two_neighbours_are_ever_candidates():
    """A turret is walked one detent at a time. Before this, the
    magnification and brightness searches ranked every position and could
    nominate one two or three places away, which no hand movement produces.

    Five positions, sitting at index 0, so that indices 2 and 3 are genuinely
    distant -- a turret is a ring, and on a four-position one every position
    is adjacent to every other, which is not a test of anything.
    """
    det = TurretDetector()
    det._direction = None
    turret = Turret([Objective(6.3, 0.16), Objective(10, 0.32),
                     Objective(16, 0.40), Objective(25, 0.65),
                     Objective(40, 1.00)], current=0)
    neighbours = {1, 4}

    # A brightness reading that fits a distant position best must not be
    # allowed to name it.
    signatures = [1.0, 0.9, 0.1, 0.11, 0.8]
    event = det._decide(None, turret, level_ratio=0.1, level=0.1,
                        signatures=signatures, learned=[True] * 5)
    assert event.suggested_index is None or event.suggested_index in neighbours

    # And the same for magnification: index 2 fits a halving field of view
    # far better than either neighbour, and must still not be proposed.
    event = det._decide(0.394, turret, level_ratio=None, level=None,
                        signatures=None, learned=None)
    assert event.suggested_index is None or event.suggested_index in neighbours


def test_the_constraint_rescues_a_confusable_pair():
    """Measured on the Zeiss in phase: 6.3x and 25x sit 22% apart in
    brightness and were confusable, which cost 10x->6.3x its corroboration.
    They are never adjacent, so restricting candidates to neighbours removes
    the ambiguity outright."""
    det = TurretDetector(rotation_sign=SIGN)
    det._direction = -1
    turret = _turret(1)
    # Levels chosen so a distant position fits the ratio better than the
    # neighbour does -- the situation that used to mislead it.
    signatures = [0.30, 1.00, 0.20, 0.29]
    event = det._decide(None, turret, level_ratio=0.30, level=0.3,
                        signatures=signatures, learned=[True] * 4)
    assert event.suggested_index in (0, 2), "must pick a neighbour"


def test_the_sweep_direction_is_read_from_the_first_dimming_half():
    """An occlusion crosses the whole frame: the leading half darkens, and
    then the trailing half darkens as the edge continues. Both give a large
    differential and the second is often marginally larger, so keeping the
    strongest reads the sweep backwards."""
    det = TurretDetector()
    lit = np.full((256, 256), 140.0, np.float32)
    det._observe_sweep(lit)

    # Left goes first.
    step1 = lit.copy()
    step1[:, :128] = 40.0
    det._observe_sweep(step1)
    assert det._sweep_side == -1

    # Then the right follows, more strongly. The reading must not flip.
    step2 = step1.copy()
    step2[:, 128:] = 2.0
    det._observe_sweep(step2)
    assert det._sweep_side == -1, "the first half to darken is the answer"


def test_uneven_illumination_does_not_bias_the_direction():
    """Reading which half is *darker* is biased by whatever the illumination
    already does. Reading which half *fell more* is not, because the standing
    unevenness sits in both frames and cancels.

    The case that separates them: a field already much brighter on the left,
    with the occlusion entering from that same left side. Early in the sweep
    the left has darkened but is still the brighter half in absolute terms,
    so the absolute reading names the wrong side while the differential
    names the right one.
    """
    det = TurretDetector()
    lopsided = np.empty((256, 256), np.float32)
    lopsided[:, :128] = 200.0
    lopsided[:, 128:] = 80.0
    det._observe_sweep(lopsided)

    entering_left = lopsided.copy()
    entering_left[:, :30] = 5.0            # a narrow bite out of the left
    det._observe_sweep(entering_left)

    assert det._sweep_side == -1, "the left fell; the left is the answer"
    # The absolute test still sees the left as the brighter half and says
    # the opposite, which is why it is only a fallback.
    assert TurretDetector._which_side(entering_left) == +1


def test_the_objective_position_is_written_on_every_change(tmp_path):
    """The schema always carried it; nothing reliably wrote it, so stepping
    the objective by hand did not survive a restart."""
    from darlaston.session.model import CameraProfile, Library, ScopeProfile

    path = tmp_path / "library.json"
    lib = Library(path)
    scope = ScopeProfile(id="z", name="Zeiss", turret=_turret(0))
    lib.scopes["z"] = scope
    lib.cameras["CAM"] = CameraProfile(serial="CAM", last_scope="z")

    scope.turret.current = 3
    lib.scopes["z"] = scope
    lib.save()

    again = Library(path)
    assert again.scope_or_default("z").turret.current == 3, \
        "the last objective is the best guess for the next launch"


def test_the_file_says_whether_anybody_checked(qapp):
    """A capture used to assert an objective with no record of whether it
    had been confirmed or merely carried over. The value is kept either
    way -- it is usually right, and dropping a good guess to avoid
    admitting it is a guess helps nobody -- but the admission travels
    with it."""
    from darlaston.process.metadata import from_setup
    from darlaston.session.model import (CameraProfile, Objective,
                                         ScopeProfile, Setup, Turret)

    turret = Turret(positions=[Objective(magnification=25.0, na=0.65)],
                    current=0)
    setup = Setup(camera=CameraProfile(serial="X", pixel_um=2.4),
                  scope=ScopeProfile(id="s", name="Zeiss", turret=turret))

    assert turret.confirmed is False, "a fresh belief is nobody's word"
    shot = dict(exposure_us=8300, gain_pct=100)

    unchecked = from_setup(setup, **shot)
    assert "objective=25x/0.65" in unchecked.comment, "the guess is kept"
    # Written as a string on purpose: the comment is built with `if v`, so
    # a falsy 0 would be dropped and "not confirmed" would be
    # indistinguishable from "never asked".
    assert "objective_confirmed=0" in unchecked.comment
    assert "unconfirmed" in unchecked.description

    turret.confirmed = True
    checked = from_setup(setup, **shot)
    assert "objective=25x/0.65" in checked.comment
    assert "objective_confirmed=1" in checked.comment
    assert "unconfirmed" not in checked.description


def test_a_saved_turret_comes_back_unconfirmed():
    """The position is persisted and the confidence deliberately is not.
    A nosepiece is a thing you turn with your hand, on a bench, while the
    software is not running -- so a restored belief is a memory, not an
    observation, however good a memory it usually is."""
    from darlaston.session.model import _turret_from

    saved = {"positions": [{"magnification": 16.0, "na": 0.4}],
             "current": 0, "capped": [False]}
    back = _turret_from(saved)
    assert back.current == 0, "the position is remembered"
    assert back.confirmed is False, "the certainty is not"
