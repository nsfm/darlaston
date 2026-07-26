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

### The flat can be derived from the mosaic tiles themselves

The subject moves between tiles; the illumination field does not. So the field
is recoverable from a set of unaligned tiles without shooting a blank slide —
per-pixel max (brightfield, where subject is darker than background) or median,
followed by heavy smoothing.

Tested on the four brightfield captures:

```
                       centre ---------------------------> corner    spread
ov0004 raw             1.000  0.989  0.984  0.952  0.920  0.915  0.888  0.851   +17.5%
derived flat field     1.000  0.995  0.985  0.964  0.943  0.926  0.899  0.864   +15.8%
ov0004 corrected       1.000  1.000  1.002  1.003  0.998  1.002  1.003  1.003    +0.5%

same derived flat applied to the other three tiles:
ov0001 corrected  +0.2%      ov0002 corrected  +0.4%      ov0003 corrected  +0.5%
```

**17.5 % → 0.2–0.5 %.** A 35–80× reduction in field non-uniformity, from JPEG
data, with no calibration frames captured.

**Feature: auto-flat from session tiles.** A user who never shoots a flat still
gets the correction, derived from their own mosaic. A properly captured flat
will beat this, but this is the floor and the floor is good.

### Illumination mode determines the flat — measured

```
brightfield   1.00 -> 0.85    edges 15% DARKER
phase         1.00 -> 1.30    edges 30% BRIGHTER
```

Same objective, same relay, opposite directions. The phase gradient is therefore
**not vignetting and not the camera relay** — brightfield through the identical
optical train falls off the normal way. It originates in the phase contrast
system itself: off-axis rays at the field edge traverse the phase plate
differently than the annulus intends.

**Consequence: a flat cannot be shared between illumination modes.** The
per-mode profile keying in §6 is a requirement, not tidiness.

*(Also noted: brightfield clips the darkest diatom cores to 0. More 8-bit
damage, and another thing raw fixes.)*

### Calibration practice worth adopting wholesale

From the astrophotography survey. All of it works with zero motion control.

- **`OPTION_ZERO_PADDING` (0x78) must be set to 1.** Default is "high": 12-bit
  data arrives **left-justified** in the 16-bit word, so values run 0–65520 in
  steps of 16 rather than 0–4095. INDI's `indi_toupbase.cpp` sets it. Silent
  and confusing if missed.
- **`BLACKLEVEL_AUTOADJUST` (0x89) must be off** for raw work. An auto-adjusting
  offset silently changes the meaning of every dark frame ever captured.
- **Per-Bayer-phase flat normalisation is non-optional.** ASTAP splits its flat
  norm four ways on `odd(x)`/`odd(y)` (`flatNorm11/12/21/22`) because the four
  phases have different sensitivity and different vignetting; one scalar leaves
  a 2×2 checkerboard baked into every corrected frame. `tools/derive_flat.py`
  now has `--bayer` for this.
- **ASTAP's `CALSTAT` FITS keyword** — records which steps have been applied
  (`'D'` dark, `'F'` flat, `'B'` bias) and skips re-application. Idempotent
  calibration, self-documenting on disk, about ten lines. Plus `DARK_CNT` /
  `FLAT_CNT` / `BIAS_CNT`.
- **PHD2's `DefectMap`** (BSD-3, `src/image_math.h`) — sigma-threshold a master
  dark into a bad-pixel list, then *repair* those pixels per frame instead of
  subtracting a whole master. Cheaper, and **temperature-robust**, which matters
  a great deal here because this camera has no TEC (§4b). Ekos ships the same
  idea with separate hot- and cold-pixel aggressiveness sliders.
- **AstroDMx's preview-only vs preview+saved calibration toggle.** Its stated
  rationale is exactly our problem: hot pixels are indistinguishable from small
  bright objects and **poison the histogram and the object detector**. Being
  able to calibrate the *display* while leaving the data untouched is the right
  default for an art workflow.
- **Ordering rule** (SharpCap): dark → flat → *everything else*. On 12-bit raw,
  before demosaic.
- **Master auto-selection axes.** SharpCap keys masters on
  camera/colour-space/resolution/exposure/gain; N.I.N.A. adds an **"Ignore"
  wildcard** fallback. Our extra axis is **illumination mode in place of
  "filter"**, and flats key on objective × condenser × illumination. Nobody in
  astronomy has that axis.
- **A Flat Wizard.** N.I.N.A.'s Dynamic Exposure does a binary search on
  exposure to hit a target histogram mean. We have a lamp knob and a blank
  slide, which *is* a flat panel. "Trained flat exposure per objective" is the
  direct analogue of their trained-per-filter table.
- **AstroDMx's Connection Monitor** — polls for a stalled stream, then resets,
  reinitialises with existing settings, and **resumes from the point of failure,
  pausing rather than aborting** an in-progress capture. Directly addresses the
  USB dropout problem in §4b. A 20 MP USB3 camera will drop out.

### Two SDK gotchas documented in-tree

Worth knowing before debugging them from scratch:

- INDI: *"When taking an exposure, the camera switches to software trigger mode.
  When streaming video, the camera switches to video mode."*
- INDIGO: *"There is a known issue with SDK and reopening the camera — exposure
  is not possible until the camera is reconnected."* **This directly constrains
  the hotplug/reconnect design in §10.**

### Licensing update: the vendor pushes the blob itself

`indi-3rdparty` carries `libtoupcam` at **60.31631.20260606** — newer than the
SDK downloaded here (59.30594) — and **the blob updates are pushed by GitHub
user `touptek` itself**, on a roughly six-weekly cadence (PR #1335 merged
2026-06-07; #1291, #1252, #1223, #1169 before it).

That is materially better evidence than "no licence file": the vendor is
actively and repeatedly placing the binary in a public repository for
redistribution. It does not create a written grant, so §4c's posture stands, but
it substantially weakens any argument that redistribution was unintended.

Also confirmed there: this camera is PID **0x1122** (USB3) / **0x1123**
(USB2 variant), vendor `0547`, with `04b4` (Cypress FX2/FX3) also covered by the
udev rules.

### Audio focus feedback — nobody does this, and it fits

SharpCap explicitly has no audio feedback, and neither does anything else in the
survey. But a microscopist focusing by hand is looking **down the tube or at the
sample**, not at the monitor.

The one existing implementation is `rpicam-apps/post_processing_stages/`
`acoustic_focus_stage.cpp` (BSD-2-Clause) — maps focus figure-of-merit to an
audible tone, 300–3000 Hz, log or linear, 1 Hz update. Its header states the
rationale exactly: *"No visual contact with the preview is required."*

Cheap, and it fits the ergonomics of this instrument better than any overlay.

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
- **Focus assist.** Live `Toupcam_calc_ClarityFactorV2` as a readout. Find the
  peak, then sweep a symmetric Z range around it. Makes "the same Z stack at
  every tile" repeatable, which is precisely the step that is currently
  miserable. See below — with a manual Z axis this is core scope.

### Focus peaking

A per-pixel sharpness overlay, in the manner of a mirrorless camera's peaking.
`calc_ClarityFactor` is a whole-frame scalar, so the map is ours to compute.

**The naive implementation fails on this subject.** Standard peaking is
high-pass → threshold → colorize. In darkfield, every diatom is a bright blob on
black and its outline carries enormous edge gradient *at any focus*. Naive
peaking outlines everything, permanently, and tells you nothing.

Two changes fix it:

1. **Normalize by local contrast.** Divide the high-frequency response by local
   RMS, so the measure is *relative* sharpness rather than absolute edge
   strength and a bright diatom stops winning merely for being bright. Without
   this, peaking in darkfield is decorative.
2. **Band-pass, not high-pass.** Focus in a diatom is indicated by the
   **striae** — pore rows near the resolution limit, and the first structure to
   vanish on defocus, while the gross frustule outline stays sharp well out of
   focus. A difference-of-Gaussians tuned to that band is effectively a striae
   detector, which is the thing being focused on anyway.

### Which focus metric — revised against the literature

The obvious choice is wrong for this subject, and it was measured decades ago.

**Santos et al. 1997** ([open PDF](https://www2.die.upm.es/im/papers/Autofocus.pdf),
DOI `10.1046/j.1365-2818.1997.2630819.x`) evaluated 13 autofocus functions on
**FISH fluorescence — sparse bright objects on a black background**, the closest
published analogue to darkfield diatoms. Final ranking, lower is better:

```
Fvoll4       1.49    <- Vollath F4
Fvoll5       2.85
Fnor_var     3.06
Ftenengrad   3.17
Fvar         3.27
...
Frange        --     too noisy to locate a maximum in 4 of 10 series
```

From the discussion, verbatim:

> "accuracy is highest for function Ftenengrad, **but this function performs
> badly when the information content is low.**"

> "this worsening is less pronounced in functions Fvoll4 and Fvoll5. These two
> functions seem to work properly on cytogenetic images with a small number of
> nuclei."

**A diatom in a black field is the low-information-content case by
construction.** Tenengrad is what `focus-stack` uses, what oaCapture uses, and
the obvious first reach.

**Mateos-Pérez et al. 2012** (DOI `10.1002/cyto.a.22020`) ran 13 methods on
fluorescent TB bacteria — again sparse bright objects on black, different group,
15 years later. **VOL4 won again.**

Why it survives sparsity:

```
F4 = Σ g(i,j)·g(i+1,j) − Σ g(i,j)·g(i+2,j)
```

A **difference of two autocorrelation lags**, so zero-mean noise cancels between
the terms rather than being squared and accumulated. And because it is
multiplicative in intensity, **empty background contributes ~0 to both terms**.
It is also parameter-free — which matters for a tool that cannot be tuned per
slide. Two shifted multiplies; pure NumPy.

**Defaults by illumination mode:**

| Mode | Metric | Source |
|---|---|---|
| **Darkfield / phase** | **Vollath F4** | Santos 1997, Mateos-Pérez 2012 — two independent wins on this image class |
| **Brightfield** | Normalised variance, or variance of Laplacian | Sun 2004, Liu 2007; LAPV is Pech-Pacheco's own diatom result |
| Alternates | Tenengrad, LAPV, Redondo, RMS contrast with saturation mask | Tenengrad's accuracy advantage is real on a densely populated strew |

**Prefilter must also switch by mode.** Mateos-Pérez found **median filtering
degraded** focus precision on sparse bright objects while **white top-hat
morphology improved** it. pyuscope's `medianBlur(9)` was tuned for brightfield
IC dies. So: **none / median / top-hat**, defaulting to top-hat for darkfield
and median for brightfield. Not hardcoded.

**Field confirmation.** Squid's `_def.py` states it outright, and their deployed
fluorescence configs actually set it:

```python
LAPE = "LAPE"  # LAPE has worked well for bright field images
GLVA = "GLVA"  # GLVA works well for darkfield/fluorescence
```

**Pipeline order** (from ImSwitch's `_compute_focus_value_fast`, GPLv3):
ROI crop → grayscale → optional bin → optional prefilter → metric. Crop first so
everything downstream is cheap.

*Aside worth knowing: the ubiquitous `cv2.Laplacian(img).var()` one-liner traces
directly to **Pech-Pacheco et al. 2000, "Diatom autofocusing in brightfield
microscopy"** (DOI `10.1109/ICPR.2000.903548`), which fed the ADIAC automatic
diatom identification project. The most-copied focus metric in computer vision
comes from a paper about autofocusing diatoms. It was brightfield.*

### Peaking implementation — four pieces, all liftable

1. **Peak the display buffer, never the sensor frame.** darktable
   (`thumbnail.c:811`, `cairo_scale(1/scale)`) and Open Camera
   (`use_preview_bitmap_small`) both run peaking on the already-rendered
   screen-resolution buffer. Neither does anything clever to make full
   resolution fast — they just don't do it. At ~1600×1000 a Sobel plus Gaussian
   is trivially realtime.
2. **`focus-stack`'s `src/task_focusmeasure.cc`** — MIT, 47 lines, the whole
   per-pixel map: Sobel x and y → `accumulateSquare` → threshold to a noise
   floor → **GaussianBlur** → sqrt. The blur is the load-bearing step nobody
   else does: it turns a sparse edge response into a smooth *field* that can be
   colourmapped. `m_radius` is the spatial-resolution knob.
3. **darktable's two-scale close-vs-far gradient** (`src/common/focus_peaking.h`,
   GPL-3.0, same licence as us), four lines:
   ```c
   luma_ds[i] = _laplacian(luma, index_close)
              - 0.67f * (_laplacian(luma, index_far) - 0.00390625f);
   ```
   Its in-source rationale: **if the close-neighbour gradient ≈ the
   far-neighbour gradient you have local *contrast*, not *sharpness*.** That is
   precisely the failure mode identified above — a bright diatom outlining
   itself regardless of focus — and it is a cleaner fix than local-RMS
   normalisation alone.
4. **Magic Lantern's percentage-target auto-threshold servo** (`src/zebra.c`,
   GPLv2+):
   ```c
   if (1000 * n_over / n_total > focus_peaking_pthr) thr += thr_increment;
   else                                              thr -= thr_increment;
   ```
   The user sets a **target fraction of pixels lit** (default 0.5 %) and the
   threshold servos toward it frame by frame. Scene-invariant. **Essential for a
   hand crank** — while racking continuously a fixed threshold either saturates
   or goes blank.

Optionally **Open Camera's `count >= 3` cross-neighbourhood cleanup**, which
kills isolated speckle without a blur pass.

### Focus coverage — the feature that fixes stack depth

Union the in-focus masks across a Z sweep. Report **coverage**: "94 % of the
central crop has been in focus at some Z", and paint the regions that have not.

This answers *"have I taken enough slices?"* objectively. At present that is a
feel, it differs at every tile, and inconsistent stack depth between tiles is a
significant part of why stacking and stitching refuse to compose. With coverage,
the operator stops at 100 % rather than when they feel finished, and every tile
gets equivalent treatment without ever reading a Z scale.

**Free bonus:** paint the *current* in-focus mask live and rack the fine focus.
The in-focus zone visibly travels across the field. With field curvature it
moves as a ring or blob rather than the whole frame lighting together — a **live
field-curvature visualization**, showing during setup what §6 measures properly
with a grid target.

All of this runs on the binned live stream. DoG plus local normalization at
1824 × 1216 is not a performance concern.

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

## 10a. Library survey — verified licences and empirical tests

Findings from a dedicated survey pass. Licences verified from repository files,
not assumed. Two of the results below come from experiments rather than
documentation.

### Confidence alone cannot detect a wrong match

Four overlap scenarios, measured:

| Overlap content | `cv2.phaseCorrelate` response | Correct? |
|---|---|---|
| Sparse darkfield blobs, true overlap | 0.910 | yes |
| Pure black + independent noise | 0.021 | correctly rejected |
| Smooth shading only | 0.009 | correctly rejected |
| **One blob each, different objects** | **0.799** | **confidently wrong** |

The last row is the real darkfield risk: one diatom in each overlap strip, phase
correlation locks the wrong pair together and reports 0.80 — indistinguishable
from the true match at 0.91. **No confidence threshold catches this.**

The only thing that does is a **position constraint** — MIST's search window,
m2stitch's repeatability filter, ASHLAR's `max_shift`.

**This revises §7.** Phase correlation does work on this subject, but the
constraint is not optional, and the overview frame is one way to supply it.
It is a stronger argument for capture-time position tracking than previously
made, and a strong argument against writing our own stitcher.

Note also: **`skimage.registration.phase_cross_correlation`'s `error` return is
unusable under its own default** — it returns ~1.0 unconditionally
([scikit-image#7078](https://github.com/scikit-image/scikit-image/issues/7078)).
`cv2.phaseCorrelate`'s `response` is a genuine confidence. Use OpenCV.

### Depth-map beats pyramid merging on glowing edges

Synthetic phase-contrast-like Z sweep with bright halo rings, max-Laplacian
(PMax/wavelet family) versus a smoothed regularised depth map:

| Metric | max-Laplacian | depth map |
|---|---|---|
| Selection-map speckle | 0.912 % | **0.011 %** (83× fewer) |
| RMSE near the glowing edge | 0.0264 | **0.0113** |
| Max overshoot (ringing) | +0.107 | **+0.044** |
| RMSE inside the halo ring | **0.0442** | 0.0507 |

Depth-map wins on exactly the two properties that matter here — background
purity beside a bright edge, and ringing — and loses slightly *inside* the halo,
which is the expected trade. Synthetic, so indicative rather than definitive.

**And no maintained, well-licensed, depth-map focus stacker exists in Python.**
That is the genuine gap in the ecosystem and the highest-leverage build in this
pipeline.

### Selected tools

| Role | Choice | Licence | Why |
|---|---|---|---|
| **Mosaic** | **m2stitch** | MIT | Pure Python, **no JVM**, v0.7.2 (2026-01). `position_initial_guess=` *clamps the search window* — the direct hand-off from capture-time tracking. Returns positions only, so we own the blend |
| Mosaic cross-check | ASHLAR | MIT | v1.20.0 (2026-04). Cleanest reject-then-predict-from-neighbours logic. Drags in a JVM via Bio-Formats |
| **Global layout** | `scipy.sparse.linalg.lsqr` | BSD | Pure translation makes this **linear** least squares — ~30 lines, one tile pinned for gauge. No packaged library exists and none is needed. Do not reach for g2o / Ceres / GTSAM |
| **Stack alignment** | focus-stack (Aimonen) | MIT | Active (2026-01). `findTransformECC` genuinely handles focus breathing. Use `--consistency=2` |
| **Stack merge** | **build a depth-map merger** | — | See above. Use focus-stack's wavelet output as baseline, not final |
| Blending | `cv2.detail_FeatherBlender` or distance-transform ramp | Apache-2.0 | **Not multiband.** Multiband hides parallax and exposure mismatch; after flat-fielding we have neither, and its cross-octave mixing is the mechanism that manufactures halos at a glowing edge. *(Mechanism-based reasoning, not a citation — verify empirically.)* |
| **Flat field** | median-of-tiles (ours) | — | Theoretically correct for sparse foreground. Keep as default |
| Flat field upgrade | BaSiCPy | MIT | Optional, for its darkfield offset term. **Guard and pin** — documented to return all-NaN darkfield on small stacks; v2.0 broke API (JAX→PyTorch) |
| Archival format | OME-TIFF via `tifffile` | BSD-3 | See below |
| RAW export | PiDNG | MIT | Pure Python, purpose-built for 12-bit Bayer |

**On storage:** DNG is likely the wrong *primary* format. No Python library
gives spec-compliant DNG for free, and its metadata is camera-shaped —
`AsShotNeutral` is meaningless in darkfield, and no Adobe calibration illuminant
describes a microscope condenser. Write OME-TIFF as archival; offer DNG as an
optional export for Lightroom/RawTherapee users.

### Licence blockers and traps

- **Original BaSiC (`marrlab/BaSiC`) is CC BY-NC-ND 4.0** — NonCommercial *and*
  NoDerivatives. Unusable. **BaSiCPy is a separate MIT codebase**; the taint
  does not carry.
- **Adobe DNG SDK** — proprietary EULA with an indemnification clause, a
  "further restriction" barred by GPLv3 §7.
- **CIDRE** bundles minFunc under CC BY-NC. Also dead since 2021.
- **`sjawhar/focus-stacking`** and **`cw1204772/depth_from_focus`** have no
  licence file, therefore all rights reserved. The second is the best
  architectural reference found (MRF depth-from-focus via graph cut) and can be
  read but not copied.
- **LibRaw's CDDL-1.0 option is GPL-incompatible.** Take LGPL-2.1.
- **Fiji Extended Depth of Field** — unresolved conflict. EPFL's site now states
  GPLv3; the repository's own `LICENSE.txt` still says research-only,
  no-redistribution, and has not been touched since 2017. Skip.
- **pystackreg** ships pre-relicence Thévenaz terms with a same-terms clause
  that conflicts with GPLv3 redistribution. Fine as an arms-length dependency;
  do not vendor.
- **Trap:** PyPI `focus-stack` v0.0.1 (2020) is an unrelated abandoned stub, not
  Aimonen's tool.

**Assumptions that were wrong in our favour:** Fiji/Stitching, BigStitcher,
`mpicbg` and enblend/enfuse are all GPL-2.0-**or-later** (verified in source
headers), hence GPLv3-compatible — the JVM is the cost, not the licence. MIST is
effectively public domain (NIST notice). `cv2.detail_*` blenders and seam
finders are in the plain `opencv-python` wheel, not contrib.

---

## 10d. Honest framing of novelty

Live mosaic guidance for a **manual** stage is already shipped by:

- **ToupTek ToupView** — green/yellow/red rectangle, "move the slides"
- **Zeiss ZEN Panorama** — documented verbatim as supporting "un-coded and
  un-motorized stages"
- **Olympus cellSens Manual MIA**, Microvisioneer mvSlide, ViewsIQ Panoptiq,
  PROMICRA QuickPHOTO, BioStitch-500
- **RT-4M** (research, Windows binary, research-use licence expiring
  2027-07-31)

So the accurate claim is **"first free Linux implementation"**, not "first
implementation". Still worthwhile — ToupTek withholds stitching and EDF from
ToupLite specifically, RT-4M ships no source, MicroMos is offline MATLAB, sWSI
never released — but the earlier framing in §4b was wrong.

### What does appear genuinely unbuilt

**Focus coverage — union the in-focus masks across a Z sweep and report
coverage %.** No prior art found in microscopy or astronomy. Every open-source
focus aid is a whole-frame scalar or, at best, Ekos' 3×3 tile grid for tilt
inspection. The entire public focus-peaking corpus is naive
high-pass-and-threshold, which §9 already establishes is useless in darkfield.

Given that Z stays hand-cranked, **this should be ranked above the mosaic work.**
It is a smaller feature and it is worth more.

### Negative finding

The amateur diatom and photomicrography community has **no open-source capture
tool of any kind.** Quekett recommends Helicon and Zerene; the MicrobeHunter
ToupTek threads are people running ToupLite on Ubuntu and getting capture only.
That entire space is post-processing, on Windows.

---

## 10e. Steal rather than write

| Source | Licence | What to take |
|---|---|---|
| **Squid / Cephla** `software/control/camera_toupcam.py` | BSD-3 | 46 KB production `ToupcamCamera(AbstractCamera)` — RAW mode, `OPTION_BITDEPTH`, binning→resolution map, strobe. Runs on Ubuntu, committed 2026-06. *Caveat: sits on ToupTek's unlicensed vendor binding* |
| **OpenFlexure** `camera_stage_mapping.py` | GPLv3 | Cross-correlates live frames as a **2D displacement encoder**, calibrates a px↔stage affine ([arXiv:2101.00933](https://arxiv.org/abs/2101.00933)). Our tracker, minus the motors |
| **Ekos** `align/polaralignmentassistant` | GPL-2.0-or-later | Already solves *guide a human hand with live image feedback* — recomputes error live from the image while the user turns manual knobs. Their documented caveat (continuous live estimation is expensive, therefore optional) is a constraint we will hit |
| **Ekos** `auxiliary/darklibrary` | GPL-2.0-or-later | Dark masters keyed on duration/binning/**temperature**, plus **defect maps** (hot-pixel lists) as a lighter alternative to full dark subtraction. Directly applicable to the per-profile calibration store, especially given this camera has no TEC |
| **Micro-Manager** `ImgSharpnessAnalysis.java` | BSD | Eleven focus metrics including **FFTBandpass** — the band-pass measure §9 argues for, already parameterised with cutoffs. Do not write a focus metric |
| **Ekos** `focus/focusblurriness.cpp` | GPL-2.0-or-later | Splits metrics into star-field (StdDev = **variance/mean**) vs extended-object (Sobel var/mean, Laplacian mean², Canny mean). The **variance/mean local-contrast normalisation §9 derived from first principles**, arrived at independently |
| **sWSI** ([JMIR 2017](https://mhealth.jmir.org/2017/9/e132/)) | not released | The UI design: mini-map plus four discrete operator states — *moving too fast*, *lost*, *touching a boundary*, *ok*. And the architecture: **downsampled realtime tracking on the live path, full-resolution stitching deferred offline.** 20 MP was never a realtime budget |
| **Olympus cellSens** Manual MIA | commercial | Two primitives nobody else has: **semi-transparent live overlay on the previous tile** (superimpose structures by eye) and **undo-last-frame**. Both cheap, both belong in v1 |

### Contradictions between survey passes — unresolved

Two claims came back in conflict. Neither is settled; both are cheap to check.

- **m2stitch.** One pass called it top pick for accepting seed positions via
  `position_initial_guess`. Another says it **requires a regular grid with
  row/column indices**, making it the wrong shape for freehand tiles, and points
  to **ASHLAR** (MIT, arbitrary per-tile positions, spanning-tree refinement,
  `--maximum-shift`) instead. For hand-driven capture ASHLAR looks the better
  fit. **§10a's recommendation is provisional until its API is read.**
- **ChimpStackr.** One pass read the current source and found Laplacian pyramid
  and weighted average only, no depth map. Another lists a depth-map mode. This
  matters — depth-map is the merge we want.

### Also settled

- **UVC is definitively out.** `libtoupcam.so` statically links libusb-1.0 and
  addresses usbfs directly (`/dev/bus/usb/%03u/%03u`); zero UVC/VIDIOC/videodev
  symbols. There is no V4L2 path.
- **No viable free reimplementation exists.**
  `openastroproject/libtouptek` (GPL-3.0) has the E3ISPM20000KPA in its camera
  table but contains **49,524 `notYetImplemented` entries** and implements no
  image acquisition at all. Abandoned after 18 days in 2021. The blob is the
  only road.
- **Micro-Manager's ToupTek path is broken on Linux.** The in-tree `AmScope`
  adapter is Windows-only and absent from the Linux build's SUBDIRS. ToupTek's
  own `mmgr` adapter targets Device Interface 68–74; MM moved to DI 75 on
  2026-02-26, so using it means pinning `pymmcore==11.10.0.74.1` (Oct 2024).

### Licence action item with a deadline

**Write the GPL linking exception now, while sole copyright holder.** After the
first outside contribution it requires their agreement too. One paragraph today
against a coordination problem later.

`oaCapture` (GPL-3.0) is the precedent for the `dlopen` half — its
`dynloader.c` `dlopen`s libtoupcam with per-symbol `dlsym` — and notably lacks
the exception half. INDI's `libtoupcam/COPYING.LGPL` sitting beside a
proprietary blob is the thing not to copy.

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
