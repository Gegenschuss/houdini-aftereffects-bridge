"""AE Cam Link - export cameras and nulls from Houdini to After Effects.

Writes one self-contained .jsx.  Running it in AE (File > Scripts > Run
Script File...) creates a 1-node camera per Houdini camera and a 3D null
per other object.  Every layer is tagged in its Comment field with the
Houdini node path, so re-running a newer export updates the same layers
in place (keys are replaced) instead of importing new ones.

Coordinate convention (same as gegenschuss::ae_export / the AE<->USD
roundtrip, verified against AE toWorld probes):
    AE position = (x, -y, -z) * world_scale
    AE X/Y/Z Rotation = (rx, -ry, -rz) of a Houdini Euler decomposition whose
                        order is the node's "AE Rotation Order" menu (default zyx,
                        the order the original Pa_obj2AE used in production)
    AE zoom (px)  = focal / aperture * comp.width
"""

import datetime
import json
import math
import os

import hou

TAG_PREFIX = "camlink:"


# ---------------------------------------------------------------- helpers

def _target_nodes(this):
    """All wired input objects followed by the objects listed on the node."""
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


def _r(v, nd=4):
    v = round(float(v), nd)
    return 0.0 if v == 0 else v


def _wrap_to(a, ref):
    """Shift angle a by multiples of 360 so it is within 180 of ref."""
    while a - ref > 180.0:
        a -= 360.0
    while a - ref < -180.0:
        a += 360.0
    return a


def _same_rotation(a, b, order, tol=1e-3):
    ma = hou.hmath.buildRotate(hou.Vector3(a), order)
    mb = hou.hmath.buildRotate(hou.Vector3(b), order)
    return all(abs(x - y) < tol for x, y in zip(ma.asTuple(), mb.asTuple()))


def _continuous_euler(triples, order):
    """Pick, per frame, the Euler solution closest to the previous frame.

    Candidates: the explode() result, its mirror branch
    (first + 180, 180 - middle, last + 180), and, near gimbal lock of the
    middle axis, solutions that keep the previous frame's first-axis angle
    and move the difference into the last axis. Every candidate is checked
    to describe the same rotation before it is allowed to compete.
    """
    ax = {"x": 0, "y": 1, "z": 2}
    first, mid, last = (ax[c] for c in order)
    out = []
    prev = None
    for t in triples:
        t = list(t)
        cands = [tuple(t)]
        alt = list(t)
        alt[first] += 180.0
        alt[mid] = 180.0 - alt[mid]
        alt[last] += 180.0
        cands.append(tuple(alt))
        if prev is not None and abs(math.cos(math.radians(t[mid]))) < 0.05:
            for base in (t, alt):
                d = base[first] - prev[first]
                for sign in (1.0, -1.0):
                    c = list(base)
                    c[first] = prev[first]
                    c[last] = base[last] + sign * d
                    if _same_rotation(c, t, order):
                        cands.append(tuple(c))
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


def _pack(values):
    """{"v": value} when the channel is constant, else {"k": [per frame]}."""
    first = values[0]
    if all(v == first for v in values):
        return {"v": first}
    return {"k": values}


def _sample(node, frames, world_scale, order):
    """Step the timeline and read the cooked world transform at every frame.

    hou.setFrame() is used on purpose (instead of worldTransformAtTime): it
    cooks the whole rig the way the viewport shows it, so CHOP constraints,
    rivets, object merges and simulations come across baked.
    """
    is_cam = node.type().name() == "cam"
    pos, rx, ry, rz, scl, zoom = [], [], [], [], [], []
    focal_p = node.parm("focal") if is_cam else None
    aper_p = node.parm("aperture") if is_cam else None
    for f in frames:
        hou.setFrame(f)
        x = node.worldTransform().explode(transform_order="srt", rotate_order=order)
        tx, ty, tz = x["translate"]
        ex, ey, ez = x["rotate"]
        sx, sy, sz = x["scale"]
        pos.append([_r(tx * world_scale), _r(-ty * world_scale), _r(-tz * world_scale)])
        rx.append(ex)
        ry.append(-ey)
        rz.append(-ez)
        scl.append([_r(sx * 100.0), _r(sy * 100.0), _r(sz * 100.0)])
        if is_cam:
            aper = aper_p.eval() or 1.0
            zoom.append(_r(focal_p.eval() / aper, 6))
    rot = _continuous_euler(list(zip(rx, ry, rz)), order)
    layer = {
        "id": node.path(),
        "name": node.name(),
        "type": "camera" if is_cam else "null",
        "pos": _pack(pos),
        "rx": _pack([_r(a[0]) for a in rot]),
        "ry": _pack([_r(a[1]) for a in rot]),
        "rz": _pack([_r(a[2]) for a in rot]),
    }
    if is_cam:
        layer["zoom"] = _pack(zoom)   # multiplied by comp.width inside AE
    else:
        layer["scale"] = _pack(scl)
    return layer


def _point_indices(geo, group):
    if group:
        grp = geo.findPointGroup(group)
        return [p.number() for p in grp.points()] if grp is not None else []
    return list(range(len(geo.iterPoints())))


def _count_points(point_nodes, group, frame):
    """(total, [(node, count)]) at the given frame; used for the warning."""
    hou.setFrame(frame)
    per = []
    for n in point_nodes:
        sop = n.displayNode() if isinstance(n, hou.ObjNode) else None
        geo = sop.geometry() if sop is not None else None
        per.append((n, len(_point_indices(geo, group)) if geo is not None else 0))
    return sum(c for _, c in per), per


def _sample_points(node, frames, world_scale, group, name_attr):
    """One AE null per point of the object's display SOP, position keyed per frame."""
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
                tracks[k].append([_r(v[0] * world_scale), _r(-v[1] * world_scale), _r(-v[2] * world_scale)])
            else:  # point vanished (changing topology): hold the last value
                tracks[k].append(tracks[k][-1] if tracks[k] else [0.0, 0.0, 0.0])
    layers = []
    for k, i in enumerate(idx):
        layers.append({
            "id": "%s/pt%d" % (node.path(), i),
            "name": names.get(i) or "%s_pt%03d" % (node.name(), i),
            "type": "null",
            "pos": _pack(tracks[k]),
            "rx": {"v": 0.0}, "ry": {"v": 0.0}, "rz": {"v": 0.0},
            "scale": {"v": [100.0, 100.0, 100.0]},
        })
    return layers


def _resolution(nodes, frame):
    """Comp size from the first camera (resx/resy), else HD."""
    t = hou.frameToTime(frame)
    for n in nodes:
        if n.type().name() == "cam":
            try:
                return int(n.parm("resx").evalAtTime(t)), int(n.parm("resy").evalAtTime(t))
            except Exception:
                pass
    return 1920, 1080


# ----------------------------------------------------------------- export

def export_jsx(kwargs):
    this = kwargs["node"]
    nodes = _target_nodes(this)
    point_nodes = []
    if this.evalParm("points_enable"):
        point_nodes = [n for n in this.parm("point_objects").evalAsNodes() if n is not None and n != this]
    if not nodes and not point_nodes:
        _msg("AE Cam Link: wire objects into the inputs, add them to Objects, or pick Point Objects.", warn=True)
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
    time_mode = this.evalParm("time_mode")       # 0: range start -> 0 s, 1: Houdini time
    frame_offset = this.evalParm("frame_offset")
    if time_mode == 1:
        times = [hou.frameToTime(f + frame_offset) for f in frames]
    else:
        times = [(f - start + frame_offset) / fps for f in frames]
    times = [round(t, 6) for t in times]

    width, height = _resolution(nodes, start)
    comp_name = this.evalParm("comp_name").strip() or hou.text.expandString("$HIPNAME")

    data = {
        "generator": this.type().name(),
        "hip": hou.hipFile.path(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "comp": comp_name,
        "fps": fps,
        "width": width,
        "height": height,
        "start": start,
        "end": end,
        "duration": round((end - start + 1) / fps, 6),
        "times": times,
        "linear": bool(this.evalParm("linear_keys")),
        "centerOrigin": bool(this.evalParm("center_origin")),
        "nullStyle": this.parm("null_style").evalAsString(),
        "markerSize": max(2, int(round(this.evalParm("marker_size") * world_scale))),   # Houdini units -> px
        "markerColor": [round(c, 4) for c in this.evalParmTuple("marker_color")],
        "layers": None,
    }
    current = hou.frame()
    try:
        point_group = this.evalParm("point_group").strip()
        name_attr = this.evalParm("point_name_attr").strip()
        if point_nodes:
            total, per = _count_points(point_nodes, point_group, start)
            limit = this.evalParm("point_limit")
            if total > limit:
                lines = "\n".join("  %s: %d points" % (n.name(), c) for n, c in per)
                text = ("Points To Nulls would create %d After Effects nulls, each keyed on every frame "
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
            order = this.parm("rot_order").evalAsString()
            layers = [_sample(n, frames, world_scale, order) for n in nodes]
            for n in point_nodes:
                layers.extend(_sample_points(n, frames, world_scale, point_group, name_attr))
            data["layers"] = layers
    finally:
        hou.setFrame(current)
    if not data["layers"]:
        _msg("AE Cam Link: nothing to export (no objects, or point objects have no points).", warn=True)
        return None

    path = this.evalParm("file_path").strip()
    if not path:
        _msg("AE Cam Link: destination file is empty.", warn=True)
        return None
    if not path.lower().endswith(".jsx"):
        path += ".jsx"
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)

    jsx = _jsx_template().replace("__DATA__", json.dumps(data, separators=(",", ":")))
    with open(path, "w") as fp:
        fp.write(jsx)

    n_cam = sum(1 for l in data["layers"] if l["type"] == "camera")
    n_pt = sum(1 for l in data["layers"] if "/pt" in l["id"])
    n_null = len(data["layers"]) - n_cam - n_pt
    _msg("AE Cam Link: wrote %s\n%d camera(s), %d null(s), %d point null(s), frames %d-%d @ %g fps"
         % (path, n_cam, n_null, n_pt, start, end, fps))
    return path


def _msg(text, warn=False):
    print(text)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(text.replace("\n", "  "),
                                severity=hou.severityType.Warning if warn else hou.severityType.Message)


# ------------------------------------------------------------- jsx script

def _jsx_template():
    return r'''// AE Cam Link export (gegenschuss::ae_camlink)
// Run in After Effects: File > Scripts > Run Script File...
// First run creates the layers in the active comp (or a comp named like the
// hip file).  Every layer is tagged "camlink:<houdini path>" in its Comment;
// re-running a newer export replaces the keyframes on those same layers.

(function aeCamLink() {
    var DATA = __DATA__;

    function findComp(name) {
        for (var i = 1; i <= app.project.numItems; i++) {
            var it = app.project.item(i);
            if (it instanceof CompItem && it.name === name) return it;
        }
        return null;
    }

    function getComp() {
        var ai = app.project.activeItem;
        if (ai && ai instanceof CompItem) return { comp: ai, created: false };
        var c = findComp(DATA.comp);
        if (c) return { comp: c, created: false };
        c = app.project.items.addComp(DATA.comp, DATA.width, DATA.height, 1.0, DATA.duration, DATA.fps);
        try { c.displayStartTime = DATA.start / DATA.fps; } catch (e) {}
        return { comp: c, created: true };
    }

    function findLayer(comp, tag, name) {
        var i, l;
        for (i = 1; i <= comp.numLayers; i++) {
            l = comp.layer(i);
            if (l.comment === tag) return l;
        }
        for (i = 1; i <= comp.numLayers; i++) {
            l = comp.layer(i);
            if (l.name === name && l.comment === "") return l;
        }
        return null;
    }

    function adjust(v, offset, mult) {
        var m = (mult === undefined) ? 1 : mult;
        if (v instanceof Array) {
            var out = [];
            for (var j = 0; j < v.length; j++) out.push(v[j] * m + (offset ? offset[j] : 0));
            return out;
        }
        return v * m + (offset || 0);
    }

    function setProp(prop, data, offset, mult) {
        if (!prop || !data) return;
        try { if (prop.dimensionsSeparated) prop.dimensionsSeparated = false; } catch (e) {}
        try { if (prop.expressionEnabled) prop.expressionEnabled = false; } catch (e) {}
        while (prop.numKeys > 0) prop.removeKey(1);
        if (data.v !== undefined) {
            prop.setValue(adjust(data.v, offset, mult));
            return;
        }
        var vals = [];
        for (var i = 0; i < data.k.length; i++) vals.push(adjust(data.k[i], offset, mult));
        prop.setValuesAtTimes(DATA.times, vals);
        if (DATA.linear) {
            var lin = KeyframeInterpolationType.LINEAR;
            for (var k = 1; k <= prop.numKeys; k++) prop.setInterpolationTypeAtKey(k, lin, lin);
        }
    }

    app.beginUndoGroup("AE Cam Link update");
    var res = getComp();
    var comp = res.comp;
    var created = 0, updated = 0, ptCreated = 0, ptUpdated = 0, ptStillNull = 0, report = [], errors = [];
    var origin = DATA.centerOrigin ? [comp.width / 2, comp.height / 2, 0] : [0, 0, 0];

    for (var n = 0; n < DATA.layers.length; n++) {
        var L = DATA.layers[n];
        var tag = "camlink:" + L.id;
        var isPoint = L.id.indexOf("/pt") >= 0;
        var style = isPoint ? DATA.nullStyle : "null";   // only point markers become solids
        var layer = null;
        try {
            layer = findLayer(comp, tag, L.name);
            if (!layer) {
                if (L.type === "camera") {
                    layer = comp.layers.addCamera(L.name, [comp.width / 2, comp.height / 2]);
                } else if (style === "null") {
                    layer = comp.layers.addNull(comp.duration);
                    layer.threeDLayer = true;
                } else {
                    layer = comp.layers.addSolid(DATA.markerColor, L.name, DATA.markerSize, DATA.markerSize, 1.0, comp.duration);
                    layer.threeDLayer = true;
                }
                layer.name = L.name;
                layer.comment = tag;
                created++;
                if (L.id.indexOf("/pt") >= 0) ptCreated++;
                else report.push("+ " + L.name + (L.type === "camera" ? " (camera)" : " (null)"));
            } else {
                updated++;
                if (L.id.indexOf("/pt") >= 0) ptUpdated++;
                else report.push("~ " + layer.name + (L.type === "camera" ? " (camera)" : " (null)"));
            }
            if (L.type !== "camera" && !layer.threeDLayer) layer.threeDLayer = true;
            var billboard = (style === "billboard" && !layer.nullLayer);
            try { layer.autoOrient = billboard ? AutoOrientType.CAMERA_OR_POINT_OF_INTEREST : AutoOrientType.NO_AUTO_ORIENT; } catch (e) {}
            if (style !== "null" && layer.nullLayer) ptStillNull++;

            var xf = layer.property("ADBE Transform Group");
            setProp(xf.property("ADBE Position"), L.pos, origin);
            setProp(xf.property("ADBE Orientation"), { v: [0, 0, 0] });
            if (!billboard) {
                setProp(xf.property("ADBE Rotate X"), L.rx);
                setProp(xf.property("ADBE Rotate Y"), L.ry);
                setProp(xf.property("ADBE Rotate Z"), L.rz);
            }
            if (L.type === "camera") {
                setProp(layer.property("ADBE Camera Options Group").property("ADBE Camera Zoom"), L.zoom, 0, comp.width);
            } else if (L.scale) {
                setProp(xf.property("ADBE Scale"), L.scale);
            }
        } catch (err) {
            errors.push(L.name + ": " + err.toString() + (err.line ? " (line " + err.line + ")" : ""));
        }
    }
    app.endUndoGroup();
    if (res.created) comp.openInViewer();

    var fpsNote = (Math.abs(comp.frameRate - DATA.fps) > 0.001)
        ? "\nNote: comp is " + comp.frameRate + " fps, Houdini export is " + DATA.fps + " fps." : "";
    var errNote = errors.length ? "\n\nERRORS:\n" + errors.join("\n") : "";
    alert("AE Cam Link  (" + DATA.date + ")\nComp: " + comp.name + (res.created ? " (new)" : "") +
          "\nFrames " + DATA.start + "-" + DATA.end + "\n" +
          "Created " + created + ", updated " + updated + " of " + DATA.layers.length + "\n" + report.join("\n") +
          ((ptCreated + ptUpdated) ? "\nPoint markers: " + ptCreated + " created, " + ptUpdated + " updated" +
              (ptStillNull ? " (" + ptStillNull + " still nulls: delete them in AE to recreate as solids)" : "") : "") +
          fpsNote + errNote);
})();
'''
