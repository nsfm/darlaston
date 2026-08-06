# Next up

- [ ] **A scale bar on the photograph.** Drawn on the printed plate
      already, and nowhere else. Not a measurement tool: we already compute
      the number from pixel pitch and total magnification, and putting it on
      the picture costs almost nothing. Nice to have rather than urgent --
      see the note on what this program is for.
- [ ] **Flyby: the orchestrated version.** Design notes in `spike/FLYBY.md`,
      deliberately not started. A stacked mosaic is a four-dimensional
      recording -- x, y, zoom and focal plane -- and every move through it
      can be perfectly smooth because it is synthesised rather than
      performed, which is virtual camera work on a slide and is offered
      because we keep both the mosaic and the depth. The thing that makes
      it a big feature rather than a small one is circular: a flyby can
      only be planned over a mosaic that already exists, and the mosaic
      that should exist depends on the flyby. Nate's answer is to plan
      over the live slide map first -- sketch the path, get the minimum
      coverage and an honest size estimate, then shoot it -- which turns a
      rendering feature into a capture feature. Order and reasoning in the
      doc.
- [ ] **Auto-exposure.** The SDK has policy (exposure or gain preferred),
      a target percentile and damping coefficients, and darlaston is
      manual-only, which is fine for a practised operator and a black screen
      for anyone else. Note what ToupLite learned: it disables auto-exposure
      during stacking and stitching, because a moving exposure ruins both.
      Being manual is why that has never bitten us, and it would have to be
      reproduced along with the feature.
- [ ] **Turret belief is the rotation sign, not the detector.** The A/B
      above reproduces it cleanly and rules out the arithmetic: driving the
      mock through a rotation with direction +1 reads back as -1, and four
      of five rotations name the wrong objective on _both_ routes. So this
      is the optical path's sign convention, which is the UX question
      already raised rather than a detection-tuning one.
- [ ] **The tracker must not run at a divisor**, recorded so it is not
      re-proposed. `StageTracker.MAX_STEP` rejects a single-frame shift past
      0.35 of the frame, which is 7.0 fields/second at 30 fps; a divisor of
      2 halves that to 3.5, and routine hand motion at 40x is well past it.
      A rejected frame does not merely skip -- `advance()` returns without
      integrating, so the displacement is silently and permanently lost from
      the accumulated position. It would also double the settle latency
      before a capture is allowed, 0.27 s to 0.53 s.
- [ ] **Detection can never land on an empty turret position.** The
      _marking_ shipped: `Turret.capped` exists, the setup editor offers a
      "capped" box on every empty position, and `model_signatures()` predicts
      0.0 for a capped slot and `condenser_na**2` for an open one -- opposite
      ends of the scale, which is the whole reason the two were separated.
      What is missing is anywhere for that prediction to be used.
      `Turret.step()` skips empty positions, so the neighbour set `_decide()`
      builds from it never contains one, and the magnification and brightness
      arms then skip them again explicitly
      (`if turret.positions[i] is None: continue`). So no proposal can name
      an empty detent and the cap state cannot influence detection at all --
      even though you can physically park on one, and a frame going white or
      black is the loudest thing the darkness sweep will ever see. The mock
      can occlude the field on demand, so both cases can be simulated as
      soon as there is something to test.
- [ ] **Which error was the tenth tile?** Unknown — the old message threw the
      code away. Next occurrence will name it. If it was E_TIMEOUT the retry
      may now paper over it silently; if it repeats, suspect the cable or a
      hub before anything in the app.
- [ ] **A measured colour matrix.** The default is now XYZ→sRGB, which is a
      guess rather than a mistake, but a matrix measured from a colour target
      would be better than assuming sRGB primaries.
- [ ] **Exposure handoff.** Carry the live view's brightness into the capture
      at unity gain. Needs calibration to be verifiable.
- [ ] **Tracker on real glass.** The confidence gate (0.10) and the hold-on-
      blank behaviour are tuned against the mock; darkfield and phase will
      stress both differently. Also worth measuring whether static overlays
      (vignette, sensor-window dust) bias the offsets on real optics — on the
      mock they measurably do not, so a high-pass prefilter was tried and
      reverted rather than shipped on theory.
- [ ] **Map scale changes with the objective.** Clearing on setup-dialog accept
      is wired; the objective _stepper_ and future turret auto-detection are
      not. When magnification becomes known per objective, positions could be
      rescaled instead of discarded.
- [ ] **Spike `Toupcam_CtiEnable`.** `libtoupcam` is a GenTL _consumer_
      (`TLOpen`, `IFOpenDevice`, `GENICAM_GENTL64_PATH` in its strings);
      the call succeeds and is non-destructive. If a third-party GenTL
      producer makes Basler/IDS/Daheng cameras enumerate through
      `EnumV2`, that is an industrial-camera backend for one line.
      Entirely unproven — needs a borrowed camera and an hour.
- [ ] **A retouch brush.** Both commercial stackers' answer to the
      physically-unfixable halo is a human with a brush, and in our
      architecture that is cheap: the aligned slices and the depth map
      already exist on disk, so "take this region from slice N" is a
      local paint operation on the depth map that then flows through the
      blend we already have. The zone at a depth discontinuity genuinely
      has no correct pixel — a ray is observed twice, once sharp and once
      as a blur circle — so no algorithm will ever finish this job, which
      is exactly why Zerene and Helicon both ship the brush instead.
      Wants: a slice picker, a soft round brush, undo, and a live
      before/after. The stack window is where it belongs.
- [ ] **Tell people when there is a newer version.** A copy handed to a
      stranger is a copy that never updates. Check a published version
      file on a timer, mention it quietly in the status bar, and never
      block on it or phone home with anything identifying — a lookup of a
      static file, no telemetry, and an off switch in settings. Ship the
      switch before the check.
- [ ] **The turret-belief question is a UX one, not a detection one.**
      The 16×/0.4-on-a-25× session traced to a prompt being ignored and
      the turret never being set before the capture, which the software
      then faithfully recorded. Detection is not the thing to tune. The
      question is what the app should do when its belief is _stale rather
      than wrong_: an ignored proposal already marks the objective
      uncertain, but nothing stops a capture from being written with an
      uncertain belief, and nothing surfaces it at the moment it matters.
      Options worth weighing: refuse-and-ask at capture time (harsh but
      unmissable), write "objective: unconfirmed" into the file rather
      than a confident wrong number, or make the uncertain state loud in
      the rail instead of a small "?".
- [ ] **The slide map as a finding aid.** The other half of the plate
      idea, still open: export the accumulated map — pins, thumbnails,
      µm coordinates — as a printable sheet. For a catalogued mount that
      is an archival artifact, and Victorian mounters drew them by hand.
- [ ] **Nate's mosaic EXIF says 16×/0.4 and it was shot at 25×/0.65.**
      Found while building the plate: every file in
      `260729-230442_mosaic_tiles` records the wrong objective, so the
      turret belief was stale for that whole session (the stack folders
      from the same evening are correct). Worth finding out whether an
      unanswered proposal, a missed detent, or the startup guess is
      responsible — the optics metadata is only as good as that belief,
      and now the scale bar depends on it too.
- [ ] **Down-then-back-up stacks.** Nate racks down through the subject
      and back up, so slice order revisits planes: the merge is untouched
      (argmax picks the sharpest wherever it lives) but slice index stops
      being monotonic depth, which muddies depth.png as _geometry_ — the
      wigglegram's z is capture order. Options: teach the habit (one
      direction reads best), or order slices by estimated z at merge time
      (cross-slice sharpness correlation could recover the turning point).
- [ ] **Stack polish, next pass.** Respect `keep_slices` after a verified
      merge; a `metric` per slice is recorded as 0.0 (thread the real value
      from signals); coverage-complete could suggest finishing; real-glass
      trigger thresholds need a session on the Zeiss — the floor
      self-calibrates but has never met real hand tremor.
- [ ] **Why does stage tracking undershoot Y at 25×?** The drift that
      broke registration was systematic: monotonic, one direction, ~14%
      of traveled distance. Candidates: per-objective calibration error
      in preview-px-per-µm, tracking loss during capture blackouts, or
      anisotropy in the tracker. The stitcher now survives it, but the
      minimap would place fields better if dead reckoning were honest.
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
- [ ] **Why does a capture-time flat leave 18.7%?** The tiles carried
      `flat+wb` and still showed strong residual shading. Either the stored
      flat was built under different lamp/condenser state, or the flat path
      is not doing what it claims. Measure a fresh flat immediately before a
      mosaic and re-run the comparison — this is the highest-value open
      question, because it may indicate a real defect in the calibration
      path rather than drift.
- [ ] **Deflate is a dead end for us, and it is worth knowing why.**
      Implemented and round-tripping through our own reader, but rawspeed
      refuses it: "Only float format is supported for deflate-compressed
      data" — the DNG spec only allows Deflate on floating-point samples.
      Measured cost of the restriction: 27.0 MB with deflate versus 30.0 MB
      packed, so it was buying 10%, not a category change. Left in the code
      behind no UI. A float DNG would unlock it and cost more than it saves.
- [ ] **Binned capture as a size option.** `grab_raw` hard-codes full
      resolution. The sensor's own binned modes would give 7.5 MB at
      2736×1824 and 3.3 MB at 1824×1216, packed — a real choice for survey
      work where 20 MP per tile is not the point.
- [ ] **Confirm the system thumbnailer is happy.** The preview is in the
      right place and extracts correctly, but whether a given file manager
      picks it up depends on that thumbnailer.
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

## Capture features

- [ ] **Inverted brightfield** native in the live view. Display and export
      transform only; the raw stays linear positive.

## Optics and measurement

- [ ] **Optical profiler.** Stage micrometer plus grid target → µm/pixel,
      distortion, field curvature, lateral CA, MTF50 centre vs corner, and a
      computed **usable field fraction** that derives the crop radius instead
      of leaving it to judgement. Also settles AmScope adapter vs planar relay
      eyepiece with numbers.
- [ ] **Brightness signatures on real glass.** The third signal is wired and
      learns from every confirmed rotation, per illumination mode — which
      matters most for the 6.3×, which has no phase ring and goes darkfield
      against the phase stop. Whether the learned values stay stable across a
      session (lamp drift, iris adjustments) is the open question.
- [ ] Re-measure sensor dust after cleaning the C-mount adapter. Baseline is
      0.93% overall, 1.02% at fine scale, 57 features near the cover glass.

- [ ] **Stop the preview during a long timelapse.** Nate's idea, and the
      reasoning is sound: a 30 fps preview between shots that are minutes
      apart is an enormous amount of readout for nothing, and readout is
      what heats a sensor. Dark current roughly doubles every 6-8 C, so
      the preview may be *causing* the drift the timelapse warning
      describes. Wake the stream only shortly before each frame, and show
      the last capture in the meantime rather than a live view.

      Cheap to do: a mode change measured about a second, which is
      nothing against a minute-long interval. Two things to check first
      -- the stage tracker feeds on preview frames, which is fine for a
      timelapse where nothing moves but means the hold-still guard is
      unavailable; and stopping a UVC stream may drop the manual exposure
      and white balance we set on open, so they would need re-applying
      each time. Worth measuring the actual sensor warming before and
      after, since the whole premise is that it matters.

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
- [ ] **? The window frames, on Windows and macOS.** Checklist in
      `docs/frame-bench.md`, written to be worked through by somebody with
      the hardware. Most of the frame is a pure function of rectangles and
      is tested on any machine; the rest reaches past Qt into the platform
      and has never run. The two that matter most: a maximised window on a
      scaled display, and the macOS title bar surviving a trip through
      fullscreen.
- [ ] **? Snap layouts below half-screen.** The window's minimum width is
      766 px and Microsoft's limit for snap layouts is 500, so the
      half-screen layouts work and the third- and quarter-width ones
      invoke and then fail to snap. Traced to three constraints in a
      chain -- the rail fixed at 286, the live view's 480 minimum and the
      waiting page's 458 -- so relaxing the live view alone bottoms out
      at 744 and changes nothing. The one that bites is a third of 1920,
      the commonest screen. Three positions and the numbers behind them
      in `docs/frame-bench.md`; it is a judgement about what this program
      is for, so it is measured rather than quietly changed.

## Packaging

.

- [ ] **A build people can double-click.** `pip install` is a developer
      distribution and the users are microscopists. **AppImage** rather
      than Flatpak: the two hard requirements here are raw USB device
      access and loading a vendor SDK from the user's home directory, and
      a sandbox makes both awkward at once. Briefcase can produce it, and
      a tag-triggered release job is a small addition to the existing
      build job.
- [ ] **macOS: wired, unverified.** The platform-specific pieces are done
      and tested by simulation -- the loader finds `mac/libtoupcam.dylib`
      (a universal binary, so Intel and Apple silicon share one file), the
      SDK installer verifies that build, `--list-cameras` stops claiming
      "none on the bus" where there is no sysfs to survey, and the presence
      check answers _try_ rather than _no_, which had left the window
      waiting for ever with a camera plugged in. Nobody has run it on a
      Mac. Next: pull it down there, `--list-cameras`, then a capture.
      Known remaining: `--usb` is Linux only (V4L2 ioctls), and config
      lives in `~/.config/darlaston` rather than `~/Library`, which works
      and is consistent but is not the platform convention.
- [ ] **Windows testing.** Library naming is handled (`toupcam.dll`, no
      `lib` prefix) and nothing else has been looked at. Support that
      cannot be tested is a claim, not a feature.

## Later

- [ ] **Gallery and basic develop view (v2).** The sidecar above is the
      camera's JPEG, one honest rendering with no choices in it. This is the
      other half: browsing what you shot, and a few sliders over the raw for
      people who are not raw photographers and never will be.
- [ ] **Setup card** — "what's your setup?", neofetch style. Drafted in
      `session/setup_card.py`; Nate is workshopping the design.
- [ ] **Audio focus tone.** Nobody in the surveyed field does this. Demoted
      from likely-useful once it emerged that the screen is the viewfinder,
      since camera and eyepieces are rarely parfocal — but still cheap and
      still interesting.
- [ ] **Z motorisation.** 0.8 MOD gear ring on the fine focus, NEMA 17 +
      TMC2209 + RP2040, ~$35. Optional accelerator only; the unmodified-scope
      workflow must be complete without it.
- [ ] Session resumability, needed once mosaics reach 40 tiles. The
      _budgeting_ half is done: free space is on the status bar permanently,
      brass under 20 GB and red under 2, so a session no longer dies of a
      full disk without warning. Resuming an interrupted one is still open.
