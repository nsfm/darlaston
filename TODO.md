# Next up

- [ ] **Flyby: the orchestrated version.** Design notes in `spike/FLYBY.md`, A stacked mosaic is a four-dimensional
      recording - x, y, zoom and focal plane - and every move through it
      can be perfectly smooth because it is synthesised rather than
      performed, which is virtual camera work on a slide and is offered
      because we keep both the mosaic and the depth. The thing that makes
      it a big feature rather than a small one is circular: a flyby can
      only be planned over a mosaic that already exists, and the mosaic
      that should exist depends on the flyby. Plan
      over the live slide map first - sketch the path, get the minimum
      coverage and an honest size estimate, then shoot it - which turns a
      rendering feature into a capture feature. Order and reasoning in the
      doc.
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
- [ ] **A measured colour matrix.** The default is now XYZ→sRGB, which is a
      guess rather than a mistake, but a matrix measured from a colour target
      would be better than assuming sRGB primaries.
- [ ] **Exposure handoff.** Carry the live view's brightness into the capture
      at unity gain. Needs calibration to be verifiable.
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
- [ ] **The slide map as a finding aid.** The other half of the plate
      idea, still open: export the accumulated map — pins, thumbnails,
      µm coordinates — as a printable sheet. For a catalogued mount that
      is an archival artifact, and Victorian mounters drew them by hand.
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

- [ ] **A background mask, and what it might be worth.** Measured
      2026-08-07 on two of Nate's stacks; the numbers are strong enough
      that this is a plan rather than an idea.

      **The fault.** `depth.png` is `argmax` over per-slice sharpness, and
      argmax is undefined where there is no sharpness. In a featureless
      region every slice scores about zero plus sensor noise, so the
      winner is whichever slice had the largest noise excursion at that
      pixel: a near-uniform random draw. Measured against a perfectly
      uniform draw, the diatom stack's background depth came out at 0.75
      of uniform entropy, spread over 18 of its 30 slices. The
      pseudoscorpion's was 0.38 over 7 of 15.

      This is also why the composites look fine while the depth maps do
      not, which was Nate's observation and is the same fact from the
      other side: the merge takes the winning slice's *pixel*, and where
      every slice looks identical a random one is harmless. The depth map
      takes its *index*, and there the coin toss is the whole output.

      **The signal.** Peak sharpness separates background from specimen by
      three orders of magnitude: 0.10 against 96.8 on the pseudoscorpion,
      0.10 against 146.1 on the diatoms. Gating at the specimen's 5th
      percentile catches 100% and 99.7% of background. There are two
      decades of empty space to put a threshold in, so the usual worry
      about a tuned constant does not apply here -- and it need not be a
      constant at all: the populations separate on a log scale, so Otsu on
      `log(peak)` finds the gap per stack. Verified against an inverted
      stack standing in for darkfield: the threshold moved from 10^0.27 to
      10^0.80 and the mask barely changed, 25.0% of frame against 22.7%.

      **What it fixes.** Background depth standard deviation went 86.5 to
      0 and 51.9 to 0, because those pixels stop voting. Nate: "the
      diatoms are vastly improved", and the PLYs are the best he has had
      from this.

      **Growing it from seeds.** The gate is right that a smooth
      translucent interior was never in focus and wrong that it is not
      specimen: the pseudoscorpion's pedipalps came out as lace. Treating
      the gate as *seeds* rather than as a decision fixes it. Well above
      the gate is certain specimen, well below is certain background, and
      the wide band between goes to the composite's own colour model
      (grabCut, seeded by those markers rather than by a drawn rectangle).
      The interiors filled in and the mask grew only 2% of frame, so it is
      filling the specimen it had already found rather than bleeding.
      The diatoms moved 25.0% to 26.1%, which is the right outcome: they
      are textured throughout, so there was nothing to carry.

      Two caveats: grabCut runs on a downscaled field so its boundaries
      are softer than the gate's, and it is iterative, so its cost on a
      real merge wants measuring before it goes near the pipeline.

      **The next algorithm, and the interesting one.** Growth recovers
      *membership*, not *height*. A smooth carapace is correctly marked
      specimen and still has no depth to put there, because no slice ever
      found focus on it. The same seeded logic should interpolate depth:
      let a confident rim imply the surface across the smooth interior it
      encloses, so the region takes the height its own boundary predicts
      rather than a flat fill. That is a real algorithm rather than a
      capture workaround, and it works where "use more contrast" is not
      available. Candidate shapes: solve Laplace over the unconfident
      region with the confident boundary as its Dirichlet condition, which
      is the standard membrane interpolation and has the right smoothness;
      or push the existing joint-bilateral weighted median further, since
      `_refine_depth` already has the machinery and already lets
      textureless pixels defer to textured neighbours.

      **The background plane.** Filling at the 2nd percentile of specimen
      depth puts it in *front* of the subject: Nate's upright
      pseudoscorpion had its background at the ceiling with the specimen
      depressed into it but correctly convex, and inverting fixed the
      floor while turning the specimen concave, because inversion flips
      both together. The fill belongs behind the specimen, not at an
      extreme of its range. Keep the plane rather than dropping the
      vertices: Nate wants it, since a solid background anchors the
      subject for printing.

- [ ] **? Is the halo a background problem?** Nate's observation and
      possibly the most valuable thing here. The mask removed the diatom
      blur-haloes completely from the PLY. The retouch-brush entry above
      calls that halo physically unfixable and cites both commercial
      stackers shipping a human with a brush as the answer, on the
      grounds that at a depth discontinuity a ray is genuinely observed
      twice, once sharp and once as a blur circle.

      If a good share of what that brush exists for is instead
      out-of-plane material landing on *empty field*, then it is a
      background problem, it is separable, and it is fixable without a
      human. That would be a real result rather than a tidier render.

      It has to go through `tools/stack_bench.py` rather than through
      anybody's eye: "the haloes look gone" and "the composite error
      fell" are different claims and only the second is defensible. The
      bench already has a synthetic glow case to measure against.

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
- [ ] **Stop the preview during a long timelapse.** Nate's idea, and the
      reasoning is sound: a 30 fps preview between shots that are minutes
      apart is an enormous amount of readout for nothing, and readout is
      what heats a sensor. Dark current roughly doubles every 6-8 C, so
      the preview may be _causing_ the drift the timelapse warning
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
- [ ] **? Sony Camera Remote SDK on Linux for the A6700**, for the second
      backend.
- [ ] **? Dead-reckoning drift** across a 40-tile mosaic — is overview
      anchoring sufficient, or is a brightfield registration pass needed?
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
      chain - the rail fixed at 286, the live view's 480 minimum and the
      waiting page's 458 - so relaxing the live view alone bottoms out
      at 744 and changes nothing. The one that bites is a third of 1920,
      the commonest screen.

## Packaging

- [ ] **Windows testing.** Library naming is handled (`toupcam.dll`, no
      `lib` prefix) and nothing else has been looked at.

## Later

- [ ] **Gallery and basic develop view (v2).** The sidecar above is the
      camera's JPEG, one honest rendering with no choices in it. This is the
      other half: browsing what you shot, and a few sliders over the raw for
      people who are not raw photographers and never will be.
- [ ] **Setup card.** "what's your setup?", neofetch style
- [ ] **Session resumability.** To rescue interrupted mosaics
