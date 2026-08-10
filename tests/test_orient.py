"""The orientation: pictures turn, sensors and instruments do not."""
import numpy as np
import pytest

from darlaston.process import orient


def _frame() -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)


def test_upright_returns_the_frame_itself():
    a = _frame()
    assert orient.frame(a, 0, False) is a
    # Nonsense degrades to upright, never to an exception mid-frame.
    assert orient.frame(a, 45, False) is a
    assert orient.sane_rotation("sideways") == 0
    assert orient.sane_rotation(None) == 0
    assert orient.sane_rotation(270) == 270


def test_rotations_match_their_numpy_definitions():
    a = _frame()
    # np.rot90 rotates counter-clockwise; ours is clockwise.
    assert (orient.frame(a, 90, False) == np.rot90(a, -1)).all()
    assert (orient.frame(a, 180, False) == np.rot90(a, 2)).all()
    assert (orient.frame(a, 270, False) == np.rot90(a, 1)).all()
    assert (orient.frame(a, 0, True) == a[:, ::-1]).all()


def test_mirror_is_applied_after_the_rotation():
    """The menu's promise: "Mirrored" swaps the left and right the
    operator sees, whatever the rotation."""
    a = _frame()
    assert (orient.frame(a, 90, True)
            == np.rot90(a, -1)[:, ::-1]).all()


def test_exif_codes_match_the_standard_table():
    """Pin the transpose cases against the raw array definitions:
    code 5 is a transpose (flip over the main diagonal), code 7 the
    transverse. Getting these two swapped is the classic mistake."""
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    assert orient.exif_code(0, False) == 1
    assert orient.exif_code(0, True) == 2
    assert orient.exif_code(180, False) == 3
    assert orient.exif_code(180, True) == 4
    assert orient.exif_code(90, False) == 6
    assert orient.exif_code(270, False) == 8
    # Code 5: viewer transposes. Our (90, mirror) display must equal it.
    assert (orient.frame(a, 90, True) == a.T).all()
    assert orient.exif_code(90, True) == 5
    # Code 7: the transverse -- transpose over the other diagonal.
    assert (orient.frame(a, 270, True) == a.T[::-1, ::-1]).all()
    assert orient.exif_code(270, True) == 7


@pytest.mark.parametrize("rot", orient.ROTATIONS)
@pytest.mark.parametrize("mirror", (False, True))
def test_rect_mapping_round_trips(rot, mirror):
    rng = np.random.default_rng(rot + mirror)
    for _ in range(20):
        x, y = rng.uniform(0, 0.6, 2)
        w, h = rng.uniform(0.05, 0.35, 2)
        rect = (x, y, w, h)
        there = orient.rect_to_view(rect, rot, mirror)
        back = orient.rect_to_sensor(there, rot, mirror)
        assert back == pytest.approx(rect, abs=1e-12)


def _tag_274(path) -> list[int]:
    """Every Orientation tag in the file, read straight off the TIFF
    structure so the assertion does not depend on our own writer being
    right about itself."""
    import struct

    data = path.read_bytes()
    assert data[:4] == b"II*\x00"
    found = []
    seen = set()
    offsets = [struct.unpack_from("<I", data, 4)[0]]
    while offsets:
        at = offsets.pop()
        if not at or at in seen:
            continue
        seen.add(at)
        n = struct.unpack_from("<H", data, at)[0]
        for i in range(n):
            code, kind, _count, value = struct.unpack_from(
                "<HHII", data, at + 2 + 12 * i)
            if code == 274:
                found.append(value & 0xFFFF)
            if code in (0x014A, 0x8769):          # SubIFDs, EXIF
                offsets.append(value)
        offsets.append(struct.unpack_from("<I", data, at + 2 + 12 * n)[0])
    return found


def test_a_capture_turns_the_jpeg_and_tags_the_dng(tmp_path):
    """The two files diverge on purpose: the JPEG's pixels turn because
    it carries no EXIF to say so, the DNG stays in the sensor's frame
    and says so with the standard tag."""
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
                        display_rotation=90, display_mirror=True)
    results, done = [], threading.Event()
    cap = StillCapture(types.SimpleNamespace(backend=cam), settings,
                       on_result=lambda r: (results.append(r), done.set()))
    assert cap.trigger(None, subject="t")
    assert done.wait(30)
    cam.close()
    r = results[-1]
    assert r.ok, r.message

    # The photograph is portrait now; the sensor is not.
    jpeg = cv2.imread(str(r.path.with_suffix(".jpg")))
    assert jpeg.shape[0] > jpeg.shape[1]
    raw = read_bayer_dng(str(r.path))
    assert raw.shape[1] > raw.shape[0], "the raw data must never turn"
    # (90, mirrored) is EXIF 5, the transpose. On every IFD that
    # carries the tag, or a thumbnailer and a developer would disagree
    # about which way up the same file is.
    codes = _tag_274(r.path)
    assert codes and all(c == 5 for c in codes), codes


def test_the_view_turns_and_the_instruments_do_not(qapp):
    """The fan-out's contract, geometry edition: the view gets the
    turned picture and the mapped focus box, the slide map keeps the
    sensor's frame, and a rectangle drawn on the view comes back to the
    pipeline in sensor space."""
    from darlaston.camera.mock import MockCamera
    from darlaston.live.pipeline import LiveSignals
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    try:
        win.settings.display_rotation = 90
        win.settings.display_mirror = False
        shown, boxes, mapped = [], [], []
        win.view.set_frame = lambda f, *a, **k: shown.append(f)
        win.view.set_focus_rect = lambda r: boxes.append(r)
        win.slidemap.update_live = lambda s: mapped.append(s.preview)

        preview = np.zeros((6, 8, 3), np.uint8)
        win._publish_signals(LiveSignals(
            seq=1, timestamp=1.0, preview=preview,
            histogram=np.zeros(256, np.int32),
            clipped_fraction=0.0, focus_metric=1.0,
            focus_fraction_of_peak=1.0, focus_trace=np.zeros(4, np.float32),
            focus_rect=(0.25, 0.25, 0.5, 0.5),
            stats={"analysed_fps": 30.0, "delivered": 1, "dropped": 0,
                   "exposure_us": 8000, "gain_pct": 100}))
        qapp.processEvents()

        assert shown and shown[0].shape[:2] == (8, 6), "the view turns"
        assert mapped and mapped[0] is preview, "the map does not"
        assert boxes and boxes[0] == pytest.approx(
            orient.rect_to_view((0.25, 0.25, 0.5, 0.5), 90, False))

        # And the return trip: a balance rect drawn on the turned view
        # reaches the pipeline in the sensor's frame.
        picked = []
        win.pipeline.set_balance_rect = lambda r: picked.append(r)
        win._on_balance_region((0.1, 0.2, 0.3, 0.4))
        assert picked[0] == pytest.approx(
            orient.rect_to_sensor((0.1, 0.2, 0.3, 0.4), 90, False))
    finally:
        win.close()


@pytest.mark.parametrize("rot", orient.ROTATIONS)
@pytest.mark.parametrize("mirror", (False, True))
def test_a_mapped_rect_lands_on_the_same_pixels(rot, mirror):
    """The property the whole module exists for: cutting the mapped
    rect out of the turned image yields the turned cut of the original
    rect. If this holds, the focus box and the balance patch point at
    the same specimen on screen and on the sensor."""
    a = _frame()
    rect = (0.25, 1 / 3, 0.5, 1 / 3)          # exact on an 8x6 grid

    def cut(img, r):
        h, w = img.shape[:2]
        x, y, rw, rh = r
        return img[round(y * h):round((y + rh) * h),
                   round(x * w):round((x + rw) * w)]

    turned = orient.frame(a, rot, mirror)
    mapped = orient.rect_to_view(rect, rot, mirror)
    assert (cut(turned, mapped)
            == orient.frame(cut(a, rect), rot, mirror)).all()
