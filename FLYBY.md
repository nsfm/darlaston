# Flyby

Design notes for turning `process/flythrough.py` from a party trick into
a tool. Nothing here is built beyond what that file already does; this
exists so the thinking is not lost between now and picking it up.

---

## What it is

A stitched mosaic of stacks is a **four-dimensional recording**: x, y,
zoom, and focal plane. Every move through it can be perfectly smooth,
because it is synthesised rather than performed -- no stage drift, no
hand tremor, no hunting for focus. And it is repeatable, which nothing
done by hand at the eyepiece ever is: the same move can be re-rendered
after a re-stitch, at a different length, or with one mark nudged.

That is the feature. Not "a video of a mosaic" but **virtual camera work
on a slide**, offered because darlaston keeps both halves -- the mosaic
and the depth -- and nothing else does.

The shipped version picks three subjects by itself and pulls out. That
stays: it is the thing that works on the first mosaic anybody ever
shoots, with nothing to configure. Everything below is the path taken
when someone wants control, and none of it may become a thing you *must*
do to get a video.

## The problem that makes this big

A flyby can only be planned over a mosaic that already exists, and the
mosaic that should exist depends on the flyby. Shoot first and the path
is constrained by whatever happens to have been captured; plan first and
there is nothing to plan against.

**Nate's answer, and it is the right one: plan over the live slide map
before capturing.** Sketch the path on the preview, and the application
answers with the minimum set of fields that path needs, how long that is
to shoot, and how much disk it will take -- *then* capture, stitch, and
render. The plan is the thing that survives all three stages.

That turns a rendering feature into a capture feature, which is why it
is filed here rather than started.

Consequences worth noting now:

* The slide map already knows where the stage is and where fields have
  been banked, so it is the surface this belongs on. It is also already
  the crowded part of the window.
* A path implies coverage, and coverage implies overlap, so the estimate
  can reuse the mosaic guidance that already exists.
* The estimate must be honest about stacks: a corridor of twenty fields
  is a different proposition at one exposure each than at thirty.
* Capture order should follow the path, so an interrupted session leaves
  a usable prefix rather than a scatter.

## Marks

A shot is a list of marks. Each carries:

| | |
|---|---|
| where | position on the mosaic |
| how wide | the field the frame covers, never tighter than one output pixel per source pixel |
| what is sharp | focal plane, where a depth map exists |
| hold | seconds to sit still |
| travel | seconds to arrive from the previous mark |

The renderer already interpolates position and zoom, eased, with zoom
interpolated geometrically. Focus is one more channel through the same
machinery. `refocus` was made fast enough for this in 477bfc3 -- 342 ms a
frame to 165 -- because the view changes every
frame and blur layers cannot be reused across them.

## Where marks come from

Three options, cheapest first. The order matters: each one tells us
whether the next is worth building.

**1. The pins that already exist.** Fields get pinned on the slide map
while working. Those are positions on the mosaic, chosen by a person,
for the reason a person cared. *"Fly through my pins, in order"* needs
no new interface at all, and it reuses a gesture already being made.
Build this first, and find out whether an editor is wanted before
building one.

**2. Auto, as now.** Structure-seeking, three stops, a reveal. The
floor, and the thing that must keep working with nothing set.

**3. An editor.** Show the composite, click to drop marks, scroll for
zoom, a slider for focus, drag to reorder, scrub a preview. Real work
and probably eventually right, but only after (1) has shown what people
actually reach for.

**Not** a keyframe timeline with curve editors. The moment this needs
one it has stopped being darlaston and become a video editor that
happens to open DNGs.

## Shot shape should follow mosaic shape

The current film always ends by pulling out to reveal everything, and
pads the canvas to 16:9 to do it. On a long thin mosaic -- a corridor
between two subjects, which is a shape people will deliberately shoot --
that ending is a sliver in a sea of mountant.

The fix is not a setting. Let the mosaic's own aspect pick the default:

* compact: the reveal, as now
* elongated: a **tracking shot** along the long axis, holding at each end
* and refuse to pad beyond about a third of the frame -- past that,
  travel rather than pull out

## How honest the focus is

Synthetic defocus from a depth map softens but does not **occlude**: a
blurred foreground should bleed over what sits behind it, and ours
merely goes soft. Out-of-focus highlights do not bloom the way real ones
do. `process/aperture.py` says so in its own docstring and it is the
same limit the parallax renders carry.

For a gentle pull through a subject this reads convincingly. For a
dramatic rack it may read as *a blur* rather than as a microscope.

**The ceiling, if it ever matters enough:** with `keep_slices` on, the
real optical defocus exists at every plane already, and a rack could
sample actual slices rather than synthesising. That is correct rather
than convincing. It costs disk -- a mosaic of stacks at thirty slices is
tens of gigabytes -- so it would be an opt-in for a hero piece, never
the default.

## Order

1. Marks as a structure, plus the focus channel. Small, and unlocks the
   rest.
2. Fly through the pins. No new interface; tells us whether (4) is
   wanted.
3. Shot shape follows mosaic shape. Fixes the corridor.
4. Planning mode over the slide map: draw the path, get the minimum
   coverage and an honest size estimate, then shoot it. The big one, and
   the one that makes the rest coherent.
5. A visual mark editor, if 2 and 3 have shown it earns its place.
