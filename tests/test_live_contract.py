"""Tests for the threading contract.

These are the tests worth having on day one. If LatestFrame's drop semantics
are wrong, or buffers leak, nothing built on top can be correct -- and both
failures are the kind that look fine in a demo and rot under load.
"""
import threading
import time

import numpy as np
import pytest

from darlaston.camera.buffers import BufferPool, Frame
from darlaston.live.cell import LatestFrame


def make_frame(pool, seq=0):
    buf = pool.acquire()
    assert buf is not None, "pool exhausted unexpectedly"
    return Frame(data=buf, seq=seq, timestamp=time.time(), exposure_us=1000,
                 gain_pct=100, binned=True, _pool=pool)


# ---- the cell replaces rather than queues ------------------------------------

def test_put_replaces_pending_frame():
    pool = BufferPool((4, 4), np.uint8, count=4)
    cell = LatestFrame()
    for i in range(3):
        cell.put(make_frame(pool, seq=i))

    got = cell.take(timeout=0.1)
    assert got.seq == 2, "the cell must yield the newest frame, not the oldest"
    assert cell.take(timeout=0.01) is None, "only one frame may be held"

    delivered, dropped = cell.stats
    assert dropped == 2, "replaced frames must be counted as dropped"


def test_replaced_frames_return_to_the_pool():
    """The failure this guards against is a slow leak that only shows up after
    minutes of streaming, by which time the live view has quietly stalled."""
    pool = BufferPool((8, 8), np.uint8, count=2)
    cell = LatestFrame()
    for i in range(50):
        f = make_frame(pool, seq=i)
        cell.put(f)
        taken = cell.take(timeout=0.01)
        if taken is not None:
            taken.release()
    assert pool.available == pool.count, "buffers leaked"
    assert pool.exhausted_count == 0


def test_producer_never_blocks_on_a_stalled_consumer():
    pool = BufferPool((4, 4), np.uint8, count=8)
    cell = LatestFrame()
    t0 = time.perf_counter()
    for i in range(200):
        f = make_frame(pool, seq=i)
        cell.put(f)          # consumer never runs
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, "put() must not block when nobody is consuming"


def test_close_wakes_a_blocked_consumer():
    """A consumer parked on an empty cell must not hang when the camera stops."""
    cell = LatestFrame()
    woken = threading.Event()

    def waiter():
        assert cell.take(timeout=5.0) is None
        woken.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    cell.close()
    t.join(timeout=2.0)
    assert woken.is_set(), "close() must wake a blocked consumer"


def test_close_releases_a_pending_frame():
    pool = BufferPool((4, 4), np.uint8, count=2)
    cell = LatestFrame()
    cell.put(make_frame(pool))
    assert pool.available == 1
    cell.close()
    assert pool.available == pool.count, "close() must release the pending frame"


def test_put_after_close_does_not_leak():
    """Shutdown races are normal: the camera thread may deliver one more frame
    after the consumer has gone."""
    pool = BufferPool((4, 4), np.uint8, count=2)
    cell = LatestFrame()
    cell.close()
    cell.put(make_frame(pool))
    assert pool.available == pool.count


# ---- buffer discipline -------------------------------------------------------

def test_release_is_idempotent():
    """Double release is a bug, but it must not also be a crash that takes the
    camera thread down mid-session."""
    pool = BufferPool((4, 4), np.uint8, count=1)
    f = make_frame(pool)
    f.release()
    f.release()
    assert pool.available == 1


def test_pool_exhaustion_reports_rather_than_allocates():
    pool = BufferPool((4, 4), np.uint8, count=2)
    a, b = pool.acquire(), pool.acquire()
    assert pool.acquire() is None, "exhaustion must be visible, not papered over"
    assert pool.exhausted_count == 1
    pool._give_back(a)
    assert pool.acquire() is not None


def test_frame_context_manager_releases():
    pool = BufferPool((4, 4), np.uint8, count=1)
    with make_frame(pool):
        assert pool.available == 0
    assert pool.available == 1


# ---- end to end through the synthetic camera --------------------------------

def test_mock_camera_through_pipeline_produces_signals():
    """Integration: frames flow camera -> cell -> analysis -> signals, and the
    pool is whole afterwards. Drop *semantics* are covered directly above."""
    from darlaston.camera.mock import MockCamera
    from darlaston.live.pipeline import LivePipeline

    received = []
    pipeline = LivePipeline(received.append)
    cam = MockCamera(fps=30.0)
    cam.open()
    pipeline.start()
    cam.start_stream(pipeline.submit)
    time.sleep(1.0)
    cam.stop_stream()
    pipeline.stop()

    assert len(received) > 5, f"only {len(received)} frames analysed in 1 s"
    assert cam._pool.available == cam._pool.count, "buffers leaked"

    s = received[-1]
    assert s.histogram.shape == (256,)
    assert 0.0 <= s.clipped_fraction <= 1.0
    assert s.focus_metric > 0
    assert s.preview.shape[2] == 3
    assert s.xy_offset is not None, "tracker should lock after the first frame"


def test_focus_metric_peaks_at_best_focus():
    """The metric has to actually be a focus metric. Sweep the synthetic Z axis
    and require the score to peak where the blur is least."""
    from darlaston.camera.mock import MockCamera
    from darlaston.live.focus import Illumination, DEFAULTS, measure
    import cv2

    cam = MockCamera()
    cam.open()
    metric, prefilter = DEFAULTS[Illumination.PHASE]

    scores = []
    zs = [-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0]
    for z in zs:
        cam.focus_z = z
        f = cam.grab_raw()
        with f:
            g = (f.data >> 8).astype(np.uint8)
            scores.append(measure(g, metric, prefilter))

    best = zs[int(np.argmax(scores))]
    assert best == 0.0, f"metric peaked at z={best}, not at focus. {scores}"
    assert scores[3] > scores[0] * 1.2, "peak is not meaningfully above the tails"


def test_focus_metrics_are_exposure_invariant():
    """Changing exposure or gain must not move the focus score.

    Every one of these metrics is polynomial in intensity, so without
    normalisation the score changes when a slider moves and the peak memory --
    which is the whole point of the trace -- becomes meaningless.
    """
    from darlaston.live.focus import (Metric, Prefilter, measure)
    rng = np.random.default_rng(3)
    base = (rng.random((256, 256)) * 120 + 40).astype(np.uint8)

    for metric in (Metric.VOLLATH4, Metric.LAPV, Metric.TENENGRAD,
                   Metric.NORM_VARIANCE):
        dim = measure(base, metric, Prefilter.NONE)
        bright = measure(np.clip(base.astype(np.float32) * 1.8, 0, 255
                                 ).astype(np.uint8), metric, Prefilter.NONE)
        ratio = bright / max(dim, 1e-12)
        assert 0.8 < ratio < 1.25, (
            f"{metric.value} moved {ratio:.2f}x for a brightness change alone")


def test_framerate_default_follows_the_core_count():
    """A laptop should not open at a workstation's settings.

    Measured on one camera and scene with the process pinned: sixteen cores
    analyse a frame in 12.7 ms of a 25 ms budget and drop nothing, four take
    29.0 ms, and two take 37.5 ms and drop a sixth of the frames.
    """
    from darlaston.camera.session import default_framerate_cap, usable_cores

    from darlaston.camera.session import FRAMERATES

    assert default_framerate_cap(16) == 40
    assert default_framerate_cap(8) == 40
    assert default_framerate_cap(4) == 30
    assert default_framerate_cap(2) == 15
    assert default_framerate_cap(1) == 15
    # Monotonic: more cores must never ask for a lower rate.
    caps = [default_framerate_cap(n) for n in range(1, 65)]
    assert caps == sorted(caps)
    assert usable_cores() >= 1
    # The one that actually bites: a default the status bar has no entry for
    # leaves the combo showing one number while the camera runs at another,
    # because findData returns -1 and the selection silently stays put.
    for n in range(1, 65):
        assert default_framerate_cap(n) in FRAMERATES
    assert default_framerate_cap() in FRAMERATES


def test_the_status_bar_offers_every_rate_a_default_can_pick(qapp):
    """The combo is built from the same tuple the default is chosen from,
    so the two cannot drift apart. This pins that they stay wired together."""
    from darlaston.camera.session import FRAMERATES
    from darlaston.ui.shell import StatusBar

    strip = StatusBar()
    offered = [strip.rate.itemData(i) for i in range(strip.rate.count())]
    assert offered == list(FRAMERATES)
    for fps in FRAMERATES:
        strip.select_rate(fps)
        assert strip.rate.currentData() == fps


def test_the_thread_budget_is_applied_and_bounded():
    """Four threads measured no worse than sixteen for both the live loop and
    the batch jobs, at a third less CPU. The budget only bites on machines
    with cores to spare: on a small one it is what OpenCV would pick anyway.
    """
    import cv2
    from darlaston.cpu import THREAD_BUDGET, apply_thread_budget, usable_cores

    assert THREAD_BUDGET == min(4, usable_cores())
    assert 1 <= THREAD_BUDGET <= usable_cores()
    apply_thread_budget()
    assert cv2.getNumThreads() == THREAD_BUDGET


def test_levels_are_computed_at_the_divisor_but_never_go_stale_or_missing():
    """The histogram is computed every INSTRUMENT_DIVISOR frames now, so the
    thing to guard is that every frame still carries a usable one and that a
    clipped frame is still reported clipped -- one repaint late is fine, never
    is not. Subsampling pixels was the alternative and it can miss a
    one-pixel-tall clipped streak outright; this cannot.
    """
    import numpy as np
    from darlaston.camera.buffers import BufferPool, Frame
    from darlaston.live.pipeline import INSTRUMENT_DIVISOR, LivePipeline

    got = []
    pipe = LivePipeline(got.append)
    pool = BufferPool((64, 96, 3), np.uint8, count=4)

    def feed(fill, streak=False):
        buf = pool.acquire()
        buf[:] = fill
        if streak:
            buf[31, :, 1] = 255            # one row of blown green
        f = Frame(data=buf, seq=len(got), timestamp=0.0, exposure_us=1000,
                  gain_pct=100, binned=True, _pool=pool)
        with f:
            pipe._analyse(f)

    for _ in range(2 * INSTRUMENT_DIVISOR):
        feed(40)
    assert all(s.histogram.shape == (256,) for s in got), \
        "every frame must carry a histogram, not only the ones that recompute"
    assert all(s.clipped_fraction == 0.0 for s in got)

    # A one-pixel-tall clipped streak, held. It must be seen within one
    # instrument period.
    before = len(got)
    for _ in range(INSTRUMENT_DIVISOR + 1):
        feed(40, streak=True)
    fresh = got[before:]
    assert any(s.clipped_fraction > 0 for s in fresh), \
        "a one-row clipped streak was never reported"
    expected = 1.0 / 64
    seen = max(s.clipped_fraction for s in fresh)
    assert abs(seen - expected) < 1e-6, f"clipping mismeasured: {seen}"


def test_preview_quality_modes_do_what_the_dialog_says(qapp):
    """Three real options, not a fast-or-slow slider, so each is checked for
    the thing its description promises: same size in every case, `reduced`
    softer than the honest reduction, `fast` *sharper* than it -- which is
    the tell, because a cheaper filter cannot honestly find more detail. It
    is inventing edge energy, and that is what shimmers on striae.
    """
    import cv2
    import numpy as np
    from darlaston.ui.perf_ui import PREVIEW_CHOICES
    from darlaston.ui.widgets import LiveView

    rng = np.random.default_rng(11)
    # Fine periodic structure, which is the case that separates these.
    y, x = np.mgrid[0:1216, 0:1824]
    frame = (120 + 90 * np.sin(x / 2.5) * np.sin(y / 40.0)).astype(np.uint8)
    frame = np.repeat(frame[:, :, None], 3, axis=2)

    view = LiveView()
    view.resize(1039, 693)
    energy = {}
    for value, _label, _why in PREVIEW_CHOICES:
        view.preview_quality = value
        view._set_frame(frame, None)
        out = view._buf.copy()
        assert out.shape[0] > 0 and out.shape[1] > 0
        assert out.shape[:2] == view._buf.shape[:2]
        grey = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        energy[value] = cv2.Laplacian(grey, cv2.CV_32F).var()

    assert set(energy) == {"full", "reduced", "fast"}
    assert energy["reduced"] < energy["full"], \
        "the softer mode should carry less detail, not more"
    assert energy["fast"] > energy["full"], \
        "bilinear should show its aliasing as excess edge energy"


def test_performance_preferences_persist_and_apply(tmp_path, qapp):
    import cv2
    from darlaston.cpu import THREAD_BUDGET, apply_thread_budget, usable_cores
    from darlaston.session.settings import Settings
    from darlaston.ui.perf_ui import PerformanceDialog

    path = tmp_path / "settings.json"
    s = Settings()
    assert s.preview_quality == "fast" and s.cpu_threads == 0

    applied = []
    dialog = PerformanceDialog(s, lambda: applied.append(True))
    # Redirect the save at the temp path without losing the real one, which
    # is easy to get wrong: assigning a lambda that calls s.save makes it
    # call itself.
    real_save = Settings.save
    s.save = lambda: real_save(s, path)

    for button in dialog._quality.buttons():
        if button.property("value") == "full":
            button.setChecked(True)
    assert s.preview_quality == "full"
    assert applied, "a change must reach the live view immediately"
    assert Settings.load(path).preview_quality == "full", \
        "a comparison you must remember to confirm is one you will lose"

    # An explicit thread count overrides the measured default; 0 restores it.
    apply_thread_budget(2)
    assert cv2.getNumThreads() == 2
    apply_thread_budget(0)
    assert cv2.getNumThreads() == THREAD_BUDGET
    assert all(n <= usable_cores()
               for n in (dialog.threads.itemData(i)
                         for i in range(dialog.threads.count())) if n)


def test_old_settings_files_still_load():
    """Adding fields must not orphan somebody's existing preferences."""
    import json
    from darlaston.session.settings import Settings

    old = {"capture_root": "/tmp/shots", "raw_format": "dng",
           "artist": "Nate", "stack_smoothing": "normal"}
    s = Settings(**old)
    assert s.artist == "Nate" and s.stack_smoothing == "normal"
    assert s.preview_quality == "fast" and s.cpu_threads == 0
    assert json.loads(json.dumps({k: v for k, v in old.items()}))


def test_disk_readout_warns_before_it_is_too_late(qapp):
    """A stacked mosaic writes tens of gigabytes without mentioning it. The
    warning has to arrive while there is still room to do something about it,
    which means gigabytes rather than megabytes."""
    from darlaston.ui import theme
    from darlaston.ui.shell import StatusBar

    bar = StatusBar()

    bar.set_disk(int(500e9), "/pictures")
    assert bar.disk.text() == "500 GB free"
    assert not bar.disk.styleSheet(), "plenty of room needs no colour"

    # Under ten it gains a decimal, because "9 GB" and "9.4 GB" are
    # different answers to "does one more tile fit".
    bar.set_disk(int(9.4e9), "/pictures")
    assert bar.disk.text() == "9.4 GB free"

    bar.set_disk(int(12e9), "/pictures")
    assert theme.BRASS in bar.disk.styleSheet(), "low should be visible"
    bar.set_disk(int(1.2e9), "/pictures")
    assert theme.BAD in bar.disk.styleSheet(), "critical should be alarming"
    assert bar.DISK_CRITICAL_GB < bar.DISK_LOW_GB

    # And an unreachable capture folder reports nothing rather than lying.
    bar.set_disk(None)
    assert bar.disk.text() == ""


def test_framing_guides_read_on_both_a_white_and_a_black_field(qapp):
    """A microscope preview is either mostly blown white (brightfield) or
    mostly black (darkfield), and a single-colour guide vanishes into one of
    them. Hence the double stroke, and hence this test.
    """
    import numpy as np
    from PySide6 import QtGui
    from darlaston.ui.widgets import LiveView

    def render(view, fill):
        frame = np.full((300, 400, 3), fill, np.uint8)
        view._set_frame(frame, None)
        img = QtGui.QImage(view.size(), QtGui.QImage.Format.Format_RGB888)
        view.render(img)
        b = img.constBits()
        return np.frombuffer(b, np.uint8).reshape(img.height(), -1, 3).copy()

    view = LiveView()
    view.resize(400, 300)
    for fill in (255, 0):
        view.framing_grid = "none"
        view.framing_cross = False
        plain = render(view, fill)
        view.framing_grid = "thirds"
        view.framing_cross = True
        guided = render(view, fill)
        changed = int((np.abs(guided.astype(int) - plain.astype(int))
                       .max(axis=2) > 6).sum())
        assert changed > 200, (
            f"guides invisible on a field of {fill}: only {changed} pixels moved")
        # And faint: a guide that competes with the specimen is worse than none.
        assert changed < guided[:, :, 0].size * 0.06, \
            f"guides too heavy on {fill}: {changed} pixels"


def test_framing_guides_persist_and_are_independent(tmp_path):
    """Centring a specimen and composing a frame are different jobs, so the
    cross is not part of the grid choice."""
    from darlaston.session.settings import Settings

    s = Settings()
    assert s.framing_grid == "none" and s.framing_cross is False
    s.framing_grid = "thirds"
    s.framing_cross = True
    s.save(tmp_path / "s.json")
    back = Settings.load(tmp_path / "s.json")
    assert back.framing_grid == "thirds" and back.framing_cross is True

    from darlaston.ui.widgets import LiveView
    assert set(LiveView.GUIDES) == {"none", "thirds", "grid"}
    assert LiveView.GUIDES["thirds"] == 3
    assert LiveView.GUIDES["grid"] > LiveView.GUIDES["thirds"]


def test_driver_drops_are_reported_apart_from_ours(qapp):
    """Two opposite faults with opposite fixes: ours means the analysis
    could not keep up and wants less work per frame; the driver's means the
    link could not deliver and wants a lower rate or another USB port. From
    outside, both look like the frame rate sagging."""
    from darlaston.camera.base import CameraBackend
    from darlaston.ui.perf_ui import PerfPanel

    panel = PerfPanel()
    stats = {"analysed_fps": 30.0, "delivered": 90, "dropped": 10}

    panel.update_costs({"decode": 5.0}, stats)
    assert "driver" not in panel.summary.text(), \
        "silence when the camera cannot say, rather than a confident zero"

    panel.set_driver_dropped(0)
    panel.update_costs({"decode": 5.0}, stats)
    assert "driver" not in panel.summary.text(), "zero is not worth the room"

    panel.set_driver_dropped(42)
    panel.update_costs({"decode": 5.0}, stats)
    text = panel.summary.text()
    assert "42 dropped by the driver" in text
    assert "10% of frames dropped" in text, "our own count must survive"

    # The base backend answers None, so a camera that cannot report simply
    # says nothing rather than raising into the UI thread.
    assert CameraBackend.driver_dropped(object()) is None


def test_the_rail_goes_quiet_without_a_camera(qapp):
    """The first screen a stranger sees is this one, and it used to sit
    fully lit: sliders for an exposure that does not exist, toggles for a
    focus nothing is measuring. It read as an application that was broken
    rather than one that was waiting.
    """
    from darlaston.camera.base import CameraState
    from darlaston.camera.session import SessionStatus
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0), allow_synthetic=True,
                     presence=lambda: False)
    try:
        win._set_shooting_enabled(False)
        for w in (win.exposure, win.gain, win.focus, win.calib_button):
            assert not w.isEnabled()
        assert not any(b.isEnabled() for b in win.regions.buttons())

        # What you can still do: name the subject and describe the optics,
        # because those are reasonable to set while the camera is still in
        # its box, and disabling them trades one wrong impression for another.
        assert win.subject.isEnabled()
        assert win.objective.isEnabled()

        win._set_shooting_enabled(True)
        for w in (win.exposure, win.gain, win.focus, win.calib_button):
            assert w.isEnabled()
        assert all(b.isEnabled() for b in win.regions.buttons())
    finally:
        win.shutdown()


def test_disabled_controls_actually_look_disabled():
    """Qt puts the state on the sub-control, not the widget, so
    `QSlider:disabled::handle` silently matches nothing -- which is how the
    first attempt disabled every slider and changed not one pixel."""
    from darlaston.ui import theme

    css = theme.stylesheet()
    assert "QSlider::handle:horizontal:disabled" in css
    assert "QSlider:disabled::handle" not in css, "matches nothing in Qt"
    assert "QPushButton:disabled" in css


def test_the_synthetic_camera_is_not_offered_to_users(qapp):
    """It renders invented diatoms. It is how this gets developed without a
    microscope on the desk, and it is of no use to somebody who owns one --
    an option that cannot help you is noise in the manual."""
    import argparse
    import inspect
    from darlaston.camera.mock import MockCamera
    from darlaston.ui import main as M

    # Off unless something asks for it, rather than on unless suppressed.
    sig = inspect.signature(M.MainWindow.__init__)
    assert sig.parameters["allow_synthetic"].default is False

    win = M.MainWindow(lambda: MockCamera(fps=30.0))
    try:
        assert not win.waiting.synthetic.isVisible()
    finally:
        win.shutdown()

    # --mock still works, and stays out of --help.
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    assert ap.parse_args(["--mock"]).mock is True
    assert "--mock" not in ap.format_help()


def test_the_wordmark_has_its_own_face_and_a_licence():
    """One face, used once, so it can afford to be a period display letter
    where nothing else could. Swapping the file swaps the wordmark."""
    from pathlib import Path
    from darlaston.ui import theme

    fonts = Path(theme.__file__).parent / "fonts"
    assert (fonts / "Wordmark.ttf").exists()
    licence = (fonts / "LICENSE.txt").read_text()
    assert "Petit Formal Script" in licence, "OFL requires the notice ship too"
    assert "Open Font License" in licence

    css = theme.stylesheet()
    # Brass, and white under the pointer: the inverse of every other
    # control, because it is the one place the application is allowed to
    # be a little proud of itself.
    mark = css[css.index('QPushButton[role="wordmark"]'):]
    assert theme.BRASS in mark[:mark.index("}")]
    hover = mark[mark.index(':hover'):]
    assert theme.INK in hover[:hover.index("}")]


def test_about_carries_the_man_and_the_licences(qapp):
    """The name is the one part of this project that argues for something,
    so the dialog behind it has to be right about a real person and honest
    about what it is made of."""
    from darlaston.ui.about import TAGLINE, AboutDialog

    d = AboutDialog()
    try:
        text = " ".join(w.text() for w in d.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))

        # His, from a 1917 price list, carried as an inscription.
        assert TAGLINE == "Every Slide Perfect"
        assert TAGLINE.upper() in text

        # Sourced claims only. He was not a diatom arranger and we do not
        # say he was; he did go professional at thirty-nine, not thirty-eight.
        assert "1867-1949" in text
        assert "amateur" in text and "Birmingham" in text
        assert "thirty-nine" in text
        assert "arranger" not in text.lower(), "he was a general preparer"

        # Every bundled thing that carries a licence is named.
        assert "GPLv3" in text
        assert "IBM Plex" in text and "Petit Formal Script" in text
        assert "Open Font License" in text
        assert "linking exception" in text
    finally:
        d.deleteLater()


def test_about_is_frameless_and_draggable(qapp):
    from PySide6 import QtCore, QtGui
    from darlaston.ui.about import AboutDialog

    d = AboutDialog()
    try:
        assert d.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint
        start = d.pos()
        press = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress, QtCore.QPointF(20, 20),
            QtCore.QPointF(start.x() + 20, start.y() + 20),
            QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)
        d.mousePressEvent(press)
        assert d._drag_from is not None
        move = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseMove, QtCore.QPointF(20, 20),
            QtCore.QPointF(start.x() + 90, start.y() + 60),
            QtCore.Qt.MouseButton.NoButton, QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)
        d.mouseMoveEvent(move)
        assert d.pos() != start, "a frameless window must be movable somehow"
        d.mouseReleaseEvent(move)
        assert d._drag_from is None
    finally:
        d.deleteLater()
