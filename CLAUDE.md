> This repo is `houdini-aftereffects-bridge`. Besides this AE → USD exporter it holds
> `houdini-ae-camlink/` (OBJ, baked) and `solaris-ae-export/` (LOP, hierarchy), both built on
> `shared/ae_runtime.js` + `shared/ae_convention.py`. Rotation convention measured in AE 26.3:
> X/Y/Z Rotation compose `Rx*Ry*Rz`, same as Orientation.

# CLAUDE.md

Guidance for Claude (and contributors) working on this exporter. The conventions below were verified empirically against AE preview — please don't change them based on Adobe / forum / community sources without first re-running the probe in `Verifying against AE` below.

## Coordinate convention

```
position:  (x,  -y, -z)     # AE world → USD world
rotation:  (rx, -ry, -rz)
```

At the matrix level: bilateral conjugation by `S = diag(1, -1, -1)`:

```
M_usd_col = S · R_ae · S         (column-vector form)
M_usd_row = S · R_ae^T · S       (row-vector form for USD storage)
```

The formula is the unique consequence of two requirements:
1. AE is left-handed Y-down; USD is right-handed Y-up.  The basis change is `S = diag(1, -1, -1)`.
2. Identity AE rotation must map to identity USD rotation (otherwise grafted Y-up geometry would tilt).

There's no creative choice here — only one sign pattern satisfies both.  Applied uniformly to cameras, lights, nulls, AVLayers — no per-prim-type branching.  If you find yourself wanting to add a sign-flip for one prim type only, you've probably misdiagnosed something else.

## AE Orientation Euler order is `X*Y*Z`

In `aeRotMatrix`:

```javascript
var Mo = m3mul(rotX(ori[0]), m3mul(rotY(ori[1]), rotZ(ori[2])));   // ✓ correct
// NOT: m3mul(rotY(ori[1]), m3mul(rotX(ori[0]), rotZ(ori[2])));     // ✗ Y*X*Z is wrong
```

**Verified empirically** with the probe-null script (`Verifying against AE` below). The `Y*X*Z` order — which various Adobe docs, ProVideoCoalition articles, and community plugins assume — produces a ~2° drift on animated 2-node cameras. `X*Y*Z` matches AE's rendered camera matrix to ~1e-4 across all tested frames.

The X/Y/Z Rotation order (`Mi`) is `X*Y*Z` (Z innermost), the same as Orientation. **Measured 2026-09-15** in AE 26.3 with a toWorldVec probe on nulls and cameras (ten multi-axis cases, exact fit); the earlier `Z*Y*X` assumption was off by up to 2.0 in the matrix entries once several channels were non-zero.

## 2-node camera composition

For an AE 2-node camera with `autoOrient = CAMERA_OR_POINT_OF_INTEREST`:

```javascript
Rae = m3mul(lookAtMatrix(rawPos, poi),
            aeRotMatrix(rp.ori, rp.xr, rp.yr, rp.zr));
```

(column-vector form — `aeRotMatrix` is applied first, then `lookAt`)

For 1-node cameras (`NO_AUTO_ORIENT` or `ALONG_PATH`) and nulls:

```javascript
Rae = aeRotMatrix(rp.ori, rp.xr, rp.yr, rp.zr);
```

Other compositions tested and rejected (all worse than the above):

| Composition | Result |
|---|---|
| `lookAt × aeRotMatrix` (X\*Y\*Z order) | ✅ matches AE to ~1e-4 |
| `aeRotMatrix × lookAt` | wrong — R rotates in world, not camera-local |
| `lookAt` only | wrong — ignores user's keyframed orientation |
| `aeRotMatrix` only | wrong — ignores POI auto-orient |

So Adobe DOES apply orientation values on top of POI lookAt for 2-node cameras, despite some doc text suggesting they're "ignored". Don't tear out the composition.

## Verifying against AE (probe-null trick)

When the script's camera matrix doesn't match AE preview, **don't iterate on math fixes by guessing**. Run this to get AE's ground-truth camera matrix at sample frames, then compare and solve.

`Layer.toWorld()` is not directly callable on a `CameraLayer` from ExtendScript (errors `Function layer.toWorld is undefined`), but it IS available in expressions. We use that.

Run with the target camera selected. Cleans up after itself:

```javascript
(function () {
    var comp = app.project.activeItem;
    var cam = comp.selectedLayers[0];
    if (!cam || !(cam instanceof CameraLayer)) {
        alert("Select a camera layer first"); return;
    }
    var fps = comp.frameRate;
    var frames = [0, 30, 60];   // edit as needed
    var probes = [[0,0,0], [100,0,0], [0,100,0], [0,0,100]];
    var nulls = [];
    app.beginUndoGroup("camera matrix probe");
    try {
        for (var i = 0; i < probes.length; i++) {
            var n = comp.layers.addNull();
            n.threeDLayer = true;
            n.name = "_PROBE_" + i;
            n.transform.position.expression =
                'thisComp.layer("' + cam.name + '").toWorld([' + probes[i].join(",") + '])';
            nulls.push(n);
        }
        var out = "Camera: " + cam.name + "  autoOrient=" + cam.autoOrient + "\n\n";
        for (var f = 0; f < frames.length; f++) {
            var t = frames[f] / fps;
            var o = nulls[0].transform.position.valueAtTime(t, false);
            out += "Frame " + frames[f] + ":\n  origin: (" + o.join(", ") + ")\n";
            var labels = ["+X", "+Y", "+Z"];
            for (var ax = 1; ax < 4; ax++) {
                var p = nulls[ax].transform.position.valueAtTime(t, false);
                var d = [(p[0]-o[0])/100, (p[1]-o[1])/100, (p[2]-o[2])/100];
                out += "  " + labels[ax-1] + ":     (" +
                       d[0].toFixed(4) + ", " + d[1].toFixed(4) + ", " + d[2].toFixed(4) + ")\n";
            }
            out += "\n";
        }
        alert(out);
    } finally {
        for (var i = nulls.length - 1; i >= 0; i--) { try { nulls[i].remove(); } catch (e) {} }
        app.endUndoGroup();
    }
})();
```

The `+X / +Y / +Z` lines are the camera's local axes expressed in AE world coordinates — i.e. the columns of the camera's rotation matrix. That's the ground truth to compare against the script's output.

## Verifying triangulation (geometry — no AE needed)

The vector/text mesh pipeline (`tessellatePath`, `dedupAdjacent`, `earClipTriangulate`, `isEar`, `pointInTriangle`, `buildPolygonsWithHoles` and its bridge helpers) is **pure geometry** — no AE dependency — so verify it in Node rather than guessing. Extract the named functions from the `.jsx` (brace-match each `function NAME(...) {…}`), `eval` them, and assert on synthetic rings: correct net area (outer − holes), zero triangle centroids inside any hole, plain shapes unchanged, multiple disjoint outers preserved. This is how hole subtraction (260429ba) was validated against O / 8 / L-shape / 48-gon / reversed-winding / nested-"@" cases **before** it ever touched AE. Do the same for any future triangulation change (Trim Paths, Merge Paths, …).

## Camera focal length

Read per-frame from `cameraOption.zoom` (pixels) and converted via:

```javascript
flS.push([frame, FILM_WIDTH_MM * zoom / comp.width * MM_TO_USD]);
```

`FILM_WIDTH_MM` is exposed in the dialog (default 36 mm = AE's hidden default).  AE's `filmSize` isn't reachable from ExtendScript, so users with APS-C / S35 backs override it before export; the value persists in `app.settings`.

USD `focalLength` units are "tenths of scene unit" with `metersPerUnit = 1`. A 50 mm AE camera lands as `focalLength = 0.5`, which Houdini reads correctly.

### Depth of field + anamorphic aperture (build 260429az / bb)

`fStop = cameraOption.zoom / cameraOption.aperture` — both are AE pixel quantities, so the ratio is unitless (no mm conversion). It's gated on `cameraOption.depthOfField`: when DoF is off the value is forced to 0 (USD pinhole), and the attribute is only authored when some frame actually has DoF engaged. `focusDistance` (already exported) is inert in Karma without a non-zero `fStop`.

Vertical aperture divides by `comp.pixelAspect` (`apertureV = FILM_WIDTH_MM · height / (width · PAR) · MM_TO_USD`) so anamorphic comps get the right vertical FOV. PAR = 1 (square pixels) → identical to the old formula.

## Build version

Bump `BUILD_DATE` (YYMMDD format with optional letter suffix) at the top of the script on each meaningful change. Shown in the dialog title. Current build: `260429bc`.

## What's verified vs not

End-to-end against AE preview:
- ✅ Translation, hierarchy, comp-centre offset
- ✅ 1-node camera (static + animated)
- ✅ 2-node camera (static + animated, with keyframed Orientation)
- ✅ Parented 2-node camera/light with animated parent — POI is routed through an expression probe-null so `parent.fromWorld(camera.pointOfInterest)` evaluates per frame inside an AE expression (ExtendScript can't call fromWorld directly).
- ✅ Camera focal length, aperture, focus distance
- ✅ Static-value optimisation, dedup of held timeSamples
- ✅ Visibility filter (eyeball off, solo)
- ✅ Solid → quad mesh + displayColor
- ✅ Footage → quad mesh + UsdPreviewSurface (texture, UV reader).  ⚠️ The material binding used a wrong relative path (`<mat>` instead of `<../mat>`) through build 260429aw — textures silently rendered grey; fixed 260429ax.  Re-confirm in a Karma/Hydra render.
- ✅ Text → triangulated glyph outlines via `Create Shapes from Text`; animated text (sourceText keys / range-selector animators) emits per-frame timeSampled mesh data; static text falls through to the cheap single-snapshot path; bbox-quad as last-resort fallback.
- ✅ Shape → triangulated outline of every Path / Rect / Ellipse / Star primitive (incl. Pen-drawn Shape - Group)
- ✅ Group-level Vector Transform applied — anchor / position / rotation / scale on a shape group composes correctly into the path coords.
- ✅ 3D AVLayer anchor point — Solid / Footage / Text / Shape all anchor-shift their points so AE rotation/scale pivot lines up
- ✅ Film width exposed in dialog (no more hardcoded 36 mm)
- ✅ Non-uniform / negative AVLayer scale — bilateral conjugation derives `M_row = S * R_usd_row` (rows scaled by `s_i`); identical to the previous code for uniform scale, correct for the rest.
- ✅ Layer time-stretch / time-remap — AE's `valueAtTime(t)` already accounts for both when reading transform properties, so comp-time sampling produces the right per-frame values.  No special handling required.
- ✅ 2D parent / 2D-selected preflight (interactive convert-to-3D)
- ✅ Multi-shape preflight (split layers with >1 Vector Group)
- ✅ Versioned backup (Increment-and-Save / `.aep` copy fallback) before destructive AE ops
- ✅ UTF-8 file writer (non-ASCII layer names round-trip)
- ✅ Layer in/out → `visibility.timeSamples`

Verified by standalone geometry tests (pure math, no AE — see "Verifying triangulation"):
- ✅ Polygon hole subtraction (counters in O / 8 / donuts) — `buildPolygonsWithHoles` classifies rings by containment + winding and bridges each hole into its outer with a keyhole edge; the existing ear-clipper then subtracts the hole.  Validated for correct net area and zero triangles inside holes.
- ✅ Multi-colour fills → one sub-Mesh per distinct colour (`groupPathsByColor`) — verified that a red "O" still subtracts its counter while a differently-coloured shape stays a separate filled mesh.
- ✅ Trim Paths (`trimPolyline`) — arc-length trim verified for open / closed / offset / wrap / corner-preservation.
- ✅ `toUtf8Bytes` rewrite — byte-identical to the old encoder vs reference + 2000 fuzz inputs (incl. surrogate pairs / lone surrogates).

Implemented but NOT yet AE-smoke-tested (build 260429ax–bc; math-correct + additive — confirm in a real export, ideally diffing against a frame-0-start baseline):
- ⚠️ Layer opacity → `primvars:displayOpacity` (all geo writers); textured footage folds opacity into the `UsdUVTexture` alpha scale.
- ⚠️ Camera `fStop` (= zoom / aperture) gated on `depthOfField`; anamorphic vertical aperture (÷ pixelAspect).
- ⚠️ `comp.displayStartTime` → every emitted frame label / timeCode shifted by `frameOffset` (0 for frame-0 comps → identical output).
- ⚠️ Static-layer single-sample optimisation (`isLayerSampleAnimated`) — output identical after dedup; confirm animated layers still move and static ones don't freeze.
- ⚠️ Layer comment + markers → `customData`; per-path stroke widths; `purpose="guide"` on path curves; unsupported-feature preflight; `ensureBackup` save-verification; probe sweep.  All additive — confirm the preflight dialog reads well and markers/comments land in `customData`.

Not yet visually verified:
- ⚠️ All four light types (data is exported, framing/falloff in Houdini not confirmed)

When chasing one of these, run the probe trick first if rotation/orientation is involved.

## Pending follow-ups

Use this section to resume across sessions. Remove items as they ship.

### 1. Functional gaps (not yet addressed)

Done since first release (kept here so future sessions don't re-litigate solved problems):

- ✅ **Film width** — exposed in the dialog (default 36 mm).  AE's `filmSize` isn't scriptable, so the dialog is the bullet-proof workaround.  Persisted via `app.settings`.
- ✅ **3D AVLayer anchor point** — every geo writer (solid, footage, text, shape) now anchor-shifts the mesh points so AE rotation/scale pivot lines up.
- ✅ **Text + Shape vertex reconstruction** — text routes through AE's `Create Shapes from Text` command (acting on a duplicate so the original survives) and the resulting paths are walked, bezier-tessellated and ear-clip-triangulated into a real USD Mesh.  Shape layers walk Path / Rect / Ellipse / Star primitives directly.  Sub-property lookups now use matchName ("ADBE Vector Shape", "ADBE Vector Fill Color") so Pen-drawn shapes don't silently fall back to bbox.  Bbox quad remains as a last-resort fallback.
- ✅ **Animated text** — `isTextAnimated` checks for sourceText keys / expression / text animators; if any is present, the pre-extract loop steps `comp.time` per frame and re-runs `Create Shapes from Text` so each frame's glyph outlines land in `extractedPathsByFrame`.  `writeAnimatedVectorMesh` then emits timeSampled `points` (and timeSampled `faceVertexCounts/Indices` when vertex count changes).  Held-run dedup keeps the file size sane.
- ✅ **Parented 2-node camera with animated parent** — POI probe-null with `parent.fromWorld(camera.pointOfInterest)` expression provides per-frame parent-local POI; we read `position.valueAtTime` from the probe instead of trying `Layer.fromWorld()` from ExtendScript (which silently fails).
- ✅ **Negative / non-uniform scale on AVLayers** — switched matrix baking from column-scaling (`R[i][j] * s_j`) to row-scaling (`R[i][j] * s_i`), matching the row-vector form derivation.
- ✅ **Layer time-stretch / time-remap** — handled transparently by AE's `valueAtTime`, no special-case needed.  Comp-time samples produce correct per-frame transforms regardless of the layer's stretch/remap.
- ✅ **Animation paths (per-type checkboxes)** — emitted as sibling `BasisCurves` prims under AE_Scene (`<primName>_path`).  Dialog has four checkboxes (Cameras, Lights, Nulls, AV layers); all default off, user opts in per type.  Per-frame world-space samples come from a `toWorld([0,0,0])` probe-null so parented + animated layers also produce correct trajectories.  Static paths are skipped.  `displayColor` per type — cam = yellow, light = orange, null = cyan, AVLayer = green.
- ✅ **Animated shape geometry** — `isShapeAnimated` recursively walks `Contents` looking for keyframed / expression-driven properties; if any, the pre-extract loop samples paths per frame via `extractShapePaths(layer, t)` (no destructive ops needed) and `writeShapeGeo` routes through `writeAnimatedVectorMesh`.
- ✅ **Stroke-only paths** — when a Vector Group has Stroke without Fill, paths land in a sibling `def BasisCurves "stroke"` instead of being triangulated.  Stroke colour goes to `displayColor`, Stroke Width to USD `widths` (per-vertex, in scene units).  Fill+stroke layers still render as filled Mesh; the stroke is treated as decoration.
- ✅ **Star primitive roundness** — Outer / Inner Roundness read from the Star property (Adobe matchName has the long-standing "Roundess" typo; we try the typo first, then the corrected spelling).  Tangent length scales with `roundness/100 × half-edge × 0.5523`, cubic-arcing the corners.  Visually matches AE; not bit-exact (Adobe doesn't publish the algorithm).
- ✅ **Correctness sweep (260429ax)** — footage `material:binding` relative path (`<mat>`→`<../mat>`, textures now bind); `UsdPrimvarReader varname` typed `string` not `token`; guarded file `open()`/`write()` so a failed/partial write can't report "Export complete"; `esc()` escapes `\n \r \t` (a newline in a layer name no longer corrupts the whole file); per-frame `valueAtTime` guards on position/zoom/intensity/color (a broken expression on one layer/frame can't abort the export and dangle the undo group); `endFrame < startFrame` clamp; `!isFinite` guard in `fmt`/`fmt6`.
- ✅ **Layer opacity (260429ay)** — `transform.opacity` sampled per-frame for AVLayers → `float[] primvars:displayOpacity` (static or held-run-deduped timeSamples) on solid / footage-untextured / bbox / vector mesh / animated mesh / stroke.  Textured footage folds opacity into `UsdUVTexture inputs:scale = (1,1,1,op)` (out = raw·scale → alpha multiplied).  Fully-opaque layers author nothing.
- ✅ **Camera fStop + DoF (260429az)** — see "Depth of field + anamorphic aperture" above.
- ✅ **Polygon hole subtraction (260429ba)** — `buildPolygonsWithHoles` (ring classification by containment + winding, keyhole bridges) feeds the ear-clipper one simple polygon per outer.  `isEar` skips vertices coincident with a triangle corner so the bridge duplicates don't stall the clip.  Rejected even-odd (USD Mesh has no fill rule); deep nesting ("@") falls back to filled so geometry is never dropped.  Validated by the Node geometry harness.
- ✅ **Polish + perf sweep (260429bb)** — `comp.displayStartTime` → `frameOffset` on every emitted frame label / timeCode; sub-prim name collisions reserved (`geo`/`mat`/`stroke`; `<name>_path` routed through `makePrimName`); dialog numeric validation (reject NaN / ≤0 / far≤near before close, store parsed prefs) + `defaultElement = btnSave`; triangulated-mesh winding reversed to front-facing (matches the quads); degenerate-triangle filter; anamorphic aperture ÷ pixelAspect; `@`-in-texture-path triple-delimiter; header `comp.name` control-char strip; static-layer single-sample (`isLayerSampleAnimated`); cancel-check before write; dead-code cleanups (empty reverse-walk loop, `makeStarShape` `(d/len)·len` cancellation).
- ✅ **Robustness + features sweep (260429bc)** —
  - `ensureBackup` verifies the Increment-and-Save actually ran (project path changed) before trusting it; else falls through to the copy.
  - `toUtf8Bytes` rebuilt as array-push + single `join` with ASCII-run batching (was O(n²)); **byte-identical** to the old output (verified vs reference + 2000 fuzz inputs incl. surrogates).
  - `sweepStaleProbes()` removes leftover `_AE2USD_*` probe nulls from a prior crashed run (self-healing) at the start of the sampling phase.
  - Parented 2-node POI fallback: when the probe fails, fall back to orientation-only instead of mixing parent-local pos with world POI.
  - Stroke width is now **per-path** (USD `widths` per-vertex), not last-seen-wins.
  - **Multi-colour fills** → one sub-Mesh per distinct fill colour (`groupPathsByColor`); a glyph's outer+counter share a colour so holes still subtract.  Node-verified.
  - **Trim Paths** (`ADBE Vector Filter - Trim`): Start/End/Offset trim of the tessellated polyline by arc length (`trimPolyline`), open + closed + wrap.  Per-path (Simultaneously); animated trim is caught by `isShapeAnimated`.  Node-verified.  A trimmed path emits as an open arc.
  - `uniform token purpose = "guide"` on the animation-path `BasisCurves` so trajectories don't render in finals.
  - Layer **comment + markers → `customData`** (`ae:comment`; markers as parallel `ae:markerFrames` / `ae:markerNames`, since customData has no timeSamples).
  - **Unsupported-feature preflight** (`warnUnsupportedFeatures`): a non-blocking confirm listing layers with masks / track mattes / non-Normal blend modes / effects.

Still pending:

- **Light visual verification in Houdini/Karma.**  ⭐ The #1 item.  All four types export data; per-type intensity tuning (Sphere × 1.0, Distant × 0.05, Dome × 0.01, plus `inputs:normalize = 1` and `radius = 0.1` for SphereLight) is the current convention. Needs a render-comparison sweep: AE preview vs Houdini render, all four types.
- ~~X/Y/Z Rotation order untested~~ -- resolved 2026-09-15: measured `X*Y*Z` (Z innermost), `Mi` fixed accordingly.
- **Vector geometry edge cases.**  Still skipped: Merge Paths, Repeater, Wiggle Paths, Pucker & Bloat, Twist.  (Hole subtraction + Trim Paths now shipped.)  All graceful — fall back to bbox quad when no paths could be extracted.  Merge Paths needs robust polygon boolean ops; Repeater is a duplicate-with-incremental-transform; the deformers (Wiggle/Pucker/Twist) are per-vertex displacers (Wiggle is time-random — won't match AE exactly).
- **Roadmap features** (impact-ordered, not yet started): Houdini-side import HDA/LOP (sublayer + viewport camera + guide toggle) — highest net-new value, but needs Houdini to author/test; multi-comp / batch export (refactor core into `exportComp(comp, file, opts)` — large, high untested-regression risk); references / payloads / variants (architectural — needs design + AE testing).

### How to resume a session

1. Re-read this section first; tick off anything the user has confirmed since.
2. For the README: read current `README.md`, apply the diff sketched above. Keep existing tone (early-release warning, coordinate-convention block, terse install/usage).
3. For functional gaps: run the probe-null trick before guessing math fixes.
4. Bump `BUILD_DATE` at the top of `GegenschussAeUsdExporter.jsx` for each meaningful change (current `260429bb`; continue `bc`, `bd`, … or roll the date).
   For pure-geometry changes, verify with the Node harness ("Verifying triangulation") before AE.
5. **Never auto-commit.** Make the edit, save, tell the user briefly what changed, wait for `ship`.
