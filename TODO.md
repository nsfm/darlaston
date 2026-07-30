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
- [ ] **The rail needs restructuring, not scrolling.** It now holds eleven
      groups and calibration is squashed at the top. The honest read is that
      the rail is doing two different jobs: *setup* (calibration, preview
      profile, measurement region, metric) which is touched at the start of a
      session and then left, and *shooting* (exposure, focus, coverage,
      subject, optics, average, shutter) which is touched constantly. A
      scrollbar would preserve that confusion and add hunting to it. Split by
      how often a thing is touched, keep the shooting controls always visible
      and unscrolled, and move setup behind its own surface. Collapsible
      sections are the fallback if the split proves wrong, not the first move.
- [x] ~~**Live loop profiled.**~~ Frame-rate regression measured and fixed,
      A/B on the same harness: 24.65 → 18.44 ms with peaking off, 42.21 →
      17.64 ms with peaking and Z sweep on (23.7 → 56.7 fps ceiling). Three
      causes, all measured: the clipping test built three full-frame boolean
      temporaries every frame (10.9 ms; `cv2.split` plus per-plane calcHist
      is 3.4 ms and hands back the green plane the focus metric needed
      anyway), the sharpness field ran at full preview resolution when both
      its consumers are insensitive to that (16.6 → 4.9 ms at half size), and
      coverage recomputed its structure mask three times per update and used
      boolean-mask indexing to average (13.7 → 1.3 ms).
- [x] ~~**UI thread profiled too.**~~ Qt was smooth-scaling a 2.2 MP frame to
      the widget every paint (15.5 ms with the peaking overlay); the resize
      moved to OpenCV in `set_frame` and painting became a blit, 15.5 → 1.7 ms.
      The peaking overlay's `np.quantile` sorted the whole field to find one
      element, 5.2 → 0.87 ms with a partition. Instruments repaint at a third
      of frame rate. The mock camera itself cost 61 ms/frame while cranking
      and had been the real bottleneck in every earlier measurement.
- [x] ~~**Frame rate is adjustable.**~~ 60 was too much on real hardware:
      25–45 fps with a quarter of frames discarded, and every dropped frame
      was still pulled over USB with the driver holding the GIL. Default 40,
      selectable in the status bar (15/24/30/40/60/uncapped) because the right
      number depends on the machine, the link and the preview resolution — and
      because pinning a CPU to render frames nobody sees is not a feature.
- [ ] **Does 40 hold?** Watch the drop percentage in the status bar. If it is
      still climbing, the next suspect is the map canvas, which redraws every
      banked thumbnail on every position change — cheap at 20 fields, unknown
      at 200.
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
- [x] ~~**Z-stacks.**~~ The knob is the interface: rack, pause, a slice
      captures itself; rack again. The trigger reads focus motion from the
      sharpness field (racking is invisible to the xy tracker), with
      Pearson-on-mean-removed distances, a self-calibrating noise floor, and
      settle judged against the motion's own peak *and* the floor — each
      refinement traced to a measured failure. One honest limit: inside the
      depth of field, racking and pausing are optically identical, so a
      continuous rack may fire once near best focus; that slice is one a
      stack wants anyway. Moved slices discard and retake themselves — the
      plane is easy to revisit, a dialog is not. StackSession mirrors
      MosaicSession exactly, so a mosaic tile can one day *be* a stack.
      The merge is depth-map based: align (constrained phase correlation),
      small-pooled Tenengrad per slice, running argmax + joint-bilateral
      weighted-median refinement + median for depth, feathered one-hot
      blend, streamed DNG out — plus `depth.png`, kept because a
      depth map is a measurement of the subject's shape and diagnostic gold.
      Proven on the mock's tilted focal plane: merged output sharp in every
      strip where no single slice was, end to end through the running app
      (7 slices auto-captured, assembled live, merged with 7 depth levels).
- [ ] **The assembly panel is the fun part** — the all-in-focus image builds
      live as slices land. Winner-takes-all at preview resolution, no
      alignment; its job is feel, not accuracy.
- [x] ~~**Stacks and mosaics shipped four stops dark.**~~ Found by Nate
      pushing every stack +3 EV in post. The blend of 12-bit sources lands
      back in 0..4095, and both composite writers declared a 16-bit white
      level against it — 4095/65535 is exactly 1/16, four stops. Scaled ×16
      into the declared range now, which also keeps the sub-LSB precision
      the weighted blend creates, exactly as frame averaging does. Verified
      on the real 18-slice stack: relative mean 0.173 against the slices'
      0.174. A regression test holds the levels together from now on.
- [ ] **Wigglegram / stereo from the depth map** — Nate's idea and a genuinely
      good one. `depth.png` is now plain grayscale (near dark, far light),
      the encoding depth consumers expect; `depth_view.png` keeps the pretty
      colormap. Depth + all-in-focus is everything DIBR needs: synthesise two
      or three parallax views and emit an anaglyph or a wobble GIF. The data
      is already on disk after every merge.
- [x] ~~**Stack output re-mosaicked, 119 → 40 MB.**~~ Nate asked whether the
      merge could be re-mosaicked, and the answer is better than yes:
      bilinear demosaic passes each site's native value through untouched, so
      sampling the blended RGB back onto the GBRG grid recovers exactly the
      per-site blend of aligned raw values. Nothing meaningful is lost, and
      the developer's demosaic — better than ours — does the final
      interpolation. Default output now; linear stays as an option. Tested:
      bayer round-trips and is under 40% of the linear size.
- [x] ~~**Composites carry provenance.**~~ The stacked and stitched outputs
      read the EXIF back out of a middle slice/tile — photographer, optics,
      exposure — because it is one photograph made of many exposures and it
      should say who took it and through what.
- [x] ~~**The stack window is the control surface.**~~ Finish & merge and
      Discard (confirmed — it is the one button that destroys data) live in
      the assembly window, with the merge's three-stage progress bar. The
      "depth" toggle tints each region by the slice that won it — the
      depth data as a living image while it is still being collected — and
      is on by default (Nate: "it's more exciting to watch"). After the
      merge the endings have all happened, so Finish and Discard give way
      to a single Close.
- [x] ~~**Halo at depth edges.**~~ Measured before fixing, and the measuring
      mattered: the first theory (edge-aware *pooling* of the sharpness
      field, guided filter) tested strictly worse than the plain Gaussian —
      a sharpness ridge sits exactly on the luma edge and spans every luma
      level, so no similarity kernel can exclude it. Joint-bilateral pooling
      barely moved it for the same reason. What worked: pool *small*
      (sigma 1.5, accurate but speckled), then refine the **depth map** —
      the thing that actually is piecewise-constant w.r.t. the image — with
      a joint-bilateral weighted median (r=8, votes = luma similarity ×
      own sharpness evidence), and make the blend follow the refined depth
      via feathered one-hot weights instead of raw field powers. Synthetic
      halo case: band misassignment 54% → 3%, band RMSE 238 → 103, depth
      boundary ±11.5 px → ±0.9 px; parameters insensitive across a 4×
      sweep. On Nate's real 20-slice stack: background particles beside the
      diatom resolve as particles instead of mush, halo rings mostly gone,
      striae legible in the depth map — which answers his depth-resolution
      ask too. Running argmax replaced the field stack (~360 MB less on 20
      slices); merge is 41 s for 20 slices. Regression test holds the
      synthetic halo band under 10%.
- [x] ~~**Terracing in glow aprons (one ridge per exposure).**~~ Nate's field
      report, root-caused as integer depth quantisation: in a glow band the
      winner at each radius is whichever slice's defocused edge-ring lands
      there — contour lines of the argmax. Fixed by two measured stages on
      the new benchmark (`tools/stack_bench.py`, five synthetic scenes with
      ground truth): log-parabolic sub-slice depth (slope RMSE 0.298 → 0.099
      against the 0.289 integer floor) and push-pull diffusion gated on
      *fine-scale* Laplacian energy of the winning slice (glow rings have
      gradients but no fine detail; Tenengrad confidence measured the wrong
      pixels as MORE confident). Terrace scene: 21.4% → 6.4% wrong. Weber
      normalisation (÷ local mean) of the focus measure: composite error in
      the band 100 → 66. Research sweep (Zerene/Helicon docs, SFF
      literature, four open-source stackers read at source) confirmed the
      architecture — continuous depth + two-frame lerp + vote-gate-then-
      inpaint is what every shipped solution converges on — and the bench
      *refuted* four plausible ideas from the same sweep: guided-filter
      pooling, SML as primary measure, band-passed luma, and Guo's
      whole-curve Gaussian fit (worse in-pipeline on 4 of 5 scenes).
- [x] ~~**OpenCV 5 phaseCorrelate mutates its inputs.**~~ Found because the
      terrace scene measured 6.4% wrong through the bench and 70% through
      merge(): cv2 5.0's phaseCorrelate applies the Hann window to its
      input arrays IN PLACE. Three real call sites shielded with copies:
      stack registration (middle slices were windowed twice, ends once),
      the live stage tracker (every frame correlated a twice-windowed past
      against a once-windowed present — a standing tracking bias), and
      mosaic registration (at small overlaps the strips were *views* into
      tile lumas reused for other neighbours). Regression test pins it.
- [x] ~~**Stacking knobs.**~~ Gear menu in the assembly window, bound to
      persisted settings: glow smoothing (off/light/normal/strong), seam
      feather (1/2/4 px), output (bayer/linear).
- [x] ~~**A/B on two real stacks of the same subject** (Nate's 25×/0.65
      diatoms, 13 slices each).~~ He reported the *older* merge looked
      better — more even glow, more striae — and he was right. Measured
      end to end, fine detail against the best single slice and
      manufactured glow structure against a typical slice:
      off 0.753/0.716, light 0.752/0.549, normal 0.736/0.287,
      strong 0.699/0.171. Findings, several of them corrections:
      (a) the tent blend introduced with continuous depth was the detail
      loss — averaging two slices at a sharp feature is softer than either,
      and worse, cross-fading two *defocused* slices does not produce
      intermediate defocus, it produces both their rings at partial
      opacity, so it made glow structure worse too (0.58 vs one-hot 0.74
      was the wrong-direction reading from a proxy; see (d));
      (b) the depth *diffusion* does nearly all the glow work at 2% detail;
      (c) blending across many slices buys little for real detail cost;
      (d) **a half-resolution proxy of the blend is not trustworthy for
      the glow metric** — it showed diffusion doing nothing (0.718→0.738)
      where the full merge showed 0.716→0.287. Only end-to-end merges
      count. Refuted along the way: peak-prominence confidence (at 25×/0.65
      the DoF spans several slices, so a real striae winner is not
      distinctly sharper — textured median 1.38 vs glow 1.26), and trust
      dilation as a free lunch (it interpolates between off and normal
      rather than beating both, though r=5 is where detail becomes
      indistinguishable from winner-takes-all, which is the "light"
      preset and the new default).
- [ ] **Stack polish, next pass.** Respect `keep_slices` after a verified
      merge; a `metric` per slice is recorded as 0.0 (thread the real value
      from signals); coverage-complete could suggest finishing; real-glass
      trigger thresholds need a session on the Zeiss — the floor
      self-calibrates but has never met real hand tremor.
- [ ] **Measured candidates in waiting** (from the research sweep, each goes
      through `tools/stack_bench.py` before shipping): CombineZP's ramp
      subtraction (monotone-vs-peaked profile test — the only shipped
      glow-specific detector found anywhere); EDF-style reassignment (snap
      blended pixels to the nearest real slice value); GFF base/detail
      split (smooth weights for low frequencies, tight for detail —
      targets low-frequency residual halo); per-slice photometric gain
      normalisation (Zerene does it by default; terracing's third
      mechanism); 16-bit depth.png export (8-bit quantises the now-
      continuous map); a retouch brush ("take this region from slice N" —
      both commercial vendors' answer to the physically unfixable halo,
      cheap for us since aligned slices + depth map already exist);
      capture-side step-size hint from NA/magnification (3-4 steps per
      DoF is the community rule and we know both numbers).
- [ ] **The dream composition: stacked mosaics.** Both halves now exist and
      share the same session shape. A mosaic tile becomes a stack folder,
      tiles close as you leave them (stack merges in the background), and
      the stitcher consumes `stacked.dng` instead of `tile_NNN.dng`. This is
      the project's original thesis — complaint #4 — one sprint away.
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
- [x] ~~**Our own DNG writer.**~~ `process/tiff.py`. Strip-based, streaming,
      little-endian, 8/12/16-bit. IFD0 now holds a real reduced-resolution
      preview with the image in a SubIFD, which is the structure thumbnailers
      and DNG readers actually expect. Verified against darktable 5.4.1:
      12-bit Bayer, 16-bit Bayer and the 272 MP linear composite all decode
      clean. Peak memory to write the 1.63 GB composite fell 6.84 → 3.05 GB;
      a single capture fell 39.7 → 30.0 MB. `read_bayer_dng` grew to follow
      SubIFDs and unpack 12-bit while still reading every pidng file already
      on disk — there are mosaics in the old shape and they must keep working.
- [ ] **Deflate is a dead end for us, and it is worth knowing why.**
      Implemented and round-tripping through our own reader, but rawspeed
      refuses it: "Only float format is supported for deflate-compressed
      data" — the DNG spec only allows Deflate on floating-point samples.
      Measured cost of the restriction: 27.0 MB with deflate versus 30.0 MB
      packed, so it was buying 10%, not a category change. Left in the code
      behind no UI. A float DNG would unlock it and cost more than it saves.
- [x] ~~**Raw file size.**~~ 12-bit packing is on by default for single
      frames: 39.7 → 30.0 MB, lossless, since the sensor is 12-bit and the
      other four bits were always zeroes. Averaged frames stay 16-bit — the
      mean carries real sub-LSB precision and packing it would throw away
      exactly the SNR the burst paid for.
- [x] ~~**pidng removed entirely.**~~ Nothing outside `camera/` now depends on
      anything but numpy, OpenCV and Qt. `process/dng.py` is the DNG
      vocabulary; `process/tiff.py` writes the bytes. The Software tag reads
      `darlaston` alone — pidng was stamping itself into every file we made.
- [x] ~~**Optics in the fields raw editors show.**~~ Both derived, neither
      invented: focal length is the stand's tube length over the objective's
      magnification (160/40 = 4 mm on a Zeiss Universal, and tube length is
      now a scope property, since infinity stands use a tube *lens* focal
      length instead), and the f-number is total magnification over twice the
      NA, because the cone reaching the sensor has NA_image = NA_obj / M. A
      40×/0.75 writes f/26.7 — which is the honest explanation for why more
      magnification stops adding detail. Verified with exiv2: F27, 4.0 mm,
      ISO 100, lens "40x/0.75". Missing NA writes no f-number rather than a
      guess.
- [ ] **Binned capture as a size option.** `grab_raw` hard-codes full
      resolution. The sensor's own binned modes would give 7.5 MB at
      2736×1824 and 3.3 MB at 1824×1216, packed — a real choice for survey
      work where 20 MP per tile is not the point.
- [x] ~~**Thumbnails baked into our DNGs.**~~ Every file we write now carries
      a gamma-corrected, white-balanced RGB preview in IFD0. The balance is
      estimated for the preview even when the file itself carries no measured
      neutral, because a raw microscope field rendered flat is a green
      rectangle that tells the operator nothing.
- [x] ~~**The full EXIF set.**~~ 76 tags now. The one that matters most is
      `FocalPlaneXResolution` — the SDK reports a 2.40 µm pitch and we had
      been discarding it, so nothing in the file said how big anything was.
      Also: lens make/serial/specification, working distance as
      SubjectDistance, ImageNumber, sub-second timestamps for timelapse
      ordering, LightSource (Tungsten — every mode on this stand is the same
      halogen lamp), a pixel fingerprint as ImageUniqueID, Artist and
      Copyright, and the manual/no-flash/one-chip constants that stop a
      reader guessing. Verified with exiv2, not by inspection.
- [ ] **A caught mistake worth remembering.** EXIF and TIFF/EP both define
      focal-plane resolution at *different* tag numbers (0xA20E vs 0x920E).
      The TIFF/EP numbers produce structurally valid tags that exiv2 silently
      skips — the scale metadata was in the file and unreadable. Any future
      tag should be verified by reading it back with an outside tool, never
      by checking the writer's own output.
- [x] ~~**Photographer identity.**~~ Session → Photographer…, with a licence
      picker because typing a CC string from memory is how it gets wrong.
      Empty writes no tag: an unset copyright notice looks like a claim the
      work is unowned.
- [ ] **Confirm the system thumbnailer is happy.** The preview is in the
      right place and extracts correctly, but whether a given file manager
      picks it up depends on that thumbnailer. Nate to check.
- [x] ~~**Output size is a knob now.**~~ Capture → Stitch mosaic… measures the
      real geometry from the manifest and prices every choice before starting
      (Nate's 17-tile run: full 19718 × 13925 = 275 MP / 1.65 GB, half 69 MP,
      quarter 17 MP). Compositing is banded, so peak memory is the finished
      image plus one band rather than a 3.3 GB float accumulator that grew
      with the square of the area covered. Choices past the 4 GB a classic
      TIFF can address are disabled rather than offered and then failed.
- [ ] **The composite still holds one full canvas.** With the writer fixed,
      peak is 3.05 GB for 272 MP and the remainder is the uint16 result
      array plus the registration lumas. Rendering bands on demand into the
      writer, rather than filling a canvas and then streaming it, would drop
      it again — and the writer's `rows` callback is already the right shape
      for it.
- [ ] **Beyond 4 GB.** A big enough mosaic cannot be a DNG at all — classic
      TIFF offsets are 32-bit. BigTIFF or a pyramidal TIFF is the answer for
      viewing; the linear DNG stays the right output while it fits.
- [ ] **Stitch, the rest.** Full-resolution composite streamed band-by-band
      (current default renders at 0.25 scale into RAM; fine to ~10 tiles,
      not at 40). Verify the GBRG→OpenCV demosaic code choice on real glass
      (`_DEMOSAIC` in stitch.py — the mock is grey and cannot catch a channel
      swap). Real darkfield tiles are the acid test. Undo does not yet
      re-anchor if tile 1 is undone (edge case: undoing the origin tile).

- [ ] **Docs sync pass.** DISCOVERY/DESIGN/ARCHITECTURE predate the own-DNG
      writer, Z-stacks, turret detection, the floating-panel UI and the
      re-mosaic output. TODO.md has carried the state; the design docs should
      catch up in one deliberate pass rather than dribble.
- [ ] **Strip Co-Authored-By trailers when re-signing.** Every commit from
      the 2026-07-28/29 sessions carries one against Nate's standing
      preference (harness default won silently). `git rebase` + `--no-gpg-sign`
      removal pass pending anyway for signatures.

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
- [x] ~~**Turret auto-detection, wired.**~~ 6/6 single-step rotations
      identified on the mock, with the measured field-of-view ratio inside
      15% of truth rather than merely ranking the right answer first. Costs
      1.1 ms a frame. A proposal bar appears over the live view and expires
      after twenty seconds — doing nothing is a valid answer and means no,
      because the objective keys every calibration lookup and being quietly
      wrong would attach the wrong flat to everything after.
      Four bugs found by testing it, all of which had been sitting unexercised:
      `cv2.logPolar` was removed in OpenCV 4 (it is `warpPolar` with
      `WARP_POLAR_LOG`, and its scale constant is defined differently); the
      measured ratio and the expected one were reciprocals, so the comparison
      could never have matched; the reference frame was captured *after*
      darkening began, so the magnification was measured against a
      third-occluded frame and came out threefold wrong; and agreement was
      declared without checking the fit was good, so a badly measured ratio
      that happened to rank correctly produced a confident corroboration out
      of noise.
- [ ] **Turret detection on real optics.** The mock's rotation is clean:
      perfectly parcentric, uniform occlusion, no defocus on arrival. Real
      objectives are none of those. Expect the magnification measurement to
      be the fragile half — log-polar correlation assumes a shared centre,
      and a badly parcentric turret breaks that assumption directly.
- [x] ~~**Image handedness is settable, and self-teaching.**~~ Confirmed
      inverted on the Zeiss: 6.3× → 10× proposed 40×, exactly one step the
      wrong way. Settable in Microscope setup… beside the condenser's working
      NA, but it should never need touching — one correction teaches it.
- [x] ~~**Proposals fired mid-rotation.**~~ The settle-based escape from
      darkness treated a *steady* level as an arrival, and a turret held
      mid-turn is perfectly steady and perfectly black — so a proposal could
      be made from an occluded frame, using measurements taken through the
      turret body. Recovery now also requires an actual image, tested after
      averaging down to 32 squares so that sensor noise on a black frame is
      not mistaken for structure.
- [x] ~~**The direction setting calibrates itself.**~~ Correcting one wrong
      proposal now teaches it: the raw occlusion reading is reported
      separately from the interpreted direction, so accepting a different
      objective than the one suggested says exactly which sign was right, and
      it is saved — from the proposal bar *or* from the objective stepper,
      because reaching for the stepper is the natural motion when a
      suggestion is wrong. And until it is confirmed the bar offers **both**
      candidate objectives rather than picking one and being wrong half the
      time. Renamed from "turret order" to "image handedness", because
      the old name implied a claim about the operator's turret when it is
      really the product of four optical stages — the objective inverting the
      image, the head and photo tube, however the camera is screwed onto its
      C-mount, and our own vertical flip of the bottom-up raw stream.
- [ ] **Brightness signatures on real glass.** The third signal is wired and
      learns from every confirmed rotation, per illumination mode — which
      matters most for the 6.3×, which has no phase ring and goes darkfield
      against the phase stop. Whether the learned values stay stable across a
      session (lamp drift, iris adjustments) is the open question.
- [ ] **Empty vs capped slots, now testable.** The mock can occlude the field
      on demand, so the two cases can finally be simulated: an empty slot
      passes light and a capped one blocks it, which the darkness sweep reads
      as opposite events.
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
