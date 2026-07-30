"""The stack as an object: a turntable render, and a mesh you can print.

A depth map with its texture is a heightfield, and a heightfield is a
mesh. This exports one -- binary PLY with per-vertex colour, which every
slicer, Blender, MeshLab and Windows 3D Viewer opens -- and renders a
turntable animation of it, so the shape can be *seen* without anyone
installing anything.

Two honest limits, both stated in the exported file's own header. First,
this is a relief, not a solid: a focus stack measures the surface facing
the objective and knows nothing about the far side of the frustule, so
what comes out is a landscape of the subject, not a model of it. Second,
depth is in *slices*, not microns, because nothing in the capture path
measures the fine focus travel -- so the vertical scale is a chosen
exaggeration rather than a measurement, and the header says so.

Printing wants a solid, so the mesh is optionally skirted and closed:
walls dropped from the border down to a flat base, which turns the
landscape into a slab with the subject standing on it.
"""
from __future__ import annotations

import struct
from pathlib import Path

import cv2
import numpy as np

from .wiggle import FRAME_MS, load_pair

#: Grid width the heightfield is sampled at. 400 x ~270 is 108k vertices
#: and about 215k triangles -- detailed enough for the striae to survive
#: as geometry, small enough to open instantly anywhere.
GRID_W = 400
#: Vertical exaggeration: the depth range as a fraction of the mesh's
#: width. Real diatom relief is far shallower than this; at true scale
#: the print is a flat tile, so the default is honestly theatrical.
RELIEF = 0.12
#: Smoothing of the depth before it becomes geometry, in grid cells.
#: Residual slice steps read as terraces on a printed surface.
SMOOTH = 1.6
#: Turntable frames for one full revolution.
SPIN_FRAMES = 60


def _height(directory: Path, invert: bool, grid: int):
    """(colour grid, height grid) sampled to `grid` wide."""
    img, depth = load_pair(directory, width=10 ** 9, invert=invert)
    h, w = depth.shape
    size = (grid, max(2, round(grid * h / w)))
    colour = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    height = cv2.resize(depth, size, interpolation=cv2.INTER_AREA)
    height = cv2.GaussianBlur(height, (0, 0), SMOOTH)
    return colour, height


def export_ply(directory: Path | str, invert: bool = False,
               grid: int = GRID_W, relief: float = RELIEF,
               solid: bool = True) -> Path:
    """Write `model.ply`: a coloured heightfield, optionally closed."""
    directory = Path(directory)
    colour, height = _height(directory, invert, grid)
    gh, gw = height.shape

    # Model space: x across [0, 1], y down, z the exaggerated relief.
    xs = np.linspace(0.0, 1.0, gw, dtype=np.float32)
    ys = np.linspace(0.0, gh / gw, gh, dtype=np.float32)
    gx, gy = np.meshgrid(xs, ys)
    gz = height * (relief / 2.0)
    floor = float(gz.min()) - relief * 0.25

    verts = [np.stack([gx.ravel(), gy.ravel(), gz.ravel()], 1)]
    cols = [colour.reshape(-1, 3)[:, ::-1]]                 # BGR -> RGB
    faces = []
    idx = np.arange(gh * gw, dtype=np.int32).reshape(gh, gw)
    a = idx[:-1, :-1].ravel()
    b = idx[:-1, 1:].ravel()
    c = idx[1:, 1:].ravel()
    d = idx[1:, :-1].ravel()
    faces.append(np.stack([a, b, c], 1))
    faces.append(np.stack([a, c, d], 1))

    if solid:
        # Skirt: the border dropped to a flat base, then the base capped.
        # Without it the print is a sheet, and slicers refuse sheets.
        base = len(verts[0])
        border = np.concatenate([idx[0, :], idx[1:, -1],
                                 idx[-1, -2::-1], idx[-2:0:-1, 0]])
        wall = np.stack([gx.ravel()[border], gy.ravel()[border],
                         np.full(border.size, floor, np.float32)], 1)
        verts.append(wall)
        cols.append(cols[0][border])
        n = border.size
        for k in range(n):
            top_a, top_b = border[k], border[(k + 1) % n]
            bot_a, bot_b = base + k, base + (k + 1) % n
            faces.append(np.array([[top_a, bot_a, bot_b],
                                   [top_a, bot_b, top_b]], np.int32))
        # Cap the base with a fan from its first vertex.
        fan = np.array([[base, base + k + 1, base + k]
                        for k in range(1, n - 1)], np.int32)
        faces.append(fan)

    v = np.concatenate(verts).astype(np.float32)
    rgb = np.concatenate(cols).astype(np.uint8)
    f = np.concatenate(faces).astype(np.int32)

    target = directory / "model.ply"
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"comment made by darlaston from {directory.name}\n"
        "comment a focus stack sees only the surface facing the "
        "objective, so this is a relief of the subject, not a model of it\n"
        f"comment z is {relief:g} of the model width, an exaggeration: "
        "depth is measured in slices and nothing here measures the fine "
        "focus travel in microns\n"
        f"element vertex {len(v)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        f"element face {len(f)}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n")
    with target.open("wb") as fh:
        fh.write(header.encode("ascii"))
        body = np.empty(len(v), dtype=[("p", "<f4", 3), ("c", "u1", 3)])
        body["p"] = v
        body["c"] = rgb
        fh.write(body.tobytes())
        tri = np.empty(len(f), dtype=[("n", "u1"), ("i", "<i4", 3)])
        tri["n"] = 3
        tri["i"] = f
        fh.write(tri.tobytes())
    return target


def _shade(height: np.ndarray, colour: np.ndarray, azimuth: float,
           tilt: float) -> np.ndarray:
    """Lambertian shading of the heightfield, lit from `azimuth`."""
    gy, gx = np.gradient(height.astype(np.float32))
    scale = 6.0
    nx, ny, nz = -gx * scale, -gy * scale, np.ones_like(gx)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    az, el = np.deg2rad(azimuth), np.deg2rad(tilt)
    lx, ly, lz = np.cos(az) * np.cos(el), np.sin(az) * np.cos(el), np.sin(el)
    lam = np.clip((nx * lx + ny * ly + nz * lz) / norm, 0, 1)
    lit = colour.astype(np.float32) * (0.35 + 0.75 * lam)[:, :, None]
    return np.clip(lit, 0, 255).astype(np.uint8)


def turntable(directory: Path | str, invert: bool = False,
              frames: int = SPIN_FRAMES, grid: int = 900) -> Path:
    """`turntable.webm`: the heightfield lit from a circling light.

    The light orbits rather than the camera. Rotating the object needs a
    real renderer and a depth buffer; moving the light needs a dot
    product, reads as the same "this is a solid thing" cue, and cannot
    tear at silhouettes because there are none.
    """
    directory = Path(directory)
    colour, height = _height(directory, invert, grid)
    seq = [_shade(height, colour, 360.0 * k / frames, 35.0)
           for k in range(frames)]
    target = directory / "turntable.webm"
    h, w = seq[0].shape[:2]
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"VP90"),
                             1000.0 / FRAME_MS, (w, h))
    if not writer.isOpened():
        raise RuntimeError("no VP9 encoder available for the turntable")
    for _cycle in range(2):
        for frame in seq:
            writer.write(frame)
    writer.release()
    return target


if __name__ == "__main__":
    import sys
    d = sys.argv[1]
    print(export_ply(d))
    print(turntable(d))
