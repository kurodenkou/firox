#!/usr/bin/env python3
"""
Build the Fish Tank's saltwater fish from Reef Glass's art.

Reef Glass (https://github.com/timsaucer/reef_glass_roku_screensaver) made each
fish as a keyed side photo plus a 3D mesh reconstructed from it (TripoSR), and
dedicated them to the public domain (CC0 1.0). This turns them into what
firox_fishtank.py loads from /usr/share/firox/fishtank:

    NAME.mesh   the mesh, simplified, turned to the tank's axes (head +x, back
                +y, length 1 from x = +0.5 to -0.5), with vertex colours
    NAME.png    the side photo (head right) cropped to the fish, its edge
                pulled in and softened so the outline doesn't land on the
                face and back when it's projected onto the flanks

Usage (needs trimesh, fast-simplification, scipy, opencv-python-headless,
pillow; not needed in the image):

    scripts/fishtank-assets.py MESH_DIR OUT_DIR [--faces 6000]

MESH_DIR holds Reef Glass's gen/meshes/NAME.ply and NAME_full.png (Git LFS:
media.githubusercontent.com/media/timsaucer/reef_glass_roku_screensaver/main/gen/meshes/...).

.mesh format, little-endian: b"FTM1", uint32 vertex count, uint32 index
count, float32 x0 x1 y0 y1 (the box the photo spans), then per vertex float32
position[3] normal[3] colour[3], then uint32 indices.
"""

import argparse
import struct
import sys
from pathlib import Path

import cv2
import fast_simplification
import numpy as np
import trimesh
from PIL import Image
from scipy.spatial import cKDTree

FISH = ["clown", "bluetang", "yellowtang", "damsel", "gramma", "puffer"]


def convert(ply, photo, out_mesh, out_png, faces):
    m = trimesh.load(ply, process=False)
    v = np.asarray(m.vertices, np.float64)
    col = np.asarray(m.visual.vertex_colors, np.float64)[:, :3] / 255
    v -= (v.max(0) + v.min(0)) / 2
    # Reef Glass meshes: +y head, +z up, x sideways. Ours: +x head, +y up, z sideways.
    v = v[:, [1, 2, 0]]
    v /= v[:, 0].max() - v[:, 0].min()

    target = 1 - min(1.0, faces / len(m.faces))
    pts, tris = fast_simplification.simplify(v.astype(np.float32), np.asarray(m.faces, np.int32),
                                            target_reduction=target)
    simple = trimesh.Trimesh(pts, tris, process=True)
    pts, tris = np.asarray(simple.vertices), np.asarray(simple.faces)
    _, nearest = cKDTree(v).query(pts)
    colours = col[nearest]
    normals = np.asarray(simple.vertex_normals)

    x0, x1 = float(pts[:, 0].min()), float(pts[:, 0].max())
    y0, y1 = float(pts[:, 1].min()), float(pts[:, 1].max())
    data = np.hstack([pts, normals, colours]).astype("<f4")
    with open(out_mesh, "wb") as f:
        f.write(b"FTM1")
        f.write(struct.pack("<II", len(pts), tris.size))
        f.write(struct.pack("<4f", x0, x1, y0, y1))
        f.write(data.tobytes())
        f.write(tris.astype("<u4").tobytes())

    im = Image.open(photo).convert("RGBA")
    a = np.array(im)[..., 3]
    ys, xs = np.nonzero(a > 128)
    im = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    if im.width > 512:
        im = im.resize((512, max(1, round(im.height * 512 / im.width))), Image.LANCZOS)
    arr = np.array(im)
    al = (arr[..., 3] > 128).astype(np.uint8)
    e = max(2, im.width // 60)
    al = cv2.erode(al, np.ones((2 * e + 1, 2 * e + 1), np.uint8)).astype(np.float32)
    arr[..., 3] = (cv2.GaussianBlur(al, (0, 0), e * 0.7) * 255).astype(np.uint8)
    Image.fromarray(arr, "RGBA").save(out_png, optimize=True)
    return len(pts), len(tris)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mesh_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--faces", type=int, default=6000)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in FISH:
        n, t = convert(args.mesh_dir / f"{name}.ply", args.mesh_dir / f"{name}_full.png",
                       args.out_dir / f"{name}.mesh", args.out_dir / f"{name}.png", args.faces)
        print(f"{name}: {n} vertices, {t} triangles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
