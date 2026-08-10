"""The slide map as a place-recognition database.

Held to the promises that make it a headline feature: drift dies over
trodden ground, a lost position recovers anywhere on familiar ground
and rejoins the same origin, a big claim needs two agreeing witnesses,
blank glass is never matched, and a resumed-but-possibly-wrong position
stays under suspicion for a bounded number of sweeps.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from darlaston.camera.mock import _render_scene
from darlaston.live import relocate
from darlaston.live.relocate import Relocator
from darlaston.live.tracker import SlideMap

W, H = 1824, 1216
SW, SH = W * 2, H * 2


@pytest.fixture(scope="module")
def scene():
    return _render_scene(SW, SH, seed=23)


def frame_at(scene, x, y):
    x = int(np.clip(x, 0, SW - W))
    y = int(np.clip(y, 0, SH - H))
    return cv2.cvtColor(scene[y:y + H, x:x + W], cv2.COLOR_GRAY2BGR)


def bank(scene, x_range=None):
    m = SlideMap()
    x_lo, x_hi = x_range or (0, SW - W)
    xs = range(x_lo, x_hi + 1, int(W * 0.30))
    ys = range(0, SH - H + 1, int(H * 0.30))
    for y in ys:
        for x in xs:
            m.observe((float(x), float(y)), frame_at(scene, x, y), True)
    return m


def until_fix(r, preview, pos, tracking, snaps, tries=60):
    for i in range(tries):
        fix = r.observe(preview, pos, tracking, snaps)
        if fix is not None:
            return fix, i + 1
    return None, tries


def test_drift_dies_over_trodden_ground(scene):
    m = bank(scene)
    r = Relocator()
    truth = (900.0, 700.0)
    believed = (truth[0] + 30.0, truth[1] + 20.0)
    fix, _n = until_fix(r, frame_at(scene, *truth), believed, True,
                        m.terrain)
    assert fix is not None, "a drift-scale error went uncorrected"
    assert np.hypot(fix.pos[0] - truth[0], fix.pos[1] - truth[1]) < 10
    assert fix.delta == pytest.approx(
        (truth[0] - believed[0], truth[1] - believed[1]), abs=10)


def test_a_large_claim_needs_two_witnesses(scene):
    """Arrangements repeat similar valves; one confident match is not
    proof. A correction past drift scale must be seen twice."""
    m = bank(scene)
    r = Relocator()
    truth = (900.0, 700.0)
    believed = (truth[0] + 300.0, truth[1])
    fix, n = until_fix(r, frame_at(scene, *truth), believed, True,
                       m.terrain)
    assert fix is not None
    assert n > relocate.CONTINUOUS_EVERY, (
        "a 300 px correction was believed on a single match")
    assert np.hypot(fix.pos[0] - truth[0], fix.pos[1] - truth[1]) < 10


def test_lost_recovers_anywhere_on_familiar_ground(scene):
    m = bank(scene)
    r = Relocator()
    truth = (1500.0, 900.0)
    frame = frame_at(scene, *truth)
    fix, _n = until_fix(r, frame, (0.0, 0.0), False, m.terrain)
    assert fix is not None, "the sweep never found familiar ground"
    assert np.hypot(fix.pos[0] - truth[0], fix.pos[1] - truth[1]) < 10
    assert not r.searching, "found, but still says searching"


def test_searching_is_said_while_lost(scene):
    m = bank(scene)
    r = Relocator()
    r.observe(frame_at(scene, 500, 500), None, False, m.terrain)
    assert r.searching


def test_blank_glass_is_never_matched(scene):
    """Bare glass correlates with everything and means nothing, so a
    featureless probe must not produce a fix -- Darlaston's clean
    mounts are the case that started all of this."""
    m = bank(scene)
    r = Relocator()
    flat = np.full((H, W, 3), 120, np.uint8)
    for _ in range(30):
        assert r.observe(flat, None, False, m.terrain) is None
    assert r.searching


def test_a_resumed_position_is_suspect_until_confirmed(scene):
    """Crossing blank glass, the tracker re-keys on whatever ground it
    lands on and reports itself locked at the held position -- wrong by
    the whole crossing. The bank corrects it."""
    m = bank(scene)
    r = Relocator()
    truth = (1700.0, 200.0)
    frame = frame_at(scene, *truth)
    # One lost frame marks the suspicion...
    r.observe(np.full((H, W, 3), 120, np.uint8), (600.0, 300.0), False,
              m.terrain)
    # ...then the tracker resumes, confidently wrong by 1100 px.
    fix, _n = until_fix(r, frame, (600.0, 300.0), True, m.terrain)
    assert fix is not None, "a wrong resume was believed without question"
    assert np.hypot(fix.pos[0] - truth[0], fix.pos[1] - truth[1]) < 10


def test_suspicion_of_new_ground_ends(scene):
    """Ground beyond the crossing may genuinely never have been seen.
    Suspicion has a budget of sweeps, then dead reckoning is accepted
    rather than searched for for ever."""
    m = bank(scene, x_range=(0, W // 2))          # only the left edge
    r = Relocator()
    unseen = frame_at(scene, SW - W, SH - H)      # far corner, never banked
    r.observe(np.full((H, W, 3), 120, np.uint8), (100.0, 100.0), False,
              m.terrain)
    for _ in range(80):
        assert r.observe(unseen, (100.0, 100.0), True,
                         m.terrain) is None
    assert r._suspect_sweeps == 0
    assert not r.searching, "still suspicious long after the budget"


def test_a_foreign_preview_size_is_refused(scene):
    """Terrain painted at another preview scale measures another world;
    it is refused rather than matched wrongly. The map is cleared on a
    mode change, so this is a guard, not a path."""
    m = bank(scene)
    r = Relocator()
    shrunk = cv2.resize(frame_at(scene, 900, 700), (912, 608))
    fix, _n = until_fix(r, shrunk, (900.0, 700.0), True, m.terrain,
                        tries=20)
    assert fix is None
