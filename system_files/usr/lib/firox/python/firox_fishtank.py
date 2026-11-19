"""
A planted freshwater aquarium, rendered live with OpenGL 3.3 (the Fish Tank
screen saver, /usr/libexec/firox/screensavers/fishtank).

Everything is made in code when the tank starts, so no two runs look the same:

* gravel, light shafts, the rippling surface seen from below and the moving
  light patterns (caustics) are drawn per pixel by one full-screen shader;
* rocks are pebble-cut spheres; plants are swaying ribbons and leaves;
* fish are 3D bodies with fins, bent by a travelling wave the way real fish
  swim. Each one steers on its own (wandering, schooling, keeping apart,
  avoiding rocks and the glass), with its tail beat following its speed.

Tank(seed) holds the scene. The caller owns the GL context: call gl_init()
once it's current, resize(w, h) when the size changes, step(dt) to move time
on, render(target_fbo) to draw, and gl_free() before the context goes away.
It needs PyOpenGL (python3-pyopengl).

The saltwater fish (clownfish, tangs, damselfish, royal grammas, pufferfish)
are Reef Glass's art (github.com/timsaucer/reef_glass_roku_screensaver, CC0):
a 3D mesh per fish with its side photo projected onto the flanks, prepared by
scripts/fishtank-assets.py into DATA_DIR. Without those files they're left out.
"""

import ctypes
import math
import os
import random
import re
import struct
from array import array

import OpenGL

OpenGL.ERROR_CHECKING = False
from OpenGL import GL  # noqa: E402

SURF = 11.5            # water surface height
FRONT = 1.2            # front glass (z); the camera is outside it
CAM = (0.0, 7.4, 15.0)
LOOK = (0.0, 5.0, -6.0)
FOVY = 40.0
BACK = -14.0           # back of the tank
FLOOR_K = 0.11         # gravel rises towards the back: y = -FLOOR_K * z
Z_NEAR, Z_FAR = 0.5, 80.0
DATA_DIR = "/usr/share/firox/fishtank"      # (set firox_fishtank.DATA_DIR to try other files)
TAU = math.tau


def floor_y(z):
    return -FLOOR_K * z


# ---------------------------------------------------------------- matrices
# Column-major 4x4 lists, as GL wants them.

def perspective(fovy, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy) / 2)
    return [f / aspect, 0, 0, 0,
            0, f, 0, 0,
            0, 0, (far + near) / (near - far), -1,
            0, 0, 2 * far * near / (near - far), 0]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    length = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / length, a[1] / length, a[2] / length)


def look_at(eye, center, up):
    f = _norm(_sub(center, eye))
    s = _norm(_cross(f, up))
    u = _cross(s, f)
    return [s[0], u[0], -f[0], 0,
            s[1], u[1], -f[1], 0,
            s[2], u[2], -f[2], 0,
            -_dot(s, eye), -_dot(u, eye), _dot(f, eye), 1]


def mat_mul(a, b):
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def mat_inverse(m):
    # Gauss-Jordan on a row-major copy
    a = [[m[c * 4 + r] for c in range(4)] + [1.0 if r == i else 0.0 for i in range(4)]
         for r in range(4)]
    for col in range(4):
        piv = max(range(col, 4), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(4):
            if r != col:
                f = a[r][col]
                a[r] = [v - f * w for v, w in zip(a[r], a[col])]
    return [a[r][4 + c] for c in range(4) for r in range(4)]


# ---------------------------------------------------------------- shaders

COMMON = """
#version 330 core
uniform float uTime;
uniform vec3 uCam;
const float SURF = %(SURF).3f;
const float FRONT = %(FRONT).3f;
const float FLOOR_K = %(FLOOR_K).3f;
const float BACK = %(BACK).3f;
const vec3 LIGHT = vec3(0.2287, 0.9149, 0.3330);

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
vec2 hash22(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.xx + p3.yz) * p3.zy);
}
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), u.x),
               mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
    return v;
}
// Cell distances: x = nearest, y = second nearest; z = id of the nearest cell
vec3 cells(vec2 q) {
    vec2 i = floor(q), f = fract(q);
    float d1 = 8.0, d2 = 8.0, id = 0.0;
    for (int y = -1; y <= 1; y++)
    for (int x = -1; x <= 1; x++) {
        vec2 g = vec2(x, y);
        vec2 o = hash22(i + g);
        float d = length(g + o - f);
        if (d < d1) { d2 = d1; d1 = d; id = hash12(i + g + 7.31); }
        else if (d < d2) d2 = d;
    }
    return vec3(d1, d2, id);
}
// Light focused by the moving surface: bright wandering webs
float caustic(vec2 p) {
    float c = 0.0;
    for (int l = 0; l < 2; l++) {
        float fl = float(l);
        vec2 q = p * (1.0 + 0.7 * fl) + vec2(uTime * 0.13, -uTime * 0.09) * (1.0 - 2.3 * fl);
        q += 0.45 * vec2(sin(q.y * 1.3 + uTime * 0.7), sin(q.x * 1.1 - uTime * 0.6));
        vec2 i = floor(q), f = fract(q);
        float d1 = 8.0, d2 = 8.0;
        for (int y = -1; y <= 1; y++)
        for (int x = -1; x <= 1; x++) {
            vec2 g = vec2(x, y);
            vec2 o = 0.5 + 0.42 * sin(uTime * 0.5 + TAU_ * hash22(i + g));
            float d = length(g + o - f);
            if (d < d1) { d2 = d1; d1 = d; } else if (d < d2) d2 = d;
        }
        c += pow(clamp(1.0 - (d2 - d1) * 2.0, 0.0, 1.0), 3.5);
    }
    return c * (0.55 + 0.7 * noise(p * 0.23 + uTime * 0.02));
}
vec3 waterCol(float y) {
    vec3 deep = vec3(0.020, 0.085, 0.095);
    vec3 mid = vec3(0.060, 0.250, 0.250);
    vec3 top = vec3(0.260, 0.520, 0.490);
    float h = clamp(y / SURF, 0.0, 1.0);
    return h < 0.6 ? mix(deep, mid, h / 0.6) : mix(mid, top, (h - 0.6) / 0.4);
}
float waterDist(vec3 p) {
    vec3 d = p - uCam;
    float len = length(d);
    float enter = (uCam.z - FRONT) / max(-d.z / len, 1e-3);
    return max(len - enter, 0.0);
}
vec3 fog(vec3 col, vec3 p) {
    float f = 1.0 - exp(-waterDist(p) * 0.042);
    return mix(col, waterCol(mix(p.y, 5.5, 0.35)), f);
}
float depthLight(float y) { return mix(0.40, 1.05, smoothstep(0.0, SURF, y)); }

vec3 shade(vec3 albedo, vec3 n, vec3 p, float spec, float shin, float trans) {
    vec3 v = normalize(uCam - p);
    float dl = depthLight(p.y);
    float nl = dot(n, LIGHT);
    float c = caustic(p.xz * 0.45 + p.y * 0.05) * smoothstep(-0.2, 0.7, n.y);
    vec3 amb = mix(waterCol(0.5) * 1.2, waterCol(SURF) * 1.0, n.y * 0.5 + 0.5);
    vec3 light = amb + vec3(1.0, 0.98, 0.92) * (max(nl * 0.5 + 0.5, 0.0) * 0.45 + max(nl, 0.0) * 0.55
                                                  + c * 0.6) * dl;
    vec3 col = albedo * light;
    col += albedo * trans * dl * 0.35;
    vec3 h = normalize(LIGHT + v);
    col += spec * pow(max(dot(n, h), 0.0), shin) * dl * vec3(0.95, 1.0, 0.95);
    return col;
}
""".replace("TAU_", "6.2831853") % {"SURF": SURF, "FRONT": FRONT, "FLOOR_K": FLOOR_K, "BACK": BACK}

QUAD_VS = """
layout(location = 0) in vec2 aPos;
out vec2 vNdc;
void main() { vNdc = aPos; gl_Position = vec4(aPos, 0.0, 1.0); }
"""

BACK_FS = """
in vec2 vNdc;
out vec4 frag;
uniform mat4 uInvVP;
uniform mat4 uVP;
uniform vec4 uShadow[64];
uniform int uShadowN;

vec3 gravel(vec3 p) {
    vec2 q = p.xz * 8.0;
    float fw = length(fwidth(q));
    float detail = 1.0 - smoothstep(0.3, 1.1, fw);
    vec3 cl = cells(q + 0.35 * vec2(noise(q * 0.5), noise(q * 0.5 + 4.0)));
    float r = cl.z;
    vec3 c = mix(vec3(0.66, 0.57, 0.44), vec3(0.93, 0.85, 0.70), r);
    c = mix(c, vec3(0.42, 0.38, 0.33), step(0.80, fract(r * 13.7)));
    c = mix(c, vec3(0.80, 0.70, 0.55), step(0.90, fract(r * 7.3)));
    float edge = smoothstep(0.0, 0.18, cl.y - cl.x);
    float dome = clamp(1.0 - cl.x * 1.1, 0.0, 1.0);
    vec3 pebbles = c * (0.62 + 0.25 * edge + 0.22 * dome);
    pebbles *= 0.92 + 0.16 * hash12(floor(p.xz * 80.0));
    vec3 far = vec3(0.70, 0.62, 0.50);
    vec3 g = mix(far, pebbles, detail);
    // patches of darker detritus and finer sand
    g *= 0.82 + 0.3 * fbm(p.xz * 0.4);
    return g;
}

vec3 surfaceBelow(vec3 p, vec3 rd) {
    // Seen from below at a low angle the surface is a wobbling mirror of the tank,
    // laced with bright lines where the ripples catch the light
    vec2 q = p.xz * vec2(0.22, 0.32);
    float n = fbm(q * 2.0 + vec2(uTime * 0.05, uTime * 0.03));
    float web = caustic(q * 1.6 + n * 0.6);
    vec3 mirror = mix(waterCol(SURF * 0.55), waterCol(SURF * 0.8), n);
    float window = smoothstep(0.66, 0.8, rd.y);
    vec3 c = mix(mirror, vec3(0.75, 0.9, 0.92), window);
    c += vec3(0.55, 0.78, 0.74) * web * 0.35;
    return c;
}

vec3 rays(vec3 ro, vec3 rd, float len, float jitter) {
    const int N = 12;
    float stepL = min(len, 34.0) / float(N);
    float acc = 0.0;
    for (int i = 0; i < N; i++) {
        float t = stepL * (float(i) + jitter);
        vec3 p = ro + rd * t;
        if (p.y > SURF) break;
        vec2 sp = vec2(p.x - p.y * 0.25, p.z - p.y * 0.36) * 0.30;
        float s = noise(sp * vec2(1.0, 0.55) + vec2(uTime * 0.03, uTime * 0.008));
        s = pow(smoothstep(0.48, 0.95, s), 2.0);
        float fall = smoothstep(-2.0, SURF, p.y);
        acc += s * fall * exp(-t * 0.045);
    }
    return vec3(0.55, 0.80, 0.74) * acc * stepL * 0.042;
}

void main() {
    vec4 a = uInvVP * vec4(vNdc, -1.0, 1.0);
    vec4 b = uInvVP * vec4(vNdc, 1.0, 1.0);
    vec3 rd = normalize(b.xyz / b.w - a.xyz / a.w);
    float enter = (uCam.z - FRONT) / max(-rd.z, 1e-4);
    float den = rd.y + FLOOR_K * rd.z;
    float tF = den < -1e-5 ? -(uCam.y + FLOOR_K * uCam.z) / den : 1e9;
    float tS = rd.y > 1e-5 ? (SURF - uCam.y) / rd.y : 1e9;
    float tB = rd.z < -1e-5 ? (BACK - uCam.z) / rd.z : 1e9;
    vec3 col, p;
    float tHit = min(min(tF, tS), tB);
    p = uCam + rd * tHit;
    if (tHit == tF) {
        vec3 alb = gravel(p);
        float sh = 0.0;
        for (int i = 0; i < 64; i++) {
            if (i >= uShadowN) break;
            vec4 s = uShadow[i];
            float h = max(s.y + FLOOR_K * s.z, 0.0);
            float r = s.w * (0.8 + h * 0.2);
            vec2 d = p.xz - s.xz;
            sh += exp(-dot(d, d) / (r * r)) * 0.5 / (1.0 + h * 0.3);
        }
        sh = min(sh, 0.55);
        float c = caustic(p.xz * 0.42) * (1.0 - sh * 1.5);
        float dl = depthLight(p.y);
        col = alb * (waterCol(2.0) * 1.6 + vec3(1.0, 0.97, 0.88) * (0.62 + c * 0.9) * dl);
        col *= 1.0 - sh;
    } else if (tHit == tS) {
        col = surfaceBelow(p, rd);
    } else {
        // the back of the tank: a dark backdrop, lighter towards the top
        float g = smoothstep(0.0, SURF, p.y);
        col = mix(vec3(0.010, 0.030, 0.035), vec3(0.06, 0.16, 0.17), g * g)
            * (0.85 + 0.3 * fbm(p.xy * 0.3 + uTime * 0.01));
    }
    vec4 clip = uVP * vec4(p, 1.0);
    float depth = clamp(clip.z / clip.w * 0.5 + 0.5, 0.0, 1.0);
    col = fog(col, p);
    float wd = max(tHit - enter, 0.0);
    col += rays(uCam + rd * enter, rd, wd, hash12(gl_FragCoord.xy + fract(uTime) * 61.0));
    frag = vec4(col, 1.0);
    gl_FragDepth = depth;
}
"""

ROCK_VS = """
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNor;
uniform mat4 uVP;
out vec3 vWorld, vNor;
void main() { vWorld = aPos; vNor = aNor; gl_Position = uVP * vec4(aPos, 1.0); }
"""

ROCK_FS = """
in vec3 vWorld, vNor;
out vec4 frag;
void main() {
    vec3 p = vWorld, n = normalize(vNor);
    float strata = fbm(vec2(p.x * 0.7 + p.z * 0.5, p.y * 2.4));
    vec3 alb = mix(vec3(0.27, 0.28, 0.29), vec3(0.50, 0.51, 0.50), strata);
    float vline = abs(fract(p.y * 1.1 + fbm(p.xz * 0.6 + p.y * 0.3) * 1.8) - 0.5);
    alb = mix(alb, vec3(0.74, 0.74, 0.70), smoothstep(0.035, 0.0, vline) * 0.65);
    alb *= 0.72 + 0.35 * fbm(p.xy * 3.1 + p.zx * 2.3);
    float moss = smoothstep(0.55, 0.9, n.y) * smoothstep(0.42, 0.68, fbm(p.xz * 1.7 + 3.0));
    alb = mix(alb, vec3(0.14, 0.30, 0.09), moss * 0.85);
    vec3 col = shade(alb, n, p, 0.05, 12.0, 0.0);
    col *= mix(0.45, 1.0, smoothstep(0.0, 0.9, p.y + FLOOR_K * p.z));
    frag = vec4(fog(col, p), 1.0);
}
"""

PLANT_VS = """
layout(location = 0) in vec2 aSeg;      // along the blade 0..1, side -1/+1
layout(location = 1) in vec4 iBase;     // root xyz, facing
layout(location = 2) in vec4 iShape;    // height, width, lean, kind (0 ribbon, 1 leaf)
layout(location = 3) in vec4 iCol;      // rgb, phase
uniform mat4 uVP;
out vec3 vWorld, vNor, vCol;
out vec2 vSeg;
out float vKind;
void main() {
    float s = aSeg.x, side = aSeg.y;
    float h = iShape.x, wid = iShape.y, lean = iShape.z, kind = iShape.w;
    float yaw = iBase.w;
    float twist = kind < 0.5 ? (iCol.w - 3.14) * 0.22 * s : 0.0;
    vec3 d = vec3(cos(yaw), 0.0, sin(yaw));
    vec3 t = vec3(-sin(yaw + twist), 0.0, cos(yaw + twist));
    float sway = sin(uTime * 0.55 + iCol.w + iBase.x * 0.22) * 0.7
               + sin(uTime * 1.21 + iCol.w * 1.7 + iBase.z * 0.3) * 0.3;
    float flex = kind < 0.5 ? 1.0 : 0.3;
    vec3 c = iBase.xyz + vec3(0.0, h * s, 0.0);
    c += d * (lean * h * s * s);
    c.y -= abs(lean) * h * s * s * 0.4;
    c += vec3(1.0, 0.0, 0.3) * (sway * flex * 0.055 * h * pow(s, 1.6));
    float w;
    if (kind < 0.5) w = wid * (1.0 - 0.75 * s * s);
    else w = s < 0.22 ? wid * 0.1 : wid * pow(sin(3.14159 * (s - 0.22) / 0.78), 0.7);
    vec3 p = c + t * (side * 0.5 * w);
    vec3 tangent = normalize(vec3(0.0, h * (1.0 - 0.8 * abs(lean) * s), 0.0) + d * (2.0 * lean * h * s));
    vWorld = p;
    vNor = normalize(cross(t, tangent));
    vCol = iCol.rgb;
    vSeg = vec2(s, side);
    vKind = kind;
    gl_Position = uVP * vec4(p, 1.0);
}
"""

PLANT_FS = """
in vec3 vWorld, vNor, vCol;
in vec2 vSeg;
in float vKind;
out vec4 frag;
void main() {
    vec3 n = normalize(vNor);
    if (!gl_FrontFacing) n = -n;
    float s = vSeg.x;
    vec3 alb = vCol * mix(0.42, 1.12, smoothstep(0.0, 0.85, s));
    if (vKind > 0.5) {
        float rib = smoothstep(0.12, 0.0, abs(vSeg.y));
        alb *= 0.9 + 0.12 * sin(s * 34.0 - abs(vSeg.y) * 7.0) + rib * 0.3;
    } else {
        alb *= 0.93 + 0.1 * smoothstep(0.3, 0.0, abs(vSeg.y));
    }
    vec3 col = shade(alb, n, vWorld, 0.18, 26.0, 0.45);
    col *= mix(0.5, 1.0, smoothstep(0.0, 0.7, vWorld.y + FLOOR_K * vWorld.z));
    frag = vec4(fog(col, vWorld), 1.0);
}
"""

FISH_VS = """
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNor;
layout(location = 2) in vec3 aUv;       // body: along, height (-1..1), part; fins: along, out, part
layout(location = 3) in vec4 iPos;      // xyz, size
layout(location = 4) in vec4 iRot;      // heading, pitch, roll, bend
layout(location = 5) in vec4 iAnim;     // tail phase, wave size, colour seed, fin phase
uniform mat4 uVP;
uniform float uWaveK;
uniform int uMesh;          // 1: a photo-textured mesh; aUv is its vertex colour
out vec3 vWorld, vNor, vLocal, vUv, vLocalN;
out float vSeed;
void main() {
    vec3 p = aPos, n = aNor;
    float part = uMesh == 1 ? 0.0 : aUv.z;
    if (part > 1.5 && part < 3.5) {         // pectoral fins row
        float side = part < 2.5 ? 1.0 : -1.0;
        float f = aUv.y * (0.6 + 0.4 * sin(iAnim.w));
        p.z += side * f * 0.05;
        p.x += f * 0.02 * cos(iAnim.w);
    }
    if (part > 3.5) p.y -= aUv.y * aUv.y * 0.03 * sin(iAnim.w * 0.5 + aUv.y * 2.0);
    float w = clamp(0.5 - p.x, 0.0, 1.5);
    float env = 0.07 + w * w * 0.85;
    float ph = iAnim.x - w * uWaveK;
    float amp = iAnim.y;
    float off = amp * env * sin(ph) + iRot.w * w * w * 0.35;
    float slope = amp * (1.7 * w * sin(ph) - env * uWaveK * cos(ph)) + iRot.w * w * 0.7;
    p.z += off;
    float a = atan(slope);       // dz/dw; dz/dx = -slope
    float ca = cos(a), sa = sin(a);
    n = vec3(n.x * ca + n.z * sa, n.y, -n.x * sa + n.z * ca);
    float cy = cos(iRot.x), sy = sin(iRot.x);
    float cp = cos(iRot.y), sp = sin(iRot.y);
    float cr = cos(iRot.z), sr = sin(iRot.z);
    mat3 Ry = mat3(cy, 0.0, -sy,  0.0, 1.0, 0.0,  sy, 0.0, cy);
    mat3 Rz = mat3(cp, sp, 0.0,  -sp, cp, 0.0,  0.0, 0.0, 1.0);
    mat3 Rx = mat3(1.0, 0.0, 0.0,  0.0, cr, sr,  0.0, -sr, cr);
    mat3 R = Ry * Rz * Rx;
    vec3 wp = R * (p * iPos.w) + iPos.xyz;
    vWorld = wp;
    vNor = R * n;
    vLocal = aPos;
    vLocalN = aNor;
    vUv = aUv;
    vSeed = iAnim.z;
    gl_Position = uVP * vec4(wp, 1.0);
}
"""

FISH_FS = """
in vec3 vWorld, vNor, vLocal, vUv, vLocalN;
in float vSeed;
out vec4 frag;
uniform int uMesh;
uniform sampler2D uPhoto;
uniform vec4 uBox;          // the part of the mesh the photo spans: x0, x1, y0, y1
uniform int uSpecies;
uniform int uPass;          // 0 body, 1 fins
uniform vec3 uEye;          // local x, y, radius
uniform vec3 uIris;

float band(float u, float c, float w) { return smoothstep(w, w * 0.45, abs(u - c)); }

vec3 bodyColor(float u, float v, float fres, out float spec) {
    vec3 c;
    spec = 0.5;
    if (uSpecies == 0) {            // neon tetra
        vec3 back = vec3(0.30, 0.31, 0.21);
        vec3 belly = vec3(0.84, 0.86, 0.87);
        vec3 red = vec3(0.88, 0.10, 0.07);
        vec3 blue = mix(vec3(0.04, 0.38, 1.0), vec3(0.18, 1.0, 0.85), fres);
        c = mix(belly, back, smoothstep(0.30, 0.65, v));
        float redA = smoothstep(0.36, 0.50, u) * (1.0 - smoothstep(0.93, 1.0, u))
                   * smoothstep(0.10, -0.05, v) * smoothstep(-0.97, -0.55, v);
        c = mix(c, red, redA);
        float stripe = smoothstep(0.0, 0.08, v) * smoothstep(0.50, 0.38, v)
                     * smoothstep(0.09, 0.17, u) * smoothstep(0.84, 0.70, u);
        c = mix(c, blue * 1.35, stripe);
        spec = 0.55 + stripe * 0.8;
    } else if (uSpecies == 1) {     // harlequin rasbora
        vec3 base = mix(vec3(0.96, 0.60, 0.46), vec3(0.72, 0.38, 0.28), smoothstep(0.1, 0.8, v));
        base = mix(base, vec3(0.90, 0.88, 0.86), smoothstep(-0.3, -0.85, v) * 0.6);
        float t = (u - 0.44) / 0.54;
        float wedge = 0.0;
        if (t > 0.0 && t < 1.0)
            wedge = smoothstep(0.04, -0.03, abs(v - 0.06 * (1.0 - t)) - 0.6 * (1.0 - t));
        c = mix(base, vec3(0.06, 0.05, 0.08), wedge * smoothstep(0.0, 0.06, t));
        spec = 0.5;
    } else if (uSpecies == 2) {     // dwarf gourami
        vec3 orange = vec3(0.93, 0.33, 0.10);
        vec3 blue = mix(vec3(0.12, 0.50, 0.95), vec3(0.30, 0.85, 0.90), fres);
        float s = u * 9.0 - v * 0.9 + (noise(vec2(u * 12.0, v * 3.0)) - 0.5) * 0.35;
        float stripe = smoothstep(0.22, 0.12, abs(fract(s) - 0.5));
        c = mix(orange, blue, stripe * 0.9 * smoothstep(0.06, 0.16, u));
        c = mix(c, blue * 0.9, smoothstep(0.36, 0.2, u) * smoothstep(0.05, -0.45, v));
        c *= mix(1.0, 0.65, smoothstep(0.5, 1.0, v));
        spec = 0.55;
    } else if (uSpecies == 3) {     // angelfish
        vec3 silver = mix(vec3(0.80, 0.82, 0.80), vec3(0.86, 0.76, 0.52),
                          smoothstep(0.3, 0.9, v) * smoothstep(0.5, 0.1, u));
        float n = (noise(vec2(u * 9.0, v * 4.0)) - 0.5) * 0.06;
        float b = max(max(band(u, 0.17 + n, 0.035), band(u, 0.43 + n, 0.055)),
                      max(band(u, 0.70 + n, 0.045), band(u, 0.97, 0.03) * 0.8));
        c = mix(silver, vec3(0.04, 0.04, 0.05), b * 0.92);
        spec = 0.9 * (1.0 - b * 0.7);
    } else {                        // panda cory
        vec3 base = mix(vec3(0.62, 0.56, 0.51), vec3(0.80, 0.76, 0.71), smoothstep(0.1, -0.6, v));
        float patch1 = smoothstep(0.075, 0.05, length(vec2(u - 0.14, (v - 0.35) * 0.3)));
        float patch2 = smoothstep(0.11, 0.07, length(vec2((u - 0.42) * 0.9, (v - 1.0) * 0.28)));
        float patch3 = smoothstep(0.07, 0.04, length(vec2(u - 0.95, v * 0.11)));
        c = mix(base, vec3(0.06, 0.06, 0.07), max(max(patch1, patch2), patch3));
        spec = 0.4;
    }
    return c * (0.9 + 0.2 * vSeed);
}

vec4 finColor(float along, float out_, float part) {
    float u = 0.5 - vLocal.x;
    float rays = 0.82 + 0.18 * smoothstep(0.25, 0.75, abs(fract(along * 16.0) - 0.5) * 2.0);
    vec3 c;
    float a;
    if (uSpecies == 0) { c = vec3(0.85, 0.86, 0.82); a = 0.3; }
    else if (uSpecies == 1) { c = vec3(0.98, 0.62, 0.42); a = 0.45; }
    else if (uSpecies == 2) {
        float spots = smoothstep(0.55, 0.7, noise(vLocal.xy * 30.0));
        c = mix(vec3(0.90, 0.28, 0.12), vec3(0.25, 0.62, 0.95), max(spots, smoothstep(0.75, 0.95, out_)));
        a = 0.62;
        if (part > 3.5) { c = vec3(0.98, 0.62, 0.30); a = 0.9; }
    } else if (uSpecies == 3) {
        float b = max(max(band(u, 0.17, 0.035), band(u, 0.43, 0.055)),
                      max(band(u, 0.70, 0.045), band(u, 0.97, 0.03)));
        c = mix(vec3(0.82, 0.84, 0.82), vec3(0.05), b * 0.9);
        a = mix(0.55, 0.85, b);
        rays = 0.7 + 0.3 * rays;
    } else {
        c = vec3(0.92, 0.90, 0.86);
        a = 0.32;
        c *= 1.0 - 0.6 * step(0.7, noise(vLocal.xy * 30.0));
    }
    a *= mix(1.0, 0.55, out_) * (1.0 - smoothstep(0.9, 1.0, out_) * 0.6);
    return vec4(c * rays, a);
}

void main() {
    vec3 n = normalize(vNor);
    if (!gl_FrontFacing) n = -n;
    vec3 view = normalize(uCam - vWorld);
    float fres = pow(1.0 - abs(dot(n, view)), 2.0);
    vec3 col;
    float alpha = 1.0;
    if (uMesh == 1) {
        // The side photo, projected straight across onto both flanks; the mesh's own
        // colours where the surface turns away from the side (face, back, belly)
        vec2 uv = vec2((vLocal.x - uBox.x) / (uBox.y - uBox.x), (uBox.w - vLocal.y) / (uBox.w - uBox.z));
        vec4 ph = texture(uPhoto, uv);
        float side = smoothstep(0.45, 0.8, abs(normalize(vLocalN).z)) * ph.a;
        vec3 alb = mix(vUv, ph.rgb, side) * (0.93 + 0.14 * vSeed);
        // The photo has its own shading baked in, so light it more gently
        vec3 lit = shade(alb, n, vWorld, 0.35, 40.0, 0.0);
        col = mix(lit, alb * (0.55 + 0.6 * depthLight(vWorld.y)), 0.45);
        col += alb * fres * 0.2 * depthLight(vWorld.y);
    } else if (uPass == 0) {
        float spec;
        vec3 alb = bodyColor(vUv.x, vUv.y, fres, spec);
        vec2 sq = vec2(vUv.x * 70.0, vUv.y * 16.0);
        sq.x += step(1.0, mod(floor(sq.y), 2.0)) * 0.5;
        vec2 sf = fract(sq) - vec2(0.5, 0.3);
        float scale = smoothstep(0.55, 0.2, length(sf * vec2(1.0, 1.4)));
        alb *= 0.96 + 0.04 * scale;
        float shin = 40.0;
        float d = length(vLocal.xy - uEye.xy);
        if (d < uEye.z) {
            float r = d / uEye.z;
            alb = r < 0.55 ? vec3(0.01) : mix(uIris, vec3(0.05), smoothstep(0.85, 1.0, r));
            spec = 1.6;
            shin = 90.0;
        }
        col = shade(alb, n, vWorld, spec * (0.6 + 0.4 * scale), shin, 0.0);
        col += alb * fres * 0.25 * depthLight(vWorld.y);
    } else {
        vec4 fc = finColor(vUv.x, vUv.y, vUv.z);
        col = shade(fc.rgb, n, vWorld, 0.25, 30.0, 0.6);
        alpha = fc.a;
    }
    col = fog(col, vWorld);
    float f = 1.0 - exp(-waterDist(vWorld) * 0.042);
    frag = vec4(col, alpha * mix(1.0, 0.6, f * float(uPass)));
}
"""

SPRITE_VS = """
layout(location = 0) in vec2 aCorner;
layout(location = 1) in vec4 iPos;      // xyz, radius
uniform mat4 uVP;
uniform vec3 uRight, uUp;
out vec2 vUv;
out vec3 vWorld;
void main() {
    vec3 p = iPos.xyz + (uRight * aCorner.x + uUp * aCorner.y) * iPos.w;
    vUv = aCorner;
    vWorld = iPos.xyz;
    gl_Position = uVP * vec4(p, 1.0);
}
"""

SPRITE_FS = """
in vec2 vUv;
in vec3 vWorld;
out vec4 frag;
uniform int uKind;          // 0 bubble, 1 floating speck
void main() {
    float r = length(vUv);
    if (r > 1.0) discard;
    float f = 1.0 - exp(-waterDist(vWorld) * 0.042);
    vec3 tint = mix(vec3(0.85, 0.97, 1.0), waterCol(vWorld.y) * 2.0, f * 0.7);
    float a;
    if (uKind == 0) {
        float rim = smoothstep(0.62, 0.98, r) * (1.0 - smoothstep(0.96, 1.0, r));
        float hl = exp(-dot(vUv - vec2(-0.35, 0.42), vUv - vec2(-0.35, 0.42)) * 45.0);
        a = rim * 0.6 + hl * 0.95 + 0.05;
    } else {
        a = (1.0 - r * r) * 0.35;
        tint = mix(vec3(0.75, 0.80, 0.70), tint, 0.5);
    }
    frag = vec4(tint * depthLight(vWorld.y), a * (1.0 - f * 0.8));
}
"""

POST_FS = """
in vec2 vNdc;
out vec4 frag;
uniform sampler2D uImage;
uniform float uAspect;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    vec3 c = texture(uImage, uv).rgb;
    c *= 1.18;                                          // exposure
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    c = mix(vec3(l), c, 1.12);                          // a little more colour
    c = c / (1.0 + max(c - 0.8, 0.0) * 0.9);            // soft highlights
    c = smoothstep(0.0, 1.0, c) * 0.35 + c * 0.65;      // gentle contrast
    vec2 q = vNdc * vec2(uAspect / 1.78, 1.0);
    c *= 1.0 - 0.4 * pow(clamp(length(q * vec2(0.82, 1.0)) / 1.25, 0.0, 1.0), 2.2);
    c += (hash12(gl_FragCoord.xy) - 0.5) / 255.0;      // no banding
    frag = vec4(c, 1.0);
}
"""


# ---------------------------------------------------------------- species

def _interp(points, u):
    """Catmull-Rom through (u, a, b, c...) control points."""
    if u <= points[0][0]:
        return points[0][1:]
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        if u <= p2[0]:
            p0 = points[max(i - 1, 0)]
            p3 = points[min(i + 2, len(points) - 1)]
            t = (u - p1[0]) / (p2[0] - p1[0])
            t2, t3 = t * t, t * t * t
            return tuple(0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2
                                + (-a + 3 * b - 3 * c + d) * t3)
                         for a, b, c, d in zip(p0[1:], p1[1:], p2[1:], p3[1:]))
    return points[-1][1:]


class Species:
    def __init__(self, index, name, count, length, speed, burst, zone, school, wave_k, amp,
                 beat, turn, profile, eye, iris, fins, mesh=None):
        self.index = index
        self.mesh = mesh                # a Reef Glass fish (NAME.mesh + NAME.png in DATA_DIR)
        self.name = name
        self.count = count
        self.length = length            # body length in tank units (1 unit is ~4 cm)
        self.speed = speed
        self.burst = burst
        self.zone = zone                # (lowest above the gravel, highest)
        self.school = school
        self.wave_k = wave_k
        self.amp = amp
        self.beat = beat
        self.turn = turn                # radians a second
        self.profile = profile          # (u, half height, half width, centre)
        self.eye = eye                  # u, height fraction, radius
        self.iris = iris
        self.fins = fins

    def shape(self, u):
        if not self.profile:
            return 0.1, 0.05, 0.0
        h, w, c = _interp(self.profile, u)
        return max(h, 0.002), max(w, 0.002), c


SPECIES = [
    Species(0, "neon tetra", 5, 0.95, 1.05, 2.8, (1.8, 7.5), True, 4.6, 0.075, 2.2, 3.2,
            [(0, 0.004, 0.003, 0), (0.02, 0.045, 0.03, 0), (0.1, 0.095, 0.058, 0.0),
             (0.32, 0.125, 0.066, 0.0), (0.58, 0.1, 0.052, -0.005), (0.8, 0.055, 0.03, 0),
             (0.93, 0.04, 0.018, 0), (1.0, 0.045, 0.01, 0)],
            (0.085, 0.3, 0.032), (0.55, 0.65, 0.75),
            dict(tail=(0.26, 0.15, 0.6, 0.0), dorsal=(0.38, 0.52, 0.14, 0.5, 0.25),
                 anal=(0.55, 0.8, 0.09, 0.4, 0.2), pect=(0.2, 0.12))),
    Species(1, "harlequin rasbora", 3, 1.05, 0.9, 2.4, (2.5, 8.5), True, 4.4, 0.07, 2.0, 3.0,
            [(0, 0.004, 0.003, 0), (0.02, 0.05, 0.03, 0), (0.1, 0.11, 0.055, 0.0),
             (0.35, 0.15, 0.06, 0.0), (0.6, 0.12, 0.05, -0.01), (0.82, 0.06, 0.03, 0),
             (0.94, 0.045, 0.016, 0), (1.0, 0.05, 0.01, 0)],
            (0.08, 0.3, 0.03), (0.75, 0.55, 0.35),
            dict(tail=(0.28, 0.17, 0.65, 0.0), dorsal=(0.42, 0.56, 0.15, 0.5, 0.25),
                 anal=(0.55, 0.78, 0.1, 0.4, 0.2), pect=(0.2, 0.12))),
    Species(2, "dwarf gourami", 1, 1.55, 0.55, 1.6, (2.5, 8.0), False, 3.8, 0.05, 1.5, 1.6,
            [(0, 0.005, 0.004, 0), (0.03, 0.08, 0.04, 0), (0.15, 0.2, 0.06, 0.0),
             (0.4, 0.24, 0.07, 0.0), (0.65, 0.18, 0.055, 0.0), (0.85, 0.09, 0.03, 0),
             (1.0, 0.065, 0.012, 0)],
            (0.085, 0.3, 0.03), (0.85, 0.35, 0.15),
            dict(tail=(0.3, 0.2, 0.0, 0.35), dorsal=(0.3, 0.92, 0.13, 0.35, 0.7),
                 anal=(0.28, 0.95, 0.15, 0.3, 0.75), pect=(0.22, 0.14), feelers=(0.3, 0.75))),
    Species(3, "angelfish", 1, 2.4, 0.42, 1.2, (3.2, 7.8), False, 3.2, 0.035, 1.1, 1.0,
            [(0, 0.008, 0.005, 0), (0.04, 0.12, 0.03, 0.02), (0.16, 0.28, 0.045, 0.02),
             (0.36, 0.34, 0.05, 0.0), (0.58, 0.29, 0.042, 0.0), (0.78, 0.17, 0.028, 0),
             (0.92, 0.08, 0.016, 0), (1.0, 0.07, 0.01, 0)],
            (0.1, 0.32, 0.035), (0.85, 0.15, 0.08),
            dict(tail=(0.42, 0.26, 0.3, 0.1), dorsal=(0.22, 0.72, 0.62, 0.75, 0.88),
                 anal=(0.3, 0.74, 0.6, 0.75, 0.88), pect=(0.22, 0.1), feelers=(0.3, 0.9))),
    Species(4, "panda cory", 1, 1.2, 0.45, 1.4, (0.18, 0.5), False, 4.0, 0.05, 1.8, 1.8,
            [(0, 0.006, 0.006, 0.0), (0.04, 0.08, 0.07, 0.01), (0.18, 0.16, 0.12, 0.03),
             (0.4, 0.18, 0.12, 0.04), (0.68, 0.12, 0.075, 0.02), (0.88, 0.055, 0.035, 0.0),
             (1.0, 0.05, 0.012, 0)],
            (0.14, 0.42, 0.03), (0.2, 0.2, 0.22),
            dict(tail=(0.26, 0.16, 0.5, 0.0), dorsal=(0.3, 0.42, 0.24, 0.3, 0.2),
                 anal=(0.68, 0.82, 0.08, 0.3, 0.3), pect=(0.18, 0.18))),
    # Reef Glass's saltwater fish: name, count, length, speed, dash, zone, school,
    # wave number, wave size, beats a second, turn rate
    Species(5, "clownfish", 2, 1.85, 0.6, 1.6, (1.5, 7.0), False, 3.6, 0.06, 2.6, 2.4,
            None, None, None, None, mesh="clown"),
    Species(6, "blue tang", 1, 2.7, 0.9, 2.2, (2.5, 8.5), False, 3.0, 0.04, 1.8, 1.6,
            None, None, None, None, mesh="bluetang"),
    Species(7, "yellow tang", 2, 2.35, 0.8, 2.0, (2.5, 8.5), False, 3.0, 0.04, 2.0, 1.7,
            None, None, None, None, mesh="yellowtang"),
    Species(8, "blue damselfish", 6, 1.05, 0.75, 2.4, (2.0, 8.0), True, 4.0, 0.06, 3.0, 3.0,
            None, None, None, None, mesh="damsel"),
    Species(9, "royal gramma", 2, 1.15, 0.45, 1.6, (1.0, 5.5), False, 4.0, 0.06, 2.6, 2.6,
            None, None, None, None, mesh="gramma"),
    Species(10, "pufferfish", 1, 2.4, 0.35, 0.9, (2.0, 7.5), False, 2.6, 0.02, 2.2, 1.2,
            None, None, None, None, mesh="puffer"),
]




def _mesh_files(sp):
    if not re.fullmatch(r"[a-z0-9_]+", sp.mesh):
        raise ValueError(f"{sp.mesh!r} isn't a fish name")
    return (os.path.join(DATA_DIR, sp.mesh + ".mesh"), os.path.join(DATA_DIR, sp.mesh + ".png"))


def active_species():
    """The built-in fish, plus the Reef Glass ones whose files are installed."""
    return [sp for sp in SPECIES if not sp.mesh or all(os.path.exists(f) for f in _mesh_files(sp))]


def load_mesh(sp):
    """A prepared Reef Glass fish: (MeshData, photo box)."""
    path, _png = _mesh_files(sp)
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] != b"FTM1":
        raise ValueError(f"{path}: not a fish mesh")
    nv, ni = struct.unpack_from("<II", raw, 4)
    box = struct.unpack_from("<4f", raw, 12)
    off = 28
    m = MeshData()
    m.stride = 9
    m.verts = array("f", raw[off:off + nv * 36])
    m.idx = array("I", raw[off + nv * 36:off + nv * 36 + ni * 4])
    return m, box


# ---------------------------------------------------------------- meshes

class MeshData:
    def __init__(self):
        self.verts = []         # flat floats
        self.idx = []
        self.stride = 0

    def nverts(self):
        return len(self.verts) // self.stride


def _grid(rows, wrap):
    """Indices for a rows x cols grid, optionally wrapping the columns."""
    out = []
    for r in range(len(rows) - 1):
        n = len(rows[r])
        cols = n if wrap else n - 1
        for c in range(cols):
            c2 = (c + 1) % n
            a, b = rows[r][c], rows[r][c2]
            d, e = rows[r + 1][c], rows[r + 1][c2]
            out += [a, d, b, b, d, e]
    return out


def _smooth_normals(pos, idx):
    acc = [[0.0, 0.0, 0.0] for _ in pos]
    for i in range(0, len(idx), 3):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        n = _cross(_sub(pos[b], pos[a]), _sub(pos[c], pos[a]))
        for k in (a, b, c):
            acc[k][0] += n[0]
            acc[k][1] += n[1]
            acc[k][2] += n[2]
    return [_norm(n) for n in acc]


def fish_mesh(sp):
    """Body (wrapped tube) then fins; returns (MeshData, body index count)."""
    pos, uvs, idx = [], [], []

    # Body: nose at x = +0.5, tail root at x = -0.5
    rings, around = 30, 18
    rows = []
    for i in range(rings + 1):
        t = i / rings
        u = 0.5 - 0.5 * math.cos(math.pi * t)      # more rings at the nose and the tail
        h, w, c = sp.shape(u)
        row = []
        for j in range(around):
            th = TAU * j / around
            row.append(len(pos))
            # Flatten the underside a little, fuller above
            sy = math.sin(th)
            pos.append((0.5 - u, c + h * sy, w * math.cos(th)))
            uvs.append((u, sy, 0.0))
        rows.append(row)
    idx += _grid(rows, True)
    nor = _smooth_normals(pos, idx)
    body_count = len(idx)

    fin_pos, fin_uv, fin_idx = [], [], []

    def fin(root, edge, part, radial=5):
        grid = []
        n = len(root)
        for i in range(n):
            row = []
            for j in range(radial + 1):
                t = j / radial
                p = tuple(root[i][k] + (edge[i][k] - root[i][k]) * t for k in range(3))
                row.append(len(pos) + len(fin_pos))
                fin_pos.append(p)
                fin_uv.append((i / (n - 1), t, part))
            grid.append(row)
        fin_idx.extend(_grid(grid, False))

    def top(u):
        h, w, c = sp.shape(u)
        return c + h * 0.9

    def bottom(u):
        h, w, c = sp.shape(u)
        return c - h * 0.9

    f = sp.fins
    # Tail: length, half spread, fork depth, roundness
    tl, spread, fork, rnd = f["tail"]
    h1, w1, c1 = sp.shape(0.985)
    root, edge = [], []
    n = 13
    for i in range(n):
        s = i / (n - 1)
        k = 2 * s - 1
        root.append((-0.485, c1 + h1 * k * 0.9, 0.0))
        length = tl * (1 - fork * (1 - abs(k)) ** 1.3 + rnd * (1 - k * k))
        edge.append((-0.485 - length, c1 + spread * k * (0.85 + 0.15 * rnd), 0.0))
    fin(root, edge, 1.0)

    # Dorsal and anal: root span (u0, u1), height, sweep back, where the tip is
    for key, sign in (("dorsal", 1), ("anal", -1)):
        u0, u1, height, sweep, peak = f[key]
        root, edge = [], []
        n = 13
        for i in range(n):
            s = i / (n - 1)
            u = u0 + (u1 - u0) * s
            y = top(u) if sign > 0 else bottom(u)
            if s < peak:
                prof = math.sin(math.pi / 2 * s / peak) ** 0.8
            else:
                prof = 1 - 0.85 * ((s - peak) / (1 - peak)) ** 0.9
            prof = 0.06 + 0.94 * prof
            root.append((0.5 - u, y, 0.0))
            edge.append((0.5 - u - sweep * height * prof * (0.3 + s), y + sign * height * prof, 0.0))
        fin(root, edge, 1.0)

    # Pectoral fins, one each side
    pu, plen = f["pect"]
    h, w, c = sp.shape(pu)
    for part, side in ((2.0, 1.0), (3.0, -1.0)):
        root, edge = [], []
        n = 6
        for i in range(n):
            s = i / (n - 1)
            y = c - h * 0.45 + h * 0.35 * s
            root.append((0.5 - pu - 0.01 * s, y, side * w * 0.92))
            r = plen * (0.55 + 0.45 * math.sin(math.pi * s))
            edge.append((0.5 - pu - r, y - 0.02 + 0.02 * s, side * (w * 0.92 + r * 0.45)))
        fin(root, edge, part, radial=4)

    # Long feelers (gourami ventral fins) or trailing ventral fins (angelfish)
    if "feelers" in f:
        fu, flen = f["feelers"]
        h, w, c = sp.shape(fu)
        for side in (1.0, -1.0):
            y = c - h * 0.85
            root = [(0.5 - fu + 0.008, y, side * 0.012), (0.5 - fu - 0.008, y, side * 0.012)]
            edge = [(0.5 - fu - flen * 0.55 + 0.006, y - flen * 0.55, side * 0.03),
                    (0.5 - fu - flen * 0.55 - 0.006, y - flen * 0.55, side * 0.03)]
            fin(root, edge, 4.0, radial=10)

    fin_nor = _smooth_normals(pos + fin_pos, fin_idx)[len(pos):]

    m = MeshData()
    m.stride = 9
    for p, nn, uv in zip(pos + fin_pos, nor + fin_nor, uvs + fin_uv):
        m.verts += [p[0], p[1], p[2], nn[0], nn[1], nn[2], uv[0], uv[1], uv[2]]
    m.idx = idx + fin_idx
    return m, body_count


def _icosphere(level):
    t = (1 + math.sqrt(5)) / 2
    v = [_norm(p) for p in [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
                            (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
         (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
         (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    for _ in range(level):
        cache = {}

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                cache[key] = len(v)
                v.append(_norm(tuple((v[a][k] + v[b][k]) / 2 for k in range(3))))
            return cache[key]

        nf = []
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return v, f


def rock_mesh(rng, rocks):
    """Stones cut by random planes (flat faces and sharp edges), sunk into the gravel."""
    base_v, base_f = _icosphere(3)
    pos, idx = [], []
    for (x, z, size, flat) in rocks:
        cuts = []
        for _ in range(rng.randint(7, 11)):
            d = _norm((rng.gauss(0, 1), rng.gauss(0, 1) * 0.8 + 0.25, rng.gauss(0, 1)))
            cuts.append((d, rng.uniform(0.62, 0.92)))
        yaw = rng.uniform(0, TAU)
        cy, sy = math.cos(yaw), math.sin(yaw)
        sx, sz = size * rng.uniform(0.9, 1.3), size * rng.uniform(0.7, 1.0)
        syy = size * flat
        start = len(pos)
        for n in base_v:
            r = 1.0
            for d, o in cuts:
                k = _dot(n, d)
                if k > 1e-3:
                    r = min(r, o / k)
            r *= 1 + 0.04 * math.sin(n[0] * 7 + n[1] * 5) * math.cos(n[2] * 6)
            px, py, pz = n[0] * r * sx, n[1] * r * syy, n[2] * r * sz
            wx, wz = px * cy - pz * sy, px * sy + pz * cy
            pos.append((x + wx, floor_y(z + wz) + py + syy * 0.3, z + wz))
        idx += [start + i for tri in base_f for i in tri]
    nor = _smooth_normals(pos, idx)
    m = MeshData()
    m.stride = 6
    for p, n in zip(pos, nor):
        m.verts += [p[0], p[1], p[2], n[0], n[1], n[2]]
    m.idx = idx
    return m


# ---------------------------------------------------------------- simulation

class Fish:
    def __init__(self, sp, tank, rng):
        self.sp = sp
        self.size = sp.length * rng.uniform(0.85, 1.12)
        self.pos = list(tank.random_point(sp, rng))
        self.yaw = rng.uniform(0, TAU)
        self.pitch = 0.0
        self.roll = 0.0
        self.speed = sp.speed * rng.uniform(0.6, 1.0)
        self.want = self.speed
        self.phase = rng.uniform(0, TAU)
        self.fin = rng.uniform(0, TAU)
        self.bend = 0.0
        self.seed = rng.random()
        self.target = tank.random_point(sp, rng)
        self.timer = rng.uniform(2, 8)
        self.offset = (rng.uniform(-1, 1), rng.uniform(-0.6, 0.6), rng.uniform(-1, 1))
        self.rest = 0.0

    def forward(self):
        cp = math.cos(self.pitch)
        return (math.cos(self.yaw) * cp, math.sin(self.pitch), -math.sin(self.yaw) * cp)


def _wrap(a):
    return (a + math.pi) % TAU - math.pi


class Tank:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.time = self.rng.uniform(0, 1000)
        self.width = self.height = 0
        self.aspect = 16 / 9
        self.built = False
        self.gl = None

    # ---- layout

    def half_width(self, z, aspect=None):
        a = aspect or self.aspect
        tan_h = math.tan(math.radians(FOVY) / 2) * a
        return (CAM[2] - z) * tan_h

    def random_point(self, sp, rng):
        z = rng.uniform(-8.5, -0.8)
        hw = self.half_width(z) * 0.82
        lo, hi = sp.zone
        y = rng.uniform(floor_y(z) + lo, min(floor_y(z) + hi, SURF - 1.2)) if sp.index == 4 else \
            rng.uniform(max(floor_y(z) + lo, 1.0), min(hi, SURF - 1.2))
        return (rng.uniform(-hw, hw), y, z)

    def build(self):
        rng = self.rng
        self.built = True
        hw_mid = self.half_width(-6)

        # Rocks: one big stone off centre, a medium one, a few small ones
        side = rng.choice((-1, 1))
        rocks = [(side * hw_mid * rng.uniform(0.25, 0.45), rng.uniform(-7.5, -5.5), rng.uniform(2.2, 2.8), 0.75),
                 (-side * hw_mid * rng.uniform(0.3, 0.55), rng.uniform(-6.5, -4.0), rng.uniform(1.4, 1.9), 0.65)]
        for _ in range(rng.randint(3, 5)):
            bx, bz = rocks[rng.randint(0, 1)][:2]
            rocks.append((bx + rng.uniform(-2.6, 2.6), bz + rng.uniform(-1.0, 2.2), rng.uniform(0.35, 0.8), 0.6))
        self.rocks = rocks
        self.obstacles = [(x, floor_y(z) + size * 0.4, z, size * 1.05) for x, z, size, _f in rocks]

        def blocked(x, z, pad=0.0):
            for rx, rz, size, _f in rocks:
                if (x - rx) ** 2 + (z - rz) ** 2 < (size * 0.9 + pad) ** 2:
                    return True
            return False

        plants = []

        def blade(x, z, yaw, height, width, lean, kind, col):
            plants.append((x, floor_y(z) - 0.05, z, yaw, height, width, lean, kind,
                           col[0], col[1], col[2], rng.uniform(0, TAU)))

        def vary(c, amount=0.12):
            k = rng.uniform(1 - amount, 1 + amount)
            return (c[0] * k * rng.uniform(0.92, 1.08), c[1] * k, c[2] * k * rng.uniform(0.9, 1.1))

        # Background: clumps of ribbons reaching for the surface, with gaps, and red stems
        z = -13.4
        while z < -8.4:
            hw = self.half_width(z) * 1.05
            x = -hw + rng.uniform(0, 1)
            while x < hw:
                cx, cz = x, z + rng.uniform(-0.5, 0.5)
                kind = rng.random()
                green = rng.choice([(0.20, 0.48, 0.12), (0.27, 0.55, 0.14), (0.16, 0.40, 0.13), (0.33, 0.58, 0.16)])
                tall = rng.uniform(0.35, 1.0)
                for _ in range(rng.randint(4, 11)):
                    bx, bz = cx + rng.gauss(0, 0.28), cz + rng.gauss(0, 0.22)
                    top = SURF - floor_y(bz) - rng.uniform(0.3, 2.0)
                    if kind < 0.13:
                        blade(bx, bz, rng.uniform(0, TAU), rng.uniform(2.5, 5.0), rng.uniform(0.13, 0.2),
                              rng.uniform(-0.25, 0.25), 0, vary((0.66, 0.24, 0.14)))
                    else:
                        blade(bx, bz, rng.uniform(0, TAU), top * tall * rng.uniform(0.6, 1.0),
                              rng.uniform(0.10, 0.17), rng.uniform(-0.35, 0.35), 0, vary(green))
                x += rng.uniform(0.6, 1.9)
            z += rng.uniform(1.1, 1.7)

        # Midground: broad-leaved clumps beside the rocks, and one at the front corner
        spots = []
        for rx, rz, size, _f in rocks[:2]:
            ang = rng.uniform(0, TAU)
            spots.append((rx + math.cos(ang) * (size + 0.6), rz + math.sin(ang) * (size * 0.5) - 0.6))
        spots.append((rng.uniform(-hw_mid * 0.7, hw_mid * 0.7), rng.uniform(-8.5, -7.5)))
        corner = rng.choice((-1, 1))
        spots.append((corner * self.half_width(-1.0) * rng.uniform(0.78, 0.92), rng.uniform(-1.6, -0.6)))
        for i, (cx, cz) in enumerate(spots):
            col = (0.62, 0.30, 0.18) if i == 2 and rng.random() < 0.5 else (0.21, 0.50, 0.15)
            for _ in range(rng.randint(10, 16)):
                ang = rng.uniform(0, TAU)
                blade(cx + math.cos(ang) * 0.15, cz + math.sin(ang) * 0.15, ang, rng.uniform(2.0, 3.8),
                      rng.uniform(0.38, 0.58), rng.uniform(0.45, 1.05), 1, vary(col, 0.15))

        # Foreground carpet: grass patches
        patches = [(rng.uniform(-1, 1), rng.uniform(-4.5, 0.6), rng.uniform(1.2, 3.0)) for _ in range(9)]
        for px, pz, pr in patches:
            hw = self.half_width(pz)
            cx = px * hw
            for _ in range(int(pr * pr * 70)):
                ang, rad = rng.uniform(0, TAU), pr * math.sqrt(rng.random())
                bx, bz = cx + math.cos(ang) * rad, pz + math.sin(ang) * rad * 0.6
                if blocked(bx, bz) or bz > 0.9:
                    continue
                blade(bx, bz, rng.uniform(0, TAU), rng.uniform(0.25, 0.75) * (1 - rad / pr * 0.5),
                      rng.uniform(0.025, 0.045), rng.uniform(-0.5, 0.5), 0, vary((0.32, 0.62, 0.18), 0.18))
        self.plants = plants

        # Fish
        self.fish = []
        self.species = active_species()
        for sp in self.species:
            for _ in range(sp.count):
                self.fish.append(Fish(sp, self, rng))
        self.school_targets = {sp.index: self.random_point(sp, rng) for sp in self.species if sp.school}
        self.school_timers = {k: rng.uniform(5, 10) for k in self.school_targets}

        # Bubbles from an air stone behind a rock; specks drifting everywhere
        sx = -side * hw_mid * rng.uniform(0.6, 0.85)
        self.stone = (sx, -8.2)
        self.bubbles = []
        self.bubble_clock = 0.0
        self.specks = []
        for _ in range(220):
            z = rng.uniform(-12, 0.8)
            hw = self.half_width(z)
            self.specks.append([rng.uniform(-hw, hw), rng.uniform(floor_y(z), SURF), z,
                                rng.uniform(0.012, 0.035), rng.uniform(0, TAU)])

    def resize(self, width, height):
        self.width, self.height = max(width, 1), max(height, 1)
        self.aspect = self.width / self.height
        if not self.built:
            self.build()

    # ---- time

    def step(self, dt):
        if not self.built:
            return
        dt = min(max(dt, 0.0), 0.1)
        self.time += dt
        rng = self.rng
        for key in self.school_timers:
            self.school_timers[key] -= dt
            if self.school_timers[key] <= 0:
                sp = SPECIES[key]  # (indices match)
                self.school_targets[key] = self.random_point(sp, rng)
                self.school_timers[key] = rng.uniform(6, 14)
        fish = self.fish
        fwd = [f.forward() for f in fish]
        for i, f in enumerate(fish):
            self.steer(i, f, fish, fwd, dt)
        self.step_bubbles(dt)
        for s in self.specks:
            s[4] += dt * 0.2
            s[0] += math.sin(s[4] + s[2]) * 0.05 * dt + 0.03 * dt
            s[1] += math.cos(s[4] * 1.3 + s[0]) * 0.04 * dt
            hw = self.half_width(s[2])
            if s[0] > hw:
                s[0] = -hw

    def steer(self, i, f, fish, fwd, dt):
        sp = f.sp
        rng = self.rng
        x, y, z = f.pos
        fx, fy, fz = fwd[i]
        sx = sy = sz = 0.0

        # Where it's heading
        f.timer -= dt
        if sp.school:
            t = self.school_targets[sp.index]
            tgt = (t[0] + f.offset[0] * 1.6, t[1] + f.offset[1], t[2] + f.offset[2] * 1.6)
        else:
            if f.timer <= 0 or (abs(f.target[0] - x) + abs(f.target[1] - y) + abs(f.target[2] - z)) < 1.0:
                f.target = self.random_point(sp, rng)
                f.timer = rng.uniform(4, 12)
            tgt = f.target
        dx, dy, dz = tgt[0] - x, tgt[1] - y, tgt[2] - z
        d = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        wt = 0.6 if sp.school else 0.9
        sx, sy, sz = dx / d * wt, dy / d * wt * 0.6, dz / d * wt

        # Neighbours: keep apart from everyone, school with your own kind
        ax = ay = az = cx = cy = cz = 0.0
        nn = 0
        for j, o in enumerate(fish):
            if j == i:
                continue
            ox, oy, oz = o.pos[0] - x, o.pos[1] - y, o.pos[2] - z
            d2 = ox * ox + oy * oy + oz * oz
            room = (f.size + o.size) * 0.55
            if d2 < room * room * 4 and d2 > 1e-6:
                dd = math.sqrt(d2)
                push = (room * 2 - dd) / (room * 2) * 2.2
                sx -= ox / dd * push
                sy -= oy / dd * push
                sz -= oz / dd * push
            if sp.school and o.sp is sp and d2 < 9.0:
                ax += fwd[j][0]
                ay += fwd[j][1]
                az += fwd[j][2]
                cx += ox
                cy += oy
                cz += oz
                nn += 1
        if nn:
            sx += ax / nn * 0.7 + cx / nn * 0.25
            sy += ay / nn * 0.7 + cy / nn * 0.25
            sz += az / nn * 0.7 + cz / nn * 0.25

        # Rocks
        for ox, oy, oz, r in self.obstacles:
            vx, vy, vz = x - ox, y - oy, z - oz
            dd = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
            gap = dd - r - f.size * 0.5
            if gap < 1.2:
                k = (1.2 - gap) * 2.0
                sx += vx / dd * k
                sy += vy / dd * k * 0.6 + 0.2 * k
                sz += vz / dd * k

        # Glass, gravel, surface (soft walls)
        hw = self.half_width(z) * 1.04
        lo = floor_y(z) + sp.zone[0]
        hi = floor_y(z) + sp.zone[1] if sp.index == 4 else min(sp.zone[1], SURF - 1.0)
        m = 1.5

        def wall(v, low, high):
            if v < low + m:
                return (low + m - v) / m * 2.5
            if v > high - m:
                return -(v - (high - m)) / m * 2.5
            return 0.0

        sx += wall(x, -hw, hw)
        sz += wall(z, -9.0, 0.3)
        if sp.index == 4:
            sy += (lo + 0.05 - y) * 3.0
        else:
            sy += wall(y, lo, hi)

        # Turn towards the wanted direction, no faster than the fish can
        wx, wy, wz = fx * 1.5 + sx, fy * 1.5 + sy, fz * 1.5 + sz
        wl = math.sqrt(wx * wx + wy * wy + wz * wz) or 1.0
        want_yaw = math.atan2(-wz, wx)
        max_pitch = 0.2 if sp.index == 4 else 0.32
        want_pitch = max(-max_pitch, min(max_pitch, math.asin(max(-1, min(1, wy / wl)))))
        dyaw = _wrap(want_yaw - f.yaw)
        turn = max(-sp.turn * dt, min(sp.turn * dt, dyaw * min(1.0, 3.0 * dt)))
        f.yaw = _wrap(f.yaw + turn)
        f.pitch += (want_pitch - f.pitch) * min(1.0, 1.5 * dt)
        rate = turn / dt if dt > 0 else 0.0
        f.bend += (max(-1, min(1, rate / sp.turn)) * 0.5 - f.bend) * min(1.0, 6 * dt)
        f.roll += (-f.bend * 0.4 - f.roll) * min(1.0, 3 * dt)

        # Speed: cruise, the odd dash, and for corys short scoots with rests
        if sp.index == 4:
            f.rest -= dt
            if f.rest <= 0:
                f.want = 0.0 if f.want > 0 else sp.speed * rng.uniform(0.6, 1.6)
                f.rest = rng.uniform(1.5, 5.0) if f.want == 0 else rng.uniform(0.6, 2.0)
        elif rng.random() < dt * (0.15 if sp.school else 0.05):
            f.want = sp.burst * rng.uniform(0.7, 1.0)
        elif rng.random() < dt * 0.4:
            f.want = sp.speed * rng.uniform(0.45, 1.15)
        if f.want > sp.speed * 1.2:
            f.want += (sp.speed - f.want) * min(1.0, 1.2 * dt)
        f.speed += (f.want - f.speed) * min(1.0, (3.0 if f.want > f.speed else 1.0) * dt)
        fx, fy, fz = f.forward()
        f.pos[0] += fx * f.speed * dt
        f.pos[1] += fy * f.speed * dt
        f.pos[2] += fz * f.speed * dt
        rel = f.speed / max(sp.speed, 1e-3)
        f.phase += dt * sp.beat * TAU * (0.35 + 0.65 * rel + abs(f.bend) * 0.8)
        f.fin += dt * (5.0 + 3.0 * (1 - min(rel, 1)))

    def step_bubbles(self, dt):
        rng = self.rng
        self.bubble_clock += dt
        while self.bubble_clock > 0.045:
            self.bubble_clock -= 0.045 * rng.uniform(0.4, 1.6)
            x, z = self.stone
            r = rng.uniform(0.025, 0.08) if rng.random() < 0.9 else rng.uniform(0.09, 0.15)
            self.bubbles.append([x + rng.gauss(0, 0.12), floor_y(z) + 0.15, z + rng.gauss(0, 0.12), r,
                                 rng.uniform(0, TAU), rng.uniform(2.2, 3.4) + r * 8])
        alive = []
        for b in self.bubbles:
            b[4] += dt * 9.0
            b[0] += math.sin(b[4]) * 0.25 * dt + 0.06 * dt
            b[2] += math.cos(b[4] * 0.8) * 0.15 * dt
            b[1] += b[5] * dt
            if b[1] < SURF - b[3]:
                alive.append(b)
        self.bubbles = alive[-600:]

    # ---- drawing

    def gl_init(self):
        g = _GL()
        self.gl = g

    def gl_free(self):
        if self.gl:
            self.gl.free()
            self.gl = None

    def render(self, target_fbo):
        if not self.built or not self.gl:
            return
        self.gl.draw(self, target_fbo)


# ---------------------------------------------------------------- GL side

def _compile(vs, fs):
    def stage(kind, src):
        s = GL.glCreateShader(kind)
        GL.glShaderSource(s, src)
        GL.glCompileShader(s)
        if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
            raise RuntimeError(GL.glGetShaderInfoLog(s).decode(errors="replace"))
        return s
    prog = GL.glCreateProgram()
    a, b = stage(GL.GL_VERTEX_SHADER, COMMON + vs), stage(GL.GL_FRAGMENT_SHADER, COMMON + fs)
    GL.glAttachShader(prog, a)
    GL.glAttachShader(prog, b)
    GL.glLinkProgram(prog)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        raise RuntimeError(GL.glGetProgramInfoLog(prog).decode(errors="replace"))
    GL.glDeleteShader(a)
    GL.glDeleteShader(b)
    return prog


def _floats(values):
    return array("f", values).tobytes()


class _Program:
    def __init__(self, vs, fs):
        self.id = _compile(vs, fs)
        self.loc = {}

    def use(self):
        GL.glUseProgram(self.id)

    def u(self, name):
        if name not in self.loc:
            self.loc[name] = GL.glGetUniformLocation(self.id, name)
        return self.loc[name]


class _GL:
    def __init__(self):
        self.programs = {
            "back": _Program(QUAD_VS, BACK_FS),
            "rock": _Program(ROCK_VS, ROCK_FS),
            "plant": _Program(PLANT_VS, PLANT_FS),
            "fish": _Program(FISH_VS, FISH_FS),
            "sprite": _Program(SPRITE_VS, SPRITE_FS),
            "post": _Program(QUAD_VS, POST_FS),
        }
        self.vaos, self.buffers = [], []
        self.quad = self._vao()
        self._buffer(GL.GL_ARRAY_BUFFER, _floats([-1, -1, 1, -1, -1, 1, 1, 1]))
        self._attr(0, 2, 2, 0)
        self.scene_ready = False
        self.fbo = None
        self.fbo_size = (0, 0)
        samples = GL.glGetIntegerv(GL.GL_MAX_SAMPLES)
        self.samples = min(4, int(samples)) if samples else 0

    def _vao(self):
        v = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(v)
        self.vaos.append(v)
        return v

    def _buffer(self, target, data, usage=GL.GL_STATIC_DRAW):
        b = GL.glGenBuffers(1)
        GL.glBindBuffer(target, b)
        GL.glBufferData(target, len(data), data, usage)
        self.buffers.append(b)
        return b

    @staticmethod
    def _attr(loc, size, stride, offset, divisor=0):
        GL.glEnableVertexAttribArray(loc)
        GL.glVertexAttribPointer(loc, size, GL.GL_FLOAT, GL.GL_FALSE, stride * 4, ctypes.c_void_p(offset * 4))
        if divisor:
            GL.glVertexAttribDivisor(loc, divisor)

    def build_scene(self, tank):
        rng = random.Random(tank.rng.random())
        # Rocks
        m = rock_mesh(rng, tank.rocks)
        self.rock_vao = self._vao()
        self._buffer(GL.GL_ARRAY_BUFFER, _floats(m.verts))
        self._attr(0, 3, 6, 0)
        self._attr(1, 3, 6, 3)
        self._buffer(GL.GL_ELEMENT_ARRAY_BUFFER, array("I", m.idx).tobytes())
        self.rock_count = len(m.idx)

        # Plants: one strip, instanced
        segs = 9
        strip = []
        for i in range(segs + 1):
            s = i / segs
            strip += [s, -1.0, s, 1.0]
        self.plant_verts = (segs + 1) * 2
        self.plant_vao = self._vao()
        self._buffer(GL.GL_ARRAY_BUFFER, _floats(strip))
        self._attr(0, 2, 2, 0)
        flat = []
        for p in tank.plants:
            flat += p
        self._buffer(GL.GL_ARRAY_BUFFER, _floats(flat))
        self._attr(1, 4, 12, 0, 1)
        self._attr(2, 4, 12, 4, 1)
        self._attr(3, 4, 12, 8, 1)
        self.plant_count = len(tank.plants)

        # Fish, one mesh per species
        self.fish_meshes = []
        self.textures = []
        for sp in tank.species:
            tex, box = None, None
            if sp.mesh:
                m, box = load_mesh(sp)
                body = len(m.idx)
                tex = self._texture(_mesh_files(sp)[1])
            else:
                m, body = fish_mesh(sp)
            vao = self._vao()
            self._buffer(GL.GL_ARRAY_BUFFER, _floats(m.verts))
            self._attr(0, 3, 9, 0)
            self._attr(1, 3, 9, 3)
            self._attr(2, 3, 9, 6)
            self._buffer(GL.GL_ELEMENT_ARRAY_BUFFER, array("I", m.idx).tobytes())
            inst = self._buffer(GL.GL_ARRAY_BUFFER, b"\0" * 4, GL.GL_DYNAMIC_DRAW)
            self._attr(3, 4, 12, 0, 1)
            self._attr(4, 4, 12, 4, 1)
            self._attr(5, 4, 12, 8, 1)
            self.fish_meshes.append((sp, vao, inst, body, len(m.idx), tex, box))

        # Sprites
        self.sprite_vao = self._vao()
        self._buffer(GL.GL_ARRAY_BUFFER, _floats([-1, -1, 1, -1, -1, 1, 1, 1]))
        self._attr(0, 2, 2, 0)
        self.sprite_inst = self._buffer(GL.GL_ARRAY_BUFFER, b"\0" * 4, GL.GL_DYNAMIC_DRAW)
        self._attr(1, 4, 4, 0, 1)
        GL.glBindVertexArray(0)
        self.scene_ready = True

    def _texture(self, path):
        from PIL import Image
        im = Image.open(path).convert("RGBA")
        t = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, t)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, im.width, im.height, 0, GL.GL_RGBA,
                        GL.GL_UNSIGNED_BYTE, im.tobytes())
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        for k in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, k, GL.GL_CLAMP_TO_EDGE)
        self.textures.append(t)
        return t

    def ensure_fbo(self, w, h):
        if self.fbo_size == (w, h) and self.fbo is not None:
            return
        self.free_fbo()
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        self.rbos = GL.glGenRenderbuffers(2)
        color, depth = self.rbos
        for rb, fmt, att in ((color, GL.GL_RGBA8, GL.GL_COLOR_ATTACHMENT0),
                             (depth, GL.GL_DEPTH_COMPONENT24, GL.GL_DEPTH_ATTACHMENT)):
            GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
            if self.samples > 1:
                GL.glRenderbufferStorageMultisample(GL.GL_RENDERBUFFER, self.samples, fmt, w, h)
            else:
                GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, fmt, w, h)
            GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, att, GL.GL_RENDERBUFFER, rb)
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("can't make an off-screen buffer")
        # The smoothed picture, which the last pass reads
        self.tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        for k in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, k, GL.GL_NEAREST)
        for k in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, k, GL.GL_CLAMP_TO_EDGE)
        self.resolve = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.resolve)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.tex, 0)
        self.fbo_size = (w, h)

    def free_fbo(self):
        if self.fbo is not None:
            GL.glDeleteFramebuffers(1, [self.fbo])
            GL.glDeleteRenderbuffers(2, list(self.rbos))
            GL.glDeleteFramebuffers(1, [self.resolve])
            GL.glDeleteTextures(1, [self.tex])
            self.fbo = None

    def free(self):
        self.free_fbo()
        for p in self.programs.values():
            GL.glDeleteProgram(p.id)
        if getattr(self, "textures", None):
            GL.glDeleteTextures(len(self.textures), self.textures)
        if self.buffers:
            GL.glDeleteBuffers(len(self.buffers), self.buffers)
        if self.vaos:
            GL.glDeleteVertexArrays(len(self.vaos), self.vaos)

    def draw(self, tank, target):
        if not self.scene_ready:
            self.build_scene(tank)
        w, h = tank.width, tank.height
        self.ensure_fbo(w, h)
        proj = perspective(FOVY, tank.aspect, Z_NEAR, Z_FAR)
        view = look_at(CAM, LOOK, (0, 1, 0))
        vp = mat_mul(proj, view)
        inv = mat_inverse(vp)
        t = tank.time

        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, w, h)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glClear(GL.GL_DEPTH_BUFFER_BIT)

        def common(p):
            p.use()
            GL.glUniform1f(p.u("uTime"), t)
            GL.glUniform3f(p.u("uCam"), *CAM)
            GL.glUniformMatrix4fv(p.u("uVP"), 1, GL.GL_FALSE, vp)

        # Water, gravel, surface, light shafts
        p = self.programs["back"]
        common(p)
        GL.glUniformMatrix4fv(p.u("uInvVP"), 1, GL.GL_FALSE, inv)
        shadows = []
        for f in tank.fish[:64]:
            shadows += [f.pos[0], f.pos[1], f.pos[2], f.size * 0.35]
        GL.glUniform1i(p.u("uShadowN"), len(shadows) // 4)
        if shadows:
            GL.glUniform4fv(p.u("uShadow"), len(shadows) // 4, shadows)
        GL.glDepthFunc(GL.GL_ALWAYS)
        GL.glBindVertexArray(self.quad)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        GL.glDepthFunc(GL.GL_LESS)

        # Rocks and plants
        p = self.programs["rock"]
        common(p)
        GL.glBindVertexArray(self.rock_vao)
        GL.glDrawElements(GL.GL_TRIANGLES, self.rock_count, GL.GL_UNSIGNED_INT, None)

        p = self.programs["plant"]
        common(p)
        GL.glBindVertexArray(self.plant_vao)
        GL.glDrawArraysInstanced(GL.GL_TRIANGLE_STRIP, 0, self.plant_verts, self.plant_count)

        # Fish bodies, then fins see-through on top
        p = self.programs["fish"]
        common(p)
        per = {}
        for f in tank.fish:
            sp = f.sp
            amp = sp.amp * (0.45 + 0.55 * min(f.speed / sp.speed, 1.8))
            per.setdefault(sp.index, []).extend(
                [f.pos[0], f.pos[1], f.pos[2], f.size, f.yaw, f.pitch, f.roll, f.bend,
                 f.phase, amp, f.seed, f.fin])
        for pass_ in (0, 1):
            if pass_:
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glDepthMask(GL.GL_FALSE)
            GL.glUniform1i(p.u("uPass"), pass_)
            for sp, vao, inst, body, total, tex, box in self.fish_meshes:
                data = per.get(sp.index)
                if not data or (pass_ and total == body):
                    continue
                GL.glBindVertexArray(vao)
                if not pass_:
                    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, inst)
                    raw = _floats(data)
                    GL.glBufferData(GL.GL_ARRAY_BUFFER, len(raw), raw, GL.GL_DYNAMIC_DRAW)
                GL.glUniform1i(p.u("uSpecies"), sp.index)
                GL.glUniform1f(p.u("uWaveK"), sp.wave_k)
                GL.glUniform1i(p.u("uMesh"), 1 if tex else 0)
                if tex:
                    GL.glActiveTexture(GL.GL_TEXTURE1)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
                    GL.glUniform1i(p.u("uPhoto"), 1)
                    GL.glUniform4f(p.u("uBox"), *box)
                    GL.glActiveTexture(GL.GL_TEXTURE0)
                else:
                    h_, w_, c_ = sp.shape(sp.eye[0])
                    GL.glUniform3f(p.u("uEye"), 0.5 - sp.eye[0], c_ + h_ * sp.eye[1], sp.eye[2])
                    GL.glUniform3f(p.u("uIris"), *sp.iris)
                n = len(data) // 12
                if pass_ == 0:
                    GL.glDrawElementsInstanced(GL.GL_TRIANGLES, body, GL.GL_UNSIGNED_INT, None, n)
                else:
                    GL.glDrawElementsInstanced(GL.GL_TRIANGLES, total - body, GL.GL_UNSIGNED_INT,
                                               ctypes.c_void_p(body * 4), n)

        # Specks and bubbles
        p = self.programs["sprite"]
        common(p)
        GL.glUniform3f(p.u("uRight"), view[0], view[4], view[8])
        GL.glUniform3f(p.u("uUp"), view[1], view[5], view[9])
        GL.glBindVertexArray(self.sprite_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.sprite_inst)
        for kind, items in ((1, tank.specks), (0, tank.bubbles)):
            if not items:
                continue
            flat = []
            for s in items:
                flat += s[:4]
            raw = _floats(flat)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, len(raw), raw, GL.GL_STREAM_DRAW)
            GL.glUniform1i(p.u("uKind"), kind)
            GL.glDrawArraysInstanced(GL.GL_TRIANGLE_STRIP, 0, 4, len(items))
        GL.glDepthMask(GL.GL_TRUE)

        # Smooth the edges, then colour-grade onto the screen
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self.fbo)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, self.resolve)
        GL.glBlitFramebuffer(0, 0, w, h, 0, 0, w, h, GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target)
        GL.glViewport(0, 0, w, h)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        p = self.programs["post"]
        p.use()
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
        GL.glUniform1i(p.u("uImage"), 0)
        GL.glUniform1f(p.u("uAspect"), tank.aspect)
        GL.glBindVertexArray(self.quad)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        GL.glBindVertexArray(0)
