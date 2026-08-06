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

    order = sorted([infrared, laptop, sealed, scope],
                   key=lambda c: (c.uncompressed, c.controls > 0,
                                  c.width * c.height), reverse=True)
    assert order[0] is scope, "the biggest controllable sensor should lead"
    assert sealed in order, "a sealed camera is still offered, just later"
    assert order[-1] is sealed, "and it goes last, having neither"


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

    # And the port still wins when it is there.
    assert choose([moved, other], remembered="v4l2:port-C",
                  fingerprint="abc123") is other


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
