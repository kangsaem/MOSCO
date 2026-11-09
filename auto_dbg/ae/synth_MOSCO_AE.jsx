// MOSCO 0.7.14 -> After Effects
// source: synth  1920x1080  29.970fps  60f  mode=beauty
// AE: File > Scripts > Run Script File... (select the source layer in the comp first)

(function () {
var MOSCO = {w:1920,h:1080,fps:29.970030,frames:60,mode:"beauty",kind:"none",blurriness:0,blockpx:0,masks:[

]};
    function makeShape(k) {
        var s = new Shape();
        s.vertices = k[1];
        s.inTangents = k[2];
        s.outTangents = k[3];
        s.closed = true;
        return s;
    }
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) { alert("MOSCO: open the comp and select the source layer."); return; }
    if (comp.selectedLayers.length != 1) { alert("MOSCO: select exactly one source layer."); return; }
    var src = comp.selectedLayers[0];
    if (!src.source || src.source.width != MOSCO.w || src.source.height != MOSCO.h) {
        if (!confirm("MOSCO: layer size is not " + MOSCO.w + "x" + MOSCO.h + ". Continue?")) { return; }
    }
    if (src.stretch != 100) { alert("MOSCO: layer time stretch is not 100% - keys may not line up."); }
    var fd = (src.source && src.source.frameDuration) ? src.source.frameDuration : 1 / MOSCO.fps;
    app.beginUndoGroup("MOSCO masks");
    var lay = src.duplicate();
    lay.name = "MOSCO " + MOSCO.mode;
    lay.moveBefore(src);
    var i, j, m, mk, shp, op;
    for (i = 0; i < MOSCO.masks.length; i++) {
        m = MOSCO.masks[i];
        mk = lay.Masks.addProperty("ADBE Mask Atom");
        mk.name = m.name;
        mk.maskMode = m.minus ? MaskMode.SUBTRACT : MaskMode.ADD;
        mk.property("ADBE Mask Feather").setValue([m.feather, m.feather]);
        shp = mk.property("ADBE Mask Shape");
        for (j = 0; j < m.keys.length; j++) {
            shp.setValueAtTime(src.startTime + m.keys[j][0] * fd, makeShape(m.keys[j]));
        }
        op = mk.property("ADBE Mask Opacity");
        for (j = 0; j < m.op.length; j++) {
            op.setValueAtTime(src.startTime + m.op[j][0] * fd, m.op[j][1]);
        }
        for (j = 1; j <= op.numKeys; j++) {
            op.setInterpolationTypeAtKey(j, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
        }
    }
    if (MOSCO.kind == "blur") {
        var fxb = null;
        try { fxb = lay.Effects.addProperty("ADBE Gaussian Blur 2"); } catch (e1) { fxb = null; }
        if (!fxb) { try { fxb = lay.Effects.addProperty("ADBE Gaussian Blur"); } catch (e2) { fxb = null; } }
        if (fxb) { try { fxb.property(1).setValue(MOSCO.blurriness); } catch (e3) {} }
    } else if (MOSCO.kind == "mosaic") {
        try {
            var fxm = lay.Effects.addProperty("ADBE Mosaic");
            var bw = Math.max(1, Math.round(MOSCO.w / Math.max(3, MOSCO.blockpx)));
            var bh = Math.max(1, Math.round(MOSCO.h / Math.max(3, MOSCO.blockpx)));
            fxm.property(1).setValue(bw);
            fxm.property(2).setValue(bh);
        } catch (e4) {}
    }
    app.endUndoGroup();
    alert("MOSCO: " + MOSCO.masks.length + " masks added to layer '" + lay.name + "'.");
})();
// ~0 keys
