# TODO

This document tracks upcoming features, concerns, and ideas.

## Capture features

- [ ] **Binned capture as a size option.** `grab_raw` hard-codes full resolution. The sensor's own binned modes would give 7.5 MB at 2736×1824 and 3.3 MB at 1824×1216.
- [ ] **Beyond 4 GB.** A big enough mosaic cannot be a DNG at all, TIFF offsets are 32-bit. BigTIFF or a pyramidal TIFF is the answer for viewing; the linear DNG stays the right output while it fits.
- [ ] **The composite still holds one full canvas.** With the writer fixed peak is 3.05 GB for 272 MP and the remainder is the uint16 result array plus the registration lumas. Rendering bands on demand into the writer, rather than filling a canvas and then streaming it, would drop it again — and the writer's `rows` callback is already the right shape for it.
- [ ] **Exposure handoff.** Carry the live view's brightness into the capture at unity gain. Needs calibration to be verifiable.
- [ ] **Video recording.**
- [ ] **Real-time EDF (live focus stacking).** ToupView ships live EDF
      with three selectable algorithms — Maximum Contrast, Weighted
      Average, and "Stacking" (FFDSSD) — plus auto-alignment for shift,
      rotation and scale (their FAQ 41 and 59). And they withhold it
      from ToupLite, so their own Linux and Mac users have never had
      it. We cover the offline half well (rack-pause capture, measured
      merge, panels deciding); the live half is a different shape:
      accumulate a sharpest-so-far composite into the preview while the
      operator racks, no capture step, alignment tolerant of hand
      wobble. Their quality is contested in the forums, which is the
      opening: be visibly better, not merely present. Any algorithm
      goes through tools/stack_bench.py before shipping, same as every
      other merge claim. Detail in spike/docs/competitors-toupview.md.

## Camera Support

- [ ] **Exposure control, by what the camera reports (planned, not built).** One button, "Automatic", stays. The engine behind it follows the capability flags the probe now reports: exposure reports manual capability, our own loop as today (stacks and mosaics need matching frames); auto-only, hide our slider and loop and let "Automatic" switch the camera's own auto-exposure (UVC AE mode, DirectShow's Auto flag, V4L2 `exposure_auto`); and for ISP cameras that do both, "let the camera meter" as a line in the camera profile editor, off by default, not a new control on the main screen. Held until the X5FCAM4K8MPA's listing says which case it is: the tester reports gain working and exposure not, and no "Automatic" button at all, which does not add up through the plain device and may all be the SDK wrapper they chose.
- [ ] **The X5FCAM's motor on the plain device.** The tester found it behind the proc-amp Backlight Compensation *on the SDK wrapper's property page*, which is the wrapper's own translation of its `AFPOSITION`. On the kernel device Backlight is apparently a real backlight compensation, so the motor is behind some other standard property (the DLL has no extension unit); the full-property listing shows which range looks like 0..854. Until then the Backlight-as-focus rule applies only to kernel devices and may be pointing at the wrong knob.
- [ ] **4K over USB goes pink; 2688x1512 does not; lower gain stops it.** Reported on the X5FCAM. Not a format problem (that is constant garbage, not intermittent): bandwidth. Higher gain means noisier frames means larger MJPEG frames, and 3840x2160 at 30 fps sits at the link's ceiling, so dropped packets corrupt frames into pink and green blocks. The preview should sit at 2688x1512 (`preferred_preview` already prefers under 6 MP; check it is what opens) and 4K be for captures; a frame-rate cap at 4K on DirectShow (none is set today, unlike the ToupTek SDK path) and a USB 2.0 warning like the Linux one would both help. Also: "1080p is missing", on the wrapper, which advertises its own list; check the plain device's.
- [ ] **Sony Camera Remote SDK.**
- [ ] **Ximea SDK.**
- [ ] **Support for older Toup cameras.** Does support fail on the ToupView side or the SDK side? Can we load older SDKs?
- [ ] **libgphoto** backend for mirrorless cameras.
- [ ] **Windows UVC: unverified on hardware.** `camera/dshow.py` was written on Linux and is exercised in CI against the OBS virtual camera (enumeration, formats, index mapping, streaming and capture all proven there, on OpenCV 4 and 5), which has no camera controls. Unity Capture was tried first: our enumeration described it and OpenCV's DirectShow open refused it on both majors with no reason, and OpenCV's debug output is compiled out of release wheels. Still to be seen on a real device: that the second filter instance's `IAMCameraControl::Set` reaches the camera while OpenCV streams from it, the exposure units DirectShow reports for a ToupTek hybrid, and that the X5CAM's focus motor answers the standard focus property (its vendor DLL uses nothing else, so it should). The first Windows user's `--list-cameras` output is the ground truth; it prints every range for that reason.
- [ ] **Windows exposure steps are powers of two.** DirectShow's exposure is log2 seconds, so the slider on a Windows UVC camera lands on 1/8, 1/4, 1/2 s rather than in between. Media Foundation's extended camera controls carry exposure in 100 ns units; reaching them is a KS property set away and not yet earned.
- [ ] **macOS UVC.** `cv2` opens a webcam through AVFoundation but nothing describes one, and AVFoundation exposes no focus control for an external camera. ToupTek's `toupnam` SDK (network, ships mac/linux/win with a Python binding and the full autofocus table) is the honest macOS route to a focus motor, over ethernet.
- [ ] **ToupTek `uvcham` as a fallback.** Their hybrid-camera DLL, measured to be DirectShow plus ffmpeg with standard camera controls underneath, so it adds nothing our backend lacks; the archive and a fake-binding test plan exist in case a real device proves otherwise.
- [ ] **The suite on Windows.** Only the camera tests run on the Windows runner: `v4l2.py` imports `fcntl` at module level and a handful of tests import it directly.
- [ ] **PySide6 6.12.0 crashes the suite two ways; pinned at 6.11.1 until it does not.** Reproduced 2026-10-11 in fresh venvs with the runner's exact set. On 3.11 the fourth `MainWindow` built in one process aborts with Python's own refcount fatal ("deallocating None") from inside `QApplicationPrivate::notify_helper`, i.e. a binding returning `None` without owning it during event delivery; alone, the same test passes, so it is cumulative across windows. On 3.13 all 888 pass and the interpreter segfaults at exit with "shared QObject was deleted directly" from `PySide::destroyQCoreApplication`, which other projects are reporting this week against parentless widgets kept alive past the QApplication. Both vanish on 6.11.1 (3.11 full suite green). When Dependabot proposes 6.12.x, the first thing to try is closing and deleting every leftover top-level widget in the `window` fixture's teardown while the application still exists; the refcount one may simply be an upstream bug to wait out.

## Slide mapping

- [ ] **Map scale changes with the objective.** Clearing on setup-dialog accept is wired; the objective _stepper_ and future turret auto-detection are not. When magnification becomes known per objective, positions could be rescaled instead of discarded.
- [ ] **The slide map as a finding aid.** The other half of the plate idea, still open: export the accumulated map, pins, thumbnails, µm coordinates, as a printable sheet. For a catalogued mount that is an archival artifact, and Victorian mounters drew them by hand.

## Presentation

- [ ] **Export the slide map as an image.** Asked for by the first Windows tester; the other half of the plate idea in Slide mapping above, and it wants the same thought about scale, pins and thumbnails before it is a button.
- [ ] **UI scale.** Small, normal, large: the 10px monospace is tiny on a 4K laptop, and the tester's "higher contrast" ask was probably half a "bigger" ask. Held, because the rail is already full at its one size: most heights are pinned in pixels, and a larger type has to reflow the rail rather than overflow it. Wants the rail's rows to size from the font before it is a setting.
- [ ] **Capture moment.** When the shutter fires, hold the developed capture on the presentation for a couple of seconds with a border pulse. "We just took that" is half the fun of tabling; deferred to keep the first release of the feature calm.
- [ ] **QR code beside the header.** Visitors ask "where do I find you". Needs either a dependency or a hand-rolled encoder, and neither is earned yet. The /still.jpg endpoint is a natural target once it exists.
- [ ] **Auto-hold on a blank field.** The slide leaving the stage could hold the last good frame automatically, piggybacking on the blank detection the stack trigger uses. Needs care in darkfield, where blank is dark rather than bright, and must never fire during ordinary panning.
- [ ] **Field width readout.** "This view spans 1.3 mm" is the third question visitors ask, is exact from µm/pixel times frame width with no assumptions, and would slot into the magnification line. Low priority while the scale bar answers it indirectly.

## Optics and measurement

- [ ] **Measurement tools.** The most-used ToupView feature cluster
      after capture itself — the forums are full of dedicated calibrate,
      measure and reset-calibration help threads. Length, angle, radius,
      area and polygon overlays, calibrated per objective. We already
      hold the honest half: µm/px from sensor pitch over magnification,
      written into every file and drawn as the scale bar — but there is
      no way to _click_ a measurement onto a picture. Table stakes for
      anyone moving off ToupView. The gallery/develop view is its
      natural home when that exists, the live view second. Worth saying
      in comparison copy: their per-objective calibration dies on every
      reinstall (its own recurring help-thread genre); ours survives by
      construction. Detail in spike/docs/competitors-toupview.md.
- [ ] **Optical profiler.** Stage micrometer plus grid target → µm/pixel, distortion, field curvature, lateral CA, MTF50 centre vs corner, and a computed **usable field fraction** that derives the crop radius instead of leaving it to judgement. Lets users share nice optics setup stats, too.
- [ ] **Stop the preview during a long timelapse.** A 30 fps preview between shots that are minutes apart is an enormous amount of readout for nothing, and readout is what heats a sensor. Wake the stream only shortly before each frame, and show the last capture in the meantime rather than a live view. Cheap to do: a mode change measured about a second, which is nothing against a minute-long interval. Two things to check first the stage tracker feeds on preview frames, which is fine for a timelapse where nothing moves but means the hold-still guard is unavailable; and stopping a UVC stream may drop the manual exposure and white balance we set on open, so they would need re-applying each time.
- [ ] **A measured color matrix.** The default is now XYZ→sRGB, but a matrix measured from a color target would be better than assuming sRGB primaries. Though nobody's got a microscopic color target, so this is low priority.

## Unverified

- [ ] **Can the optics out-resolve the binned mode at all?** If the relay is the limit, full resolution records empty magnification. The profiler answers this.
- [ ] **Do the SDK's FFC/DFC apply in the raw path or only the ISP path?**

## Packaging

- [ ] **Windows testing.** Library naming is handled (`toupcam.dll`, `lib` prefix) and nothing else has been looked at. Does the window frame work? Does capture work at all?
- [ ] **Microsoft store publishing.** This is a free route to a signed package for Windows users. A github action supports it.
- [ ] **Apple signing cert.** I need to cough up $100/yr for an Apple signing certificate to avoid the warnings for Mac OS users.
- [ ] **Mac OS Brew cask.**
- [ ] **Microsoft signing cert.** This one's $120/yr through Azure signing... maybe worth it if Windows users hate the Microsoft Store.
- [ ] **Confirm the system thumbnailer is happy.** The preview is in the right place and extracts correctly.
- [ ] **Snap layouts below half-screen.** The window's minimum width is 766 px and Microsoft's limit for snap layouts is 500, so the half-screen layouts work and the third- and quarter-width ones invoke and then fail to snap. Traced to three constraints in a chain - the rail fixed at 286, the live view's 480 minimum and the waiting page's 458 - so relaxing the live view alone bottoms out at 744 and changes nothing. The one that bites is a third of 1920.

## Later

- [ ] **Gallery and basic develop view.** Browsing what you shot, and a few sliders for developing RAW to jpeg
- [ ] **Setup card.** "what's your setup?", neofetch style
- [ ] **Session resumability.** To rescue interrupted mosaics
- [ ] **Flyby: the orchestrated version.** Design notes in `spike/FLYBY.md`, A stacked mosaic is a four-dimensional recording - x, y, zoom and focal plane - and every move through it can be perfectly smooth because it is synthesised rather than performed, which is virtual camera work on a slide and is offered because we keep both the mosaic and the depth. The thing that makes it a big feature rather than a small one is circular: a flyby can only be planned over a mosaic that already exists, and the mosaic that should exist depends on the flyby. Plan over the live slide map first - sketch the path, get the minimum coverage and an honest size estimate, then shoot it - which turns a rendering feature into a capture feature. Order and reasoning in the doc.

## Objective Swap Detection

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

## Focus Stacking

- [ ] **Motorised sweep: settle by picture, calibrated by nobody yet.** `FocusSweep` fires when the sharpness field holds still for `QUIET_FRAMES` after `MIN_FRAMES`, or after 3 s regardless. Those numbers were chosen, not measured; the first camera with a motor should set them the way the rack-pause trigger's were. Also open: a sweep in a stacked mosaic hands the knob back and waits for the slide, which is right, but there is no "sweep every tile" yet.
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
      0 and 51.9 to 0, because those pixels stop voting.

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

## Developer quality-of-life

- [ ] **CLI tools & docs**
- [ ] **Refactoring**, there are a lot of big files that need breaking down
- [ ] **Internal docs**, mainly architecture and docgen
- [ ] **General cleanup sweep** for comments and unused code
- [ ] **Test suite cleanup**, since it takes ages to run
