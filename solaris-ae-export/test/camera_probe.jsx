/**
 * Camera probe -- diagnostic for the 1-node-vs-2-node round-trip drift.
 *
 * Creates pairs of cameras (and parallel lights) in a fresh AE comp.
 * Each pair is set up to point in the SAME direction:
 *   <name>_1NODE   -- autoOrient=NO_AUTO_ORIENT, explicit Euler rotation
 *   <name>_2NODE   -- autoOrient=CAMERA_OR_POINT_OF_INTEREST, POI = pos + fwd*1000
 *
 * Where fwd is computed from the same Euler rotation using
 * aeRotMatrix(0, 0, 0, xr, yr, zr) * (0, 0, 1).  If AE's internal
 * lookAt matches the forward exporter's lookAtMatrix(), the world
 * matrix of each pair should be IDENTICAL when written to USD.  Any
 * difference exposes the convention mismatch driving the visual drift
 * we see on round-trip.
 *
 * USAGE
 *   1. File > Scripts > Run Script File... -> pick this file.
 *      Creates the comp + cameras.  Leaves the comp open.
 *   2. Run the GegenschussAeUsdExporter on this comp.  Save as .usda.
 *   3. Send me the .usda.  I'll diff each *_1NODE vs *_2NODE matrix.
 */

(function cameraProbe() {

    var COMP_W = 1920, COMP_H = 1080, FPS = 25, DUR = 5;
    var POI_DISTANCE = 1000;

    var comp = app.project.items.addComp("camera_probe", COMP_W, COMP_H, 1, DUR, FPS);
    comp.openInViewer();

    function rad(d) { return d * Math.PI / 180; }

    /**
     * fwd = aeRotMatrix(0, 0, 0, xr, yr, zr) * [0, 0, 1]
     * Matches the forward exporter's `aeRotMatrix` when Orientation = (0,0,0)
     * and individual rotations compose X*Y*Z (Z innermost, measured in AE 26).
     */
    function fwdFromEuler(xr, yr, zr) {
        // Third column of Rx*Ry*Rz (AE's measured channel composition, Z innermost).
        var cx = Math.cos(rad(xr)), sx = Math.sin(rad(xr));
        var cy = Math.cos(rad(yr)), sy = Math.sin(rad(yr));
        return [
            sy,          // X
            -sx * cy,    // Y  (AE Y goes down)
            cx * cy      // Z
        ];
    }

    var cx = COMP_W / 2, cy = COMP_H / 2;

    // Each case: position + Euler.  POI for the 2-node twin is derived
    // from the same Euler so both should resolve to the same look dir.
    var cases = [
        { name: "FACE_PZ",  pos: [cx, cy, -1000], rot: [0,   0,   0] },  // looking down +Z
        { name: "FACE_PX",  pos: [cx, cy,     0], rot: [0, -90,   0] },  // looking down +X
        { name: "PITCH_30", pos: [cx, cy, -1000], rot: [-30, 0,   0] },  // tilt up 30°
        { name: "YAW_45",   pos: [cx, cy, -1000], rot: [0,  45,   0] },  // pan right 45°
        { name: "ROLL_20",  pos: [cx, cy, -1000], rot: [0,   0,  20] }   // roll 20° (tests roll loss)
    ];

    function makeCameraPair(c) {
        // 1-node: explicit rotation channels.
        var cam1 = comp.layers.addCamera(c.name + "_1NODE", [cx, cy]);
        cam1.autoOrient = AutoOrientType.NO_AUTO_ORIENT;
        cam1.transform.position.setValue(c.pos);
        cam1.transform.xRotation.setValue(c.rot[0]);
        cam1.transform.yRotation.setValue(c.rot[1]);
        cam1.transform.zRotation.setValue(c.rot[2]);

        // 2-node: POI driven, autoOrient stays at default
        // CAMERA_OR_POINT_OF_INTEREST.
        var fwd = fwdFromEuler(c.rot[0], c.rot[1], c.rot[2]);
        var poi = [
            c.pos[0] + fwd[0] * POI_DISTANCE,
            c.pos[1] + fwd[1] * POI_DISTANCE,
            c.pos[2] + fwd[2] * POI_DISTANCE
        ];
        var cam2 = comp.layers.addCamera(c.name + "_2NODE", [cx, cy]);
        cam2.transform.position.setValue(c.pos);
        cam2.transform.pointOfInterest.setValue(poi);
    }

    function makeLightPair(c) {
        // Parallel light pair -- same logic as cameras.  If the camera
        // pair matches but the light pair doesn't (or vice versa), the
        // bug is layer-type-specific.
        var l1 = comp.layers.addLight(c.name + "_LIGHT_1NODE", [cx, cy]);
        l1.lightType = LightType.PARALLEL;
        l1.autoOrient = AutoOrientType.NO_AUTO_ORIENT;
        l1.transform.position.setValue(c.pos);
        l1.transform.xRotation.setValue(c.rot[0]);
        l1.transform.yRotation.setValue(c.rot[1]);
        l1.transform.zRotation.setValue(c.rot[2]);

        var fwd = fwdFromEuler(c.rot[0], c.rot[1], c.rot[2]);
        var poi = [
            c.pos[0] + fwd[0] * POI_DISTANCE,
            c.pos[1] + fwd[1] * POI_DISTANCE,
            c.pos[2] + fwd[2] * POI_DISTANCE
        ];
        var l2 = comp.layers.addLight(c.name + "_LIGHT_2NODE", [cx, cy]);
        l2.lightType = LightType.PARALLEL;
        l2.transform.position.setValue(c.pos);
        l2.transform.pointOfInterest.setValue(poi);
    }

    for (var i = 0; i < cases.length; i++) {
        // Try-catch so a hidden-property error on one light doesn't
        // abort the whole script.
        try { makeCameraPair(cases[i]); } catch (eC) {}
        try { makeLightPair(cases[i]);  } catch (eL) {}
    }

    alert(
        "Created " + (cases.length * 4) + " probe layers in '" + comp.name + "':\n" +
        cases.length + " camera pairs (1NODE / 2NODE) + " + cases.length + " light pairs.\n\n" +
        "Now run GegenschussAeUsdExporter on this comp and save as .usda.\n" +
        "Send the .usda back -- I'll diff each *_1NODE vs *_2NODE matrix to\n" +
        "find the convention mismatch."
    );
})();
