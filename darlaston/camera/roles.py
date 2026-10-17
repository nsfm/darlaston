"""Which control fills which slot.

The rail speaks in slots: focus, and the picture controls a camera that
finishes its own picture carries (brightness, contrast, saturation,
sharpness, gamma, hue). A device speaks in controls, by its own names
and ids. This is the one place the two are matched: the profile's roles
first, since a person's tick beats a guess, then the heuristics, which
are right for most cameras and exist so that nobody has to tick.

Roles are keyed by control *id* rather than name. The fingerprint that
identifies a camera hashes its control names, so a kernel rename would
orphan the profile and its roles in the same moment; V4L2 control ids
and DirectShow property numbers are stable.
"""
from __future__ import annotations

from .base import Control, FocusAxis, FocusRange

#: The slots the Picture row offers, in the order it shows them. Hue
#: last: on a stained section it is the one that does the most damage.
PICTURE_SLOTS = ("brightness", "contrast", "saturation", "sharpness",
                 "gamma", "hue")
#: Everything a control can be routed to from the controls page.
SLOTS = ("focus",) + PICTURE_SLOTS

#: The names each slot's control goes by, lower-cased, across V4L2 and
#: DirectShow. A match is a whole name or the name before a comma, so
#: "Focus, Absolute" is focus and "Focus, Automatic Continuous" is not.
_NAMES = {
    "focus": ("focus", "focus, absolute", "focus (absolute)"),
    "brightness": ("brightness",),
    "contrast": ("contrast",),
    "saturation": ("saturation",),
    "sharpness": ("sharpness",),
    "gamma": ("gamma",),
    "hue": ("hue",),
}


def guess(slot: str, controls: tuple[Control, ...]) -> Control | None:
    """The heuristic's choice for a slot, or None."""
    wanted = _NAMES.get(slot, ())
    for control in controls:
        name = control.name.strip().lower()
        if name in wanted and control.kind == "int":
            return control
    return None


#: Controls the rail or the backend already drive, by name: the exposure
#: time and its switch, gain, focus and its switch, the white balance
#: switch, and UVC's frame-rate priority. "Device defaults" leaves these
#: alone, and a route from the page cannot take them: resetting a focus
#: switch to its factory value turns continuous autofocus back on under
#: a sweep, and the backend settles these itself at open.
_OWNED = ("exposure", "gain", "focus", "white balance, auto",
          "whitebalance", "auto exposure", "autogain", "backlight")


def rail_owned(control: Control, roles: dict | None = None) -> bool:
    name = control.name.strip().lower()
    if control.kind != "int":
        return True                     # switches and menus are the camera's
    if roles and any(int(v) == control.id for v in roles.values() if v):
        return True                     # routed somewhere on the rail
    return any(name.startswith(w) or name == w for w in _OWNED)


def resolve(controls: tuple[Control, ...], roles: dict | None,
            slots: tuple[str, ...] = SLOTS) -> dict[str, Control | None]:
    """Slot to control, the profile's roles first and the guesses second.

    A role naming a control the device no longer reports is ignored, not
    an error: a camera that changed its control list has changed more
    than its control list, and the guess is the right fallback. A role
    of "nothing" (id 0 or None) empties the slot even where a guess
    would have filled it, which is how a person says a Brightness that
    lifts the blacks is not a brightness they want on the rail.
    """
    by_id = {c.id: c for c in controls}
    roles = roles or {}
    out: dict[str, Control | None] = {}
    taken: set[int] = set()
    # What a person said, first, whatever slot it is in; a guess never
    # takes a control a role has claimed.
    for slot in slots:
        if slot in roles and roles[slot] and int(roles[slot]) in by_id:
            control = by_id[int(roles[slot])]
            if control.id not in taken and control.kind == "int":
                out[slot] = control
                taken.add(control.id)
    for slot in slots:
        if slot in out:
            continue
        if slot in roles and not roles[slot]:
            out[slot] = None                    # emptied on purpose
            continue
        control = guess(slot, controls)
        if control is not None and control.id in taken:
            control = None                      # one control, one slot
        if control is not None:
            taken.add(control.id)
        out[slot] = control
    return out


class ControlFocusAxis(FocusAxis):
    """A focus motor behind any control, by the device's own key.

    For the camera whose stepper hides behind a property the heuristics
    did not pick: the person routes that control to focus on the
    controls page, and the sweep drives it through the generic API the
    same way it drives a motor the backend found itself.
    """

    def __init__(self, backend, control: Control) -> None:
        self._backend = backend
        self._control = control
        self._position = control.value if control.value is not None else control.default

    def range(self) -> FocusRange:
        c = self._control
        return FocusRange(c.min, c.max, max(1, c.step))

    def get(self) -> int:
        return int(self._position)

    def set(self, position: int) -> None:
        c = self._control
        position = max(c.min, min(c.max, int(position)))
        self._position = position
        self._backend.set_control(c.id, position)

    def set_auto(self, on: bool) -> None:
        if self._control.auto_id is not None:
            self._backend.set_auto(self._control.id, bool(on))
