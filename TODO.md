# TODO

Everything asked for, deferred, or left unverified — gathered from the design
docs and from conversation so it stops living in scrollback.

Ordered within each section by how much it would change what gets built.
`?` marks something unverified rather than merely unbuilt.

---

## Next up

- [x] ~~**Single capture.**~~ Shutter → DNG with metadata, subject and slide
      fields, settings for location and naming. Calibration is not applied
      yet — that is the next item.
- [x] ~~**Structured metadata.**~~ pidng names no `UserComment` or ISO tag, but
      its `Tag` entries are only `(id, Type)` pairs and `set()` takes any of
      them — so both are defined locally rather than reached for through
      exiftool. No new dependency.
- [x] ~~**The preview histogram measured the wrong thing.**~~ Green-based
      warning now (free, honest to ~12%), plus a measured preview LUT during
      calibration that reports headroom as two numbers: fraction pinned and the
      raw level that implies. A neutral-looking preview and an honest
      per-channel histogram are mutually exclusive from 8-bit data — the ISP
      must boost blue ~3x, so blue's information is destroyed before we see it.
- [ ] **UI sweep.** Spacing, resizing behaviour, layout. Deliberately deferred
      until more elements exist, so the pass is done once against the finished
      set rather than twice. The rail is now dense enough that this is getting
      closer.
- [x] ~~**Calibration engine.**~~ Store keyed by product lifetime, dark
      averaging with a defect map, flat medianing with per-Bayer-phase
      normalisation, measured white balance, opportunistic blank banking, and
      the preview LUT. Dark path verified on real hardware.
- [x] ~~**Wire calibration into the UI.**~~ Status panel, guided routines,
      opportunistic banking on the tracker's motion estimate. Preview
      profiling verified on real hardware.
- [x] ~~**Apply calibration at capture.**~~ Dark, flat, defects and white
      balance are looked up and applied; what was used is recorded in the file
      and reported in the result line.
- [x] ~~**Verify the flat path on real glass.**~~ Ran end to end. Two things
      came out of it: opportunistic banking started before the flat step was
      reached (correct, but startling), and a two-field flat medians to its
      mean and rejects nothing.
- [x] ~~**Multiple scopes.**~~ Stands are a collection now, stored separately
      from cameras, with stable ids derived from the name. One scope selects
      itself silently; the picker only appears when there is a choice. The
      camera remembers which stand it was last on.
- [ ] **Empty vs capped turret positions.** Currently one state. They behave
      oppositely for visual detection — an empty slot passes light, a capped
      one blocks it — so the darkness sweep would read them as different
      events entirely.
- [ ] **A measured colour matrix.** The default is now XYZ→sRGB, which is a
      guess rather than a mistake, but a matrix measured from a colour target
      would be better than assuming sRGB primaries.
- [ ] **Exposure handoff.** Carry the live view's brightness into the capture
      at unity gain. Needs calibration to be verifiable. DESIGN.md §4.
- [x] ~~**Focus coverage.**~~ Per-pixel pass-through detection with a settled
      denominator, a meter, and an overlay showing *where* to keep racking.
      Verified against a synthetic tilted focal plane. Still needs trying on a
      real thick subject.
- [ ] **Z-stacks.** Movement detection, settle, auto-trigger. Depends on
      coverage to know when a sweep is done.
- [ ] **"Hold still" during a capture.** A full-resolution pull takes over a
      second, and the operator has no reason to know that — carrying on racking
      mid-shot smears the slice. Needs an unmissable state during the exposure,
      and probably a discard-and-retry if motion is detected while it runs.
- [ ] **Mosaic.** Overview anchoring, minimap, coverage, undo-last-tile.

## Capture features

- [ ] **Timelapse.** Interval capture. Interacts with calibration (dark drift
      over a long run) and with disk budgeting.
- [ ] **Frame averaging**, hero shots only, on its own control — √N SNR, and
      the reason this sensor can beat the A6700 despite smaller pixels.
- [ ] **Inverted brightfield** native in the live view. Display and export
      transform only; the raw stays linear positive.
- [ ] Optovar position in the UI, feeding total magnification and metadata.

## Optics and measurement

- [ ] **Optical profiler.** Stage micrometer plus grid target → µm/pixel,
      distortion, field curvature, lateral CA, MTF50 centre vs corner, and a
      computed **usable field fraction** that derives the crop radius instead
      of leaving it to judgement. Also settles AmScope adapter vs planar relay
      eyepiece with numbers.
- [ ] **Turret auto-detection.** `live/turret.py` exists and is unproven
      against real optics. Needs a magnification axis in the mock to test
      properly, then real turret rotations.
- [ ] Re-measure sensor dust after cleaning the C-mount adapter. Baseline is
      0.93% overall, 1.02% at fine scale, 57 features near the cover glass.

## Unverified

- [ ] **? `imagepro_stitch` on sparse darkfield.** The largest open question in
      the project. If it grades diatoms `GOOD` it saves months on Linux and
      Windows — but it has no arm64 slice on macOS, so the portable path is
      ours regardless.
- [ ] **? m2stitch's grid requirement.** One survey pass called it the top pick
      for accepting seed positions; another said it needs a regular grid with
      row/column indices, which would make ASHLAR the better fit for freehand
      tiles. Read the API.
- [ ] **? ChimpStackr's depth-map mode.** Two passes disagreed on whether it
      has one. Matters, because depth-map is the merge we want.
- [ ] **? Resolution index [1]:** true on-sensor binning or pixel skipping?
      The SDK reports 4.80 µm pixels, but `get_BinningNumber` returns 0.
      Settle empirically: shoot both, software-average the full-res one, and
      compare noise.
- [ ] **? Can the optics out-resolve the binned mode at all?** If the relay is
      the limit, full resolution records empty magnification. The profiler
      answers this.
- [ ] **? Do the SDK's FFC/DFC apply in the raw path or only the ISP path?**
- [ ] **? Sustained frame rate for full-res 12-bit raw** over this link.
      Bandwidth arithmetic says ~8–10 fps, not the rated 15.
- [ ] **? Sony Camera Remote SDK on Linux for the A6700**, for the second
      backend.
- [ ] **? Dead-reckoning drift** across a 40-tile mosaic — is overview
      anchoring sufficient, or is a brightfield registration pass needed?
- [ ] **? Darkfield raw capture.** Never actually shot. The extreme case: 90.6%
      of pixels in four luma levels at 8-bit, sixty-four at 12.

## Legal and release

- [ ] **Write the GPL linking exception now**, while sole copyright holder.
      After the first outside contribution it needs their agreement too.
- [ ] Verify `focus-stack`'s licence before vendoring (MIT as of Jan 2026).

## Packaging

- [ ] **Briefcase** builds: `.dmg` for macOS with signing and notarisation,
      AppImage/deb for Linux, `.msi` for Windows.
- [ ] **First-run SDK fetch.** We never redistribute the ToupTek blob, but
      "download this other thing first" is where most users quit.
- [ ] **Windows testing.** Support that cannot be tested is a claim, not a
      feature. Linux and macOS supported, Windows best-effort until someone
      with the hardware actually runs it.

## Later

- [ ] **Gallery and basic develop view (v2).** Most microscopists are not raw
      photographers; they want a JPEG and sliders in the app. `develop.py`'s
      primitives are kept warm for this.
- [ ] **Setup card** — "what's your setup?", neofetch style. Drafted in
      `session/setup_card.py`; Nate is workshopping the design.
- [ ] **Audio focus tone.** Nobody in the surveyed field does this. Demoted
      from likely-useful once it emerged that the screen is the viewfinder,
      since camera and eyepieces are rarely parfocal — but still cheap and
      still interesting.
- [ ] **Z motorisation.** 0.8 MOD gear ring on the fine focus, NEMA 17 +
      TMC2209 + RP2040, ~$35. Optional accelerator only; the unmodified-scope
      workflow must be complete without it. DISCOVERY.md §9b.
- [ ] Setup editor, so the provisional scope in `ui/main.py` can go away.
- [ ] Session resumability and disk budgeting, needed once mosaics reach 40
      tiles (~47 GB of slices if none are discarded).
