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
                 "a bug in darlaston's mode handling — please report it."),
    0x80004001: ("E_NOTIMPL",
                 "This camera model does not support that feature."),
    0x80070005: ("E_ACCESSDENIED",
                 "No permission to open the camera. Install the SDK's udev "
                 "rules file, then unplug and replug the camera."),
    0x8007000E: ("E_OUTOFMEMORY", "The system is out of memory."),
    0x80070057: ("E_INVALIDARG",
                 "The camera rejected an argument. This is a bug in "
                 "darlaston — please report it."),
    0x80004003: ("E_POINTER",
                 "The camera was handed a null pointer. This is a bug in "
                 "darlaston — please report it."),
    0x80004005: ("E_FAIL", "The camera reported a generic failure."),
    0x8001010E: ("E_WRONG_THREAD",
                 "The camera was called from the wrong thread. This is a bug "
                 "in darlaston — please report it."),
    0x8007001F: ("E_GEN_FAILURE",
                 "The camera stopped responding. This is almost always "
                 "physical: try a different USB 3 port, reseat the cable, or "
                 "avoid a hub. The link can also drop under a long session."),
    0x800700AA: ("E_BUSY",
                 "The camera is already in use — another copy of darlaston "
                 "or ToupLite may have it open."),
    0x8000000A: ("E_PENDING", "The camera has no data ready yet."),
    0x8001011F: ("E_TIMEOUT",
                 "The camera did not deliver the frame in time. If the "
                 "exposure is long this may just need more patience; if it "
                 "repeats, the link may have dropped to USB 2.0 — usually "
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
