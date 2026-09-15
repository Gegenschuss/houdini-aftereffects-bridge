// houdini-aftereffects-bridge :: shared After Effects runtime (ExtendScript, ES3)
//
// Both Houdini exporters (AE Cam Link and Solaris AE Export) embed this file
// after replacing __DATA__ with their scene data.  The runtime owns everything
// that happens inside After Effects:
//   - find the target comp (active comp -> comp by name -> new comp), or always new
//   - find existing layers by their Comment tag "camlink:<id>" and update them in
//     place; otherwise create them (camera, null, marker solid, light, solid, footage)
//   - write keyframes in one batch per channel, optional linear interpolation
//   - parent links, visibility in/out points, camera zoom and focus, light options
//   - report what happened (and any per-layer error) in one alert
//
// DATA layout (produced by shared/ae_convention.py on the Houdini side):
//   comp, fps, width, height, par, start, end, duration, compMode ("reuse"|"new"),
//   times[], linear, centerOrigin, markerSize, markerColor,
//   layers[]: { id, name, type, parent, pos, rx, ry, rz, scale, zoom, focus,
//               light{kind,intensity,color,coneAngle,coneFeather,poi},
//               solid{w,h,color,anchor}, footage{path,anchor}, billboard, inPoint, outPoint }
//   a channel is {v: value} (static) or {k: [values]} aligned with DATA.times,
//   optionally with its own {t: [seconds]}.

(function aeBridge() {
    var DATA = __DATA__;
    var TAG = "camlink:";
    var LINEAR = KeyframeInterpolationType.LINEAR;

    // ------------------------------------------------------------ comp
    function findComp(name) {
        for (var i = 1; i <= app.project.numItems; i++) {
            var it = app.project.item(i);
            if (it instanceof CompItem && it.name === name) return it;
        }
        return null;
    }
    function createComp() {
        var c = app.project.items.addComp(DATA.comp, DATA.width, DATA.height, DATA.par || 1.0, DATA.duration, DATA.fps);
        try { c.displayStartTime = DATA.displayStart || 0; } catch (e) {}
        return c;
    }
    function getComp() {
        if (DATA.compMode !== "new") {
            var ai = app.project.activeItem;
            if (ai && ai instanceof CompItem) return { comp: ai, created: false };
            var c = findComp(DATA.comp);
            if (c) return { comp: c, created: false };
        }
        return { comp: createComp(), created: true };
    }

    // ------------------------------------------------------------ layers
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

    var importedFootage = {};
    function importFootageOnce(path) {
        if (!path) return null;
        if (importedFootage[path]) return importedFootage[path];
        var f = new File(path);
        if (!f.exists) return null;
        var item = app.project.importFile(new ImportOptions(f));
        importedFootage[path] = item;
        return item;
    }

    function createLayer(comp, L) {
        var layer = null;
        var cx = comp.width / 2, cy = comp.height / 2;
        if (L.type === "camera") {
            layer = comp.layers.addCamera(L.name, [cx, cy]);
        } else if (L.type === "light") {
            layer = comp.layers.addLight(L.name, [cx, cy]);
            var kinds = { ambient: LightType.AMBIENT, parallel: LightType.PARALLEL, point: LightType.POINT, spot: LightType.SPOT };
            layer.lightType = kinds[L.light.kind] || LightType.POINT;
        } else if (L.type === "marker") {
            var sz = DATA.markerSize || 40;
            layer = comp.layers.addSolid(DATA.markerColor || [1, 0.35, 0.1], L.name, sz, sz, 1.0, comp.duration);
            layer.threeDLayer = true;
        } else if (L.type === "solid") {
            layer = comp.layers.addSolid(L.solid.color || [0.5, 0.5, 0.5], L.name, L.solid.w, L.solid.h, 1.0, comp.duration);
            layer.threeDLayer = true;
        } else if (L.type === "footage") {
            var item = importFootageOnce(L.footage.path);
            if (item) {
                layer = comp.layers.add(item);
            } else {   // missing file: placeholder solid, relink later via Replace Footage
                var fw = L.footage.w || 100, fh = L.footage.h || 100;
                layer = comp.layers.addSolid([0.3, 0.3, 0.3], L.name, fw, fh, 1.0, comp.duration);
            }
            layer.threeDLayer = true;
        } else {
            layer = comp.layers.addNull(comp.duration);
            layer.threeDLayer = true;
        }
        layer.name = L.name;
        return layer;
    }

    // ------------------------------------------------------------ keys
    function adjust(v, offset, mult) {
        var m = (mult === undefined || mult === null) ? 1 : mult;
        if (v instanceof Array) {
            var out = [];
            for (var j = 0; j < v.length; j++) out.push(v[j] * m + (offset ? (offset[j] || 0) : 0));
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
        var times = data.t || DATA.times;
        var vals = [];
        for (var i = 0; i < data.k.length; i++) vals.push(adjust(data.k[i], offset, mult));
        prop.setValuesAtTimes(times, vals);
        if (DATA.linear) {
            for (var k = 1; k <= prop.numKeys; k++) prop.setInterpolationTypeAtKey(k, LINEAR, LINEAR);
        }
    }

    // ------------------------------------------------------------ main
    app.beginUndoGroup("Houdini AE Bridge");
    var res = getComp();
    var comp = res.comp;
    var origin = DATA.centerOrigin ? [comp.width / 2, comp.height / 2, 0] : [0, 0, 0];
    var created = 0, updated = 0, mkCreated = 0, mkUpdated = 0, mkStillNull = 0;
    var report = [], errors = [], byId = {};
    var n, L;

    // Create missing layers in reverse order so the first data layer ends on top.
    for (n = DATA.layers.length - 1; n >= 0; n--) {
        L = DATA.layers[n];
        try {
            var tag = TAG + L.id;
            var layer = findLayer(comp, tag, L.name);
            var isNew = false;
            if (!layer) {
                layer = createLayer(comp, L);
                layer.comment = tag;
                isNew = true;
            }
            byId[L.id] = layer;
            L._layer = layer; L._new = isNew;
        } catch (e0) {
            errors.push(L.name + ": " + e0.toString());
        }
    }

    for (n = 0; n < DATA.layers.length; n++) {
        L = DATA.layers[n];
        var lay = L._layer;
        if (!lay) continue;
        var label = L.type === "marker" ? null : (L.type === "camera" ? " (camera)" : L.type === "light" ? " (" + L.light.kind + " light)" : " (" + L.type + ")");
        if (L.type === "marker") { if (L._new) mkCreated++; else mkUpdated++; if (lay.nullLayer) mkStillNull++; }
        else { if (L._new) { created++; report.push("+ " + lay.name + label); } else { updated++; report.push("~ " + lay.name + label); } }
        try {
            var isCam = L.type === "camera", isLight = L.type === "light";
            var isAV = !isCam && !isLight;
            if (isAV && !lay.threeDLayer) lay.threeDLayer = true;
            var billboard = !!L.billboard && isAV && !lay.nullLayer;
            var poiLight = isLight && (L.light.kind === "parallel" || L.light.kind === "spot");
            if (!poiLight) {
                try { lay.autoOrient = billboard ? AutoOrientType.CAMERA_OR_POINT_OF_INTEREST : AutoOrientType.NO_AUTO_ORIENT; } catch (e1) {}
            }
            // parent link (raw local values, no jump compensation)
            var wantParent = L.parent ? (byId[L.parent] || null) : null;
            if ((lay.parent || null) !== wantParent) lay.parent = wantParent;

            var xf = lay.transform;
            var hasPos = !(isLight && L.light.kind === "ambient");
            var hasRot = isCam || (isAV && !billboard);
            if (hasPos && L.pos) setProp(xf.position, L.pos, L.parent ? null : origin);
            if (hasRot) {
                setProp(xf.orientation, { v: [0, 0, 0] });
                setProp(xf.xRotation, L.rx || { v: 0 });
                setProp(xf.yRotation, L.ry || { v: 0 });
                setProp(xf.zRotation, L.rz || { v: 0 });
            }
            if (isAV && L.scale) setProp(xf.scale, L.scale);
            var anchor = (L.solid && L.solid.anchor) || (L.footage && L.footage.anchor);
            if (isAV && anchor) setProp(xf.anchorPoint, { v: [anchor[0], anchor[1], 0] });
            if (isCam) {
                if (L.zoom) setProp(lay.cameraOption.zoom, L.zoom, 0, comp.width);   // zoom = focal/aperture * width
                if (L.focus) setProp(lay.cameraOption.focusDistance, L.focus);
            }
            if (isLight) {
                var lo = lay.lightOption, lg = L.light;
                if (poiLight && lg.poi) setProp(xf.pointOfInterest, lg.poi, L.parent ? null : origin);
                if (lg.intensity) setProp(lo.intensity, lg.intensity);
                if (lg.color) setProp(lo.color, lg.color);
                if (lg.kind === "spot") {
                    if (lg.coneAngle) setProp(lo.coneAngle, lg.coneAngle);
                    if (lg.coneFeather) setProp(lo.coneFeather, lg.coneFeather);
                }
            }
            if (L.inPoint !== undefined && L.inPoint !== null) lay.inPoint = L.inPoint;
            if (L.outPoint !== undefined && L.outPoint !== null) lay.outPoint = L.outPoint;
        } catch (err) {
            errors.push(L.name + ": " + err.toString() + (err.line ? " (line " + err.line + ")" : ""));
        }
    }
    app.endUndoGroup();
    if (res.created) comp.openInViewer();

    var fpsNote = (Math.abs(comp.frameRate - DATA.fps) > 0.001)
        ? "\nNote: comp is " + comp.frameRate + " fps, export is " + DATA.fps + " fps." : "";
    var mkNote = (mkCreated + mkUpdated) ? "\nPoint markers: " + mkCreated + " created, " + mkUpdated + " updated" +
        (mkStillNull ? " (" + mkStillNull + " still nulls: delete them in AE to recreate as solids)" : "") : "";
    var errNote = errors.length ? "\n\nERRORS:\n" + errors.join("\n") : "";
    alert((DATA.title || "Houdini AE Bridge") + "  (" + DATA.date + ")\nComp: " + comp.name + (res.created ? " (new)" : "") +
          "\nFrames " + DATA.start + "-" + DATA.end + "\n" +
          "Created " + created + ", updated " + updated + "\n" + report.join("\n") + mkNote + fpsNote + errNote);
})();
