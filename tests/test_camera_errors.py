"""SDK failures: reading them, and surviving them.

The bug these cover was found on real hardware — the tenth tile of a mosaic
failed, the operator saw "-2147417825" and nothing else, and the camera was
left stopped in raw trigger mode so every later capture failed too.
"""
import time

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


def test_v4l2_backend_reports_no_raw_and_streams_colour(tmp_path):
    """The USB-camera backend's contract, without needing a USB camera:
    it must describe itself honestly (8-bit, no CFA, no raw) so the
    capture path writes a linear DNG instead of claiming a Bayer
    pattern that does not exist."""
    import numpy as np
    from darlaston.camera import v4l2

    fake = {"node": "/dev/videoX", "card": "Test Scope Cam",
            "driver": "uvcvideo", "formats": ["MJPG", "YUYV"],
            "sizes": [(1280, 720), (640, 480)], "raw": []}

    class FakeCapture:
        def __init__(self, *a, **kw):
            self._open = True

        def isOpened(self):
            return self._open

        def set(self, prop, value):
            return True

        def get(self, prop):
            return 0.0

        def read(self):
            return True, np.full((720, 1280, 3), 40, np.uint8)

        def release(self):
            self._open = False

    monkey = v4l2.cv2.VideoCapture
    v4l2.cv2.VideoCapture = FakeCapture
    try:
        cam = v4l2.V4L2Backend(node="/dev/videoX")
        v4l2.describe = lambda node: fake
        info = cam.open()
        assert info.max_bit_depth == 8
        assert not info.is_colour, "a decoded camera declares no CFA pattern"
        assert not info.raw_capable
        assert info.resolutions[0].pixel_um == 0.0, \
            "unknown pitch must stay unknown, so no scale bar is invented"

        seen = []
        cam.start_stream(lambda f: (seen.append(f.data.shape), f.release()))
        for _ in range(50):
            if seen:
                break
            time.sleep(0.02)
        cam.stop_stream()
        assert seen and seen[0] == (720, 1280, 3)

        frame = cam.grab_raw()
        assert frame.data.ndim == 3, "no raw available; colour is the truth"
        cam.close()
    finally:
        v4l2.cv2.VideoCapture = monkey


def test_problems_say_what_to_do_and_survive_to_the_window():
    """Every failure a first-time user can hit must carry next steps, and
    they must reach the status the window renders -- an explanation that
    only exists in a traceback reaches nobody, because nobody starts a
    GUI from a terminal."""
    from darlaston.camera.errors import (CameraBusy, CameraProblem,
                                         NoCameraFound, PermissionDenied,
                                         SdkMissing, SdkTooOld)

    problems = [SdkMissing(), SdkTooOld("/x/libtoupcam.so", ("Toupcam_a",)),
                NoCameraFound(), CameraBusy(), PermissionDenied()]
    kinds = set()
    for p in problems:
        assert isinstance(p, CameraProblem)
        assert p.heading and not p.heading.endswith("."), p.heading
        assert p.steps, f"{type(p).__name__} tells nobody what to do"
        # Deliberately not asserting terminal punctuation: a step that is
        # a command to copy must not end in a full stop, because somebody
        # will paste it.
        assert all(len(s.strip()) >= 10 for s in p.steps), p.steps
        assert str(p), "must still be a usable exception message"
        kinds.add(p.kind)
    assert len(kinds) == len(problems), "kinds must distinguish the cases"


def test_session_carries_a_problem_through_to_status():
    from darlaston.camera.base import CameraState
    from darlaston.camera.errors import SdkMissing
    from darlaston.camera.session import CameraSession

    published = []

    def boom():
        raise SdkMissing()

    session = CameraSession(boom, published.append, lambda f: None,
                            is_present=lambda: True)
    session.RETRY_BACKOFF = (0.0,)
    session._try_connect_once = None
    try:
        session._try_connect()
    except SdkMissing as exc:
        session._fail(exc)

    faults = [s for s in published if s.state is CameraState.ERROR]
    assert faults, "the failure must be published, not just raised"
    status = faults[-1]
    assert status.kind == "sdk-missing"
    assert status.steps and "--usb" in " ".join(status.steps)
    assert "traceback" not in status.detail.lower()


def test_sdk_install_refuses_an_archive_that_escapes_its_directory(tmp_path):
    """Unpacking somebody else's zip is the one place here that handles a
    file we did not write. A member with `../` in its path would be
    written outside the install directory, so it is refused rather than
    extracted."""
    import zipfile

    from darlaston.camera.sdk_install import _safe_extract

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("linux/x64/libtoupcam.so", b"fine")
        zf.writestr("../../escaped.txt", b"not fine")

    into = tmp_path / "into"
    into.mkdir()
    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ValueError, match="outside"):
            _safe_extract(zf, into)
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_sdk_sources_never_offer_a_link_they_cannot_serve():
    """Every entry either has a direct URL we fetch, or a human download
    page — never a guessed archive link. Sending someone to a 404 is
    worse than sending them to a page that works."""
    from darlaston.camera.sdk_install import SOURCES, find
    from darlaston.camera.toupcam import BRANDS

    known = {b[0] for b in BRANDS}
    for source in SOURCES:
        assert source.brand in known, f"{source.brand} is not a known brand"
        assert source.page.startswith("https://"), source.page
        assert find(source.brand) is source
        if source.automatic:
            assert source.url.startswith("https://"), source.url
            assert source.approx_mb > 0, "an automatic fetch states its size"


# ---- running somewhere that is not Linux -----------------------------------

def test_library_naming_follows_the_platform(monkeypatch):
    """BRANDS records the Linux filename because that is where this was
    written. The vendors ship one archive holding every platform and only the
    convention changes -- Windows drops the `lib` prefix."""
    from darlaston.camera import toupcam

    monkeypatch.setattr(toupcam.sys, "platform", "darwin")
    assert toupcam.library_name("libtoupcam.so") == "libtoupcam.dylib"
    assert toupcam.library_name("libmeadecam.so") == "libmeadecam.dylib"

    monkeypatch.setattr(toupcam.sys, "platform", "win32")
    assert toupcam.library_name("libtoupcam.so") == "toupcam.dll"

    monkeypatch.setattr(toupcam.sys, "platform", "linux")
    assert toupcam.library_name("libtoupcam.so") == "libtoupcam.so"


def test_library_directories_match_the_shipped_archive(monkeypatch):
    """Read off ToupTek 59.30594's actual tree, not assumed: mac/ is a single
    universal binary, and arm64 Linux is split into glibc and musl builds --
    which is why that one returns two candidates."""
    from darlaston.camera import toupcam

    monkeypatch.setattr(toupcam.sys, "platform", "darwin")
    assert toupcam.library_dirs() == ("mac",)

    monkeypatch.setattr(toupcam.sys, "platform", "linux")
    monkeypatch.setattr(toupcam.platform, "machine", lambda: "x86_64")
    assert toupcam.library_dirs() == ("linux/x64",)
    monkeypatch.setattr(toupcam.platform, "machine", lambda: "aarch64")
    assert toupcam.library_dirs() == ("linux/arm64/glibc", "linux/arm64/musl")
    monkeypatch.setattr(toupcam.platform, "machine", lambda: "armv7l")
    assert toupcam.library_dirs()[0] == "linux/armhf"

    monkeypatch.setattr(toupcam.sys, "platform", "win32")
    monkeypatch.setattr(toupcam.platform, "machine", lambda: "AMD64")
    assert toupcam.library_dirs()[0] == "win/x64"


def test_the_loader_finds_a_mac_sdk(monkeypatch, tmp_path):
    """The whole point: an SDK unpacked on a Mac must be found where the
    vendor actually puts it, without the loader having been told."""
    from darlaston.camera import toupcam

    root = tmp_path / "sdk-59"
    (root / "mac").mkdir(parents=True)
    (root / "python").mkdir()
    (root / "python" / "toupcam.py").write_text("# binding\n")
    lib = root / "mac" / "libtoupcam.dylib"
    lib.write_bytes(b"not a real dylib")

    monkeypatch.setattr(toupcam.sys, "platform", "darwin")
    seen = {}

    class FakeCDLL:
        def __init__(self, path, mode=0):
            seen["path"] = path

        def __getattr__(self, name):
            return object()          # every REQUIRED symbol present

    monkeypatch.setattr(toupcam.ctypes, "CDLL", FakeCDLL)
    monkeypatch.setattr(toupcam, "_Vendor",
                        lambda mod, module, cls, prefix: "loaded")
    monkeypatch.setattr(toupcam, "__import__", lambda n: object(), raising=False)
    import builtins
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: object() if n == "toupcam"
                        else __import__(n, *a, **k))

    got = toupcam._load_brand(root, "toupcam", "libtoupcam.so",
                              "Toupcam", "TOUPCAM")
    assert got == "loaded"
    assert seen["path"] == str(lib), "must load the mac build, not guess"


def test_presence_unknown_means_try_rather_than_refuse(monkeypatch):
    """This gates whether the session opens a camera at all. Answering "no"
    off Linux -- where there is no sysfs to read -- left macOS sitting at
    "waiting for a camera" for ever with one plugged in."""
    from darlaston.camera import usb

    monkeypatch.setattr(usb.sys, "platform", "darwin")
    assert usb.present() is True
    assert usb.probe().port is None, "still honest that it cannot tell"

    monkeypatch.setattr(usb.sys, "platform", "win32")
    assert usb.present() is True


def test_sdk_verification_accepts_each_platform_build(monkeypatch, tmp_path):
    """The installer proved the download by finding exactly linux/x64. It has
    to find mac/ and win/x64 the same way, and still refuse android/x64 --
    which sorts first and is a valid ELF for the wrong operating system."""
    from darlaston.camera import sdk_install, toupcam

    root = tmp_path / "toupcam" / "sdk-59"
    for sub, name in (("android/x64", "libtoupcam.so"),
                      ("linux/x64", "libtoupcam.so"),
                      ("mac", "libtoupcam.dylib"),
                      ("win/x64", "toupcam.dll")):
        (root / sub).mkdir(parents=True, exist_ok=True)
        (root / sub / name).write_bytes(b"stub")
    (root / "python").mkdir()
    (root / "python" / "toupcam.py").write_text("# binding\n")

    class FakeCDLL:
        def __init__(self, path, mode=0):
            self.path = path

        def __getattr__(self, name):
            return object()

    monkeypatch.setattr(sdk_install.ctypes if hasattr(sdk_install, "ctypes")
                        else __import__("ctypes"), "CDLL", FakeCDLL,
                        raising=False)
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", FakeCDLL)

    for plat, machine, expect in (("linux", "x86_64", "linux/x64"),
                                  ("darwin", "arm64", "mac"),
                                  ("win32", "AMD64", "win/x64")):
        monkeypatch.setattr(toupcam.sys, "platform", plat)
        monkeypatch.setattr(toupcam.platform, "machine", lambda m=machine: m)
        found = sdk_install._verify(tmp_path / "toupcam", "toupcam")
        assert found == root, f"{plat}: found {found}"
        # And never the android build, whatever the platform.
        assert "android" not in str(found)
