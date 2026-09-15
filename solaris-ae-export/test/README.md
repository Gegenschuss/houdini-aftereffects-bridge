# test/

Regression fixtures for the camera math.  Touched by anyone editing the
1-node-vs-2-node logic, the Euler decomposition, or the rotation-channel
routing in `gegenschuss_solaris_ae_export.py`.

## Files

- **`camera_probe.jsx`** -- creates 5 pairs of cameras and 5 pairs of
  Parallel lights in a fresh AE comp.  Each pair sets up the SAME
  orientation two ways:
  - `<case>_1NODE` -- `autoOrient = NO_AUTO_ORIENT`, explicit Euler
  - `<case>_2NODE` -- `autoOrient = CAMERA_OR_POINT_OF_INTEREST`, POI
  Cases cover face +Z, face +X, pitch 30°, yaw 45°, and **roll 20°**
  (the diagnostic for the POI roll-loss problem we fixed in the
  reverse direction).
- **`camera_probe_expected.usda`** -- the forward-exported USD from a
  known-good AE run of the probe.  Diff against this when verifying
  forward-exporter changes.

## How to use

### Verify the forward exporter (AE -> USD)

1. AE: `File > Scripts > Run Script File...` -> `test/camera_probe.jsx`
2. Run `GegenschussAeUsdExporter` on the resulting `camera_probe` comp,
   save as `.usda`.
3. `diff` the new `.usda` against `camera_probe_expected.usda`.  Matrix
   values should match to 10 decimals.

### Verify the reverse exporter (USD -> AE) round-trip

1. Run the install script and load the HDA.
2. In Solaris, sublayer `camera_probe_expected.usda` and wire it into
   the Gegenschuss AE Export node.
3. Save the JSX, run it in AE, then run `GegenschussAeUsdExporter`
   on the new comp.
4. Diff the second forward export against `camera_probe_expected.usda`.
   ROLL_20_1NODE in particular must keep its 20° z-rotation matrix --
   if it comes back as a translate-only prim, cameras have slipped
   back to POI-only mode and need fixing.

The test/ folder is otherwise gitignored, so feel free to dump
ad-hoc scratch files in here -- only the three files above are
committed.

## Rotation-order probe (added 2026-09-15)

- **`ae_rotation_probe.jsx`** -- sets a null and a camera to ten X/Y/Z
  Rotation combinations and asks AE for the resulting world axes via
  `toWorldVec()`; writes `camlink_probe_result.txt` next to itself.
- **`ae_rotation_probe_result_AE26.txt`** -- the measured answer from AE
  26.3: AE composes the channels as `Rx * Ry * Rz` (Z innermost), same
  as Orientation.  `euler_zyx_from_matrix` and the forward exporter's
  `aeRotMatrix` follow this.  Re-run the probe before touching either.
