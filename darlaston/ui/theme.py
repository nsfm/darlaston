"""Palette, type and stylesheet.

Three decisions, each with a reason:

  * **Brass accent.** It never appears in a micrograph -- phase backgrounds are
    cyan, darkfield is neutral on black -- so the interface cannot compete with
    the image it exists to serve.
  * **Red means exactly one thing:** clipping and faults. A palette that spends
    red on decoration cannot warn with it.
  * **Dark by default.** Used in a dark room beside a bright eyepiece, and
    the default palette never forgets it. Light palettes exist for a bench in
    daylight; each palette says which it is, and the window frame follows.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

# ---- palette --------------------------------------------------------------
#
# Several, every one dark, every one named for something seen down the
# tube. The module-level names below are the *current* palette's colours:
# the stylesheet and a hundred-odd hand-drawn places read them by name at
# the moment they draw, so `use()` swaps them in place and the next paint
# is in the new colours. The dataclass is the record; the names are the
# working copy.

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str           # ground
    panel: str
    sunk: str
    line: str
    ink: str
    dim: str
    accent: str       # the brass, or whatever stands in for it
    accent_lit: str   # under the pointer
    accent_deep: str  # pressed
    on_accent: str    # lettering that sits on the accent
    good: str
    bad: str          # clipping and faults, nothing else
    dark: bool = True # the frame and the icons follow this
    #: Two axes. The organism is the hue family and the chip on the
    #: page; the variant is dark or light. High contrast is not a third
    #: variant but a switch over either, derived (see high_contrast). A
    #: palette's key stays its name, so a settings file from before the
    #: axes still names what it named.
    organism: str = ""
    variant: str = "dark"

    def __post_init__(self) -> None:
        if not self.organism:
            object.__setattr__(self, "organism", self.name)


VARIANTS = ("dark", "light")


PALETTES: dict[str, Palette] = {
    # The default: the grey-green of an instrument's inside, with brass.
    # Named for the amber pseudoscorpion that previewed blue and captured
    # amber the night the byte order was wrong.
    "pseudoscorpion": Palette(
        "pseudoscorpion", bg="#101210", panel="#191c19", sunk="#0b0d0b",
        line="#2a2e29", ink="#e4e7e0", dim="#757a72", accent="#c89b4a",
        accent_lit="#d7ad60", accent_deep="#ab8339", on_accent="#101210",
        good="#6fa96f", bad="#d0605e"),
    # The instrument's outside rather than its inside: brass on pale
    # lacquer, the brass darkened until white lettering reads on it.
    "pseudoscorpion-light": Palette(
        "pseudoscorpion-light", bg="#ecebe4", panel="#e1e0d7", sunk="#d6d5cb",
        line="#b8b7ab", ink="#1e211d", dim="#5e625a", accent="#8a6520",
        accent_lit="#9d7530", accent_deep="#705018", on_accent="#ffffff",
        good="#2f7d3f", bad="#b3261e", dark=False,
        organism="pseudoscorpion", variant="light"),
    # Blepharisma are pink, and it is their own pigment. A wine-dark
    # ground so the pink is the accent and not the glare.
    "blepharisma": Palette(
        "blepharisma", bg="#1a1115", panel="#251a20", sunk="#120b0f",
        line="#45303a", ink="#f3e4ea", dim="#a98a98", accent="#f48fb6",
        accent_lit="#ffa8c8", accent_deep="#d06f96", on_accent="#1a1115",
        good="#7fc98f", bad="#ff6b6b"),
    # Stentor coeruleus is that blue-green. The high-contrast one: white
    # ink, strong lines, an accent that cannot be missed.
    "stentor": Palette(
        "stentor", bg="#06110f", panel="#0c1c19", sunk="#030807",
        line="#245a50", ink="#ffffff", dim="#a9d2ca", accent="#4fd1c5",
        accent_lit="#7fe3d9", accent_deep="#38a99f", on_accent="#06110f",
        good="#8ef0a0", bad="#ff7070"),
    # A diatom under darkfield: glass on black. Neutral, bright, silver
    # for an accent. Also high contrast, with no colour spent at all.
    "diatom": Palette(
        "diatom", bg="#0a0b0c", panel="#151719", sunk="#040506",
        line="#3a3f45", ink="#ffffff", dim="#aeb6bf", accent="#d6dde6",
        accent_lit="#eef2f6", accent_deep="#aeb7c2", on_accent="#0a0b0c",
        good="#8ef0a0", bad="#ff7070"),
    # The same diatom under brightfield: glass on white, slate for an
    # accent. Still no colour spent.
    "diatom-light": Palette(
        "diatom-light", bg="#f4f5f6", panel="#e8eaec", sunk="#dde0e3",
        line="#b7bcc2", ink="#0e1012", dim="#585e66", accent="#4a5560",
        accent_lit="#5b6774", accent_deep="#39424b", on_accent="#ffffff",
        good="#2e7d4f", bad="#b3261e", dark=False,
        organism="diatom", variant="light"),
    # The same pink, on a bright ground, for a desk in daylight. The
    # accent is a shade deeper than the dark one's so that white lettering
    # clears 4.5:1 on it -- the first draft's did not, and the test said.
    "blepharisma-light": Palette(
        "blepharisma-light", bg="#fbe9f0", panel="#f5d7e2", sunk="#efc5d5",
        line="#d8a2b7", ink="#2b1620", dim="#6f4e5d", accent="#c93a74",
        accent_lit="#d44b84", accent_deep="#a82f61", on_accent="#ffffff",
        good="#2e7d4f", bad="#b3261e", dark=False,
        organism="blepharisma", variant="light"),
    # Noctiluca glows in the dark. Red on black for a blacked-out room:
    # fluorescence, darkfield, anything done with the eyes dark-adapted,
    # which only red light leaves alone. Red being every colour here, a
    # fault cannot be red; it is the one pale thing on the page.
    "noctiluca": Palette(
        "noctiluca", bg="#0e0404", panel="#170707", sunk="#080202",
        line="#4a1515", ink="#ff7373", dim="#b84a4a", accent="#ff3d3d",
        accent_lit="#ff6464", accent_deep="#cc3030", on_accent="#0e0404",
        good="#ff9a5c", bad="#ffd9b0"),
    # Its high-contrast one is authored, not derived: derived, the ink
    # would go white, and white is what a dark-adapted eye is being
    # spared. Pale red instead, as far toward white as it can go and
    # still be red.
    "noctiluca-high": Palette(
        "noctiluca-high", bg="#0e0404", panel="#170707", sunk="#080202",
        line="#a03c3c", ink="#ffdada", dim="#e68c8c", accent="#ff6464",
        accent_lit="#ff8a8a", accent_deep="#ff3d3d", on_accent="#0e0404",
        good="#ffb088", bad="#fff0d8", organism="noctiluca"),
    # Anna Atkins, 1843: cyanotypes of British algae, the first book
    # with photographs in it. Prussian blue and paper white, two
    # pigments and nothing else, so the accent is the paper. Turned
    # over for the light one: the same two, the other way up.
    "atkins": Palette(
        "atkins", bg="#0b1f3a", panel="#112a4a", sunk="#071628",
        line="#2a4a74", ink="#eef3f7", dim="#9bb3cc", accent="#f3eedc",
        accent_lit="#ffffff", accent_deep="#d8d2bd", on_accent="#0b1f3a",
        good="#a8e6c0", bad="#ff8c7a"),
    "atkins-light": Palette(
        "atkins-light", bg="#f3efe3", panel="#e9e4d4", sunk="#ddd7c4",
        line="#b9b3a0", ink="#0b1f3a", dim="#4a5f7c", accent="#1d4b8a",
        accent_lit="#2a5fa8", accent_deep="#143a6c", on_accent="#f3efe3",
        good="#2e7d4f", bad="#b3261e", dark=False,
        organism="atkins", variant="light"),
    # Haematoxylin and eosin, the stain every histologist has looked at
    # for a thousand hours: purple-blue nuclei, pink everything else.
    # Light first, since a section is viewed on white: lavender paper,
    # haematoxylin ink, the violet for an accent, eosin in the tints.
    # The dark one puts the eosin on an aubergine ground.
    "haematoxylin": Palette(
        "haematoxylin", bg="#140b1a", panel="#1f1227", sunk="#0d0712",
        line="#45305a", ink="#efe0f5", dim="#a88fb8", accent="#e87aa8",
        accent_lit="#f293bb", accent_deep="#c9608c", on_accent="#140b1a",
        good="#7fc98f", bad="#ff6b6b"),
    "haematoxylin-light": Palette(
        "haematoxylin-light", bg="#f6edf3", panel="#eddfe8", sunk="#e3d0dd",
        line="#c5a9bd", ink="#2c1a3d", dim="#6a4f7a", accent="#6d3fa0",
        accent_lit="#8255b8", accent_deep="#58328a", on_accent="#ffffff",
        good="#2e7d4f", bad="#b3261e", dark=False,
        organism="haematoxylin", variant="light"),
    # Spirogyra, the pond-water classic with the chloroplast wound in a
    # spiral. The green one. Green being spent, "good" is a pale
    # yellow-green on the dark and an ochre on the light.
    "spirogyra": Palette(
        "spirogyra", bg="#0b170b", panel="#132113", sunk="#060d06",
        line="#2d4a2d", ink="#e3f0e0", dim="#8fae8c", accent="#8fe36a",
        accent_lit="#a9f28a", accent_deep="#6fbf4f", on_accent="#0b170b",
        good="#f0e68c", bad="#ff6b6b"),
    "spirogyra-light": Palette(
        "spirogyra-light", bg="#eef5e8", panel="#e1ecd8", sunk="#d3e2c7",
        line="#a9c29c", ink="#152312", dim="#4f6a48", accent="#3f7f2e",
        accent_lit="#4d9639", accent_deep="#2f6322", on_accent="#ffffff",
        good="#8a6d00", bad="#b3261e", dark=False,
        organism="spirogyra", variant="light"),
    # Radiolaria as Haeckel drew them: sepia on cream, and cream on
    # sepia for the dark. Monochrome, warm, the colour of a plate in a
    # book, which is where most people have seen one.
    "radiolaria": Palette(
        "radiolaria", bg="#15100b", panel="#1f1812", sunk="#0c0906",
        line="#4a3a2a", ink="#ece2d2", dim="#a89480", accent="#efe0c8",
        accent_lit="#ffffff", accent_deep="#cfbfa4", on_accent="#15100b",
        good="#a8d8a0", bad="#ff8a70"),
    "radiolaria-light": Palette(
        "radiolaria-light", bg="#f5efe4", panel="#ece3d3", sunk="#e0d5c1",
        line="#b9a78c", ink="#2b1f14", dim="#6b5a46", accent="#6b4a2a",
        accent_lit="#7f5c38", accent_deep="#543a20", on_accent="#f5efe4",
        good="#2e7d4f", bad="#b3261e", dark=False,
        organism="radiolaria", variant="light"),
    # Aequorea victoria, the jellyfish GFP came from. A fluorescence
    # palette: black, DAPI blue for the ink, GFP green for the accent,
    # a red-channel fault. Dark only, as fluorescence is.
    "aequorea": Palette(
        "aequorea", bg="#050a12", panel="#0b1320", sunk="#02050a",
        line="#1f3a4f", ink="#bfe3ff", dim="#6f9bbd", accent="#4dffa0",
        accent_lit="#7dffba", accent_deep="#2fd882", on_accent="#050a12",
        good="#9bffc4", bad="#ff5c7a"),
}
DEFAULT_PALETTE = "pseudoscorpion"


def _mix(a: str, b: str, t: float) -> str:
    """`a` moved `t` of the way to `b`."""
    ca, cb = QtGui.QColor(a), QtGui.QColor(b)
    return QtGui.QColor(*(round(x + (y - x) * t) for x, y in
                          zip(ca.getRgb()[:3], cb.getRgb()[:3]))).name()


def high_contrast(p: Palette) -> Palette:
    """The same palette with the contrast turned up.

    Ink to white (black, on a light ground), the dim and the lines
    pulled a good way toward it, the accent to its lit value. A switch
    over any palette rather than a variant of each, because derived it
    looks right on all of them and authored it would be the third
    hand-written one where it gets sloppy. An authored one under the
    key "<name>-high", in the table or in palettes.json, is used
    instead when there is one; the contrast test holds both to the
    same figures.
    """
    toward = "#ffffff" if p.dark else "#000000"
    return Palette(
        f"{p.name}-high", bg=p.bg, panel=p.panel, sunk=p.sunk,
        line=_mix(p.line, toward, 0.35), ink=toward,
        dim=_mix(p.dim, toward, 0.5),
        accent=p.accent_lit if p.dark else p.accent_deep,
        accent_lit=_mix(p.accent_lit, toward, 0.2) if p.dark else p.accent_deep,
        accent_deep=p.accent, on_accent=p.on_accent,
        good=_mix(p.good, toward, 0.2), bad=_mix(p.bad, toward, 0.2),
        dark=p.dark, organism=p.organism, variant=p.variant)


def organisms() -> list[str]:
    """The hue families, in the table's order."""
    seen: list[str] = []
    for p in PALETTES.values():
        if p.organism not in seen and not p.name.endswith("-high"):
            seen.append(p.organism)
    return seen


def variants_of(organism: str) -> dict[str, str]:
    """Variant to palette key, for what the organism has."""
    return {p.variant: p.name for p in PALETTES.values()
            if p.organism == organism and not p.name.endswith("-high")}


def key_for(organism: str, variant: str) -> str:
    """The palette key for an organism in a variant, falling back to
    the organism's dark one, then to whatever it has."""
    have = variants_of(organism)
    return have.get(variant) or have.get("dark") or next(iter(have.values()), DEFAULT_PALETTE)


def resolve(name: str, high: bool) -> Palette:
    """The palette a name and the switch come to, unknown names being
    the default."""
    p = PALETTES.get(name, PALETTES[DEFAULT_PALETTE])
    if not high:
        return p
    return PALETTES.get(f"{p.name}-high") or high_contrast(p)


#: A palette has these, and `dark` besides; a file that leaves one out
#: is describing something else.
_PALETTE_KEYS = ("bg", "panel", "sunk", "line", "ink", "dim", "accent",
                 "accent_lit", "accent_deep", "on_accent", "good", "bad")


def load_extra(path: Path) -> list[str]:
    """Palettes from a file of the person's own, added to PALETTES.

    `palettes.json` beside settings.json: an object of name to palette,
    each a flat object of the twelve colours as "#rrggbb", and `dark`
    (true unless said). No page for it and none planned: the person who
    wants grey-on-grey with a neon accent can have it without the rest
    of us seeing it. A bad entry is skipped, not fatal; a name that is
    one of ours replaces ours, which is how a palette gets tuned without
    waiting for a release. Returns the names taken.
    """
    import json
    import re
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    taken = []
    for name, entry in raw.items():
        if not isinstance(entry, dict) or not isinstance(name, str):
            continue
        colours = {k: entry.get(k) for k in _PALETTE_KEYS}
        if not all(isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v)
                   for v in colours.values()):
            continue
        variant = entry.get("variant", "dark")
        organism = entry.get("organism", name)
        PALETTES[name] = Palette(name, **{k: v.lower() for k, v in colours.items()},
                                 dark=bool(entry.get("dark", True)),
                                 organism=organism if isinstance(organism, str) else name,
                                 variant=variant if variant in VARIANTS else "dark")
        taken.append(name)
    return taken

# ---- marks on the picture -------------------------------------------------
#
# What is drawn over the picture itself -- the focus-assist box, peaking,
# the presenter's ring -- is chosen against the slide, not the chrome. A
# pink box on a pink mount is no box at all, and the palette does not
# know what is on the stage. Four inks that are not the colour of any
# common mountant, the palette's accent for whoever wants the old look,
# and "auto", which reads the picture and picks the ink furthest from it.

MARK_INKS: dict[str, str] = {
    "gold": "#ffeb5a",      # the peaking colour since the first release
    "green": "#5aff7a",
    "cyan": "#5ae6ff",
    "magenta": "#ff5ad6",
}
MARK_CHOICES = ("auto", "accent", *MARK_INKS)
#: The setting, as chosen: a name from MARK_CHOICES.
mark = "auto"
#: How much better a different ink has to score before "auto" changes its
#: mind. The field under a drifting stage sits near the middle, and an
#: ink that flickers between two colours is worse than either.
MARK_MARGIN = 0.15


def use_marks(name: str) -> str:
    global mark
    mark = name if name in MARK_CHOICES else "auto"
    return mark


def mark_ink(name: str) -> str:
    """The hex for a mark choice other than "auto"."""
    return BRASS if name == "accent" else MARK_INKS.get(name, MARK_INKS["gold"])


def _opponent(rgb) -> tuple[float, float]:
    """Red-against-green and yellow-against-blue, each in -1..1. Two axes
    are enough to say how far apart two hues are, and they are the two
    the eye uses."""
    r, g, b = (float(v) / 255.0 for v in rgb[:3])
    return r - g, (r + g) / 2.0 - b


def pick_mark(field_rgb, current: str | None = None) -> str:
    """Which of MARK_INKS stands out most against a picture whose mean
    colour is `field_rgb`, keeping `current` unless another ink beats it
    by MARK_MARGIN. With no incumbent the first in the table stands,
    which is the old gold: a grey field is about equally far from every
    ink, and the margin is what keeps it gold there."""
    fa, fb = _opponent(field_rgb)
    scores = {}
    for name, hexcode in MARK_INKS.items():
        ia, ib = _opponent(QtGui.QColor(hexcode).getRgb())
        scores[name] = math.hypot(ia - fa, ib - fb)
    if current not in scores:
        current = next(iter(MARK_INKS))
    best = max(scores, key=lambda n: scores[n])
    if scores[best] - scores[current] < MARK_MARGIN:
        return current
    return best

BG = PANEL = SUNK = LINE = ON_BRASS = INK = DIM = ""
BRASS = BRASS_LIT = BRASS_DEEP = GOOD = BAD = ""
current: Palette = PALETTES[DEFAULT_PALETTE]
#: The high-contrast switch, as last used.
high = False
#: Told after every `use()`. For colours held as objects rather than read
#: by name -- the painted widgets keep QColors -- so they can follow.
_listeners: list = []


def on_change(callback) -> None:
    _listeners.append(callback)


#: Widgets wearing a sheet built from the palette, with the recipe that
#: built it, so `use()` can have them dressed again.
_dressed: list = []


def dress(widget, sheet) -> None:
    """Give a widget a stylesheet that follows the palette.

    `sheet` is a callable returning the sheet; it is applied now and
    again after every `use()`. A sheet set once at construction is the
    one thing a palette change cannot reach on its own, and there were
    forty of them. The widget is held weakly and a dead one is dropped
    the next time round, so this costs nothing to leave registered.
    """
    import inspect
    import weakref
    ref = weakref.ref(widget)
    # A bound method as the recipe would otherwise keep its widget alive
    # through this list; held weakly too, so a dressed widget can die.
    recipe = weakref.WeakMethod(sheet) if inspect.ismethod(sheet) else (lambda: sheet)

    def apply() -> bool:
        w, fn = ref(), recipe()
        if w is None or fn is None:
            return False
        try:
            w.setStyleSheet(fn())
        except RuntimeError:        # the C++ side has gone
            return False
        return True

    apply()
    _dressed.append(apply)


def use(name: str, high_contrast: bool | None = None) -> Palette:
    """Make a palette current, by name; an unknown name is the default.
    `high_contrast` turns the switch; left out, it stays as it was.

    Rebinds the module-level colours, which is what everything draws
    from. Already-built widgets repaint in the new colours as they
    repaint; a stylesheet built earlier has to be asked for again, which
    the window does.
    """
    global BG, PANEL, SUNK, LINE, ON_BRASS, INK, DIM
    global BRASS, BRASS_LIT, BRASS_DEEP, GOOD, BAD, current, high
    if high_contrast is not None:
        high = bool(high_contrast)
    palette = resolve(name, high)
    current = palette
    BG, PANEL, SUNK, LINE = palette.bg, palette.panel, palette.sunk, palette.line
    #: Lettering that sits *on* the accent rather than beside it. Brass is
    #: a light colour, so INK over it is two light tones and reads as a
    #: smudge at eleven pixels.
    ON_BRASS, INK, DIM = palette.on_accent, palette.ink, palette.dim
    BRASS, BRASS_LIT, BRASS_DEEP = palette.accent, palette.accent_lit, palette.accent_deep
    GOOD, BAD = palette.good, palette.bad
    for callback in list(_listeners):
        try:
            callback()
        except Exception:
            pass
    _dressed[:] = [apply for apply in list(_dressed) if apply()]
    # The application's own palette, for whatever the stylesheet does not
    # name: a spin box's frame, a scroll bar, a list's base. Without it
    # those draw in the desktop's colours, which on a dark desktop under a
    # light palette is black boxes on pink.
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.setPalette(qpalette())
    return palette


def qpalette() -> QtGui.QPalette:
    """The current palette as Qt's own, for the roles the stylesheet
    does not reach."""
    P, R = QtGui.QPalette, QtGui.QPalette.ColorRole
    pal = P()
    pairs = {R.Window: BG, R.WindowText: INK, R.Base: SUNK, R.AlternateBase: PANEL,
             R.Text: INK, R.Button: PANEL, R.ButtonText: INK, R.ToolTipBase: PANEL,
             R.ToolTipText: INK, R.Highlight: BRASS, R.HighlightedText: ON_BRASS,
             R.PlaceholderText: DIM, R.Light: LINE, R.Midlight: LINE, R.Dark: SUNK,
             R.Mid: LINE, R.Shadow: SUNK, R.Link: BRASS}
    for role, colour in pairs.items():
        pal.setColor(role, QtGui.QColor(colour))
    pal.setColor(P.ColorGroup.Disabled, R.Text, QtGui.QColor(LINE))
    pal.setColor(P.ColorGroup.Disabled, R.WindowText, QtGui.QColor(LINE))
    pal.setColor(P.ColorGroup.Disabled, R.ButtonText, QtGui.QColor(LINE))
    return pal


use(DEFAULT_PALETTE)


def _luminance(hex_colour: str) -> float:
    """Relative luminance, as WCAG defines it, from "#rrggbb"."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours, 1 to 21."""
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


FONT_DIR = Path(__file__).parent / "fonts"

_families: dict[str, str] = {}


def load_fonts() -> dict[str, str]:
    """Register the bundled faces. Falls back to system stacks if absent.

    IBM Plex, OFL 1.1. Chosen because it was drawn for an engineering company's
    technical material -- slightly mechanical, excellent tabular figures -- and
    because it is not the face every generated interface reaches for.
    """
    global _families
    if _families:
        return _families
    # QFontDatabase segfaults without a QApplication rather than raising, so
    # refuse politely instead of taking the process down.
    from PySide6 import QtWidgets
    if QtWidgets.QApplication.instance() is None:
        # Every role, including display. Returning a short dict here made
        # this an ordering trap rather than a fallback: app_icon() reads
        # families["display"], so it raised KeyError when it happened to
        # run before anything had built a QApplication, and passed in any
        # order where something had.
        return {"mono": "monospace", "sans": "sans-serif",
                "display": "sans-serif"}
    wanted = {
        "mono": ["IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf"],
        "sans": ["IBMPlexSans-Regular.ttf", "IBMPlexSans-SemiBold.ttf"],
        # The wordmark only. One face, used once, so it can afford to be
        # a period display letter where nothing else could.
        "display": ["Wordmark.ttf"],
    }
    found: dict[str, str] = {}
    for role, files in wanted.items():
        for name in files:
            path = FONT_DIR / name
            if not path.exists():
                continue
            fid = QtGui.QFontDatabase.addApplicationFont(str(path))
            if fid < 0:
                continue
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid)
            if fams and role not in found:
                found[role] = fams[0]
    _families = {
        "mono": found.get("mono", "monospace"),
        "sans": found.get("sans", "sans-serif"),
        # Falls back to the body face rather than to a system serif: an
        # unpredictable substitute in the one place the name appears is
        # worse than the name simply set in the face we already ship.
        "display": found.get("display", found.get("sans", "sans-serif")),
    }
    return _families


class _OurIconsOnly(QtWidgets.QProxyStyle):
    """Refuses the platform's icons on standard dialog buttons.

    Whether a QDialogButtonBox decorates its buttons is a style *hint*,
    answered by the platform: off under the offscreen platform, on under
    GNOME and KDE, where it contributes icons drawn for a light desktop.
    A black cross on a dark button.

    The stylesheet has a property for this, but it only reaches dialogs
    that apply our stylesheet. Answering the hint reaches every one of
    them, including any written later by somebody who forgets to.
    """

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QtWidgets.QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        return super().styleHint(hint, option, widget, returnData)


def install(app) -> None:
    """Apply what has to be set on the application rather than a widget."""
    app.setStyle(_OurIconsOnly(app.style()))
    app.setPalette(qpalette())


#: Sizes a window manager, taskbar, dock or alt-tab switcher is likely to
#: ask for, plus what an .icns wants. Rasterised at each rather than scaled
#: from one: the slots are thin at the small end and a resample turns them
#: to speckle.
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)

#: The aperture: a brass annulus cut by five slots around a pentagonal
#: opening. It is a diatom valve and a lens iris at the same time, which is
#: the subject and the instrument in one shape.
#:
#: Every number here was chosen against a 16 px render magnified with
#: nearest-neighbour, because that is where icons are actually judged and
#: where the obvious version of this dies. What was learned:
#:
#:   * Detail has to live in *negative space*. Twelve stroked ribs, which
#:     is what a diatom really looks like, alias into noise below 32 px.
#:     Solid areas survive a resample; thin lines do not.
#:   * Five, not six. A hexagon at 32 px reads as a nut or a bolt head.
#:     Odd symmetry reads as nothing else in a dock, which is the whole
#:     job of an icon at that size.
#:   * The slots are phase-offset by half a step so no slot lines up with
#:     a vertex of the opening. Aligned, the two dark shapes merge into
#:     one smear small.
BLADES = 5
#: Radii as a fraction of the disc, and the slot width in degrees.
OPENING = 0.46
SLOT_DEGREES = 10.0
DISC = 0.36


def _aperture(size: int) -> QtGui.QImage:
    """One rasterisation of the mark, at `size` square."""
    image = QtGui.QImage(size, size,
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtGui.QColor(0, 0, 0, 0))
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setPen(QtCore.Qt.PenStyle.NoPen)

    # The plate. A rounded square rather than a full-bleed one because
    # macOS expects the art to *be* that shape -- it applies no mask of
    # its own -- and Windows is happy either way.
    p.setBrush(QtGui.QColor(PANEL))
    radius = size * 0.225
    p.drawRoundedRect(QtCore.QRectF(0, 0, size, size), radius, radius)

    centre = size / 2.0
    disc = size * DISC
    p.setBrush(QtGui.QColor(BRASS))
    p.drawEllipse(QtCore.QPointF(centre, centre), disc, disc)

    # Slots, as filled wedges rather than strokes: a stroked line has a
    # width that has to survive scaling and a filled shape does not.
    p.setBrush(QtGui.QColor(PANEL))
    inner, outer = disc * (OPENING + 0.08), disc * 1.02
    for i in range(BLADES):
        angle = 360.0 * i / BLADES + 180.0 / BLADES
        wedge = QtGui.QPainterPath()
        out_box = QtCore.QRectF(centre - outer, centre - outer,
                                2 * outer, 2 * outer)
        in_box = QtCore.QRectF(centre - inner, centre - inner,
                               2 * inner, 2 * inner)
        wedge.arcMoveTo(out_box, angle - SLOT_DEGREES / 2)
        wedge.arcTo(out_box, angle - SLOT_DEGREES / 2, SLOT_DEGREES)
        wedge.arcTo(in_box, angle + SLOT_DEGREES / 2, -SLOT_DEGREES)
        wedge.closeSubpath()
        p.drawPath(wedge)

    # The opening, point up.
    opening = QtGui.QPainterPath()
    for i in range(BLADES):
        angle = math.radians(360.0 * i / BLADES - 90.0)
        point = QtCore.QPointF(centre + math.cos(angle) * disc * OPENING,
                               centre + math.sin(angle) * disc * OPENING)
        opening.moveTo(point) if i == 0 else opening.lineTo(point)
    opening.closeSubpath()
    p.drawPath(opening)
    p.end()
    return image


def app_icon() -> QtGui.QIcon:
    """The mark, at every size a platform asks for.

    Drawn rather than shipped as a folder of PNGs, so the dock, the
    taskbar, the About window and the installers cannot drift apart. The
    files the platforms need are generated from this same function; see
    packaging/make_icons.py.
    """
    # Not strictly needed now the mark carries no type, but kept: a
    # QApplication is required for QImage on some platforms and this is
    # called early during startup.
    if QtWidgets.QApplication.instance() is None:
        return QtGui.QIcon()
    icon = QtGui.QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QtGui.QPixmap.fromImage(_aperture(size)))
    return icon


def identify(app) -> None:
    """Say who we are, everywhere a platform asks.

    Qt's default is the literal string "PySideApp", and with no icon set at
    all every title bar, taskbar, dock and alt-tab switcher on a platform
    that draws them shows the interpreter's own mark. None of that is
    visible on a window manager that draws no decorations, which is exactly
    why it survived this long.

    The desktop file name is the freedesktop hook: under Wayland it is the
    only way the compositor can match a window to an installed icon, and
    without it the window gets a generic placeholder however good the icon
    we set here is.
    """
    app.setApplicationName("darlaston")
    app.setApplicationDisplayName("darlaston")
    app.setDesktopFileName("darlaston")
    app.setWindowIcon(app_icon())


#: DWM's dark-frame attribute. 20 from Windows 10 build 18985 onwards, 19
#: on the builds just before it, and the call simply fails on anything
#: older -- which is the whole error handling this needs.
_DWM_DARK = (20, 19)


#: How far the traffic lights reach across the top left of a macOS
#: window, plus a little air. The toolbar is inset by this so the wordmark
#: does not sit under them once the title bar is transparent.
#:
#: A measurement, not a specification. Apple publishes no figure for it --
#: not in the human interface guidelines and not in the headers -- and has
#: moved the buttons before, in Big Sur. The runtime answer is
#: `NSMaxX([window standardWindowButton:NSWindowZoomButton].frame)`, which
#: returns an NSRect: 32 bytes, so it needs `objc_msgSend_stret` on Intel
#: and the plain call on Apple silicon, which is a two-architecture ctypes
#: problem in exchange for about six points of accuracy. Measured extent
#: runs 70 to 75 across Big Sur through Sequoia, so this errs wide and the
#: cost of being wrong is a little more air rather than a hidden wordmark.
#:
#: `safeAreaMargins` does not answer this. It reports the *title bar's
#: height* as a top margin; the lights' reach across is a left margin that
#: macOS does not report at all.
MACOS_LIGHTS = 78

#: NSWindowTitleHidden.
#:
#: Distinct from setting an empty title: hiding the title is also what
#: vertically centres the traffic lights in the bar. An empty string
#: leaves them where a title would have pushed them.
_NS_TITLE_HIDDEN = 1

#: Re-exported. It lives in `frame` now, because it has to be consulted
#: before the takeover as well as before the restyling, and `frame` is
#: what the takeover goes through.
from .frame import NATIVE_FRAME_ENV  # noqa: E402


def _mac_unify_titlebar(window) -> bool:
    """Make the title bar transparent and run the content up under it.

    macOS draws a grey strip above the window saying the application's
    name, which on a program with its own toolbar is a second bar saying
    less. The usual answer is to go frameless, and the usual cost is
    reimplementing everything the frame does: full screen, zoom on double
    click, snapping, the window menu, and the traffic lights themselves.

    This is the other answer, and it is what every Mac application with a
    custom toolbar actually does. The window keeps its real title bar and
    every gesture that comes with it; the bar is simply made transparent,
    its title hidden, and the content view told it may extend the whole
    height.

    **Two of the three are Qt's own API and must be**, on Qt 6.9 and
    later. `ExpandedClientAreaHint` is what Qt now derives
    `NSWindowStyleMaskFullSizeContentView` from, and
    `NoTitleBarBackgroundHint` is what it derives
    `titlebarAppearsTransparent` from. Setting either by hand through
    AppKit works and does not last: `QCocoaWindow::setWindowFlags`
    reassigns both from the Qt flags, and both native fullscreen handlers
    call it to restore the style mask. So the window would come back out
    of fullscreen with an opaque title bar and no title in it. Before 6.9
    Qt preserved a hand-set bit deliberately -- that was the fix for
    QTBUG-69975 -- and 6.9 removed the preservation once the flags
    existed to replace it.

    **The third is not Qt's**, and is done here. `titleVisibility`
    appears nowhere in the Cocoa plugin in any release from 6.2 to dev,
    so nothing recomputes it and one message settles it for good.

    The remaining call is a read-nothing, set-once `objc_msgSend` through
    a private prototype: `ctypes` caches one function object per name on
    the library, and assigning `argtypes` to the shared one is process-
    wide mutable state that anything else calling into libobjc would
    race with.
    """
    from PySide6 import QtCore

    handle = window.windowHandle()
    if handle is None:
        return False

    handle.setFlags(handle.flags()
                    | QtCore.Qt.WindowType.ExpandedClientAreaHint
                    | QtCore.Qt.WindowType.NoTitleBarBackgroundHint)

    # Qt 6.8 gave macOS safe-area margins, and a widget layout then insets
    # itself by the title bar -- so the content runs under the bar and the
    # layout immediately puts it back below, which looks exactly like
    # nothing happened. Clearing this on the top level is what stops that.
    # Only the top level: since QTBUG-133215 the attribute defaults on for
    # top levels alone, so children need no help.
    window.setAttribute(
        QtCore.Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)

    _mac_hide_title(window)
    return True


def _mac_hide_title(window) -> bool:
    """Hide the title text, which is the one part Qt leaves alone."""
    import ctypes
    import ctypes.util
    import sys

    # Checked before the library is even looked for. `find_library("objc")`
    # succeeds on a Linux box with GNUstep installed and hands back a
    # runtime with no `objc_msgSend` in it -- a real machine, since that is
    # what this was written on.
    if sys.platform != "darwin":
        return False
    path = ctypes.util.find_library("objc")
    if not path:
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(path)
        send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    except (OSError, AttributeError):
        return False
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]

    view = ctypes.c_void_p(int(window.winId()))
    if not view.value:
        return False

    # A prototype per signature, rather than assigning to the cached
    # `objc.objc_msgSend`. Declaring every argument is also what makes the
    # call non-variadic, which is what it has to be: objc_msgSend is a
    # trampoline into a method that reads its arguments from registers,
    # and a variadic call on arm64 would put them on the stack instead.
    # A *partial* argtypes is worse than none -- CPython treats a call
    # with more arguments than argtypes as variadic.
    send_p = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p)(send)
    send_i = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_long)(send)

    ns_window = send_p(view,
                       ctypes.c_void_p(objc.sel_registerName(b"window")))
    if not ns_window:
        return False
    send_i(ctypes.c_void_p(ns_window),
           ctypes.c_void_p(objc.sel_registerName(b"setTitleVisibility:")),
           ctypes.c_long(_NS_TITLE_HIDDEN))
    return True


def follow_window_controls(window, toolbar) -> None:
    """Keep the toolbar clear of the traffic lights, including when there
    are none.

    In native fullscreen macOS takes the title bar out of the layout and
    moves the traffic lights into the overlay that slides down with the
    menu bar. They are no longer above the toolbar, so an inset that
    stayed put would be a hole in the top left with nothing in it, and
    the hole would come and go as the pointer neared the top edge.

    Driven from Qt's window state rather than an AppKit notification:
    `QCocoaWindow` reports both fullscreen transitions through it, and it
    is the same signal on every platform even though only one uses it.
    """
    handle = window.windowHandle()
    if handle is None:
        return

    from PySide6 import QtCore

    def apply(state=None) -> None:
        full = bool(window.windowState()
                    & QtCore.Qt.WindowState.WindowFullScreen)
        toolbar.inset_for_window_controls(0 if full else MACOS_LIGHTS)
        # Re-asserted here, not because Qt recomputes it -- it never
        # touches titleVisibility, in any release -- but because Qt
        # destroys and rebuilds the NSWindow on some transitions, and a
        # *new* one starts with the title visible. Free, since this is
        # already connected to the signal those transitions raise.
        _mac_hide_title(window)

    handle.windowStateChanged.connect(apply)
    apply()


def match_frame(window) -> bool:
    """Ask the platform for a title bar that matches the program.

    **Windows** draws the frame in the *system* light or dark setting
    rather than the application's, so a near-black program on a
    light-themed machine gets a white strip across the top of it. One
    documented DWM attribute settles that.

    **macOS** gets its title bar made transparent, with the content run
    up underneath -- see `_mac_unify_titlebar`. The window keeps its
    traffic lights and every native gesture.

    **X11 and Wayland** are left alone: decorations belong to the window
    manager there by design, and taking them over would be arguing with
    the desktop rather than fitting into it.

    Returns whether anything was changed. Never raises: a frame that could
    not be restyled is a cosmetic disappointment, and taking the window
    down over it would not be.
    """
    import os
    import sys

    if os.environ.get(NATIVE_FRAME_ENV):
        return False
    try:
        if sys.platform == "darwin":
            return _mac_unify_titlebar(window)
        if not sys.platform.startswith("win"):
            return False
        import ctypes

        handle = ctypes.c_void_p(int(window.winId()))
        enabled = ctypes.c_int(1 if current.dark else 0)
        for attribute in _DWM_DARK:
            ok = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle, ctypes.c_uint(attribute), ctypes.byref(enabled),
                ctypes.sizeof(enabled))
            if ok == 0:
                return True
    except Exception:
        return False
    return False


def stylesheet() -> str:
    fam = load_fonts()
    from . import icons
    chevron = icons.path_for("chevron-down", INK)
    chevron_off = icons.path_for("chevron-down", LINE)
    chevron_up = icons.path_for("chevron-up", INK)
    return f"""
    QWidget {{
        background: {BG}; color: {INK};
        font-family: "{fam['sans']}"; font-size: 12px;
    }}
    QLabel, QCheckBox {{ background: transparent; }}
    QLabel[role="mono"], QLabel[role="value"], QLabel[role="key"] {{
        font-family: "{fam['mono']}";
    }}
    QLabel[role="key"] {{ color: {DIM}; }}
    QLabel[role="value"] {{ color: {INK}; }}
    QLabel[role="label"] {{
        font-family: "{fam['mono']}"; color: {DIM};
        font-size: 10px; letter-spacing: 1.4px;
    }}
    QLabel[role="title"] {{ font-family: "{fam['mono']}"; color: {INK}; }}
    QLabel[role="sub"] {{ font-family: "{fam['mono']}"; color: {DIM}; }}
    QLabel[role="heading"] {{ font-size: 15px; font-weight: 600; color: {INK}; }}
    QLabel[role="body"] {{ color: {DIM}; font-size: 12.5px; }}
    QLabel[role="advice"] {{ color: {BRASS}; font-size: 12px; }}
    QLabel[role="fault"] {{ font-size: 15px; font-weight: 600; color: {BAD}; }}

    /* Bordered widgets are inset by a pixel throughout.
       On a fractional device pixel ratio -- any HiDPI display with
       non-integer scaling -- a border flush against the widget's clip
       boundary loses its outermost device pixel, and the left edge simply
       does not draw. Measured: at 1.5x the left column rendered at 151
       against 236 on the right; inset by one pixel it renders at 239.
       A thicker border does not help, because the problem is the boundary
       rather than the weight. */
    QFrame[role="panel"] {{
        background: {PANEL}; border: 1px solid {LINE}; border-radius: 4px;
        margin: 1px;
    }}
    QFrame[role="bar"] {{
        background: {PANEL}; border: 0; border-bottom: 1px solid {LINE};
    }}
    /* The caption strip sits inside the bar and has to let it through.
       Without this the blanket QWidget rule above paints it the window's
       colour, and the window buttons sit in a darker patch. */
    QWidget[role="caption"] {{ background: transparent; }}

    QPushButton {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; margin: 1px;
        padding: 6px 12px; color: {INK}; background: transparent;
    }}
    QPushButton:hover {{ border-color: {DIM}; }}
    QPushButton:focus {{ border-color: {BRASS}; outline: none; }}
    QPushButton[role="primary"] {{ border-color: {BRASS}; color: {BRASS}; }}
    QPushButton[role="seg"] {{
        padding: 3px 0; font-size: 11px; color: {DIM};
    }}
    /* Dark lettering on the fill, as the sliders do. This was {INK},
       which is a near-white on an amber ground: legible in a screenshot
       and a smudge at the size these are actually drawn. */
    QPushButton[role="seg"]:checked {{
        border-color: {BRASS}; color: {ON_BRASS}; background: {BRASS};
    }}
    /* A segment that wants pressing, as opposed to one that merely can
       be. Brass lettering on the panel reads as an invitation where the
       usual dim grey reads as furniture -- and the checked state above
       already says "this is on" in the other direction, brass behind
       ink. So one control tells the whole story: dim when there is
       nothing to do, brass when there is, filled while it is doing it. */
    QPushButton[role="seg"][invite="true"] {{ color: {BRASS}; }}
    QPushButton[role="seg"][invite="true"]:hover {{
        color: {BRASS_LIT}; border-color: {BRASS_LIT};
    }}
    /* An inviting segment that is also on. Without this the invitation
       wins on specificity, being one selector longer, and a switch that
       is doing its job goes back to looking like one asking to be
       pressed. */
    QPushButton[role="seg"][invite="true"]:checked {{
        border-color: {BRASS}; color: {ON_BRASS}; background: {BRASS};
    }}
    QPushButton[role="seg"][invite="true"]:checked:hover {{
        background: {BRASS_LIT}; border-color: {BRASS_LIT};
        color: {ON_BRASS};
    }}
    QPushButton[role="seg"]:checked:hover {{ background: {BRASS_LIT}; }}
    QPushButton[role="step"] {{
        padding: 2px 9px; font-size: 13px; min-width: 0;
    }}

    /* Standard dialog buttons pull an icon from the *system* icon theme
       -- a black tick or cross on a desktop whose icons were drawn for a
       light one. It does not appear on every platform, which is why it
       was invisible from here: the style hint is off under the offscreen
       platform and on under GNOME and KDE. Turned off rather than
       replaced, because "Close" is already a word. */
    QDialogButtonBox {{ dialogbuttonbox-buttons-have-icons: 0; }}

    /* Text fields were never styled at all, so every one of them drew a
       bright platform-blue focus ring -- the only saturated colour in the
       application, on the one widget you look at while typing. */
    QLineEdit {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; margin: 1px;
        padding: 5px 7px; background: {SUNK}; color: {INK};
        selection-background-color: {LINE}; selection-color: {INK};
    }}
    QLineEdit:focus {{ border-color: {BRASS}; }}
    QLineEdit:disabled {{ color: {LINE}; border-color: {LINE}; }}
    QLineEdit[readOnly="true"] {{ color: {DIM}; background: transparent; }}
    /* The same box, for numbers. Left to Qt these drew in the desktop's
       colours, which under a light palette on a dark desktop was black. */
    QSpinBox, QDoubleSpinBox {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; margin: 1px;
        padding: 3px 5px; background: {SUNK}; color: {INK};
        selection-background-color: {LINE}; selection-color: {INK};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {BRASS}; }}
    QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {LINE}; border-color: {LINE}; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 14px; border: 0; background: transparent;
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url({chevron_up}); width: 9px; height: 9px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url({chevron}); width: 9px; height: 9px;
    }}

    QComboBox {{
        font-family: "{fam['mono']}";
        border: 1px solid {LINE}; border-radius: 3px; padding: 4px 6px;
        margin: 1px;
    }}
    QComboBox:focus {{ border-color: {BRASS}; }}
    /* Qt's default is a filled triangle, which was the heaviest mark on
       screen and attached to the least important control. */
    QComboBox::drop-down {{ border: 0; width: 16px; }}
    QComboBox::down-arrow {{ image: url({chevron}); width: 12px;
                             height: 12px; }}
    QComboBox::down-arrow:disabled {{ image: url({chevron_off}); }}
    QComboBox QAbstractItemView {{
        background: {PANEL}; border: 1px solid {LINE};
        selection-background-color: {LINE};
    }}
    QCheckBox {{ font-family: "{fam['mono']}"; spacing: 7px; }}
    QCheckBox::indicator {{
        width: 12px; height: 12px; border: 1px solid {LINE}; border-radius: 2px;
        margin: 1px;
    }}
    QCheckBox::indicator:checked {{ background: {BRASS}; border-color: {BRASS}; }}
    /* Radios were unstyled, which on this background left the unchosen ones
       all but invisible -- a set of options you cannot see is not a choice. */
    QRadioButton {{ font-family: "{fam['mono']}"; spacing: 7px;
                    background: transparent; }}
    QRadioButton::indicator {{
        width: 12px; height: 12px; border: 1px solid {LINE}; border-radius: 7px;
        margin: 1px;
    }}
    QRadioButton::indicator:checked {{ background: {BRASS};
                                       border-color: {BRASS}; }}
    QRadioButton::indicator:hover {{ border-color: {BRASS}; }}

    QSlider::groove:horizontal {{ height: 3px; background: {SUNK};
                                  border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {INK}; width: 11px;
                                  margin: -5px 0; border-radius: 5px; }}
    QSlider::handle:horizontal:hover {{ background: {BRASS}; }}

    /* Nothing was styled for being disabled, so a control that had gone
       dead looked exactly like one that worked. With no camera attached
       the rail sat fully lit -- sliders for an exposure that did not
       exist, toggles for a focus nothing was measuring -- and read as an
       application that was broken rather than one that was waiting. */
    QSlider::groove:horizontal:disabled {{ background: {SUNK}; }}
    QSlider::handle:horizontal:disabled {{ background: {LINE}; }}
    QPushButton:disabled {{ color: {LINE}; border-color: {LINE}; }}
    QPushButton[role="seg"]:disabled {{ color: {LINE};
                                        border-color: {LINE}; }}
    QPushButton[role="seg"]:checked:disabled {{ color: {LINE};
                                                border-color: {LINE};
                                                background: transparent; }}
    QLabel:disabled {{ color: {LINE}; }}
    QCheckBox:disabled {{ color: {LINE}; }}
    QCheckBox:disabled::indicator {{ border-color: {LINE}; }}

    /* Qt's default tooltip is black on grey, which is unreadable against a
       dark interface and looks like it belongs to a different application. */
    QToolTip {{
        background: {PANEL}; color: {INK};
        border: 1px solid {BRASS}; border-radius: 3px; margin: 1px;
        padding: 6px 8px; font-family: "{fam['sans']}"; font-size: 12px;
        opacity: 240;
    }}

    QMenu {{
        background: {PANEL}; border: 1px solid {LINE};
        padding: 4px; font-family: "{fam['mono']}"; font-size: 12px;
    }}
    QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 2px; }}
    QMenu::item:selected {{ background: {LINE}; color: {BRASS}; }}

    /* Lists of things you own -- microscopes, cameras. Without these the
       selection is the platform's own blue, which is the one colour in the
       window that belongs to somebody else's design language. */
    QListWidget {{
        background: {SUNK}; border: 1px solid {LINE}; border-radius: 3px;
        padding: 3px;
    }}
    QListWidget::item {{ padding: 5px 8px; border-radius: 2px; }}
    QListWidget::item:hover {{ color: {INK}; }}
    QListWidget::item:selected {{ background: {LINE}; color: {BRASS}; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}

    QPushButton[role="menu"] {{
        border: 0; margin: 0; padding: 5px 10px; color: {DIM};
        font-family: "{fam['mono']}"; font-size: 12px;
    }}
    QPushButton[role="menu"]:hover {{ color: {INK}; }}

    /* The name sits in brass and goes white under the pointer, rather
       than the other way about: it is the one place the application is
       allowed to be a little proud of itself, and brass is the accent
       chosen precisely because it never appears in a micrograph. */
    QPushButton[role="wordmark"] {{
        border: 0; margin: 0; padding: 4px 10px; color: {BRASS};
        font-family: "{fam['display']}"; font-size: 17px;
        letter-spacing: 0.3px;
    }}
    QPushButton[role="wordmark"]:hover {{ color: {INK}; }}
    """
