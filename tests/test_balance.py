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


def test_picking_again_on_a_corrected_image_changes_nothing():
    """A pick is relative -- it is taken off the image as displayed, which
    already carries whatever is in force. So the second press on an
    already-neutral region has to be a no-op, or the control would drift
    every time somebody checked their work."""
    blue = _patch(b=200, g=120, r=90)
    first = B.from_region(blue)
    fixed = B.applied(blue, B.lut(first))
    assert B.from_region(fixed) == pytest.approx(B.UNITY, abs=0.02)
    assert B.combine(first, B.from_region(fixed)) == pytest.approx(first, rel=0.02)


def test_two_picks_compound_the_way_a_hand_expects():
    """Each press moves further from where it started, rather than
    replacing the last one."""
    a = B.combine(B.UNITY, (2.0, 1.0, 0.5))
    b = B.combine(a, (1.5, 1.0, 0.5))
    assert b[0] == pytest.approx(3.0)
    assert b[2] == pytest.approx(0.25)


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
