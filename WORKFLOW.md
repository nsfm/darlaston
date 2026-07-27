# Session walkthrough

A strawman. One real capture session, cold scope to finished image, written so
it can be corrected rather than admired. Requirements derive from this; the data
model derives from requirements; architecture derives from the data model.

**?** marks a guess that needs Nate's answer. **!** marks a decision that shapes
the architecture.

---

## 0. Cold start

App launches, enumerates cameras. On first run it has no ToupTek SDK, so it
offers to fetch one (§4c: we never redistribute the blob, but one click is a
better experience than a README instruction).

**Setup profile** — the slow-changing physical stack:

```
microscope   Zeiss Universal
objective    <turret position + spec>
optovar      1x / 1.25x / 1.6x / 2x
relay        AmScope 1-2x C-mount  |  planar relay eyepiece
camera       E3ISPM20000KPA
```

Changes rarely. Selected once and remembered.

**? How often does the relay actually change?** If it's a semi-permanent choice,
setup profiles are a settings screen. If you swap between the AmScope and a
planar relay within one session, it needs to be a first-class switch.

---

## 1. Illumination

Pick brightfield / darkfield / phase. This changes *often* — several times per
session.

**Everything downstream keys on this.** §6b measured brightfield falling off 15%
toward the edges while phase rises 30%, through identical optics, so a flat
cannot be shared between modes. Switching illumination invalidates the flat.

Brightfield has a sub-mode: **inverted**, which Nate uses to synthesise a clean
darkfield look. That is a develop-time choice, not a capture one — so it belongs
in Darktable, not here. **!** Unless it should be recorded as session metadata
so the intent survives to the developer.

---

## 2. Calibration gate

Before the app will let a real capture start, it checks what it has:

| Product | Valid for | Expires when |
|---|---|---|
| **Dark** | exposure × gain × sensor temperature | exposure or gain changes; temperature drifts |
| **Flat** | objective × relay × illumination × **the actual slide** | any of those change |
| **White balance** | derived from the flat | flat changes |

The slide dependency is the awkward one and it is real: in phase contrast the
condenser annulus and objective phase ring are conjugate through the specimen
plane, so glass thickness, coverslip and mountant all participate. Today's flat
was shot with no slide at all and its illumination gradient did not match the
subject frame's.

**Guided calibration**, when something is missing:

- *"Cap the camera or kill the lamp"* → shoots a dark. Reports black level and
  read noise. Today: 1.04 DN and 0.20 DN.
- *"Move to an empty area of this slide"* → shoots N frames at different empty
  positions, medians them. Debris on the slide moves and cancels; sensor dust
  and the illumination field do not, which is exactly the separation wanted.
- White balance falls out of the flat automatically. Today's measurement:
  R 1.4882 / G 1.0 / B 2.9559.

**? How many blank positions is reasonable to ask for?** Four is probably enough
statistically. Six is better. Ten is annoying.

**!** The gate should be a nag, not a wall. Sometimes the light is right and the
diatom is beautiful and calibration can wait. Capture anyway, flag the session
as uncalibrated, offer to shoot the flat afterwards while nothing has moved.

---

## 3. Framing and exposure

Live view, binned (§4b: full-res raw exceeds the USB3 budget, so live view runs
at index 1 or 2 and full resolution is pulled on trigger only).

On screen:

- **histogram with a clipping indicator.** Non-negotiable. ToupLite was blowing
  62% of the frame and giving no sign of it.
- exposure and gain, manual, never auto — auto-exposure makes frames
  incomparable, which breaks stacking and mosaicking both.
- **focus assist** (§9): live metric trace, not a bare number. Vollath F4 for
  darkfield and phase, normalised variance or LAPV for brightfield. Prefilter
  switches too — top-hat for darkfield, median for brightfield.
- **focus peaking overlay**, computed on the display buffer at ~34 fps measured.
- **? audio focus tone.** Nobody in the surveyed field does this, and you will be
  looking down the tube with both hands on the knobs. Worth trying, easy to
  remove.

---

## 4. Choosing what to capture

Four shapes, and the app should not pretend they are one thing:

```
A  single frame                      one shutter
B  Z-stack                           one XY, many Z          -> one EDF image
C  mosaic                            many XY, one Z          -> one panorama
D  mosaic of Z-stacks                many XY, many Z         -> the real goal
```

**D is the thing that currently costs hours in GIMP**, and it is the reason the
project exists.

---

## 5. Z-stack at one position

Z is hand-cranked and stays that way (§9b). So the app's job is to make a manual
sweep repeatable:

1. Rack down until sharpness clearly falls. App records the peak it saw.
2. Rack up through focus until sharpness falls again.
3. **Focus coverage** (§9) accumulates the union of in-focus masks and reports
   *"94% of the central crop has been in focus at some Z"*, painting what has
   not. You stop at 100%, not when you feel finished.
4. **App auto-triggers on detected Z movement.** *(decided)* Hands never leave
   the fine focus.

   The obvious risk is that vibration and stage bumps also look like movement.
   The discriminator is free, because both signals are already being computed
   every frame:

   ```
   sharpness changed, |XY shift| ~ 0   -> fine focus turned.   capture.
   |XY shift| non-zero                 -> stage moved/bumped.  do not.
   ```

   Phase correlation gives the XY term, the focus metric gives the sharpness
   term. Z motion is a sharpness change with no translation; anything else is
   not Z.

   **Settle detection is required.** Fire *after* motion stops, not during — a
   rolling shutter plus a moving stage is a smeared frame, and on a hand crank
   there is always a little motion. Detect → wait for the metric to go quiet for
   K frames → capture. Costs a fraction of a second per slice and saves a stack
   full of soft frames.

Each slice may itself be **N frames averaged** for SNR (§3: the subject does not
move, so √N applies; 40 frames beats the A6700's per-pixel advantage outright).

**? Is frame averaging worth the time cost at capture, or better as an option
for hero shots only?**

---

## 6. Mosaic

1. **Overview frame first.** Drop the Optovar to 1x, capture the whole
   arrangement in one shot. This becomes the literal minimap — you watch a
   rectangle move across a picture of your actual subject, not an abstract grid.
   The Optovar makes this nearly free because magnification changes without the
   optical axis moving.
2. Drive the stage by hand. The app phase-correlates the live frame against the
   overview and against the last captured tile, and shows:
   - position on the overview
   - live overlap %, green at target, red when about to leave a gap
   - captured tiles shaded, gaps obvious
   - **cellSens's two good ideas**: semi-transparent overlay of the previous
     tile so you can align structures by eye, and **undo-last-tile**
3. At each tile, run §5.

**!** §10a measured that phase-correlation *confidence cannot detect a wrong
match* — an overlap containing different objects reported 0.80 against 0.91 for
a true match. Only a position constraint catches that. The overview frame is
that constraint, which is why it is load-bearing rather than a nicety.

**Target: 12-40 tiles.** *(decided)* That size means coverage tracking, gap
detection and undo all have to be genuinely good rather than decorative, and a
session must survive an interruption -- 40 tiles is half an hour at the
eyepiece.

---

## 7. Processing

Per tile: calibrate (dark, flat, per-Bayer-phase normalised) → align the Z stack
→ focus stack to one EDF tile. Then mosaic the EDF tiles, seeded with the
capture-time positions.

Stack-then-stitch, for the reasons in §8.

**Output formats** *(decided, and verified end to end)*

| Output | Format | Size | Verified |
|---|---|---|---|
| Single frame | **Bayer DNG** | 39.7 MB | Darktable 5.4.1 opens it, correct orientation, no CFA artefacts |
| Z-stack / mosaic | **linear DNG** | 119 MB | same — `PhotometricInterpretation = Linear_Raw`, 3 samples |

Linear DNG keeps stacked and stitched results inside Darktable's raw pipeline
with white balance still live and non-destructive, even though the data is
already demosaiced.

**Compression is not available.** pidng writes it, Darktable cannot read it:

```
compressed linear   rawspeed: "Component count should be no less than sample count (1 vs 3)"
compressed bayer    rawspeed: "Unsupported predictor mode: 6"
```

pidng's LJPEG encoder emits a single-component stream regardless of
`SamplesPerPixel`, and uses predictor 6, which rawspeed does not implement. If
size becomes painful later, `imagecodecs` (BSD-3) writes standard-predictor
lossless JPEG and would be the route. For now: uncompressed.

### Storage, for a 12–40 tile session

```
raw Bayer slice           39.7 MB
30 slices x 40 tiles       47.6 GB    <- if every slice is kept
one tile's slices           1.2 GB    <- working set, if streamed
40 linear DNG tiles          4.8 GB    <- the deliverable
```

**So tiles are processed as they are captured** — slices stack to an EDF tile
immediately, then are kept or discarded. "Keep raw slices" is a toggle, default
off, **with the disk estimate shown before the session starts** rather than
discovered at 80% full.

---

## 8. What the app never does

- **Develop.** Tone, colour, local contrast, sharpening — Darktable's, entirely.
- **Bake decisions.** White balance, black point and orientation are recorded,
  not applied irreversibly.
- **Require modification of the microscope.** Manual stage, hand-cranked focus,
  on any ordinary scope. Motorisation stays an optional accelerator.

---

## Open questions, collected

**Decided:**

- Z slices auto-trigger on detected movement, discriminated from stage bumps by
  phase correlation, gated on settle.
- Mosaics target 12-40 tiles, so sessions must be resumable.
- Bayer DNG for single frames, linear DNG for composites. Uncompressed.

**Still open:**

1. How often does the relay optic actually change within a session?
2. How many blank-field positions is reasonable to ask for? (Four is probably
   enough statistically, six better, ten annoying.)
3. Is frame averaging worth the capture-time cost routinely, or hero shots only?
4. Does a session mean one subject, or several in a sitting?
5. Should inverted-brightfield intent be recorded as session metadata, given the
   inversion itself belongs to Darktable?
