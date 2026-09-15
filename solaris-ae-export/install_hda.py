"""
install_hda.py

Run *inside Houdini* (Python Source Editor or `hython -c`) to build the
Solaris LOP HDA that wraps gegenschuss_solaris_ae_export.

Why this lives outside the HDA itself: the wrapper is short and rarely
changes, but the core Python module is large and iterates often.  Keeping
the module as a sibling .py file means edits don't require re-saving the
HDA, and the module can be imported and unit-tested standalone outside
Houdini.

Usage (inside Houdini, Python Source Editor):

    exec(open("/path/to/install_hda.py").read())
    install_hda("/path/to/output.hda")     # creates the HDA on disk

The repo's `otls/` folder is the canonical install target.  After saving,
add `otls/` to HOUDINI_OTLSCAN_PATH (or `File > Install Asset Library`)
so Houdini picks the HDA up on next launch.
"""

import os

HDA_TYPE_NAME    = "gegenschuss::ae_export::1.0"
HDA_LABEL        = "Gegenschuss AE Export"
HDA_CONTEXT      = "Lop"           # Solaris LOP network
HDA_DESCRIPTION  = (
    "Walks the input USD stage and writes an After Effects .jsx that "
    "recreates the scene as a comp.  Reverse of GegenschussAeUsdExporter.jsx."
)


PYTHON_MODULE_TEMPLATE = '''\
"""HDA backing module -- delegates to gegenschuss_solaris_ae_export.

The core converter source is embedded in this HDA as a section, so the
HDA is fully self-contained: copy it anywhere, drop into Houdini, and
it just works.  Pass an explicit path via the `module_path` parameter
to override with a live disk copy (handy during development).
"""

import os
import sys
import types
import importlib

MODULE_NAME = "gegenschuss_solaris_ae_export"
MODULE_SECTION = MODULE_NAME + ".py"   # section name inside the HDA


def _module_from_string(name, source):
    """exec source into a fresh ModuleType so callers see real attributes."""
    mod = types.ModuleType(name)
    mod.__file__ = "<embedded:%s>" % name
    exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    return mod


def _resolve_module(node):
    # 1. Explicit override -- live disk copy.  Used during development so
    #    edits to the .py don't need an HDA rebuild.
    explicit = node.parm("module_path").evalAsString().strip() if node.parm("module_path") else ""
    if explicit:
        path = os.path.normpath(explicit)
        if not os.path.isfile(path):
            raise RuntimeError("module_path is not a file: " + path)
        d = os.path.dirname(path)
        if d not in sys.path:
            sys.path.insert(0, d)
        if MODULE_NAME in sys.modules:
            importlib.reload(sys.modules[MODULE_NAME])
        return importlib.import_module(MODULE_NAME)

    # 2. Embedded sections -- the default path for end users.  The shared
    #    bridge convention module is registered first so the core module's
    #    `import ae_convention` resolves to the embedded copy.
    defn = node.type().definition()
    if defn is not None:
        sections = defn.sections()
        conv = sections.get("ae_convention.py")
        if conv is not None and "ae_convention" not in sys.modules:
            sys.modules["ae_convention"] = _module_from_string("ae_convention", conv.contents())
        section = sections.get(MODULE_SECTION)
        if section is not None:
            return _module_from_string(MODULE_NAME, section.contents())

    raise RuntimeError(
        "{section} section is missing from this HDA, and no module_path "
        "parameter is set.  Re-run install_hda.py to rebuild the HDA "
        "with the embedded module."
        .format(section=MODULE_SECTION)
    )


def _runtime_js(node):
    """Shared AE runtime: embedded section, else ../shared next to a disk module."""
    defn = node.type().definition()
    if defn is not None:
        sec = defn.sections().get("ae_runtime.js")
        if sec is not None:
            return sec.contents()
    return None


def export_jsx(node):
    """Run the export.  Bound to the `execute` button callback."""
    in_node = node.input(0)
    if in_node is None:
        raise hou.NodeError("Connect a USD stage to the input first.")
    stage = in_node.stage()
    if stage is None:
        raise hou.NodeError("Input did not produce a USD stage.")

    out_path = node.parm("file_path").evalAsString().strip()
    if not out_path:
        raise hou.NodeError("JSX File path is empty.")
    if not out_path.lower().endswith(".jsx"):
        out_path += ".jsx"

    # Resolve and call the core module.
    mod = _resolve_module(node)

    fps = node.parm("fps").evalAsFloat() or None          # None = stage metadata / hip fps
    w, h = node.parmTuple("comp_size").eval()
    kwargs = {
        "scale":            node.parm("world_scale").evalAsFloat(),
        "comp_width":       int(w) or None,
        "comp_height":      int(h) or None,
        "fps":              fps,
        "unwrap_ae_scene":  node.parm("unwrap_ae_scene").evalAsInt() == 1,
        "comp_mode":        node.parm("comp_mode").evalAsString(),
        "linear":           node.parm("linear_keys").evalAsInt() == 1,
        "center_origin":    node.parm("center_origin").evalAsInt() == 1,
        "time_mode":        node.parm("time_mode").evalAsString(),
        "frame_offset":     node.parm("frame_offset").evalAsInt(),
        "runtime_js":       _runtime_js(node),
    }
    name = node.parm("comp_name").evalAsString().strip()
    kwargs["comp_name"] = name or hou.text.expandString("$HIPNAME")
    if node.parm("range_toggle").evalAsInt() == 1:
        kwargs["start_frame"] = node.parm("frame_rangemin").evalAsInt()
        kwargs["end_frame"]   = node.parm("frame_rangemax").evalAsInt()
    if node.parm("duration_override").evalAsFloat() > 0:
        kwargs["duration_s"] = node.parm("duration_override").evalAsFloat()

    summary = mod.usd_to_jsx(stage, out_path, **kwargs)

    msg = ("Solaris AE Export: wrote {out_path}  |  {n_cams} camera(s), {n_lights} light(s), "
           "{n_nulls} null(s), {n_solids} solid(s), {n_footage} footage, frames {fr0}-{fr1} @ {fps:g} fps"
           ).format(out_path=summary["out_path"], n_cams=summary["n_cams"], n_lights=summary["n_lights"],
                    n_nulls=summary["n_nulls"], n_solids=summary["n_solids"], n_footage=summary["n_footage"],
                    fr0=summary["frame_range"][0], fr1=summary["frame_range"][1], fps=summary["fps"])
    print(msg)
    if hou.isUIAvailable():
        hou.ui.setStatusMessage(msg, severity=hou.severityType.Message)
'''


def _build_param_template_group():
    """Parameter UI, laid out like the bridge's AE Cam Link OBJ node."""
    import hou
    g = hou.ParmTemplateGroup()
    F = hou.FolderParmTemplate("solaris_ae_export", "Solaris AE Export", folder_type=hou.folderType.Simple)

    F.addParmTemplate(hou.StringParmTemplate(
        "file_path", "JSX File", 1, string_type=hou.stringParmType.FileReference,
        default_value=("$HIP/camlink/$OS.jsx",), file_type=hou.fileType.Any,
        tags={"filechooser_mode": "write", "filechooser_pattern": "*.jsx"},
        help="Keep this path stable: re-running the same file in AE updates the layers in place."))
    F.addParmTemplate(hou.StringParmTemplate(
        "comp_name", "Comp Name", 1, default_value=("",),
        help="Comp to update or create in AE. Empty = $HIPNAME."))
    F.addParmTemplate(hou.MenuParmTemplate(
        "comp_mode", "Target Comp", ("reuse", "new"),
        ("Update active comp (or comp by name, else create)", "Always create a new comp"),
        default_value=0,
        help="Layers are tagged with their USD path in the layer Comment, so re-running the JSX "
             "on the same comp replaces their keyframes instead of importing again."))
    F.addParmTemplate(hou.SeparatorParmTemplate("sep1"))
    F.addParmTemplate(hou.FloatParmTemplate(
        "world_scale", "World Scale", 1, default_value=(100.0,), min=0.0001, max=100000.0,
        help="AE pixels per USD unit. 100 matches the AE-side exporter's default for round-trips; "
             "AE Cam Link uses 1000 for Houdini-authored scenes."))
    F.addParmTemplate(hou.ToggleParmTemplate(
        "center_origin", "Origin At Comp Center", default_value=True,
        help="Put the USD world origin at the comp center (width/2, height/2). Ignored for stages "
             "that carry the AE-side exporter's AE_Scene wrapper, which already encodes the offset."))
    F.addParmTemplate(hou.SeparatorParmTemplate("sep2"))
    F.addParmTemplate(hou.ToggleParmTemplate(
        "range_toggle", "Use Frame Range", default_value=False,
        help="Off = the stage's start/end timecodes."))
    fr = hou.IntParmTemplate("frame_range", "Frame Range", 2, default_value=(1, 240),
                             default_expression=("$FSTART", "$FEND"),
                             naming_scheme=hou.parmNamingScheme.MinMax)
    fr.setConditional(hou.parmCondType.DisableWhen, "{ range_toggle == 0 }")
    F.addParmTemplate(fr)
    F.addParmTemplate(hou.MenuParmTemplate(
        "time_mode", "AE Time", ("stage", "range"),
        ("Stage time (timecode / fps)", "First exported frame at 0 s"), default_value=0,
        help="Stage time keeps AE round-trips identical (USD frame 0 = 0 s)."))
    F.addParmTemplate(hou.IntParmTemplate(
        "frame_offset", "Frame Offset", 1, default_value=(0,), min=-100, max=100,
        help="Shift all AE keys by this many frames (e.g. plate handles)."))
    F.addParmTemplate(hou.ToggleParmTemplate(
        "linear_keys", "Linear Keyframes", default_value=True,
        help="Set every written keyframe to linear interpolation."))
    F.addParmTemplate(hou.SeparatorParmTemplate("sep3"))

    CF = hou.FolderParmTemplate("comp_folder", "Comp Settings", folder_type=hou.folderType.Collapsible)
    CF.addParmTemplate(hou.IntParmTemplate(
        "comp_size", "Comp Size", 2, default_value=(1920, 1080), min=0, max=16384,
        naming_scheme=hou.parmNamingScheme.XYZW,
        help="Used when a comp is created. Height 0 = derived from the first camera's aperture ratio."))
    CF.addParmTemplate(hou.FloatParmTemplate(
        "fps", "FPS", 1, default_value=(0.0,), min=0.0, max=240.0,
        help="0 = stage metadata, else the hip's FPS."))
    CF.addParmTemplate(hou.FloatParmTemplate(
        "duration_override", "Comp Duration (s)", 1, default_value=(0.0,), min=0.0,
        help="0 = frame range / FPS."))
    CF.addParmTemplate(hou.ToggleParmTemplate(
        "unwrap_ae_scene", "Unwrap AE_Scene Wrapper", default_value=True,
        help="Strip the centre-comp parent the AE-side exporter adds, so round-trips stay identity."))
    F.addParmTemplate(CF)

    F.addParmTemplate(hou.ButtonParmTemplate(
        "run_script", "Export JSX",
        script_callback="hou.phm().export_jsx(kwargs['node'])",
        script_callback_language=hou.scriptLanguage.Python))
    g.append(F)
    return g


def install_hda(out_hda_path, icon_path=None):
    """Create the HDA file at `out_hda_path` and load it in this Houdini session.

    If `icon_path` is None, looks for `Gegenschuss.png` next to this
    script (or in the current working directory when run via exec) and
    embeds it as the HDA icon.  Pass an explicit path to override, or
    `False` to skip icon embedding entirely.
    """
    import hou

    out_hda_path = os.path.abspath(out_hda_path)
    out_dir = os.path.dirname(out_hda_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = os.getcwd()

    if icon_path is None:
        candidate = os.path.join(here, "Gegenschuss.png")
        if os.path.isfile(candidate):
            icon_path = candidate

    # Use a throwaway Python LOP as the seed -- gives us a working LOP node we
    # can promote to an HDA via createDigitalAsset.  Created in /stage and
    # deleted after.
    stage_root = hou.node("/stage")
    if stage_root is None:
        raise RuntimeError("/stage network not present.  Open a Houdini scene with Solaris support.")

    seed = stage_root.createNode("pythonscript", "ae_export_seed")
    try:
        # Promote to HDA: 1 input, 0 outputs (this is an exporter, not a stage modifier).
        hda_node = seed.createDigitalAsset(
            name=HDA_TYPE_NAME,
            hda_file_name=out_hda_path,
            description=HDA_LABEL,
            min_num_inputs=1,
            max_num_inputs=1,
            ignore_external_references=True,
            change_node_type=True,
            create_backup=False,
        )
        defn = hda_node.type().definition()
        # Wire up parameters and Python module.
        defn.setParmTemplateGroup(_build_param_template_group())
        defn.addSection("PythonModule", PYTHON_MODULE_TEMPLATE)
        defn.setExtraInfo(HDA_DESCRIPTION)
        # Embed the core Python module so the HDA is self-contained.  No
        # external file dependency at runtime; copy the .hda anywhere.
        module_py = os.path.join(here, "gegenschuss_solaris_ae_export.py")
        if os.path.isfile(module_py):
            with open(module_py, "r", encoding="utf-8") as f:
                defn.addSection("gegenschuss_solaris_ae_export.py", f.read())
        # Shared bridge files (../shared): Houdini-side conventions + AE runtime.
        shared = os.path.join(here, "..", "shared")
        for name in ("ae_convention.py", "ae_runtime.js"):
            with open(os.path.join(shared, name), "r", encoding="utf-8") as f:
                defn.addSection(name, f.read())
        # Embed the Gegenschuss logo as the HDA icon (shown in network
        # editor + tab menu).  Houdini looks up the icon section by its
        # filename (e.g. "icon.png" / "icon.svg"), not by an arbitrary
        # name -- a section named "Icon" is silently ignored and falls
        # back to the warning-triangle placeholder.
        if icon_path and os.path.isfile(icon_path):
            ext = os.path.splitext(icon_path)[1].lower()  # ".png" / ".svg"
            section_name = "icon" + ext
            with open(icon_path, "rb") as f:
                defn.addSection(section_name, f.read())
            defn.setIcon("opdef:.?" + section_name)
        # Make the PythonModule accessible via hou.phm() inside callbacks.
        opts = defn.options()
        opts.setSaveCachedCode(False)
        defn.setOptions(opts)
        defn.save(out_hda_path, hda_node, opts)
    finally:
        try:
            hda_node.destroy()
        except Exception:
            pass
        try:
            seed.destroy()
        except Exception:
            pass

    # Reinstall so the rest of the session can use it immediately.
    hou.hda.installFile(out_hda_path)
    return out_hda_path


if __name__ == "__main__":
    # Two execution paths:
    #   1. `hython install_hda.py /path/to/output.hda` -- argv[1] is the target.
    #   2. `exec(open(...).read())` from Houdini's Python console -- argv has
    #      whatever Houdini set; we must NOT call sys.exit() here, that
    #      terminates Houdini (silent crash).  Print a hint instead.
    import sys
    if len(sys.argv) >= 2 and sys.argv[1].endswith(".hda"):
        p = install_hda(sys.argv[1])
        print("Installed HDA: {}".format(p))
    else:
        print("install_hda.py loaded.  Call install_hda('/path/to/output.hda') "
              "to build the HDA.")
