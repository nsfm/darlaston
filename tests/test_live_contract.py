"""Tests for the threading contract.

These are the tests worth having on day one. If LatestFrame's drop semantics
are wrong, or buffers leak, nothing built on top can be correct -- and both
failures are the kind that look fine in a demo and rot under load.
"""
import threading
import time

import numpy as np
import pytest

from photomicrography.camera.buffers import BufferPool, Frame
from photomicrography.live.cell import LatestFrame


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
    from photomicrography.camera.mock import MockCamera
    from photomicrography.live.pipeline import LivePipeline

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
    from photomicrography.camera.mock import MockCamera
    from photomicrography.live.focus import Illumination, DEFAULTS, measure
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
