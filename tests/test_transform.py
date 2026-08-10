"""The rendering: a look worn by pictures and never by data."""
import numpy as np

from darlaston.process import transform


def _frame() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, (12, 16, 3), dtype=np.uint8)


def test_nothing_to_do_returns_the_frame_itself():
    a = _frame()
    assert transform.applied(a, "none") is a
    # An unknown mode -- a newer settings file, a hand edit -- degrades to
    # the untouched picture rather than to an exception in the frame loop.
    assert transform.applied(a, "sepia") is a
    assert transform.sane("sepia") == "none"
    assert transform.sane(None) == "none"
    assert transform.sane("") == "none"


def test_invert_is_its_own_inverse_and_never_writes_to_its_input():
    a = _frame()
    kept = a.copy()
    out = transform.applied(a, "invert")
    assert out is not a
    assert (a == kept).all()
    assert (out == 255 - a).all()
    assert (transform.applied(out, "invert") == a).all()


def test_swap_exchanges_red_and_blue_and_touches_nothing_else():
    a = _frame()
    out = transform.applied(a, "swap")
    assert (out[..., 0] == a[..., 2]).all()
    assert (out[..., 1] == a[..., 1]).all()
    assert (out[..., 2] == a[..., 0]).all()
    assert (transform.applied(out, "swap") == a).all()


def test_grey_is_a_colour_picture_that_happens_to_be_grey():
    out = transform.applied(_frame(), "grey")
    assert out.shape == (12, 16, 3)
    assert out.dtype == np.uint8
    assert (out[..., 0] == out[..., 1]).all()
    assert (out[..., 1] == out[..., 2]).all()


def test_grey_weighs_channels_by_what_they_are_not_where_they_sit():
    # One bright channel in position 0. Read as BGR that is blue, the
    # channel luma weighs least; read as RGB it is red, weighed nearly
    # three times heavier. If the order parameter did nothing, a DNG's
    # embedded preview would go grey through the wrong weights.
    a = np.zeros((4, 4, 3), np.uint8)
    a[..., 0] = 200
    as_bgr = transform.applied(a, "grey", order="bgr")
    as_rgb = transform.applied(a, "grey", order="rgb")
    assert int(as_bgr[0, 0, 0]) < int(as_rgb[0, 0, 0])


def test_a_mono_frame_has_no_channels_to_swap_or_weigh():
    m = np.arange(64, dtype=np.uint8).reshape(8, 8)
    assert transform.applied(m, "swap") is m
    assert transform.applied(m, "grey") is m
    # The invert still means something on mono and still works.
    assert (transform.applied(m, "invert") == 255 - m).all()


def test_the_rendering_key_appears_only_when_it_says_something():
    from darlaston.process.metadata import from_setup
    from darlaston.session.model import (CameraProfile, Objective,
                                         ScopeProfile, Setup, Turret)

    setup = Setup(camera=CameraProfile(serial="x"),
                  scope=ScopeProfile(id="s", name="Stand",
                                     turret=Turret([Objective(40, 0.75)])))
    plain = from_setup(setup, exposure_us=1000, gain_pct=100)
    worn = from_setup(setup, exposure_us=1000, gain_pct=100,
                      rendering="invert")
    assert "rendering=" not in plain.comment
    assert "rendering=invert" in worn.comment


def test_a_capture_carries_the_look_into_the_jpeg(tmp_path):
    """A brightfield negative is dark. The raw next to it is not touched,
    which the reader proves by finding sensor levels in it."""
    import threading
    import types

    import cv2

    from darlaston.camera.mock import MockCamera
    from darlaston.capture.still import StillCapture
    from darlaston.process.stitch import read_bayer_dng
    from darlaston.session.settings import Settings

    cam = MockCamera()
    cam.open()
    settings = Settings(capture_root=str(tmp_path),
                        display_transform="invert")
    results, done = [], threading.Event()
    cap = StillCapture(types.SimpleNamespace(backend=cam), settings,
                       on_result=lambda r: (results.append(r), done.set()))
    assert cap.trigger(None, subject="t")
    assert done.wait(30)
    cam.close()
    r = results[-1]
    assert r.ok, r.message

    jpeg = cv2.imread(str(r.path.with_suffix(".jpg")))
    assert jpeg is not None
    # The mock's field is bright; its negative is not.
    assert float(jpeg.mean()) < 100, f"JPEG mean {jpeg.mean():.0f}"
    # And the negative's raw develops bright: the data never inverted.
    raw = read_bayer_dng(str(r.path))
    assert float(raw.mean()) > float(raw.max()) / 4


def test_the_view_wears_the_rendering_and_the_instruments_do_not(qapp):
    """The fan-out's contract: the live view, the presentation and the
    stream show the operator's chosen look, while the slide map -- whose
    terrain the relocalizer matches probes against -- keeps reading
    sensor levels. Painting terrain in display space would blind the
    relocalizer to ground it mapped before the mode was toggled.
    """
    from darlaston.camera.mock import MockCamera
    from darlaston.live.pipeline import LiveSignals
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        win.settings.display_transform = "invert"
        shown, mapped = [], []
        win.view.set_frame = lambda f, *a, **k: shown.append(f)
        win.slidemap.update_live = lambda s: mapped.append(s.preview)

        preview = np.full((8, 8, 3), 10, np.uint8)
        win._publish_signals(LiveSignals(
            seq=1, timestamp=1.0, preview=preview,
            histogram=np.zeros(256, np.int32),
            clipped_fraction=0.0, focus_metric=1.0,
            focus_fraction_of_peak=1.0, focus_trace=np.zeros(4, np.float32),
            stats={"analysed_fps": 30.0, "delivered": 1, "dropped": 0,
                   "exposure_us": 8000, "gain_pct": 100}))
        qapp.processEvents()

        assert shown and (shown[0] == 245).all(), "the view is the picture"
        assert mapped and mapped[0] is preview, "the map is an instrument"
    finally:
        win.close()
