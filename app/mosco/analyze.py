"""영상 분석(얼굴 검출·랜드마크)과 트래킹 후처리.

분석은 슬라이더와 무관하게 한 번만(캐시). 슬라이더(민감도·최소 크기·놓침 유지·떨림 보정·크기 등)는
분석 결과에 대한 후처리라 즉시 반영된다.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import cv2
import numpy as np

from . import ff, models, paths

VERSION = 2
LOW_SCORE = 0.3          # 분석 때 낮게 저장 → 민감도로 후처리


class Cancelled(Exception):
    pass


def _key(info: ff.Info, mode: str) -> str:
    st = os.stat(info.path)
    raw = f"{os.path.abspath(info.path)}|{st.st_size}|{int(st.st_mtime)}|{mode}|{VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_file(info: ff.Info, mode: str):
    d = paths.cache_dir() / "analysis"
    d.mkdir(exist_ok=True)
    return d / f"{_key(info, mode)}.npz"


def load(info: ff.Info, mode: str):
    f = cache_file(info, mode)
    if not f.exists():
        return None
    try:
        z = np.load(f, allow_pickle=False)
        return dict(z)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ 1인(뷰티): 랜드마크 추적
def beauty_frame(rgb, prev):
    """한 프레임 랜드마크. prev(이전 랜드마크)가 있으면 그 주변에서 추적, 실패하면 검출부터."""
    mesh = models.mesh()
    if prev is not None:
        pts, conf = mesh.run(rgb, *models.roi_from_landmarks(prev))
        if conf > 0.5:
            return pts, conf
    dets = models.detector().detect(rgb, 0.5)
    if not dets:
        return None, 0.0
    d = max(dets, key=lambda d: (d.box[2] - d.box[0]) * d.score)
    pts, conf = mesh.run(rgb, *models.roi_from_det(d))
    if conf < 0.5:
        return None, conf
    # 한 번 더(ROI 정밀화)
    pts2, conf2 = mesh.run(rgb, *models.roi_from_landmarks(pts))
    return (pts2, conf2) if conf2 > 0.5 else (pts, conf)


def analyze_beauty(info: ff.Info, progress=None, cancel=None, hw=False):
    n = max(1, info.frames)
    lm = np.full((n, 468, 2), np.nan, np.float32)
    r = ff.Reader(info, 0, 8, hw=hw)
    prev = None
    i = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            fr = r.read()
            if fr is None:
                break
            if i >= n:
                lm = np.concatenate([lm, np.full((n, 468, 2), np.nan, np.float32)])
                n *= 2
            pts, _ = beauty_frame(fr, prev)
            if pts is not None:
                lm[i] = pts
            prev = pts
            i += 1
            if progress and i % 15 == 0:
                progress(min(1.0, i / max(1, info.frames)))
    finally:
        r.close()
    if i == 0:
        raise RuntimeError("프레임을 읽지 못했습니다")
    return {"lm": lm[:i].astype(np.float16), "n": np.array(i)}


# ------------------------------------------------------------ 군중(블러): 프레임별 검출
def blur_frame(rgb):
    """검출 + 오검출 거르기: 점수가 애매한(0.7 미만) 것은 얼굴 랜드마크 모델로 한 번 더 확인.
    (실측: 진짜 얼굴은 23px 도 확인값 1.0, 무늬 오검출은 0.001 이하)"""
    dets = models.detector().detect(rgb, LOW_SCORE, tiles=True, min_size=6)
    out = []
    for d in dets:
        if d.score < 0.7:
            _, conf = models.mesh().run(rgb, *models.roi_from_det(d))
            if conf < 0.05:
                continue
        out.append((*d.box, d.score))
    return out


def analyze_blur(info: ff.Info, progress=None, cancel=None, hw=False, workers=None):
    """프레임마다 독립 검출 → 여러 스레드로 병렬."""
    from concurrent.futures import ThreadPoolExecutor
    from collections import deque
    workers = workers or max(2, min(16, (os.cpu_count() or 4) - 2))
    rows = []      # frame, x0, y0, x1, y1, score
    r = ff.Reader(info, 0, 8, hw=hw)
    pool = ThreadPoolExecutor(workers)
    q = deque()
    i = 0

    def take():
        f, fut = q.popleft()
        for d in fut.result():
            rows.append((f, *d))
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            fr = r.read()
            if fr is None:
                break
            q.append((i, pool.submit(blur_frame, fr.copy())))
            i += 1
            while len(q) >= workers * 2:
                take()
            if progress and i % 10 == 0:
                progress(min(1.0, i / max(1, info.frames)))
        while q:
            take()
    finally:
        r.close()
        pool.shutdown(cancel_futures=True)
    if i == 0:
        raise RuntimeError("프레임을 읽지 못했습니다")
    return {"det": np.array(rows, np.float32).reshape(-1, 6), "n": np.array(i)}


def analyze(info: ff.Info, mode: str, progress=None, cancel=None, hw=False):
    z = load(info, mode)
    if z is not None:
        return z
    fn = analyze_beauty if mode == "beauty" else analyze_blur
    z = fn(info, progress, cancel, hw)
    f = cache_file(info, mode)
    import threading
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp.npz")
    np.savez_compressed(tmp, **z)
    os.replace(tmp, f)
    return z


# ------------------------------------------------------------ 후처리 공통
def _smooth_series(x: np.ndarray, valid: np.ndarray, sigma: float):
    """NaN(없음)을 건너뛰는 시간축 가우시안(정규화 합성곱). x: (N, ...) valid: (N,)"""
    if sigma <= 0.05:
        return x
    r = int(sigma * 3) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2).astype(np.float32)
    shp = x.shape
    flat = np.where(valid[:, None], x.reshape(shp[0], -1), 0).astype(np.float32)
    w = valid.astype(np.float32)
    num = np.apply_along_axis(lambda c: np.convolve(c, k, "same"), 0, flat) if flat.shape[1] < 64 else \
        cv2.filter2D(flat, -1, k[:, None], borderType=cv2.BORDER_CONSTANT)
    den = np.convolve(w, k, "same")
    out = num / np.maximum(den, 1e-6)[:, None]
    return out.reshape(shp)


def stab_sigma(v):
    return float(v) / 100 * 4.0       # 0~4 프레임


# ------------------------------------------------------------ 뷰티 트랙
@dataclass
class BeautyTrack:
    lm: np.ndarray        # (N,468,2) 보정된 랜드마크 (없으면 NaN)
    presence: np.ndarray  # (N,) 0~1 (페이드)
    n: int

    def at(self, i):
        if i < 0 or i >= self.n or self.presence[i] <= 0.001:
            return None, 0.0
        return self.lm[i], float(self.presence[i])


def beauty_track(z, params, hold=15) -> BeautyTrack:
    lm = z["lm"].astype(np.float32)
    n = lm.shape[0]
    valid = ~np.isnan(lm[:, 0, 0])
    # 짧은 공백은 앞뒤 값으로 보간
    idx = np.where(valid)[0]
    filled = lm.copy()
    if len(idx):
        gaps = np.diff(idx)
        for a, g in zip(idx[:-1], gaps):
            if 1 < g <= hold + 1:
                b = a + g
                for t in range(1, g):
                    u = t / g
                    filled[a + t] = lm[a] * (1 - u) + lm[b] * u
    v2 = ~np.isnan(filled[:, 0, 0])
    sm = _smooth_series(np.nan_to_num(filled), v2, stab_sigma(params.get("stabilize", 50)))
    sm[~v2] = np.nan
    # 존재(페이드 0.25초)
    pres = v2.astype(np.float32)
    k = np.ones(7, np.float32) / 7
    pres = np.minimum(pres, np.convolve(pres, k, "same") * 1.0)
    pres = np.clip(np.convolve(pres, k, "same"), 0, 1) * v2
    return BeautyTrack(sm, pres, n)


def head_ellipse(pts: np.ndarray, size=145, neck=0):
    """얼굴 윤곽 랜드마크 → 머리 전체를 덮는 타원 (cx, cy, ax_w, ax_h, angle_deg). ax 는 전체 지름."""
    oval = pts[models.FACE_OVAL]
    (cx, cy), (w, h), ang = cv2.fitEllipse(oval.astype(np.float32))
    if w > h:      # fitEllipse 는 축 순서가 바뀔 수 있음 → 세로가 긴 쪽
        w, h = h, w
        ang -= 90
    s = size / 100.0
    hw, hh = w * s, h * s
    # 위쪽(머리카락)으로 더 넓게: 중심을 머리 위 방향으로
    up = (hh - h) * 0.42
    ext = h * neck / 100 * 0.9
    hh2 = hh + ext
    down = ext / 2 - up
    a = np.deg2rad(ang)
    # 타원 세로축 방향(아래쪽) 단위벡터
    dx, dy = -np.sin(a), np.cos(a)
    return (cx + dx * down, cy + dy * down, hw, hh2, ang)


# ------------------------------------------------------------ 블러 트랙(여러 명)
@dataclass
class BlurTrack:
    frames: list          # frame -> list of (id, cx, cy, w, h, 관측여부)  (얼굴 상자 기준, 여유 전)
    n: int
    ids: int

    def at(self, i):
        if 0 <= i < self.n:
            return self.frames[i]
        return []


def score_thr(sens):
    return 0.75 - float(sens) / 100 * 0.45       # 민감도 0→0.75, 100→0.30


def blur_track(z, params, two_pass=True) -> BlurTrack:
    det = z["det"]
    n = int(z["n"])
    thr = score_thr(params.get("sensitivity", 75))
    mins = float(params.get("min_size", 16))
    hold = int(params.get("hold", 15))
    keep = (det[:, 5] >= thr) & ((det[:, 3] - det[:, 1]) >= mins) if len(det) else np.zeros(0, bool)
    det = det[keep]
    per = [[] for _ in range(n)]
    for row in det:
        f = int(row[0])
        if f < n:
            x0, y0, x1, y1, sc = row[1:]
            per[f].append(np.array([(x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, sc], np.float32))
    # 탐욕 매칭 트래커(등속 예측)
    tracks = []    # dict: id, last(frame), state(cx,cy,w,h), vel, obs{frame: box}
    nid = 0
    for f in range(n):
        obs = per[f]
        live = [t for t in tracks if f - t["last"] <= hold + 1]
        pairs = []
        for ti, t in enumerate(live):
            dt = f - t["last"]
            p = t["state"][:2] + t["vel"] * dt
            for oi, o in enumerate(obs):
                size = max(t["state"][2], o[2])
                dist = np.hypot(*(o[:2] - p)) / size
                ratio = max(o[2], t["state"][2]) / max(1e-3, min(o[2], t["state"][2]))
                if dist < 1.0 + 0.15 * dt and ratio < 1.8:
                    pairs.append((dist, ti, oi))
        pairs.sort()
        used_t, used_o = set(), set()
        for dist, ti, oi in pairs:
            if ti in used_t or oi in used_o:
                continue
            used_t.add(ti); used_o.add(oi)
            t = live[ti]; o = obs[oi]
            dt = f - t["last"]
            v = (o[:2] - t["state"][:2]) / dt
            t["vel"] = t["vel"] * 0.6 + v * 0.4
            t["state"] = o[:4].copy(); t["last"] = f; t["obs"][f] = o
        for oi, o in enumerate(obs):
            if oi not in used_o:
                tracks.append({"id": nid, "last": f, "state": o[:4].copy(), "vel": np.zeros(2, np.float32),
                               "obs": {f: o}})
                nid += 1
    frames = [[] for _ in range(n)]
    sig = stab_sigma(params.get("stabilize", 50))
    out_id = 0
    for t in tracks:
        fs = sorted(t["obs"])
        best = max(o[4] for o in t["obs"].values())
        if len(fs) < 3 and best < 0.6:
            continue        # 잠깐 나온 약한 검출 = 오검출로 봄
        a, b = fs[0], fs[-1]
        pre = hold if two_pass else 0
        post = hold
        a2, b2 = max(0, a - pre), min(n - 1, b + post)
        L = b2 - a2 + 1
        arr = np.zeros((L, 4), np.float32); val = np.zeros(L, bool)
        for f in fs:
            arr[f - a2] = t["obs"][f][:4]; val[f - a2] = True
        # 공백 보간(관측 사이) + 앞뒤 연장(끝 값 유지)
        idx = np.where(val)[0]
        for c in range(4):
            arr[:, c] = np.interp(np.arange(L), idx, arr[idx, c])
        # 관측 사이 공백이 hold 보다 길면 그 구간은 비움
        okm = np.ones(L, bool)
        for p, q in zip(idx[:-1], idx[1:]):
            if q - p - 1 > hold:
                okm[p + 1:q] = False
        arr = _smooth_series(arr, okm, sig)
        obsm = np.zeros(L, bool); obsm[idx] = True
        for k in range(L):
            if okm[k]:
                frames[a2 + k].append((out_id + 1, *arr[k], bool(obsm[k])))
        out_id += 1
    return BlurTrack(frames, n, out_id)


def face_to_head(cx, cy, w, h, margin=160):
    """BlazeFace 얼굴 상자 → 머리 타원 (cx, cy, 지름w, 지름h, 0)."""
    s = margin / 100.0
    hw, hh = w * s, h * s * 1.25
    return (cx, cy - h * 0.12 * s, hw, hh, 0.0)
