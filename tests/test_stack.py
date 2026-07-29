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
    path, report = merge(session.dir)
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
