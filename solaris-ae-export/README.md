```
       _____                          __
      / ___/__ ___ ____ ___  ___ ____/ /  __ _____ ___
     / (_ / -_) _ `/ -_) _ \(_-</ __/ _ \/ // (_-<(_-<
     \___/\__/\_, /\__/_//_/___/\__/_//_/\_,_/___/___/
             /___/
```

# Houdini → After Effects USD Exporter

> 🚧 Early release — coordinates, hierarchy, cameras and nulls are
> exercised against synthetic stages.  Lights, footage materials, and
> animation passes have not yet been round-tripped end-to-end against
> a real AE preview.  Open an issue if you hit something off.

A Solaris LOP HDA that walks a USD stage and writes an After Effects
`.jsx` script.  When the script is run in AE, it creates a comp and
populates it with cameras, lights, nulls, solids, and footage layers —
preserving hierarchy and per-frame animation.

Part of [`houdini-aftereffects-bridge`](https://github.com/Gegenschuss/houdini-aftereffects-bridge) since 2026-09-15 (was its own repo). This is the **reverse** of the bridge's `GegenschussAeUsdExporter.jsx`
and uses the same AE↔USD coordinate conversion applied in inverse.  The bilateral conjugation `S · M · S` (with `S = diag(1, -1, -1)`) is involutive, so the same matrix function maps both directions.

## Why

Round-trip Houdini Solaris ↔ After Effects without going through C4D /
Alembic.  Smaller files, no per-layer Z stacking, no fps drift.  The
`.jsx` is plain text and human-readable.

## Conventions

```
USD position (ux, uy, uz)        →  AE position (ux,  -uy, -uz) * scale
USD rotation matrix (row-vector) →  AE column-vector form, S·R^T·S, S = diag(1, -1, -1)
```

Identity USD → identity AE for every prim type.  Same convention as the
forward exporter; the bilateral conjugation is involutive so the exact
same matrix function maps either direction.

## Prim mapping

| USD                               | AE                                                |
|-----------------------------------|---------------------------------------------------|
| `Camera`                          | Camera (1-node, focal length keyed)               |
| `DomeLight`                       | Ambient light                                     |
| `DistantLight`                    | Parallel light                                    |
| `SphereLight`                     | Point light                                       |
| `SphereLight` + `ShapingAPI`      | Spot light (cone angle / softness keyed)          |
| `Xform` (no `geo` child)          | Null                                              |
| `Xform` + `Mesh "geo"` + displayColor | Solid (color + size from quad bounds)         |
| `Xform` + `Mesh "geo"` + `UsdPreviewSurface` | Footage (texture file imported by absolute path) |

Hierarchy is preserved — nested USD prims become parented AE layers.
Animation `timeSamples` become AE keyframes (one key per frame across
the export range; static channels collapse to a single `setValue`).

## Install

1. Clone the repo.
2. Run the installer from a terminal:

   - **macOS / Linux**: `./install.sh`
   - **Windows**: `install.bat`

   The installer locates Houdini's `hython`, runs `install_hda.py`, and
   writes `otls/gegenschuss_ae_export.hda`.  Override `hython` detection
   by setting `HYTHON` (or `set HYTHON=...` on Windows) before running.

   The installer prompts for the install path (defaults to the repo's
   `otls/`).  If the .hda already exists, it asks before overwriting.

   **Optional: `install_secrets`** -- copy `install_secrets.example` to
   `install_secrets` and put your preferred install directory on a single
   line.  The installer will use it as the default (no need to type the
   path each time).  `install_secrets` is gitignored.

3. Load the HDA into Houdini however you usually do.

The HDA embeds `gegenschuss_solaris_ae_export.py` as a section, so the
`.hda` file is fully self-contained — copy it anywhere.  After editing
the source, re-run the installer to rebuild.

To install from inside Houdini's Python panel instead of the terminal:

```python
exec(open("/path/to/install_hda.py").read())
install_hda("/path/to/repo/otls/gegenschuss_ae_export.hda")
```

## Usage

1. Build a Solaris graph that produces the USD stage you want to export
   (`sopimport`, `karma`, file `usd_import`, etc.).
2. Drop a **Gegenschuss AE Export** node and wire your stage into its
   single input.
3. Set the parameters:
   - **Output JSX** — where to write the script (defaults to `$HIP/<node>.jsx`)
   - **Comp name** — leave blank to use the stage's `defaultPrim` name
   - **Comp width / height** — defaults 1920×1080.  Height of 0 derives
     from the first Camera prim's aperture ratio.
   - **FPS** — 0 = read from stage metadata
   - **Scale** — must match the AE-side exporter's Scale (default 100,
     so 1 USD unit = 100 AE px = 1 m at default Houdini scale)
   - **Frame range** — defaults to the stage's start/end timecodes
   - **Unwrap AE_Scene wrapper** — strips the centre-comp parent the
     AE-side exporter adds, so round-trips stay identity
   - **Target comp** — update the active comp (or one named like the comp
     name) in place, or always create a new comp. Layers are tagged with
     their USD path in the layer Comment, so re-running a newer export
     replaces their keyframes instead of importing again.
   - **Linear keyframes**, **USD frame at 0 s** — interpolation and time base
4. Hit **Save JSX**.  A confirmation dialog reports the prim counts.
5. In After Effects: `File → Scripts → Run Script File…` → pick the JSX.
   First run creates the layers; later runs update them in place.

The AE side of the JSX is the bridge's shared `../shared/ae_runtime.js`; the
maths is `../shared/ae_convention.py`. Camera zoom is `focalLength /
horizontalAperture * comp width` (no fixed 36 mm assumption any more).

## What's verified vs not

End-to-end against synthetic USD stages and a real AE round-trip
(`AE → forward USD → reverse JSX → AE → forward USD` compared
matrix-by-matrix):
- ✅ Coordinate inversion (identity USD → identity AE)
- ✅ Translation, scale, hierarchy, layer order
- ✅ Static + animated transforms (timeSamples → AE keyframes)
- ✅ Camera world matrix round-trip (matrix-exact, including roll)
- ✅ Camera focal length conversion (USD → AE zoom)
- ✅ Static-value optimisation (single `setValue` instead of per-frame keys)
- ✅ AE_Scene wrapper unwrap
- ✅ Solid / Footage / shape / text mesh-vs-anchor placement

Not yet visually verified end-to-end:
- ⚠️ All four light types in AE preview.  Data is converted; intensity
  scaling matches the forward direction's per-type factor.
- ⚠️ Footage import — absolute paths from the Houdini side; AE relinking
  may be needed if assets moved.
- ⚠️ Visibility timeSamples → AE in/out points.
- ⚠️ Spot-light cone angle / softness conversion.

## Known limitations

- **Cameras land as 1-node** — every camera comes back as a 1-node
  (NO_AUTO_ORIENT) AE camera with explicit `xRotation` / `yRotation` /
  `zRotation` from the USD matrix.  This is what gives matrix-exact
  round-trip including roll, but it does mean the camera no longer
  carries a Point of Interest after import; if you need POI handles,
  flip `autoOrient` back to 2-node manually in AE.
- **Orientation = (0, 0, 0)** — all rotation goes into individual X/Y/Z
  Rotation channels (AE composes them Rx*Ry*Rz, Z innermost; measured in AE 26).  If the original AE comp used keyed
  Orientation, the world matrix is still exact but the channel split
  differs from what was originally typed in.
- **Parallel / Spot lights lose roll** — AE hides the rotation channels
  on 2-node lights and blocks `setValue` even via scripting, so we set
  Point of Interest instead.  POI captures the look direction perfectly
  but drops any rotation around the local +Z axis.  Position, look
  direction, intensity, color, and (for Spot) cone angle / softness
  round-trip cleanly; only the camera-style "twist" around the look
  axis is lost on Parallel and Spot.
- **Ambient / Point lights** — Ambient has no transform in AE; Point
  has only Position.  Both round-trip those values cleanly.
- **Text and shape layers** — re-imported as Solid layers (the forward
  exporter writes them as Mesh quads with `displayColor`; we can't
  distinguish them from real solids on import).  Mesh placement
  (`anchorPoint` derived from the original mesh extents) round-trips
  cleanly; the actual glyph / vector outlines are lost.
- **Mesh geometry** — only quad-`geo` meshes are recognised as
  AVLayers.  Arbitrary USD meshes are skipped.
- **Footage paths** — absolute, relative to the Houdini host filesystem.
  Cross-machine projects will need manual relinking.

## Files

- `gegenschuss_solaris_ae_export.py` — core USD → JSX module.  Pure
  Python; uses `pxr` (USD bindings).  Importable standalone for tests.
- `install_hda.py` — run inside Houdini once to build the HDA.
- `otls/` — built HDAs land here.

## Companion repos

- [`houdini-aftereffects-bridge` (formerly aftereffects-to-houdini-usd-exporter)](https://github.com/Gegenschuss/houdini-aftereffects-bridge) —
  forward direction (AE → USD).  Same coordinate conversion, applied in reverse.

## Licence

MIT.
