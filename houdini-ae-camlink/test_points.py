import hou, json, sys
S = sys.argv[1]
hou.hda.installFile(S + "/build/gegenschuss_ae_camlink.hdalc")
hou.setFps(25); hou.playbar.setFrameRange(1, 4); hou.playbar.setPlaybackRange(1, 4)
obj = hou.node("/obj")
cam = obj.createNode("cam", "cam1"); n1 = obj.createNode("null", "n1"); n2 = obj.createNode("null", "n2"); n2.parm("tx").set(3)
geo = obj.createNode("geo", "markers"); geo.parm("ty").setExpression("$F")
grid = geo.createNode("grid"); grid.parm("rows").set(2); grid.parm("cols").set(3)
nm = geo.createNode("name"); nm.setInput(0, grid); nm.parm("class").set("point"); nm.parm("name1").set("mk_`@ptnum`"); nm.setDisplayFlag(True); nm.setRenderFlag(True)
exp = obj.createNode("gegenschuss::ae_camlink::1.0", "ae_camlink")
exp.setInput(0, cam); exp.parm("objects").set("../n1 ../n2")
print("inputs wired:", [n.name() for n in exp.inputs()], " max inputs:", exp.type().maxNumInputs())
exp.parm("points_enable").set(1); exp.parm("point_objects").set("../markers"); exp.parm("point_limit").set(5)
exp.parm("file_path").set(S + "/build/out/points_test.jsx")
hou.setFrame(2)
path = exp.hm().export_jsx({"node": exp})
txt = open(path).read()
data = json.loads(txt[txt.index("var DATA = ") + len("var DATA = "): txt.index(";\n", txt.index("var DATA = "))])
def v(ch, i): return ch["v"] if "v" in ch else ch["k"][i]
for L in data["layers"]:
    print("%-12s %-7s %s  pos f1 %s  f4 %s" % (L["name"], L["type"], L["id"], v(L["pos"], 0), v(L["pos"], 3)))
hou.setFrame(1); g = nm.geometry(); p0 = g.iterPoints()[0].position() * geo.worldTransform()
print("truth pt0 f1:", [round(p0[0]*1000,1), round(-p0[1]*1000,1), round(-p0[2]*1000,1)], " frame restored:", hou.frame())
