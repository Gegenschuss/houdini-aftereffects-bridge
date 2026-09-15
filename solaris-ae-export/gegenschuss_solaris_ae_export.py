"""
gegenschuss_solaris_ae_export.py

USD -> After Effects exporter.  Walks a USD stage and writes a .jsx file
that, when run in After Effects, recreates the scene as a comp with
cameras, lights, nulls, solids, and footage layers.

This is the inverse of GegenschussAeUsdExporter.jsx and uses the same
AE<->USD coordinate conversion, applied in reverse:

    AE position  = ( ux, -uy, -uz) * scale       (USD units -> AE pixels)
    AE rotation  = inverse bilateral conjugation by S = diag(1, -1, -1)

Identity USD -> identity AE for every prim type.

Forward / reverse summary
-------------------------
USD prim                              -> AE layer
    Camera                             -> CameraLayer
    DistantLight                       -> Parallel light
    DomeLight                          -> Ambient light
    SphereLight (+ ShapingAPI)         -> Spot light
    SphereLight (no shaping)           -> Point light
    Xform (no children mesh)           -> Null
    Xform + Mesh + displayColor only   -> Solid (color from displayColor)
    Xform + Mesh + UsdPreviewSurface   -> Footage (texture file imported)

Hierarchy is preserved (parent/child prims -> layer.parent links).
Animation timeSamples become AE keyframes.

Public API
----------
    usd_to_jsx(stage, out_jsx_path, **opts) -> dict (summary)

`stage` may be a Usd.Stage instance or a filesystem path to a USD file.
"""

import math
import os
import re
import sys

try:
    from pxr import Usd, UsdGeom, UsdLux, UsdShade, Sdf, Gf
    HAVE_USD = True
except ImportError:
    HAVE_USD = False

# Shared bridge conventions + AE runtime.  Inside the HDA the PythonModule
# registers `ae_convention` from the embedded section before exec'ing this
# file; from a repo checkout we fall back to ../shared.
try:
    import ae_convention as C
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() and not __file__.startswith("<") else os.getcwd()
    sys.path.insert(0, os.path.join(_here, "..", "shared"))
    import ae_convention as C


def _default_runtime_js():
    _here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() and not __file__.startswith("<") else os.getcwd()
    with open(os.path.join(_here, "..", "shared", "ae_runtime.js"), "r", encoding="utf-8") as f:
        return f.read()


# ----- Constants (must match the AE-side exporter) -----

FILM_WIDTH_MM = 36.0
MM_TO_USD = 0.01           # exporter:  apertureH = FILM_WIDTH_MM * MM_TO_USD

# Per-light intensity scale in the forward direction; reverse divides by this
# to recover AE percentage.
LIGHT_INTENSITY_SCALE = {
    "DomeLight":    0.01,
    "DistantLight": 0.05,
    "SphereLight":  1.0,
}

# Default values when a prim attribute is missing.
DEFAULT_COMP_WIDTH  = 1920
DEFAULT_COMP_HEIGHT = 1080
DEFAULT_COMP_FPS    = 24.0
DEFAULT_SCALE       = 100.0     # AE px per USD unit
DEFAULT_DURATION_S  = 10.0


# ----- Math -----

def usd_to_ae_rot3(M):
    """USD row-vector rotation -> AE column-vector rotation (S * M^T * S)."""
    return C.ae_rot_from_row_matrix(M)


def decompose_usd_mat4(m4):
    """USD 4x4 -> (tx, ty, tz, R_ae_3x3, sx, sy, sz).  See ae_convention."""
    t, R, sc = C.decompose_row_matrix4(m4)
    return t[0], t[1], t[2], R, sc[0], sc[1], sc[2]


def euler_zyx_from_matrix(R):
    """AE column-form rotation -> (xr, yr, zr) with R = Rx*Ry*Rz (see ae_convention)."""
    return C.euler_from_ae(R)


def usd_pos_to_ae(tx, ty, tz, scale):
    return C.ae_position((tx, ty, tz), scale)


def usd_focal_to_ae_zoom(focal_usd, comp_width):
    """Legacy helper (36 mm film assumption); the writer now uses focal/aperture."""
    return focal_usd * comp_width / (FILM_WIDTH_MM * MM_TO_USD)


# ----- USD walker -----

class PrimNode:
    """Lightweight per-prim record collected by the walker.

    Holds the prim's classification, animation samples, hierarchy
    pointers, and a JSX-safe variable name.  Everything the writer
    needs is on this object so the writer doesn't re-touch the stage.
    """
    __slots__ = (
        "prim", "kind", "ae_var", "ae_name", "doc",
        "parent", "children",
        "pos_samples", "rot_samples", "scale_samples",
        "poi_samples",
        "focal_samples", "focus_samples",
        "intensity_samples", "color_samples",
        "cone_angle_samples", "cone_feather_samples",
        "vis_in_frame", "vis_out_frame",
        "solid_color", "solid_w", "solid_h",
        "solid_anchor_x", "solid_anchor_y",
        "footage_path",
    )

    def __init__(self, prim, kind):
        self.prim = prim
        self.kind = kind
        self.parent = None
        self.children = []
        self.pos_samples = []        # [(frame, [x, y, z]), ...]
        self.rot_samples = []        # [(frame, [xr, yr, zr]), ...]
        self.scale_samples = []      # [(frame, [sx, sy, sz]), ...]
        self.poi_samples = []        # [(frame, [x, y, z]), ...] for 2-node lights
        self.focal_samples = []      # [(frame, zoom_px)]  (camera only)
        self.focus_samples = []
        self.intensity_samples = []  # [(frame, percent)]
        self.color_samples = []      # [(frame, [r, g, b])]
        self.cone_angle_samples = []
        self.cone_feather_samples = []
        self.vis_in_frame = None
        self.vis_out_frame = None
        self.solid_color = None
        self.solid_w = None
        self.solid_h = None
        # Anchor point in AE-pixel layer-local coords.  Defaults to
        # mesh centre (w/2, h/2) when the USD mesh is centred at origin;
        # offset for shape / text layers whose mesh sits away from anchor.
        self.solid_anchor_x = None
        self.solid_anchor_y = None
        self.footage_path = None
        self.doc = ""
        # Filled in by `assign_names`.
        self.ae_var = ""
        self.ae_name = ""


def _classify(prim):
    """Map a USD prim type to one of the supported AE-side kinds.

    Returns one of: "Camera", "Ambient", "Parallel", "Point", "Spot",
    "Solid", "Footage", "Null", or None to skip the prim entirely
    (it will still be traversed for children).
    """
    t = prim.GetTypeName()
    if t == "Camera":
        return "Camera"
    if t == "DomeLight":
        return "Ambient"
    if t == "DistantLight":
        return "Parallel"
    if t == "SphereLight":
        # Spot iff the prim has the ShapingAPI applied.
        if prim.HasAPI(UsdLux.ShapingAPI):
            return "Spot"
        return "Point"
    if t == "Xform":
        # An Xform with a Mesh child becomes a Solid or Footage layer in AE;
        # a bare Xform becomes a Null.  Decided by `_inspect_geo` later.
        return "Xform"
    if t == "Mesh":
        # Standalone meshes are skipped -- the exporter always wraps geo
        # in an Xform parent.  If we encounter one we treat its parent
        # as the carrier instead.
        return None
    return None


def _inspect_geo(prim):
    """If `prim` has a child Mesh "geo", classify it as Solid or Footage and
    extract the data AE needs (color or texture path, pixel size, and the
    mesh-vs-anchor offset for shape/text layers).

    Returns
        (kind, color, footage_path, width_units, height_units, min_x, max_y)
    where min_x / max_y are the mesh's USD-space extents (caller flips
    Y to AE coords) -- used to derive the AE anchor point so the round-
    tripped mesh lands at the same layer-local position as the original.
    Returns None if the prim has no geo.
    """
    mesh_prim = prim.GetChild("geo")
    if not mesh_prim or mesh_prim.GetTypeName() != "Mesh":
        return None

    points_attr = mesh_prim.GetAttribute("points")
    if not points_attr:
        return None
    pts = points_attr.Get()
    if not pts or len(pts) < 4:
        return None

    # Quad bounds in USD units; convert to AE pixels via caller-provided scale.
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x_usd = min(xs)
    max_y_usd = max(ys)
    width_units  = max(xs) - min_x_usd
    height_units = max_y_usd - min(ys)

    # Footage check: material binding to a UsdPreviewSurface chain.
    rel = mesh_prim.GetRelationship("material:binding")
    footage_path = None
    if rel:
        targets = rel.GetTargets()
        if targets:
            mat_prim = mesh_prim.GetStage().GetPrimAtPath(targets[0])
            if mat_prim:
                # Find the UsdUVTexture child shader and read its file input.
                for child in mat_prim.GetChildren():
                    info_id = child.GetAttribute("info:id")
                    if info_id and info_id.Get() == "UsdUVTexture":
                        f_attr = child.GetAttribute("inputs:file")
                        if f_attr:
                            asset = f_attr.Get()
                            if asset:
                                footage_path = str(asset.resolvedPath or asset.path)
                                break

    if footage_path:
        return ("Footage", None, footage_path, width_units, height_units, min_x_usd, max_y_usd)

    # Solid: read displayColor primvar.
    color = (0.5, 0.5, 0.5)
    dc_attr = mesh_prim.GetAttribute("primvars:displayColor")
    if dc_attr:
        dc = dc_attr.Get()
        if dc and len(dc) > 0:
            color = (dc[0][0], dc[0][1], dc[0][2])
    return ("Solid", color, None, width_units, height_units, min_x_usd, max_y_usd)


def _read_visibility(prim, start_frame, end_frame):
    """Return (in_frame, out_frame) for the prim or (None, None) if always visible.

    Mirrors the exporter's `writeVisibility`: a static "invisible" or a
    timeSamples block with "inherited"/"invisible" transitions.  We collapse
    that back to AE in/out points.
    """
    vis = prim.GetAttribute("visibility")
    if not vis:
        return (None, None)

    samples = vis.GetTimeSamples()
    if not samples:
        v = vis.Get()
        if v == "invisible":
            return (end_frame + 1, end_frame)  # never visible -> in > out
        return (None, None)

    # Walk transitions.  in = first frame where value becomes "inherited";
    # out = last frame before next "invisible" (or end_frame).
    in_f = None
    out_f = None
    last = "inherited"
    for t in samples:
        v = vis.Get(t)
        f = int(round(t))
        if v == "inherited" and last != "inherited" and in_f is None:
            in_f = f
        if v == "invisible" and last == "inherited" and out_f is None and in_f is not None:
            out_f = f - 1
        last = v
    return (in_f, out_f)


def _read_xform_matrix(prim, frame, time_codes_per_second):
    """Compute the prim's *local* transform at `frame` as a 4x4 list-of-lists.

    Uses UsdGeom.Xformable's `GetLocalTransformation` so we get composed
    xform-op stacks (translate, rotate*, scale, transform) without having
    to re-implement op evaluation.  Frame is converted to USD time via
    timeCodesPerSecond from the stage.
    """
    xformable = UsdGeom.Xformable(prim)
    if not xformable:
        return None
    time = Usd.TimeCode(frame)
    m4 = xformable.GetLocalTransformation(time)
    return [[m4[r][c] for c in range(4)] for r in range(4)]


def _is_animated_xform(prim):
    """True iff any xform op on the prim has time samples."""
    xformable = UsdGeom.Xformable(prim)
    if not xformable:
        return False
    for op in xformable.GetOrderedXformOps():
        if op.GetTimeSamples():
            return True
    return False


def _get_attr_samples(prim, attr_name, start_frame, end_frame, default=None):
    """Return [(frame, value), ...] over the integer frame range.

    If the attribute has no time samples, returns one entry with the static
    value (or `default` if both are missing).  Skips emitting anything if
    the attribute exists with no value AND no default.
    """
    attr = prim.GetAttribute(attr_name)
    if not attr:
        if default is None:
            return []
        return [(start_frame, default)]

    times = attr.GetTimeSamples()
    if not times:
        v = attr.Get()
        if v is None:
            v = default
        if v is None:
            return []
        return [(start_frame, v)]

    out = []
    for f in range(int(start_frame), int(end_frame) + 1):
        v = attr.Get(Usd.TimeCode(f))
        if v is None:
            v = default
        if v is None:
            continue
        out.append((f, v))
    return out


def _ae_safe_var(name, used):
    """Sanitise to a JSX-safe identifier: ASCII letters/digits/_, no leading digit.

    `used` is a set of already-claimed names; we suffix `_2`, `_3`, ... until
    free.  Independent of `_ae_safe_layer_name`, which keeps the human-
    readable AE layer name including unicode/punctuation.
    """
    s = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not s or s[0].isdigit():
        s = "_" + s
    base = s
    n = 2
    while s in used:
        s = "{}_{}".format(base, n)
        n += 1
    used.add(s)
    return s


def _ae_safe_layer_name(name):
    """AE layer names tolerate most characters; we only strip control chars."""
    return re.sub(r"[\x00-\x1f]", " ", name)


# ----- Per-frame sampling -----

def _sample_prim(node, start_frame, end_frame, scale, comp_width):
    """Fill `node`'s sample lists by reading its USD attributes per-frame.

    Optimisation: if the entire xform stack is static, we still emit one
    sample (frame == start_frame) so the JSX writer can use setValue
    instead of setValueAtTime.  Same for cameras/lights -- per-attr
    static checks happen at write time.
    """
    prim = node.prim

    # Choose frame list: if nothing is animated, one frame is enough.
    animated = _is_animated_xform(prim)
    if node.kind == "Camera":
        for a in ("focalLength", "focusDistance"):
            attr = prim.GetAttribute(a)
            if attr and attr.GetTimeSamples():
                animated = True
                break
    if node.kind in ("Ambient", "Parallel", "Point", "Spot"):
        for a in ("inputs:intensity", "inputs:color",
                  "inputs:shaping:cone:angle", "inputs:shaping:cone:softness"):
            attr = prim.GetAttribute(a)
            if attr and attr.GetTimeSamples():
                animated = True
                break

    frame_iter = (range(int(start_frame), int(end_frame) + 1) if animated
                  else [int(start_frame)])

    # Transform: decompose the local matrix per frame.
    # Distance to project the Point of Interest in front of 2-node lights.
    # Arbitrary -- POI just defines the look direction; magnitude doesn't
    # affect orientation.  1000 px sits well outside typical comp space
    # so the POI doesn't accidentally end up inside scene geometry.
    POI_DISTANCE = 1000.0

    for f in frame_iter:
        m4 = _read_xform_matrix(prim, f, None)
        if m4 is None:
            continue
        tx, ty, tz, R_ae, sx, sy, sz = decompose_usd_mat4(m4)
        ae_pos = usd_pos_to_ae(tx, ty, tz, scale)
        node.pos_samples.append((f, ae_pos))
        node.rot_samples.append((f, R_ae))
        # Only AVLayers have a meaningful scale -- nulls/cameras/lights
        # ignore it on the AE side.  We still record it; the writer
        # decides whether to emit.
        node.scale_samples.append((f, [sx * 100.0, sy * 100.0, sz * 100.0]))

        # POI-driven 2-node setup is reserved for Parallel/Spot lights:
        # AE hides their rotation channels, so POI is the only path.  We
        # do NOT use POI for cameras -- 2-node POI loses any roll around
        # the look axis (small but visible drift on animated 2-node
        # orbit cameras), so cameras stay 1-node with explicit Euler
        # rotation, which is matrix-exact regardless of how the original
        # was authored.  Local +Z in column-vector form is R_ae's third
        # column; project that out from the AE-space position to get a
        # POI that yields the same look direction.
        if node.kind in ("Parallel", "Spot"):
            fwd = (R_ae[0][2], R_ae[1][2], R_ae[2][2])
            poi = [ae_pos[0] + fwd[0] * POI_DISTANCE,
                   ae_pos[1] + fwd[1] * POI_DISTANCE,
                   ae_pos[2] + fwd[2] * POI_DISTANCE]
            node.poi_samples.append((f, poi))

    # Camera: zoom ratio = focalLength / horizontalAperture (the AE runtime
    # multiplies by the comp width).  Falls back to the exporter's 36 mm film
    # width when the prim has no aperture.
    if node.kind == "Camera":
        ap_attr = prim.GetAttribute("horizontalAperture")
        ap_static = ap_attr.Get() if ap_attr else None
        for f, v in _get_attr_samples(prim, "focalLength", start_frame, end_frame):
            ap = None
            if ap_attr:
                ap = ap_attr.Get(Usd.TimeCode(f)) if ap_attr.GetTimeSamples() else ap_static
            if not ap:
                ap = FILM_WIDTH_MM * MM_TO_USD
            node.focal_samples.append((f, C.zoom_ratio(v, ap)))
        for f, v in _get_attr_samples(prim, "focusDistance", start_frame, end_frame):
            node.focus_samples.append((f, v * scale))

    # Lights: divide intensity back by the per-type factor used in forward.
    if node.kind in ("Ambient", "Parallel", "Point", "Spot"):
        usd_type = {"Ambient": "DomeLight", "Parallel": "DistantLight",
                    "Point": "SphereLight", "Spot": "SphereLight"}[node.kind]
        scale_factor = LIGHT_INTENSITY_SCALE.get(usd_type, 1.0)
        for f, v in _get_attr_samples(prim, "inputs:intensity", start_frame, end_frame):
            node.intensity_samples.append((f, v / scale_factor))
        for f, v in _get_attr_samples(prim, "inputs:color", start_frame, end_frame):
            node.color_samples.append((f, [v[0], v[1], v[2]]))
        if node.kind == "Spot":
            # AE coneAngle is full angle (deg); USD inputs:shaping:cone:angle is half.
            for f, v in _get_attr_samples(prim, "inputs:shaping:cone:angle", start_frame, end_frame):
                node.cone_angle_samples.append((f, v * 2.0))
            # AE coneFeather is percent (0-100); USD softness is 0-1.
            for f, v in _get_attr_samples(prim, "inputs:shaping:cone:softness", start_frame, end_frame):
                node.cone_feather_samples.append((f, v * 100.0))

    # Visibility -> in/out points.
    in_f, out_f = _read_visibility(prim, start_frame, end_frame)
    node.vis_in_frame = in_f
    node.vis_out_frame = out_f

    # Geo: classify the Xform's direct child Mesh, if any.
    if node.kind == "Xform":
        geo = _inspect_geo(prim)
        if geo:
            kind, color, footage, w_units, h_units, min_x_usd, max_y_usd = geo
            node.kind = kind
            node.solid_color = color
            node.footage_path = footage
            node.solid_w = max(1, int(round(w_units * scale)))
            node.solid_h = max(1, int(round(h_units * scale)))
            # AE local-pixel L = min_x_usd * scale; T = -max_y_usd * scale.
            # Anchor = (-L, -T) so the new Solid's mesh (0,0)-(W,H) lines
            # up with the original mesh extents in layer-local space.
            # Default-centred meshes get anchor (W/2, H/2) automatically.
            node.solid_anchor_x = -min_x_usd * scale
            node.solid_anchor_y =  max_y_usd * scale
        else:
            node.kind = "Null"


# ----- Stage walk + tree build -----

def _collect_prims(stage, start_frame, end_frame, scale, comp_width,
                   unwrap_ae_scene=True):
    """Walk the stage and produce a list of PrimNodes plus the root list.

    Pass 1: traverse, classify, and create PrimNodes.  Skip prims that
    don't map to an AE layer (the walk still descends past them).

    Pass 2: link parent/child via prim path containment.

    Optionally unwrap the exporter's "AE_Scene" wrapper -- a top-level
    Xform with a single translate that re-centres AE's top-left origin.
    Detected by name only ("AE_Scene"); the translate it carries is
    discarded so re-imports don't accumulate centre-offsets.
    """
    nodes = []
    by_path = {}

    # Optional unwrap.
    unwrap_path = None
    if unwrap_ae_scene:
        for prim in stage.GetPseudoRoot().GetChildren():
            if prim.GetName() == "AE_Scene" and prim.GetTypeName() == "Xform":
                unwrap_path = prim.GetPath()
                break

    used_vars = set()
    for prim in stage.Traverse():
        if not prim.IsActive():
            continue
        if unwrap_path and prim.GetPath() == unwrap_path:
            continue   # never emit the wrapper itself
        kind = _classify(prim)
        if kind is None:
            continue
        node = PrimNode(prim, kind)
        node.ae_name = _ae_safe_layer_name(prim.GetName())
        node.ae_var = _ae_safe_var(prim.GetName(), used_vars)
        # Doc string from the exporter for original-AE-type info; not
        # currently used to reclassify but kept for debug.
        try:
            doc = prim.GetMetadata("documentation") or ""
        except Exception:
            doc = ""
        node.doc = doc
        nodes.append(node)
        by_path[prim.GetPath()] = node

    # Sample each prim's animation.
    for n in nodes:
        _sample_prim(n, start_frame, end_frame, scale, comp_width)

    # Link parents: nearest collected ancestor in the prim path.
    roots = []
    for n in nodes:
        path = n.prim.GetPath()
        parent_path = path.GetParentPath()
        # Walk up until we find another node, the unwrap point, or root.
        while parent_path != Sdf.Path("/") and parent_path != Sdf.Path.emptyPath:
            if unwrap_path and parent_path == unwrap_path:
                break
            if parent_path in by_path:
                n.parent = by_path[parent_path]
                by_path[parent_path].children.append(n)
                break
            parent_path = parent_path.GetParentPath()
        if n.parent is None:
            roots.append(n)

    return nodes, roots


# ----- Data for the shared AE runtime -----

def _chan(samples, start_frame, end_frame, fps, time_base, conv=None):
    """[(frame, value)] -> packed channel; per-frame samples carry their own times."""
    if not samples:
        return None
    vals = [conv(v) if conv else v for _, v in samples]
    if len(vals) == 1:
        return {"v": vals[0]}
    times = [round((f - time_base) / float(fps), 6) for f, _ in samples]
    return C.pack(vals, times)


def _vec(v):
    return [C.rnd(v[0]), C.rnd(v[1]), C.rnd(v[2])]


def _layer_data(n, start_frame, end_frame, fps, time_base):
    kind = n.kind
    ch = lambda samples, conv=None: _chan(samples, start_frame, end_frame, fps, time_base, conv)
    if kind == "Camera":
        ltype = "camera"
    elif kind in ("Ambient", "Parallel", "Point", "Spot"):
        ltype = "light"
    elif kind == "Solid":
        ltype = "solid"
    elif kind == "Footage":
        ltype = "footage"
    else:
        ltype = "null"
    L = C.layer(n.prim.GetPath().pathString, n.ae_name, ltype,
                parent=n.parent.prim.GetPath().pathString if n.parent is not None else None)
    if kind != "Ambient" and n.pos_samples:
        L["pos"] = ch(n.pos_samples, _vec)
    if n.rot_samples and kind in ("Camera", "Solid", "Footage", "Null"):
        rx, ry, rz = C.rotation_channels([R for _, R in n.rot_samples])
        if len(n.rot_samples) > 1:
            t = [round((f - time_base) / float(fps), 6) for f, _ in n.rot_samples]
            for c in (rx, ry, rz):
                if "k" in c:
                    c["t"] = t
        L["rx"], L["ry"], L["rz"] = rx, ry, rz
    if kind in ("Solid", "Footage", "Null") and n.scale_samples:
        L["scale"] = ch(n.scale_samples, _vec)
    if kind == "Camera":
        if n.focal_samples:
            L["zoom"] = ch(n.focal_samples)
        if n.focus_samples:
            L["focus"] = ch(n.focus_samples, C.rnd)
    if ltype == "light":
        lg = {"kind": kind.lower()}
        if n.poi_samples:
            lg["poi"] = ch(n.poi_samples, _vec)
        if n.intensity_samples:
            lg["intensity"] = ch(n.intensity_samples, C.rnd)
        if n.color_samples:
            lg["color"] = ch(n.color_samples, _vec)
        if n.cone_angle_samples:
            lg["coneAngle"] = ch(n.cone_angle_samples, C.rnd)
        if n.cone_feather_samples:
            lg["coneFeather"] = ch(n.cone_feather_samples, C.rnd)
        L["light"] = lg
    if kind in ("Solid", "Footage"):
        w = n.solid_w or DEFAULT_COMP_WIDTH
        h = n.solid_h or DEFAULT_COMP_HEIGHT
        anchor = None
        if n.solid_anchor_x is not None and n.solid_anchor_y is not None:
            if abs(n.solid_anchor_x - w / 2.0) >= 0.5 or abs(n.solid_anchor_y - h / 2.0) >= 0.5:
                anchor = [C.rnd(n.solid_anchor_x), C.rnd(n.solid_anchor_y)]
        if kind == "Solid":
            c = n.solid_color or (0.5, 0.5, 0.5)
            L["solid"] = {"w": w, "h": h, "color": [C.rnd(c[0]), C.rnd(c[1]), C.rnd(c[2])], "anchor": anchor}
        else:
            L["footage"] = {"path": (n.footage_path or "").replace("\\", "/"), "w": w, "h": h, "anchor": anchor}
    if n.vis_in_frame is not None:
        L["inPoint"] = round((n.vis_in_frame - time_base) / float(fps), 6)
    if n.vis_out_frame is not None:
        L["outPoint"] = round((n.vis_out_frame + 1 - time_base) / float(fps), 6)
    return L


def _build_data(stage, nodes, comp_name, comp_w, comp_h, fps, start_frame, end_frame,
                par=1.0, comp_mode="reuse", linear=True, time_base=0, source=""):
    times = [round((f - time_base) / float(fps), 6) for f in range(int(start_frame), int(end_frame) + 1)]
    data = C.new_data("Solaris AE Export", "gegenschuss::ae_export", comp_name, fps, comp_w, comp_h,
                      start_frame, end_frame, times, linear=linear, center_origin=False,
                      comp_mode=comp_mode, par=par, display_start=0.0, source=source)
    data["layers"] = [_layer_data(n, start_frame, end_frame, fps, time_base) for n in nodes]
    return data


# ----- Public entry -----

def usd_to_jsx(stage, out_jsx_path,
               comp_name=None, comp_width=None, comp_height=None, fps=None,
               duration_s=None, scale=DEFAULT_SCALE,
               start_frame=None, end_frame=None,
               unwrap_ae_scene=True, par=1.0,
               comp_mode="reuse", linear=True, time_base=0, runtime_js=None):
    """Walk `stage` and write a JSX to `out_jsx_path`.

    `stage` may be a Usd.Stage or a path to a USD file.  All other
    arguments fall back to stage metadata or the DEFAULT_* constants.
    `comp_mode` "reuse" updates the active comp (or one named `comp_name`)
    in place, "new" always creates a comp.  `time_base` is the USD frame
    that lands at 0 s in AE (0 = absolute timecode / fps, as before).
    `runtime_js` is the shared AE runtime text; None reads ../shared.

    Returns a summary dict for caller logging:
        {
            "out_path": str,
            "n_cams": int, "n_lights": int, "n_nulls": int,
            "n_solids": int, "n_footage": int,
            "frame_range": (start, end), "fps": float,
            "comp_w": int, "comp_h": int,
        }
    """
    if not HAVE_USD:
        raise RuntimeError("USD bindings (pxr) not available -- run inside Houdini hython.")

    if isinstance(stage, str):
        stage = Usd.Stage.Open(stage)
        if stage is None:
            raise RuntimeError("Could not open USD stage at: {}".format(stage))

    # Stage metadata fallbacks.
    if fps is None:
        fps = stage.GetFramesPerSecond() or DEFAULT_COMP_FPS
    if start_frame is None:
        start_frame = int(stage.GetStartTimeCode())
    if end_frame is None:
        end_frame = int(stage.GetEndTimeCode())
    if end_frame < start_frame:
        end_frame = start_frame

    # Comp dimensions: prefer user-supplied; else derive from default-prim
    # camera aperture aspect ratio if a Camera exists; else fall back.
    if comp_width is None:
        comp_width = DEFAULT_COMP_WIDTH
    if comp_height is None:
        # Try to derive from any Camera prim's aperture.
        comp_height = DEFAULT_COMP_HEIGHT
        for prim in stage.Traverse():
            if prim.GetTypeName() != "Camera":
                continue
            ah = prim.GetAttribute("horizontalAperture")
            av = prim.GetAttribute("verticalAperture")
            if ah and av:
                ah_v, av_v = ah.Get(), av.Get()
                if ah_v and av_v and ah_v > 0:
                    comp_height = max(1, int(round(comp_width * av_v / ah_v)))
                    break

    if duration_s is None:
        duration_s = (end_frame - start_frame + 1) / float(fps)
    if duration_s <= 0:
        duration_s = DEFAULT_DURATION_S

    # Comp name: explicit > defaultPrim name > stem of output path > "Comp".
    if comp_name is None:
        dp = stage.GetDefaultPrim()
        if dp and dp.GetName() not in ("AE_Scene",):
            comp_name = dp.GetName()
        else:
            comp_name = os.path.splitext(os.path.basename(out_jsx_path))[0] or "Comp"

    nodes, roots = _collect_prims(
        stage, start_frame, end_frame, scale, comp_width,
        unwrap_ae_scene=unwrap_ae_scene,
    )
    data = _build_data(stage, nodes, comp_name, comp_width, comp_height, fps,
                       start_frame, end_frame, par=par, comp_mode=comp_mode, linear=linear,
                       time_base=time_base, source=stage.GetRootLayer().identifier if stage.GetRootLayer() else "")
    data["duration"] = round(float(duration_s), 6)
    text = C.build_jsx(data, runtime_js or _default_runtime_js())

    # Write UTF-8.  AE's $.evalFile reads JSX as UTF-8 fine.
    out_dir = os.path.dirname(os.path.abspath(out_jsx_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_jsx_path, "w", encoding="utf-8") as f:
        f.write(text)

    summary = {
        "out_path": out_jsx_path,
        "n_cams":    sum(1 for n in nodes if n.kind == "Camera"),
        "n_lights":  sum(1 for n in nodes if n.kind in ("Ambient", "Parallel", "Point", "Spot")),
        "n_nulls":   sum(1 for n in nodes if n.kind == "Null"),
        "n_solids":  sum(1 for n in nodes if n.kind == "Solid"),
        "n_footage": sum(1 for n in nodes if n.kind == "Footage"),
        "frame_range": (start_frame, end_frame),
        "fps": float(fps),
        "comp_w": comp_width,
        "comp_h": comp_height,
    }
    return summary
