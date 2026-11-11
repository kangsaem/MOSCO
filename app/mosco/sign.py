"""간판 블러(v0.7.8): 글자 검출(PP-OCRv4 DB) → 간판별 4꼭짓점 사각형(직각 아님, 원근 따라감) → 추적.

- 검출: 글자줄(DB 코어 윤곽) → 윗/아랫 경계에 직선(원근으로 기울기 달라도 됨) → 4꼭짓점
- 같은 간판의 여러 줄: 글자 크기가 비슷하고 (글자 높이 60%) 키운 사각형이 겹치면 하나로.
  옆변 = 줄 끝이 가지런한 쪽의 기울기(줄 길이 차이를 기울기로 착각하지 않게), 둘 다 들쭉날쭉하면 수직
- 여유: 트랙 단계에서 네 변을 바깥으로 평행 이동(글자 높이 × 여유%) → 각도 유지
- 추적: 얼굴 블러와 같은 방식(_greedy_tracks·_link·_dedupe). 위치 = [떨림 보정], 모양(중심 기준 꼭짓점) = 더 강하게 평균
분석 행: [f, x0, y0, x1, y1, score, kind=3, hist(16), quad(8), 글자높이]
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import cv2
import numpy as np

from . import analyze as A
from . import ff, models

KIND_TEXT = 3
QOFF = 7 + A.HIST          # 행에서 quad 시작 열
DET_SIDE = 960              # 검출 입력 긴 변(px) — FHD 이하 기본


def det_side(info) -> int:
    """v0.7.55 원본 해상도에 맞춘 검출 크기: 긴 변의 절반(32 배수), 960~1920. FHD=960(그대로), 4K=1920.
    큰 원본의 작은·먼 간판을 놓치지 않게. 분석 시간은 면적에 비례(4K ≈ 4배)."""
    long = max(int(info.w), int(info.h))
    return int(min(1920, max(DET_SIDE, round(long / 2 / 32) * 32)))
MERGE_MARGIN = 0.6          # 줄 합치기 기준(글자 높이 배)

_lock = threading.Lock()
_det = None


class TextDetector:
    def __init__(self):
        self.s = models._sess("text_det_ppocrv4.onnx")
        self.inp = self.s.get_inputs()[0].name

    def prob(self, rgb, side=DET_SIDE):
        H, W = rgb.shape[:2]
        s = side / max(H, W)
        h, w = max(32, int(round(H * s / 32)) * 32), max(32, int(round(W * s / 32)) * 32)
        x = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        x = (x - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        pm = self.s.run(None, {self.inp: x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
        return pm, W / w, H / h


def detector() -> TextDetector:
    global _det
    with _lock:
        if _det is None:
            _det = TextDetector()
        return _det


def reset():
    global _det
    with _lock:
        _det = None


# ------------------------------------------------------------ 기하
def grow(q, d):
    """사각형 네 변을 바깥으로 d 만큼 평행 이동 → 새 꼭짓점(각도 유지)."""
    q = np.asarray(q, np.float64).reshape(4, 2)
    if d == 0:
        return q.astype(np.float32)
    c = q.mean(0)
    L = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        e = b - a
        n = np.array([e[1], -e[0]]) / max(1e-6, float(np.hypot(*e)))
        if np.dot(n, (a + b) / 2 - c) < 0:
            n = -n
        L.append((a + n * d, b + n * d))
    out = []
    for i in range(4):
        (a, b), (c2, d2) = L[i - 1], L[i]
        r, s = b - a, d2 - c2
        den = r[0] * s[1] - r[1] * s[0]
        t = ((c2 - a)[0] * s[1] - (c2 - a)[1] * s[0]) / den if abs(den) > 1e-9 else 1.0
        out.append(a + t * r)
    return np.array(out, np.float32)


def quad_ell(q):
    """사각형 → 표시·선택·키용 상자 타원 (cx, cy, w, h, 0)."""
    q = np.asarray(q, np.float32).reshape(4, 2)
    x0, y0 = q.min(0)
    x1, y1 = q.max(0)
    return (float((x0 + x1) / 2), float((y0 + y1) / 2), float(x1 - x0), float(y1 - y0), 0.0)


def rect_quad(cx, cy, w, h):
    return np.array([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2],
                     [cx - w / 2, cy + h / 2]], np.float32)


def scale_quad(q, s):
    q = np.asarray(q, np.float32).reshape(4, 2)
    c = q.mean(0)
    return c + (q - c) * s


def _fit(us, vs):
    if len(us) < 2:
        return 0.0, float(np.mean(vs)) if len(vs) else 0.0
    a, b = np.polyfit(us, vs, 1)
    return float(a), float(b)


def text_line(c, sx, sy):
    """DB 코어 윤곽 → 글자줄 {c0, ex, ey, u0, u1, top, bot, h}. 코어는 DB 가 줄여 낸 것 → 1.5 비율로 되돌림."""
    p = c.reshape(-1, 2).astype(np.float64) * [sx, sy]
    pf = p.astype(np.float32)
    area, per = cv2.contourArea(pf), cv2.arcLength(pf, True)
    d = area * 1.5 / max(per, 1e-6)
    (cx, cy), (w, h), ang = cv2.minAreaRect(pf)
    if w < h:
        w, h = h, w
        ang += 90
    t = np.deg2rad(ang)
    ex = np.array([np.cos(t), np.sin(t)])
    if ex[0] < 0:
        ex = -ex
    ey = np.array([-ex[1], ex[0]])
    if ey[1] < 0:
        ey = -ey
    c0 = np.array([cx, cy])
    u, v = (p - c0) @ ex, (p - c0) @ ey
    nb = max(4, int((u.max() - u.min()) / max(h, 1) * 2))
    edges = np.linspace(u.min(), u.max(), nb + 1)
    tu, tv, bv = [], [], []
    for i in range(nb):
        m = (u >= edges[i]) & (u < edges[i + 1])
        if m.sum() < 2:
            continue
        tu.append((edges[i] + edges[i + 1]) / 2)
        tv.append(v[m].min())
        bv.append(v[m].max())
    k = max(1, len(tu) // 6)                       # 양 끝(둥근 부분) 빼고 맞춤
    sl = slice(k, -k) if len(tu) > 2 * k + 1 else slice(None)
    tu, tv, bv = np.array(tu), np.array(tv), np.array(bv)
    top = _fit(tu[sl], tv[sl])
    bot = _fit(tu[sl], bv[sl])
    return {"c0": c0, "ex": ex, "ey": ey, "u0": u.min() - d, "u1": u.max() + d,
            "top": (top[0], top[1] - d), "bot": (bot[0], bot[1] + d), "h": float(h + 2 * d)}


def _xy(L, u, v):
    return L["c0"] + L["ex"] * u + L["ey"] * v


def line_quad(L):
    (a1, b1), (a2, b2) = L["top"], L["bot"]
    return np.array([_xy(L, L["u0"], a1 * L["u0"] + b1), _xy(L, L["u1"], a1 * L["u1"] + b1),
                     _xy(L, L["u1"], a2 * L["u1"] + b2), _xy(L, L["u0"], a2 * L["u0"] + b2)], np.float32)


def group_quad(G):
    """여러 줄: 첫 줄 좌표계에서 윗변 = 맨 윗줄 위, 아랫변 = 맨 아랫줄 아래, 옆변 = 줄 끝이 가지런한 쪽 기울기."""
    if len(G) == 1:
        return line_quad(G[0])
    B = G[0]
    ex, ey, c0 = B["ex"], B["ey"], B["c0"]

    def uv(P):
        return np.stack([(P - c0) @ ex, (P - c0) @ ey], 1)
    qs = [uv(line_quad(L)) for L in sorted(G, key=lambda L: float((L["c0"] - c0) @ ey))]
    tq, bq = qs[0], qs[-1]
    top = np.polyfit(tq[:2, 0], tq[:2, 1], 1) if abs(tq[1, 0] - tq[0, 0]) > 1e-3 else np.array([0.0, tq[:2, 1].min()])
    bot = np.polyfit(bq[2:, 0], bq[2:, 1], 1) if abs(bq[2, 0] - bq[3, 0]) > 1e-3 else np.array([0.0, bq[2:, 1].max()])
    lp = np.concatenate([q[[0, 3]] for q in qs])
    rp = np.concatenate([q[[1, 2]] for q in qs])
    hh = float(np.median([L["h"] for L in G]))

    def fit(P):
        f = np.polyfit(P[:, 1], P[:, 0], 1)
        return f, float(np.abs(P[:, 0] - np.polyval(f, P[:, 1])).max())
    (lf, le), (rf, re) = fit(lp), fit(rp)
    slope = lf[0] if le <= re else rf[0]
    if min(le, re) > 0.5 * hh:                      # 둘 다 들쭉날쭉(가운데 정렬 등) → 수직
        slope = 0.0
    lf = np.array([slope, (lp[:, 0] - slope * lp[:, 1]).min()])
    rf = np.array([slope, (rp[:, 0] - slope * rp[:, 1]).max()])

    def hit(edge, side):                            # v = a u + b 와 u = c v + d 교점
        a, b = edge
        c, d = side
        den = 1 - a * c
        v = (a * d + b) / (den if abs(den) > 1e-9 else 1e-9)
        return c0 + ex * (c * v + d) + ey * v
    return np.array([hit(top, lf), hit(top, rf), hit(bot, rf), hit(bot, lf)], np.float32)


def signs_from_prob(pm, sx, sy, thr=0.3):
    """확률 지도 → [(quad(4,2), 글자높이, 점수)] — 간판별(여러 줄 합침), 여유 없음."""
    m = (pm > thr).astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    lines = []
    for c in cs:
        if cv2.contourArea(c) < 6:
            continue
        x, y, w, h = cv2.boundingRect(c)
        sc = float(pm[y:y + h, x:x + w][m[y:y + h, x:x + w] > 0].mean())
        L = text_line(c, sx, sy)
        L["score"] = sc
        lines.append(L)
    groups = [[L] for L in lines]
    merged = True
    while merged:
        merged = False
        for a in range(len(groups)):
            ha = float(np.median([L["h"] for L in groups[a]]))
            qa = grow(group_quad(groups[a]), MERGE_MARGIN * ha)
            for b in range(a + 1, len(groups)):
                hb = float(np.median([L["h"] for L in groups[b]]))
                if abs(np.log(max(ha, 1e-3) / max(hb, 1e-3))) > 0.7:      # 글자 크기 많이 다름 = 다른 간판
                    continue
                qb = grow(group_quad(groups[b]), MERGE_MARGIN * hb)
                if cv2.intersectConvexConvex(qa, qb)[0] > 0:
                    groups[a] += groups[b]
                    del groups[b]
                    merged = True
                    break
            if merged:
                break
    out = []
    for g in groups:
        q = group_quad(g)
        if not np.all(np.isfinite(q)):
            continue
        out.append((q, float(np.median([L["h"] for L in g])), float(max(L["score"] for L in g))))
    return out


def sign_frame(rgb, side=DET_SIDE):
    """한 프레임 → 분석 행 목록(프레임 번호 제외)."""
    pm, sx, sy = detector().prob(rgb, side)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    rows = []
    for q, h, sc in signs_from_prob(pm, sx, sy):
        x0, y0 = q.min(0)
        x1, y1 = q.max(0)
        rows.append(np.r_[[x0, y0, x1, y1, sc, KIND_TEXT], A._hist(hsv, x0, y0, x1, y1), q.reshape(-1), [h]]
                    .astype(np.float32))
    return rows


def analyze_sign(info: ff.Info, progress=None, cancel=None, hw=False, workers=None, side=None):
    """프레임마다 독립 검출 → 여러 스레드로 병렬(얼굴 블러와 같은 구조)."""
    import os
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    workers = workers or max(2, (os.cpu_count() or 4) - 2)
    n = max(1, info.frames)
    r = ff.Reader(info, 0, 8, hw=hw)
    rows = []
    i = 0
    q = deque()
    side = side or det_side(info)
    cam, cams = A.CamEst(), []

    def drain(block):
        while q and (block or q[0][1].done()):
            f, fu = q.popleft()
            for row in fu.result():
                rows.append(np.r_[np.float32(f), row])
    try:
        with ThreadPoolExecutor(workers) as pool:
            while True:
                if cancel is not None and cancel.is_set():
                    raise A.Cancelled()
                fr = r.read()
                if fr is None:
                    break
                cams.append(cam.step(fr))
                q.append((i, pool.submit(sign_frame, fr.copy(), side)))
                i += 1
                while len(q) > workers * 2:
                    f, fu = q.popleft()
                    for row in fu.result():
                        rows.append(np.r_[np.float32(f), row])
                drain(False)
                if progress and i % 10 == 0:
                    progress(min(1.0, i / n))
            drain(True)
    finally:
        r.close()
    if cancel is not None and cancel.is_set():
        raise A.Cancelled()
    if i == 0:
        raise RuntimeError("프레임을 읽지 못했습니다")
    det = np.array(rows, np.float32) if rows else np.zeros((0, 1 + QOFF + 9), np.float32)
    return {"det": det, "n": np.array(i), "sign": np.array(1), "cam": np.array(cams, np.float32).reshape(-1, 3),
            "wh": np.array([info.w, info.h])}


# ------------------------------------------------------------ 트랙
@dataclass
class SignTrack:
    frames: list          # frame -> [(id, quad(4,2) 여유 포함, 관측여부)]
    n: int
    ids: int
    tracks: dict          # id -> {"f0", "box": (L,8) quad, "obs", "ok"}

    def at(self, i):
        if 0 <= i < self.n:
            return self.frames[i]
        return []


def score_thr(sens):
    return 0.85 - float(sens) / 100 * 0.35       # 민감도 0→0.85, 100→0.50 (DB 평균 확률)


def sign_track(z, params, two_pass=True) -> SignTrack:
    det = z["det"]
    n = int(z["n"])
    thr = score_thr(params.get("sensitivity", 60))
    mins = float(params.get("min_size", 12))
    hold = int(params.get("hold", 15))
    link_max = int(params.get("link", 90))
    margin = float(params.get("margin", 60)) / 100
    maxn = int(params.get("max_count", 20))
    if len(det):
        det = det[(det[:, 5] >= thr) & (det[:, -1] >= mins)]
    # v0.7.55 카메라 움직임을 뺀 기준 좌표에서 잇기·평균(간판은 세상에 고정 → 팬·줌 중에도 사각형이 간판에 붙음)
    S, T = A.cam_world(z, n)
    per = [[] for _ in range(n)]
    for row in det:
        f = int(row[0])
        if f < n:
            x0, y0, x1, y1, sc = row[1:6]
            o = np.r_[np.array([((x0 + x1) / 2 - T[f, 0]) / S[f], ((y0 + y1) / 2 - T[f, 1]) / S[f],
                                (x1 - x0) / S[f], (y1 - y0) / S[f], sc], np.float32), row[6:]]   # kind, hist, quad8, h
            qw = (o[6 + A.HIST:6 + A.HIST + 8].reshape(4, 2) - T[f]) / S[f]
            o[6 + A.HIST:6 + A.HIST + 8] = qw.reshape(-1)
            o[6 + A.HIST + 8] /= S[f]
            per[f].append(o.astype(np.float32))
    # 간판: 글자 검출이 프레임마다 깜빡이고 상자 크기도 출렁여 얼굴보다 느슨하게 이음(거리·크기비 완화)
    raw = A._greedy_tracks(per, n, hold, dist_gate=1.2, ratio_gate=2.0)
    raw = [t for t in raw if len(t["obs"]) >= 2 or max(o[4] for o in t["obs"].values()) >= 0.7]
    linked = A._link(raw, hold, link_max) if link_max > hold else [{"obs": t["obs"], "linked": []} for t in raw]
    linked = A._dedupe(linked)
    frames = [[] for _ in range(n)]
    tracks = {}
    sig = A.stab_sigma(params.get("stabilize", 50))
    sig_shape = max(sig * 3, 4.0)                   # 모양은 더 강하게 평균(꼭짓점 출렁임 방지)
    qo = 6 + A.HIST                                 # obs 안 quad 위치
    out_id = 0
    for t in sorted(linked, key=lambda t: min(t["obs"])):
        fs = sorted(t["obs"])
        a, b = fs[0], fs[-1]
        pre = hold if two_pass else 0
        a2, b2 = max(0, a - pre), min(n - 1, b + hold)
        L = b2 - a2 + 1
        cen = np.zeros((L, 2), np.float32)
        rel = np.zeros((L, 8), np.float32)
        hh = np.zeros((L, 1), np.float32)
        val = np.zeros(L, bool)
        for f in fs:
            o = t["obs"][f]
            qd = o[qo:qo + 8].reshape(4, 2)
            c = qd.mean(0)
            cen[f - a2] = c
            rel[f - a2] = (qd - c).reshape(-1)
            hh[f - a2] = o[qo + 8]
            val[f - a2] = True
        idx = np.where(val)[0]
        for arr in (cen, rel, hh):
            for c in range(arr.shape[1]):
                arr[:, c] = np.interp(np.arange(L), idx, arr[idx, c])
        okm = np.ones(L, bool)
        bridged = {(p - a2, q - a2) for p, q in t["linked"]}
        for p, q in zip(idx[:-1], idx[1:]):
            if q - p - 1 > hold and (p, q) not in bridged:
                okm[p + 1:q] = False
        cen = A._smooth_series(cen, okm, sig)
        rel = A._smooth_series(rel, okm, sig_shape)
        hh = A._smooth_series(hh, okm, sig_shape)
        quads = np.zeros((L, 8), np.float32)
        for k in range(L):
            qk = grow(cen[k] + rel[k].reshape(4, 2), margin * float(hh[k, 0]))
            quads[k] = (qk * S[a2 + k] + T[a2 + k]).reshape(-1)             # 기준 좌표 → 프레임 좌표
        out_id += 1
        msc = float(np.mean([t["obs"][f][4] for f in fs]))
        # 의심 트랙(검수용 표시): 아주 짧음 · 점수 낮음 · 튀는 동작(글자 상자는 출렁임이 커서 얼굴보다 느슨하게)
        jumps = A._jumps([(f, t["obs"][f][:4]) for f in fs], move=0.8, grow=1.8,
                         skip=A._edge_frames(z, S, T, fs, t["obs"]))
        why = [w for w, c in (("짧음", len(fs) < 6), ("점수 낮음", msc < thr + 0.08), ("튐", jumps >= 1)) if c]
        tracks[out_id] = {"f0": a2, "box": quads, "obs": val, "ok": okm, "sus": bool(why), "why": why, "score": msc}
        for k in range(L):
            if okm[k]:
                frames[a2 + k].append((out_id, quads[k].reshape(4, 2), bool(val[k])))
    if maxn > 0:                                    # 최대 개수: 큰 것부터
        for i in range(n):
            if len(frames[i]) > maxn:
                frames[i] = sorted(frames[i], key=lambda e: -cv2.contourArea(e[1]))[:maxn]
    return SignTrack(frames, n, out_id, tracks)


# ------------------------------------------------------------ 처리(알파)
def quad_alpha(q, roi, feather_px):
    """ROI 안 4꼭짓점 사각형 알파(0~1). 페더가 크면 축소 격자(4×4 점 면적)에서 흐린 뒤 키움."""
    x0, y0, x1, y1 = roi
    W, H = x1 - x0, y1 - y0
    q = np.asarray(q, np.float32).reshape(4, 2)
    if feather_px <= 8:
        m = np.zeros((H, W), np.float32)
        cv2.fillPoly(m, [np.round((q - (x0, y0)) * 16).astype(np.int32)], 1.0, cv2.LINE_AA, 4)
        return cv2.GaussianBlur(m, (0, 0), feather_px) if feather_px > 0.5 else m
    k = int(max(2, min(8, feather_px / 6)))
    R = 3 * feather_px + 2 * k
    qx0, qy0 = q.min(0) - R
    qx1, qy1 = q.max(0) + R
    lx = int(np.ceil(max(0.0, x0 - qx0) / k))
    ty = int(np.ceil(max(0.0, y0 - qy0) / k))
    gx0, gy0 = x0 - lx * k, y0 - ty * k
    w = -(-(int(max(x1, qx1)) - gx0) // k) + 1
    h = -(-(int(max(y1, qy1)) - gy0) // k) + 1
    ss = 4
    big = np.zeros((h * ss, w * ss), np.uint8)
    # 작은 격자 화소 j 중심 = 원래 j*k + (k-1)/2 → 촘촘한 격자(ss배) 점 = 원래 좌표 (x - gx0)/k*ss - 0.5 근처
    pts = (q - (gx0, gy0) + 0.5) * (ss / k) - 0.5          # 원래 좌표 → 촘촘한 격자 좌표(화소 중심 정렬)
    cv2.fillPoly(big, [np.round(pts * 16).astype(np.int32)], 1, cv2.LINE_8, 4)
    m = cv2.resize(big.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA)
    m = cv2.GaussianBlur(m, (0, 0), feather_px / k)
    up = cv2.resize(m, (w * k, h * k), interpolation=cv2.INTER_LINEAR)
    return up[ty * k:ty * k + H, lx * k:lx * k + W]
