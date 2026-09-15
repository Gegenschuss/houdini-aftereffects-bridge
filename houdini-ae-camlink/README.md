# AE Cam Link (Houdini → After Effects, baked)

Object-level Houdini HDA `gegenschuss::ae_camlink::1.0` (built file: `../otls/gegenschuss_ae_camlink.hdalc`).
Exports cameras and nulls, and optionally geometry points as markers, into one self-contained `.jsx`.

- Wire one object into the input and/or list any number in *Objects*, press *Export JSX*.
- In AE: File > Scripts > Run Script File. First run creates a 1-node camera per Houdini camera and a 3D
  null per other object; each layer is tagged `camlink:<houdini path>` in its Comment. Re-running a newer
  export replaces the keyframes on those same layers in place, so nothing is re-imported or re-parented.
- Everything is baked: the timeline is stepped frame by frame and the cooked world transform is read, so
  object parenting, CHOP constraints, rivets and sims all come across.
- *Points To Nulls*: every point of the selected geometry objects becomes a keyed marker (small 3D solid
  by default, since AE hides null outlines during playback). A dialog asks for confirmation above the
  point limit. Marker size is in Houdini units × World Scale.

Conventions, shared with the USD exporters in this repo and measured in AE 26.3
(`ae_rotation_probe.jsx`, result in `ae_rotation_probe_result_AE26.txt`):

    AE position        = (x, -y, -z) * World Scale
    AE X/Y/Z Rotation  = (rx, -ry, -rz) of a Houdini "zyx" Euler split
                         (AE composes its channels Rx*Ry*Rz, Z innermost; menu on the node)
    AE camera Zoom     = focal / aperture * comp width

## Rebuild after editing `ae_camlink_module.py`

    cd /opt/hfs22.0 && source houdini_setup_bash
    hython build_hda.py ../otls/gegenschuss_ae_camlink.hdalc ae_camlink_module.py
    hython test_hda.py /tmp/scratch        # camera math check (matrix-exact)
    hython test_points.py /tmp/scratch     # points-to-markers + warning path

The build copies the icon and hidden base parameters from the legacy `gegenschuss::Pa_obj2AE::2`
asset in `/mnt/houdini/env/otls`, then replaces its interface and Python module.
