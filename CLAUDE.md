# CLAUDE.md

Guidance for Claude (and contributors) working on this exporter.  Read
this BEFORE editing the math, the layer-type rotation handling, or the
JSX format -- the conventions here are the inverse of
`houdini-aftereffects-bridge` (formerly aftereffects-to-houdini-usd-exporter) and need to stay paired.

## What this is

A Solaris LOP HDA that walks a USD stage and writes an After Effects
.jsx that recreates the scene as a comp.  Reverse of
`houdini-aftereffects-bridge` (formerly aftereffects-to-houdini-usd-exporter)'s `GegenschussAeUsdExporter.jsx`.
Same AE↔USD coordinate conversion, applied in inverse.

## Repo orientation

- `gegenschuss_solaris_ae_export.py` -- the core converter.  Pure
  Python; uses `pxr` (USD bindings, available inside Houdini's hython).
  Importable standalone for tests.  Public entry: `usd_to_jsx(stage,
  out_path, **opts)`.
- `install_hda.py` -- builds the HDA.  Run via `install.sh` /
  `install.bat` from a terminal, OR exec from Houdini's Python source
  editor.  The HDA EMBEDS `gegenschuss_solaris_ae_export.py` as a
  section so the .hda is fully self-contained -- copy it anywhere and
  it works without external dependencies.  Re-run the installer to
  pick up edits to the .py.
- `install.sh` / `install.bat` -- macOS+Linux / Windows wrappers.
  Auto-detect hython, prompt for install path (default = repo's
  `otls/`), confirm before overwriting, confirm before writing outside
  the repo.  `install_secrets` (gitignored) sets a per-machine default
  install path.
- `otls/` -- built HDA lives here.  Committed for convenience so the
  HDA is usable straight from a clone.
- `test/` -- regression fixtures (camera_probe.jsx + expected USD).
  See test/README.md.

## Coordinate convention (inverse)

Forward (AE -> USD), from `houdini-aftereffects-bridge` (formerly aftereffects-to-houdini-usd-exporter):

```
position:  ( px / s, -py / s, -pz / s)        s = scale (default 100)
rotation:  M_usd_row = S * R_ae^T * S          S = diag(1, -1, -1)
```

Reverse (USD -> AE):

```
position:  ( ux * s, -uy * s, -uz * s)
rotation:  same conjugation -- the function is involutive
```

Identity USD -> identity AE for every prim type.  Don't add per-prim
sign flips.  If decomposition looks off for one prim type only, you've
probably misdiagnosed something else.

### Why the same matrix function works in both directions

`S * A^T * S` is involutive when applied twice:
`S * (S * A^T * S)^T * S = S * S * A * S * S = I * A * I = A`.

So `usd_to_ae_rot3` and the exporter's `toUSDMat3` are the same code.

## Layer-type rotation handling

AE hides certain transform channels per layer type; calling `setValue`
on a hidden property errors with "the property or a parent property is
hidden".  The writer routes each kind to the correct AE construct:

| Layer kind        | autoOrient        | What we set                              |
|-------------------|-------------------|------------------------------------------|
| Camera            | NO_AUTO_ORIENT    | position + xRotation/yRotation/zRotation |
| AVLayer (Solid/Footage/Null) | (default) | position + xRotation/yRotation/zRotation |
| Parallel light    | (default 2-node)  | position + pointOfInterest               |
| Spot light        | (default 2-node)  | position + pointOfInterest + cone angle  |
| Point light       | (default)         | position only                            |
| Ambient light     | (default)         | nothing transform-related                |

**Cameras stay 1-node** (`NO_AUTO_ORIENT`) deliberately.  The 2-node /
POI path silently drops any roll around the look axis -- AE's lookAt
gives a roll-free orientation, so animated 2-node orbit cameras
accumulate a small twist that POI can't reproduce.  Going through the
Euler decomposition is matrix-exact regardless of whether the original
camera was 1- or 2-node, so we always emit 1-node.

**Parallel and Spot lights stay POI-only** because AE blocks
`xRotation/yRotation/zRotation` setValue on them via scripting even
when `autoOrient = NO_AUTO_ORIENT`.  This loses roll on those two
light types -- documented limitation.

POI is computed as `pos + R_ae[:,2] * 1000`: the local +Z axis (third
column in column-vector form) projected forward 1000 px.

## Euler decomposition

Cameras + AVLayers go through `euler_zyx_from_matrix`, which decomposes
R = Rx(xr) * Ry(yr) * Rz(zr) (column form, Z innermost -- AE's measured
channel composition, see test/ae_rotation_probe_result_AE26.txt) into
degrees.  Orientation stays at
(0, 0, 0); all rotation goes into individual X/Y/Z Rotation channels.
This is **lossy** when the original AE used keyed Orientation -- the
world-space matrix is exact but the channel split won't match what was
typed in.  Round-trip is still identity at the matrix level.

`euler_zyx_from_matrix` has a gimbal-lock branch at `|sin(yr)| > 0.99999`
that pins `xr = 0` and recovers `zr` from the remaining cells.

History: until 2026-09-15 the decomposition assumed Rz*Ry*Rx (X innermost).
That only matched AE for single-axis rotations; a real shot with a panning,
rolled camera exposed it.  `test/ae_rotation_probe.jsx` measures AE's true
axes via toWorldVec -- run it before changing this again.

## Camera focal length

Forward: `focal_usd = FILM_WIDTH_MM * zoom_px / comp_width * MM_TO_USD`
with `FILM_WIDTH_MM = 36`, `MM_TO_USD = 0.01`.

Reverse: `zoom_px = focal_usd * comp_width / (FILM_WIDTH_MM * MM_TO_USD)`.

The reverse needs `comp_width`, which the user provides as an HDA
parameter.  USD apertureH/V give the aspect ratio so we can derive
`comp_height` from `comp_width` if the user requests auto-height.

## Light intensity

Per-type scale factor used in forward, divided out in reverse:

| USD type      | Forward (AE % -> USD) | Reverse (USD -> AE %) |
|---------------|-----------------------|------------------------|
| DomeLight     | * 0.01                | / 0.01                 |
| DistantLight  | * 0.05                | / 0.05                 |
| SphereLight   | * 1.0                 | / 1.0                  |

Spot light cone angle: USD half-angle * 2 = AE full angle.
Spot light cone softness: USD 0-1 * 100 = AE percent.

## Mesh placement (Solid / Footage / shape / text)

The forward exporter writes Mesh `points` in layer-local coords:
- Solid layers: anchor-relative (`(-anchor_x, -anchor_y) -> (W-anchor_x,
  H-anchor_y)`)
- Shape / Text layers: layer-local from `sourceRectAtTime(t, false)`,
  which can be wildly offset from origin

The reverse computes `min_x_usd` and `max_y_usd` from the mesh's USD
points and sets the AE anchorPoint to `(-L, -T)` in pixel coords
(L = `min_x_usd * scale`, T = `-max_y_usd * scale`).  This makes the
AE Solid's mesh `(0, 0) -> (W, H)` line up with the original's
layer-local bbox.  Default-centred meshes (shape spans ±W/2, ±H/2)
hit the "skip emission" guard and keep AE's default anchor `(W/2, H/2)`.

This is what makes shape / text round-trip cleanly even though they
come back as Solids.

## Layer order

AE's `comp.layers.addNull / addCamera / addLight / addSolid` always
inserts the new layer at index 1 (top of comp), so creation order
maps to REVERSE display order.  We iterate the prim list in reverse
in `_build_jsx` so the first USD prim ends up on top of the AE comp,
matching the forward exporter's top-to-bottom walk order.

## Animation sampling

The walker reads one sample per frame across the export range.  Static
attributes collapse to a single `setValue` call in the JSX; animated
attributes emit `setValueAtTime(t, v)` per frame.  No keyframe
interpolation modes are preserved -- AE re-interpolates linearly between
samples, which matches USD's default linear timeSample interp.

## Verifying

Quick math sanity: build a synthetic stage with a single Xform at
translate `(1, 2, 3)`, no rotation; run export; the resulting JSX
should set position to `(100, -200, -300)` at scale=100.

Camera matrix regression: run `test/camera_probe.jsx` in AE, run the
forward exporter on the resulting comp, diff against
`test/camera_probe_expected.usda`.  All five cases (FACE_PZ, FACE_PX,
PITCH_30, YAW_45, ROLL_20) should match to 10 decimals.  This catches
any regression of the 1-node Euler camera handling -- specifically
ROLL_20 will go translate-only the moment we slip back to POI-only.

Full round-trip: AE -> USD via `houdini-aftereffects-bridge` (formerly aftereffects-to-houdini-usd-exporter)
-> USD -> AE via this tool -> forward export again -> diff matrices.
Any drift > 1e-7 on translation / rotation (in degrees) is a bug.

## Pending follow-ups

Done since first release (kept for the historical record so future
sessions don't re-litigate solved problems):

- ✅ End-to-end round-trip test verified on real AE comps
- ✅ Camera matrix-exact round-trip including roll (1-node Euler)
- ✅ Layer order preservation
- ✅ Solid / shape / text mesh placement via anchorPoint
- ✅ Embedded module in HDA (self-contained .hda)
- ✅ Layer-type-aware rotation channel routing (skip hidden channels)
- ✅ Cross-platform installer with install_secrets

Still pending:

- **Light visual verification in AE preview.**  All four types export
  data; intensity scaling matches the forward direction's per-type
  factor.  Needs render-side comparison to confirm Karma/AE parity.
- **Visibility round-trip.**  Currently extracts in/out frames from
  `visibility.timeSamples`.  Untested against real AE preview.
- **Text/Shape glyph reconstruction.**  Mesh placement preserved, but
  text/shape come back as Solids -- the actual glyph outlines and
  vector paths are lost.  Could detect via the prim's `documentation`
  metadata (`[Text]` / `[Shape]`) and at least set a placeholder text
  layer with the original colour.
- **Parallel / Spot light roll loss.**  AE blocks rotation setValue on
  these even with `autoOrient = NO_AUTO_ORIENT`, so we use POI which
  drops roll around the look axis.  No script-side workaround known.
- **Footage relinking.**  Paths are absolute, baked from the Houdini
  side.  Cross-machine workflows need manual relink.

## How to resume a session

1. Re-read the "Pending follow-ups" section -- the ✅ items are
   off-limits for re-litigation.
2. `git log --oneline -20` for recent context.
3. For matrix changes: read the `Layer-type rotation handling` table
   above before touching `_emit_layer_creation` or `_sample_prim`.
   The routing is load-bearing.
4. For testing: run `test/camera_probe.jsx` first; it's a quick
   regression for the camera math.
5. **Never auto-commit.**  Make the edit, rebuild the HDA via the
   installer, tell the user briefly what changed, wait for `ship`.
