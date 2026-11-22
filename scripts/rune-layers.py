#!/usr/bin/env python3
"""
Cut Rune's Glade into the layers firox-rune animates (2.5D).

The wallpaper is one flat picture. This separates Rune from his glade so he
can breathe and blink in front of it, and finds what else moves:

    background.jpg  the glade with Rune taken out and the gap filled in (it
                    only shows at his edges as he moves)
    rune.png        Rune, cut from the wallpaper itself (so he looks exactly
                    as he does there), with a transparent background
    rune-solo.png   Rune from the mascot picture (crisper edges), for the
                    "just Rune" mode with no glade behind him
    scene.json      where things are, in the wallpaper's pixels: Rune's box
                    and the point he breathes from, his eyes (to blink), his
                    tail flame (embers rise from it), and the glowing runes,
                    crystals and stars (to pulse and twinkle)

How Rune is found: the mascot picture (firox-mascot.png, the same drawing on
white) is scaled and moved until its outline lines up with Rune's in the
wallpaper (edge correlation, to half a pixel); its outline, grown by a few
pixels, is the stencil.

Usage (needs numpy, scipy and pillow; not needed in the image):

    scripts/rune-layers.py WALLPAPER MASCOT OUT_DIR

e.g. scripts/rune-layers.py system_files/usr/share/backgrounds/firox/rune-glade.jpg \\
         system_files/usr/share/firox/icons/firox-mascot.png system_files/usr/share/firox/rune
"""

import argparse
import json
import os

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage
from scipy.signal import fftconvolve

SCENE_W, SCENE_H = 1376, 768        # the original wallpaper's size


def edges(a):
    gx = np.abs(np.diff(a, axis=1, prepend=a[:, :1]))
    gy = np.abs(np.diff(a, axis=0, prepend=a[:1]))
    e = gx + gy
    return (e - e.mean()) / (e.std() + 1e-6)


def luma(a):
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def align(scene, mascot):
    """(scale_x, scale_y, x, y) placing the mascot over Rune in the scene."""
    grey = np.asarray(Image.fromarray(scene).convert("L").filter(ImageFilter.GaussianBlur(1))).astype(np.float32)
    # coarse: one scale, whole picture
    ew = edges(grey)
    best = None
    for s in np.arange(0.55, 0.90, 0.01):
        a = np.asarray(mascot.resize((round(mascot.width * s), round(mascot.height * s)), Image.LANCZOS)).astype(np.float32)
        em = edges(luma(a) * a[..., 3] / 255) * (a[..., 3] > 128)
        c = fftconvolve(ew, em[::-1, ::-1], mode="valid")
        y, x = np.unravel_index(np.argmax(c), c.shape)
        score = c[y, x] / (np.abs(em).sum() + 1e-6)
        if best is None or score > best[0]:
            best = (score, s, x, y)
    _, s0, x0, y0 = best
    # fine: separate x and y scales, at twice the resolution, near the coarse fit
    up = 2
    big = np.asarray(Image.fromarray(grey.astype(np.uint8)).resize((grey.shape[1] * up, grey.shape[0] * up),
                                                                    Image.BICUBIC)).astype(np.float32)
    pad = 20
    span = round(mascot.width * s0) + 2 * pad
    ox, oy = max(x0 - pad, 0), max(y0 - pad, 0)
    ew = edges(big)[oy * up:(oy + span) * up, ox * up:(ox + span) * up]
    best = None
    for sx in np.arange(s0 - 0.02, s0 + 0.02, 0.002):
        for sy in np.arange(s0 - 0.02, s0 + 0.02, 0.002):
            a = np.asarray(mascot.resize((round(mascot.width * sx * up), round(mascot.height * sy * up)),
                                         Image.LANCZOS)).astype(np.float32)
            em = edges(luma(a) * a[..., 3] / 255) * (a[..., 3] > 128)
            if em.shape[0] > ew.shape[0] or em.shape[1] > ew.shape[1]:
                continue
            c = fftconvolve(ew, em[::-1, ::-1], mode="valid")
            y, x = np.unravel_index(np.argmax(c), c.shape)
            score = c[y, x] / (np.abs(em).sum() + 1e-6)
            if best is None or score > best[0]:
                best = (score, sx, sy, ox + x / up, oy + y / up)
    return best[1:]


def place(mascot, fit, size):
    """The mascot drawn where fit puts it, on a transparent canvas of size."""
    sx, sy, x, y = fit
    # (the inverse map, as Image.transform wants: canvas -> mascot pixels)
    return mascot.transform(size, Image.AFFINE, (1 / sx, 0, -x / sx, 0, 1 / sy, -y / sy), Image.BICUBIC)


def fill(rgb, hole):
    """Fill the hole smoothly from its surroundings (push-pull: average down a
    pyramid of the known pixels, then pull the averages back up into the hole)."""
    levels = []
    img, known = rgb.astype(np.float64), (~hole).astype(np.float64)
    while min(img.shape[:2]) > 4:
        levels.append((img, known))
        h, w = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
        wsum = known[:h, :w].reshape(h // 2, 2, w // 2, 2).sum((1, 3))
        csum = (img[:h, :w] * known[:h, :w, None]).reshape(h // 2, 2, w // 2, 2, 3).sum((1, 3))
        img = csum / np.maximum(wsum, 1e-9)[..., None]
        known = (wsum > 0).astype(np.float64)
    for big, kb in reversed(levels):
        up = np.asarray(Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize(
            (big.shape[1], big.shape[0]), Image.BILINEAR)).astype(np.float64)
        img = np.where(kb[..., None] > 0, big, up)
    # a soft blur inside the hole, so the fill has no blocky steps
    soft = ndimage.gaussian_filter(img, sigma=(6, 6, 0))
    return np.where(hole[..., None], soft, rgb).astype(np.uint8)


def components(mask, min_area=1, max_area=None):
    lab, n = ndimage.label(mask)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        part = lab[sl] == i
        area = int(part.sum())
        if area < min_area or (max_area and area > max_area):
            continue
        ys, xs = np.nonzero(part)
        out.append({"x": float(xs.mean() + sl[1].start), "y": float(ys.mean() + sl[0].start),
                    "w": int(np.ptp(xs) + 1), "h": int(np.ptp(ys) + 1), "area": area})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wallpaper")
    ap.add_argument("mascot")
    ap.add_argument("out")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    scene_img = Image.open(args.wallpaper).convert("RGB")
    if scene_img.size != (SCENE_W, SCENE_H):
        scene_img = scene_img.resize((SCENE_W, SCENE_H), Image.LANCZOS)
    scene = np.asarray(scene_img)
    mascot = Image.open(args.mascot).convert("RGBA")
    fit = align(scene, mascot)
    print("Rune in the wallpaper: scale %.3f x %.3f at (%.1f, %.1f)" % fit)

    placed = place(mascot, fit, (SCENE_W, SCENE_H))
    alpha = np.asarray(placed)[..., 3] > 100
    alpha = ndimage.binary_fill_holes(alpha)
    # The stencil: his outline grown a little (the wallpaper's own outline is
    # a touch wider than the mascot's), with a soft edge
    stencil = ndimage.binary_dilation(alpha, iterations=4)
    soft = ndimage.gaussian_filter(stencil.astype(np.float32), 1.2)
    soft = np.clip(soft * 1.4, 0, 1)
    ys, xs = np.nonzero(stencil)
    box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]

    rune = np.dstack([scene, (soft * 255).astype(np.uint8)])
    Image.fromarray(rune, "RGBA").crop(box).save(os.path.join(args.out, "rune.png"), optimize=True)
    # Rune from the mascot picture, same size and place (for "just Rune")
    placed.crop(box).save(os.path.join(args.out, "rune-solo.png"), optimize=True)

    # The glade without him: the gap (grown further, so no outline is left)
    hole = ndimage.binary_dilation(stencil, iterations=6)
    background = fill(scene, hole)
    Image.fromarray(background).save(os.path.join(args.out, "background.jpg"), quality=90, optimize=True,
                                     progressive=True)

    f = scene.astype(np.int32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    # Eyes: the amber in his head (the top third of him)
    head = np.zeros_like(alpha)
    # (his face is on the left: his wing, top right, is amber-orange too)
    head[box[1]:box[1] + (box[3] - box[1]) // 3, box[0]:box[0] + (box[2] - box[0]) * 9 // 20] = True
    amber = head & alpha & (r > 150) & (g > 85) & (g < 210) & (b < 115) & (r - b > 80)
    # (the pupil splits each eye in two: pieces closer than a few pixels are
    # one eye; then each eye is measured on its own amber)
    lab, n = ndimage.label(ndimage.binary_dilation(amber, iterations=5))
    eyes = []
    for i in range(1, n + 1):
        part = amber & (lab == i)
        if part.sum() >= 12:
            ys, xs = np.nonzero(part)
            eyes.append({"x": float(xs.mean()), "y": float(ys.mean()), "w": int(np.ptp(xs) + 1),
                         "h": int(np.ptp(ys) + 1), "area": int(part.sum())})
    eyes = sorted(eyes, key=lambda c: -c["area"])[:2]
    eyes.sort(key=lambda c: c["x"])
    for e in eyes:
        # the fur around the eye: blue pixels in a ring around it
        ring = np.zeros_like(alpha)
        cx, cy, rad = int(e["x"]), int(e["y"]), max(e["w"], e["h"])
        ring[cy - rad * 2:cy + rad * 2, cx - rad * 2:cx + rad * 2] = True
        fur = ring & alpha & (b > r + 40) & (b > 120)
        e["fur"] = [int(np.median(f[..., i][fur])) for i in range(3)] if fur.any() else [47, 98, 192]
    # Tail flame: orange and gold on his left (the wing, on his right, is orange too)
    mid_x = (box[0] + box[2]) / 2
    left = np.zeros_like(alpha)
    left[:, :int(mid_x)] = True
    flame = left & stencil & (r > 170) & (r > b + 50) & (g < r)
    ys, xs = np.nonzero(flame)
    flame_box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if xs.size else None
    # Glowing runes (cyan and gold glyphs on and around the stone circle) and
    # the crystals: bright, saturated, outside him, in the lower half
    lower = np.zeros_like(alpha)
    lower[SCENE_H // 2:, :] = True
    sat = f.max(axis=2) - f.min(axis=2)
    glow = lower & ~hole & (f.max(axis=2) > 200) & (sat > 60)
    glow_parts = components(ndimage.binary_dilation(glow, iterations=2), min_area=20)
    # Stars: the pale blue sparkles in the sky, a little brighter than the
    # sky around them (the orange specks are embers: those are in glows)
    sky = np.zeros_like(alpha)
    sky[:SCENE_H * 3 // 5, :] = True
    lum = luma(f).astype(np.float32)
    lift = lum - ndimage.gaussian_filter(lum, 6)
    bright = sky & ~hole & (lift > 18) & (b > r + 30)
    stars = [s for s in components(bright, min_area=3, max_area=400) if 3 <= s["w"] < 40 and 3 <= s["h"] < 40]
    stars.sort(key=lambda s: -s["area"])

    meta = {
        "size": [SCENE_W, SCENE_H],
        "rune_box": box,
        # he breathes from where he sits: the bottom middle of his body
        "rune_anchor": [(box[0] + box[2]) / 2, box[3] - 0.08 * (box[3] - box[1])],
        "eyes": [{k: e[k] for k in ("x", "y", "w", "h", "fur")} for e in eyes],
        "flame_box": flame_box,
        "glows": [{k: c[k] for k in ("x", "y", "w", "h")} for c in glow_parts],
        "stars": [{k: s[k] for k in ("x", "y", "w", "h")} for s in stars[:40]],
        "fit": [round(v, 4) for v in fit],
    }
    with open(os.path.join(args.out, "scene.json"), "w") as fh:
        json.dump(meta, fh, indent=1)
    # The glow layer: just the glowing parts, to brighten and dim them
    glow_a = (ndimage.gaussian_filter(glow.astype(np.float32), 1.5) * 255).clip(0, 255).astype(np.uint8)
    # (black where it's transparent: the file stays small)
    glow_img = np.dstack([np.where(glow_a[..., None] > 0, scene, 0).astype(np.uint8), glow_a])
    Image.fromarray(glow_img, "RGBA").save(os.path.join(args.out, "glow.png"), optimize=True)
    print(f"eyes {len(eyes)}, flame {flame_box}, glowing parts {len(glow_parts)}, stars {len(stars)}")


if __name__ == "__main__":
    main()
