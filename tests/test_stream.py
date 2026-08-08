"""The presentation, served: a mirror with a caption, on the wire.

Held to the same promises as the window -- newest frame wins, a hold
freezes it, the live claim cannot outlive the feed -- plus the ones a
network service owes: nothing encoded for an audience of zero, and
nothing served at all unless somebody turned it on this session.
"""
from __future__ import annotations

import threading
import time
import urllib.request

import numpy as np
import pytest

from darlaston.ui.stream import STREAM_SIZE, Streamer


def _frame(level: int) -> np.ndarray:
    return np.full((300, 600, 3), level, np.uint8)


@pytest.fixture
def streamer(qapp):
    s = Streamer(0)                    # an ephemeral port; tests never collide
    yield s
    s.stop()


def _fetch(url: str, n: int, feed=None, timeout: float = 5.0) -> bytes:
    """The first `n` bytes of `url`, calling `feed` while waiting."""
    got = bytearray()
    error = []

    def read() -> None:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                while len(got) < n:
                    chunk = response.read(min(4096, n - len(got)))
                    if not chunk:
                        break
                    got.extend(chunk)
        except Exception as e:          # noqa: BLE001 -- reported to the test
            error.append(e)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    while reader.is_alive() and time.monotonic() < deadline:
        if feed is not None:
            feed()
        reader.join(timeout=0.05)
    if error:
        raise error[0]
    return bytes(got)


def test_the_page_is_a_picture_and_nothing_else(streamer):
    page = _fetch(streamer.url, 1000)
    assert b"/stream" in page
    assert b"img" in page


def test_the_stream_serves_jpeg_frames(streamer):
    body = _fetch(streamer.url + "stream", 400,
                  feed=lambda: streamer.frame(_frame(120)))
    assert b"--frame" in body
    assert b"Content-Type: image/jpeg" in body
    at = body.find(b"\xff\xd8")
    assert at != -1, "no JPEG start marker in the stream"


def test_nothing_is_encoded_for_an_audience_of_zero(streamer):
    assert not streamer.wants_frames()
    generation = streamer._server.generation
    # Feeding while nobody watches is the caller's mistake; even so the
    # pacing gate and the encoder must not build frames nobody reads.
    streamer.frame(_frame(120))
    time.sleep(0.2)
    assert streamer._server.generation - generation <= 1


def test_a_held_stream_freezes(streamer):
    streamer.frame(_frame(120))
    first = streamer.view._buf.copy()
    streamer.set_held(True)
    time.sleep(1.1 / 30.0)             # past the pacing gate
    streamer.frame(_frame(240))
    assert np.array_equal(streamer.view._buf, first)
    streamer.set_held(False)
    time.sleep(1.1 / 30.0)
    streamer.frame(_frame(240))
    assert not np.array_equal(streamer.view._buf, first)


def test_the_live_claim_cannot_outlive_the_feed(streamer):
    streamer.view.set_live(True)
    streamer.frame(_frame(120))
    assert streamer.view._live_lit
    streamer._last_fed -= 5.0
    streamer._check_stale()
    assert not streamer.view._live_lit


def test_the_stream_never_learns_a_screen_size(streamer):
    """Its viewers are on screens nobody here can measure, and
    unmeasured claims nothing -- the window may know its projector,
    the stream must never borrow that knowledge."""
    assert streamer.view._screen_mm_per_px is None
    assert streamer.view.size().toTuple() == STREAM_SIZE


def test_a_still_is_one_fresh_photograph(streamer):
    body = _fetch(streamer.url + "still.jpg", 100,
                  feed=lambda: streamer.frame(_frame(120)))
    assert body.startswith(b"\xff\xd8"), "not a JPEG"


def test_the_pointer_reaches_the_stream_immediately(streamer):
    streamer.frame(_frame(120))
    deadline = time.monotonic() + 2.0
    while streamer._server.generation == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    before = streamer._server.generation
    assert before, "the first frame never published"
    streamer.pointer(0.5, 0.5)
    deadline = time.monotonic() + 2.0
    while streamer._server.generation == before \
            and time.monotonic() < deadline:
        time.sleep(0.02)
    # Published without waiting for a camera frame: a held stream still
    # shows the ring the moment it is placed.
    assert streamer._server.generation > before
    assert streamer._animate.isActive(), "nothing animates the bloom"


def test_port_zero_reports_the_real_port(streamer):
    assert streamer.port != 0
    assert str(streamer.port) in streamer.url


def test_off_is_off(qapp):
    s = Streamer(0)
    port = s.port
    s.stop()
    with pytest.raises(Exception):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.0)
