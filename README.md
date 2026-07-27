# photomicrography

A capture, calibration and mosaic tool for photomicrography on Linux.

Built for large diatom arrangements shot on an ordinary microscope with a
ToupTek camera, brightfield, darkfield and phase, where the goal is a stitched,
focus-stacked photograph rather than a measurement.

**Status: discovery. No implementation yet.** See [DISCOVERY.md](DISCOVERY.md).

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
