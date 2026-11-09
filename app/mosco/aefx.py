"""v0.7.14 AE 보내기: 타원·간판 사각형 트랙 → After Effects 스크립트(.jsx).

렌더 대신 '편집 가능한' 형태로 보냄 — 에펙에서 [파일 › 스크립트 › 스크립트 파일 실행] 하면 선택한 레이어를 복제해
마스크(타원 = 4점 베지어, 간판 = 4점) + 마스크 모양 키프레임을 만든다.
- 키: 오차(꼭짓점 위치)가 TOL px 넘는 곳에만(줄인 키, Ramer–Douglas–Peucker). 마스크 모양은 에펙에서 꼭짓점별 선형 보간.
- 나타남/사라짐: 마스크 불투명도 고정(HOLD) 키 100/0.
- 제외 타원 = 빼기(Subtract) 마스크(맨 아래). 페더 = 에펙 마스크 페더(근사: 가우시안 σ × 2.5, 아이템별 중앙값).
- 블러/간판: 복제 레이어에 가우시안 블러(또는 모자이크) 효과 — 세기는 근사값(에펙에서 조절).
- 뷰티: 마스크만(MOSCO 피부보정은 에펙에 없음 → 에펙 쪽 효과와 같이 씀).
- 구버전 에펙(ExtendScript ES3)용: JSON·let·화살표 함수 안 씀, 글자는 ASCII 만.
"""
from __future__ import annotations

import math

import numpy as np

from . import edits, fx

TOL = 1.5          # 줄인 키 허용 오차(px)
KAPPA = 0.5522847498   # 원 → 4점 베지어 핸들 길이
AE_FEATHER = 2.5   # MOSCO 페더(가우시안 σ) → 에펙 마스크 페더 폭(근사)


# ------------------------------------------------------------ 모양
def ellipse_shape(cx, cy, w, h, ang=0.0):
    """→ (verts(4,2), in(4,2), out(4,2)) 위·오른쪽·아래·왼쪽 순(시계 방향). ang = 도(cv2 와 같은 방향, y 아래)."""
    rx, ry = w / 2.0, h / 2.0
    v = np.array([[0, -ry], [rx, 0], [0, ry], [-rx, 0]], np.float64)
    kx, ky = KAPPA * rx, KAPPA * ry
    o = np.array([[kx, 0], [0, ky], [-kx, 0], [0, -ky]], np.float64)
    a = math.radians(ang or 0.0)
    if a:
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        v, o = v @ R.T, o @ R.T
    return v + [cx, cy], -o, o


def quad_shape(pts):
    q = np.asarray(pts, np.float64).reshape(4, 2)
    z = np.zeros((4, 2))
    return q, z, z


# ------------------------------------------------------------ 줄인 키
def reduce_keys(vals, tol=TOL, must=()):
    """vals (n, d) 프레임별 값(연속 구간). 선형 보간 오차 ≤ tol 이 되게 고른 키 인덱스(처음·끝·must 포함)."""
    n = len(vals)
    if n <= 2:
        return list(range(n))
    vals = np.asarray(vals, np.float64)
    keep = {0, n - 1, *[m for m in must if 0 <= m < n]}
    anchors = sorted(keep)
    stack = list(zip(anchors[:-1], anchors[1:]))
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        t = (np.arange(a + 1, b) - a) / (b - a)
        lin = vals[a] + (vals[b] - vals[a]) * t[:, None]
        err = np.abs(vals[a + 1:b] - lin).max(1)
        j = int(err.argmax())
        if err[j] > tol:
            m = a + 1 + j
            keep.add(m)
            stack += [(a, m), (m, b)]
    return sorted(keep)


# ------------------------------------------------------------ 트랙 → 아이템별 프레임 값
def collect(src, track, n, progress=None, cancel=None, mode=None):
    """→ {key: {"label", "minus", "quad", "frames": [f], "shape": [(v,i,o)], "feather": [px], "keyed": set, "sigma": [px]}}"""
    mode = mode or src.mode
    r = edits.Resolver(src, track, mode)
    p = src.params_for(mode)
    out = {}
    for i in range(n):
        if cancel is not None and cancel.is_set():
            from .analyze import Cancelled
            raise Cancelled()
        for it in r.items(i):
            quad = fx._is_quad(it)
            fe = it.ov.get("feather", p.get("feather", 10))
            if quad:
                from .sign import quad_ell
                q = fx.item_quad(it, p)
                shp = quad_shape(q)
                ell = quad_ell(q)
                size = min(ell[2], ell[3])
                sig = size * fe / 100 / 2
            else:
                ell = fx._item_ell(it, p)
                shp = ellipse_shape(*ell)
                size = max(ell[2], ell[3])
                sig = size * fe / 100 / 2
            d = out.get(it.key)
            if d is None:
                if it.minus:
                    lab = f"Minus {it.tid if it.tid is not None else 'Add ' + str(it.gid)}"
                elif it.tid is not None:
                    lab = f"ID {it.tid}"
                else:
                    lab = f"Add {it.gid}"
                d = out[it.key] = {"label": lab, "minus": bool(it.minus), "quad": quad, "frames": [], "shape": [],
                                   "feather": [], "keyed": set(), "blur": [], "block": []}
            d["frames"].append(i)
            d["shape"].append(shp)
            d["feather"].append(sig)
            if it.keyed:
                d["keyed"].add(i)
            if mode != "beauty" and not it.minus:
                st = max(1, it.ov.get("strength", p.get("strength", 70))) / 100
                d["blur"].append(size * st * (0.25 if quad else 0.09))     # fx.blur 와 같은 σ
                d["block"].append(max(3, int(size * st * (0.3 if quad else 0.14))))   # fx.blur 모자이크 블록(px)
        if progress and i % 50 == 0:
            progress(0.9 * i / max(1, n))
    return out


def _segments(frames):
    segs, a = [], 0
    for k in range(1, len(frames) + 1):
        if k == len(frames) or frames[k] != frames[k - 1] + 1:
            segs.append((a, k))
            a = k
    return segs


def _f(x):
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pts(a):
    return "[" + ",".join(f"[{_f(x)},{_f(y)}]" for x, y in a) + "]"


# ------------------------------------------------------------ .jsx
def build_jsx(src, info, data, tol=TOL):
    """data = collect() 결과 → 스크립트 글(ASCII)."""
    p = src.params()
    W, H = int(info.w), int(info.h)
    fps = float(info.fps)
    masks = []
    blur_sig, blocks, nkeys = [], [], 0
    order = sorted(data.values(), key=lambda d: (d["minus"], d["label"]))      # 빼기 마스크는 맨 아래
    for d in order:
        frames = d["frames"]
        keys, opac = [], []
        for a, b in _segments(frames):
            vec = np.array([np.r_[s[0].ravel()] for s in d["shape"][a:b]])
            must = [k - a for k in range(a, b) if frames[k] in d["keyed"]]
            for k in reduce_keys(vec, tol, must):
                keys.append(a + k)
            opac.append((frames[a], 100))
            if frames[b - 1] + 1 < info.frames:
                opac.append((frames[b - 1] + 1, 0))
        if frames and frames[0] > 0:
            opac.insert(0, (0, 0))
        kf = ",".join(f"[{frames[k]},{_pts(d['shape'][k][0])},{_pts(d['shape'][k][1])},{_pts(d['shape'][k][2])}]"
                      for k in keys)
        feather = float(np.median(d["feather"])) * AE_FEATHER if d["feather"] else 0.0
        masks.append(f'{{name:"{d["label"]}",minus:{str(d["minus"]).lower()},feather:{_f(feather)},'
                     f'keys:[{kf}],op:[{",".join(f"[{f},{v}]" for f, v in opac)}]}}')
        blur_sig += d["blur"]
        blocks += d["block"]
        nkeys += len(keys)
    mode = src.mode
    kind = p.get("kind", "blur") if mode != "beauty" else "none"
    sig = float(np.median(blur_sig)) if blur_sig else 0.0
    # 에펙 가우시안 '흐림' 값 ≈ σ×2(근사), 모자이크 = 블록 수(MOSCO 블록 크기 기준)
    blurriness = round(sig * 2, 1)
    blk = int(np.median(blocks)) if (kind == "mosaic" and blocks) else 0
    head = (f"// MOSCO {__import__('mosco').__version__} -> After Effects\n"
            f"// source: {_ascii(src.name)}  {W}x{H}  {fps:.3f}fps  {info.frames}f  mode={mode}\n"
            "// AE: File > Scripts > Run Script File... (select the source layer in the comp first)\n")
    return head + _TEMPLATE.replace("%DATA%", (
        f'var MOSCO = {{w:{W},h:{H},fps:{fps:.6f},frames:{int(info.frames)},mode:"{mode}",kind:"{kind}",'
        f'blurriness:{_f(blurriness)},blockpx:{blk},masks:[\n' + ",\n".join(masks) + "\n]};")) + f"// ~{nkeys} keys\n"


def _ascii(s):
    return "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in s)


def export(src, info, track, path, progress=None, cancel=None, tol=TOL):
    """트랙 → .jsx 저장. track = 단일 트랙(활성 모드) 또는 [(track, mode), ...](동시 블러). 반환 {"masks", "path"}."""
    pairs = track if isinstance(track, list) else [(track, src.mode)]
    n = int(info.frames)
    data = {}
    for tr, mode in pairs:
        d = collect(src, tr, n, progress, cancel, mode=mode)
        for k, v in d.items():
            data[(mode, k)] = v          # 종류별로 키 분리(얼굴·간판 키 겹침 방지)
    txt = build_jsx(src, info, data, tol)
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(txt)
    if progress:
        progress(1.0)
    return {"masks": len(data), "path": str(path)}


# 구버전 ExtendScript(ES3) — JSON·let·화살표 함수·Array.forEach 안 씀
_TEMPLATE = r"""
(function () {
%DATA%
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
"""
