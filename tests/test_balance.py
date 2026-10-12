"""The working white balance -- the one picked off the screen.

Distinct from the flat's measured balance, and the tests are separate for
the same reason the controls are: one is a per-scene adjustment somebody
makes while looking down the eyepiece, the other is a measurement keyed to
the optical configuration.
"""
import numpy as np
import pytest

from darlaston.live import balance as B


def _patch(b: int, g: int, r: int, shape=(64, 64)) -> np.ndarray:
    out = np.zeros((*shape, 3), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = b, g, r
    return out


def test_a_picked_region_comes_out_neutral():
    """The whole promise of the control: point at something that should be
    grey, and it becomes grey."""
    blue = _patch(b=200, g=120, r=90)
    fixed = B.applied(blue, B.lut(B.from_region(blue)))
    channels = [float(fixed[..., i].mean()) for i in range(3)]
    assert max(channels) - min(channels) <= 1.0, channels


def test_the_stored_gain_is_a_linear_ratio_not_a_display_one():
    """The one thing this change alters: the number kept for the file.
    A 120-green over 90-red patch is a display ratio of 1.33 and a linear
    ratio of 1.33 ** 2.2, and the raw path multiplies linear values."""
    gains = B.gains_from_means(b=120, g=120, r=90)
    assert gains[0] == pytest.approx((120 / 90) ** B.GAMMA, rel=1e-6)
    assert gains[1] == 1.0 and gains[2] == 1.0


def test_the_preview_lookup_is_unchanged_by_the_domain():
    """A power law commutes with a scale, so deriving in linear light and
    applying through a linearising lookup gives the display-domain table
    main always had, wherever the clamp does not bind. Pinned so nobody
    claims a preview fix out of it again."""
    rng = np.random.default_rng(1)
    ramp = np.arange(256, dtype=np.float32)
    for _ in range(200):
        b, g, r = rng.uniform(80, 220, 3)        # ratios under the 3.5x clamp
        new = B.lut(B.gains_from_means(b, g, r))
        old = np.empty((1, 256, 3), np.uint8)
        for ch, gain in enumerate((g / b, 1.0, g / r)):
            old[0, :, ch] = np.clip(ramp * gain + 0.5, 0, 255)
        assert np.abs(new.astype(int) - old.astype(int)).max() <= 1


def test_a_legacy_display_ratio_is_carried_into_linear_once():
    assert B.from_display_ratio((1.33, 1.0, 0.6)) == pytest.approx(
        (1.33 ** B.GAMMA, 1.0, 0.6 ** B.GAMMA), rel=1e-6)


def test_an_old_settings_file_is_converted_on_load(qapp, tmp_path, monkeypatch):
    """A pick saved by the build that kept display ratios must not come
    back weaker: 1.33 read as linear is a 1.14 display ratio."""
    import json
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.session.settings import Settings, config_dir
    path = config_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"white_balance_gains": [1.33, 1.0, 0.6]}))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        assert win.pipeline.white_balance[0] == pytest.approx(1.33 ** B.GAMMA, rel=1e-3)
        saved = Settings.load()
        assert saved.white_balance_domain == "linear"
        assert saved.white_balance_gains[0] == pytest.approx(1.33 ** B.GAMMA, rel=1e-3)
    finally:
        win.shutdown()


def test_picking_again_on_a_corrected_image_changes_nothing():
    """A pick is relative -- it is taken off the image as displayed, which
    already carries whatever is in force. So the second press on an
    already-neutral region has to be a no-op, or the control would drift
    every time somebody checked their work."""
    blue = _patch(b=200, g=120, r=90)
    first = B.from_region(blue)
    fixed = B.applied(blue, B.lut(first))
    assert B.from_region(fixed) == pytest.approx(B.UNITY, abs=0.02)


def test_green_is_the_reference_whatever_it_is_handed():
    """Normalised here rather than trusted, so a stored value from an older
    version -- or a hand-edited settings file -- cannot brighten the whole
    preview by calling itself a white balance."""
    assert B.sane((2.0, 2.0, 2.0)) == pytest.approx(B.UNITY)
    assert B.sane((4.0, 2.0, 1.0)) == pytest.approx((2.0, 1.0, 0.5))


def test_nonsense_gains_become_no_correction():
    """These reach us from a JSON file somebody may have edited."""
    for bad in (("x", None, 0), (0, 0, 0), (1, 0, 1), (float("nan"), 1, 1),
                (float("inf"), 1, 1), (-1, 1, 1), None, (1, 2)):
        assert B.sane(bad) == B.UNITY, bad


def test_an_absurd_gain_is_clamped_rather_than_obeyed():
    """A region picked on a blown highlight, where one channel has already
    clipped and reads far darker than it really is."""
    r, g, b = B.sane((1e6, 1.0, 1.0))
    assert r == B.MAX_GAIN and g == 1.0


def test_unity_costs_nothing_at_all():
    """Off has to be free, not merely cheap: this runs on every frame."""
    assert B.lut(B.UNITY) is None
    frame = _patch(b=10, g=20, r=30)
    out = B.applied(frame, None)
    assert np.array_equal(out, frame)
    assert out is not frame, "handed back the pooled buffer itself"


def test_a_mono_frame_is_left_alone():
    assert B.from_region(np.zeros((8, 8), np.uint8)) == B.UNITY
    assert B.from_region(np.zeros((0, 0, 3), np.uint8)) == B.UNITY


def test_the_pipeline_balances_the_preview_and_not_the_histogram():
    """The instruments report the sensor's own levels -- that is what the
    preview LUT exists for. Correcting the frame first would put the cast
    back into the numbers that machinery was written to take out."""
    from darlaston.camera.buffers import BufferPool, Frame
    from darlaston.live.pipeline import LivePipeline

    got = []
    pipe = LivePipeline(got.append)
    pool = BufferPool((64, 96, 3), np.uint8, count=1)

    def push():
        buf = pool.acquire()
        buf[:] = _patch(b=200, g=120, r=90, shape=(64, 96))
        f = Frame(data=buf, seq=len(got), timestamp=0.0, exposure_us=8000,
                  gain_pct=100, binned=True, _pool=pool)
        pipe._analyse(f)
        f.release()

    push()
    plain = got[-1]
    before = plain.histogram.copy()
    assert plain.preview[..., 0].mean() > plain.preview[..., 2].mean(), \
        "the premise: an uncorrected blue cast"

    pipe.set_white_balance(B.from_region(_patch(b=200, g=120, r=90)))
    push()
    done = got[-1]
    channels = [float(done.preview[..., i].mean()) for i in range(3)]
    assert max(channels) - min(channels) <= 2.0, f"preview not balanced: {channels}"
    assert np.array_equal(done.histogram, before), \
        "the balance reached the instruments"


# ---- and it has to reach the files, not just the screen ------------------

def _shoot(tmp_path, **settings):
    """One real capture through the real path, returning what it applied."""
    import threading
    import types

    from darlaston.camera.mock import MockCamera
    from darlaston.capture.still import StillCapture
    from darlaston.session.settings import Settings

    cam = MockCamera(fps=30.0)
    cam.open()
    done, out = threading.Event(), []
    cap = StillCapture(types.SimpleNamespace(backend=cam),
                       Settings(capture_root=str(tmp_path), **settings),
                       on_result=lambda r: (out.append(r), done.set()))
    assert cap.trigger(None, subject="t")
    assert done.wait(30)
    cam.close()
    return out[-1]


def test_a_picked_balance_reaches_the_capture(tmp_path):
    """Colour temperature moves every time the lamp is turned, and nobody
    is going to reshoot four blank fields because they dimmed a halogen.
    A balance picked off the screen has to reach the files."""
    plain = _shoot(tmp_path / "off")
    assert plain.ok, plain.message
    assert "wb(picked)" not in plain.applied

    picked = _shoot(tmp_path / "on", white_balance_gains=[1.4, 1.0, 0.7])
    assert picked.ok, picked.message
    assert "wb(picked)" in picked.applied, (
        f"the picked balance never reached the file: {picked.applied}")


def test_a_picked_balance_works_with_no_calibration_at_all(tmp_path):
    """The common case for somebody who has just changed the lamp: no dark,
    no flat, nothing in the store. It must not need one."""
    got = _shoot(tmp_path, white_balance_gains=[1.4, 1.0, 0.7])
    assert got.ok, got.message
    assert got.applied == ("wb(picked)",), got.applied


def test_the_file_says_which_balance_it_carries(tmp_path):
    """"wb" and "wb(picked)" are different provenance, and a file that
    cannot tell them apart cannot be trusted about either."""
    got = _shoot(tmp_path, white_balance_gains=[1.4, 1.0, 0.7])
    assert "wb" not in got.applied, (
        f"claimed a measured balance it does not have: {got.applied}")


# ---- the controls --------------------------------------------------------

def test_the_two_boxes_mean_different_things(qapp):
    """Focus assist is aimed at the subject; a balance has to come off
    something that ought to be neutral, which is by definition not the
    subject. One box could not serve both."""
    from darlaston.ui.widgets import LiveView

    view = LiveView()
    focus, wb = [], []
    view.region_drawn.connect(focus.append)
    view.balance_region_drawn.connect(wb.append)

    view.set_frame(np.zeros((240, 320, 3), np.uint8), None)
    view.resize(320, 240)

    def drag():
        from PySide6 import QtCore, QtGui
        press = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(40, 40),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)
        release = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease, QtCore.QPointF(160, 140),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)
        view.mousePressEvent(press)
        view.mouseReleaseEvent(release)

    drag()
    assert len(focus) == 1 and not wb, "unarmed, a drag must set focus"
    view.arm_balance(True)
    drag()
    assert len(focus) == 1 and len(wb) == 1, "armed, a drag must set balance"


def _push_cast(win, b=200, g=120, r=90, shape=(240, 320)):
    """One frame of a flat colour cast, through the real pipeline.

    Through it rather than around it, because picking now reads the
    *uncorrected* sample the pipeline takes on the way past -- which is
    the whole fix, and a test that set `_last_preview` by hand would go
    around the thing it is checking.
    """
    from darlaston.camera.buffers import BufferPool, Frame

    pool = BufferPool((*shape, 3), np.uint8, count=1)
    buf = pool.acquire()
    buf[..., 0], buf[..., 1], buf[..., 2] = b, g, r
    frame = Frame(data=buf, seq=0, timestamp=0.0, exposure_us=8000,
                  gain_pct=100, binned=True, _pool=pool)
    win.pipeline._analyse(frame)
    frame.release()


def test_one_press_arrives_rather_than_creeping(qapp, tmp_path, monkeypatch):
    """Nate caught this at the bench: the balance crept toward neutral over
    several clicks. Picking read the *displayed* frame, which already
    carried the last press's correction, so a press could only remove part
    of what was left. One press has to mean "make this grey"."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        seen = []
        for _ in range(4):
            _push_cast(win)
            win._take_white_balance()
            seen.append(win.pipeline.white_balance)
        # The gain is derived in linear light now, not from the display-
        # encoded means, so the ratio that neutralises a 120-green/90-red
        # patch is (120/90) raised through the gamma, not the bare ratio.
        assert seen[0][0] == pytest.approx((120 / 90) ** B.GAMMA, rel=0.02), \
            f"one press did not arrive: {seen[0]}"
        for later in seen[1:]:
            assert later == pytest.approx(seen[0], rel=1e-6), \
                f"the balance drifted on a repeated press: {seen}"
    finally:
        win.shutdown()


def test_reset_returns_the_sensors_own_colour(qapp, tmp_path, monkeypatch):
    """Two buttons rather than three: the way back to the sensor's own
    colour is the same control that left it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        _push_cast(win)
        win._take_white_balance()
        assert win.pipeline.white_balance != B.UNITY
        assert win.wb_reset.isEnabled(), "reset should be live once balanced"

        win._reset_white_balance()
        assert win.pipeline.white_balance == B.UNITY, "reset did not reset"
        assert tuple(win.settings.white_balance_gains) == B.UNITY
        assert not win.wb_reset.isEnabled(), "reset offered with nothing to undo"
    finally:
        win.shutdown()


def test_a_picked_balance_survives_a_restart(qapp, tmp_path, monkeypatch):
    """Somebody looking at the same kind of specimen under the same lamp
    should start nearer where they want to be."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    first = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        _push_cast(first)
        first._take_white_balance()
        kept = first.pipeline.white_balance
    finally:
        first.shutdown()

    again = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        assert again.wb_reset.isEnabled(), "came back with no balance"
        assert again.pipeline.white_balance == pytest.approx(kept), \
            "the preview came back a different colour than it was left"
    finally:
        again.shutdown()


def test_the_button_becomes_pressable_when_the_camera_arrives(qapp, tmp_path,
                                                              monkeypatch):
    """It was disabled once at startup, before the session had come up, and
    nothing enabled it again -- so it could not be pressed at all. The
    per-frame refresh that used to paper over this went with the numeric
    readout it existed for."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.base import CameraInfo, CameraState
    from darlaston.camera.mock import MockCamera
    from darlaston.camera.session import SessionStatus
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        # The window drives a real session against the synthetic camera,
        # and its supervisor thread publishes status of its own -- which
        # overwrites anything injected here, at whatever moment the
        # scheduler chooses. Stopping it first makes this a test of the
        # rule rather than of the race.
        win.session.stop()
        # Said out loud rather than assumed. The window starts a real
        # session against the synthetic camera, so "before there is a
        # camera" is a race the moment the machine is busy -- it can come
        # up between the constructor and this line. Stating the state
        # tests the rule instead of the scheduler.
        dark = SessionStatus(CameraState.DISCONNECTED)
        win.session._status = dark
        win._on_status(dark)
        assert not win.wb_pick.isEnabled(), "offered before there is a camera"

        live = SessionStatus(CameraState.STREAMING, info=CameraInfo(
            model="m", serial="s", resolutions=(), max_bit_depth=12,
            bayer_pattern="GBRG", exposure_range_us=(100, 10 ** 6),
            gain_range_pct=(100, 2000)))
        win.session._status = live
        win._on_status(live)
        assert win.wb_pick.isEnabled(), "still unpressable with a live camera"
        assert win.wb_pick.property("invite") == "true", "gave no sign it wants pressing"
    finally:
        win.shutdown()


def test_the_button_says_which_of_its_three_states_it_is_in(qapp, tmp_path,
                                                            monkeypatch):
    """Dim when there is nothing to do, brass when there is, filled while
    it is doing it. A control that looks the same whether or not it can be
    pressed is one people stop trying."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.base import CameraInfo, CameraState
    from darlaston.camera.mock import MockCamera
    from darlaston.camera.session import SessionStatus
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        # The window drives a real session against the synthetic camera,
        # and its supervisor thread publishes status of its own -- which
        # overwrites anything injected here, at whatever moment the
        # scheduler chooses. Stopping it first makes this a test of the
        # rule rather than of the race.
        win.session.stop()
        live = SessionStatus(CameraState.STREAMING, info=CameraInfo(
            model="m", serial="s", resolutions=(), max_bit_depth=12,
            bayer_pattern="GBRG", exposure_range_us=(100, 10 ** 6),
            gain_range_pct=(100, 2000)))
        win.session._status = live
        win._on_status(live)

        # Inviting, not lit.
        assert win.wb_pick.property("invite") == "true"
        assert not win.wb_pick.isChecked()

        # Lit while armed, and no longer inviting -- it is already doing it.
        win._arm_white_balance()
        assert win.wb_pick.isChecked()
        assert win.wb_pick.property("invite") == "false"

        # Back to inviting once the point has been taken.
        win.view.arm_balance(False)
        win._on_balance_region((0.4, 0.4, 0.2, 0.2))
        assert not win.wb_pick.isChecked()
        assert win.wb_pick.property("invite") == "true"

        # Reset only invites when there is something to undo.
        assert win.wb_reset.property("invite") == "false"
        win.settings.white_balance_gains = [1.4, 1.0, 0.7]
        win._refresh_wb()
        assert win.wb_reset.isEnabled()
        assert win.wb_reset.property("invite") == "true"
    finally:
        win.shutdown()


def test_a_camera_leaving_mid_gesture_does_not_strand_the_operator(
        qapp, tmp_path, monkeypatch):
    """Armed, then the cable is nudged. Without this the cursor stays a
    crosshair over a dead preview, there is nothing to sample, and the
    control that would let them out is greyed out for having no camera."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.base import CameraInfo, CameraState
    from darlaston.camera.mock import MockCamera
    from darlaston.camera.session import SessionStatus
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        win.session.stop()
        live = SessionStatus(CameraState.STREAMING, info=CameraInfo(
            model="m", serial="s", resolutions=(), max_bit_depth=12,
            bayer_pattern="GBRG", exposure_range_us=(100, 10 ** 6),
            gain_range_pct=(100, 2000)))
        win.session._status = live
        win._on_status(live)

        win._arm_white_balance()
        assert win.view.armed_for_balance

        gone = SessionStatus(CameraState.DISCONNECTED)
        win.session._status = gone
        win._on_status(gone)
        assert not win.view.armed_for_balance, "left waiting for a point"
    finally:
        win.shutdown()


# ---- the camera's own balance stage -----------------------------------------

class _FakeIsp:
    """A camera ISP: codes in, a patch's channel means out.

    Forty codes per stop, deliberately not the balancer's opening guess
    of sixty-four, and a cast that blows two channels at the start -- the
    magenta mount that started all this.
    """

    def __init__(self, cast=(0.9, 1.0, 3.6), base=180.0, slope=40.0):
        self.cast = cast                  # B, G, R multipliers on the scene
        self.base, self.slope = base, slope
        self.codes = (0, 0, 0)
        self.writes = []

    def sample(self):
        r_code, g_code, b_code = self.codes
        out = []
        for tint, code in zip(self.cast, (b_code, g_code, r_code)):
            out.append(min(255.0, self.base * tint * 2.0 ** (code / self.slope)))
        return tuple(out)                 # B, G, R

    def clipped(self):
        return tuple(1.0 if v >= 255.0 else 0.0 for v in self.sample())


def test_the_isp_balancer_converges_on_a_camera_whose_scale_it_was_not_told():
    cam = _FakeIsp()
    loop = B.IspBalancer(cam.codes)
    assert B.patch_is_clipped(cam.sample(), cam.clipped()), "starts blown"
    for _ in range(loop.MAX_STEPS + 1):
        proposed = loop.propose(cam.sample(), cam.clipped())
        if proposed is None:
            break
        cam.codes = proposed
        cam.writes.append(proposed)
    b, g, r = cam.sample()
    assert loop.done and loop.reason == "neutral", (loop.reason, cam.sample())
    assert abs(r - g) / g < 0.03 and abs(b - g) / g < 0.03, cam.sample()
    assert cam.codes[1] == 0, "green is the reference and never moves"
    assert len(cam.writes) <= 6, cam.writes


def test_the_balancer_stops_at_the_edge_rather_than_spinning():
    cam = _FakeIsp(cast=(1.0, 1.0, 40.0))       # beyond what -127 can reach
    loop = B.IspBalancer(cam.codes)
    for _ in range(loop.MAX_STEPS + 2):
        proposed = loop.propose(cam.sample(), cam.clipped())
        if proposed is None:
            break
        cam.codes = proposed
    assert loop.done and loop.reason in ("at the edge of the range", "out of steps")
    assert cam.codes[0] == -127


def test_a_blown_patch_is_refused_where_there_is_no_isp(qapp, tmp_path, monkeypatch):
    """A pick off a clipped channel neutralises the clipping, not the
    light: the mount goes grey and every dimmer pixel keeps its cast."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        _push_cast(win, b=255, g=120, r=255)
        assert max(win.pipeline.balance_sample_clipped) > 0.9
        win._take_white_balance()
        assert win.pipeline.white_balance == B.UNITY
        _push_cast(win, b=200, g=120, r=90)
        win._take_white_balance()
        assert win.pipeline.white_balance != B.UNITY, "an honest patch still picks"
    finally:
        win.shutdown()


def test_the_pick_drives_the_camera_isp_when_it_has_one(qapp, tmp_path, monkeypatch):
    """End to end on the synthetic camera: a magenta cast, a camera with a
    balance stage, one press -- and the codes converge while the display
    trim retires to unity."""
    import time
    from PySide6 import QtWidgets
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    cam = MockCamera(fps=30.0)
    cam.cast = (0.8, 1.0, 1.7)                  # B, G, R: a warm magenta
    cam.grow_isp_white_balance()
    win = MainWindow(lambda: cam)
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not (
                win.session.status.is_live and win.pipeline.balance_sample):
            QtWidgets.QApplication.processEvents()
            time.sleep(0.02)
        assert win.session.status.is_live
        before = win.pipeline.balance_sample
        assert before[2] > before[1] * 1.3, before
        win._take_white_balance()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and win._isp_loop is not None:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.02)
        assert win._isp_loop is None, "the loop did not finish"
        assert win._isp_balanced and win.settings.isp_white_balance
        assert cam.isp_white_balance() != (0, 0, 0)
        assert win.pipeline.white_balance == B.UNITY, "the camera carries it, not the LUT"
        # Let a few balanced frames through, then read the patch.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.02)
        b, g, r = win.pipeline.balance_sample
        assert abs(r - g) / g < 0.05 and abs(b - g) / g < 0.05, (b, g, r)
        # Reset puts the camera back to its default and forgets.
        win._reset_white_balance()
        assert cam.isp_white_balance() == (0, 0, 0)
        assert win.settings.isp_white_balance is None and not win._isp_balanced
    finally:
        win.shutdown()
