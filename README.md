# darlaston

**Named for Herbert William Hutton Darlaston (1867–1949)**, a Birmingham mounter
who turned professional around 1905 because too many people wanted his slides.

A capture, calibration and mosaic tool for photomicrography on Linux.

Built for large diatom arrangements shot on an ordinary microscope with a
ToupTek camera, brightfield, darkfield and phase, where the goal is a stitched,
focus-stacked photograph rather than a measurement.

**Status: working, and used on real glass.** Capture, calibration, mosaic
stitching, focus stacking and the composition of the two all run end to end
on a ToupTek E3ISPM20000KPA and a 1960s Zeiss Universal.

## What it does

- **Captures** 12-bit raw to DNG through its own TIFF writer — no raw library
  — with 76 EXIF tags describing the optics it was taken through.
- **Tracks the stage from the image itself.** No encoders: consecutive preview
  frames are phase-correlated and the offsets integrate into a position, which
  builds a slide map you can pin and navigate by.
- **Stitches mosaics** with *position-constrained* registration. It never
  searches, because correlation confidence cannot detect a wrong match; it only
  refines within a window around where the stage said it went.
- **Stacks focus** without touching the computer. Rack the fine focus, pause,
  rack again: the trigger watches the live sharpness field and fires the
  shutter itself.
- **Composes the two.** With both modes on, each field's rack-pauses build that
  tile's own stack, and *sliding to the next field seals it* — the merge runs in
  the background while you are already racking the next one. Single shots and
  stacks mix freely in one mosaic.
- **Renders the depth map**, which every stack keeps: a wigglegram, a focus
  pull, a lit turntable, a stereo pair, a red/cyan anaglyph, an autostereogram,
  a printable watertight mesh, and a relief image we call **DIC — Darlaston
  Inferred Contrast**, which looks like differential interference contrast and
  is not. (It shades the gradient of measured focus depth. Real DIC responds to
  refractive index through the whole specimen. The resemblance is real; the
  physics is not the same, and the name says so on purpose.)

| | |
|---|---|
| [DISCOVERY.md](DISCOVERY.md) | what the hardware and the ecosystem actually do, measured |
| [WORKFLOW.md](WORKFLOW.md) | one capture session as a narrative — requirements derive from it |
| [DESIGN.md](DESIGN.md) | entity model, calibration lifetimes, the exposure handoff |
| [ARCHITECTURE.md](ARCHITECTURE.md) | threading contract and component boundaries |
| [SUPPORT.md](SUPPORT.md) | which cameras work, which are planned, and which are refused |
| [TODO.md](TODO.md) | everything asked for, deferred, or left unverified |

## Why

The only Linux software that drives these cameras is ToupTek's ToupLite, and it
quietly discards most of what the hardware can do. Measured, not assumed:

| Stage      | ToupLite gives you             | The camera can do |
| ---------- | ------------------------------ | ----------------- |
| Resolution | 2736 × 1824 (2×2 binned)       | **5440 × 3648**   |
| Bit depth  | 8-bit                          | **12-bit raw**    |
| Tone       | curve + colour matrix baked in | linear            |
| Chroma     | 4:2:0 subsampled               | full resolution   |
| Encoding   | JPEG                           | lossless          |

On a real darkfield capture, **90.6 % of all pixels occupy just four luma
levels** — at 12 bits that same tonal region gets 64. The faint outer glow around
the diatoms is not dim in those files. It is gone.

Meanwhile a 30% radial illumination gradient across every frame is what makes
tiles refuse to blend, and it disappears entirely under flat-field correction.

## Design principles

- **Works on a completely unmodified microscope.** No motors, no gears, no
  soldering. Manual stage, hand-cranked focus. Motorization is an optional
  accelerator, never a prerequisite.
- **The camera is behind an interface.** ToupTek first; tethered mirrorless later.
- **Calibration is per-profile**, keyed on objective × relay optic × illumination
  mode × binning. Switch illumination and the right calibration follows.
- **The glow is the point.** These are art photographs. Optical artefacts that
  look good are preserved; only the ones that break registration get corrected.

## Licence

GPLv3 — see [LICENSE](LICENSE).

**Linking exception.** As a special exception, the copyright holders give
permission to link this program with the proprietary ToupTek SDK libraries
(`libtoupcam`, `libimagepro`, and companions), and to distribute the resulting
executable, without those libraries falling under the terms of the GPL. This
exception does not invalidate any other reasons the executable might be covered
by the GPL.

### On the ToupTek SDK

The SDK ships with **no licence, EULA or copyright notice of any kind** — audited
across the full 243 MB archive. That is ambiguous rather than permissive, so this
project takes a deliberately conservative posture:

- the SDK is **never vendored** into this repository
- libraries are **`dlopen`ed at runtime**, never linked at build time
- users install the SDK themselves from
  [ToupTek's download centre](https://www.touptekphotonics.com/download/?category=SDK)

_This is an engineering posture, not legal advice._

## tools/

Standalone diagnostics written during discovery. Each runs on its own.

|                        |                                                                                                                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `probe_camera.py`      | Interrogates a connected camera — bit depth, raw format, resolutions, and which SDK options the firmware actually honours. Read-only: options are set, read back, and restored. |
| `analyze_shots.py`     | Measures what a capture pipeline costs — clipping, posterisation, chroma damage, illumination falloff.                                                                          |
| `test_registration.py` | Phase-correlates a sequence of captures to test whether a subject can be registered at all, with proper noise-floor controls.                                                   |

`probe_camera.py` finds `libtoupcam.so` automatically, or takes a path as its
first argument, or reads `$TOUPCAM_LIB`.

## Acknowledgements

[pyuscope](https://github.com/JohnDMcMaster/pyuscope) and
[gst-plugin-toupcam](https://github.com/JohnDMcMaster/gst-plugin-toupcam) by John
McMaster solve the adjacent problem — motorized scanning of feature-rich flat
subjects — and share the camera layer.
