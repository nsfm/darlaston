# Architecture

Derived from [DESIGN.md](DESIGN.md). This is the last planning document; after
it there is code.

Two things here are load-bearing and expensive to retrofit: the **threading
contract** (§3) and the **live pipeline boundary** (§4). Everything else can be
rearranged later without much pain.

---

## 1. Package layout

```
photomicrography/
  camera/
    base.py            CameraBackend — the abstraction the rest of the app sees
    toupcam.py         ToupTek backend over the vendor bindings
    session.py         CameraSession — owns the handle, reconnection, profiles
    buffers.py         pre-allocated frame pool

  live/                ← narrow interface. see §4.
    pipeline.py        LivePipeline: frames in, LiveSignals out
    tracker.py         phase correlation → XY offset + confidence
    focus.py           metrics + prefilters, switched by illumination
    peaking.py         per-pixel sharpness map
    coverage.py        union of in-focus masks across a Z sweep

  calib/
    store.py           keyed lookup, by product lifetime
    dark.py
    flat.py            opportunistic banking, median, per-phase normalisation
    whitebalance.py
    defects.py         hot-pixel map from a master dark

  capture/
    raw.py             full-resolution pulls, exposure handoff
    stack.py           Z-stack orchestration: movement detection, settle, trigger
    mosaic.py          tile orchestration, overview anchoring

  process/
    align.py           focus-breathing correction
    edf.py             depth-map focus merge
    stitch.py          adapter: m2stitch / ASHLAR / imagepro
    dng.py             Bayer and linear DNG writers

  session/
    model.py           entity model and serialisation
    manager.py         resumability, disk budgeting

  ui/                  PySide6. main thread only, no exceptions.

  rawio.py             canonical orientation, manifests, calibration maths
```

---

## 2. Threads

Five, with deliberately different personalities.

| Thread | Owner | Job | Backpressure |
|---|---|---|---|
| **SDK callback** | libtoupcam | pull into a pooled buffer, hand off, return | none — must never block |
| **Live analysis** | us | tracker, focus metric, coverage | **drop oldest** |
| **Peaking** | us | per-pixel sharpness map | **drop oldest**, independent rate |
| **Capture** | us | full-resolution pulls, exposure handoff | **never drops**, blocks its own caller |
| **Processing pool** | us | align, stack, stitch, DNG | queued to disk, **resumable** |
| **Qt main** | Qt | UI only | queued signals in |

---

## 3. The threading contract

### 3.1 Three paths, three opposite policies

Mixing these up is the actual bug, and it is the one everybody ships.

```
live path         ALWAYS drops      a lost preview frame is invisible
capture path      NEVER drops       a lost slice is unrecoverable
processing        NEVER drops       queued to disk, survives a crash
```

### 3.2 The live path is a cell, not a queue

**This is the single most important decision in the document.**

A queue means that when analysis falls behind, lag *accumulates*: the buffer
drains while you look at a frame from 400 ms ago. The stage has moved and the
screen has not caught up. That is precisely what makes microscope software feel
awful to drive.

So the handoff between acquisition and analysis is a **latest-value cell of size
one with replace-on-write**. A new frame arriving while analysis is busy
*replaces* the pending frame. Frames are dropped under load; you are always
looking at now.

```python
class LatestFrame:
    """Single-slot handoff. Producers never block; consumers get the newest."""
    def put(self, frame): ...      # replaces any pending frame, returns immediately
    def take(self, timeout): ...   # blocks for the next frame, clears the slot
```

### 3.3 Buffers are pooled, never allocated per frame

A full-resolution frame is 39.7 MB. Allocating per frame makes the allocator the
frame-rate ceiling. A ring of pre-allocated buffers is handed out as numpy views
and returned when the last reference drops.

Corollary: **nothing may hold a frame view beyond its stage.** Anything needing
to keep pixels takes a copy explicitly and says so.

### 3.4 The SDK callback does almost nothing

Pull into a pooled buffer, `put` it, return. Every millisecond spent there is a
millisecond of the vendor's thread held — and the GIL with it.

No analysis, no logging, no Qt, no allocation in the callback.

### 3.5 Analyses run at independent rates

Measured on this machine: tracker ~6 ms, focus metric ~2.7 ms, peaking ~20 ms.

Binding them to one loop lets the expensive one set the pace for everything.
Peaking at half rate is indistinguishable to the eye; a tracker at half rate is
visibly laggy. So they are separate consumers of the same cell, each dropping
independently. Load degrades *gracefully* rather than uniformly.

### 3.6 No Qt object is touched off the main thread

No exceptions, no clever workarounds. Queued signals only. The live pipeline
emits plain data; the UI decides what to draw.

### 3.7 The GIL is not a correctness mechanism

Real locks everywhere they are needed, even where the GIL would incidentally
save us. That is what makes a free-threaded Python build a free win later rather
than a debugging season.

---

## 4. The live pipeline boundary

The genuinely hard discipline, and the one that costs something now.

**`live/` is one component with a narrow interface.** Frames in, signals out.

```python
@dataclass(frozen=True)
class LiveSignals:
    seq: int
    xy_offset: tuple[float, float] | None
    xy_confidence: float
    focus_metric: float
    focus_peak_seen: float
    coverage: float | None
    histogram: np.ndarray
    clipped_fraction: float
    peaking: np.ndarray | None      # arrives at its own rate
```

**Forbidden, and enforced by review rather than convention:**

- `live/` imports nothing from `ui/`. Ever.
- `live/` imports no Qt.
- `ui/` reaches into no member of `LivePipeline` except its signal stream.
- No UI concept — widgets, colours, layout, user preferences — appears inside
  `live/`.

### Why the rule exists

The available hard thing is putting this whole pipeline in a compiled extension
so it never enters Python per frame.

**We measured, and we should not build that now.** A full live loop is 29.5 ms,
of which **0.0017 ms is the Python interpreter**. A Rust pipeline would buy 1.7
microseconds per frame and cost the ability to change the pipeline quickly
during exactly the period when it will change constantly.

But keeping that door cheap is worth real discipline. With the boundary held,
swapping the guts for Rust or C++ later is a local change behind a stable
interface. Without it, the pipeline smears across the widgets and the door
closes quietly.

The hard thing to do now is boundary discipline. Not thread count.

---

## 5. Failure and recovery

The camera *will* vanish mid-session. The USB link runs near saturation at full
resolution, and jostling drops it. Two documented hazards compound this:

- INDIGO: *"a known issue with SDK and reopening the camera — exposure is not
  possible until the camera is reconnected."*
- INDI: the camera switches between video and software-trigger modes underneath
  you depending on what you asked for.

**So disconnection is a state, not an exception.**

```
CameraSession states:   disconnected → connecting → ready → streaming
                                ↑                              │
                                └──────── link lost ───────────┘
```

- `CameraSession` owns the handle and the state machine. Nothing else opens or
  closes the camera.
- On link loss, **acquisition restarts underneath a live analysis thread and UI
  that stay alive.** The user sees a reconnecting banner, not a dead window.
- On reconnect the active profile is re-applied in full — exposure, gain, mode
  flags, ROI — because the SDK does not remember it and half-applied state is
  worse than none.
- An in-progress capture **pauses rather than aborts**. Losing a 30-slice stack
  to a nudged cable is the kind of thing that makes people stop using software.
- Link speed is checked on reconnect. A drop from 5000 to 480 Mbps means the
  link fell back to USB 2.0 — that is the cable, and the app should say so
  rather than let the user wonder why everything got slow.

---

## 6. Escape hatches, deliberately kept open

| Hatch | Why it may be needed | Cost of keeping it |
|---|---|---|
| `camera/base.py` abstraction | tethered mirrorless later; other ToupTek OEM rebrands | one ABC |
| `live/` narrow interface | compiled pipeline if measurement ever demands it | discipline |
| `process/stitch.py` adapter | m2stitch vs ASHLAR unresolved; imagepro is Linux/Windows-only and cannot ship to Apple Silicon | one interface |
| `process/edf.py` pluggable merge | depth-map is the build, but wavelet stays available for comparison | one interface |

The stitch adapter is not hypothetical hedging. `libimagepro` has no arm64 slice
on macOS, so the portable path has to be ours regardless of how good theirs
turns out to be — but on Linux and Windows it may still be the faster option.

---

## 7. What gets built first

Ordered so that each step is verifiable on real glass rather than in the
abstract.

1. **`camera/` + `live/` skeleton.** Live view in a window, with the histogram
   and clipping indicator. That alone beats ToupLite for framing, and it
   exercises the threading contract before anything depends on it.
2. **Focus assist.** Metric trace, then peaking, then coverage. The measured
   highest-leverage feature, and the one with no prior art.
3. **`calib/`.** Dark, flat with opportunistic banking, white balance. Then the
   exposure handoff, which needs calibration to be verifiable.
4. **`capture/` single frames and Z-stacks.** Movement detection, settle,
   auto-trigger. Export via the existing DNG writers.
5. **`capture/mosaic.py`.** Overview anchoring, minimap, coverage, undo.
6. **`process/`.** Align, depth-map EDF, stitch, linear DNG out.
7. **Packaging.** Briefcase, macOS signing, first-run SDK fetch.

Steps 1–2 are already usable daily. That matters more than completeness.
