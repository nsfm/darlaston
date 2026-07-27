# Data model and core mechanisms

Derived from [WORKFLOW.md](WORKFLOW.md). Architecture derives from this.

---

## 1. The entity model

The shape follows the physical reality, including the awkward part: the camera
and its relay travel together between microscopes, while objectives belong to
the scope.

```
CameraUnit                          identified by camera serial
  camera        model, serial, sensor, resolutions, max bit depth
  relay         AmScope 1-2x C-mount | planar relay eyepiece | ...

Scope
  name          "Zeiss Universal" | "inverted" | "stereo"
  objectives    [ {position, magnification, NA, type, immersion}, ... ]
  optovar       [1.0, 1.25, 1.6, 2.0] or absent
  illuminations [brightfield, darkfield, phase, ...]

Setup = CameraUnit x Scope
  the thing confirmed at launch: "yes, this is your Zeiss Universal"
```

**Why the relay sits with the camera, not the scope:** it rarely changes and
stays bolted to the camera when the camera moves. For a typical user this whole
layer is invisible — one camera, one scope, auto-matched by serial, never
thought about again.

**Serial-based identification** makes the confirmation honest rather than
decorative. `Toupcam_get_SerialNumber` gives us a unique key. One camera on one
scope auto-selects silently. One camera across four scopes gets a chip that says
which scope it thinks it's on, and a way to say no.

---

## 2. Calibration products have different lifetimes

This is the whole reason the combinatorics feel unmanageable, and separating
them is what makes it tractable.

| Product | Keyed on | Invalidated by | Typical lifetime |
|---|---|---|---|
| **Dark** | exposure × gain × sensor temperature | exposure/gain change, temperature drift | hours; reusable across optics entirely |
| **Flat** | objective × relay × illumination × **slide** | any of those | one slide, one sitting |
| **White balance** | illumination (lamp colour temp) | lamp power, illumination mode | long — barely depends on objective |
| **Defect map** | sensor | never, practically | permanent |

**The dark and the white balance are cheap and broad. Only the flat is
genuinely combinatorial** — and it's needed only for the combination about to be
shot, never for the full cross product.

Five objectives × three illuminations is fifteen flats *in principle*. In
practice you shoot one subject with one objective under one illumination, so you
need one.

---

## 3. Opportunistic flat capture

The feature that removes the wizard from most sessions.

While hunting for a subject, most of the time is spent crossing empty slide. The
app watches the live stream and, when a frame looks blank, banks it against the
current (objective × relay × illumination × slide):

```
blank if:   low high-pass structure
            no strong connected features
            reasonable mean level (not clipped, not black)
            XY position differs from previously banked frames
```

That last condition matters — four frames of the *same* empty patch don't median
away slide debris. Positions come from the tracker we already run.

By the time a flat is wanted, four are often already in hand and nothing was
asked of the user. When they aren't, the wizard is a 30-second prompt for four
positions, with "give me more" available for anyone feeling thorough.

**The gate nags, never blocks.** Sometimes the light is right and the diatom is
beautiful. Capture, flag the session uncalibrated, and offer to shoot the flat
afterwards while nothing has moved.

---

## 4. Exposure handoff

Gain is a multiplier applied after the photons are collected; exposure collects
more photons. Same brightness, different noise.

Observed behaviour to automate: gain gets pushed up so the live view stays
usable while hunting, then exposure gets pushed up once the shot is framed so
the capture isn't grainy. Walking a slide at 100 ms — let alone 1 s — is
miserable.

```
live:      E_live x G_live
capture:   E_capture = E_live x G_live,  G_capture = 1.0
```

Identical mean level, `G_live`× the photons, `sqrt(G_live)`× the SNR — better in
practice, since gain amplifies read noise along with signal.

**Verified, not assumed:** after the first capture at the new setting, compare
its histogram against the live view's. Correct once if they disagree. Constraints
are the exposure ceiling (15 s on this camera) and not clipping.

**Applied at mode boundaries, not per frame.** Switching exposure costs a few
hundred milliseconds to take effect; doing it thirty times in a Z stack is
wasteful.

```
hunting / framing        fast live view, gain high, exposure short
stack or tile started    switch once to low gain, long exposure, stay there
```

Which is exactly the manual behaviour, moved to the boundary where it belongs.

---

## 5. Session structure

A sitting contains several subjects. A subject may be a single frame, a stack, a
mosaic, or a mosaic of stacks.

```
Session
  setup, slide, date, notes
  calibration/   dark, flat(s), white balance, defect map
  subjects/
    Subject
      illumination, objective, optovar, exposure, gain
      overview frame          (mosaic only, the literal minimap)
      tiles/
        Tile
          position, overlap, capture-time XY from the tracker
          slices/             raw Bayer, kept or discarded per setting
          edf                 stacked result
      result                  linear DNG
```

Sessions must be **resumable** — 40 tiles is half an hour at the eyepiece, and
interruptions are certain.

Every artefact carries a manifest recording what has been applied to it
(orientation, dark, flat, white balance), the lesson from
`rawio.py`: a file whose history has to be inferred is a file that will
eventually be wrong.

---

## 6. Inverted brightfield as a first-class mode

Common in micro art, and colour at this scale is invented anyway.

**Live view: native.** A display transform, cheap, and it changes what you frame
because you're judging the final look while you shoot.

**Raw: untouched.** Inverting destroys linearity and with it the whole point of
raw. The DNG stays linear positive.

**Export: an option.** Recorded as session intent so it survives to whichever
developer the user reaches for — a JPEG export honours it, and a raw export
carries it as metadata rather than baked pixels.

---

## 7. Scope: v1 and v2

**v1 — capture, calibrate, export.** Development belongs to Darktable. This is
the workflow that already works and that the project has proven.

**v2 — gallery and basic develop.** Most microscopists are not raw
photographers. They want a JPEG and sliders in the app, not a second program
with a learning curve. So `develop.py`'s primitives — white balance by colour
temperature via the SDK's own `TempTint2Gain`, black point, gamma — stay as the
seed of an in-app develop view rather than being discarded.

Not built until the capture flow is live, but the pieces are kept warm.
