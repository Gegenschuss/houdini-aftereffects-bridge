"""houdini-aftereffects-bridge :: shared Houdini-side conventions.

Used by AE Cam Link (OBJ, baked) and Solaris AE Export (LOP, hierarchy).
Pure Python (no hou / pxr imports) so it can be exec'd from an HDA section
or imported from disk.

Coordinate convention (AE <-> Houdini/USD), measured in AE 26.3 with
houdini-ae-camlink/ae_rotation_probe.jsx:

    AE position       = (x, -y, -z) * scale
    AE rotation       = S * R_row^T * S  with S = diag(1, -1, -1),
                        split into X/Y/Z Rotation as R = Rx * Ry * Rz (column form,
                        Z innermost -- the order AE composes its channels)
    AE camera zoom    = focal / aperture * comp width   (the runtime multiplies by width)
"""

import datetime
import json
import math

TAG_PREFIX = "camlink:"
RUNTIME_PLACEHOLDER = "__DATA__"


# ------------------------------------------------------------------ math

def ae_rot_from_row_matrix(M):
    """3x3 row-vector-form rotation (Houdini / USD) -> AE column-form rotation.

    S * M^T * S with S = diag(1, -1, -1); the conjugation is involutive.
    """
    return [
        [ M[0][0], -M[1][0], -M[2][0]],
        [-M[0][1],  M[1][1],  M[2][1]],
        [-M[0][2],  M[1][2],  M[2][2]],
    ]


def decompose_row_matrix4(m4):
    """4x4 row-vector-form matrix (m4[row][col]) -> (t, R_ae, s).

    t = (tx, ty, tz) untransformed, R_ae = AE column-form rotation,
    s = (sx, sy, sz) from the column lengths.
    """
    tx, ty, tz = m4[3][0], m4[3][1], m4[3][2]
    M = [[m4[r][c] for c in range(3)] for r in range(3)]

    def col_len(j):
        return math.sqrt(M[0][j] ** 2 + M[1][j] ** 2 + M[2][j] ** 2)

    sx, sy, sz = col_len(0), col_len(1), col_len(2)
    sx = sx if sx > 1e-12 else 1.0
    sy = sy if sy > 1e-12 else 1.0
    sz = sz if sz > 1e-12 else 1.0
    R = [
        [M[0][0] / sx, M[0][1] / sy, M[0][2] / sz],
        [M[1][0] / sx, M[1][1] / sy, M[1][2] / sz],
        [M[2][0] / sx, M[2][1] / sy, M[2][2] / sz],
    ]
    return (tx, ty, tz), ae_rot_from_row_matrix(R), (sx, sy, sz)


def ae_position(t, scale):
    return [t[0] * scale, -t[1] * scale, -t[2] * scale]


def euler_from_ae(R):
    """AE column-form rotation -> (xr, yr, zr) degrees with R = Rx * Ry * Rz.

    Gimbal-lock guard at |R[0][2]| ~ 1 (pitch +-90): x pinned to 0.
    """
    sy = R[0][2]
    if abs(sy) > 0.99999:
        yr = math.copysign(math.pi / 2, sy)
        xr = 0.0
        zr = math.atan2(R[1][0], R[1][1])
    else:
        yr = math.asin(max(-1.0, min(1.0, sy)))
        xr = math.atan2(-R[1][2], R[2][2])
        zr = math.atan2(-R[0][1], R[0][0])
    return math.degrees(xr), math.degrees(yr), math.degrees(zr)


def ae_matrix(xr, yr, zr):
    """Rx * Ry * Rz (column form) from degrees -- AE's channel composition."""
    cx, sx = math.cos(math.radians(xr)), math.sin(math.radians(xr))
    cy, sy = math.cos(math.radians(yr)), math.sin(math.radians(yr))
    cz, sz = math.cos(math.radians(zr)), math.sin(math.radians(zr))
    return [
        [cy * cz,                 -cy * sz,                 sy],
        [cx * sz + sx * sy * cz,   cx * cz - sx * sy * sz, -sx * cy],
        [sx * sz - cx * sy * cz,   sx * cz + cx * sy * sz,  cx * cy],
    ]


def _same_rotation(a, b, tol=1e-3):
    ma, mb = ae_matrix(*a), ae_matrix(*b)
    return all(abs(ma[i][j] - mb[i][j]) < tol for i in range(3) for j in range(3))


def _wrap_to(a, ref):
    while a - ref > 180.0:
        a -= 360.0
    while a - ref < -180.0:
        a += 360.0
    return a


def continuous_euler(triples):
    """Per frame, choose the (xr, yr, zr) branch closest to the previous frame.

    (x, y, z) and (x + 180, 180 - y, z + 180) are the same rotation; near a
    +-90 degree pitch the decomposition may also be re-split between x and z.
    Every candidate is verified to be the same rotation before competing.
    Keeps AE channels continuous so sub-frame interpolation (motion blur,
    different comp fps) does not wobble.
    """
    out = []
    prev = None
    for t in triples:
        t = tuple(t)
        cands = [t, (t[0] + 180.0, 180.0 - t[1], t[2] + 180.0)]
        if prev is not None and abs(math.cos(math.radians(t[1]))) < 0.05:
            for base in list(cands):
                d = base[0] - prev[0]
                for sign in (1.0, -1.0):
                    c = (prev[0], base[1], base[2] + sign * d)
                    if _same_rotation(c, t):
                        cands.append(c)
        if prev is None:
            best = cands[0]
        else:
            best, best_d = None, None
            for c in cands:
                w = tuple(_wrap_to(a, r) for a, r in zip(c, prev))
                d = sum(abs(a - r) for a, r in zip(w, prev))
                if best_d is None or d < best_d:
                    best, best_d = w, d
        out.append(best)
        prev = best
    return out


# --------------------------------------------------------------- packing

def rnd(v, nd=4):
    v = round(float(v), nd)
    return 0.0 if v == 0 else v


def pack(values, times=None):
    """{"v": value} when constant, else {"k": values} (+ "t" when given)."""
    if not values:
        return None
    first = values[0]
    if all(v == first for v in values):
        return {"v": first}
    out = {"k": list(values)}
    if times is not None:
        out["t"] = list(times)
    return out


def rotation_channels(R_list):
    """List of AE rotation matrices (per frame) -> packed rx, ry, rz channels."""
    eul = continuous_euler([euler_from_ae(R) for R in R_list])
    return (pack([rnd(e[0]) for e in eul]),
            pack([rnd(e[1]) for e in eul]),
            pack([rnd(e[2]) for e in eul]))


def zoom_ratio(focal, aperture):
    """focal / horizontal aperture; the AE runtime multiplies by comp width."""
    return rnd(focal / (aperture or 1.0), 6)


# ---------------------------------------------------------------- output

def new_data(title, generator, comp, fps, width, height, start, end, times,
             linear=True, center_origin=True, comp_mode="reuse", par=1.0,
             display_start=0.0, marker_size=40, marker_color=(1.0, 0.35, 0.1), source=""):
    return {
        "title": title,
        "generator": generator,
        "source": source,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "comp": comp,
        "fps": fps,
        "width": int(width),
        "height": int(height),
        "par": par,
        "start": int(start),
        "end": int(end),
        "duration": round((end - start + 1) / float(fps), 6),
        "displayStart": display_start,
        "times": [round(t, 6) for t in times],
        "linear": bool(linear),
        "centerOrigin": bool(center_origin),
        "compMode": comp_mode,
        "markerSize": int(marker_size),
        "markerColor": [rnd(c) for c in marker_color],
        "layers": [],
    }


def layer(id_, name, type_, parent=None, **channels):
    d = {"id": id_, "name": name, "type": type_, "parent": parent}
    d.update({k: v for k, v in channels.items() if v is not None})
    return d


def build_jsx(data, runtime_js):
    header = (
        "// %s -- generated by %s\n"
        "// Source: %s   (%s)\n"
        "// Run in After Effects: File > Scripts > Run Script File...\n"
        "// Layers are tagged \"%s<id>\" in their Comment; re-running a newer export\n"
        "// updates those same layers in place.\n\n"
        % (data["title"], data["generator"], data.get("source", ""), data["date"], TAG_PREFIX)
    )
    return header + runtime_js.replace(RUNTIME_PLACEHOLDER, json.dumps(data, separators=(",", ":")))


def counts(data):
    c = {"camera": 0, "null": 0, "marker": 0, "light": 0, "solid": 0, "footage": 0}
    for L in data["layers"]:
        c[L["type"]] = c.get(L["type"], 0) + 1
    return c
