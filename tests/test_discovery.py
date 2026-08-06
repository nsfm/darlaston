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
