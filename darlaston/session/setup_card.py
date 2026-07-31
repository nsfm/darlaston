"""'What's your setup?' -- a shareable summary of the rig.

Microscopy forums ask this constantly and the answers are always a paragraph of
half-remembered model numbers. The app already knows the camera, the sensor,
the stand, the turret and the link, so it can just say.

Renders to plain text for pasting into a forum, or ANSI for a terminal.

    from darlaston.session.setup_card import render
    print(render(setup, info, link))
"""
from __future__ import annotations

from dataclasses import dataclass

ART = r"""
        ___
       /   \
      |  o  |        eyepiece
       \___/
      __|_|__
     |  ===  |       optovar
     |_______|
      /|   |\
     ( | o | )       turret
      \|___|/
        | |
     ___|_|___
    |  _____  |      stage
    | |     | |
    |_|_____|_|
       |   |
      /     \
     /_______\       base
"""

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
BRASS, GOOD, BAD = "\033[38;5;179m", "\033[38;5;108m", "\033[38;5;167m"


@dataclass
class Row:
    key: str
    value: str
    tone: str = ""          # "", "good", "bad", "brass"


def _rows(setup, info=None, link=None, calibration=None) -> list[Row]:
    rows: list[Row] = []
    cam = setup.camera
    rows.append(Row("camera", cam.display, "brass"))
    if info is not None:
        full = max(info.resolutions, key=lambda r: r.width * r.height)
        rows.append(Row("sensor", f"{full.width}×{full.height} "
                                  f"({full.megapixels:.1f} MP), "
                                  f"{info.max_bit_depth}-bit"))
        if info.bayer_pattern:
            rows.append(Row("cfa", info.bayer_pattern))
    if cam.relay:
        rows.append(Row("relay", cam.relay))

    scope = setup.scope
    rows.append(Row("scope", scope.name, "brass"))

    obj = scope.turret.objective
    turret = []
    for i, o in enumerate(scope.turret.positions):
        if o is None:
            turret.append("--")
        else:
            turret.append(f"[{o.label}]" if i == scope.turret.current else o.label)
    if turret:
        rows.append(Row("turret", "  ".join(turret)))
    if scope.optovar:
        opt = "  ".join(f"[{v:g}×]" if i == scope.optovar_current else f"{v:g}×"
                        for i, v in enumerate(scope.optovar))
        rows.append(Row("optovar", opt))

    total = setup.total_magnification
    if obj and total:
        rows.append(Row("magnification", f"{total:g}× total"))

    rows.append(Row("illumination", setup.illumination.display, "brass"))

    if link is not None and link.speed_mbps:
        rows.append(Row("link", link.label, "bad" if link.is_degraded else "good"))

    if calibration:
        rows.append(Row("calibration", calibration,
                        "good" if "ready" in calibration.lower() else ""))
    return rows


def render(setup, info=None, link=None, calibration=None,
           colour: bool = False, width: int = 34) -> str:
    """Side-by-side art and specification, neofetch style."""
    rows = _rows(setup, info, link, calibration)
    art = [ln for ln in ART.strip("\n").splitlines()]
    keyw = max(len(r.key) for r in rows)

    tones = {"good": GOOD, "bad": BAD, "brass": BRASS, "": ""}
    lines: list[str] = []
    for i in range(max(len(art), len(rows) + 2)):
        left = art[i] if i < len(art) else ""
        left = left.ljust(width)

        if i == 0:
            right = (f"{BOLD}{setup.camera.display}{RESET}" if colour
                     else setup.camera.display)
            right += f" on {setup.scope.name}"
        elif i == 1:
            right = ("─" * (keyw + 26))
            if colour:
                right = f"{DIM}{right}{RESET}"
        elif 0 <= i - 2 < len(rows):
            r = rows[i - 2]
            k = r.key.rjust(keyw)
            v = r.value
            if colour:
                k = f"{DIM}{k}{RESET}"
                v = f"{tones[r.tone]}{v}{RESET}" if r.tone else v
            right = f"{k}  {v}"
        else:
            right = ""
        lines.append((left + right).rstrip())
    return "\n".join(lines)


def render_markdown(setup, info=None, link=None, calibration=None) -> str:
    """For pasting into a forum post, where ASCII art tends not to survive."""
    rows = _rows(setup, info, link, calibration)
    out = [f"**{setup.camera.display}** on **{setup.scope.name}**", ""]
    out += [f"- **{r.key}** -- {r.value}" for r in rows]
    return "\n".join(out)
