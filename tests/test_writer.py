"""The queue that lets the operator stop holding still.

Measured on Nate's 18-slice stack: 5.4 s per slice, 2.4 s of it the JPEG
sidecar. The point of every test here is that the shutter is free again
while that 2.4 s happens, and that nothing is lost in exchange.
"""
import threading
import time

import pytest

from darlaston.capture.writer import WriteQueue, default_budget

MB = 1024 * 1024


def _drained(q, timeout=5.0):
    assert q.drain(timeout), f"queue never drained, {q.depth} left"


def test_the_shutter_is_free_before_the_write_finishes():
    """The whole reason this exists. Submitting returns immediately; the
    work happens after, on somebody else's time."""
    q = WriteQueue(budget=100 * MB)
    release = threading.Event()
    done = []

    started = time.perf_counter()
    assert q.submit(10 * MB, lambda: (release.wait(2.0), done.append(1)))
    handed_back = time.perf_counter() - started

    assert handed_back < 0.1, f"submit blocked for {handed_back:.2f}s"
    assert not done, "the job ran on the caller's thread"
    release.set()
    _drained(q)
    assert done == [1]


def test_work_lands_in_the_order_it_was_taken():
    """A stack is a sequence through Z and slices are numbered as they
    land. Letting two writes race would number them by whichever finished
    first, which is a function of JPEG entropy, not of the focus knob."""
    q = WriteQueue(budget=100 * MB)
    order = []
    # Descending sleeps: submission order and completion order disagree
    # unless something enforces it.
    for i, nap in enumerate([0.05, 0.03, 0.01]):
        assert q.submit(MB, lambda i=i, nap=nap: (time.sleep(nap),
                                                  order.append(i)))
    _drained(q)
    assert order == [0, 1, 2]


def test_a_full_queue_refuses_rather_than_drops():
    """Backpressure travels backwards to the shutter. A live frame lost is
    invisible; a capture lost is gone."""
    q = WriteQueue(budget=25 * MB)
    hold = threading.Event()
    assert q.submit(10 * MB, lambda: hold.wait(5.0))
    assert q.submit(10 * MB, lambda: None)
    assert not q.submit(10 * MB, lambda: None), "took more than it can hold"
    assert not q.room_for(10 * MB)
    hold.set()
    _drained(q)
    assert q.room_for(10 * MB), "never recovered"


def test_one_frame_larger_than_the_whole_budget_still_gets_written():
    """The bound is about how many pile up, not about permission to work.
    A camera whose single frame exceeds the budget must still be able to
    take a photograph."""
    q = WriteQueue(budget=1 * MB)
    done = []
    assert q.submit(500 * MB, lambda: done.append(1)), \
        "refused the only frame it was asked to write"
    _drained(q)
    assert done == [1]


def test_the_count_cap_holds_even_when_the_frames_are_tiny():
    """Bytes are the real bound, but a 2 MP webcam could queue hundreds of
    small frames inside the budget and turn a crash into hundreds of lost
    captures instead of a few."""
    q = WriteQueue(budget=10_000 * MB)
    hold = threading.Event()
    taken = sum(q.submit(1, lambda: hold.wait(5.0)) for _ in range(200))
    assert taken == WriteQueue.MAX_PENDING, f"took {taken}"
    hold.set()
    _drained(q)


def test_a_job_that_raises_does_not_take_the_next_twenty_with_it():
    """The queue outlives any single capture."""
    q = WriteQueue(budget=100 * MB)
    done = []

    def boom():
        raise RuntimeError("no space left on device")

    assert q.submit(MB, boom)
    for i in range(3):
        assert q.submit(MB, lambda i=i: done.append(i))
    _drained(q)
    assert done == [0, 1, 2]
    assert q.idle


def test_memory_is_released_as_each_job_finishes_not_at_the_end():
    """The budget is resident memory. If accounting only settled when the
    queue emptied, a long stack would refuse captures it had room for."""
    q = WriteQueue(budget=30 * MB)
    gate = threading.Event()
    assert q.submit(10 * MB, lambda: gate.wait(5.0))
    assert q.submit(10 * MB, lambda: None)
    assert q.pending_bytes == 20 * MB
    gate.set()
    _drained(q)
    assert q.pending_bytes == 0


def test_draining_waits_for_photographs_that_exist_nowhere_else():
    """Called before the window closes. This is the difference between
    quitting and losing them."""
    q = WriteQueue(budget=100 * MB)
    written = []
    for i in range(4):
        assert q.submit(MB, lambda i=i: (time.sleep(0.02), written.append(i)))
    assert q.drain(5.0)
    assert written == [0, 1, 2, 3], "returned before the work was done"


def test_drain_reports_failure_rather_than_pretending():
    """A close guard that cannot tell a finished queue from a stuck one is
    worse than no guard: it would say the photographs are safe."""
    q = WriteQueue(budget=100 * MB)
    stuck = threading.Event()
    assert q.submit(MB, lambda: stuck.wait(10.0))
    assert not q.drain(0.2), "claimed a stuck queue had finished"
    stuck.set()
    _drained(q)


def test_stop_refuses_new_work_and_waits_for_what_it_took():
    q = WriteQueue(budget=100 * MB)
    done = []
    assert q.submit(MB, lambda: (time.sleep(0.05), done.append(1)))
    assert q.stop(timeout=5.0)
    assert done == [1], "dropped work it had already accepted"
    assert not q.submit(MB, lambda: done.append(2)), "took work after stop"


def test_nothing_is_started_until_something_is_submitted():
    """One thread per constructed object is one thread per test."""
    before = {t.name for t in threading.enumerate()}
    q = WriteQueue(budget=MB, name="unstarted-writer")
    assert "unstarted-writer" not in {t.name for t in threading.enumerate()} - before
    assert q.idle


def test_the_budget_is_a_share_of_the_machine_not_a_constant():
    """A number tuned on this laptop would be wrong on a four-gigabyte one,
    and the failure mode there is swapping mid-stack."""
    b = default_budget()
    assert 256 * MB <= b <= 2048 * MB
    # Six 20 MP Bayer frames is the floor that matters: fewer than that and
    # a burst of stack slices stalls on the queue rather than on the disk.
    assert b >= 6 * 40 * MB or b == 256 * MB


@pytest.mark.parametrize("n", [1, 5, WriteQueue.MAX_PENDING])
def test_the_high_water_mark_says_whether_the_queue_was_ever_the_problem(n):
    q = WriteQueue(budget=1000 * MB)
    gate = threading.Event()
    q.submit(MB, lambda: gate.wait(5.0))
    for _ in range(n - 1):
        q.submit(MB, lambda: None)
    gate.set()
    _drained(q)
    assert q.high_water == n
    assert q.idle


# ---- the watermark ---------------------------------------------------------

def test_the_watermark_warns_before_the_shutter_starts_refusing():
    """Not everybody has 64 GB. A delay is fine as long as it is broadcast,
    and the moment to broadcast it is before a capture is refused, not
    after -- an operator whose shutter does nothing has been told the wrong
    thing by silence."""
    q = WriteQueue(budget=100 * MB)
    hold = threading.Event()
    assert not q.under_pressure, "crying wolf on an empty queue"

    assert q.submit(50 * MB, lambda: hold.wait(5.0))
    assert not q.under_pressure, "half full is working, not struggling"

    assert q.submit(30 * MB, lambda: hold.wait(5.0))
    assert q.under_pressure, "past the watermark and said nothing"
    # Still accepting: the warning comes first, the refusal after.
    assert q.room_for(15 * MB)

    hold.set()
    _drained(q)
    assert not q.under_pressure, "never stopped complaining"


def test_pressure_is_reported_by_whichever_bound_bites_first():
    """A small-sensor camera runs out of slots long before bytes."""
    q = WriteQueue(budget=10_000 * MB)
    hold = threading.Event()
    for _ in range(int(WriteQueue.MAX_PENDING * WriteQueue.PRESSURE)):
        q.submit(1, lambda: hold.wait(5.0))
    assert q.pending_bytes < q.budget * 0.01, "the byte bound is nowhere near"
    assert q.under_pressure, "full of slots and calling itself idle"
    assert q.fullness >= WriteQueue.PRESSURE
    hold.set()
    _drained(q)


def test_a_cramped_machine_gets_a_share_not_the_same_flat_number():
    """A flat 256 MB floor is a sixteenth of a roomy laptop and a quarter
    of a cramped one, and on the cramped one that is a swap storm."""
    import darlaston.capture.writer as w

    def budget_when(spare_gb):
        real = w._available_bytes
        w._available_bytes = lambda: int(spare_gb * 1024 * MB)
        try:
            return w.default_budget()
        finally:
            w._available_bytes = real

    roomy, cramped = budget_when(32), budget_when(0.5)
    assert cramped < 256 * MB, "took the flat floor on a half-gigabyte machine"
    assert cramped <= 0.5 * 1024 * MB / 4, "took over a quarter of what is free"
    assert roomy > cramped
    assert budget_when(64) <= 2048 * MB, "no ceiling"
    # The quarter rule holds all the way down, which is the point: the
    # failure mode being avoided is swapping, and swapping is a share
    # question rather than an absolute one.
    for gb in (0.25, 0.5, 1, 2, 8, 32, 128):
        assert budget_when(gb) <= gb * 1024 * MB / 4, f"{gb} GB machine"


def test_a_dozen_is_the_depth_because_it_drains_while_it_fills():
    """Depth absorbs a burst of racking; it does not hold a session. The
    queue is writing the whole time it is filling, so a deeper one only
    hides a disk that cannot keep up."""
    assert WriteQueue.MAX_PENDING == 12
    # And the two bounds have to be able to bite in either order: on a
    # 20 MP camera the bytes run out first, on a webcam the slots do.
    q = WriteQueue(budget=10_000 * MB)
    assert q.room_for(40 * MB * 3), "a single 20 MP frame does not fit"


def test_the_queue_measures_its_own_writes_so_a_bar_can_fill():
    """A progress bar without a measured duration can only pulse. What a
    write costs here is the sensor's size, the disk, and how busy the live
    preview is, and no constant knows any of those."""
    q = WriteQueue(budget=100 * MB)
    assert q.typical_write == 0.0, "claimed to know before measuring"
    assert q.progress() == (0, 0.0)

    for _ in range(3):
        assert q.submit(MB, lambda: time.sleep(0.08))
    _drained(q)

    assert 0.04 < q.typical_write < 0.4, f"measured {q.typical_write:.3f}s"
    assert q.progress() == (0, 0.0), "still claims work when idle"


def test_the_fill_never_sits_at_full_waiting():
    """A bar that fills and then stops has told the operator the write
    finished. It saturates short of the end instead."""
    q = WriteQueue(budget=100 * MB)
    assert q.submit(MB, lambda: time.sleep(0.05))
    _drained(q)
    assert q.typical_write > 0

    gate = threading.Event()
    assert q.submit(MB, lambda: gate.wait(5.0))
    time.sleep(0.4)                       # far past the typical write
    depth, fraction = q.progress()
    assert depth == 1
    assert fraction <= 0.95, f"filled to {fraction}"
    gate.set()
    _drained(q)


# ---- the gauge -------------------------------------------------------------

def test_the_gauge_is_absent_until_there_is_something_to_say(qapp):
    """It lives under the stack window, and the stack window is what the
    operator is looking at while racking. Furniture that is always there
    is furniture that gets looked at."""
    from darlaston.ui.widgets import SaveGauge

    g = SaveGauge()
    assert not g.isVisible()
    g.set_progress(2, 0.4)
    assert g.isVisibleTo(g.parentWidget() or g)
    g.set_progress(0, 0.0)
    assert not g.isVisible(), "stayed up with nothing left to write"


def test_the_gauge_paints_at_every_depth_without_falling_over(qapp):
    """Including past what cells can legibly say."""
    from PySide6 import QtGui
    from darlaston.ui.widgets import SaveGauge

    g = SaveGauge()
    g.resize(160, SaveGauge.HEIGHT)
    for depth in (1, 2, 8, WriteQueue.MAX_PENDING, 99):
        for fraction in (0.0, 0.5, 0.95):
            g.set_progress(depth, fraction)
            img = QtGui.QImage(160, SaveGauge.HEIGHT,
                               QtGui.QImage.Format.Format_RGB32)
            img.fill(QtGui.QColor("#0b0d0b"))
            g.render(img)                       # must not raise
    assert g.toolTip(), "no way to ask what it is"
