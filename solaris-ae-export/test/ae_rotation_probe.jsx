// AE Cam Link rotation probe
// Run in After Effects: File > Scripts > Run Script File...
// Builds a temporary comp, sets a 3D null and a camera to a series of
// X/Y/Z Rotation values, asks AE for the resulting world axes through
// toWorldVec() expressions, and writes the answers to
// camlink_probe_result.txt next to this script.  The temporary comp is
// removed afterwards.  Nothing else in the project is touched.

(function camlinkProbe() {
    var cases = [
        [30, 0, 0], [0, 30, 0], [0, 0, 30],
        [30, 40, 0], [30, 0, 40], [0, 30, 40],
        [30, 40, 50], [-20, 35, 10], [45.44, 106.61, 46.66], [90, 102, 90]
    ];
    var axes = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    var lines = ["AE " + app.version + "  rotation probe  " + new Date().toString(), ""];

    app.beginUndoGroup("AE Cam Link probe");
    var comp = app.project.items.addComp("camlink_probe_tmp", 1920, 1080, 1, 1, 25);
    var reader = comp.layers.addNull(); reader.name = "reader"; reader.threeDLayer = true;

    function probeLayer(layer, label) {
        var xf = layer.property("ADBE Transform Group");
        for (var c = 0; c < cases.length; c++) {
            var r = cases[c];
            xf.property("ADBE Orientation").setValue([0, 0, 0]);
            xf.property("ADBE Rotate X").setValue(r[0]);
            xf.property("ADBE Rotate Y").setValue(r[1]);
            xf.property("ADBE Rotate Z").setValue(r[2]);
            var row = label + " rot " + r.join(",") + " ->";
            for (var a = 0; a < 3; a++) {
                reader.property("ADBE Transform Group").property("ADBE Position").expression =
                    'thisComp.layer("' + layer.name + '").toWorldVec([' + axes[a].join(",") + '])';
                var v = reader.property("ADBE Transform Group").property("ADBE Position").valueAtTime(0, false);
                row += " axis" + "XYZ".charAt(a) + " [" + (Math.round(v[0] * 1e5) / 1e5) + "," +
                       (Math.round(v[1] * 1e5) / 1e5) + "," + (Math.round(v[2] * 1e5) / 1e5) + "]";
            }
            lines.push(row);
        }
        // Orientation only, for completeness
        xf.property("ADBE Rotate X").setValue(0); xf.property("ADBE Rotate Y").setValue(0); xf.property("ADBE Rotate Z").setValue(0);
        xf.property("ADBE Orientation").setValue([30, 40, 50]);
        var row2 = label + " orientation 30,40,50 ->";
        for (var b = 0; b < 3; b++) {
            reader.property("ADBE Transform Group").property("ADBE Position").expression =
                'thisComp.layer("' + layer.name + '").toWorldVec([' + axes[b].join(",") + '])';
            var w = reader.property("ADBE Transform Group").property("ADBE Position").valueAtTime(0, false);
            row2 += " axis" + "XYZ".charAt(b) + " [" + (Math.round(w[0] * 1e5) / 1e5) + "," +
                    (Math.round(w[1] * 1e5) / 1e5) + "," + (Math.round(w[2] * 1e5) / 1e5) + "]";
        }
        lines.push(row2);
        lines.push("");
    }

    var nul = comp.layers.addNull(); nul.name = "probe_null"; nul.threeDLayer = true;
    probeLayer(nul, "NULL  ");
    var cam = comp.layers.addCamera("probe_cam", [960, 540]);
    cam.autoOrient = AutoOrientType.NO_AUTO_ORIENT;
    probeLayer(cam, "CAMERA");

    comp.remove();
    app.endUndoGroup();

    var text = lines.join("\n");
    var outFile = new File(File($.fileName).parent.fsName + "/camlink_probe_result.txt");
    outFile.encoding = "UTF-8";
    if (outFile.open("w")) { outFile.write(text); outFile.close(); }
    alert("AE Cam Link probe done.\nWritten to:\n" + outFile.fsName + "\n\n" + text.substring(0, 1200) + "\n...");
})();
