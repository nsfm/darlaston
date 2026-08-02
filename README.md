# darlaston

Originally built for photographing diatom arrangements and other microscopic art objects, _darlaston_ is the photomicrography tool for creators.

Named for **Herbert William Hutton Darlaston** of Birmingham (1867-1949), who mounted nearly a thousand microscope slides in his first year at the bench only to give most of them away. He continued his work for the next fifty years under the promise _Every Slide Perfect_.

## What it does

- **Captures** 12-bit RAW, including 76 EXIF tags describing the optics it was taken through.
- **Tracks the stage from the image itself,** building a slide map you can pin and navigate by.
- **Stitches mosaics,** allowing you to capture super high resolution images
- **Stacks focus** without touching the computer. Rack the fine focus, pause, rack again: the trigger watches the live sharpness field and fires the shutter.
- **Composes the two.** With both modes on, each field's rack-pauses build a tiled stack, and sliding to the next field seals it. The merge runs in the background while you rack the next one. Single shots and stacks mix freely in one mosaic.
- **Renders the depth map** into wigglegrams, focus pulls, turntables, stereo pairs, red/cyan anaglyphs, autostereograms, printable watertight meshes, or relief images we call **DIC - Darlaston Inferred Contrast**.

## Why

The only free software for Linux that drives these ToupTek cameras is ToupLite, and it quietly discards most of what the hardware can do. On a real darkfield capture, **90.6 % of all pixels occupy just four luma levels**; at 12 bits that same tonal region gets 64. The faint outer glow around diatoms is not dim in those files. It is gone. By providing RAW capture capabilities, _darlaston_ returns power to the user.

## License

This software is provided freely to use, share, and modify under the GPLv3. See [LICENSE](LICENSE).

**Linking exception.** As a special exception, the copyright holders give permission to link this program with the proprietary ToupTek SDK libraries (`libtoupcam`, `libimagepro`, and companions), and to distribute the resulting executable, without those libraries falling under the terms of the GPL. This exception does not invalidate any other reasons the executable might be covered by the GPL.

### On the ToupTek SDK

The SDK ships with no license or copyright notice. That's too ambiguous for us to include the SDK with this tool, so users must install the SDK themselves from [ToupTek's download centre](https://www.touptekphotonics.com/download/?category=SDK).

## Acknowledgements

[pyuscope](https://github.com/JohnDMcMaster/pyuscope) and [gst-plugin-toupcam](https://github.com/JohnDMcMaster/gst-plugin-toupcam) by John McMaster provided a helpful ToupTek SDK reference.
