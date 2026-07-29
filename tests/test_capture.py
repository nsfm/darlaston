"""The capture path: bursts, averaging arithmetic, and the moved verdict.

Needs pidng (the DNG writer); skipped wholesale where only the analysis
dependencies are installed.
"""
import threading
from pathlib import Path
import types

import cv2
import numpy as np
import pytest

pytest.importorskip("pidng")

from darlaston.camera.mock import MockCamera
from darlaston.capture.still import CaptureResult, StillCapture
from darlaston.session.settings import Settings


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
    # The averaged file is meaningfully larger than 12 bits of range: pidng
    # writes uint16 either way, so size equality is expected -- what matters
    # is that it exists and reported itself.
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


def test_timelapse_takes_the_asked_number_and_stops(tmp_path):
    from darlaston.capture.timelapse import Timelapse

    class InstantCapture:
        def __init__(self):
            self.shots = 0
            self.busy = False
        def trigger(self, setup=None, subject="", slide="", frames=1):
            self.shots += 1
            return True

    cap = InstantCapture()
    statuses = []
    tl = Timelapse(cap, statuses.append)
    assert tl.start(0.05, count=3)
    deadline = 5.0
    import time as _t
    t0 = _t.monotonic()
    while tl.running and _t.monotonic() - t0 < deadline:
        _t.sleep(0.02)
    assert not tl.running
    assert cap.shots == 3
    assert statuses[-1].shot == 3
    assert "finished" in statuses[-1].message


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
    path = dng.write_bayer(tmp_path / "t.dng", raw)
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
        dng.write_bayer(p, raw)
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
            dng.write_bayer(p, raw)
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
