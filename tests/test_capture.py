"""The capture path: bursts, averaging arithmetic, and the moved verdict."""
import threading
from pathlib import Path
import types

import cv2
import numpy as np
import pytest

from darlaston.camera.mock import MockCamera
from darlaston.capture.still import CaptureResult, StillCapture
from darlaston.session.settings import Settings


def write_dng(path, raw, **kw):
    """A one-shot Bayer DNG, for tests that just need a file on disk."""
    from darlaston.process import dng
    from darlaston.process.tiff import make_preview
    h, w = raw.shape
    return dng.write_bayer_streamed(
        path, lambda s, c: raw[s:s + c], h, w,
        preview=make_preview(raw, bayer=True, white=kw.pop("white", 4095)),
        **kw)


def _shoot(tmp_path, frames=1, pipeline=None, cam=None):
    cam = cam or MockCamera()
    cam.open()
    session = types.SimpleNamespace(backend=cam)
    settings = Settings(capture_root=str(tmp_path))
    results = []
    done = threading.Event()
    cap = StillCapture(session, settings,
                       on_result=lambda r: (results.append(r), done.set()),
                       pipeline=pipeline)
    assert cap.trigger(None, subject="t", frames=frames)
    assert done.wait(30)
    cam.close()
    return results[-1]


def test_single_capture_writes_a_file(tmp_path):
    r = _shoot(tmp_path)
    assert r.ok, r.message
    assert r.path.exists()
    assert r.moved is None, "no pipeline, so the guard must stay silent"
    assert "avg" not in r.summary


def test_burst_averages_and_says_so(tmp_path):
    cam = MockCamera()
    grabs = []
    real = cam.grab_raw
    cam.grab_raw = lambda *a, **k: (grabs.append(1), real())[1]
    r = _shoot(tmp_path, frames=4, cam=cam)
    assert r.ok, r.message
    assert len(grabs) == 4
    assert "avg4" in r.summary
    # What matters is that it exists and reported itself; the size
    # question is covered properly in test_tiff.
    assert r.path.exists()


def test_burst_average_is_the_mean_not_the_sum(tmp_path):
    """A sum would clip white; a mean preserves exposure. Feed frames of
    constant known value and check the written scale end to end."""
    cam = MockCamera()
    cam.open()

    calls = {"n": 0}

    class Fake:
        def __init__(self, value):
            self.data = np.full((8, 8), value, np.uint16)
            self.exposure_us, self.gain_pct = 1000, 100
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_grab(*a, **k):
        calls["n"] += 1
        return Fake(1000 + calls["n"])       # 1001, 1002, 1003, 1004

    cam.grab_raw = fake_grab
    captured = {}
    from darlaston.process import dng as dng_mod
    real_write = dng_mod.write_bayer_streamed

    def spy_write(path, rows, h, w, **kw):
        captured["raw"] = rows(0, h).copy()
        captured["white"] = kw.get("white")
        captured["bits"] = kw.get("bits")
        return real_write(path, rows, h, w, **kw)

    dng_mod.write_bayer_streamed, spy = spy_write, real_write
    try:
        r = _shoot_with(cam, frames=4)
    finally:
        dng_mod.write_bayer_streamed = spy
    assert r.ok, r.message
    # mean of 1001..1004 is 1002.5; scaled x16 = 16040.
    assert captured["white"] == 4095 * 16
    assert captured["bits"] == 16, "an averaged frame must not be packed to 12"
    assert int(captured["raw"][0, 0]) == 16040


def _shoot_with(cam, frames):
    import tempfile
    session = types.SimpleNamespace(backend=cam)
    with tempfile.TemporaryDirectory() as td:
        settings = Settings(capture_root=td)
        results = []
        done = threading.Event()
        cap = StillCapture(session, settings,
                           on_result=lambda r: (results.append(r), done.set()))
        assert cap.trigger(None, subject="t", frames=frames)
        assert done.wait(30)
    return results[-1]


class _Bench:
    """A capture that fires instantly and reports back, as the window does.

    `trigger` returning True only means the shutter was claimed. The
    verdict arrives separately, on another thread, through `note_result`
    -- so a harness that only implements `trigger` is not exercising the
    thing that decides what a run reports.
    """

    busy = False

    def __init__(self, timelapse=None, ok=True, applied=("dark",)):
        self.shots = 0
        self.timelapse = timelapse
        self.ok = ok
        self.applied = applied

    def trigger(self, setup=None, subject="", slide="", frames=1):
        self.shots += 1
        if self.timelapse is not None:
            self.timelapse.note_result(types.SimpleNamespace(
                ok=self.ok, path=None, applied=self.applied))
        return True


def _run(tl, seconds=5.0):
    import time as _t
    t0 = _t.monotonic()
    while tl.running and _t.monotonic() - t0 < seconds:
        _t.sleep(0.02)
    assert not tl.running, "the timelapse did not end"


@pytest.mark.serial
def test_timelapse_takes_the_asked_number_and_stops(tmp_path):
    from darlaston.capture.timelapse import Timelapse

    statuses = []
    tl = Timelapse(None, statuses.append)
    cap = _Bench(tl)
    tl._capture = cap
    assert tl.start(0.05, count=3)
    _run(tl)
    assert cap.shots == 3
    # Three *triggers* and three *photographs*, which are not the same
    # claim -- see the failure test below.
    assert statuses[-1].shot == 3
    assert statuses[-1].failed == 0
    assert "finished" in statuses[-1].message
    # The schedule was honoured rather than fired as fast as the loop
    # could go: three shots at 50 ms start-to-start cannot finish in less
    # than two intervals.
    assert len([s for s in statuses if s.running]) >= 3


@pytest.mark.serial
def test_timelapse_counts_photographs_and_not_triggers():
    """`trigger` returns True the moment the shutter is claimed. A run
    where every capture then failed used to finish saying it took them
    all -- and nobody is watching a timelapse, so that line is the only
    account of the night."""
    from darlaston.capture.timelapse import Timelapse

    statuses = []
    tl = Timelapse(None, statuses.append)
    cap = _Bench(tl, ok=False)
    tl._capture = cap
    assert tl.start(0.05, count=4)
    _run(tl)
    assert cap.shots == 4, "the run should still have tried four times"
    assert statuses[-1].shot == 0, "reported photographs that do not exist"
    assert statuses[-1].failed == 4
    assert "4 failed" in statuses[-1].message


@pytest.mark.serial
def test_timelapse_notices_the_dark_expiring_mid_run():
    """The dark ages out and the capture path stops applying it without
    saying so. Elapsed run time is the wrong thing to measure: it called
    a dark shot just under eight hours ago fresh."""
    from darlaston.capture.timelapse import Timelapse

    statuses = []
    tl = Timelapse(None, statuses.append)
    cap = _Bench(tl, applied=("dark",))
    tl._capture = cap
    assert tl.start(0.05, count=3)
    import time as _t
    while cap.shots < 1:
        _t.sleep(0.01)
    cap.applied = ()                       # the store stops handing it over
    _run(tl)
    assert statuses[-1].dark_stale, "the dark expired and nothing said so"


@pytest.mark.serial
def test_a_run_that_never_had_a_dark_is_not_reported_as_stale():
    """Uncalibrated is not the same as expired, and "reshoot it" is bad
    advice about a thing that never existed."""
    from darlaston.capture.timelapse import Timelapse

    statuses = []
    tl = Timelapse(None, statuses.append)
    tl._capture = _Bench(tl, applied=())
    assert tl.start(0.05, count=2)
    _run(tl)
    assert not statuses[-1].dark_stale


@pytest.mark.serial
def test_timelapse_stop_ends_the_run(tmp_path):
    from darlaston.capture.timelapse import Timelapse

    class InstantCapture:
        busy = False
        def trigger(self, *a, **k):
            return True

    tl = Timelapse(InstantCapture(), lambda s: None)
    assert tl.start(60.0, count=0)          # would run forever
    import time as _t
    _t.sleep(0.1)
    tl.stop()
    t0 = _t.monotonic()
    while tl.running and _t.monotonic() - t0 < 3.0:
        _t.sleep(0.02)
    assert not tl.running


def test_timelapse_refuses_a_second_run():
    from darlaston.capture.timelapse import Timelapse

    class InstantCapture:
        busy = False
        def trigger(self, *a, **k):
            return True

    tl = Timelapse(InstantCapture(), lambda s: None)
    assert tl.start(60.0, count=0)
    assert not tl.start(1.0, count=1), "one timelapse at a time"
    tl.stop()


def test_mosaic_session_adopts_undoes_and_reloads(tmp_path):
    from darlaston.capture.mosaic import MosaicSession

    session = MosaicSession(tmp_path, subject="diatoms")
    session.set_meta(objective="40x/0.75", illumination="Darkfield")

    for i, pos in enumerate([(0.0, 0.0), (1500.0, 10.0), (2900.0, -20.0)]):
        f = tmp_path / f"cap{i}.dng"
        f.write_bytes(b"raw" * 10)
        session.adopt(f, pos, (1824, 1216))

    assert [t.filename for t in session.tiles] == \
        ["tile_001.dng", "tile_002.dng", "tile_003.dng"]
    assert (session.dir / "tile_003.dng").exists()

    gone = session.undo()
    assert gone.index == 3
    assert not (session.dir / "tile_003.dng").exists()

    # The manifest survives a restart with everything intact.
    again = MosaicSession.load(session.dir)
    assert again.subject == "diatoms"
    assert len(again.tiles) == 2
    assert again.tiles[1].pos == (1500.0, 10.0)
    assert again.tiles[1].frame == (1824, 1216)


def test_overlap_steering_numbers():
    from darlaston.capture.mosaic import Tile, best_overlap, overlap_fraction

    frame = (1000, 500)
    assert overlap_fraction((0, 0), (0, 0), frame) == 1.0
    assert overlap_fraction((0, 0), (1000, 0), frame) == 0.0
    # 80% along x, full along y -> 20% shared area.
    assert overlap_fraction((0, 0), (800, 0), frame) == pytest.approx(0.2)

    tiles = [Tile(1, "a", (0.0, 0.0), frame), Tile(2, "b", (900.0, 0.0), frame),
             Tile(3, "c", None, frame)]
    frac, who = best_overlap((850.0, 0.0), tiles, frame)
    assert who == 2, "nearest tile wins, positionless ones are skipped"
    assert frac == pytest.approx(0.95)   # 50 px apart on a 1000 px frame


def test_position_rides_the_result(tmp_path):
    class StubPipe:
        def guard_begin(self):
            return None
        def stage_position(self):
            return (123.0, -45.0)
    r = _shoot(tmp_path, pipeline=StubPipe())
    assert r.position == (123.0, -45.0)
    assert r.moved is None


def test_dng_reader_roundtrips_our_own_files(tmp_path):
    from darlaston.process import dng
    from darlaston.process.stitch import read_bayer_dng

    raw = (np.arange(64 * 64, dtype=np.uint16) % 4096).reshape(64, 64)
    path = write_dng(tmp_path / "t.dng", raw)
    back = read_bayer_dng(path)
    assert back.shape == (64, 64)
    assert np.array_equal(back, raw)


def test_stitch_recovers_truth_from_a_drifted_manifest(tmp_path):
    """The whole argument in one test: dead-reckoned positions are wrong by
    a simulated drift, and registration must land on the truth anyway --
    refining within the constraint, never searching."""
    from darlaston.camera.mock import MockCamera
    from darlaston.capture.mosaic import MosaicSession
    from darlaston.process import dng
    from darlaston.process.stitch import stitch

    cam = MockCamera()
    cam.open()
    session = MosaicSession(tmp_path, "stitchtest")
    truth_um = [(0.0, 0.0), (10444.8, 302.4), (20889.6, -240.0)]
    drift = [(0.0, 0.0), (11.0, -13.0), (-14.0, 9.0)]     # preview px, fake

    for i, (ux, uy) in enumerate(truth_um):
        cam.stage_xy = (ux, uy)
        frame = cam.grab_raw()
        with frame:
            raw = frame.copy()
        p = tmp_path / f"c{i}.dng"
        write_dng(p, raw)
        px = ux / 2.4 * (1824 / 5440) + drift[i][0]
        py = uy / 2.4 * (1216 / 3648) + drift[i][1]
        session.adopt(p, (px, py), (1824, 1216))
    cam.close()

    path, report = stitch(session.dir, scale=0.1)
    assert path.exists() and path.name == "composite.dng"
    assert report["refined"] >= 2, "neighbour seams must actually refine"

    pos = report["positions"]
    for i in (1, 2):
        true_dx = (truth_um[i][0] - truth_um[0][0]) / 2.4
        true_dy = (truth_um[i][1] - truth_um[0][1]) / 2.4
        got_dx = pos[i][0] - pos[0][0]
        got_dy = pos[i][1] - pos[0][1]
        # Drift injected was up to ~45 raw px; recovery must beat 3.
        assert abs(got_dx - true_dx) < 3.0, f"tile {i} x off by {got_dx - true_dx:.1f}"
        assert abs(got_dy - true_dy) < 3.0, f"tile {i} y off by {got_dy - true_dy:.1f}"


def test_flat_estimated_from_tiles_recovers_a_known_vignette():
    """Nine views of different subject through one lit field: the only thing
    they share is the illumination, so the median finds it."""
    from darlaston.process.stitch import estimate_flat

    h, w = 240, 360
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
    truth = 1.0 - 0.25 * r2                      # 25% falloff to the corners

    rng = np.random.default_rng(3)
    lumas = []
    for _ in range(9):
        scene = np.full((h, w), 2000.0, np.float32)
        for _ in range(14):                      # scattered dark subject
            cy, cx = rng.integers(20, h - 20), rng.integers(20, w - 20)
            scene[cy - 12:cy + 12, cx - 6:cx + 6] = 700.0
        lumas.append(scene * truth)

    flat = estimate_flat(lumas)
    assert flat is not None
    got = cv2.resize(flat, (w, h))
    expect = truth / truth.mean()
    # Recovered to a couple of percent, which is the difference between a
    # visible seam and an invisible one.
    assert float(np.abs(got - expect).max()) < 0.03


def test_flat_survives_a_subject_dense_enough_to_outvote_the_median():
    """The failure the surface fit exists to prevent. With dense subject,
    some pixels are covered in more than half the tiles, so a plain
    per-pixel median returns a dark value there — a *hole* in the profile,
    which dividing turns into a bright blob. A fitted surface has no holes
    available to it."""
    from darlaston.process.stitch import estimate_flat

    h, w = 240, 360
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    truth = 1.0 - 0.25 * (((xx - w / 2) / (w / 2)) ** 2
                          + ((yy - h / 2) / (h / 2)) ** 2)

    rng = np.random.default_rng(7)
    lumas = []
    for _ in range(9):
        scene = np.full((h, w), 2000.0, np.float32)
        for _ in range(60):                       # dense: ~20% coverage
            cy, cx = rng.integers(20, h - 20), rng.integers(20, w - 20)
            scene[cy - 12:cy + 12, cx - 6:cx + 6] = 700.0
        lumas.append(scene * truth)

    flat = estimate_flat(lumas)
    assert flat is not None
    got = cv2.resize(flat, (w, h))
    expect = truth / truth.mean()
    assert float(np.abs(got - expect).max()) < 0.05


def test_flat_refuses_when_it_cannot_be_sure():
    from darlaston.process.stitch import estimate_flat

    flatfield = [np.full((60, 90), 1000.0, np.float32) for _ in range(3)]
    assert estimate_flat(flatfield) is None, "three tiles is not a median"

    # Every tile identically half-dark: that is subject, not illumination,
    # and correcting it would erase real signal.
    same = []
    for _ in range(6):
        a = np.full((60, 90), 1000.0, np.float32)
        a[:, :45] = 50.0
        same.append(a)
    assert estimate_flat(same) is None, "an extreme profile must be refused"


def test_moved_verdict_flows_from_the_pipeline(tmp_path):
    class StubPipe:
        def guard_begin(self):
            return {"pos": (0.0, 0.0), "analysed": 0, "gated": 0}
        def guard_measure(self, token, timeout=3.0):
            return 40.0
        def stage_position(self):
            return (0.0, 0.0)
    r = _shoot(tmp_path, pipeline=StubPipe())
    assert r.moved is True
    assert r.moved_px == 40.0
    assert "moved during exposure" in r.summary
    assert r.path.exists(), "the moved shot must still be written"


def test_banded_composite_matches_a_whole_canvas():
    """Banding is an optimisation, so it must be invisible in the result.
    Two band heights over the same tiles must agree pixel for pixel."""
    from darlaston.capture.mosaic import MosaicSession
    from darlaston.process import dng, stitch as S
    import tempfile, struct

    with tempfile.TemporaryDirectory() as td:
        session = MosaicSession(td, "bands")
        rng = np.random.default_rng(5)
        shapes, positions = [], []
        for i in range(3):
            raw = (rng.integers(200, 3000, (120, 160))).astype(np.uint16)
            p = Path(td) / f"c{i}.dng"
            write_dng(p, raw)
            session.adopt(p, (i * 120.0, 0.0), (160, 120))
            shapes.append((120, 160))
            positions.append((i * 120.0, 0.0))

        def render(band_bytes):
            old = S._BAND_BYTES
            S._BAND_BYTES = band_bytes
            try:
                out = S.composite(session, positions, scale=1.0, shapes=shapes)
                data = out.read_bytes()
                (ifd,) = struct.unpack_from("<I", data, 4)
                (n,) = struct.unpack_from("<H", data, ifd)
                tags = {}
                for k in range(n):
                    t, _ty, _c, v = struct.unpack_from("<HHII", data,
                                                       ifd + 2 + k * 12)
                    tags[t] = v
                w, h = tags[256], tags[257]
                off = tags.get(324) or tags[273]
                return np.frombuffer(data, np.uint16, count=w * h * 3,
                                     offset=off).reshape(h, w, 3).copy()
            finally:
                S._BAND_BYTES = old

        one_band = render(10_000_000_000)      # whole canvas at once
        many = render(20_000)                  # forced into many bands
        assert one_band.shape == many.shape
        assert np.array_equal(one_band, many), "banding changed the result"


def test_plan_prices_the_output_before_any_work():
    from darlaston.process.stitch import plan

    shapes = [(3648, 5440)] * 4
    positions = [(0.0, 0.0), (4000.0, 0.0), (0.0, 2800.0), (4000.0, 2800.0)]
    full = plan(positions, shapes, 1.0)
    half = plan(positions, shapes, 0.5)
    # 9440 x 6448 of tiles, plus the two pixels of rounding slack plan keeps.
    assert full["w"] == 9442 and full["h"] == 6450
    assert full["megapixels"] == pytest.approx(60.9, abs=0.1)
    assert half["w"] == 4722
    assert full["fits"] and half["fits"]
    # A mosaic large enough to break classic TIFF must say so, not fail late.
    huge = plan([(0.0, 0.0), (60000.0, 40000.0)], [(3648, 5440)] * 2, 1.0)
    assert not huge["fits"]


def test_white_balance_can_be_left_out_of_the_file(tmp_path):
    """Grey-world is a guess dressed as a measurement on an interference
    field. Off must write a genuinely neutral AsShotNeutral, not a
    slightly-different estimate."""
    from darlaston.process import dng as dng_mod

    seen = {}
    real = dng_mod.write_bayer_streamed

    def spy(path, rows, h, w, **kw):
        seen["neutral"] = kw.get("neutral")
        return real(path, rows, h, w, **kw)

    cam = MockCamera()
    cam.open()
    session = types.SimpleNamespace(backend=cam)
    settings = Settings(capture_root=str(tmp_path))
    results, done = [], threading.Event()
    cap = StillCapture(session, settings,
                       on_result=lambda r: (results.append(r), done.set()))
    cap.white_balance = False

    dng_mod.write_bayer_streamed = spy
    try:
        assert cap.trigger(None, subject="t")
        assert done.wait(30)
    finally:
        dng_mod.write_bayer_streamed = real
    cam.close()
    assert results[-1].ok, results[-1].message
    assert seen["neutral"] == (1.0, 1.0, 1.0)
    assert "wb" not in results[-1].applied


def test_mosaic_adopts_a_stack_tile(tmp_path):
    """The composition in one unit: a tile that IS a stack folder. The
    manifest round-trips it, and undo removes the whole folder."""
    from darlaston.capture.mosaic import MosaicSession

    session = MosaicSession(tmp_path, "composed")
    stack = session.begin_stack_tile()
    assert stack.dir == session.dir / "tile_001_stack"
    for i in range(3):
        f = tmp_path / f"s{i}.dng"
        f.write_bytes(b"x")
        stack.adopt(f, metric=float(i))
    tile = session.adopt_stack(stack, (100.0, 50.0), (1824, 1216))
    assert tile.stack == "tile_001_stack"
    assert tile.filename == "tile_001_stack/stacked.dng"

    # A plain tile after it: mixing is the point.
    f = tmp_path / "plain.dng"
    f.write_bytes(b"y")
    t2 = session.adopt(f, (400.0, 50.0), (1824, 1216))
    assert t2.index == 2 and t2.stack is None

    again = MosaicSession.load(session.dir)
    assert again.tiles[0].stack == "tile_001_stack"
    assert again.tiles[0].pos == (100.0, 50.0)
    assert again.tiles[1].stack is None

    again.undo()                                  # the plain tile
    again.undo()                                  # the stack tile
    assert not (session.dir / "tile_001_stack").exists()


def test_stitch_mixes_stacked_and_plain_tiles(tmp_path):
    """The dream's plumbing: one tile is a single 12-bit exposure, the
    other an *unmerged* stack whose merge output is 16-bit. The stitcher
    must (a) finish the pending merge itself and (b) normalise the white
    levels -- without (b), the stacked half lands four stops bright.
    Same scene content everywhere, so any level step is the defect."""
    from darlaston.camera.mock import MockCamera
    from darlaston.capture.mosaic import MosaicSession
    from darlaston.capture.stack import StackSession
    from darlaston.process.stitch import (read_bayer_dng, read_white_level,
                                          stitch)

    cam = MockCamera()
    cam.open()
    session = MosaicSession(tmp_path, "mixed")
    positions_um = [(0.0, 0.0), (10444.8, 0.0)]

    # Tile 1: plain 12-bit capture.
    cam.stage_xy = positions_um[0]
    frame = cam.grab_raw()
    with frame:
        raw = frame.copy()
    p = tmp_path / "one.dng"
    write_dng(p, raw)
    session.adopt(p, (0.0, 0.0), (1824, 1216))

    # Tile 2: a stack of two identical slices, deliberately left unmerged.
    cam.stage_xy = positions_um[1]
    frame = cam.grab_raw()
    with frame:
        raw2 = frame.copy()
    cam.close()
    stack = session.begin_stack_tile()
    for i in range(2):
        p = tmp_path / f"sl{i}.dng"
        write_dng(p, raw2)
        stack.adopt(p, metric=0.5)
    px = positions_um[1][0] / 2.4 * (1824 / 5440)
    session.adopt_stack(stack, (px, 0.0), (1824, 1216))

    path, report = stitch(session.dir, scale=0.1)
    assert path.exists()
    # (a) the pending merge was finished, and produced a 16-bit file.
    merged = session.dir / "tile_002_stack" / "stacked.dng"
    assert merged.exists()
    assert read_white_level(merged) == 4095 * 16

    # (b) levels match across the composite: compare the two tiles'
    # territories in the output. Same mock scene, same illumination --
    # anything beyond a few percent is a normalisation failure.
    comp = read_bayer_dng(path).astype(np.float32)
    h, w = comp.shape
    left = float(comp[:, : w // 3][comp[:, : w // 3] > 0].mean())
    right = float(comp[:, -w // 3:][comp[:, -w // 3:] > 0].mean())
    assert abs(left - right) / max(left, right) < 0.06, \
        f"left {left:.0f} vs right {right:.0f} — white levels not normalised"


def test_registration_survives_accumulated_drift(tmp_path):
    """Nate's first real stacked mosaic: dead-reckoning drift accumulated
    to ~500 raw px across the scan, and the old strip-relative trust bound
    refused strong refinements (response 0.37-0.65) as suspected decoys --
    the tiles then composited at drifted positions, printing every diatom
    on the seam twice. The bound now admits drift up to a fraction of the
    *tile*, capped under the wraparound ambiguity; this injects drift of
    that size and requires registration to see through it."""
    from darlaston.camera.mock import MockCamera
    from darlaston.capture.mosaic import MosaicSession
    from darlaston.process.stitch import stitch

    cam = MockCamera()
    cam.open()
    session = MosaicSession(tmp_path, "drifted")
    truth_um = [(0.0, 0.0), (10444.8, 302.4), (20889.6, -240.0)]
    # Monotonic accumulating drift, ~150 preview px by tile 3 -- the same
    # shape and scale as the real failure (500 raw px = ~167 preview px).
    drift = [(0.0, 0.0), (-20.0, 75.0), (-35.0, 150.0)]

    for i, (ux, uy) in enumerate(truth_um):
        cam.stage_xy = (ux, uy)
        frame = cam.grab_raw()
        with frame:
            raw = frame.copy()
        p = tmp_path / f"c{i}.dng"
        write_dng(p, raw)
        px = ux / 2.4 * (1824 / 5440) + drift[i][0]
        py = uy / 2.4 * (1216 / 3648) + drift[i][1]
        session.adopt(p, (px, py), (1824, 1216))
    cam.close()

    path, report = stitch(session.dir, scale=0.1)
    assert report["refined"] >= 2, \
        f"drifted seams must refine, got {report['refined']}"
    pos = report["positions"]
    for i in (1, 2):
        true_dx = (truth_um[i][0] - truth_um[0][0]) / 2.4
        true_dy = (truth_um[i][1] - truth_um[0][1]) / 2.4
        got_dx = pos[i][0] - pos[0][0]
        got_dy = pos[i][1] - pos[0][1]
        assert abs(got_dx - true_dx) < 4.0, f"tile {i} x off {got_dx-true_dx:.1f}"
        assert abs(got_dy - true_dy) < 4.0, f"tile {i} y off {got_dy-true_dy:.1f}"


def test_capture_records_how_much_slide_a_pixel_covers():
    """The number a scale bar is drawn from, and the reason it is written
    explicitly: EXIF's FocalPlaneXResolution is a scale at the *sensor*,
    so anyone drawing a bar from it is wrong by the whole magnification
    chain. The comment carries microns-of-slide per pixel instead."""
    from darlaston.process.metadata import from_setup
    from darlaston.session.model import (BUILTIN_ILLUMINATION, CameraProfile,
                                         Objective, ScopeProfile, Setup,
                                         Turret)

    cam = CameraProfile(serial="s", name="cam", pixel_um=2.4,
                        relay_factor=2.0)
    scope = ScopeProfile(id="z", name="stand",
                         turret=Turret([Objective(25, 0.65)], current=0),
                         optovar=[1.0], optovar_current=0)
    setup = Setup(camera=cam, scope=scope,
                  illumination=BUILTIN_ILLUMINATION[0])
    assert setup.total_magnification == pytest.approx(50.0)   # 25 x 1 x 2

    meta = from_setup(setup, exposure_us=8000, gain_pct=100,
                      pixel_um=cam.pixel_um)
    field = dict(p.split("=", 1) for p in meta.comment.split() if "=" in p)
    assert float(field["um_per_px"]) == pytest.approx(2.4 / 50.0, rel=1e-3)

    # No pitch, no claim: silence beats an invented scale.
    bare = from_setup(setup, exposure_us=8000, gain_pct=100, pixel_um=None)
    assert "um_per_px" not in bare.comment


def test_mosaic_depth_levels_its_tiles(tmp_path):
    """Each tile's depth is normalised to its own slice count, so 'far'
    means something different per tile and blending them raw steps at
    every seam. The overlaps are the constraint that fixes it. Two tiles
    of one smooth ramp, deliberately offset against each other, must
    composite back to something without a step."""
    import cv2
    from darlaston.capture.mosaic import MosaicSession
    from darlaston.capture.stack import StackSession
    from darlaston.process.stitch import composite_depth

    H, W = 400, 600
    session = MosaicSession(tmp_path, "depthy")
    # One continuous ramp across the pair, seen with a 200 px overlap.
    ramp = np.linspace(0, 1, W + 400, dtype=np.float32)
    offsets = (0.30, -0.25)                # what each tile got wrong
    for i, (x0, off) in enumerate(zip((0, 400), offsets)):
        stack = session.begin_stack_tile()
        f = tmp_path / f"s{i}.dng"
        f.write_bytes(b"x")
        stack.adopt(f, metric=0.0)
        piece = np.tile(ramp[x0:x0 + W], (H, 1)) + off
        cv2.imwrite(str(stack.dir / "depth.png"),
                    np.clip(piece * 255, 0, 255).astype(np.uint8))
        session.adopt_stack(stack, (x0 + W / 2, H / 2), (W, H))

    positions = [(W / 2, H / 2), (400 + W / 2, H / 2)]
    out = composite_depth(session, positions, [(H, W)] * 2, scale=0.5)
    assert out is not None and out.exists()

    depth = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE).astype(np.float32)
    row = depth[depth.shape[0] // 2]
    # A ramp, so neighbouring differences are small and uniform. A failed
    # levelling puts a cliff where the tiles meet.
    steps = np.abs(np.diff(row))
    assert float(steps.max()) < 8.0, \
        f"seam step of {steps.max():.0f} levels — tiles were not levelled"
    assert float(row[-1] - row[0]) > 100, "the ramp itself must survive"


@pytest.mark.parametrize("depth,mono", [(8, False), (12, False),
                                        (16, False), (12, True)])
def test_capture_writes_the_sensor_it_actually_has(tmp_path, depth, mono):
    """ToupTek runs 8, 10, 12, 14 and 16 bit sensors -- and 23% mono --
    across one identical API. The white level and the CFA tags must come
    from the camera, not from constants: a hardcoded 4095 reports an
    8-bit sensor as four stops under, clips a 16-bit one to a sixteenth
    of its range, and puts a Bayer pattern on greyscale."""
    import struct
    from darlaston.camera.base import CameraInfo, Resolution
    from darlaston.process.stitch import _read_ifd, _values

    class Shaped(MockCamera):
        """A mock that reports a different sensor than it renders."""

        def open(self):
            real = super().open()
            self._info = CameraInfo(
                model=real.model, serial=real.serial,
                resolutions=real.resolutions, max_bit_depth=depth,
                bayer_pattern="" if mono else real.bayer_pattern,
                exposure_range_us=real.exposure_range_us,
                gain_range_pct=real.gain_range_pct)
            return self._info

    result = _shoot(tmp_path, cam=Shaped())
    assert result.ok, result.summary
    data = result.path.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)
    ifd = _read_ifd(data, first)
    raw_ifd = _read_ifd(data, _values(data, ifd[330])[0])

    assert _values(data, raw_ifd[50717])[0] == (1 << depth) - 1, \
        "WhiteLevel must be the sensor's, not a constant"
    photometric = _values(data, raw_ifd[262])[0]
    if mono:
        assert photometric == 1, "mono must be BlackIsZero"
        assert 33422 not in raw_ifd, "mono must carry no CFA pattern"
    else:
        assert photometric == 32803, "colour must be CFA"
        assert 33422 in raw_ifd, "colour must carry a CFA pattern"


def test_filenames_collapse_optics_tokens_without_a_setup():
    """A capture taken before any stand is configured -- or from a camera
    that has none -- must not write "{objective}_{illumination}" into its
    own filename. Unknown tokens still survive, so a typo in a pattern
    stays visible."""
    s = Settings(capture_root="/tmp/x",
                 filename_pattern="{seq}_{subject}_{objective}_{illumination}")
    path = s.resolve(setup=None, seq=1, subject="webcam")
    assert path.name == "0001_webcam.dng", path.name

    typo = Settings(capture_root="/tmp/x",
                    filename_pattern="{seq}_{objectve}")
    assert "{objectve}" in typo.resolve(setup=None, seq=2).name


# ---- developing a photograph out of the negative ---------------------------

def test_develop_bands_match_a_single_pass():
    """The band loop exists to bound memory on a stitched mosaic, which can be
    tens of megapixels. It must not change the picture to do it."""
    import cv2
    from darlaston.process.develop import develop

    rng = np.random.default_rng(5)
    raw = (rng.random((600, 800)) * 3000 + 200).astype(np.uint16)
    out = develop(raw, pattern="GBRG", white=4095, neutral=(0.75, 1.0, 0.3))

    a = cv2.cvtColor(raw, cv2.COLOR_BayerGR2BGR).astype(np.float32) / 4095.0
    for c, n in enumerate(reversed((0.75, 1.0, 0.3))):
        a[:, :, c] /= n
    s = a[::16, ::16]
    lo, hi = float(np.percentile(s, 0.5)), float(np.percentile(s, 99.7))
    a = np.clip((a - lo) / (hi - lo), 0, 1)
    ref = (a ** (1 / 2.2) * 255 + 0.5).astype(np.uint8)

    assert out.shape == ref.shape
    assert np.array_equal(out, ref)


def test_develop_knows_which_way_round_its_input_is():
    """A demosaiced frame is BGR; a merged stack and a stitched composite are
    held as linear RGB because that is how they go into the DNG. Getting it
    wrong tints the whole photograph, and presented exactly that way: neutral
    tiles composing into a blue mosaic."""
    from darlaston.process.develop import develop

    # Unambiguously red in RGB terms.
    img = np.zeros((64, 64, 3), np.uint16)
    img[:, :, 0] = 3000

    as_rgb = develop(img, pattern=None, white=4095, stretch=False,
                     rgb_input=True)
    as_bgr = develop(img, pattern=None, white=4095, stretch=False,
                     rgb_input=False)
    # Output is always BGR, so a red input must land in channel 2.
    assert as_rgb[:, :, 2].mean() > 200 and as_rgb[:, :, 0].mean() < 10
    assert as_bgr[:, :, 0].mean() > 200 and as_bgr[:, :, 2].mean() < 10


def test_write_jpeg_reports_failure_rather_than_raising(tmp_path):
    """It runs after the raw is already safely on disk. Losing the photograph
    is a disappointment; taking the capture down with it is not acceptable."""
    from darlaston.process.develop import write_jpeg

    good = np.zeros((32, 32, 3), np.uint8)
    assert write_jpeg(tmp_path / "ok.jpg", good) is True
    assert (tmp_path / "ok.jpg").exists()
    # An unwritable path, and a nonsense image: neither may raise.
    assert write_jpeg(tmp_path / "no" / "such" / "dir" / "x.jpg", good) is False
    assert write_jpeg(tmp_path / "bad.jpg", np.zeros((0, 0, 3), np.uint8)) is False


def test_image_format_setting_round_trips(tmp_path):
    from darlaston.session.settings import Settings

    s = Settings()
    assert s.image_format == "both", "a camera that writes no photograph is odd"
    assert s.jpeg_quality == 95
    for value in ("both", "raw", "jpeg"):
        s.image_format = value
        s.save(tmp_path / "settings.json")
        assert Settings.load(tmp_path / "settings.json").image_format == value


def test_tiles_and_slices_keep_their_raw_whatever_the_preference(tmp_path):
    """"JPEG only" is about photographs. A mosaic tile and a stack slice are
    ingredients -- the stitcher and the merge read them back -- so the
    preference must not be able to leave a session with nothing to work from.
    """
    from darlaston.capture.still import StillCapture
    from darlaston.session.settings import Settings

    s = Settings(capture_root=str(tmp_path), image_format="jpeg")
    cap = StillCapture(session=None, settings=s)
    assert cap.raw_required is False, "a plain capture is a photograph"

    # By shooting, not by restating the rule. This test used to define
    # `raw_wanted` locally and assert against its own copy, so deleting the
    # real check in `still.py` left it passing -- which is the one thing a
    # data-loss guarantee must never do.
    for image_format, raw_required, want_dng in (
            ("jpeg", False, False),      # a photograph, as asked
            ("jpeg", True, True),        # a tile, whatever the preference
            ("both", False, True),
            ("raw", False, True)):
        into = tmp_path / f"{image_format}-{raw_required}"
        made = _shoot_into(into, image_format=image_format,
                           raw_required=raw_required)
        assert made.ok, made.message
        dngs = list(into.rglob("*.dng"))
        assert bool(dngs) is want_dng, (
            f"{image_format} with raw_required={raw_required} wrote "
            f"{[p.name for p in into.rglob('*')]}")


def _shoot_into(root, *, image_format: str, raw_required: bool):
    """One real capture through the real path, into `root`."""
    from darlaston.capture.still import StillCapture
    from darlaston.session.settings import Settings

    root.mkdir(parents=True, exist_ok=True)
    session = types.SimpleNamespace(backend=MockCamera(fps=30.0))
    session.backend.open()
    results, done = [], threading.Event()
    cap = StillCapture(session,
                       Settings(capture_root=str(root),
                                image_format=image_format),
                       on_result=lambda r: (results.append(r), done.set()))
    cap.raw_required = raw_required
    assert cap.trigger(None, subject="t")
    assert done.wait(30), "the capture never finished"
    session.backend.close()
    return results[-1]


def test_a_file_never_claims_a_manufacturer_we_do_not_know():
    """`CaptureMetadata.make` defaulted to "ToupTek" and nothing ever
    overrode it, so every file from every camera claimed that maker --
    including an eight-dollar webcam. Raw processors key their camera
    profiles on Make and Model, so a wrong one is not untidy, it applies
    somebody else's colour science to your slide."""
    from darlaston.process.metadata import CaptureMetadata, from_setup
    from darlaston.session.model import (CameraProfile, Objective,
                                         ScopeProfile, Setup, Turret)

    assert CaptureMetadata().make == "", "a default manufacturer is a guess"

    turret = Turret(positions=[Objective(magnification=16.0, na=0.4)],
                    current=0)
    unknown = Setup(camera=CameraProfile(serial="x", pixel_um=2.4),
                    scope=ScopeProfile(id="s", name="Zeiss", turret=turret))
    assert from_setup(unknown, exposure_us=8300, gain_pct=100).make == ""

    known = Setup(camera=CameraProfile(serial="y", pixel_um=2.4,
                                       make="ToupTek"),
                  scope=ScopeProfile(id="s", name="Zeiss", turret=turret))
    assert from_setup(known, exposure_us=8300, gain_pct=100).make == "ToupTek"


def test_the_dng_omits_a_maker_it_does_not_know(tmp_path):
    """Rather than falling back to "darlaston", which names the software
    and is a claim about hardware we did not build."""
    import numpy as np

    from darlaston.process.dng import write_bayer_streamed
    from darlaston.process.metadata import CaptureMetadata

    preview = np.zeros((16, 16, 3), dtype=np.uint8)
    make = CaptureMetadata(model="Some Camera", make="Acme Optics")

    def rows(start, count):
        return np.full((count, 64), 1000, dtype=np.uint16)

    unknown = tmp_path / "unknown.dng"
    write_bayer_streamed(unknown, rows, 64, 64, preview=preview,
                         meta=CaptureMetadata(model="Some Camera"))
    named = tmp_path / "named.dng"
    write_bayer_streamed(named, rows, 64, 64, preview=preview, meta=make)

    # Tag 271 is Make. Checked by tag rather than by searching the bytes:
    # "darlaston" appears legitimately in Software, and a whole-file
    # search cannot tell the two apart.
    from darlaston.process.stitch import _read_ifd

    def make_tag(path):
        data = path.read_bytes()
        first = int.from_bytes(data[4:8], "little")
        return _read_ifd(data, first).get(271)

    assert make_tag(named) is not None, "a known maker is written"
    assert make_tag(unknown) is None, \
        "claimed a manufacturer for a camera that never named one"


def test_a_decoded_capture_puts_red_where_the_dng_says_red_is(tmp_path):
    """A UVC camera hands back BGR -- that is what OpenCV returns, and
    what the preview and the sidecar JPEG both expect -- while the DNG's
    linear planes are R, G, B.

    Written straight through, red and blue came out swapped in the raw
    and its embedded thumbnail while the JPEG beside it was correct, so
    the two files disagreed about which channel was red.
    """
    import numpy as np

    from darlaston.process import dng
    from darlaston.process.metadata import CaptureMetadata
    from darlaston.process.wiggle import _read_linear

    h, w = 32, 32
    bgr = np.zeros((h, w, 3), dtype=np.uint16)
    bgr[..., 0], bgr[..., 1], bgr[..., 2] = 100, 200, 3000   # B, G, R

    path = tmp_path / "red.dng"
    dng.write_linear_streamed(
        path, lambda s, c: bgr[s:s + c, :, ::-1], h, w,
        preview=np.zeros((8, 8, 3), np.uint8),
        meta=CaptureMetadata(model="t"), white=4095)

    # `_read_linear` returns BGR, so the bright channel must come back at
    # index 2 -- where it started.
    back = _read_linear(path)
    means = [float(back[..., i].mean()) for i in range(3)]
    assert means[2] > means[0], \
        f"red and blue are swapped in the raw: B,G,R = {means}"
    assert round(means[2]) == 3000 and round(means[0]) == 100


def test_the_trigger_is_released_by_the_exposure_not_by_the_file(tmp_path):
    """Deferring the write is only half the fix, and the half that does
    nothing on its own.

    StackTrigger holds `_pending` from the moment it fires until a slice
    lands, and refuses to look at a single frame in between. "A slice
    landed" used to mean a file on disk. Moving the write to a queue and
    leaving that alone would have freed the camera and left the trigger
    deaf for exactly as long as before -- the whole measured fault, still
    there, behind a fix that looked finished.

    So the capture says when the pixels are off the sensor, separately
    from saying when the file exists.
    """
    import time

    from darlaston.capture.writer import WriteQueue

    cam = MockCamera()
    cam.open()
    session = types.SimpleNamespace(backend=cam)
    settings = Settings(capture_root=str(tmp_path))

    held = threading.Event()
    order = []
    done = threading.Event()

    # A queue whose job blocks: the file cannot appear until released.
    writer = WriteQueue(budget=8 * 1024 * 1024 * 1024, name="test-writer")
    real_submit = writer.submit
    writer.submit = lambda n, job, label="": real_submit(
        n, lambda: (held.wait(20.0), job()), label)

    cap = StillCapture(
        session, settings, writer=writer,
        on_exposed=lambda: order.append("exposed"),
        on_result=lambda r: (order.append("written"), done.set()))

    assert cap.trigger(None, subject="t")
    for _ in range(500):                       # the exposure, not the write
        if order:
            break
        time.sleep(0.02)

    assert order == ["exposed"], \
        "the trigger is still waiting on the file, which is the whole fault"
    assert not done.is_set(), "the premise: the write has not happened yet"

    held.set()
    assert done.wait(30), "the write never completed"
    assert order == ["exposed", "written"]
    cam.close()


def test_quitting_waits_for_frames_that_exist_nowhere_but_in_memory(tmp_path):
    """The writer is a daemon thread, so whatever is still in it when the
    process goes is gone. Ctrl-C reaches `shutdown` without ever passing
    through the close question, which is why the wait lives there.
    """
    from darlaston.capture.writer import WriteQueue

    q = WriteQueue(budget=1024 * 1024 * 1024, name="test-shutdown-writer")
    landed = []
    slow = threading.Event()
    for i in range(3):
        assert q.submit(1024, lambda i=i: (slow.wait(0.2), landed.append(i)))

    assert q.depth, "the premise: work is outstanding"
    assert q.drain(timeout=20.0), "gave up on frames it had accepted"
    assert landed == [0, 1, 2], "quit with captures still unwritten"


def test_the_picked_balance_trims_the_measured_one_instead_of_erasing_it():
    """Nate's slices came out visibly green beside a preview that looked
    white, and this is why.

    The flat-measured balance is absolute and lives in the raw's domain:
    sensor, optics and lamp, measured off a blank field of Bayer data. The
    picked one is a small residual trim, taken off the preview -- which on
    a ToupTek has already been through the camera's ISP, and `grab_raw`
    turns the ISP off. Letting the pick replace the flat threw away the
    entire raw-domain correction and kept only the trim.

    Real numbers from his calibration store and settings.
    """
    from darlaston.live import balance

    measured = (1.3487, 1.0, 3.3238)      # from the flat, raw domain
    picked = (1.2145, 1.0, 0.9051)        # off the preview, a trim

    composed = balance.sane((measured[0] * picked[0], 1.0,
                             measured[2] * picked[2]))
    assert composed == pytest.approx((1.638, 1.0, 3.008), abs=0.01)

    # What actually went into his files, and how far off it was.
    assert picked[2] / composed[2] < 0.35, \
        "blue was applied at about a third of what the sensor needed"

    # The composed answer belongs in the same world as an independent
    # estimate of what that raw needs; the pick alone does not.
    grey_world_says = (1.545, 1.0, 2.314)
    assert 0.5 < composed[2] / grey_world_says[2] < 2.0
    assert picked[2] / grey_world_says[2] < 0.5, "the defect this replaces"


def test_a_pick_with_no_flat_behind_it_still_stands_on_its_own():
    """Composition must not require a calibration that may not exist. With
    no measured balance the pick is the whole answer, as before."""
    from darlaston.live import balance

    picked = (1.2145, 1.0, 0.9051)
    composed = balance.sane((balance.UNITY[0] * picked[0], 1.0,
                             balance.UNITY[2] * picked[2]))
    assert composed == pytest.approx(picked, abs=1e-6)
