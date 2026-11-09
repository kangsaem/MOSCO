"""v0.7.14 AE 보내기(.jsx) 점검 — 가짜 트랙 + (node 가 있으면) 가짜 에펙 객체로 스크립트를 실제 실행. CI 에서도 실행."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from mosco import aefx, analyze, edits  # noqa: E402
from mosco.project import Source  # noqa: E402
from test_edits import fake_det  # noqa: E402

# 가짜 에펙: 스크립트가 만드는 마스크·키·효과를 기록해 JSON 으로 출력
MOCK = r"""
var LOG = {masks: [], effects: [], alerts: [], dup: 0};
function CompItem() {}
function Shape() {}
var MaskMode = {ADD: "add", SUBTRACT: "sub"};
var KeyframeInterpolationType = {HOLD: "hold"};
function alert(s) { LOG.alerts.push(String(s)); }
function confirm(s) { LOG.alerts.push("confirm:" + s); return true; }
function Prop(name) { this.name = name; this.keys = []; this.value = null; this.numKeys = 0; this.interp = []; }
Prop.prototype.setValue = function (v) { this.value = v; };
Prop.prototype.setValueAtTime = function (t, v) { this.keys.push([t, v]); this.numKeys = this.keys.length; };
Prop.prototype.setInterpolationTypeAtKey = function (k, a, b) { this.interp.push([k, a, b]); };
function Mask() { this.props = {}; this.name = ""; this.maskMode = null; }
Mask.prototype.property = function (n) { if (!this.props[n]) { this.props[n] = new Prop(n); } return this.props[n]; };
function Effect(n) { this.match = n; this.p = [null, new Prop(1), new Prop(2)]; }
Effect.prototype.property = function (i) { return this.p[i]; };
function Layer() {
    this.source = {width: %W%, height: %H%, frameDuration: 1 / %FPS%};
    this.startTime = 0; this.stretch = 100; this.name = "src";
    var self = this;
    this.Masks = {addProperty: function (n) { var m = new Mask(); LOG.masks.push(m); return m; }};
    this.Effects = {addProperty: function (n) { if (n == "ADBE Gaussian Blur 2") { throw new Error("old AE"); }
                                               var e = new Effect(n); LOG.effects.push(e); return e; }};
}
Layer.prototype.duplicate = function () { LOG.dup++; var l = new Layer(); l.name = "dup"; return l; };
Layer.prototype.moveBefore = function () {};
var comp = new CompItem(); comp.selectedLayers = [new Layer()];
var app = {project: {activeItem: comp}, beginUndoGroup: function () {}, endUndoGroup: function () {}};
"""
DUMP = r"""
var out = {dup: LOG.dup, alerts: LOG.alerts, effects: [], masks: []};
for (var i = 0; i < LOG.effects.length; i++) { out.effects.push([LOG.effects[i].match, LOG.effects[i].p[1].value]); }
for (var i = 0; i < LOG.masks.length; i++) {
    var m = LOG.masks[i], sh = m.props["ADBE Mask Shape"], op = m.props["ADBE Mask Opacity"];
    out.masks.push({name: m.name, mode: m.maskMode, feather: m.props["ADBE Mask Feather"].value,
                    nkeys: sh.keys.length, t0: sh.keys[0][0], v0: sh.keys[0][1].vertices,
                    op: op.keys, hold: op.interp.length});
}
console.log(JSON.stringify(out));
"""


def info_of(n, w=1920, h=1080, fps=30000 / 1001):
    return SimpleNamespace(w=w, h=h, fps=fps, frames=n)


def reduce_check():
    t = np.arange(100, dtype=np.float64)
    lin = np.c_[t * 3, t * 2]
    assert aefx.reduce_keys(lin) == [0, 99], "직선 이동 = 키 2개"
    bend = np.c_[np.where(t < 50, t * 3, 150 - (t - 50) * 3), t * 0]
    k = aefx.reduce_keys(bend)
    assert 50 in k and len(k) <= 4, k
    assert 30 in aefx.reduce_keys(lin, must=[30]), "MOSCO 키 프레임은 남김"
    noise = lin + np.random.default_rng(1).normal(0, 0.3, lin.shape)
    assert len(aefx.reduce_keys(noise)) <= 6, "작은 떨림은 키를 거의 안 늘림"
    v, i, o = aefx.ellipse_shape(100, 50, 40, 20)
    assert np.allclose(v, [[100, 40], [120, 50], [100, 60], [80, 50]]), v
    assert np.allclose(o[0], [20 * aefx.KAPPA, 0]), o
    assert np.allclose(i, -o)
    v90, _, _ = aefx.ellipse_shape(0, 0, 40, 20, 90)
    assert np.allclose(v90[0], [10, 0], atol=1e-6), v90       # 90° 돌리면 위 꼭짓점 → 오른쪽
    print("reduce/shape: ok")


def export_check():
    z = fake_det()
    s = Source("x.mov", "blur")
    tr = analyze.blur_track(z, s.blur)
    r = edits.Resolver(s, tr)
    a = min(r.items(20), key=lambda it: it.ell[0])
    b_it = max(r.items(20), key=lambda it: it.ell[0])
    g = edits.ensure_group(s, a, 20)
    g["minus"] = True                                        # 제외 타원
    gb = edits.ensure_group(s, b_it, 20)
    edits.off_range(gb, 50, 59)                              # B 끄기 50~59 → 마스크 불투명도 0 구간
    edits.add_manual(s, 10, (1500, 700, 120, 160))           # 추가 타원(클립 처음~끝 존재)
    info = info_of(120)
    d = Path(tempfile.mkdtemp())
    out = aefx.export(s, info, tr, d / "t.jsx")
    txt = (d / "t.jsx").read_text(encoding="ascii")
    assert out["masks"] >= 3, out
    assert "MaskMode.SUBTRACT" in txt and "Run Script File" in txt
    assert "=>" not in txt and "let " not in txt and "JSON" not in txt, "구버전 ExtendScript 문법만"
    # 직선 이동 트랙(A)은 키가 아주 적어야 함
    data = aefx.collect(s, tr, 120)
    nseg = {k: len(aefx._segments(v["frames"])) for k, v in data.items()}
    print("masks:", {v["label"]: (len(v["frames"]), nseg[k]) for k, v in data.items()})
    node = shutil.which("node")
    if not node:
        print("export: ok (node 없음 → 실행 점검 건너뜀)")
        return
    mock = MOCK.replace("%W%", "1920").replace("%H%", "1080").replace("%FPS%", repr(info.fps))
    js = d / "run.js"
    js.write_text(mock + txt + DUMP, encoding="utf-8")
    res = json.loads(subprocess.run([node, str(js)], capture_output=True, text=True, check=True).stdout)
    assert res["dup"] == 1, res
    names = [m["name"] for m in res["masks"]]
    assert names[-1].startswith("Minus"), names               # 빼기 마스크는 맨 아래
    assert res["masks"][-1]["mode"] == "sub" and res["masks"][0]["mode"] == "add"
    add = next(m for m in res["masks"] if m["name"].startswith("Add"))
    assert add["t0"] == 0, add                               # 추가 타원 = 처음부터
    assert add["v0"][0] == [1500, 620], add["v0"]            # 위 꼭짓점 = cy - h/2
    for m in res["masks"]:
        assert m["nkeys"] < 40, (m["name"], m["nkeys"])      # 120f 중 줄인 키
        assert m["hold"] == len(m["op"]) and m["op"], m      # 나타남/사라짐 = 고정 키
    b = next(m for m in res["masks"] if m["name"].startswith("ID"))
    assert [round(t * info.fps) for t, v in b["op"] if v == 0] == [50], b      # 끈 구간 시작 = 0
    assert [round(t * info.fps) for t, v in b["op"] if v == 100] == [0, 60], b  # 다시 켜짐
    assert res["effects"] and res["effects"][0][0] == "ADBE Gaussian Blur" and res["effects"][0][1] > 0, res
    print("export (가짜 에펙 실행): ok", {m["name"]: m["nkeys"] for m in res["masks"]})


def sign_check():
    """간판: 4꼭짓점 키 그대로(접선 0)."""
    s = Source("s.mov", "sign")
    edits.add_manual(s, 0, (100, 100, 300, 120))
    g = s.edits["sign"]["groups"][0]
    edits.set_key(g, 30, [60, 50, 380, 70, 370, 170, 70, 150])
    info = info_of(60)
    data = aefx.collect(s, None, 60)
    d = next(iter(data.values()))
    assert d["quad"] and len(d["frames"]) == 60
    txt = aefx.build_jsx(s, info, data)
    assert "[[60,50],[380,70],[370,170],[70,150]],[[0,0],[0,0],[0,0],[0,0]]" in txt.replace(" ", ""), txt[-600:]
    print("sign: ok")


if __name__ == "__main__":
    reduce_check()
    export_check()
    sign_check()
    print("AEFX OK")
    sys.stdout.flush()
    os._exit(0)
