import os, sys, hou

SRC = "/mnt/houdini/env/otls/gegenschuss_object_Pa_obj2AE.hdalc"   # template: icon + hidden base parms only
DST = sys.argv[1]
MODULE = sys.argv[2]
NEW_NAME = "gegenschuss::ae_camlink::1.0"

print("license:", hou.licenseCategory())
if os.path.exists(DST):
    os.remove(DST)
hou.hda.installFile(SRC)
src_def = [d for d in hou.hda.definitionsInFile(SRC) if d.nodeTypeName() == "gegenschuss::Pa_obj2AE::2"][0]
src_def.copyToHDAFile(DST, new_name=NEW_NAME, new_menu_name="AE Cam Link")
hou.hda.installFile(DST)
d = [x for x in hou.hda.definitionsInFile(DST) if x.nodeTypeName() == NEW_NAME][0]

# ---- parameters: replace the "Object to AE" folder of the Pa_obj2AE template (only its icon
# and hidden base-parm folders are reused), keep the rest
ptg = d.parmTemplateGroup()
old = ptg.findFolder("Object to AE")

F = hou.FolderParmTemplate("camlink", "AE Cam Link", folder_type=hou.folderType.Simple)
F.addParmTemplate(hou.StringParmTemplate(
    "objects", "Objects", 1, string_type=hou.stringParmType.NodeReferenceList,
    tags={"opfilter": "!!OBJ!!", "oprelative": "."},
    help="Any number of cameras and nulls to export (use Add Selected Objects). An object wired into the "
         "input is exported too. Cameras become AE 1-node cameras, everything else a 3D null."))
F.addParmTemplate(hou.ButtonParmTemplate(
    "add_selected", "Add Selected Objects",
    script_callback="hou.phm().add_selected(kwargs)", script_callback_language=hou.scriptLanguage.Python))
F.addParmTemplate(hou.StringParmTemplate(
    "file_path", "JSX File", 1, string_type=hou.stringParmType.FileReference,
    default_value=("$HIP/camlink/$OS.jsx",), tags={"filechooser_mode": "write", "filechooser_pattern": "*.jsx"},
    help="Keep this path stable: re-running the same file in AE updates the layers in place."))
F.addParmTemplate(hou.StringParmTemplate(
    "comp_name", "Comp Name", 1, default_value=("",),
    help="Comp to create when no comp is active in AE. Empty = $HIPNAME."))
F.addParmTemplate(hou.SeparatorParmTemplate("sep1"))
F.addParmTemplate(hou.FloatParmTemplate(
    "world_scale", "World Scale", 1, default_value=(1000.0,), min=1.0, max=100000.0,
    help="AE pixels per Houdini unit."))
F.addParmTemplate(hou.ToggleParmTemplate(
    "center_origin", "Origin At Comp Center", default_value=True,
    help="Put the Houdini world origin at the comp center (width/2, height/2) instead of AE's top-left corner."))
F.addParmTemplate(hou.SeparatorParmTemplate("sep2"))
F.addParmTemplate(hou.ToggleParmTemplate("range_toggle", "Use Frame Range", default_value=True))
fr = hou.IntParmTemplate("frame_range", "Frame Range", 2, default_value=(1, 240),
                         default_expression=("$FSTART", "$FEND"),
                         naming_scheme=hou.parmNamingScheme.MinMax)
fr.setConditional(hou.parmCondType.DisableWhen, "{ range_toggle == 0 }")
F.addParmTemplate(fr)
F.addParmTemplate(hou.MenuParmTemplate(
    "time_mode", "AE Time", ("range", "houdini"),
    ("First exported frame at 0 s", "Houdini time (frame 1 = 0 s)"), default_value=0))
F.addParmTemplate(hou.IntParmTemplate(
    "frame_offset", "Frame Offset", 1, default_value=(0,), min=-100, max=100,
    help="Shift all AE keys by this many frames (e.g. plate handles)."))
F.addParmTemplate(hou.MenuParmTemplate(
    "rot_order", "AE Rotation Order", ("zyx", "zxy", "yzx", "yxz", "xzy", "xyz"),
    ("zyx  (Z first, X last)", "zxy  (Z first, Y last)", "yzx", "yxz", "xzy", "xyz  (X first, Z last)"),
    default_value=0,
    help="Euler order used to split the world rotation into AE X/Y/Z Rotation channels. "
         "Every order gives exact on-frame results only if it matches how AE composes its channels; "
         "zyx is the order the original Pa_obj2AE used. Change only if a rolled or tilted camera misbehaves in AE."))
F.addParmTemplate(hou.ToggleParmTemplate(
    "linear_keys", "Linear Keyframes", default_value=True,
    help="Set every written keyframe to linear interpolation (one key per frame, so this only affects sub-frame motion)."))
F.addParmTemplate(hou.SeparatorParmTemplate("sep3"))
PF = hou.FolderParmTemplate("points_folder", "Points To Nulls", folder_type=hou.folderType.Collapsible)
PF.addParmTemplate(hou.ToggleParmTemplate(
    "points_enable", "Export Points As Nulls", default_value=False,
    help="Every point of the listed objects becomes an AE 3D null with its world position keyed per frame. "
         "For previz markers only; a warning appears above the point limit."))
po = hou.StringParmTemplate(
    "point_objects", "Point Objects", 1, string_type=hou.stringParmType.NodeReferenceList,
    tags={"opfilter": "!!OBJ!!", "oprelative": "."},
    help="Geometry objects whose display SOP points become nulls. Deliberately separate from the inputs.")
po.setConditional(hou.parmCondType.DisableWhen, "{ points_enable == 0 }")
PF.addParmTemplate(po)
pg = hou.StringParmTemplate("point_group", "Point Group", 1, default_value=("",),
                            help="Optional point group to restrict which points are exported.")
pg.setConditional(hou.parmCondType.DisableWhen, "{ points_enable == 0 }")
PF.addParmTemplate(pg)
pn = hou.StringParmTemplate("point_name_attr", "Name Attribute", 1, default_value=("name",),
                            help="String point attribute used for the AE layer name when present; otherwise <object>_pt<index>.")
pn.setConditional(hou.parmCondType.DisableWhen, "{ points_enable == 0 }")
PF.addParmTemplate(pn)
pl = hou.IntParmTemplate("point_limit", "Warn Above", 1, default_value=(50,), min=1, max=5000,
                         help="Ask for confirmation when the total point count exceeds this.")
pl.setConditional(hou.parmCondType.DisableWhen, "{ points_enable == 0 }")
PF.addParmTemplate(pl)
PF.addParmTemplate(hou.MenuParmTemplate(
    "null_style", "Point Markers", ("solid", "billboard", "null"),
    ("Solid marker", "Solid marker facing camera", "Null layer"), default_value=0,
    help="How point nulls are created in AE. AE hides null outlines during playback, so a small 3D solid "
         "stays visible while previewing. 'Facing camera' billboards the solid. Cameras and regular objects "
         "are not affected: objects always become real nulls."))
ms = hou.FloatParmTemplate("marker_size", "Marker Size (units)", 1, default_value=(0.1,), min=0.001, max=10.0,
                           help="Edge length of the marker solid in Houdini units (multiplied by World Scale). "
                                "Existing solids keep their size; delete them in AE to recreate at a new size.")
ms.setConditional(hou.parmCondType.DisableWhen, "{ points_enable == 0 } { null_style == null }")
PF.addParmTemplate(ms)
mc = hou.FloatParmTemplate("marker_color", "Marker Color", 3, default_value=(1.0, 0.35, 0.1), min=0.0, max=1.0,
                           look=hou.parmLook.ColorSquare, naming_scheme=hou.parmNamingScheme.RGBA)
mc.setConditional(hou.parmCondType.DisableWhen, "{ points_enable == 0 } { null_style == null }")
PF.addParmTemplate(mc)
F.addParmTemplate(PF)
F.addParmTemplate(hou.ButtonParmTemplate(
    "run_script", "Export JSX",
    script_callback="hou.phm().export_jsx(kwargs)", script_callback_language=hou.scriptLanguage.Python))

if old is not None:
    ptg.replace(old, F)
else:
    ptg.insertBefore(ptg.entries()[0], F)
d.setParmTemplateGroup(ptg)

# ---- sections
with open(MODULE) as fp:
    d.addSection("PythonModule", fp.read())
d.setExtraFileOption("PythonModule/IsPython", True)
d.addSection("Version", "1.0")
d.addSection("Help", """= AE Cam Link =

Exports cameras and nulls to After Effects as a self-contained JSX script.

List any number of objects in *Objects* (or wire one into the input), then press *Export JSX*.
*Points To Nulls* turns every point of the selected geometry objects into a keyed AE marker,
by default a small 3D solid that stays visible during playback (previz markers; a warning asks
for confirmation above the point limit).
In After Effects run the file via File > Scripts > Run Script File.

The first run creates a 1-node camera per Houdini camera and a 3D null per other object
in the active comp (or a new comp named after the hip file, sized from the camera resolution).
Each layer is tagged with its Houdini path in the layer Comment. Re-running a newer export
replaces the keyframes on those layers in place, so nothing has to be re-imported or re-parented.

Conventions: AE position = (x, -y, -z) * World Scale; AE X/Y/Z Rotation from a Euler
decomposition (order = *AE Rotation Order*, default zyx) with Y and Z negated; camera Zoom = focal / aperture * comp width.
""")
d.setMaxNumInputs(1)
d.setMinNumInputs(0)
d.save(DST, template_node=None, options=None)
print("saved", DST)
print("sections:", sorted(d.sections().keys()))
