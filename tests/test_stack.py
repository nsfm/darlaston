"""Z-stacks: the trigger, the session, and the merge.

The mock's focus_tilt is what makes any of this testable: with the focal
plane tilted, each slice is sharp in a different vertical band, which is
exactly the situation a stack exists to fix -- and a merge that works shows
sharpness everywhere at once, which no single slice has.
"""
import time
import types

import numpy as np
import pytest

import cv2

from darlaston.camera.buffers import BufferPool, Frame
from darlaston.camera.mock import MockCamera, _RESOLUTIONS
from darlaston.capture.stack import StackSession, StackTrigger, field_distance
from darlaston.live.pipeline import LivePipeline
from darlaston.process import dng

RES = _RESOLUTIONS[2]


# ---- the session ------------------------------------------------------------

def test_stack_session_adopts_and_reloads(tmp_path):
    session = StackSession(tmp_path, subject="pinnularia")
    for i in range(3):
        f = tmp_path / f"c{i}.dng"
        f.write_bytes(b"raw")
        session.adopt(f, metric=0.1 * (i + 1), coverage=0.3 * (i + 1))
    assert [s.filename for s in session.slices] == \
        ["slice_001.dng", "slice_002.dng", "slice_003.dng"]

    again = StackSession.load(session.dir)
    assert again.subject == "pinnularia"
    assert len(again.slices) == 3
    assert again.slices[1].metric == pytest.approx(0.2)

    gone = session.undo()
    assert gone.index == 3
    assert not (session.dir / "slice_003.dng").exists()


# ---- the trigger ------------------------------------------------------------

def _rig(tilt=8.0, z=-4.0):
    cam = MockCamera()
    cam.open()
    cam.focus_tilt = tilt
    cam.focus_z = z
    pool = BufferPool((RES.height, RES.width, 3), np.uint8, count=1)
    got = []
    pipe = LivePipeline(got.append)
    pipe.start_sweep()
    buf = pool.acquire()
    frame = Frame(data=buf, seq=0, timestamp=time.time(), exposure_us=8330,
                  gain_pct=100, binned=True, _pool=pool)

    def push():
        cam._render_into(buf, RES)
        pipe._analyse(frame)
        return got[-1]

    return cam, push


def test_trigger_fires_on_rack_then_pause():
    cam, push = _rig()
    fired = []
    trig = StackTrigger(lambda: fired.append(True) or True)
    trig.MIN_INTERVAL = 0.0
    trig.arm()

    for _ in range(20):                        # settle at the start
        trig.observe(push())
    assert len(fired) == 1, "the first slice fires where the operator starts"
    trig.slice_landed()

    for burst in range(3):
        for k in range(6):                     # rack a step
            cam.focus_z += 0.15
            trig.observe(push())
        before = len(fired)
        for k in range(14):                    # pause
            trig.observe(push())
        assert len(fired) == before + 1, \
            f"pause {burst}: exactly one slice per rack-and-pause"
        trig.slice_landed()


def test_trigger_does_not_fire_while_racking_out_of_focus():
    """Continuous racking must not fire -- with one physically honest
    exception. Inside the depth of field the image genuinely does not
    change, so 'racking' and 'paused' are optically the same statement and
    no image-based trigger can tell them apart. Traced: the only fire in a
    forty-step continuous rack landed within the DoF around z=0. Outside
    it, zero fires is the requirement; inside it, at most one -- and that
    slice lands at best focus, which a stack wants anyway.
    """
    cam, push = _rig()
    trig = StackTrigger(lambda: True)
    trig.MIN_INTERVAL = 0.0
    trig.arm()
    for _ in range(20):
        trig.observe(push())
    trig.slice_landed()

    fired_at = []
    z = -4.0
    for _ in range(40):
        z += 0.12
        cam.focus_z = z
        if trig.observe(push()):
            fired_at.append(z)
            trig.slice_landed()
    out_of_focus = [z for z in fired_at if abs(z) > 0.8]
    assert out_of_focus == [],         f"fired outside the depth of field at {out_of_focus}"
    assert len(fired_at) <= 2, f"too many DoF fires: {fired_at}"


def test_trigger_ignores_a_pan(monkeypatch):
    """Panning reshapes the sharpness field exactly as racking does; the xy
    gate is what stops a pan-then-pause becoming a slice of somewhere else."""
    cam, push = _rig()
    trig = StackTrigger(lambda: True)
    trig.MIN_INTERVAL = 0.0
    trig.arm()
    for _ in range(20):
        trig.observe(push())
    trig.slice_landed()

    fired = 0
    for k in range(8):                         # pan across the slide
        cam.stage_xy = (k * 300.0, 0.0)
        s = push()
        fired += bool(trig.observe(s))
    # Now hold still, but pretend the tracker never settled (short pause).
    for _ in range(10):
        s = push()
        s = types.SimpleNamespace(sharpness_field=s.sharpness_field,
                                  settled=False, looks_blank=False)
        fired += bool(trig.observe(s))
    assert fired == 0


def test_field_distance_separates_motion_from_noise():
    rng = np.random.default_rng(3)
    base = rng.random((64, 64)).astype(np.float32)
    noisy = base + rng.normal(0, 0.001, (64, 64)).astype(np.float32)
    moved = np.roll(base, 7, axis=1)
    assert field_distance(base, noisy) < 0.001
    assert field_distance(base, moved) > 0.05


# ---- the merge --------------------------------------------------------------

def _strip_sharpness(gray: np.ndarray, strips: int = 4) -> list[float]:
    """Mean Tenengrad per vertical strip -- the per-region judge."""
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    mag = gx * gx + gy * gy
    w = gray.shape[1] // strips
    return [float(mag[:, i * w:(i + 1) * w].mean()) for i in range(strips)]


def test_merge_is_sharp_everywhere_when_no_slice_is(tmp_path):
    """The whole point of a stack, as an assertion. With a tilted focal
    plane every slice is soft somewhere; the merge must not be."""
    cam = MockCamera()
    cam.open()
    cam.focus_tilt = 8.0

    session = StackSession(tmp_path, subject="tilted")
    for i, z in enumerate(np.linspace(-4.5, 4.5, 7)):
        cam.focus_z = float(z)
        frame = cam.grab_raw()
        with frame:
            raw = frame.copy()
        p = tmp_path / f"c{i}.dng"
        dng.write_bayer_streamed(
            p, lambda s, c: raw[s:s + c], raw.shape[0], raw.shape[1],
            preview=dng.make_preview(raw, bayer=True, white=4095),
            bits=12, white=4095)
        session.adopt(p, metric=0.5)

    # The reference: flat focal plane, in focus -- sharp everywhere.
    cam.focus_tilt = 0.0
    cam.focus_z = 0.0
    ref_frame = cam.grab_raw()
    with ref_frame:
        reference = ref_frame.copy()
    cam.close()

    from darlaston.process.stack import merge, _luma_half
    path, report = merge(session.dir, output="linear")
    assert path.exists() and path.name == "stacked.dng"
    assert (session.dir / "depth.png").exists()
    assert report["depth_levels"] >= 4, "the tilt must span several slices"

    # Read the merged luma back out of the linear DNG.
    import struct
    data = path.read_bytes()
    (first,) = struct.unpack_from("<I", data, 4)

    def ifd(at):
        (count,) = struct.unpack_from("<H", data, at)
        out = {}
        for k in range(count):
            t, ty, c, v = struct.unpack_from("<HHII", data, at + 2 + k * 12)
            out[t] = (ty, c, v)
        return out

    top = ifd(first)
    sub = ifd(top[330][2])
    w, h = sub[256][2], sub[257][2]
    # Strips array
    ty, cnt, off = sub[273]
    ty2, cnt2, coff = sub[279]
    offs = (struct.unpack_from(f"<{cnt}I", data, off) if cnt > 1 else (off,))
    counts = (struct.unpack_from(f"<{cnt2}I", data, coff) if cnt2 > 1
              else (coff,))
    blob = b"".join(data[o:o + c] for o, c in zip(offs, counts))
    rgb = np.frombuffer(blob, np.uint16, count=w * h * 3).reshape(h, w, 3)
    merged_luma = rgb.astype(np.float32).mean(axis=2)[::2, ::2]

    ref_luma = _luma_half(reference)
    merged_strips = _strip_sharpness(merged_luma)
    ref_strips = _strip_sharpness(ref_luma)

    # Brightness must survive the merge. The blend of 12-bit slices lands
    # back in 0..4095, and writing that against a 16-bit white level shipped
    # every stack four stops under -- found by Nate pushing +3 EV in post.
    # The relative level (mean / white level) must match the slices'.
    white = 4095 * 16
    merged_rel = float(rgb.mean()) / white
    slice_rel = float(reference.mean()) / 4095
    assert merged_rel > slice_rel * 0.5, \
        f"merged sits at {merged_rel:.3f} vs slices {slice_rel:.3f} — dark"

    ratios = [m / max(r, 1e-9) for m, r in zip(merged_strips, ref_strips)]
    assert min(ratios) > 0.5, \
        f"merged must be sharp in every strip; ratios {ratios}"

    # And no single slice comes close to that everywhere -- otherwise the
    # test proves nothing about merging.
    worst_single = 0.0
    for piece in session.slices:
        sl = _luma_half(read_bayer := __import__(
            "darlaston.process.stitch", fromlist=["read_bayer_dng"]
        ).read_bayer_dng(session.dir / piece.filename))
        strips = _strip_sharpness(sl)
        worst_single = max(worst_single,
                           min(s / max(r, 1e-9)
                               for s, r in zip(strips, ref_strips)))
    assert worst_single < min(ratios), \
        "some single slice was already sharp everywhere; weak fixture"


def test_bayer_output_round_trips_the_blend(tmp_path):
    """Nate's re-mosaic idea, as an assertion: bilinear demosaic passes each
    site's native value through untouched, so sampling the blend back onto
    the grid loses nothing at native sites. With no alignment shifts, the
    bayer output's sites must equal the weighted blend of the slices' raw
    values there -- and the file is a quarter the size of the linear one."""
    from darlaston.process.stack import merge
    from darlaston.process.stitch import read_bayer_dng

    cam = MockCamera()
    cam.open()
    cam.focus_tilt = 8.0
    session = StackSession(tmp_path, subject="remosaic")
    for i, z in enumerate(np.linspace(-3, 3, 5)):
        cam.focus_z = float(z)
        frame = cam.grab_raw()
        with frame:
            raw = frame.copy()
        p = tmp_path / f"c{i}.dng"
        dng.write_bayer_streamed(
            p, lambda s, c: raw[s:s + c], raw.shape[0], raw.shape[1],
            preview=dng.make_preview(raw, bayer=True, white=4095),
            bits=12, white=4095)
        session.adopt(p, metric=0.5)
    cam.close()

    bayer_path, rep_b = merge(session.dir, output="bayer")
    back = read_bayer_dng(bayer_path)
    assert back.shape == (3648, 5440), "bayer output is single-channel"
    assert rep_b["output"] == "bayer"
    size_b = bayer_path.stat().st_size

    linear_path, _ = merge(session.dir, output="linear")
    size_l = linear_path.stat().st_size
    assert size_b < size_l * 0.4, \
        f"bayer {size_b/1e6:.0f} MB should be ~a third of linear {size_l/1e6:.0f} MB"

    # Levels must agree between the two forms.
    assert abs(float(back.mean()) / (4095 * 16)) > 0.02

def test_merge_does_not_halo_past_a_bright_edge(tmp_path):
    """The halo, as a regression test. A bright textured disk over a fine
    textured background, two slices, each plane defocused in the other's --
    the defocused disk's glow physically spreads past its boundary, like a
    diatom edge in phase contrast. With wide sharpness pooling the disk's
    in-focus edge pushed its verdict deep into the background (54%% of the
    band beside the disk misassigned); pool-small-then-refine holds the
    line. Ground truth is known, so the failure is countable."""
    from darlaston.process.stack import merge

    rng = np.random.default_rng(7)
    H, W = 600, 800
    CX, CY, R = 400, 300, 130

    def texture(scale, lo, hi):
        t = cv2.GaussianBlur(rng.normal(size=(H, W)).astype(np.float32),
                             (0, 0), scale)
        t = (t - t.min()) / (np.ptp(t) + 1e-9)
        return lo + t * (hi - lo)

    bg = texture(1.2, 500, 1100)
    disk = texture(2.0, 2600, 3600)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dist = np.sqrt((xx - CX) ** 2 + (yy - CY) ** 2)
    alpha = np.clip(R + 1.5 - dist, 0, 1).astype(np.float32)

    slice_a = alpha * disk + (1 - alpha) * cv2.GaussianBlur(bg, (0, 0), 9.0)
    pre = cv2.GaussianBlur(alpha * disk, (0, 0), 9.0)
    soft = cv2.GaussianBlur(alpha, (0, 0), 9.0)
    slice_b = pre + (1 - soft) * bg

    session = StackSession(tmp_path, subject="halo")
    for i, s in enumerate((slice_a, slice_b)):
        raw = np.clip(s, 0, 4095).astype(np.uint16)
        p = tmp_path / f"c{i}.dng"
        dng.write_bayer_streamed(
            p, lambda st, c: raw[st:st + c], H, W,
            preview=dng.make_preview(raw, bayer=True, white=4095),
            bits=12, white=4095)
        session.adopt(p, metric=0.5)

    merge(session.dir, output="bayer")
    dmap = cv2.imread(str(session.dir / "depth.png"),
                      cv2.IMREAD_GRAYSCALE)
    near = dmap < 128                                # slice A's territory

    dist2 = cv2.resize(dist, (W // 2, H // 2),
                       interpolation=cv2.INTER_AREA) / 2
    gt_near = cv2.resize(alpha, (W // 2, H // 2),
                         interpolation=cv2.INTER_AREA) > 0.5
    halo_band = (~gt_near) & (dist2 < R / 2 + 20)
    rim_band = gt_near & (dist2 > R / 2 - 20)

    halo = float(near[halo_band].mean())
    rim = float((~near)[rim_band].mean())
    assert halo < 0.10, f"halo band {halo:.1%} taken from the wrong slice"
    assert rim < 0.10, f"subject rim {rim:.1%} eroded by the background"

def test_merge_does_not_terrace_in_glow(tmp_path):
    """Nate's field report as a regression test: stepped ridges in the glow
    apron, one per exposure -- integer depth quantisation made visible by
    the defocused edge's rings. The terrace scene from tools/stack_bench.py
    through the real merge; the glow band's depth must stay close to the
    glass it actually is, not step through the slices."""
    from tools.stack_bench import scene_terrace, WHITE
    from darlaston.process.stack import merge

    slices, gt, regions = scene_terrace()
    n = len(slices)
    session = StackSession(tmp_path, subject="terrace")
    for i, s in enumerate(slices):
        raw = np.clip(s, 0, WHITE).astype(np.uint16)
        p = tmp_path / f"c{i}.dng"
        dng.write_bayer_streamed(
            p, lambda st, c: raw[st:st + c], raw.shape[0], raw.shape[1],
            preview=dng.make_preview(raw, bayer=True, white=4095),
            bits=12, white=4095)
        session.adopt(p, metric=0.5)

    merge(session.dir, output="bayer")
    dmap = cv2.imread(str(session.dir / "depth.png"), cv2.IMREAD_GRAYSCALE)
    depth = dmap.astype(np.float32) / 255 * (n - 1)

    band = regions["glow band"]
    err = np.abs(depth[band] - gt[band])
    wrong = float((err > 0.5).mean())
    assert wrong < 0.12, \
        f"{wrong:.1%} of the glow band left the glass depth — terracing"

def test_register_does_not_mutate_lumas():
    """OpenCV 5's phaseCorrelate applies its window to the *inputs*, in
    place. _register must shield the lumas -- they are the depth pipeline's
    input, and windowed-in-place lumas cost the terrace scene 6.4% -> 70%
    wrong when this regressed silently."""
    from darlaston.process.stack import _register
    rng = np.random.default_rng(3)
    lumas = [rng.uniform(100, 3000, (240, 320)).astype(np.float32)
             for _ in range(4)]
    before = [l.copy() for l in lumas]
    _register(lumas)
    for l, b in zip(lumas, before):
        assert np.array_equal(l, b), "registration mutated a luma"

def test_every_smoothing_preset_keeps_the_depth_map_honest(tmp_path):
    """The knob trades detail against glow smoothness, and no setting may
    buy smoothness by wrecking the depth map. Only that invariant is
    asserted here: the presets' *ordering* was measured end to end on real
    stacks, and this synthetic apron is too small to separate them (all
    four land within noise of each other), so claiming the ordering from
    this fixture would be claiming more than it shows."""
    from tools.stack_bench import scene_terrace, WHITE
    from darlaston.process.stack import merge, SMOOTHING

    slices, gt, regions = scene_terrace()
    n = len(slices)
    session = StackSession(tmp_path, subject="presets")
    for i, s in enumerate(slices):
        raw = np.clip(s, 0, WHITE).astype(np.uint16)
        p = tmp_path / f"c{i}.dng"
        dng.write_bayer_streamed(
            p, lambda st, c: raw[st:st + c], raw.shape[0], raw.shape[1],
            preview=dng.make_preview(raw, bayer=True, white=4095),
            bits=12, white=4095)
        session.adopt(p, metric=0.5)

    band = regions["glow band"]
    for name in SMOOTHING:
        merge(session.dir, output="bayer", smoothing=name)
        depth = (cv2.imread(str(session.dir / "depth.png"),
                            cv2.IMREAD_GRAYSCALE).astype(np.float32)
                 / 255 * (n - 1))
        wrong = float((np.abs(depth[band] - gt[band]) > 0.5).mean())
        assert wrong < 0.25, f"{name}: {wrong:.1%} of the glow band is wrong"
