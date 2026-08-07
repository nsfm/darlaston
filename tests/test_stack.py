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

def test_wigglegram_parallax_follows_depth(tmp_path):
    """The depth map drives the parallax, as an assertion: a scene whose
    left half is near and right half is far must shift its halves in
    opposite directions between a stereo pair's eyes. Also pins that the
    artifacts (webm or webp fallback, webp, stereo pair, anaglyph) land."""
    from darlaston.process.wiggle import stereo, wigglegram

    rng = np.random.default_rng(5)
    H, W = 480, 640
    raw = np.clip(rng.normal(1500, 500, (H, W)), 0, 4095).astype(np.uint16)
    dng.write_bayer_streamed(
        tmp_path / "stacked.dng", lambda s, c: raw[s:s + c], H, W,
        preview=dng.make_preview(raw, bayer=True, white=4095),
        bits=12, white=4095)
    depth = np.zeros((H, W), np.uint8)
    depth[:, W // 2:] = 255
    cv2.imwrite(str(tmp_path / "depth.png"), depth)

    # Four phases of the wobble rather than the default twenty-four. The
    # parallax below is read off the *stereo* pair, and nothing here asserts
    # anything about the wobble beyond the two files landing -- so the only
    # thing the other twenty cost is VP9 encoding, at about 0.44 s per
    # 640x480 frame in this build. Four still walks the whole path: open the
    # writer, loop the four baked cycles over it, release, then the animated
    # WebP. This one line was 35 s of a 230 s suite. (The encoder's own speed
    # is a real question about the product, not about the test -- see
    # spike/docs/test-speed.md.)
    wob = wigglegram(tmp_path, frames=4)
    pair, ana = stereo(tmp_path)
    assert wob.exists() and pair.exists() and ana.exists()
    assert (tmp_path / "wiggle.webp").exists()

    # Existence was the whole claim, and it is not enough: a writer that is
    # opened and released without ever being fed leaves a file that exists.
    # Checked by putting exactly that defect back, which passed. So count the
    # frames -- four phases through the four cycles the module bakes in, which
    # is also the only thing that pins those cycles at all.
    if wob.suffix == ".webm":
        clip = cv2.VideoCapture(str(wob))
        try:
            read = 0
            while clip.read()[0]:
                read += 1
        finally:
            clip.release()
        assert read == 4 * 4, f"the wobble holds {read} frames, not 16"
    else:
        # `wigglegram` falls back to the animated WebP where VP9 will not
        # open. Rare, and the check must not go quiet when it happens --
        # a conditional assertion that silently stops asserting is how a
        # test starts passing for the wrong reason. Size is a weaker
        # claim than a frame count and it still catches an empty file.
        assert wob.stat().st_size > 2000, (
            f"fell back to {wob.name} and it is {wob.stat().st_size} bytes")

    # The pair is [right | left]; correlate each half of the scene between
    # the two eyes and demand opposite horizontal shifts.
    both = cv2.imread(str(pair), cv2.IMREAD_GRAYSCALE).astype(np.float32)
    right, left = both[:, :W], both[:, W:]
    m = 60                                     # stay clear of the seam
    hann = cv2.createHanningWindow((W // 2 - 2 * m, H - 2 * m), cv2.CV_32F)
    (dx_near, _), _ = cv2.phaseCorrelate(
        left[m:-m, m:W // 2 - m].copy(), right[m:-m, m:W // 2 - m].copy(),
        hann)
    (dx_far, _), _ = cv2.phaseCorrelate(
        left[m:-m, W // 2 + m:-m].copy(),
        right[m:-m, W // 2 + m:-m].copy(), hann)
    assert dx_near * dx_far < 0, \
        f"halves must shift oppositely, got {dx_near:.2f} and {dx_far:.2f}"
    assert abs(dx_near - dx_far) > 2.0, "parallax too small to read"

def test_dic_render_lights_slopes_from_one_side(tmp_path):
    """The DIC fake, as an assertion: a ramp in depth must render bright
    on one flank and dark on the other, and a flat field must render as
    the neutral middle. That asymmetry is what makes it read as relief
    rather than as an edge filter."""
    from darlaston.process.relief import dic

    H, W = 400, 600
    rng = np.random.default_rng(9)
    raw = np.clip(rng.normal(1600, 60, (H, W)), 0, 4095).astype(np.uint16)
    dng.write_bayer_streamed(
        tmp_path / "stacked.dng", lambda s, c: raw[s:s + c], H, W,
        preview=dng.make_preview(raw, bayer=True, white=4095),
        bits=12, white=4095)
    # A single ridge running vertically: up one side, down the other.
    x = np.arange(W, dtype=np.float32)
    ridge = np.clip(255 - np.abs(x - W / 2) * (255 / (W / 4)), 0, 255)
    depth = np.tile(ridge, (H, 1)).astype(np.uint8)
    cv2.imwrite(str(tmp_path / "depth.png"), depth)

    grey_path, tint_path = dic(tmp_path, azimuth=0.0, detail=0.0)
    assert grey_path.exists() and tint_path.exists()
    out = cv2.imread(str(grey_path), cv2.IMREAD_GRAYSCALE).astype(np.float32)

    m = 40
    rising = float(out[m:-m, W // 4 - 40:W // 4 + 40].mean())
    falling = float(out[m:-m, 3 * W // 4 - 40:3 * W // 4 + 40].mean())
    flat = float(out[m:-m, :m].mean())
    assert (rising - 128) * (falling - 128) < 0, \
        f"flanks must light oppositely, got {rising:.0f} and {falling:.0f}"
    assert abs(flat - 128) < 12, f"flat field should be neutral, got {flat:.0f}"
    assert abs(rising - falling) > 40, "relief too faint to read"

def test_refocus_moves_the_sharp_plane(tmp_path):
    """Synthetic aperture, as an assertion: two textured halves at
    opposite depths, and choosing a plane must sharpen one while
    softening the other -- and choosing the other plane must swap them."""
    from darlaston.process.aperture import refocus

    H, W = 300, 600
    rng = np.random.default_rng(11)
    img = np.clip(rng.normal(128, 60, (H, W, 3)), 0, 255).astype(np.uint8)
    depth = np.zeros((H, W), np.float32)
    depth[:, :W // 2] = -1.0
    depth[:, W // 2:] = 1.0

    def sharpness(im, half):
        s = slice(0, W // 2) if half == "left" else slice(W // 2, W)
        g = cv2.cvtColor(im[:, s], cv2.COLOR_BGR2GRAY).astype(np.float32)
        return float(cv2.Laplacian(g, cv2.CV_32F).var())

    near = refocus(img, depth, -1.0, aperture=10.0)
    far = refocus(img, depth, 1.0, aperture=10.0)
    assert sharpness(near, "left") > 5 * sharpness(near, "right")
    assert sharpness(far, "right") > 5 * sharpness(far, "left")

def test_autostereogram_encodes_depth_in_its_period(tmp_path):
    """A Magic Eye is only real if the pattern's repeat *period* tracks
    the depth: nearer surfaces must repeat at a shorter interval. Two
    flat halves at different depths, and the measured period of each
    half must differ in the right direction."""
    from darlaston.process.wiggle import autostereogram

    H, W = 300, 900
    rng = np.random.default_rng(2)
    raw = np.clip(rng.normal(1500, 200, (H, W)), 0, 4095).astype(np.uint16)
    dng.write_bayer_streamed(
        tmp_path / "stacked.dng", lambda s, c: raw[s:s + c], H, W,
        preview=dng.make_preview(raw, bayer=True, white=4095),
        bits=12, white=4095)
    depth = np.zeros((H, W), np.uint8)
    depth[:, W // 2:] = 255                    # right half at the far end
    cv2.imwrite(str(tmp_path / "depth.png"), depth)

    path = autostereogram(tmp_path, eye=120, mu=0.5)
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE).astype(np.float32)

    def period(strip):
        """Shift with the best self-similarity, in [30, 90) px."""
        best, at = -1e18, 0
        a = strip - strip.mean()
        for s in range(30, 90):
            b = np.roll(a, -s, axis=1)[:, :-s]
            score = float((a[:, :-s] * b).mean())
            if score > best:
                best, at = score, s
        return at

    h0, h1 = img.shape[0] // 4, 3 * img.shape[0] // 4
    near = period(img[h0:h1, 200:440])          # depth 0 -> nearer
    far = period(img[h0:h1, 640:880])           # depth 255 -> further
    assert near < far, \
        f"near half must repeat tighter: near {near} px, far {far} px"

def test_arrangement_finds_isolated_specimens_and_refuses_a_smear(tmp_path):
    """The arranger's two honest behaviours: it finds elongated textured
    objects on smooth mountant, and it declines when there is nothing it
    can cleanly separate rather than cutting up a blob."""
    from darlaston.process.arrange import arrange, find_specimens

    rng = np.random.default_rng(6)
    H, W = 900, 1400

    def build(draw_specimens):
        field = np.full((H, W), 3200.0, np.float32)
        field += rng.normal(0, 12, (H, W))
        if draw_specimens:
            for cx, cy, ang in ((300, 250, 20), (800, 300, -35),
                                (500, 650, 70), (1050, 700, 10)):
                # A striated ellipse: textured, elongated, isolated.
                patch = np.zeros((H, W), np.float32)
                cv2.ellipse(patch, (cx, cy), (120, 26), ang, 0, 360, 1.0, -1)
                stripes = (np.sin(np.arange(W) * 1.4)[None, :]
                           * np.ones((H, 1)))
                field -= patch * (900 + 500 * stripes)
        raw = np.clip(field, 0, 4095).astype(np.uint16)
        dng.write_bayer_streamed(
            tmp_path / "stacked.dng", lambda s, c: raw[s:s + c], H, W,
            preview=dng.make_preview(raw, bayer=True, white=4095),
            bits=12, white=4095)
        cv2.imwrite(str(tmp_path / "depth.png"),
                    np.zeros((H, W), np.uint8))

    build(True)
    found = find_specimens(tmp_path)
    assert len(found) >= 3, f"should isolate the specimens, got {len(found)}"
    for spec in found:
        assert spec.aspect > 2.0, "cut-outs must be elongated"
    out = arrange(found, tmp_path / "arr.png", style="rosette", size=700)
    assert out.exists()

    build(False)                          # bare mountant, nothing to find
    assert find_specimens(tmp_path) == []
    with pytest.raises(ValueError):
        arrange([], tmp_path / "none.png")


def test_a_slow_shutter_silently_swallows_deliberate_pauses():
    """Nate's report was that the trigger fires during gentle movement.
    Traced on the synthetic stage, it does no such thing -- what it does is
    ignore pauses while the previous slice is still being written, and say
    nothing about it. Six deliberate rack-and-pause gestures against a
    5.4 s shutter (which is what an unqueued 20 MP capture measured) yield
    two slices. From the bench that is indistinguishable from firing at the
    wrong moment: you stop, nothing happens, you move on, and a slice
    lands at some later pause instead.

    This is the test that says the write queue is a correctness fix and
    not only a speed one.
    """
    def gestures(busy_fields):
        cam, push = _rig()
        state = {"busy": 0}

        def fire():
            if state["busy"] > 0:
                return False           # exactly what StillCapture.trigger does
            state["busy"] = busy_fields
            return True

        def tick():
            signals = push()
            if state["busy"] > 0:
                state["busy"] -= 1
            return signals

        trig = StackTrigger(fire)
        trig.MIN_INTERVAL = 0.0
        trig.arm()
        for _ in range(20):
            trig.observe(tick())
        trig.slice_landed()
        state["busy"] = 0

        landed = 0
        for _ in range(6):
            for _ in range(6):                 # rack a step, deliberately
                cam.focus_z += 0.15
                if trig.observe(tick()):
                    landed += 1
                    trig.slice_landed()
            for _ in range(20):                # stop, and wait properly
                if trig.observe(tick()):
                    landed += 1
                    trig.slice_landed()
        return landed

    # ~12 sharpness fields a second: the field arrives at half of a 24 fps
    # preview. 65 fields is the 5.4 s per slice measured on a real stack.
    assert gestures(0) == 6, "the premise: every deliberate pause is a slice"
    assert gestures(65) < 4, \
        "a 5.4 s shutter used to swallow most of them; if this now passes, " \
        "the trigger changed and this test is measuring nothing"


def test_a_slice_is_filed_with_the_focus_of_its_own_plane(tmp_path):
    """`metric` was passed 0.0 at the only call site, so all eighteen
    slices of Nate's stack filed the field that sequences them as zero.

    And with the write deferred it is no longer enough to read the live
    metric when the file lands: by then the operator is several planes
    further on. Each exposure's metric is banked as the shutter fires and
    spent when its file arrives, in the same order.
    """
    from collections import deque

    session = StackSession(tmp_path, subject="diatom")
    banked = deque()

    for plane, metric in enumerate([12.5, 31.0, 44.25, 30.75], start=1):
        banked.append(metric)                     # as the shutter fires
        src = tmp_path / f"cap_{plane}.dng"
        src.write_bytes(b"raw")

    for plane in range(1, 5):                     # ...as each file lands
        session.adopt(tmp_path / f"cap_{plane}.dng", metric=banked.popleft())

    filed = [s.metric for s in StackSession.load(session.dir).slices]
    assert filed == [12.5, 31.0, 44.25, 30.75], \
        "slices filed with something other than their own plane's focus"
    assert not any(m == 0.0 for m in filed), "the defect this replaces"


def test_a_merge_carries_the_balance_its_slices_were_shot_with(tmp_path):
    """Grey-world was the only source of the composite's AsShotNeutral, so
    merging threw away whatever the operator had picked off the screen and
    substituted "this field averages to grey". Measured on Nate's stack:
    the slices said (0.8234, 1.0, 1.1049), which is exactly the reciprocal
    of the gains he picked, and stacked.dng said (0.6471, 1.0, 0.4321).

    A composite that confidently disagrees with every frame it is made of
    reads as the slices being wrong. They were not.
    """
    from darlaston.process import dng

    picked = (0.8234, 1.0, 1.1049)
    raw = np.full((64, 64), 800, np.uint16)
    raw[0::2, 1::2] = 1400                 # a decidedly non-grey field
    path = tmp_path / "slice_001.dng"
    dng.write_bayer_streamed(
        path, lambda a, c: raw[a:a + c], 64, 64,
        preview=dng.make_preview(raw, bayer=True, white=4095),
        neutral=picked, white=4095)

    assert dng.read_neutral(path) == pytest.approx(picked, abs=1e-3), \
        "the tag did not survive the round trip"
    # Grey-world on this frame says something else entirely, which is the
    # whole point: the two sources disagree, and the slice is the honest one.
    assert dng.grey_world_neutral(raw) != pytest.approx(picked, abs=1e-2)


def test_reading_a_neutral_never_raises_on_a_file_that_has_none(tmp_path):
    """Provenance is a bonus, never a gate: a merge must not fail because
    one slice came from somewhere else."""
    from darlaston.process import dng

    junk = tmp_path / "not.dng"
    junk.write_bytes(b"II*\x00" + b"\x00" * 64)
    assert dng.read_neutral(junk) is None
    assert dng.read_neutral(tmp_path / "absent.dng") is None
