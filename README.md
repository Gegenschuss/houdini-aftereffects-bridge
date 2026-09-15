# Houdini ↔ After Effects Bridge

Tools for moving 3D scenes between After Effects and Houdini in both directions, sharing one
coordinate convention (measured in AE 26.3, see below).

| Tool | Direction | Where |
|------|-----------|-------|
| **AE → Houdini USD Exporter** | AE comp → USD file for Solaris | `GegenschussAeUsdExporter.jsx` (this README, below) |
| **AE Cam Link** | Houdini cameras / nulls / points → self-updating AE `.jsx` (baked) | `houdini-ae-camlink/`, built HDA in `otls/` |
| **Solaris AE Export** | USD stage → AE comp with hierarchy (LOP HDA), updates in place on re-run | `solaris-ae-export/`, built HDA in `solaris-ae-export/otls/` |

Shared convention:

```
position: (x, -y, -z) * scale
rotation: (rx, -ry, -rz)   AE composes X/Y/Z Rotation as Rx*Ry*Rz (Z innermost), same as Orientation
zoom:     focal / aperture * comp width
```

## Shared runtime (`shared/`)

Both Houdini exporters emit the same data structure and embed the same two files at build time:

- `shared/ae_runtime.js` -- the After Effects side. Finds the target comp (active, by name, or new),
  finds layers by their `camlink:<id>` Comment tag and updates them in place or creates them
  (camera, null, marker solid, light, solid, footage), writes keys in one batch per channel,
  parents, in/out points, camera zoom and focus, light options, and one summary alert.
- `shared/ae_convention.py` -- the Houdini side. Coordinate conversion, the measured Euler split
  with continuity through gimbal lock, channel packing and JSX assembly.

So one file defines the maths and one file defines what happens in AE; the exporters only
decide what to sample (OBJ world transforms per frame vs USD prims with hierarchy).

The rotation composition was measured in AE 26.3 with `houdini-ae-camlink/ae_rotation_probe.jsx`
(result: `houdini-ae-camlink/ae_rotation_probe_result_AE26.txt`). Run it again before changing any
rotation math in this repo or its Solaris twin.

This repo was `aftereffects-to-houdini-usd-exporter` until 2026-09-15 (GitHub redirects the old URL), and
`houdini-to-aftereffects-usd-exporter` was folded in as `solaris-ae-export/` the same day, history included.

---

## AE → Houdini USD Exporter

> 🚧 Early release — cameras, nulls, hierarchy, translations, solids, footage, text and shape layers are verified against AE preview. Light render output across all four types isn't yet visually confirmed in Karma. Open an issue if you hit something off.

ExtendScript that exports an After Effects composition's 3D layers — cameras, lights, nulls, AVLayers, solids, footage, text and shape layers — to a USD ASCII file ready for import into Houdini.

## Why

Direct AE → USD without going through Cinema4D / Alembic round-trips. Smaller files, preserved hierarchy, no per-layer Z stacking, no time stretching from fps mismatches.

## Conventions

```
position: (x, -y, -z)
rotation: (rx, -ry, -rz)   (channels composed Rx*Ry*Rz, see top of this README)
```

— a bilateral conjugation by `S = diag(1, -1, -1)` that maps AE's left-handed Y-down coordinate system into USD's right-handed Y-up.  The formula is the unique sign pattern that both performs the basis change and preserves identity (identity AE → identity USD), so grafted Y-up Houdini geometry stays correctly oriented through the round-trip.

## Layer mapping

| AE                          | USD                                                                                |
|-----------------------------|------------------------------------------------------------------------------------|
| Camera                      | `Camera`                                                                           |
| Ambient light               | `DomeLight`                                                                        |
| Parallel light              | `DistantLight`                                                                     |
| Point light                 | `SphereLight` (`radius = 0.1`, `inputs:normalize = 1`)                             |
| Spot light                  | `SphereLight` + `ShapingAPI`                                                       |
| Null / non-geo AVLayer      | `Xform`                                                                            |
| Solid                       | `Xform` + `Mesh` (flat quad, `primvars:displayColor` = solid colour)               |
| Footage (image / video)     | `Xform` + `Mesh` + `Material` (`UsdPreviewSurface` + `UsdUVTexture` + `UsdPrimvarReader_float2`) |
| Text layer                  | `Xform` + `Mesh` (triangulated glyph outlines via `Create Shapes from Text`; animated text emits per-frame timeSampled points) |
| Shape layer                 | `Xform` + `Mesh` (triangulated outlines for every filled Path / Rect / Ellipse / Star, fill colour from first `ADBE Vector Graphic - Fill`); stroke-only paths produce a sibling `BasisCurves "stroke"`.  Star roundness + animated path keys preserved. |
| Animated layer (per-type)   | optional sibling `BasisCurves "<primName>_path"` under `AE_Scene` — yellow for cameras, orange for lights, cyan for nulls, green for AV layers — showing the per-frame world-space trajectory.  Toggled per type via dialog checkboxes; skipped when the path is static. |

AE parent/child relationships are preserved as nested USD prims. AE's local position/rotation/scale composes correctly through the nesting.

Text and shape geometry is **real triangulated mesh** built by walking each layer's bezier paths and ear-clipping them. Text routes through AE's `Create Shapes from Text` command on a duplicate so the original layer is preserved. Animated text (sourceText keyframes, range-selector animators, expression-driven content) is detected and re-extracted per frame, producing timeSampled `points` (and timeSampled topology when vertex count changes between frames); static text is sampled once at the export start frame. Polygons-with-holes (letter "O", "8", donuts) have their counters subtracted — inner contours are classified by winding and bridged out before triangulation. Trim Paths / Merge Paths / Repeater and other shape operators fall back to a bounding-box quad.

## Install

1. Save `GegenschussAeUsdExporter.jsx` to your AE Scripts folder, or anywhere on disk.
2. In After Effects: `File → Scripts → Run Script File…` and pick the `.jsx`.
3. Optional: drop it in `Adobe After Effects/Scripts/ScriptUI Panels/` to dock it as a panel.

## Usage

1. Open the comp you want to export and make sure it's the active item.
2. Run the script. The dialog offers:
   - **Scale** — AE pixels per USD scene unit (default 100, so 1 m in Houdini = 100 AE px)
   - **Clip near / far** — written into the USD camera
   - **Film width** — sensor / film-back width in mm (default 36 = full-frame; APS-C ≈ 24, S35 ≈ 25). AE's filmSize isn't scriptable, so the dialog is the override path.
   - **Frames** — Current / Work area / Full comp
   - **Visible only** — when on, only layers with the eyeball enabled (and inside any active solo set) are exported
   - **Text & Shape geometry**:
     - *Bounding box (faster)* — emit a 4-vertex quad instead of triangulated outlines.  Useful for quick layout previews, much smaller files.
     - *Animate text* / *Animate shapes* — per-frame samples when on, single start-frame snapshot when off.  Layer position / rotation / scale always animates either way; these only affect glyph / path geometry animation.
   - **Animation paths** — per-type toggles (Cameras / Lights / Nulls / Other 3D layers) for the sibling `BasisCurves` trajectories.  All default off; opt-in per type.  Static paths are skipped automatically.
   - **Reset** — restore default values
3. Pick a save path. Defaults to `<compname>.usda` in the project folder.
4. After write, a confirmation dialog offers **Reveal in Finder** and **Open .usda**.

The comp is always wrapped in a parent translate so its centre lands at world `(0, 0, 0)` instead of AE's native top-left. Last-used dialog settings are remembered between runs.

## Preflight conversions

Before writing the USD file, the script may offer one or both of these interactive preflights when the comp needs adjustment:

- **2D layers** — a 3D layer parented to a 2D layer (or any 2D layer that's selected when "Visible only" is off) won't export correctly. The preflight lists offenders and offers to flip them to 3D in one click.
- **Multi-shape layers** — a shape layer with more than one *Vector Group* is split into one layer per group (each group becomes its own USD prim with its own bounds and fill colour).

Either preflight is destructive on the AE project, so before any change a **versioned backup** runs first: `Increment and Save` if the project has been saved (the standard AE menu command), otherwise a timestamped `.aep` copy next to the original. Cmd-Z also undoes the changes within the same AE session.

## Optimisations

- Static prims emit plain values (no `timeSamples`).
- Pure-translation prims (no rotation/scale) emit `xformOp:translate` instead of a full `matrix4d`.
- Held animation runs (consecutive equal samples) are de-duplicated to bookend keys only.
- Negative-zero / trailing-zero formatting is cleaned up for readability.

## Visibility filter

Matches AE's render-time visibility:

| AE state                              | Exported? |
|---------------------------------------|-----------|
| Eyeball on                            | ✅        |
| Eyeball off                           | ❌        |
| Solo active anywhere in the comp      | only solo'd layers |
| Shy (UI-hidden but rendered)          | ✅        |
| Locked                                | ✅        |
| 3D switch off on an AVLayer           | ❌        |

## Known limitations

- Text and shape geometry is **triangulated outline only** for fills, plus a sibling **`BasisCurves`** for stroke-only paths.  Distinct fill colours each become their own sub-Mesh; counters (letter "O", "8") are subtracted; deeply-nested compound shapes ("@") fall back to filled.  **Trim Paths** is supported (Start/End/Offset, per-path).  Merge Paths, Repeater, Wiggle Paths, Pucker & Bloat and Twist are skipped silently — the layer falls back to a bounding-box quad if no extractable paths are found.  Gradients and drop-shadows are not represented.
- Layers parented to a non-exported (2D) layer become roots with a parent-relative transform — world position will be wrong. The 2D-layer preflight catches this when the parent is in the same comp.
- Camera **film width** isn't reachable from ExtendScript, so the dialog asks for it (default 36 mm).  If your AE comp uses APS-C / S35 / etc., set the value before exporting.
- Lights export the AE light type and intensity/colour/cone params but haven't been visually verified across all four light types in Houdini/Karma.
- **Animated shape geometry** (path keyframes, animated Vector Group transforms) is detected via a recursive walk of the layer's contents and emits per-frame timeSampled mesh data, matching the text path.
- **Animated text** runs `Create Shapes from Text` once per frame to capture per-frame glyph state.  This is slow on long ranges with many text layers (≈ 100 ms per frame per layer) and increases USD file size proportionally — only triggered when the layer actually has sourceText keys / animators / expression.

### Explicit non-goals (not exported)

Masks, track mattes, blend modes, effects, motion blur, audio, and physically-based materials are **not** exported — a quick preflight warns you when the selected layers use any of them.  Layer opacity (→ `displayOpacity` / texture alpha) and camera depth-of-field (`fStop` gated on AE's DoF toggle) **are** exported; layer comments and markers ride along as USD `customData`.

## Cross-references

Existing AE↔Houdini camera importers used to cross-check the math:

- [TresSims / After-Effects-3D-Camera-to-Houdini](https://github.com/TresSims/After-Effects-3D-Camera-to-Houdini)
- [howiemnet — AE camera data into Houdini gist](https://gist.github.com/howiemnet/8784cf04568c849271730965eaf35159)
- Adobe — [Cameras, lights, and points of interest](https://helpx.adobe.com/after-effects/using/cameras-lights-points-interest.html)

## Licence

MIT.
