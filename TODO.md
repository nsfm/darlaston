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
- [x] ~~**Does 40 hold?**~~ On this machine yes, on a smaller one no, and
      the suspect named here was innocent: the map canvas measures 0.30 ms,
      about 1% of the frame. Answered properly by `Instrument >
      Performance`, which reports per-feature milliseconds against the frame
      budget, always on because a profiler you have to enable is one that is
      off when the interesting stall happens. First real-camera reading was
      24.0 ms against a 25 ms budget with every optional feature off, so
      there was no headroom at all; peaking and a sweep took it to 31.3 ms
      and it dropped frames.
- [x] ~~**Live loop profiled again, on the real camera this time.**~~ Worth
      24.0 ms to 8.9 ms, and frames dropped to zero at every core count
      tested. Nearly all of it was allocation and scale factors rather than
      arithmetic, and the same scale-factor trap turned up in three separate
      places, so it is worth stating once as a rule: **OpenCV only has a
      fast box-average INTER_AREA path when the scale factor is a whole
      number, and reducing to a smaller size with a worse factor is
      routinely slower than reducing to a larger one with a good factor.**
      Specifically, splitting the preview into planes allocated three fresh
      2.2 MP buffers per frame, 3217 minor page faults of kernel mapping and
      zeroing 6.6 MB thirty times a second, which was almost the whole of
      that stage against about 0.8 ms of actual deinterleave. The tracker
      downscaled to a square 512, a scale factor of 3.5625, which misses
      OpenCV's fast box-average path; an exact quarter hits it, 2.0 ms to
      0.73 ms, and is aspect-correct as well. Shrinking further is a trap
      and was measured as one -- a 256 square is 7.125x and comes out
      *slower* than what it replaced. Windowing before correlating removes
      the phaseCorrelate in-place mutation hazard rather than defending
      against it with a copy of both inputs every call.
- [x] ~~**The preview rate follows the machine.**~~ It defaulted to 40
      everywhere. Pinned to fewer cores on the same camera and scene:
      sixteen cores 12.7 ms and no drops, four cores 29.0 ms, two cores
      37.5 ms and a sixth of frames dropped. The stages are only about
      3.5 ms of real work at two cores, so the rest is the analysis thread
      being descheduled while the SDK demosaics and Qt paints on the same
      two cores -- which means less work arriving, not cheaper work, is the
      cure. Capping 40 to 15 took a two-core machine from 185% of a core to
      80%, more than any feature in the table can give. Defaults are now 40
      at eight cores or more, 30 at four, 15 below that. After both that and
      the stage work, every size tested fits its budget and drops nothing:
      sixteen cores 8.9 ms and 297% of a core, four cores 12.6 ms and 156%,
      two cores 24.8 ms and 92% -- a two-core laptop now runs the whole
      application inside a single core, where before it needed most of two
      and still dropped a sixth of the frames.
- [x] ~~**Faster analysis does not quiet the fans by itself**~~, and the
      panel cannot show why. Halving the frame cost took the loop from 30 fps
      to about 40 at essentially unchanged CPU: the work per frame fell and
      the number of frames rose to match, because the camera was always
      delivering more than the loop could take. What did quiet it was giving
      OpenCV fewer threads, below.
- [x] ~~**OpenCV was taking the whole machine for small work.**~~ This was
      where the fan noise lived, and the frame table could never show it:
      that table measures wall-clock per stage, and the waste was in how the
      wall-clock was bought. Sixteen worker threads burned 293% of a core to
      do about 61% of a core of real work, because once the stages were made
      cheap they became too small to be worth spreading. Capped to four:
      299% and 14.9 ms a frame becomes 191% and 16.4 ms, over five
      back-to-back pairs. The knee is sharp on the other side, three costs
      19.7 ms and two costs 24.0 ms of a 25 ms budget.
      **Refuted along the way:** this was first written as a live-loop cap
      that batch jobs opted out of, since stitching and merging are
      throughput work. Measuring it took the idea apart -- both are bound by
      reading a few hundred megabytes of DNG and by the parts that were
      never parallel. A fifteen-slice merge is 14.1-14.3 s at sixteen
      threads against 14.5-14.7 s at four, but spends 22.5 s of CPU rather
      than 17.2 s to get there; a fifteen-tile stitch is 6.7 s against
      7.0 s. So the context manager and its eight call sites came back out.
- [x] ~~**The exposure levels are computed at the rate they are looked
      at.**~~ The histogram repaints at a third of frame rate and nothing
      accumulates between repaints, so two of every three were computed and
      thrown away -- and they are the most thread-hungry work in the loop,
      which is the worst thing to do at full rate once the process has four
      threads. 7.19 ms to 2.5 ms, more than the arithmetic alone predicts
      because it also stops three full-frame passes contending. Subsampling
      the pixels instead is unsafe and there is a test saying so: a
      one-pixel-tall clipped streak vanishes from a row-strided histogram.
- [x] ~~**Preview scale is the largest stage left**~~, 4.7 to 7.2 ms, on the
      UI thread. It is a 1.756x reduction so it misses the whole-number fast
      path, and both ways around that spend picture quality -- which makes
      it a preference rather than a fix. **Instrument > Performance
      settings** now offers all three, persisted, applying live:
      full 4.26 ms at 1.00x edge energy, reduced 0.70 ms at 0.48x (detail
      genuinely gone), fast 0.65 ms at 1.98x (edge energy *invented*, since
      a cheaper filter cannot honestly find more detail than the correct
      one -- that excess is the shimmer on striae). Switching live on the
      camera moves the stage 4.52 to 1.06 to 0.99 ms and total CPU by about
      28%. Default stays on full.
- [x] ~~**Pick a preview-quality default.**~~ `fast`, on the measurement
      and on Nate's eye, which agreed. My own warning against it was wrong
      and worth recording why: the near-Nyquist sine grating I tested it on
      was built to alias, and areolae on a binned preview are nowhere near
      the sampling limit. On a real tile from a 16x mosaic it came within
      0.8 of 255 levels of the honest reduction, and under sub-pixel stage
      motion -- which is what shimmer actually is, and what a still frame
      cannot show -- it churned 6% more rather than the visible beating the
      synthetic test predicted. `reduced` was the one that read as soft,
      losing a third of the edge energy. Full detail is a click away for
      anyone who wants to count pixels.
- [x] ~~**Turret watch**~~ was the largest stage left and turned out to be
      the same trap a third time: it built its 256 square from the full
      frame, 1824/256 is 7.125, and that one downscale was 3.88 ms of a
      4.01 ms stage. It now reduces the quarter-size frame the tracker
      already made. A/B through the real pipeline over five rotations: byte
      identical proposals, directions and confidences, 5.18 ms to 0.70 ms.
      A divisor was measured too and is *not* worth taking -- safe up to
      N=3, but only 0.35 ms once the resize is fixed, against tripled
      detection latency.
- [x] ~~**GPU acceleration: measured, and declined for now.**~~ OpenCV 5
      has a working OpenCL path here through `cv2.UMat`, needing no new
      dependency. Split is 11x on it, the resizes 8-12x, histograms 3.6x;
      Sobel and Gaussian blur are *slower*, 0.75x and 0.55x, so the focus
      metric and peaking would want to stay put.
      **The 4.4x I first measured was flattered by my own benchmark.** It
      kept everything resident on the card and came back to host memory
      twice. The real loop hands data to six consumers that want host
      memory -- phase correlation, the turret detector, the blank check,
      the focus metric, the preview copy Qt keeps, and the histogram values
      it indexes -- and each is a download. Written the way the loop would
      actually need it: 22.52 ms against 14.26 ms, **1.58x**, not 4.4x.
      And it is not a switch. `cv2.ocl.setUseOpenCL(True)` does nothing on
      its own -- measured 7.14 ms against 7.47 ms for a split -- because
      the acceleration only engages for `cv2.UMat` inputs. So it is a
      change to every line that touches a frame, plus a numpy path kept
      alongside for the machines that cannot use it.
      There is also a clincher for the one stage worth offloading: a GPU
      preview scale is about 1.56 ms with its transfers, and the `fast`
      setting already does it on the processor in about 1 ms.
      **The watts say the opposite of the milliseconds, and the watts are
      what the question was about.** Paced at 40 fps, moving that chain to
      the card frees 0.14 of a processor core and costs 10.2 W at the card.
      It frees so little because OpenCL's synchronisation spins rather than
      sleeps -- the core waits busily instead of being released. And the
      10 W is almost entirely the cost of *waking* the discrete card, not
      of the work: the same chain at 8 fps drew 11.8 W against 12.2 W at
      40 fps. A fifth of the work for the same power, which kills the
      obvious compromise of offloading only the preview scale.
      The interesting configuration would be the integrated Intel graphics,
      which are already powered for the display and so have no waking to
      pay for. It is not available on this machine, and the reason is not a
      missing package: `intel-compute-runtime` is installed, ocl-icd does
      load it, and OpenCV really does map `libigdrcl.so`. The runtime then
      refuses the hardware -- `Unknown device: deviceId: 9bc4`, which is
      this CometLake-H GT2 -- because Intel dropped Gen8 through Gen11 from
      the mainline NEO releases. It would need the 24.35 legacy line from
      the AUR. Not chased further: the integrated part shares system memory
      with the processor rather than bringing its own, and has a fraction
      of the discrete card's compute, so its ceiling sits below the 1.58x
      the discrete card managed -- for the same integration cost. That last
      point is reasoning rather than measurement, since the runtime will
      not run here to be measured.
      Worth revisiting only if the live view ever needs full sensor
      resolution, where the arithmetic would grow past what the transfer
      and the wake cost. Today the loop uses about 15 ms of a 25 ms budget
      and drops nothing, so speed is not the constraint and a second code
      path would be tested on far fewer machines than the first.
- [x] ~~**Read ToupLite's feature surface, and the SDK's.**~~ Its string
      table is 1767 entries and the largest family by far is *measurement*
      -- about 200 strings for calibration, scale bars, line/circle/angle/
      polygon/arrow/text annotation, a measurement sheet, statistics, and
      Word/Excel report templates. That is what people buy this class of
      software for, and it is deliberately not what this is. The SDK is 146
      documented options and darlaston uses ten of them.
      **Asked the camera rather than the header, which killed the exciting
      ideas.** On the E3ISPM20000KPA: conversion gain (LCG/HCG), low-noise
      mode, the hardware sequencer, hardware HDR synthesis, precise frame
      rate, bandwidth throttling, TEC/fan/heater and autofocus are all
      *unsupported*. Available and unused: binning, black level, fixed
      pattern noise correction, defect pixel correction, auto-exposure
      policy and percentage, rotate, and the count of frames the driver
      dropped.
- [x] ~~**Does the SDK's defect correction touch the raw path?**~~ No, and
      it matters because darlaston measures its own defect map: if the
      driver were patching the raw we would be describing an
      already-repaired sensor and correcting twice. Measured by toggling it
      between full-resolution grabs -- the difference between on and off
      (mean 0.062, max 10) came out *smaller* than between two consecutive
      frames at the same setting (mean 0.072, max 13), so it is noise. The
      option only reaches the ISP path. FFC and DFC are separate options and
      remain untested; they need a captured reference before they do
      anything.
- [ ] **A scale bar on the photograph.** Drawn on the printed plate already,
      and nowhere else. It is the one piece of measurement worth having --
      not a measurement tool, provenance: a micrograph published without one
      is unreadable, and we already compute the number from pixel pitch and
      total magnification. Wanted on captures, merged stacks and composites,
      probably as a choice between burnt-in and left off.
- [ ] **Report frames the driver dropped**, from
      `TOUPCAM_OPTION_NUMBER_DROP_FRAME`, beside our own count in the
      performance panel. Ours says the loop could not keep up; this one says
      the link or the hub could not, and today those two failures look
      identical from the outside.
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
      of five rotations name the wrong objective on *both* routes. So this
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
- [x] ~~**Wigglegram / stereo from the depth map.**~~ Dessert, served:
      `process/wiggle.py` synthesises parallax views from stacked.dng +
      depth.png (backward warp, depth softened so edge tears become leans,
      amplitude 1.2% of width). Artifacts land beside the stack:
      `wiggle.webm` (VP9 — HARD MODE request; cv2 5's VideoWriter encodes
      it despite a cosmetic tag warning, verified by readback), the
      auto-looping `wiggle.webp`, a crossed-eye `stereo_pair.png`, and a
      red/cyan `anaglyph.png` on a desaturated base. Wigglegram button in
      the finished stack window + Capture menu entry for old stacks.
      Depth polarity is a habit, not a fact (slice 1 is wherever racking
      started) — default matches Nate's rack direction, invertible in the
      gear menu. Test pins that the two halves of a two-plane scene shift
      oppositely between the stereo eyes.
- [x] ~~**Fake DIC from brightfield.**~~ `process/relief.py`: light the
      depth map obliquely and shade it, so depth *gradients* become
      relief — the same kind of quantity differential interference
      contrast displays, which on a real stand needs a Wollaston prism, a
      polariser, an analyser and a strain-free objective. Renders
      `dic.png` (grey) and `dic_tinted.png` (Nomarski-ish warm/cool bias)
      beside the stack, and rides along with the wigglegram button.
      Depth alone renders as smooth putty (it cannot resolve finer than a
      slice) and image texture alone is just an emboss filter, so the
      surface mixes both; only the ratio matters and 0.07 is where the
      frustule's form *and* its striae both read, swept against a real
      13-slice stack. Labelled a render, not a measurement — true DIC
      responds to refractive index through the whole thickness, this
      responds to where focus peaked. Test: a depth ridge must light one
      flank and shadow the other, flat field neutral.
- [x] ~~**Synthetic aperture: choose the focal plane after the shot.**~~
      `process/aperture.py`. The stack kept what a photograph normally
      throws away — which plane was sharp — so the choice is reversible:
      blur each pixel by how far its depth sits from a chosen plane, and
      widen that per unit depth to open the aperture. Two artifacts: a
      still refocused anywhere, and `focus_pull.webm`, the plane drifting
      through the subject on video with smoothstep easing at both ends
      (a constant-rate pull reads as a machine sweeping) and a reversed
      tail so it loops without a jump cut. Layered rendering — the image
      blurred once at six radii, each pixel reading between the two
      layers matching its defocus — because per-pixel blur is exact and
      unusably slow. Occlusion is not modelled (a defocused foreground
      softens rather than bleeding over its background), the same honest
      limit the parallax renders carry.
- [x] ~~**Renders were washing out.**~~ `_develop` set a white point and
      no black point, so a brightfield field — mostly bright background —
      crowded into the top third of the scale. Obvious the moment a focus
      pull filled the frame with that background. Both ends are set now,
      which lifted every parallax and aperture render at once.
- [x] ~~**Spin it, and print it.**~~ `process/mesh.py`: the heightfield
      as binary PLY with per-vertex colour, and `turntable.webm`, the
      surface lit from an orbiting light. Verified watertight — every
      edge used exactly twice across all 217k triangles — because a
      slicer refuses an open sheet, so the border is skirted down to a
      flat base and capped. The light orbits rather than the camera:
      rotating the object needs a real renderer and a depth buffer,
      moving the light needs a dot product, reads as the same "solid
      thing" cue and cannot tear at silhouettes because there are none.
      Two limits are written into the PLY's own header rather than left
      for someone to discover: a focus stack sees only the surface
      facing the objective, so this is a relief of the subject and not a
      model of it; and z is an exaggeration, because depth is measured
      in slices and nothing in the capture path measures fine-focus
      travel in microns.
- [x] ~~**The plate export.**~~ `process/plate.py`: several finished
      captures arranged on one printable sheet, numbered, labelled with
      the objective, and each carrying a scale bar. Building it found a
      real defect — **no file we have ever written carries the scale**.
      `FocalPlaneXResolution` comes from the SDK's `get_PixelSize`, which
      returns 0 on Nate's E3ISPM20000KPA, and a zero pitch silently drops
      the tag. Worse, that tag is a scale at the *sensor*: a bar drawn
      from it is wrong by the entire magnification chain. Both fixed —
      `CameraProfile.pixel_um` (settable in the setup dialog, datasheet
      beats SDK) and an explicit `um_per_px` in the structured comment,
      pitch over total magnification, which is the number a bar actually
      needs. A file without it gets no bar, because a wrong scale bar is
      worse than none.
- [x] ~~**Depth across a whole mosaic.**~~ A stacked mosaic's tiles each
      leave a `depth.png`, and the stitcher has already solved where
      every tile sits — so the depth composites exactly as the pixels do,
      same positions, same raised-cosine window. What does *not* carry
      over for free is what depth means: each tile's map is normalised to
      its own slice count and starting plane, so tile 3's "far" is not
      tile 5's, and blending raw steps at every seam. The overlaps are the
      constraint — where two tiles see the same slide they must agree —
      so the median difference across each overlap feeds one least
      squares for a per-tile offset, the same shape of solve on the same
      graph that already recovers the positions. Every depth render now
      works on an entire arrangement: a DIC relief of seven stitched
      fields, a wigglegram of the whole mosaic. Test builds one ramp seen
      as two deliberately mis-levelled tiles and requires no cliff at the
      seam.
- [x] ~~**Arranging.**~~ `process/arrange.py` — the one thing here that is
      purely for delight. Darlaston and Möller laid diatoms out one
      frustule at a time with a bristle; this finds the specimens in a
      finished capture, cuts each out, turns it to a canonical
      orientation and lays them in a rosette, spiral or taxonomic rows.
      Two things it does honestly: relative sizes are preserved (making
      them uniform would be prettier and would lie), and it *declines*
      where it cannot cleanly separate — measured, a sparse mosaic gives
      16 frustules and a crowded field gives zero, because overlapping
      elongated valves merge into one component and distance-transform
      watershed cannot split them (its maximum is one 348 px blob).
      Two touches that made it read as an arrangement rather than a
      scatter: alternating sizes from the two ends of the sorted list, so
      one side is not all giants, and re-centring on the ink actually
      drawn rather than the ideal circle. Per-specimen contrast is
      stretched, which is a display decision on a display artifact — the
      *plate* keeps capture contrast, because a plate is evidence.
- [x] ~~**The DNG assumed one sensor.**~~ Found by a camera-support audit
      that enumerated ToupTek's whole SDK model table: **1007 models, one
      identical API, 711 distinct capability words.** Of 244 microscopy
      models, 68 are 8-bit, 139 are 12-bit, 26 are 14/16-bit and 55 (23%)
      are mono — and every capture we wrote hardcoded `WHITE_LEVEL=4095`
      plus a CFA pattern. That mislabels an 8-bit sensor as four stops
      under, clips a 16-bit one to a sixteenth of its range, and puts a
      Bayer pattern on greyscale so every developer demosaics noise into
      colour. `CameraInfo.max_bit_depth` was already populated and simply
      never read. Now: white level and packing come from the sensor's own
      depth, and a mono camera gets BlackIsZero with no CFA tags at all.
      Tested end to end on 8/12/16-bit and mono through the real capture
      path. (Our E3ISPM is 12-bit colour, so this was invisible here and
      would have been someone else's silent corruption.)
- [x] ~~**Rebadge multiplexing.**~~ One backend now drives the whole
      ToupTek family: a translating proxy maps this file's `Toupcam` and
      `TOUPCAM_*` names onto whichever brand's binding is installed, so
      Altair, MallinCam, Meade, OGMA, RisingCam, AmScope, Omegon,
      TS-Optics, Bresser, Orion and SVBony all work. The preload dropped
      to `RTLD_LOCAL` — measured to keep the bare-name identity trick
      (same handle either way) while removing the hazard that
      `libmeadecam.so` exports `Toupcam_*` and would otherwise answer for
      every brand loaded after it. `usb.py` matches all five family VIDs.
      Loader feature-checks `PullImageV4`/`TriggerSync`/`get_Model` and
      names the problem, because two same-SONAME libraries are installed
      on this very machine and the 2021 one has none of them.
- [x] ~~**Capabilities come from the model table.**~~ `CameraInfo` now
      carries brand, raw-capable, cooled, fan and software-trigger, read
      from the vendor's own flags. Measured on real hardware and worth
      recording: our E3ISPM does **not** set `TRIGGER_SOFTWARE` yet has
      been capturing through a software trigger all along — the flag is a
      hint, never a gate. `FLAG_MONO` *is* authoritative, and is what
      decides whether a CFA pattern is written.
- [x] ~~**Basic USB cameras.**~~ `camera/v4l2.py`, run with `--usb`. The
      class microscopists buy first, supported honestly: no raw exists on
      consumer UVC (the ISP is on the bridge chip and the USB side has no
      bypass), so captures are written as *linear* DNGs rather than files
      claiming a CFA they do not have. Everything that does not need
      sensor data still works — tracking, mosaics, stacking, every depth
      render. `--list-cameras` reports the whole picture including
      whether a device offers raw at all.
- [x] ~~**Filenames printed their own placeholders.**~~ Found while
      shooting the webcam: a capture with no configured stand wrote
      `0001_webcam_{objective}_{illumination}.dng`, because unknown
      tokens are deliberately left visible so a *typo* shows — and the
      optics tokens were absent rather than empty. They are always
      defined now; a typo still shows.
- [ ] **Spike `Toupcam_CtiEnable`.** `libtoupcam` is a GenTL *consumer*
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
      question is what the app should do when its belief is *stale rather
      than wrong*: an ignored proposal already marks the objective
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
      being monotonic depth, which muddies depth.png as *geometry* — the
      wigglegram's z is capture order. Options: teach the habit (one
      direction reads best), or order slices by estimated z at merge time
      (cross-slice sharpness correlation could recover the turning point).
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
      better — more even glow, more striae — and they were right. Measured
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
- [x] ~~**First real stacked mosaic found two field bugs.**~~ Nate ran the
      flow twice on real glass (smooth, hands-off — the rhythm works). Both
      runs lost the final field: stitching mid-lay from the menu was the
      one route that sealed nothing. Stitch is now a "done with this
      field" gesture like sliding away, and quitting the app seals too.
      Second: ghosted/duplicated diatoms along seams — the drift bound.
      Dead-reckoning drift accumulated to ~500 raw px across the 25× scan,
      and strong refinements (response 0.37–0.65, residuals all agreeing
      in direction — drift's signature, not a decoy's) were refused
      because they exceeded MAX_RESIDUAL of a narrow overlap strip. The
      per-axis bound now admits drift up to DRIFT_FRACTION of the *tile*,
      hard-capped under the strip's wraparound ambiguity. His mosaic:
      3/7 → 7/7 seams refined, solved corrections march monotonically to
      −500 px in Y, ghosts collapse (before/after crops verified).
      Regression test injects the same drift shape.
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
- [x] ~~**The dream composition: stacked mosaics.**~~ The thesis, shipped.
      With mosaic + stack both on, each field's rack-pauses build a
      per-tile `StackSession` living *inside* the mosaic folder
      (`tile_007_stack/`), created lazily at the first slice and **sealed
      by sliding away** — moving ~a third of a field from the tile's
      anchor is the gesture; no button exists between fields. The merge
      runs in a background queue while the operator racks the next field
      (map shows a hatched veil until it lands; failed = red, retried at
      stitch). One slice = the exposure is simply a plain tile; single
      shots and stacks mix freely, so racking is spent only where the
      subject demands it. Stitcher consumes `stacked.dng`, **normalises
      per-tile white levels** (12-bit singles vs 16-bit stacked blends —
      without it every stacked tile lands four stops bright), and
      finishes any merge it finds undone, so a mosaic closed mid-merge
      still stitches. Chip-off and mosaic-end also seal; undo mid-field
      scraps the racked slices. Proven end to end on the mock: tilted
      focal plane, two fields, seal fired mid-slide, tile 1 merged
      during the pan, composite sharp in every strip (min/max 0.78)
      where no single exposure could be.
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

- [x] ~~**Write the GPL linking exception**~~ — done while sole copyright
      holder, which was the whole point: after the first outside
      contribution it would have needed their agreement too, and a
      contributor who drifts away blocks it permanently. `LICENSE.EXCEPTION`
      grants a GPL §7 permission for linking against a *class* of library
      ("Camera Support Library" — vendor SDKs, tethering, machine vision,
      stage and illumination control) rather than naming `libtoupcam`,
      because we already reach a dozen and the list is meant to grow.
      Covers linking, runtime loading and bindings. Kept to the operative
      terms only — the reasoning behind it belongs in this file, not in a
      licence document. **Nate should read it and own it**: it is their
      copyright and my draft.

      *Why it is needed:* a microscope camera is reachable only through
      its manufacturer's proprietary SDK, so without the permission it is
      doubtful anyone could distribute a working build at all. That would
      protect nobody's freedom and merely make the program
      undistributable. Worse than ordinary proprietary in our case: the
      ToupTek SDK ships with no licence, EULA or copyright notice of any
      kind, and downstream packagers have each invented a different
      answer about what it permits.
- [x] ~~Verify `focus-stack`'s licence before vendoring.~~ Moot: nothing
      was vendored. The merge in `process/stack.py` is ours end to end —
      constrained phase-correlation alignment, Weber-normalised Tenengrad,
      joint-bilateral weighted-median depth refinement, sub-slice
      interpolation and push-pull diffusion, every stage of it placed by
      measurement on `tools/stack_bench.py`. Two comments cite
      PetteriAimonen/focus-stack by name because its approach was read and
      then *tested against ours* — the Guo whole-curve fit lost on 4 of 5
      bench scenes and was not adopted. Reading a project and citing what
      you learned is not vendoring, and no third-party stacking code is in
      the tree.

## Packaging

- [x] ~~**Installable at all.**~~ `pyproject.toml` with a `darlaston`
      entry point, verified by installing the built wheel into a clean
      virtualenv and running it. CI runs the suite on the version floor
      and the current release, and asserts the wheel carries no spike,
      test or tool files.
- [ ] **A build people can double-click.** `pip install` is a developer
      distribution and the users are microscopists. **AppImage** rather
      than Flatpak: the two hard requirements here are raw USB device
      access and loading a vendor SDK from the user's home directory, and
      a sandbox makes both awkward at once. Briefcase can produce it, and
      a tag-triggered release job is a small addition to the existing
      build job.
- [x] ~~**First-run SDK fetch.**~~ `camera/sdk_install.py` plus a dialog,
      reachable from the failure screen and Instrument > Install camera
      SDK. Fetches from the vendor's own URL (never mirrored: the section
      7 permission covers linking, not redistribution, and that SDK has
      no licence to redistribute under anyway), never silently, and
      verifies structurally rather than by pinned checksum because
      vendors replace these archives in place. Only ToupTek has a
      verified direct link; the rest get their download page and a button
      that opens it. Tested against the real vendor.
- [ ] **macOS: wired, unverified.** The platform-specific pieces are done
      and tested by simulation -- the loader finds `mac/libtoupcam.dylib`
      (a universal binary, so Intel and Apple silicon share one file), the
      SDK installer verifies that build, `--list-cameras` stops claiming
      "none on the bus" where there is no sysfs to survey, and the presence
      check answers *try* rather than *no*, which had left the window
      waiting for ever with a camera plugged in. Nobody has run it on a
      Mac. Next: pull it down there, `--list-cameras`, then a capture.
      Known remaining: `--usb` is Linux only (V4L2 ioctls), and config
      lives in `~/.config/darlaston` rather than `~/Library`, which works
      and is consistent but is not the platform convention.
- [ ] **Windows testing.** Library naming is handled (`toupcam.dll`, no
      `lib` prefix) and nothing else has been looked at. Support that
      cannot be tested is a claim, not a feature.

## Later

- [x] ~~**It writes a photograph now, not only a negative.**~~ Every output
      was a raw file: you could shoot a fifteen-tile stacked arrangement and
      be unable to send it to anybody. Captures, merged stacks and stitched
      composites now leave a JPEG beside the raw, developed in
      `process/develop.py` from the levels and white balance the raw itself
      declares so the two agree. Default is both; no ordinary camera ships
      set to raw only. JPEG-only *skips* the raw rather than deleting it,
      and never applies to a tile or a slice, which the stitcher and merge
      read back.
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
      workflow must be complete without it. DISCOVERY.md §9b.
- [ ] Setup editor, so the provisional scope in `ui/main.py` can go away.
- [ ] Session resumability, needed once mosaics reach 40 tiles. The
      *budgeting* half is done: free space is on the status bar permanently,
      brass under 20 GB and red under 2, so a session no longer dies of a
      full disk without warning. Resuming an interrupted one is still open.
