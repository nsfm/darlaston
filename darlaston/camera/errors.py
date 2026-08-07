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

from ..i18n import N_, _, n_

#: HRESULT -> (short name, what to do about it). Codes from the SDK header.
_CODES: dict[int, str] = {
    0x8000FFFF: "E_UNEXPECTED",
    0x80004001: "E_NOTIMPL",
    0x80070005: "E_ACCESSDENIED",
    0x8007000E: "E_OUTOFMEMORY",
    0x80070057: "E_INVALIDARG",
    0x80004003: "E_POINTER",
    0x80004005: "E_FAIL",
    0x8001010E: "E_WRONG_THREAD",
    0x8007001F: "E_GEN_FAILURE",
    0x800700AA: "E_BUSY",
    0x8000000A: "E_PENDING",
    0x8001011F: "E_TIMEOUT",
    0x80072743: "E_UNREACH",
    0x800704C7: "E_CANCELLED",
}

#: The advice for each code, by key. Split from the table above because
#: the short name is a technical identifier that must not be translated --
#: it is what somebody searches the vendor's header for -- while the
#: sentence beside it is written for a person.
_ADVICE = {
    "E_UNEXPECTED": N_("error.code.e_unexpected"),
    "E_NOTIMPL": N_("error.code.e_notimpl"),
    "E_ACCESSDENIED": N_("error.code.e_accessdenied"),
    "E_OUTOFMEMORY": N_("error.code.e_outofmemory"),
    "E_INVALIDARG": N_("error.code.e_invalidarg"),
    "E_POINTER": N_("error.code.e_pointer"),
    "E_FAIL": N_("error.code.e_fail"),
    "E_WRONG_THREAD": N_("error.code.e_wrong_thread"),
    "E_GEN_FAILURE": N_("error.code.e_gen_failure"),
    "E_BUSY": N_("error.code.e_busy"),
    "E_PENDING": N_("error.code.e_pending"),
    "E_TIMEOUT": N_("error.code.e_timeout"),
    "E_UNREACH": N_("error.code.e_unreach"),
    "E_CANCELLED": N_("error.code.e_cancelled"),
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
    name = _CODES.get(hr)
    advice = _(_ADVICE[name]) if name else _("error.code.unknown")
    # The code itself is never translated: it is what somebody pastes into
    # a bug report or searches the vendor's header for.
    return _("error.code.with_code", advice=advice,
             name=name or "unknown", code=f"0x{hr:08X}")


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
        super().__init__(heading if not detail
                         else _("error.joined", heading=heading, detail=detail))
        self.heading = heading
        self.detail = detail
        self.steps = tuple(steps)


class SdkMissing(CameraProblem):
    """No vendor SDK is installed, so no camera of that family can open."""

    kind = "sdk-missing"

    def __init__(self, brands: tuple[str, ...] = ()) -> None:
        super().__init__(
            _("error.sdk_missing.heading"),
            _("error.sdk_missing.detail"),
            (_("error.sdk_missing.step.install"),
             _("error.sdk_missing.step.manual"),
             _("error.sdk_missing.step.usb")))


class SdkTooOld(CameraProblem):
    """A library is installed but predates functions darlaston needs."""

    kind = "sdk-old"

    def __init__(self, path: str, missing: tuple[str, ...]) -> None:
        super().__init__(
            _("error.sdk_old.heading"),
            _("error.sdk_old.detail", path=path,
              missing=", ".join(missing)),
            (_("error.sdk_old.step.download"),
             _("error.sdk_old.step.point"),
             _("error.sdk_old.step.restart")))


class NoCameraFound(CameraProblem):
    """The SDK loaded and reported no devices."""

    kind = "no-camera"

    def __init__(self, what: str = "camera") -> None:
        super().__init__(
            _("error.no_camera.heading", what=what),
            _("error.no_camera.detail"),
            (_("error.no_camera.step.plugged"),
             _("error.no_camera.step.cable"),
             _("error.no_camera.step.wait")))


class EveryCameraPassedOver(CameraProblem):
    """Cameras are attached, and all of them are marked not-the-microscope.

    Distinct from `NoCameraFound`, and the distinction is the point: this
    screen is reachable only by a preference somebody set on purpose, so
    "check the cable" is exactly the wrong advice. The way out is the
    camera list, and it says so.
    """

    kind = "passed-over"

    def __init__(self, count: int = 1) -> None:
        super().__init__(
            _("error.passed_over.heading"),
            n_("error.passed_over.detail", count),
            (_("error.passed_over.step.open"),
             _("error.passed_over.step.untick",
               label=_("setup.cameras.ignore.label")),
             _("error.passed_over.step.plug")))


class CameraBusy(CameraProblem):
    """Something else already owns the device."""

    kind = "busy"

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            _("error.busy.heading"),
            detail or _("error.busy.detail"),
            (_("error.busy.step.close"),
             _("error.busy.step.video_call"),
             _("error.busy.step.wait")))


class PermissionDenied(CameraProblem):
    """Present on the bus, but not openable by this user."""

    kind = "permission"

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            _("error.permission.heading"),
            detail or _("error.permission.detail"),
            (_("error.permission.step.rules"),
             _("error.permission.step.reload"),
             _("error.permission.step.replug")))
