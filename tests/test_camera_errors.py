"""SDK failures: reading them, and surviving them.

The bug these cover was found on real hardware — the tenth tile of a mosaic
failed, the operator saw "-2147417825" and nothing else, and the camera was
left stopped in raw trigger mode so every later capture failed too.
"""
import pytest

from darlaston.camera.errors import explain, hresult_of, is_retryable


class LinuxStyle(Exception):
    """The vendor's non-Windows HRESULTException: sets .hr, no message.

    BaseException keeps constructor args, which is why str() of one of these
    comes out as the raw signed integer.
    """
    def __init__(self, hr):
        self.hr = hr


def test_reads_the_code_from_hr():
    assert hresult_of(LinuxStyle(0x8001011F)) == 0x8001011F


def test_reads_a_signed_code():
    """The value the operator actually saw. Signed, so a naive int() of the
    message is negative and no hex match can ever find it."""
    exc = LinuxStyle(-2147417825)
    assert hresult_of(exc) == 0x8001011F
    assert "did not deliver the frame in time" in explain(exc)


def test_reads_the_code_from_args_alone():
    """Belt and braces: even without .hr, a lone integer argument is the
    code — which is exactly what str() was rendering."""
    class Bare(Exception):
        pass
    assert hresult_of(Bare(-2147024865)) == 0x8007001F


def test_ordinary_exceptions_are_left_alone():
    assert hresult_of(ValueError("nope")) is None
    assert hresult_of(RuntimeError()) is None
    assert explain(ValueError("the disk is full")) == "the disk is full"


def test_messages_say_what_to_do_and_name_the_code():
    text = explain(LinuxStyle(0x80070005))
    assert "udev" in text, "permission errors must name the actual fix"
    assert "E_ACCESSDENIED" in text and "0x80070005" in text


def test_only_transient_failures_retry():
    assert is_retryable(LinuxStyle(0x8001011F))     # timeout
    assert is_retryable(LinuxStyle(0x8000000A))     # pending
    assert not is_retryable(LinuxStyle(0x80070005))  # permission
    assert not is_retryable(LinuxStyle(0x8000FFFF))  # wrong state
    assert not is_retryable(ValueError("nope"))


# ---- the state machine around a failed pull ---------------------------------

class FakeBackend:
    """Exercises ToupcamBackend.grab_raw's orchestration with no SDK."""

    from darlaston.camera.toupcam import ToupcamBackend
    grab_raw = ToupcamBackend.grab_raw
    _quiesce = ToupcamBackend._quiesce

    def __init__(self, failures=()):
        self._failures = list(failures)
        self._pool = object()          # "streaming"
        self._on_frame = lambda f: None
        self._cam = self
        self.log = []
        self.pulls = 0

    def Stop(self):
        self.log.append("stop")

    def stop_stream(self):
        self.log.append("stop_stream")
        self._pool = None

    def start_stream(self, on_frame):
        self.log.append("start_stream")
        self._pool = object()

    def _pull_full(self, timeout_ms):
        self.pulls += 1
        self.log.append("pull")
        if self._failures:
            raise self._failures.pop(0)
        return "frame"


def test_preview_restarts_after_a_failed_capture():
    """The actual damage: without this, one timeout killed every later
    capture in the session."""
    b = FakeBackend(failures=[LinuxStyle(0x8000FFFF)])   # not retryable
    with pytest.raises(Exception):
        b.grab_raw()
    assert "start_stream" in b.log, "the preview must come back regardless"
    assert b.log.index("stop") < b.log.index("start_stream"), \
        "the camera must be stopped before the mode is re-stated"
    assert b.pulls == 1, "a state error must not be retried"


def test_transient_failure_is_retried_once():
    b = FakeBackend(failures=[LinuxStyle(0x8001011F)])
    assert b.grab_raw() == "frame"
    assert b.pulls == 2
    assert b.log.count("start_stream") == 1


def test_a_second_transient_failure_gives_up():
    b = FakeBackend(failures=[LinuxStyle(0x8001011F), LinuxStyle(0x8001011F)])
    with pytest.raises(Exception):
        b.grab_raw()
    assert b.pulls == 2, "one retry, not a loop"
    assert "start_stream" in b.log


def test_a_still_camera_is_not_restarted():
    b = FakeBackend()
    b._pool = None                     # was not streaming
    b._on_frame = None
    assert b.grab_raw() == "frame"
    assert "start_stream" not in b.log
