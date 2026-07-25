# Discovery: a photomicrography capture tool

Working notes toward a capture + calibration + mosaic application for
photomicrography on Linux. Target subject: large diatom arrangements.
Target license: GPLv3.

Status: pre-implementation. Everything in "Hardware ground truth" is measured.
Everything else is design.

---

## 1. Why

The only Linux software that drives the camera is ToupTek's **ToupLite**, and it
has five specific problems:

1. Dated UI, poor HiDPI handling, capture button buried.
2. Subjects need panoramic stitching, and general-purpose panorama software
   copes badly with the optics and the subject matter.
3. No RAW output.
4. Focus stacking and stitching don't compose. You get one or the other, or you
   hand-composite in GIMP.
5. Exposure/gain feel less controllable than shutter/ISO on a Sony A6700.

Findings below address all five. Four are solvable and one (#5) turns out to be
two separate causes, both fixable.

---

## 2. Hardware ground truth

Measured directly via `libtoupcam.so`, not from spec sheets.

**Camera:** ToupTek E3ISPM20000KPA
**Sensor:** Sony IMX183, 1", colour, rolling shutter
**Serial:** TP211207110153E2F7CE016F254B9C0 (produced 2021-12-07)
**Firmware:** 3.5.5.20210621 · **Hardware:** 3.0 · **FPGA:** 5.2 · **Rev:** 2

| Property | Value |
|---|---|
| Max bit depth | **12** |
| Raw Bayer pattern | **GBRG** |
| Full resolution | 5440 × 3648 (19.85 MP), 2.40 µm pixels |
| Binned 2×2 | 2736 × 1824 (4.99 MP), 4.80 µm |
| Binned 3×3 | 1824 × 1216 (2.22 MP), 7.20 µm |
| Sensor size | 13.06 × 8.76 mm |
| Exposure range | 264 µs – 15 s |
| Analog gain | 100 % – 5000 % (1× – 50×) |
| USB | USB 3.0, negotiated 5000 Mbps |

### Correction: this is not the A6700 sensor

Earlier assumption was that the E3ISPM shares the A6700's sensor. It does not.

| | E3ISPM20000KPA | Sony A6700 |
|---|---|---|
| Sensor | IMX183, 1" | APS-C |
| Area | 13.06 × 8.76 mm (114 mm²) | ~23.5 × 15.6 mm (367 mm²) |
| Pixel pitch | 2.40 µm | ~3.76 µm |

**3.2× the sensor area, ~2.45× the light per pixel** in favour of the A6700.
This is half of complaint #5 and it is real physics, not imagination.

The other half is section 3.

---

## 3. The ISP is cooking every image

The E3ISPM has a 12-bit hardware ISP doing demosaic, auto-exposure, white
balance, chroma and saturation on-camera. ToupLite displays the output of that
pipeline. Current state as probed:

```
LINEAR      = 1     tone linearisation active
CURVE       = 2     tone curve active
COLORMATIX  = 1     colour matrix active
RAW         = 0     ISP path, not raw
BITDEPTH    = 0     8-bit output
```

So ToupLite hands you an 8-bit, tone-curved, colour-matrixed, demosaiced image.
That is the microscopy equivalent of camera JPEG. Compare against developing an
A6700 raw yourself and of course it feels like less control — it *is* less
control.

**Every stage is individually defeatable**, and raw mode works:

```
RAW:      0 -> set 1 -> reads 1    ** accepted **
BITDEPTH: 0 -> set 1 -> reads 1    ** accepted **
```

**Complaint #3 is resolved: the camera does 12-bit raw. ToupLite hides it.**

### Bandwidth consequence

5440 × 3648 × 2 bytes = **39.7 MB/frame**. At the rated 15 fps that is
**~595 MB/s**, against a real-world USB 3.0 Gen 1 ceiling of ~400 MB/s.

Implications:
- Full-resolution 12-bit raw will sustain roughly 8–10 fps, not 15.
- **Live view must run binned** (resolution index 1 or 2, 8-bit RGB). Full-res
  raw is pulled on trigger only. This is an architectural constraint, not a
  tuning knob.
- Running near bus saturation is why marginal cables fail under jostling.

### Why raw should let this camera beat the A6700 here

The subject does not move. Capture N frames at fixed Z and average:
SNR improves by √N. 40 frames ≈ **6.3×**, which comfortably exceeds the 2.45×
per-pixel light deficit. `Toupcam_SnapN` does burst capture natively.

The A6700 wins single-shot. It cannot win 40-shot, and 40-shot is impractical
with SD-card juggling. This is the whole argument for the industrial camera.

---

## 4. The SDK is much better than the app on top of it

`libtoupcam.so` exports **180 functions**. Much of the calibration pipeline this
project needs already exists:

| Function | What it gives us |
|---|---|
| `Toupcam_calc_ClarityFactor` | **Focus metric.** Live focus assist is one call. |
| `Toupcam_FfcOnce` / `FfcExport` / `FfcImport` | **Flat-field correction** with save/load — per-objective calibration files |
| `Toupcam_DfcOnce` / `DfcExport` / `DfcImport` | **Dark-field correction**, same |
| `Toupcam_SnapN` | Burst capture → frame averaging |
| `Toupcam_Trigger` | Software trigger; deterministic capture |
| `Toupcam_put_Linear` / `put_Curve` / `put_ColorMatrix` | Defeat each ISP stage |
| `Toupcam_put_Demosaic` / `deBayerV2` | Supply our own demosaic |
| `Toupcam_put_Roi` | Hardware ROI |
| `Toupcam_put_VignetEnable` / `VignetAmountInt` / `VignetMidPointInt` | Vignetting controls |
| `Toupcam_HotPlug` / `Toupcam_Replug` | **Disconnect/reconnect handling** |
| `Toupcam_put_Speed` / `get_MaxSpeed` | Throttle USB bandwidth for stability |
| `Toupcam_GetHistogram`, `get_RealExpoTime`, `get_Temperature` | Instrumentation |
| `Toupcam_read_UART` / `write_UART`, `read_Pipe`, EEPROM | Spare serial I/O channel |
| `Toupcam_ST4PlusGuide` | Astro autoguider port (unused here) |

**We are not writing a camera driver.** We are writing a UI and an orchestration
layer over an SDK that already does calibration, bursts, triggering and focus
scoring.

### Two failure codes, two meanings

Unsupported options came back with two distinct codes, and the distinction is
actionable:

- **`0x80004001` (E_NOTIMPL)** — SDK knows the option, hardware does not
  implement it. `CG`, `TEC`, `TECTARGET`, `HEAT`. Never going to work.
  - No dual conversion gain on this sensor/firmware.
  - **No TEC.** Dark current is uncontrolled, so dark frames must be matched on
    exposure time *and* ambient temperature. Re-shoot darks per session.
- **`0x80070057` (E_INVALIDARG)** — this 2021-vintage SDK doesn't recognise the
  constant at all. `ISP`, `FPNC`, `FLUSH`, `HIGH_FULLWELL`.
  - **A newer libtoupcam may expose these.** `ISP` (full hardware ISP bypass)
    and `FPNC` (fixed pattern noise correction) are both worth having.
  - **Confirmed.** Re-probed the same camera against SDK `59.30594.20260120`:

    ```
    ISP    0x5f  = 0     now supported
    FPNC   0x67  = 0     now supported
    ```

    The SDK update alone unlocks both. `ISP` means the hardware pipeline can be
    bypassed outright rather than defeated stage by stage — a cleaner raw path
    than `LINEAR`/`CURVE`/`COLORMATIX` individually.

    `HIGH_FULLWELL` moved from `E_INVALIDARG` to `E_NOTIMPL`: the newer SDK
    knows the option, this sensor does not have it. `FLUSH` remains unknown.

## 4b. The current SDK (59.30594.20260120)

Downloaded from ToupTek. Installed version is `50.19728.20211022` — nine major
versions behind, and older than the vendor's own "legacy" build.

**217 exported functions, up from 162.** Ships `python/toupcam.py` (official
bindings) and `python/samples/qt6.py` — the exact stack proposed in §10.

New since 2021 that matter here:

| New function | Significance |
|---|---|
| `put_Binning` / `get_BinningMethod` / `get_BinningValue` | **Settles the binning question directly** |
| `get_PixelFormatSupport` / `get_PixelFormatName` | Enumerates raw formats rather than inferring them |
| `FpncOnce` / `FpncExport` / `FpncImport` | Fixed-pattern-noise correction, now real functions |
| `get_FocusMotor` / `put_AFMode` / `put_AFFMPos` / `AAF` | **Focus motor control** |
| `calc_ClarityFactorV2` | Improved focus metric |
| `export_Cfg` | Vendor-provided config save/load |
| `PullImageV3` / `V4`, `StartPushModeV4` | Newer acquisition paths |
| `log_File` / `log_Level` | Logging — useful for diagnosing USB dropouts |

**Focus motor control is a significant find.** The XY stage is manual and will
stay manual, but if a compatible focus motor fits this scope then **Z becomes
motorized while XY stays hand-driven** — which is the right split. Z-sweeping is
the repetitive mechanical part; XY framing is the part worth doing by hand.

### `libimagepro.so` — the vendor already built the guidance loop

`extra/imagepro/linux/x64/libimagepro.so`, 45 MB, native Linux, with working Qt
samples for all three features. All exports verified present on the Linux build.

**`imagepro_stitch_*` — live guided mosaicking.** The per-frame callback returns:

```c
void* outData, int stride, int outW, int outH, int curW, int curH, int curType,
int posX, int posY, eImageproStitchQuality quality, float sharpness, ...
```

Live position, live quality grade, live sharpness. The event enum includes
`AREPAIR_STOP_X`, `AREPAIR_KEEP_Y`, `AREPAIR_REVERSE_X`, `AREPAIR_RIGHT_DIR` —
**it emits stage-direction guidance to the operator.** There is also
`imagepro_stitch_cropparams` (the central crop) and precision/threshold tuning.

This is substantially the live guidance loop described in §9. It was assumed to
be the novel part of this project. It is not — it ships with the SDK.

**`imagepro_edf_*` — focus stacking.** Methods `Pyr_Max`, `Pyr_Weighted`,
`Stack`. That is precisely the detail-preserving / smooth pair §8 recommended
exposing. Removes the need to vendor `focus-stack`, and with it the licence
question about that dependency.

**`imagepro_livestack_*` — frame stacking.** `setnum` up to 99, `setalign`, and
mode `MEAN`. This is the frame-averaging SNR strategy from §3, native.

**`imagepro_demosaic`** — LINEAR / VNG / **EA** (edge-aware) debayer on raw
buffers, so we get a choice of demosaic algorithm without writing one.

### The question this all now hangs on

**Does `imagepro_stitch` grade sparse diatoms-on-black as `GOOD`?**

Reasons for doubt:
- The header notes a higher threshold "requires higher-quality input images".
- The quality enum contains `ZERO` and `BAD` as expected outcomes.
- `eImageproLivestackError` includes `NOENOUGHSTARS` and `NOENOUGHMATCHES` —
  these are feature-matching engines underneath.

Working hypothesis: it grades darkfield captures `ZERO` and gives up, for the
same reason Hugin does, which would explain the fallback to manual compositing.

**This is testable without the microscope** by replaying existing darkfield
captures through `imagepro_stitch_readdata` and reading the quality grades.

- If `GOOD` → months of work avoided; integrate and move on.
- If `ZERO` → the overview-anchored registration in §7 is the reason this
  project exists, and it is the thing to build.

Caveat on the test: the engine is designed for a continuous live stream with
small inter-frame displacement. Discrete tiles with large jumps may fail for
reasons unrelated to subject matter. Interpret a negative result carefully.

---

## 4c. Licensing

**There is no licence in the SDK.** Audited across the full 243 MB archive:

- No `LICENSE`, `EULA`, `COPYING`, `NOTICE` or terms file anywhere.
- No copyright string in either binary. (`libtoupcam.so` contains the strings
  `license` and `_license`, but they belong to an embedded copy of libbpf and
  say nothing about the SDK.)
- The vendor download page states no terms.
- The only grep hits were Visual Studio `AssemblyInfo.cs` boilerplate.

ToupTek ships two proprietary binaries — `libtoupcam.so` and `libimagepro.so` —
with **no stated grant and no stated restriction**. That is ambiguous, not
permissive. Silence is not a licence.

**Recommended posture for a GPLv3 release — all three:**

1. **`dlopen` both libraries at runtime.** Never link at build time.
2. **Ship no blob.** The installer fetches from ToupTek, or points at an
   existing install. This matches `gst-plugin-toupcam`, which has the user copy
   the `.so` themselves. INDI/INDIGO vendor the binary, which is the riskier
   precedent.
3. **Add a GPL linking exception** naming `libtoupcam` and `libimagepro`. One
   paragraph, costs nothing, removes the only substantive objection available.

With all three, the GPLv3 code stands alone and the *user* performs the linking.

*(This is a considered engineering posture, not legal advice.)*

---

## 5. Optics: three problems wearing one name

"Spherical aberration across the field" is really three separate effects, and
they have different fixes:

1. **Vignetting + uneven Köhler illumination** → brightness steps at mosaic
   seams. Fixed by flat-field correction.
2. **Field curvature** → frame edges sit on a different focal plane than centre.
   Fixed by cropping to the central region.
3. **Lateral chromatic aberration and edge aberration** → colour fringing and
   softness at the field edge. Also fixed by cropping, or corrected per-channel.

Objectives are Zeiss Plan-Apo (good). The weak link is the camera relay: a
~$200 AmScope 1–2× eyepiece-to-C-mount adapter. Planar relay eyepieces are on
hand but need a new adapter.

### Flat-field / dark-field

`(raw − dark) / (flat − dark)`, applied **before** anything else.

The SDK's FFC/DFC may apply inside the ISP path, in which case raw mode bypasses
them and we need our own implementation. **This must be tested, not assumed.**
Either way, `FfcExport`/`FfcImport` means calibration is storable per setup.

### Central crop

Use only the central ~50–60 % of each frame for the mosaic. This kills field
curvature, vignetting and edge CA in one move. Cost is more tiles, which we want
anyway for high overlap. The optical profiler (below) should *compute* the crop
radius rather than leaving it to judgement.

---

## 6. Optical profiling

A first-class feature, not a diagnostic afterthought. Using a **stage
micrometer** and a **grid target**, profile each optical configuration and
produce a comparable scorecard.

Measurable per configuration:

| Measurement | Method | Why it matters |
|---|---|---|
| **µm/pixel** | Micrometer tick spacing | Scale bars; seeds mosaic positions in real units |
| **Radial distortion** | Fit polynomial to grid intersection displacement | Correction map; comparison metric |
| **Field curvature** | Z-sweep on flat grid, focus peak per image region | *The* number for comparing relay optics |
| **Lateral CA** | Grid intersections per R/G/B channel separately | Per-channel radial rescale corrects it |
| **Illumination falloff** | Radial intensity profile from a blank flat | Sanity-checks the flat-field |
| **MTF50 centre vs corner** | Slanted-edge on grid | Real sharpness-across-field number |
| **Usable field fraction** | Combine curvature + MTF falloff + CA against a threshold | **Auto-derives the crop radius** |

Immediate practical payoff: put the AmScope adapter head-to-head against the
planar relay eyepieces and pick the optic on data.

### Profiles are the core data model

Illumination is switched frequently between **brightfield** (sometimes inverted
for a clean synthetic darkfield), **true darkfield**, and **phase**. So a
calibration profile is keyed on the full combination:

```
profile = (objective × relay optic × illumination mode × binning)
  ├─ optical    : µm/pixel, distortion, curvature, CA, crop radius
  ├─ correction : flat frame, dark frame
  ├─ capture    : exposure, gain, white balance, ISP settings
  └─ measured   : MTF, usable field fraction, date profiled
```

This is the object the UI is built around. Switch illumination, the profile
follows, and every downstream stage picks up the right calibration.

**Note on glow:** these are art photographs, not scientific measurements. Diatom
glow under phase and darkfield is wanted and must be preserved. Glow is only a
problem for *registration*, and the chosen registration method (section 7) is
indifferent to it.

---

## 6b. What the existing captures prove

Measured from `~/Pictures/ovshoot/250819_dopamine/` — 31 ToupLite captures plus
two hand-composited results. Subject: a dopamine molecule built as a diatom
arrangement.

### Every capture is binned

All 31 frames are **2736 × 1824** — resolution index [1], the 2×2 binned mode.
**5 MP of an available 19.85 MP.** Not a choice that was made; a default that was
never surfaced.

### The finished composite is smaller than one frame

| File | Pixels | vs. one full-res frame |
|---|---|---|
| `dopamine_darkfield_6x_square.jpg` | 3358 × 3358 = **11.3 MP** | **0.57×** |
| `dopamine_darkfield_6x.jpg` | 3957 × 1843 = **7.3 MP** | **0.37×** |

Hours of manual GIMP compositing produced an image with fewer pixels than a
single correctly-configured capture. Stitching is still needed — it buys field
of view, not pixels — but every tile should be carrying 4× what it currently is.

### Darkfield tonality is crushed into four levels

`ov0027.jpg` (darkfield):

```
luma in [0,  4) : 90.64%      <- nine tenths of the image, in four values
luma in [0, 64) : 93.31%
flattest-decile tile sigma : 0.00 DN
```

**90.6 % of all pixels occupy luma 0–3.** At 12 bits that same tonal region gets
**64 levels — 16× the resolution**, precisely where the faint outer glow lives.
The measured 0.00 DN noise floor in flat tiles means JPEG has quantised the dark
background to flat blocks: the softest part of the glow is not dim in these
files, it is *absent*.

This matters because the glow is wanted. These are art photographs.

### Chroma is at quarter resolution

ToupLite writes **YCbCr 4:2:0**. Downsampling each chroma plane 2× and
re-upsampling produces a mean residual of **0.13–0.15 DN** — the detail was
already destroyed at encode time. Diatom iridescence is being carried at half
resolution per axis.

(The hand-made GIMP composites are 4:4:4. The manual workflow was already better
than the capture software.)

### The stitching problem, measured

`ov0016.jpg` (phase), median radial luma profile from centre to corner:

```
1.00  1.10  1.26  1.30  1.30  1.29  1.27  1.26
```

**Frame edges are ~30 % brighter than centre.** Visible in the image, and not
purely radial — there is a diagonal component suggesting condenser centring
error layered on top of optical falloff.

A 30 % brightness gradient across every tile is a complete explanation for why
seams will not blend. Flat-field correction does not model this, it *measures*
it, which is why it works regardless of which of the three causes dominates.

Phase also wastes range: `ov0016` spans roughly levels 23–203, leaving ~25 % of
an already-limited 8-bit encoding unused.

### Full accounting of what the current pipeline discards

| Stage | Now | Available |
|---|---|---|
| Resolution | 2736 × 1824 (binned) | 5440 × 3648 |
| Bit depth | 8-bit | 12-bit |
| Tone | curve + colour matrix applied | linear |
| Chroma | 4:2:0 subsampled | full resolution |
| Encoding | JPEG | lossless |

---

## 7. Stitching: wrong category of software

Hugin, PTGui, ICE and Photoshop Photomerge model a **camera rotating about a
nodal point**, projecting onto a sphere, with correspondence found by **feature
detection**.

Neither assumption holds here. This is a **camera translating on a flat plane**,
over a subject that is mostly empty background with glowing glass in it. SIFT
and ORB have little to grab, and the features that exist — halos — shift with
focus and illumination angle, so they are unstable.

**The right method is phase correlation**: frequency-domain, whole-tile, no
features needed. Glow is fine because glow is *consistent between overlapping
tiles*. This is what the microscopy field uses, and it is a separate software
lineage:

- **MIST** (NIST) — built for exactly this
- **m2stitch** — Python, easy to embed
- **Fiji Grid/Collection Stitching** (Preibisch) — the classic
- **BigStitcher**, **ASHLAR** — modern heavyweights

Switching categories likely fixes more pain than anything else in this document,
and it is testable today against existing captures.

### Measured: phase correlation works on this subject

The concern was that a subject **sparse on pure black** would leave adjacent
tiles overlapping in featureless regions, where phase correlation has no more
signal than feature matching. **Tested against the existing captures, and this
is not what happens.**

Controls first, to establish that the measurement means anything:

```
noise floor (frame vs phase-scrambled copy)   0.014 – 0.039
sanity (known +200,+120 px shift)             recovered exactly, conf 0.90 – 0.97
```

Darkfield consecutive pairs:

```
0005->0006   shift (+1248,  +20)   conf 0.283   real tile step
0006->0024   shift (-1227,  -22)   conf 0.303   real tile step
0024->0025   shift (+1209, -255)   conf 0.195   real tile step
0027->0028   shift (-1131,  -85)   conf 0.323   real tile step
0026->0027   shift (    -1,   +0)  conf 0.911   duplicate frame, no motion
```

**Darkfield registers at 5–20× the noise floor**, with consistent ~1200 px steps
— a genuine tile sequence at roughly 45 % overlap on a 2736 px frame.

Why the pessimism was wrong: bright compact objects on black are *excellent*
phase-correlation targets. The empty regions contribute no signal, but they also
contribute no noise.

### Unexpected: phase frames register *worse* than darkfield

Median confidence across bright/phase pairs was **0.27**, against **0.42** for
darkfield, with several outright failures.

**Hypothesis:** the 30 % radial illumination gradient measured in §6b is a large
smooth low-frequency signal that the correlation locks onto, biasing the result
toward zero shift.

**If true, flat-field correction will measurably improve phase registration.**
Clean experiment, worth running as soon as flats can be captured.

### Insurance, not prerequisite: an overview reference frame

Capture one **low-magnification frame of the whole arrangement** before the
mosaic run. Every high-resolution tile then registers **against the overview**,
not against its neighbours.

- No chaining, so no accumulated drift.
- Works across black gaps, because the overview holds all landmarks at once.
- Gives an absolute coordinate frame for the whole session.

This is what astronomical mosaics do (plate solving against a reference field).

Since neighbour-chaining is now known to work on this subject, this is a
**robustness upgrade rather than a requirement**. Keep it in the plan — a
sparser arrangement than the dopamine could still break chaining, and absolute
coordinates are worth having regardless — but it is not the thing to build
first.

**The Zeiss Universal makes this cheap.** The Optovar intermediate magnification
changer alters magnification *without* swapping objectives, so the optical axis
and focal plane stay put. Overview at low Optovar, tiles at high, with nearly
free registration between them — cleaner than a turret swap.

**It also resolves the UI question.** The overview image *is* the minimap: the
live frame is drawn as a rectangle moving across a picture of the actual
arrangement, with captured tiles shaded in. Position, coverage and context in
one widget, showing the real subject rather than an abstract grid.

### Secondary option: register in one modality, capture in another

Darkfield deliberately discards exactly the low-contrast texture (dust,
mountant, coverslip debris) that makes registration easy. Brightfield keeps it.

If overview registration proves insufficient, a brightfield pass can define tile
positions and a darkfield pass can supply the pixels. Worth keeping in reserve;
the overview frame should be tried first because it costs one capture.

---

## 8. Pipeline order: stack, then stitch

Per tile position:

```
Z sweep  →  dark/flat correct  →  align stack  →  focus stack  →  one EDF tile
```

Then mosaic the EDF tiles.

Reasons:

- A Z sweep at fixed XY is already nearly registered; only focus breathing (a
  small scale change), which `align_image_stack` handles.
- **Stitching first breaks stacking.** Each Z level finds different alignment
  because different regions are sharp at different Z, so the resulting panoramas
  are not pixel-aligned with each other and stacking across them ghosts.
- Blurry input poisons registration. EDF tiles are the best possible mosaic
  input.
- Memory stays bounded per tile instead of holding N full panoramas.

The only argument for stitch-first is poor XY repeatability, and phase
correlation is robust to that anyway.

**Merge implementation:** `focus-stack` (PetteriAimonen) — C++/OpenCV,
wavelet-based, fast, embeddable. `enfuse` works but is slow. **Expose both merge
methods**: wavelet preserves fine diatom striae but halos on the glow;
depth-map is cleaner but softer. Per-subject choice.

*(Verify `focus-stack`'s licence for GPLv3 compatibility before vendoring.)*

---

## 9. The live guidance loop

The stage is **manual**. This is the constraint that defines the product: the
app must guide a human hand, and it must work on an ordinary non-motorised
microscope. That is also the gap in existing software.

Everything else in this document is assembly of existing parts. **This is the
part nobody has built.**

- **Dead-reckoned XY position.** Phase-correlate the live frame (downsampled to
  ~512 px) against the last captured tile at ~15 fps. No encoder needed. Cheap
  on 16 cores.
- **Coverage minimap.** Captured tiles, current position, live overlap
  percentage. Green at target overlap (~25–30 %), red when about to leave a gap.
  You cannot forget a tile.
- **Seed the offline stitch with those positions.** The largest single payoff.
  The stitcher stops doing blind global search and only refines locally — the
  difference between "sometimes works" and "always works".
- **Focus assist.** Live `Toupcam_calc_ClarityFactor` as a readout. Find the
  peak, then auto-sweep a symmetric Z range around it. Makes "the same Z stack
  at every tile" repeatable, which is precisely the step that is currently
  miserable.

---

## 9b. Z motorization — stretch goal

> **Design principle: the tool must work on a completely unmodified microscope.**
> No motors, no gears, no adapters, no soldering. Someone with an ordinary scope
> and a ToupTek camera should get the full workflow on day one. Motorization is
> an optional accelerator, never a prerequisite.

**Consequence, and it is a big one:** if Z stays hand-cranked, then **the focus
assist UI is carrying the hardest part of the workflow**, not a motor. Manually
sweeping Z at every tile position is precisely what currently makes
stack-plus-stitch miserable, and the software has to make that bearable.

What that demands of the UI (this is now core scope, §9):

- **Live sharpness readout** (`calc_ClarityFactorV2`) as a curve, not a number —
  you need to see the peak coming and know when you have passed it.
- **Peak memory.** Record the sharpness maximum during a sweep and show how far
  the current position is from it.
- **Sweep coaching.** "Go up until sharpness drops, then come back down" with
  frame counting, so the operator gets a consistent stack depth per tile without
  reading a Z scale.
- **Per-tile stack consistency check.** Flag a tile whose sweep covered a
  visibly different Z range from its neighbours, before it ruins the mosaic.
- **Unidirectional discipline.** Always sweep the same direction, for the same
  backlash reason a motor would need.

Getting this right is more valuable than a motor, because it ships to everyone.

---

### If motorizing later (Zeiss Universal, 1960s)

Five-objective turret, condenser filter selector, polarizers, Optovar
intermediate magnification changer, Bertrand lens for phase annulus centring.
(The Bertrand lens doubles as a cleaning instrument — focusing through it walks
you along each optical surface to locate dust.)

XY stays manual permanently — that is the point of the product. Z is the
candidate, because sweeping is repetitive mechanical work.

**Coupling:** a **0.8 MOD gear ring** (the cine follow-focus standard, available
off the shelf in 35T/40T/43T) on the fine focus wheel, driven by a pinion.

**Do not use a cine follow-focus motor.** Tilta Nucleus-Nano, Feiyu, DJI and
similar are built for a human turning a hand wheel: proprietary BLE protocols,
optimised for smooth ramping. The requirement here is the opposite — discrete,
absolutely repeatable, step-and-settle positioning.

**Use a stepper.** NEMA 17 + TMC2209 + RP2040, direct-coupled, USB serial.
~$35 in parts against ~$300 for a cine motor, fully open control, GPLv3-clean.
Well-trodden ground (Curious Scientist's motorized Z, WeMacro Micromate,
photomacrography.net, various Hackaday builds).

**Resolution budget** — there is enormous headroom, so do not over-engineer the
mechanics:

| | |
|---|---|
| Zeiss fine focus | ~1 µm per graduation, ~100 µm per turn |
| NEMA 17 direct, full step | **0.5 µm** |
| …at 1/16 microstepping | **~31 nm** |
| Needed at 40×/0.75 | ~1 µm |
| Needed at 100×/1.4 oil | ~0.3 µm |

**Two things specific to this stand:**

- **The fine focus has wheels on both sides.** Motorize one, keep manual control
  on the other. The scope never becomes software-only.
- **Backlash.** Sixty-year-old mechanism. Always approach each Z position from
  the same direction — unidirectional sweeps, overshoot and return. Costs
  nothing and is the difference between a clean stack and a soft one.

*(The SDK's `get_FocusMotor` / `put_AFFMPos` / `AAF` functions drive ToupTek's
own focus units and are unlikely to fit this stand. The Z axis will be our own
controller.)*

---

## 10. Architecture

**Stack:** Python + PySide6 (Qt 6) + OpenCV + NumPy.

- Qt 6 handles HiDPI natively — complaint #1 resolves for free.
- The ToupTek SDK ships Python bindings and samples.
- The hot loop is downsampled phase correlation. Python is not the bottleneck.
- Available hardware: 16 cores, 62 GB RAM, GTX 1650 Ti if GPU is ever needed.

**Put the camera behind an interface from day one.**

```
CameraBackend (abstract)
  ├─ ToupcamBackend      libtoupcam.so
  └─ TetheredBackend     gphoto2 / Sony Camera Remote SDK  (later)
```

The A6700 then becomes a second capture source with a real display and no SD
juggling, and the entire calibrate → stack → mosaic pipeline is shared. That is
the version worth releasing.

**Connection resilience is a feature, not a patch.** A session manager owns the
camera handle, subscribes to `Toupcam_HotPlug`, and on disconnect reopens and
re-applies the active profile. Settings must survive a nudged cable.

*Diagnostic:* on any flake, read `/sys/bus/usb/devices/*/speed`. A drop from
5000 to 480 means the link fell back to USB 2.0 — that is the cable, not the
camera.

---

## 10b. Prior art

**Micro-Manager** — the incumbent open microscopy platform. Has a ToupTek device
adapter, multi-dimensional acquisition and stitching plugins. Java, dated UI,
assumes a motorized stage. Does not do live guided mosaicking for a hand-driven
stage.

**pyuscope** (John McMaster) — the closest relative. Python + PyQt5 + gstreamer,
driving ToupTek cameras via `toupcamsrc` for panoramic scans of IC dies, with
LinuxCNC/GRBL motion control.

| | pyuscope | this project |
|---|---|---|
| Motion | LinuxCNC / GRBL, motorized | **manual stage** |
| Subject | IC dies — flat, opaque, feature-rich | diatoms — translucent, 3D, sparse on black |
| Focus stacking | not a focus | **required** |
| Illumination | single mode | brightfield / darkfield / phase |
| Camera layer | gstreamer `toupcamsrc`, PyQt5 | same camera, same toolkit |

Adjacent rather than overlapping: pyuscope solves motorized scanning of
feature-rich flat subjects; this is hand-guided scanning of sparse translucent
ones. The camera layer is shared ground and should not be duplicated —
`gst-plugin-toupcam` already exists.

**ToupTek's own `libimagepro`** — see §4b. The live stitching, EDF and stacking
engines are vendor-supplied. Viability on this subject is unverified (§4b).

---

## 11. Open questions

- [ ] **Is resolution index [1] true on-sensor 2×2 binning, or a downsample?**
      Matters more than it looks. True binning collects 4× the light per output
      pixel, so the binned frames have a genuine SNR advantage that full
      resolution gives up. If so, full-res is a real trade (detail for noise)
      rather than a free win — and frame averaging is how we buy the noise back.
      The SDK reports 4.80 µm pixels for index [1], which suggests real binning,
      but `BINNING` (0x17) separately reads 1. Test empirically.
- [ ] Is the optical resolution of the current relay even sufficient to
      out-resolve the binned mode? If the AmScope adapter is the limit, full-res
      may be recording empty magnification. The optical profiler answers this.
- [ ] Do SDK FFC/DFC apply in the raw path, or only the ISP path?
- [ ] Does a current libtoupcam expose `ISP` / `FPNC` / `FLUSH` on this hardware?
- [ ] Actual sustained frame rate for full-res 12-bit raw over this link?
- [ ] Raw storage format: DNG (Darktable/RawTherapee compatible), 16-bit TIFF,
      or FITS (trivial to write, opens the astro toolchain — Siril, PixInsight)?
- [ ] Does `focus-stack` licence cleanly for GPLv3?
- [ ] Does the Sony Camera Remote SDK actually support the A6700 on Linux?
- [ ] Is dead-reckoned drift over a large mosaic acceptable, or does the live
      loop need periodic re-anchoring against already-captured tiles?

---

## 12. Roadmap

**0a — Stitcher viability test.** *No microscope required.* Replay the existing
darkfield captures through `imagepro_stitch_readdata` and read the quality
grades. This decides whether the vendor's live mosaic engine is usable on this
subject, and therefore whether §7's overview-anchored registration is the core
of the project or an unnecessary detour. Cheapest high-information test
available. Run it first.

**0b — Raw spike.** Pull one full-res 12-bit raw frame with the ISP defeated,
apply dark/flat, write a TIFF, and put it beside a ToupLite JPEG of the same
field. Everything downstream rests on raw being a real improvement. Prove it
with pixels before designing around it.

**1 — Optical profiler.** Standalone tool. Produces immediately useful output
(which eyepiece to buy) and defines the profile data model that the rest of the
app is built around.

**2 — Capture UI.** Qt 6, live view, profile management, exposure/gain, burst
averaging, calibration capture.

**3 — Guided mosaic.** The live loop, minimap, coverage tracking, position
export.

**4 — Offline pipeline.** Per-tile focus stacking, phase-correlation mosaic
seeded with captured positions.

**5 — Second backend.** Tethered A6700.

**Stretch — Z motorization.** Optional accelerator only (§9b). Never a
prerequisite; the unmodified-scope workflow must be complete without it.
