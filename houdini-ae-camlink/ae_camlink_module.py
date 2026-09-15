"""AE Cam Link - bake cameras, nulls and points from Houdini objects to After Effects.

Houdini side of the bridge's OBJ exporter.  Steps the timeline, reads cooked
world transforms (so object parenting, CHOP constraints, rivets and sims all
bake), converts them with shared/ae_convention.py and writes one .jsx built on
shared/ae_runtime.js.  Both shared files are embedded in the HDA as sections
(build_hda.py) and loaded from there at run time; when this file is run from
the repo checkout they are read from ../shared instead.
"""

import os
import sys
import types

import hou

_SHARED_CACHE = {}


def _shared_text(node, name):
    """Contents of a shared file: HDA section first, repo checkout second."""
    defn = node.type().definition()
    if defn is not None:
        sec = defn.sections().get(name)
        if sec is not None:
            return sec.contents()
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    path = os.path.join(here, "..", "shared", name)
    with open(path, "r", encoding="utf-8") as fp:
        return fp.read()


def _convention(node):
    key = "ae_convention"
    if key not in _SHARED_CACHE:
        src = _shared_text(node, "ae_convention.py")
        mod = types.ModuleType(key)
        mod.__file__ = "<embedded:ae_convention.py>"
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
        _SHARED_CACHE[key] = mod
    return _SHARED_CACHE[key]


# ---------------------------------------------------------------- helpers

def _target_nodes(this):
    """Wired input object followed by the objects listed on the node."""
    nodes = []
    for n in this.inputs():
        if n is not None and n not in nodes:
            nodes.append(n)
    for n in this.parm("objects").evalAsNodes():
        if n is not None and n != this and n not in nodes:
            nodes.append(n)
    return nodes


def add_selected(kwargs):
    """Button callback: append the selected object nodes to the list."""
    this = kwargs["node"]
    parm = this.parm("objects")
    paths = parm.evalAsString().split()
    for n in hou.selectedNodes():
        if isinstance(n, hou.ObjNode) and n != this:
            p = this.relativePathTo(n)
            if p not in paths:
                paths.append(p)
    parm.set(" ".join(paths))


def _rows(node):
    m = node.worldTransform().asTuple()
    return [[m[r * 4 + c] for c in range(4)] for r in range(4)]


def _sample(C, node, frames, world_scale):
    """Step frames and read the cooked world transform (viewport truth)."""
    is_cam = node.type().name() == "cam"
    pos, rots, scl, zoom = [], [], [], []
    for f in frames:
        hou.setFrame(f)
        t, R, s = C.decompose_row_matrix4(_rows(node))
        pos.append([C.rnd(v) for v in C.ae_position(t, world_scale)])
        rots.append(R)
        scl.append([C.rnd(s[0] * 100.0), C.rnd(s[1] * 100.0), C.rnd(s[2] * 100.0)])
        if is_cam:
            zoom.append(C.zoom_ratio(node.evalParm("focal"), node.evalParm("aperture")))
    rx, ry, rz = C.rotation_channels(rots)
    if is_cam:
        return C.layer(node.path(), node.name(), "camera", pos=C.pack(pos), rx=rx, ry=ry, rz=rz, zoom=C.pack(zoom))
    return C.layer(node.path(), node.name(), "null", pos=C.pack(pos), rx=rx, ry=ry, rz=rz, scale=C.pack(scl))


def _point_indices(geo, group):
    if group:
        grp = geo.findPointGroup(group)
        return [p.number() for p in grp.points()] if grp is not None else []
    return list(range(len(geo.iterPoints())))


def _count_points(point_nodes, group, frame):
    hou.setFrame(frame)
    per = []
    for n in point_nodes:
        sop = n.displayNode() if isinstance(n, hou.ObjNode) else None
        geo = sop.geometry() if sop is not None else None
        per.append((n, len(_point_indices(geo, group)) if geo is not None else 0))
    return sum(c for _, c in per), per


def _sample_points(C, node, frames, world_scale, group, name_attr, style):
    """One AE marker per point of the object's display SOP, position keyed per frame."""
    sop = node.displayNode()
    if sop is None:
        return []
    hou.setFrame(frames[0])
    geo = sop.geometry()
    if geo is None:
        return []
    idx = _point_indices(geo, group)
    if not idx:
        return []
    names = {}
    attr = geo.findPointAttrib(name_attr) if name_attr else None
    if attr is not None and attr.dataType() == hou.attribData.String:
        vals = geo.pointStringAttribValues(name_attr)
        names = {i: vals[i] for i in idx if i < len(vals) and vals[i]}
    tracks = [[] for _ in idx]
    for f in frames:
        hou.setFrame(f)
        geo = sop.geometry()
        P = geo.pointFloatAttribValues("P") if geo is not None else []
        xf = node.worldTransform()
        for k, i in enumerate(idx):
            if i * 3 + 2 < len(P):
                v = hou.Vector3(P[i * 3], P[i * 3 + 1], P[i * 3 + 2]) * xf
                tracks[k].append([C.rnd(a) for a in C.ae_position((v[0], v[1], v[2]), world_scale)])
            else:  # point vanished (changing topology): hold the last value
                tracks[k].append(tracks[k][-1] if tracks[k] else [0.0, 0.0, 0.0])
    ltype = "null" if style == "null" else "marker"
    layers = []
    for k, i in enumerate(idx):
        layers.append(C.layer("%s/pt%d" % (node.path(), i), names.get(i) or "%s_pt%03d" % (node.name(), i), ltype,
                              pos=C.pack(tracks[k]), rx={"v": 0.0}, ry={"v": 0.0}, rz={"v": 0.0},
                              scale={"v": [100.0, 100.0, 100.0]}, billboard=(style == "billboard") or None))
    return layers


def _resolution(nodes, frame):
    t = hou.frameToTime(frame)
    for n in nodes:
        if n.type().name() == "cam":
            try:
                return int(n.parm("resx").evalAtTime(t)), int(n.parm("resy").evalAtTime(t))
            except Exception:
                pass
    return 1920, 1080


def _msg(text, warn=False):
    print(text)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(text.replace("\n", "  "),
                                severity=hou.severityType.Warning if warn else hou.severityType.Message)


# ----------------------------------------------------------------- export

def export_jsx(kwargs):
    this = kwargs["node"]
    C = _convention(this)
    nodes = _target_nodes(this)
    point_nodes = []
    if this.evalParm("points_enable"):
        point_nodes = [n for n in this.parm("point_objects").evalAsNodes() if n is not None and n != this]
    if not nodes and not point_nodes:
        _msg("AE Cam Link: wire an object into the input, add objects to Objects, or pick Point Objects.", warn=True)
        return None

    if this.evalParm("range_toggle"):
        start, end = this.evalParmTuple("frame_range")
    else:
        start, end = hou.playbar.frameRange()
    start, end = int(start), int(end)
    if end < start:
        start, end = end, start
    frames = list(range(start, end + 1))
    fps = hou.fps()
    world_scale = this.evalParm("world_scale")
    frame_offset = this.evalParm("frame_offset")
    if this.evalParm("time_mode") == 1:
        times = [hou.frameToTime(f + frame_offset) for f in frames]
    else:
        times = [(f - start + frame_offset) / fps for f in frames]

    width, height = _resolution(nodes, start)
    comp_name = this.evalParm("comp_name").strip() or hou.text.expandString("$HIPNAME")
    style = this.parm("null_style").evalAsString()
    data = C.new_data(
        "AE Cam Link", this.type().name(), comp_name, fps, width, height, start, end, times,
        linear=bool(this.evalParm("linear_keys")), center_origin=bool(this.evalParm("center_origin")),
        comp_mode="reuse", display_start=start / fps,
        marker_size=max(2, int(round(this.evalParm("marker_size") * world_scale))),
        marker_color=this.evalParmTuple("marker_color"), source=hou.hipFile.path())

    path = this.evalParm("file_path").strip()
    if not path:
        _msg("AE Cam Link: destination file is empty.", warn=True)
        return None
    if not path.lower().endswith(".jsx"):
        path += ".jsx"

    current = hou.frame()
    try:
        point_group = this.evalParm("point_group").strip()
        name_attr = this.evalParm("point_name_attr").strip()
        if point_nodes:
            total, per = _count_points(point_nodes, point_group, start)
            limit = this.evalParm("point_limit")
            if total > limit:
                lines = "\n".join("  %s: %d points" % (n.name(), c) for n, c in per)
                text = ("Points To Nulls would create %d After Effects layers, each keyed on every frame "
                        "(warn limit is %d).\n%s\n\nThis is meant for previz markers, not dense geometry. "
                        "Continue?" % (total, limit, lines))
                if hou.isUIAvailable():
                    if hou.ui.displayMessage(text, buttons=("Continue", "Cancel"), default_choice=1,
                                             close_choice=1, severity=hou.severityType.Warning,
                                             title="AE Cam Link") != 0:
                        _msg("AE Cam Link: export cancelled.", warn=True)
                        return None
                else:
                    print("AE Cam Link warning: " + text.replace("\n", " "))
        with hou.InterruptableOperation("AE Cam Link export", open_interrupt_dialog=hou.isUIAvailable()):
            for n in nodes:
                data["layers"].append(_sample(C, n, frames, world_scale))
            for n in point_nodes:
                data["layers"].extend(_sample_points(C, n, frames, world_scale, point_group, name_attr, style))
    finally:
        hou.setFrame(current)
    if not data["layers"]:
        _msg("AE Cam Link: nothing to export (no objects, or point objects have no points).", warn=True)
        return None

    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(C.build_jsx(data, _shared_text(this, "ae_runtime.js")))
    c = C.counts(data)
    _msg("AE Cam Link: wrote %s\n%d camera(s), %d null(s), %d point marker(s), frames %d-%d @ %g fps"
         % (path, c["camera"], c["null"], c["marker"], start, end, fps))
    return path
