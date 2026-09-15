import hou, json, math, sys
S = sys.argv[1]
hou.hda.installFile(S + "/build/gegenschuss_ae_camlink.hdalc")
hou.setFps(25); hou.playbar.setFrameRange(1001, 1010); hou.playbar.setPlaybackRange(1001, 1010)
obj = hou.node("/obj")
cam = obj.createNode("cam", "shotcam")
cam.parmTuple("t").set((2.0, 1.5, 6.0))
for i, p in enumerate(("rx", "ry", "rz")):
    cam.parm(p).setExpression("%d + ($F-1001)*%d" % ((-20, 35, 10)[i], (i+1)*3))
cam.parm("focal").setExpression("35 + ($F-1001)")
cam.parmTuple("res").set((2048, 858))
loc = obj.createNode("null", "tracker")
loc.parmTuple("t").set((-1, 0.5, 0)); loc.parm("tx").setExpression("-1 + ($F-1001)*0.1")
static = obj.createNode("null", "static_null"); static.parmTuple("t").set((0.3, 0.2, 0.1))
exp = obj.createNode("gegenschuss::ae_camlink::1.0", "ae_camlink")
exp.setInput(0, cam)
loc.setSelected(True, clear_all_selected=True); static.setSelected(True)
exp.hm().add_selected({"node": exp})
print("objects parm:", exp.parm("objects").evalAsString())
exp.parm("file_path").set(S + "/build/out/$OS.jsx")
path = exp.hm().export_jsx({"node": exp})
txt = open(path).read()
data = json.loads(txt[txt.index("var DATA = ") + len("var DATA = "): txt.index(";\n", txt.index("var DATA = "))])
print("comp", data["comp"], data["width"], data["height"], data["fps"], data["start"], data["end"], "times[:3]", data["times"][:3])
for L in data["layers"]:
    print(L["name"], L["type"], {k: ("static" if "v" in v else "keys%d" % len(v["k"])) for k, v in L.items() if isinstance(v, dict)})

# --- verify: AE matrix Rz*Ry*Rx from exported channels == S * Rhou^T * S
def rx(a):
    a=math.radians(a); c,s=math.cos(a),math.sin(a); return [[1,0,0],[0,c,-s],[0,s,c]]
def ry(a):
    a=math.radians(a); c,s=math.cos(a),math.sin(a); return [[c,0,s],[0,1,0],[-s,0,c]]
def rz(a):
    a=math.radians(a); c,s=math.cos(a),math.sin(a); return [[c,-s,0],[s,c,0],[0,0,1]]
def mm(A,B): return [[sum(A[i][k]*B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
def val(ch, i): return ch["v"] if "v" in ch else ch["k"][i]
L = data["layers"][0]; maxerr = 0
for i, f in enumerate(range(data["start"], data["end"] + 1)):
    m = cam.worldTransformAtTime(hou.frameToTime(f)).asTuple()
    Rh = [[m[r*4+c] for c in range(3)] for r in range(3)]
    Sg = [[1,0,0],[0,-1,0],[0,0,-1]]
    RT = [[Rh[j][i2] for j in range(3)] for i2 in range(3)]
    Rae_expected = mm(Sg, mm(RT, Sg))
    order = exp.parm("rot_order").evalAsString()
    R = {"x": rx(val(L["rx"], i)), "y": ry(val(L["ry"], i)), "z": rz(val(L["rz"], i))}
    Rae = mm(R[order[2]], mm(R[order[1]], R[order[0]]))   # column form: first axis innermost
    maxerr = max(maxerr, max(abs(Rae[a][b]-Rae_expected[a][b]) for a in range(3) for b in range(3)))
    p = val(L["pos"], i); t = hou.frameToTime(f)
    exp_pos = [m[12]*1000, -m[13]*1000, -m[14]*1000]
    maxerr = max(maxerr, max(abs(p[k]-exp_pos[k]) for k in range(3)) / 1000.0)
    z = val(L["zoom"], i); exp_z = cam.parm("focal").evalAtTime(t) / cam.parm("aperture").evalAtTime(t)
    maxerr = max(maxerr, abs(z - exp_z))
print("max error camera (rotation matrix / position / zoom ratio): %.2e" % maxerr)
print("zoom px at 2048 wide, frame 1001: %.1f" % (val(L["zoom"], 0) * 2048))
print("jsx size: %d bytes" % len(txt))
for i in range(10): print("  f%d rot %7.2f %7.2f %7.2f" % (1001+i, val(L["rx"],i), val(L["ry"],i), val(L["rz"],i)))
