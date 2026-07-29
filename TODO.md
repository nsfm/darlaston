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
- [x] ~~**SDK errors were unreadable and left the camera broken.**~~ Found on
      real hardware: the tenth tile of a mosaic failed and showed only
      "-2147417825". The vendor's Linux exception carries no message at all,
      just the signed HRESULT as its sole argument — and our matcher looked
      for the hex string, which could never match. Codes are now decoded from
      `.hr` into advice. Worse than the message: a throw inside `grab_raw`
      skipped `Stop()` and the preview restart, leaving the camera stopped in
      raw trigger mode, so *every later capture that session would also fail*.
      Restore is now in a `finally`, and transient codes (timeout, pending)
      buy exactly one retry.
- [ ] **Which error was the tenth tile?** Unknown — the old message threw the
      code away. Next occurrence will name it. If it was E_TIMEOUT the retry
      may now paper over it silently; if it repeats, suspect the cable or a
      hub before anything in the app.
- [ ] **A measured colour matrix.** The default is now XYZ→sRGB, which is a
      guess rather than a mistake, but a matrix measured from a colour target
      would be better than assuming sRGB primaries.
- [ ] **Exposure handoff.** Carry the live view's brightness into the capture
      at unity gain. Needs calibration to be verifiable. DESIGN.md §4.
- [x] ~~**Focus coverage.**~~ Per-pixel pass-through detection with a settled
      denominator, a meter, and an overlay showing *where* to keep racking.
      Verified against a synthetic tilted focal plane. Still needs trying on a
      real thick subject.
- [x] ~~**Stage tracking and the slide map.**~~ The image is the encoder:
      per-frame phase-correlation offsets integrate into a position, thumbnails
      bank passively at their tracked positions while hunting, pins mark points
      of interest with a fields-and-compass readout for the return trip. The
      foundation for mosaic anchoring, hold-still, and Z auto-trigger.
      Verified end to end against the mock stage (sign, scale, gating).
- [ ] **Tracker on real glass.** The confidence gate (0.10) and the hold-on-
      blank behaviour are tuned against the mock; darkfield and phase will
      stress both differently. Also worth measuring whether static overlays
      (vignette, sensor-window dust) bias the offsets on real optics — on the
      mock they measurably do not, so a high-pass prefilter was tried and
      reverted rather than shipped on theory.
- [ ] **Map scale changes with the objective.** Clearing on setup-dialog accept
      is wired; the objective *stepper* and future turret auto-detection are
      not. When magnification becomes known per objective, positions could be
      rescaled instead of discarded.
- [ ] **Z-stacks.** Movement detection, settle, auto-trigger. Depends on
      coverage to know when a sweep is done.
- [x] ~~**"Hold still" during a capture.**~~ A banner over the frozen preview
      for exactly the window where motion does damage, and a measured verdict
      afterwards: the tracker's position before the pull vs the first
      correlation across the preview gap. Moved shots stay on disk and offer
      discard-and-reshoot, with Keep as the default button. The guard only
      arms above 0.5 correlation confidence (measured: structure ≥ 0.85,
      featureless ≤ 0.3) so a blank field is never falsely accused. End to
      end on the mock: a mid-exposure crank of 451.3 px read as 451.1.
- [x] ~~**Mosaic capture.**~~ Toggle on the map panel; every capture becomes
      `tile_NNN.dng` in a session folder with `manifest.json` rewritten after
      each change (a mosaic interrupted at tile 23 is still a mosaic). Tiles
      paint brass-outlined on the map at their tracked positions; the status
      line reads out overlap with the nearest tile (steer for 15–25%, green
      when there); undo-last-tile deletes file and record. Positions recorded
      as registration *constraints*, dead-reckoned — measured 0.15% against
      the mock's commanded motion. A discarded moved shot never becomes a
      tile. `MosaicSession.load()` reopens an interrupted session.
- [x] ~~**Mosaic stitch, first light.**~~ `process/stitch.py`: minimal reader
      for our own DNGs (60 lines, refuses anything we did not write),
      registration by phase correlation on overlap strips *constrained* to a
      window around manifest positions (it never searches — that is why
      panorama software fails on diatoms and this cannot), global least
      squares anchored at tile 0, raised-cosine composite to a linear DNG.
      Capture-menu action stitches any session folder off the UI thread.
      Proven on the mock: ±45 raw px of injected manifest drift, recovered
      within 3 px; five tiles, 6/6 seams refined, seams invisible.
- [x] ~~**First light on real glass.**~~ Nine brightfield tiles at 40×/0.75,
      hand-laid: 11/11 seams refined, 3.8 s, positions from a 60-field
      hand-cranked map. Tracking holds at a modest pace and loses lock when
      cranked fast — expected, and the map shows it rather than lying.
- [x] ~~**Composite-time flat from the tiles themselves.**~~ Per-tile median
      normalisation, across-tile median, then a robust degree-4 surface fit.
      The fit is not decoration: a plain median grows *holes* where subject
      covers a pixel in more than half the tiles (measured: 1.8% of pixels),
      and dividing by a hole puts a bright blob in the composite — a surface
      cannot do that. Recovers a known 25% vignette to 0.014 sparse / 0.024
      dense, where the plain median gave 0.044 / 0.365. On real tiles that
      already had `calibration=flat+wb` applied it still found 18.7% residual
      shading and cut mean seam mismatch 10.9% → 6.0%.
- [ ] **Why does a capture-time flat leave 18.7%?** The tiles carried
      `flat+wb` and still showed strong residual shading. Either the stored
      flat was built under different lamp/condenser state, or the flat path
      is not doing what it claims. Measure a fresh flat immediately before a
      mosaic and re-run the comparison — this is the highest-value open
      question, because it may indicate a real defect in the calibration
      path rather than drift.
- [ ] **Bake a thumbnail into our DNGs.** System thumbnailers refuse the big
      files, and a 275 MP composite is unopenable-looking in a file manager.
      Structural, not cosmetic: a conformant DNG puts a *reduced* image in
      IFD0 (`NewSubfileType = 1`) and the real one in a SubIFD, but pidng
      writes the full image directly into IFD0 with no SubIFDs tag at all —
      verified by parsing our own output. So this cannot be done by adding a
      tag; it needs the IFD tree restructured after pidng writes, or our own
      writer. We already parse TIFF for `read_bayer_dng`, so writing one is
      within reach and stays dependency-free. Do it for composites first,
      where it hurts most.
- [x] ~~**Output size is a knob now.**~~ Capture → Stitch mosaic… measures the
      real geometry from the manifest and prices every choice before starting
      (Nate's 17-tile run: full 19718 × 13925 = 275 MP / 1.65 GB, half 69 MP,
      quarter 17 MP). Compositing is banded, so peak memory is the finished
      image plus one band rather than a 3.3 GB float accumulator that grew
      with the square of the area covered. Choices past the 4 GB a classic
      TIFF can address are disabled rather than offered and then failed.
- [ ] **The DNG writer is now the memory bottleneck.** Banding cut the
      compositor to the finished image plus one band, and measurement then
      showed the peak had moved: writing a 1.63 GB linear DNG through pidng
      costs about 3.2 GB *on top of* the array — 272 MP peaked at 6.84 GB,
      most of it in the write. Our own strip-based TIFF writer fixes this and
      the thumbnail item above in one stroke, and removes the last thing
      standing between us and a 40-tile mosaic.
- [ ] **Beyond 4 GB.** A big enough mosaic cannot be a DNG at all — classic
      TIFF offsets are 32-bit. BigTIFF or a pyramidal TIFF is the answer for
      viewing; the linear DNG stays the right output while it fits.
- [ ] **Stitch, the rest.** Full-resolution composite streamed band-by-band
      (current default renders at 0.25 scale into RAM; fine to ~10 tiles,
      not at 40). Verify the GBRG→OpenCV demosaic code choice on real glass
      (`_DEMOSAIC` in stitch.py — the mock is grey and cannot catch a channel
      swap). Real darkfield tiles are the acid test. Undo does not yet
      re-anchor if tile 1 is undone (edge case: undoing the origin tile).

## Capture features

- [x] ~~**Timelapse.**~~ Capture menu → dialog (interval, count or
      until-stopped, GB-and-duration estimate before starting). Each shot is
      an ordinary StillCapture — same calibration, metadata, sequence and
      moved verdict. Start-to-start scheduling so the run does not drift; an
      overrun fires immediately rather than skipping. Status strip carries
      progress, GB written, free space, and a dark-gone-stale warning past
      eight hours. The moved-shot dialog is suppressed during a run — an
      unattended timelapse must never park behind a modal question.
      Composes with averaging (each slot can be a burst).
- [x] ~~**Frame averaging**~~, on its own control beside the shutter (— / ×4 /
      ×16, not persisted: a hero shot is a decision, not a mode). The mean is
      scaled ×16 into 16-bit with the white level tag raised to match, so the
      sub-LSB precision the burst paid for survives the file. The hold-still
      guard spans the whole burst; the shutter counts frames so sixteen
      exposures never look like a hang. Averaging arithmetic verified end to
      end through the written DNG. Still to check on real glass: that
      darktable honours the 65520 white level (it should — it is just a tag).
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
