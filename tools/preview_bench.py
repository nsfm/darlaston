"""What preview-from-raw would cost, per frame.

The live preview is the camera ISP's output, which carries the ISP's own
fixed colour rendering (a ~3x blue boost to neutralise this green-dominant
sensor) rather than the per-optics flat the captured DNG is developed with.
The two colour chains disagree, and the preview keeps a cast the file does
not. One cure is to stop using the ISP and render the preview from raw the
same way `process.develop` renders the file: demosaic, white balance in
linear, gamma.

The fear was framerate. This measures the truth of it. The finding: the cost
is set entirely by *what resolution you demosaic*, and the preview is only
~2.2 MP. Demosaicing a full 20 MP sensor frame every frame is the only
"devastating" case; at the resolution the preview actually shows, the whole
thing is ~2 ms.

Measured once (numbers drift with hardware, the shape does not):

    size                  demosaic u16   +WB/gamma LUT   total    ceiling
    2.2MP (raw preview)      1.0 ms          1.2 ms       2.3 ms   ~440 fps
    5MP                      4.1 ms          2.8 ms       6.9 ms   ~145 fps
    10MP                    10.9 ms          5.5 ms      16.4 ms    ~61 fps
    20MP (full raw)         17.2 ms         13.9 ms      31.0 ms    ~32 fps

Against a 33 ms (30 fps) budget the analysis already shares. The conclusion:
preview-from-raw is affordable *iff* the camera can hand back a binned raw
Bayer stream near preview resolution (the sensor's binned modes are 2736x1824
and 1824x1216 -- see the binned-capture TODO). The CPU is not the wall; the
SDK's willingness to deliver a small raw stream is. The full-quality
`develop()` path (percentile stretch, float band loop) is ~1 s and is *not*
the preview path -- the preview wants demosaic + a combined WB/gamma lookup,
which is the ~2 ms above.

Run: PYTHONPATH=. .venv/bin/python tools/preview_bench.py [raw.dng]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _bench(fn, n=30, warm=3) -> float:
    for _ in range(warm):
        fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1000.0     # ms per call


#: A combined white-balance-and-gamma lookup, the whole per-frame colour cost
#: of a raw preview once the frame is demosaiced. One cv2.LUT, exact for
#: 8-bit, which is what a preview is.
def _wb_gamma_lut() -> np.ndarray:
    gains = (1.4, 1.0, 2.6)                            # a real-ish blue boost
    lin = (np.arange(256, dtype=np.float32) / 255.0) ** 2.2
    table = np.empty((1, 256, 3), np.uint8)
    for ch, gain in enumerate(gains):
        out = np.clip(lin * gain, 0.0, 1.0) ** (1 / 2.2)
        table[0, :, ch] = np.clip(out * 255.0 + 0.5, 0, 255)
    return table


def main() -> None:
    sizes = {
        "2.2MP (raw preview)": (1216, 1824),
        "5MP": (1824, 2736),
        "10MP": (2448, 4080),
        "20MP (full raw)": (3648, 5440),
    }
    lut = _wb_gamma_lut()
    print(f"{'size':<22}{'demosaic u16':>14}{'demosaic u8':>13}"
          f"{'+WB/gamma':>11}{'total u16':>11}{'ceiling':>9}")
    for name, (h, w) in sizes.items():
        bay16 = (np.random.rand(h, w) * 4095).astype(np.uint16)
        bay8 = (np.random.rand(h, w) * 255).astype(np.uint8)
        bgr8 = cv2.cvtColor(bay8, cv2.COLOR_BayerGR2BGR)
        dm16 = _bench(lambda: cv2.cvtColor(bay16, cv2.COLOR_BayerGR2BGR))
        dm8 = _bench(lambda: cv2.cvtColor(bay8, cv2.COLOR_BayerGR2BGR))
        lutms = _bench(lambda: cv2.LUT(bgr8, lut))
        tot = dm16 + lutms
        print(f"{name:<22}{dm16:>12.2f}ms{dm8:>11.2f}ms{lutms:>9.2f}ms"
              f"{tot:>9.2f}ms{1000 / tot:>7.0f}fps")
    print("\nbudget: 30 fps = 33.3 ms/frame, shared with the analysis pass.")

    if len(sys.argv) > 1:                             # a real frame, for scale
        from darlaston.process import develop, stitch
        raw = stitch.read_bayer_dng(sys.argv[1])
        white = stitch.read_white_level(sys.argv[1])
        t = time.perf_counter()
        develop.develop(raw, pattern="GBRG", white=white, stretch=True)
        dv = (time.perf_counter() - t) * 1000.0
        print(f"\nfull develop() on {raw.shape[1]}x{raw.shape[0]} "
              f"(the quality path, not the preview): {dv:.0f} ms")


if __name__ == "__main__":
    main()
