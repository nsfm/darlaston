"""Making the SDK's failures legible.

On Windows the vendor binding builds its `HRESULTException` from
`ctypes.FormatError`, so it carries a message. On Linux the same class is a
bare `Exception` that stores only `self.hr` -- and because `BaseException`
keeps its constructor arguments, `str(exc)` comes out as the raw *signed*
32-bit value. That is how a real capture failure reached the operator as
"-2147417825" and nothing else.

Worse, the number is signed and the SDK documents its codes in hex, so no
amount of string matching on the message will ever recognise one. The fix is
to read `.hr` and decode it.

Every message here says what to do, not merely what broke -- these are the
words a person reads at midnight with a diatom under the objective.
"""
from __future__ import annotations

#: HRESULT -> (short name, what to do about it). Codes from the SDK header.
_CODES: dict[int, tuple[str, str]] = {
    0x8000FFFF: ("E_UNEXPECTED",
                 "The camera refused a setting while it was running. This is "
                 "a bug in darlaston's mode handling. Please report it."),
    0x80004001: ("E_NOTIMPL",
                 "This camera model does not support that feature."),
    0x80070005: ("E_ACCESSDENIED",
                 "No permission to open the camera. Install the SDK's udev "
                 "rules file, then unplug and replug the camera."),
    0x8007000E: ("E_OUTOFMEMORY", "The system is out of memory."),
    0x80070057: ("E_INVALIDARG",
                 "The camera rejected an argument. This is a bug in "
                 "darlaston. Please report it."),
    0x80004003: ("E_POINTER",
                 "The camera was handed a null pointer. This is a bug in "
                 "darlaston. Please report it."),
    0x80004005: ("E_FAIL", "The camera reported a generic failure."),
    0x8001010E: ("E_WRONG_THREAD",
                 "The camera was called from the wrong thread. This is a bug "
                 "in darlaston. Please report it."),
    0x8007001F: ("E_GEN_FAILURE",
                 "The camera stopped responding. This is almost always "
                 "physical: try a different USB 3 port, reseat the cable, or "
                 "avoid a hub. The link can also drop under a long session."),
    0x800700AA: ("E_BUSY",
                 "The camera is already in use. Another copy of darlaston "
                 "or ToupLite may have it open."),
    0x8000000A: ("E_PENDING", "The camera has no data ready yet."),
    0x8001011F: ("E_TIMEOUT",
                 "The camera did not deliver the frame in time. If the "
                 "exposure is long this may just need more patience; if it "
                 "repeats, the link may have dropped to USB 2.0, usually "
                 "the cable."),
    0x80072743: ("E_UNREACH", "The camera is unreachable over the network."),
    0x800704C7: ("E_CANCELLED", "The operation was cancelled."),
}

#: Failures worth one automatic retry: transient by nature, and a capture is
#: expensive to lose. Everything else is a state or configuration problem that
#: retrying would only repeat.
RETRYABLE = frozenset({0x8001011F, 0x8000000A})


def hresult_of(exc: BaseException) -> int | None:
    """The SDK's error code, as an unsigned 32-bit value, or None.

    Reads `.hr` rather than parsing the message: on Linux there is no message,
    and the numeric form is signed, which no hex-string match would catch.
    """
    hr = getattr(exc, "hr", None)
    if hr is None:
        args = getattr(exc, "args", ())
        if len(args) == 1 and isinstance(args[0], int):
            hr = args[0]
    if hr is None:
        return None
    return int(hr) & 0xFFFFFFFF


def is_retryable(exc: BaseException) -> bool:
    hr = hresult_of(exc)
    return hr is not None and hr in RETRYABLE


def explain(exc: BaseException) -> str:
    """A sentence the operator can act on, plus the code for a bug report."""
    hr = hresult_of(exc)
    if hr is None:
        return str(exc) or exc.__class__.__name__
    name, advice = _CODES.get(hr, ("unknown", "The camera reported an error."))
    return f"{advice}  [{name} 0x{hr:08X}]"


# ---- problems a person has to do something about ---------------------------

class CameraProblem(Exception):
    """A failure stated as a person would need it stated.

    Three fields, because three questions get asked in order: what is
    wrong, why, and what do I do now. The last one is why this class
    exists at all -- the previous code matched substrings against
    exception text to guess the same thing, which is a heuristic sitting
    where a fact belongs.

    Nobody starts a GUI from a terminal, so none of this may end up only
    in a traceback. `kind` is the machine-readable half so the window can
    offer the right button rather than parsing the prose.
    """

    kind = "unknown"

    def __init__(self, heading: str, detail: str = "",
                 steps: tuple[str, ...] = ()) -> None:
        super().__init__(heading if not detail else f"{heading}: {detail}")
        self.heading = heading
        self.detail = detail
        self.steps = tuple(steps)


class SdkMissing(CameraProblem):
    """No vendor SDK is installed, so no camera of that family can open."""

    kind = "sdk-missing"

    def __init__(self, brands: tuple[str, ...] = ()) -> None:
        super().__init__(
            "No camera SDK installed",
            "Your camera needs a driver made by the company that built "
            "it. This only has to be done once.",
            (
                "Click Install the camera driver below. darlaston will "
                "download it and check that it works.",
                "Or download it yourself from your camera maker's website "
                "and unzip it into ~/toup/.",
                "If you have an ordinary USB microscope camera instead, "
                "start darlaston with --usb. Those need no driver, but "
                "they cannot produce raw files.",
            ))


class SdkTooOld(CameraProblem):
    """A library is installed but predates functions darlaston needs."""

    kind = "sdk-old"

    def __init__(self, path: str, missing: tuple[str, ...]) -> None:
        super().__init__(
            "The installed camera SDK is too old",
            f"{path} is missing {', '.join(missing)}. ToupLite bundles a "
            "library from 2021 under exactly the same filename as a current "
            "SDK, so having both installed is easy to do by accident.",
            ("Download a current SDK from your camera's manufacturer.",
             "Set TOUPCAM_SDK to point at it, so the old copy is not found "
             "first.",
             "Restart darlaston."))


class NoCameraFound(CameraProblem):
    """The SDK loaded and reported no devices."""

    kind = "no-camera"

    def __init__(self, what: str = "camera") -> None:
        super().__init__(
            f"No {what} found",
            "The software is working, but nothing answered on the bus.",
            ("Check that the camera is plugged in.",
             "Try a different USB port, and a different cable. A marginal "
             "cable does not fail cleanly, it renegotiates to USB 2.0 and "
             "everything simply becomes slow.",
             "Some cameras take a few seconds after being plugged in."))


class CameraBusy(CameraProblem):
    """Something else already owns the device."""

    kind = "busy"

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "Another program is using the camera",
            detail or "The device opened, but something else holds it.",
            ("Close ToupLite, ToupView, AmScope, or any other capture "
             "program.",
             "On a laptop, a video-call application may have claimed it.",
             "darlaston will connect on its own once the camera is free."))


class PermissionDenied(CameraProblem):
    """Present on the bus, but not openable by this user."""

    kind = "permission"

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "No permission to open the camera",
            detail or "The camera is on the bus but this user cannot open "
            "it. Cameras need a udev rule to be usable without root.",
            ("Copy the SDK's udev rules file (99-toupcam.rules or your "
             "brand's equivalent) into /etc/udev/rules.d/.",
             "Run: sudo udevadm control --reload-rules",
             "Unplug the camera and plug it back in."))
