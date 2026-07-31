# Camera support

What darlaston drives today, what it will drive, and what it deliberately
will not. Every claim about a camera family here was measured — against
the hardware on the bench, against the vendor SDK's own model table, or
against the shipping binaries — and anything inferred rather than
measured says so.

Run `python -m darlaston.ui.main --list-cameras` to see what *your*
machine offers.

---

## Supported now

### ToupTek and its rebadges — full support

The reference camera. Raw Bayer at the sensor's own bit depth, hardware
trigger, full control of exposure and gain, and everything downstream:
measured white balance, per-Bayer-phase flat fields, packed-12 DNGs.

One SDK covers about a thousand models whose capabilities differ wildly —
the vendor's own table has **711 distinct capability words** — so
darlaston reads each camera's flags rather than assuming. Of the 244
microscopy models: 68 are 8-bit, 139 are 12-bit, 26 are 14- or 16-bit,
and 55 (23%) are monochrome. The white level, the packing and the CFA
tags all follow what the camera reports.

**The rebadges are the same cameras.** ToupTek manufactures for a dozen
brands and ships each the same SDK with renamed symbols — verified binary
to binary: 272 exported symbols matching byte for byte, headers differing
by two cosmetic lines, identical model structs for the same USB ID. One
backend drives all of them.

| Brand | Works with | Notes |
|---|---|---|
| ToupTek | `libtoupcam.so` | the reference |
| RisingCam | `libtoupcam.so` | PID table is 100% identical |
| AmScope | `libtoupcam.so` or `libamcam.so` | MU series |
| Omegon | `libtoupcam.so` or `libomegonprocam.so` | |
| TS-Optics | `libtoupcam.so` or `libtscam.so` | |
| Bresser | `libtoupcam.so` or `libbressercam.so` | MikroCam |
| Orion | `libtoupcam.so` or `libstarshootg.so` | StarShoot G |
| SVBony SC715C | `libtoupcam.so` or `libsvbonycam.so` | that model only |
| Altair | **`libaltaircam.so`** | 137 of 199 IDs are exclusive |
| MallinCam | **`libmallincam.so`** | SkyRaider line is exclusive |
| Meade | **`libmeadecam.so`** | LPI-G line is exclusive |
| OGMA | **`libogmacam.so`** | AP/GP line is exclusive |

The first eight are already inside ToupTek's own device table, so they
work with the stock SDK. The last four need their own vendor library —
unpack it where you would put ToupTek's and darlaston finds it.

**Install:** unpack your vendor's Linux SDK under `~/toup/sdk-*/`, or
point `TOUPCAM_SDK` at its root. The SDK is never bundled; see
[DISCOVERY.md](DISCOVERY.md) §4c for why.

Two traps worth knowing, both handled:

- `libmeadecam.so` exports `Toupcam_*`, not `Meadecam_*`. Libraries are
  therefore loaded `RTLD_LOCAL`, or the first one loaded would answer for
  every brand afterwards, silently.
- ToupLite bundles ship a `libtoupcam.so` from 2021 with the *same*
  SONAME as a current SDK, missing several functions darlaston needs.
  The loader feature-checks and says so by name rather than failing
  somewhere deep in a capture.

### Ordinary USB cameras (V4L2 / UVC) — supported, without raw

Run with `--usb`. This is the 50-to-200-pound class that clamps to an
eyepiece or screws into a C-mount and appears as `/dev/video0`.

**These cameras do not give you raw, and that is the class, not a
limitation we could code around.** UVC standardises exactly two
uncompressed payloads, YUY2 and NV12. Raw Bayer over UVC exists only as a
vendor-specific GUID, and a survey of a corpus of several hundred
thousand real USB descriptor dumps found *not one* consumer microscope
emitting it — every one was YUY2 and/or MJPEG. The ISP sits on the bridge
chip, debayers, white-balances and gamma-corrects, and the USB side has
no bypass. One vendor's support desk states it plainly: "there is no
firmware or hardware path to bypass this limitation."

So darlaston does not pretend. With a UVC camera you get:

| Works | Does not |
|---|---|
| Live preview, focus peaking | Raw capture — files are **linear DNGs**, 8-bit, demosaiced |
| Stage tracking and the slide map | Measured white balance (the camera already applied one) |
| Mosaic capture and stitching | Flat fields with per-Bayer-phase normalisation |
| Focus stacking, rack-then-pause trigger | Scale bars (UVC reports no pixel pitch) |
| Every depth render — wigglegram, DIC, mesh | Bit depths above 8 |

A capture from one of these is written as a linear DNG, which says
"demosaiced" honestly, rather than a file claiming a CFA pattern it does
not have.

**The Imaging Source is the exception worth naming.** Their industrial
DFK/DMK cameras ship raw Bayer over plain UVC, because a TIS engineer
upstreamed the necessary format GUIDs into the Linux kernel in 2014 and
2016. If `--list-cameras` reports a raw format, that is what you have. We
recognise and report it; consuming it is not built yet.

### The synthetic camera

`--mock`. A full simulated stage, focal plane, turret and slide, used by
most of the test suite. Develops the whole application without hardware.

---

### Operating systems

Linux is the tested platform: everything in this document was measured
there, on the bench camera.

**macOS is wired but unverified.** The vendors ship one archive holding
every platform, so the pieces that differ are small and are now handled:
the loader looks for `mac/libtoupcam.dylib` rather than the Linux build
(ToupTek ships it as a universal binary, so Intel and Apple silicon are
both covered by the same file), the SDK installer verifies that same
build, and the USB presence check no longer answers "no camera" simply
because there is no sysfs to read — which would have left the window
waiting for ever with a camera plugged in. Those paths are covered by
tests that simulate the platform. None of it has been run on a Mac, so
treat it as ready to try rather than as support.

An ordinary webcam is **Linux only**: `--usb` needs V4L2, and while
OpenCV can open a camera through macOS's own framework, everything that
finds one and describes it is a V4L2 ioctl. Asked for on another
platform it now says so rather than connecting to nothing in silence.

**Windows is untested and unclaimed.** The library naming is handled
(`toupcam.dll`, without the `lib` prefix) but nobody has run it, and
support that cannot be tested is a claim rather than a feature.

## Intended

Ordered by how many people it reaches per unit of work.

### Tethered mirrorless and DSLR, via libgphoto2

The path is clear and the licence is clean: `libgphoto2` is LGPL, needs
no registration, and its C API takes the same runtime-load treatment the
ToupTek SDK already gets. One backend reaches roughly **260 bodies with
live preview** across Canon, Nikon, Sony, Fuji and OM System — including
the Sony A6700 and the OM-1.

Preview is 10–25 fps of 640×480–1024×680 JPEG. That is enough: stage
tracking phase-correlates a 512² downsample, and the sharpness field and
stack trigger run off the same frames.

The honest cost is **latency per still**: a PTP capture-and-download of a
25–35 MB raw takes seconds, against a few hundred milliseconds for a
dedicated camera. A 25-tile × 20-slice stacked mosaic is 500 captures. It
would work; it would not feel like the instrument does now.

Raw would go through `dnglab convert -c uncompressed` as a subprocess
(LGPL, single static binary) so our own writer stays the only thing that
produces darlaston DNGs.

A body **without** live preview would be stills-only — no stage tracking,
no slide map, no overlap steering, no auto-trigger. That is not worth
shipping as a supported configuration and should be refused at connect
time with a clear message.

### Capability-aware UI

The flags are read; the interface does not use them yet. Cooled cameras
should expose their TEC and fan, mono cameras should hide white balance
entirely, and a camera with hardware ROI or binning should offer them.

### GenTL bridge — one line, entirely unproven

`libtoupcam` is itself a GenTL *consumer*: `Toupcam_CtiEnable` is
exported, and the library's strings carry `TLOpen`, `IFOpenDevice` and
`GENICAM_GENTL64_PATH`. The call succeeds and is non-destructive. If a
third-party GenTL producer makes a Basler, IDS or Daheng camera enumerate
through the same path, that is industrial-camera support for almost
nothing. Nobody has confirmed it works. Needs a borrowed camera and an
hour.

### Industrial cameras, via Aravis

If GigE or USB3 Vision users appear. Aravis is LGPL-2.1+, packaged in
distributions, and reimplements both wire protocols itself — no vendor
blob, no EULA. Complete Bayer coverage including packed 12-bit. Real work
(~400 lines) and it would add a PyGObject runtime dependency, which is
the thing to weigh.

---

## Not planned, and why

**A V4L2 backend for ToupTek-family cameras.** They are vendor-class USB
devices: interface class `0xff`, no driver bound, no `/dev/video` node at
all. `uvcvideo` cannot bind them and neither can libcamera. A V4L2 path
reaches zero of them.

**libcamera.** Its UVC pipeline handler returns identical configurations
for raw and viewfinder roles, and hard-rejects the sensor-configuration
API that would let you demand bit depth or binning. On desktop USB it is
a driver-name match plus a format passthrough — strictly less than
reading V4L2 ourselves.

**Vendor SDKs with C++ ABIs** (Sony's Camera Remote SDK, IC Imaging
Control 4). They cannot be loaded at runtime from Python; they need a
compiled shim, which would break the property that keeps darlaston's
licensing simple and its dependencies at three.

**Micro-Manager / pymmcore on Linux.** Nightly builds are Windows and Mac
only, and pymmcore ships no device adapters.

**tiscamera.** In maintenance mode with a stated end of life of
2029-04-01. Not a foundation to start on. Its cameras remain supported
through plain UVC.

---

## Licensing

darlaston is GPLv3. Vendor SDKs are **never** bundled: the user installs
their own and we load it at runtime. That is the right posture and, for
the ToupTek family, the only defensible one — the SDK ships with no
licence, EULA or copyright notice of any kind, and downstream packagers
have each invented a different answer about what it permits.
`Aravis` (LGPL-2.1+) and `libgphoto2` (LGPL-2.1) are clean by
construction and would need no exception.

Writing the GPL linking exception is on [TODO.md](TODO.md), and should
name a *class* of libraries rather than one file, because the supported
list keeps growing.
