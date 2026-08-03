# What the window frames still need a real machine for

This program draws its own title bar on Windows and Linux, and restyles
the native one on macOS. Most of that is a pure function of rectangles
and is tested on whatever machine happens to be to hand. The rest
reaches past Qt into the platform, and there is no honest way to test it
from anywhere else.

This is the list of what is unverified, what to do about each, and what
"wrong" looks like -- written so somebody with the right laptop can work
through it without having to reconstruct why any of it matters.

The frame can always be turned off: `Setup > Styled window frame`, or
`DARLASTON_NATIVE_FRAME=1` in the environment before starting, which
wins over the setting and is the way back if the window will not come up
at all.

---

## Windows

### 1. Does the maximised window fit the screen? (highest value)

The one that was most wrong, and the one that shows up on ordinary
hardware rather than unusual hardware.

Windows grows a maximised window's rectangle by the resize border on
every side, expecting a frame to absorb the overhang. There is no frame,
so the overhang is pulled back by hand -- and the amount depends on the
display's real DPI, which Qt does not report. It used to ask Qt and got
the 96-DPI answer on every display, so a scaled display overhung by the
difference.

**Do this:** set the display to 100%, maximise, and look at all four
edges. Then 125%, 150%, 175%. Then drag the window to a second monitor
at a *different* scale factor and maximise there.

**Wrong looks like:** the top of the toolbar cut off, the wordmark
clipped, the close button running past the right-hand edge of the
screen, or a hairline of desktop showing where the window should meet
the edge.

### 2. Do the caption buttons let go?

Two separate stuck states, both fixed blind.

**Hover:** put the pointer on the close button so it lights red, then
move straight down into the video. Then off the top edge of the screen.
Then off the right edge with the window maximised.
*Wrong looks like:* it stays red.

**Press:** press and hold on close, drag down into the video, release.
*Wrong looks like:* the button stays drawn pressed, or the window
closes.

Then the same drag *back* onto the button before releasing -- that
should close it, because the press was never cancelled.

### 3. Snap layouts

Rest the pointer on the maximise button for about a second. The Windows
11 flyout with the layout choices should appear. Then click one.

**Known limits, both measured rather than guessed:**

- The top few pixels of the button report the top resize edge rather
  than the button, so the flyout will not appear there. Deliberate: it
  is what a standard window does, where the caption buttons sit below
  the frame instead of at the top of the client area, and the
  alternative is a top edge that cannot be grabbed above three buttons.
- **The window's minimum width is 766 px, and Microsoft's limit for
  snap layouts is 500.** So the half-screen layouts work and the third-
  and quarter-width ones will invoke and then fail to snap. The 766 is
  the live view's 480 px minimum plus the rail's 286; both are design
  decisions rather than accidents, so this is written down rather than
  quietly changed. Worth deciding once someone has seen it happen.

### 4. Auto-hiding taskbar

Set the taskbar to auto-hide. Maximise. The taskbar must still reveal
itself when the pointer reaches its edge. Repeat with the taskbar moved
to the left, and to the top.

Two pixels of desktop are left showing along that edge for it, which is
Chromium's shipped figure for the same job; the real threshold is
undocumented. If the taskbar does not appear, the number is too small
and the place to change it is `AUTOHIDE_GAP` in `darlaston/ui/frame.py`.

### 5. Multiple monitors, and negative coordinates

A monitor placed to the *left* of the primary one gives negative screen
coordinates. That decode is now a tested pure function, but the path
that reaches it is not.

**Do this:** put a second monitor to the left of the primary in display
settings. Drag the window there. Check that the caption buttons still
highlight, the edges still resize, and maximising still fits.

### 6. Window drift while dragging

[QTBUG-117704](https://bugreports.qt.io/browse/QTBUG-117704) reports a
window with a custom `WM_NCCALCSIZE` handler getting "squished to the
left when moving" -- from a `calculateFullFrameMargins` call added to
Qt's own geometry handling in 6.5.3. The ticket could not be read; the
Jira instance served an empty page.

**Do this:** drag the window slowly across the screen and watch for
horizontal drift or a width change. Ten minutes, and it is the one
outstanding question about whether Qt fights the handler at all.

### 7. The DWM margin -- an open question, not a known bug

`DwmExtendFrameIntoClientArea(hwnd, MARGINS(0, 0, 0, 1))` extends one
pixel at the *bottom*. The reason given for the call is that it keeps
the drop shadow, and that reason is folklore: the documentation never
mentions the shadow, and the window styles are never changed here, so
the shadow may well be kept regardless.

**Do this, in order:**

1. Comment the call out. Is the shadow still there? If so it was never
   load-bearing and it can go.
2. If the shadow does go, try `MARGINS(0, 0, 1, 0)` -- the top. That is
   the edge `WM_NCCALCSIZE` actually ate, it is the one Windows 11 draws
   in the active-window colour, and it is what most implementations of
   this use.

Left alone until somebody can look at it, because both are one pixel and
guessing between them from here is not engineering.

### 8. The frame colour

Windows 11 draws a thin DWM border around the window in the *system*
light or dark setting, not the program's. That is asked to be dark now,
on the takeover path as well as the fallback path. **Do this:** set
Windows to light mode. The hairline around the window should stay dark.

### 9. Windows 10

Everything above assumes 11. On 10: no snap layouts flyout (there is no
such feature), square corners rather than rounded (the rounding request
fails harmlessly), and the DWM dark attribute is 20 from build 18985 and
19 on the builds just before it -- both are tried.

---

## macOS

The implementation changed completely after a review found it was
broken in a way that only shows up after the first fullscreen
transition. It has not been run on a Mac since.

### 10. The title bar, and then fullscreen

**Do this:** the grey strip saying "darlaston" should be gone, with the
toolbar in its place and the traffic lights floating over the top left
of it. Then **press the green button, and come back out.**

That second step is the whole test. The old implementation set the style
mask through AppKit by hand, and on Qt 6.9 and later
`QCocoaWindow::setWindowFlags` reassigns both the full-size-content-view
bit and the transparent-title-bar flag from Qt's own window flags --
which both fullscreen handlers call. It would have worked at launch and
then, on the first green button, come back with an opaque title bar and
no title text in it, permanently.

*Wrong looks like:* a grey bar reappearing above the toolbar, or the
toolbar content sitting below a band rather than under it.

### 11. The traffic-light inset

The toolbar starts 78 pt in so the wordmark is not underneath the
lights. That number is a measurement, not a specification: Apple
publishes no figure for it, in the guidelines or the headers, and moved
the buttons in Big Sur. Measured extent runs 70 to 75 across Big Sur
through Sequoia, so it errs wide.

**Do this:** check the gap looks deliberate rather than accidental, and
that the wordmark is clear of the buttons with air to spare. Then go
fullscreen: the lights move into the overlay that slides down with the
menu bar, and the inset should go with them rather than leaving a hole.

**And:** the lights are vertically centred against the *system* title
bar, which is 28 pt, while the toolbar is 36. They will sit a few points
above the toolbar's own centre line. Whether that reads as wrong is a
judgement nobody can make from a description.

### 12. The hairline

macOS 11 and later can draw a separator between the title bar and the
content, and its default is "automatic". If a hairline appears across
the toolbar, that is `titlebarSeparatorStyle` and it wants setting to
`None`. Not done pre-emptively: it is one more Objective-C call that
cannot be tested from here, and it may well not draw at all with the
title bar transparent.

### 13. Modal dialogs

Qt destroys and recreates the `NSWindow` when a window's modality
changes, and everything set on the old one goes with it. Opening a
modal dialog *on the main window* is the case.

**Do this:** open Setup > Microscopes, close it, and check the title bar
is still gone.

---

## Linux

Testable here, and tested -- but only under one compositor.

### 14. The other desktops

The rule: KDE and the tiling window managers keep their own decorations,
everything else gets ours. GNOME under Wayland is the case that matters,
because Qt's fallback decoration there matches neither GNOME nor this
program.

**Do this, under GNOME Wayland, KDE Wayland, and an X11 session:** drag
by the bar, double-click it to maximise, grab each edge and each corner,
and check the cursor changes at each. Dragging and resizing are handed
to the compositor rather than done by hand -- a Wayland client cannot
position itself at all -- so if any of it fails it fails at that seam.

### 15. The shadow

A frameless window on GNOME Wayland gets no drop shadow, because the
shadow was the compositor's and it belonged to the decoration. It will
read flat against a dark background. Not fixed: drawing our own means a
translucent margin around the whole window and a redraw cost on a
program showing a live camera preview, which is a trade worth making
deliberately or not at all.
