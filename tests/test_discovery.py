"""Finding cameras, across three access models that share nothing.

Written against real hardware: a UVC camera with no controls at all, a
cheaper one with sixteen, a laptop camera that publishes two capture
nodes on one USB port, and a XIMEA that has no /dev/video node at all.
Each of those broke a different assumption.
"""
from darlaston.camera.discovery import Camera, choose, look


def _cam(key, **kw):
    return Camera(key=key, kind=kw.pop("kind", "v4l2"),
                  name=kw.pop("name", key), **kw)


def test_the_likeliest_answer_comes_first():
    """Ordered, never filtered. A rule confident enough to hide a device
    will one day hide the right one, and the recovery from that is the
    command-line flag this exists to remove."""
    infrared = _cam("ir", name="Integrated IR", width=640, height=360,
                    controls=1, uncompressed=True)
    laptop = _cam("built-in", name="Integrated Camera", width=1280,
                  height=720, controls=14, uncompressed=True)
    scope = _cam("scope", name="HD USB Camera", width=1920, height=1080,
                 controls=14, uncompressed=True)
    sealed = _cam("sealed", name="UVC Camera", width=1920, height=1080,
                  controls=0, uncompressed=False)

    from darlaston.camera.discovery import rank

    order = sorted([infrared, laptop, sealed, scope], key=rank, reverse=True)
    assert order[0] is scope, "the biggest controllable sensor should lead"
    # The sealed camera is the same size as the best one and loses only
    # the tie-breaks. This test used to require it *last*, below a
    # 640x360 infrared face-unlock sensor -- which is what the ranking
    # then did, and which would have opened somebody's face by default
    # on a laptop with a cheap eyepiece camera attached.
    assert order[1] is sealed, "a sealed 1080p camera ranked below a webcam"
    assert order[-1] is infrared, "the smallest sensor goes last"


def test_one_camera_is_not_a_choice():
    """Asking which camera to use when there is one is ceremony."""
    only = _cam("only")
    assert choose([only]) is only
    assert choose([]) is None


def test_several_cameras_is_a_question():
    """Guessing is how somebody photographs their own face for ten
    minutes before working out why."""
    assert choose([_cam("a"), _cam("b")]) is None


def test_what_was_chosen_before_wins():
    a, b = _cam("a"), _cam("b")
    assert choose([a, b], remembered="b") is b
    # And a remembered camera that is no longer attached does not stop
    # the question being asked again.
    assert choose([a, b], remembered="gone") is None


def test_two_nodes_on_one_port_never_share_a_key(tmp_path, monkeypatch):
    """A laptop's colour camera and its infrared face-unlock sensor share
    a USB port and differ only by interface. Keyed on the port alone,
    remembering one opens the other -- and an infrared sensor is a
    memorable thing to find yourself looking through.

    The nastier case is when sysfs will not say which interface a node
    belongs to. Falling back to the port alone made both collapse to one
    key, which is not instability, it is opening the wrong camera.
    """
    from darlaston.camera import v4l2

    # No hardware: the two things identity() reads are stubbed, so this
    # asks what the key-building does rather than what is plugged in.
    monkeypatch.setattr(v4l2, "_read_bus", lambda node: "usb-0000:00:14.0-8",
                        raising=False)

    interfaces = {"/dev/video0": "1.0", "/dev/video2": "1.2"}
    monkeypatch.setattr(v4l2, "_interface_of", lambda n: interfaces.get(n, ""))
    keys = [v4l2._key_from("usb-0000:00:14.0-8", n) for n in interfaces]
    assert len(set(keys)) == 2, f"one port, two cameras, same key: {keys}"

    # And when sysfs says nothing at all for either of them.
    monkeypatch.setattr(v4l2, "_interface_of", lambda n: "")
    blind = [v4l2._key_from("usb-0000:00:14.0-8", n) for n in interfaces]
    assert len(set(blind)) == 2, f"collapsed to one key: {blind}"


def test_a_camera_that_cannot_be_loaded_is_not_offered():
    """A vendor SDK that will not load is the ordinary fate of a vendor
    SDK -- libm3api here links against a libtiff this distribution has
    moved past. Offering a camera we cannot open would be worse than not
    listing it, but it must not raise either."""
    found = look()               # must not raise, whatever is installed
    assert all(c.key and c.kind and c.name for c in found)


# ---- what the rail is allowed to offer ------------------------------------

def test_a_control_the_camera_lacks_is_disabled_and_says_why(qapp):
    """A slider over a control that does not exist is worse than no
    slider: it moves, nothing happens, and the person reasonably decides
    the program is broken rather than the camera."""
    from darlaston.camera.base import CameraInfo, Resolution
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))

    sealed = CameraInfo(
        model="UVC Camera", serial="x",
        resolutions=(Resolution(0, 1920, 1080, 0.0),),
        max_bit_depth=8, bayer_pattern="",
        exposure_range_us=None, gain_range_pct=None)
    win._fit_controls_to(sealed)
    assert not win.exposure.isEnabled(), "offered an exposure it cannot set"
    assert not win.gain.isEnabled(), "offered a gain it cannot set"
    assert win.exposure.toolTip(), "disabled it without saying why"

    able = CameraInfo(
        model="HD USB Camera", serial="y",
        resolutions=(Resolution(0, 1920, 1080, 0.0),),
        max_bit_depth=8, bayer_pattern="",
        exposure_range_us=(100, 500000), gain_range_pct=(100, 200))
    win._fit_controls_to(able)
    assert win.exposure.isEnabled() and win.gain.isEnabled()
    assert not win.exposure.toolTip()
    # And the gain slider takes the device's range rather than asserting
    # one: our old floor of 100 was this camera's ceiling.
    assert win.gain.minimum() == 100 and win.gain.maximum() == 200
    win.shutdown()


def test_a_camera_whose_cable_moved_is_still_the_same_camera():
    """The port key is the best identity available and it breaks the
    moment somebody moves the cable. A device publishing exactly the same
    name, formats, sizes and controls, with no other candidate, is that
    camera -- and treating it as a stranger orphans everything written
    about it."""
    moved = _cam("v4l2:port-B", fingerprint="abc123")
    other = _cam("v4l2:port-C", fingerprint="different")

    # The remembered port is gone, but the fingerprint is unmistakable.
    assert choose([moved, other], remembered="v4l2:port-A",
                  fingerprint="abc123") is moved

    # Two of the same model, though, is a genuine question again: the
    # fingerprint identifies a model, not an instance.
    twin = _cam("v4l2:port-D", fingerprint="abc123")
    assert choose([moved, twin], remembered="v4l2:port-A",
                  fingerprint="abc123") is None

    # And a *stranger* in the remembered port does not win. A port is a
    # socket; this used to open whatever was in it.
    assert choose([moved, other], remembered="v4l2:port-C",
                  fingerprint="abc123") is moved


def test_a_different_camera_in_the_same_socket_is_a_different_camera():
    """The serial is a USB port, so plugging another camera into the
    socket where one used to live makes it arrive wearing the previous
    occupant's identity -- its name, and worse, its measured geometry and
    exposure response, which would then be applied to a camera they were
    never measured on.

    Nate hit exactly this: a cheap camera came up identified as the more
    expensive one.
    """
    import tempfile
    from dataclasses import replace
    from pathlib import Path

    from darlaston.session.model import Library

    lib = Library(path=Path(tempfile.mkdtemp()) / "library.json")

    first = lib.remember_camera("usb-port-1", "Expensive Camera",
                                fingerprint="aaa111")
    lib.cameras[first.serial] = replace(
        lib.cameras[first.serial], name="My good camera",
        geometry=[{"width": 1920, "height": 1080}])

    # Same socket, different device.
    second = lib.remember_camera("usb-port-1", "Cheap Camera",
                                 fingerprint="bbb222")
    assert second.model == "Cheap Camera"
    assert second.name != "My good camera", \
        "the new camera inherited the old one's name"
    assert not second.geometry, \
        "applied one camera's measurements to a different camera"

    # And the same device coming back is still itself.
    again = lib.remember_camera("usb-port-1", "Cheap Camera",
                                fingerprint="bbb222")
    assert again.model == "Cheap Camera"


def test_editing_a_camera_keeps_what_the_editor_cannot_show():
    """The editor showed six fields and rebuilt the profile from those
    six, so everything else was reset by construction -- the maker, the
    fingerprint that stops one camera inheriting another's identity, and
    the measured geometry and exposure response.

    Clicking Save, or merely selecting a different row, threw away a
    ten-minute profiling run. And the profiling dialog is opened from
    inside that same still-open dialog, so Save was the very next click.
    """
    from darlaston.session.model import CameraProfile
    from darlaston.ui.setup_ui import CameraEditor

    measured = CameraProfile(
        serial="usb-port-1", name="Scope camera", model="HD USB Camera",
        make="Acme Optics", fingerprint="abc123",
        geometry=[{"width": 1920, "height": 1080}],
        response=[[1, 8.0], [2, 16.0]], pixel_um=2.4)

    editor = CameraEditor()
    editor.load(measured)
    built = editor.build()

    assert built.make == "Acme Optics", "lost the manufacturer"
    assert built.fingerprint == "abc123", "lost the identity guard"
    assert built.geometry == measured.geometry, "lost the measured geometry"
    assert built.response == measured.response, "lost the exposure curve"
    assert built.model == "HD USB Camera"
    # And what the editor *does* own still comes from the editor.
    assert built.pixel_um == 2.4


def test_a_legacy_entry_with_no_fingerprint_is_still_guarded():
    """Every library written before fingerprints existed has none.
    Requiring both sides to have one let exactly the guarded case
    through: the old entry adopted the new camera's fingerprint and
    reported itself satisfied for ever after."""
    import tempfile
    from dataclasses import replace
    from pathlib import Path

    from darlaston.session.model import Library

    lib = Library(path=Path(tempfile.mkdtemp()) / "library.json")
    legacy = lib.remember_camera("usb-port-1", "Expensive Camera")
    assert not legacy.fingerprint, "not a legacy entry any more"
    lib.cameras[legacy.serial] = replace(
        lib.cameras[legacy.serial], name="My good camera",
        geometry=[{"width": 1920, "height": 1080}])

    arrived = lib.remember_camera("usb-port-1", "Cheap Camera",
                                  fingerprint="bbb222")
    assert arrived.name != "My good camera", \
        "a legacy entry handed its identity to a different camera"
    assert not arrived.geometry, \
        "and handed over calibration measured on another device"


def test_a_sealed_eyepiece_camera_outranks_a_face_unlock_sensor():
    """Nothing is filtered, so the order is the only thing deciding what
    opens by default. Ranking "offers uncompressed" above sensor size put
    a 640x360 infrared sensor ahead of a 1920x1080 eyepiece camera that
    only speaks MJPG -- and MJPG-only is the common cheap microscope
    camera, the device this exists to support."""
    from darlaston.camera.discovery import look

    infrared = _cam("ir", name="Integrated IR", width=640, height=360,
                    controls=1, uncompressed=True)
    sealed = _cam("scope", name="UVC Camera", width=1920, height=1080,
                  controls=0, uncompressed=False)
    from darlaston.camera.discovery import rank

    # The real ranking, not a copy of it living in the test. A test that
    # reimplements the sort passes whatever the real one does.
    order = sorted([infrared, sealed], key=rank, reverse=True)
    assert order[0] is sealed, "would have opened the face-unlock sensor"


def test_the_liveness_check_is_a_stat_and_not_an_enumeration():
    """The session asks once a second, for ever, on its supervisor thread.
    `look()` opens every node, walks its formats and issues up to 128
    QUERYCTRL ioctls per device -- including the one being streamed."""
    from darlaston.camera.discovery import look, presence_for

    calls = []
    here = presence_for(_cam("v4l2:port-A", node=__file__))
    gone = presence_for(_cam("v4l2:port-B", node="/dev/video-does-not-exist"))

    import darlaston.camera.discovery as D
    real, D.look = D.look, lambda: calls.append(1) or []
    try:
        assert here() is True
        assert gone() is False
    finally:
        D.look = real
    assert not calls, "the liveness check enumerated every camera"
    assert presence_for(_cam("mock", kind="mock")) is None


def test_a_backend_finds_its_camera_after_a_replug_renumbers_the_nodes():
    """A session rebuilds its backend from a record captured before the
    replug. Holding the old /dev/videoN meant reconnecting for ever to a
    device that is not there."""
    from darlaston.camera import v4l2

    moved = {"node": "/dev/video7", "card": "UVC Camera", "sizes": [(1920, 1080)],
             "formats": ["MJPG"], "raw": [], "usable": ["MJPG"],
             "key": "usb-0000:00:14.0-8", "fingerprint": "abc123",
             "controls": []}
    backend = v4l2.V4L2Backend(node="/dev/video3", key="usb-0000:00:14.0-8",
                               fingerprint="abc123")
    real_describe, real_enum = v4l2.describe, v4l2.enumerate_cameras
    v4l2.describe = lambda node: None                 # video3 is gone
    v4l2.enumerate_cameras = lambda: [moved]
    try:
        assert backend._locate() is moved
    finally:
        v4l2.describe, v4l2.enumerate_cameras = real_describe, real_enum


def test_a_backend_will_not_open_a_stranger_that_took_its_node():
    """The node is a first guess, not a promise. Something else answering
    at that number is not this camera."""
    from darlaston.camera import v4l2

    stranger = {"node": "/dev/video3", "card": "Other", "sizes": [(640, 480)],
                "formats": ["MJPG"], "raw": [], "usable": ["MJPG"],
                "key": "usb-0000:00:14.0-2", "fingerprint": "zzz",
                "controls": []}
    backend = v4l2.V4L2Backend(node="/dev/video3", key="usb-0000:00:14.0-8",
                               fingerprint="abc123")
    real_describe, real_enum, real_detail = (v4l2.describe,
                                             v4l2.enumerate_cameras, v4l2._detail)
    v4l2.describe = lambda node: dict(stranger)
    v4l2._detail = lambda found: found
    v4l2.enumerate_cameras = lambda: [stranger]
    try:
        assert backend._locate() is None
    finally:
        v4l2.describe = real_describe
        v4l2.enumerate_cameras = real_enum
        v4l2._detail = real_detail


def test_the_remembered_port_is_not_enough_on_its_own():
    """A port is a socket. A different camera in it must not simply be
    opened because the socket matches."""
    # Two attached, so the one-camera fallback does not mask the point:
    # with a single camera, opening it is right whatever it is.
    stranger = _cam("v4l2:port-A", fingerprint="different")
    spare = _cam("v4l2:port-Z", fingerprint="another")
    assert choose([stranger, spare], remembered="v4l2:port-A",
                  fingerprint="mine") is None
    # The same camera back in its own socket is still itself.
    mine = _cam("v4l2:port-A", fingerprint="mine")
    assert choose([mine, spare], remembered="v4l2:port-A",
                  fingerprint="mine") is mine


# ---- cameras the operator has passed over ---------------------------------

def test_a_passed_over_camera_is_not_opened_by_default():
    """A laptop's own webcam is on the bus at every launch, and with
    nothing else attached the likeliest-first rule opens it."""
    from darlaston.camera.discovery import offerable, rank

    webcam = _cam("v4l2:built-in", name="Integrated Camera",
                  width=1920, height=1080, fingerprint="w")
    scope = _cam("v4l2:port-8", name="UVC Camera",
                 width=1280, height=1024, fingerprint="s")
    order = sorted([webcam, scope], key=rank, reverse=True)
    assert order[0] is webcam, "the premise: the webcam ranks first"
    assert offerable(order, {"v4l2:built-in": "w"})[0] is scope


def test_passing_a_camera_over_never_hides_it():
    """`look` deliberately hides nothing -- a rule confident enough to hide
    a device will one day hide the right one. This is a preference about
    what opens, not a filter."""
    from darlaston.camera.discovery import is_ignored, offerable

    webcam = _cam("v4l2:built-in", fingerprint="w")
    scope = _cam("v4l2:port-8", fingerprint="s")
    seen = [webcam, scope]
    ignored = {"v4l2:built-in": "w"}
    assert is_ignored(webcam, ignored) and not is_ignored(scope, ignored)
    # An explicit choice still wins: `choose` never consults the list.
    assert choose(seen, remembered="v4l2:built-in", fingerprint="w") is webcam


def test_passing_over_the_only_camera_still_leaves_one():
    """Somebody can pass over every camera on the machine -- by ignoring
    the only one, or by unplugging the microscope camera and leaving the
    laptop's. A picture and a way to change it beats a blank window
    explaining that everything has been hidden."""
    from darlaston.camera.discovery import offerable

    only = _cam("v4l2:built-in", fingerprint="w")
    assert offerable([only], {"v4l2:built-in": "w"}) == [only]


def test_a_different_camera_in_a_passed_over_port_is_offered():
    """A key is a socket. Passing over a socket is not what anybody meant,
    and inheriting the verdict would hide a camera nobody has judged."""
    from darlaston.camera.discovery import is_ignored

    stranger = _cam("v4l2:built-in", fingerprint="something-else")
    assert not is_ignored(stranger, {"v4l2:built-in": "w"})
    # An entry written before fingerprints existed carries an empty one
    # and is still honoured, rather than being forgotten on upgrade.
    assert is_ignored(stranger, {"v4l2:built-in": ""})
