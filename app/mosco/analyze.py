"""영상 분석(얼굴 검출·랜드마크)과 트래킹 후처리.

분석은 슬라이더와 무관하게 한 번만(캐시). 슬라이더(민감도·최소 크기·놓침 유지·떨림 보정·크기 등)는
분석 결과에 대한 후처리라 즉시 반영된다.
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from . import ff, models, paths

VERSION = 5      # v5(0.7.55): 카메라 움직임(cam) 저장 · 뷰티 등장 구간 거꾸로 채움·슬롯 재사용 규칙 · 간판 검출 크기
LOW_SCORE = 0.3          # 분석 때 낮게 저장 → 민감도로 후처리


class Cancelled(Exception):
    pass


def _key(info: ff.Info, mode: str) -> str:
    st = os.stat(info.path)
    raw = f"{os.path.abspath(info.path)}|{st.st_size}|{int(st.st_mtime)}|{mode}|{VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


SMALL_MIN = 12           # 블러 분석 기본 하한(작은 얼굴 MTCNN 최소 px)


def level_for(mode: str, params: dict) -> int:
    """분석 '하한'(v0.7.4). 블러: [최소 크기]보다 작은 얼굴은 트랙에서 어차피 버리므로
    그보다 작은 얼굴은 분석에서 찾지 않는다(MTCNN 이 분석 시간의 대부분). 뷰티 = 0(해당 없음)."""
    if mode != "blur":
        return 0
    try:
        ms = float(params.get("min_size", SMALL_MIN))
    except (TypeError, ValueError):
        ms = SMALL_MIN
    return max(SMALL_MIN, int(ms * 0.85))        # 상자 크기 오차 여유 15%


def cache_file(info: ff.Info, mode: str, level: int = 0, bottom_skip: float = 0.0):
    d = paths.cache_dir() / "analysis"
    d.mkdir(exist_ok=True)
    bsuf = f"_B{int(round(bottom_skip * 100))}" if mode == "blur" and bottom_skip > 0 else ""
    lsuf = f"_L{level}" if mode == "blur" and level > SMALL_MIN else ""
    return d / f"{_key(info, mode)}{bsuf}{lsuf}.npz"


def _candidates(info: ff.Info, mode: str, level: int, bottom_skip: float = 0.0):
    """쓸 수 있는 캐시 파일: (같은 하단제외 설정 안에서) 하한이 요구값 이하면 그대로 써도 된다.
    하단 제외(_B..) 캐시와 전체(_B 없음) 캐시는 서로 섞지 않는다(검색 범위가 달라 결과가 다름)."""
    base = cache_file(info, mode, 0, bottom_skip)
    out = [base]
    if mode == "blur":
        lv = []
        for f in base.parent.glob(f"{base.stem}_L*.npz"):
            try:
                L = int(f.stem.rsplit("_L", 1)[1])
            except ValueError:
                continue
            if L <= level:
                lv.append((L, f))
        out += [f for _, f in sorted(lv, reverse=True)]
    return out


def load(info: ff.Info, mode: str, level: int = 0, bottom_skip: float = 0.0):
    level = max(SMALL_MIN, level) if mode == "blur" else 0
    for f in _candidates(info, mode, level, bottom_skip):
        z = _load_file(f)
        if z is not None:
            return z
    return None


def _load_file(f):
    if not f.exists():
        return None
    try:
        z = np.load(f, allow_pickle=False)
        return dict(z)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ 카메라 움직임(v0.7.55)
CAM_SIDE = 480           # 추정용 축소 긴 변(px)


class CamEst:
    """프레임 사이 화면 전체 움직임(이동 + 확대)을 순서대로 추정. 행 = (s, tx, ty): p_i = s·p_(i-1) + t (원본 px).
    특징점 광류 + RANSAC. 믿기 어려우면(특징 부족·장면 전환·튀는 값) 그 프레임은 '움직임 없음'(1,0,0)."""

    def __init__(self):
        self.prev = None

    def step(self, rgb):
        H, W = rgb.shape[:2]
        k = max(1, int(round(max(H, W) / CAM_SIDE)))
        g = np.ascontiguousarray(rgb[::k, ::k, 1])
        prev, self.prev = self.prev, g
        if prev is None or prev.shape != g.shape:
            return (1.0, 0.0, 0.0)
        try:
            p = cv2.goodFeaturesToTrack(prev, 300, 0.01, 8)
            if p is None or len(p) < 12:
                return (1.0, 0.0, 0.0)
            q, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, p, None)
            ok = st.ravel() == 1
            if ok.sum() < 12:
                return (1.0, 0.0, 0.0)
            M, inl = cv2.estimateAffinePartial2D(p[ok], q[ok], method=cv2.RANSAC, ransacReprojThreshold=1.5)
            if M is None or inl is None or inl.sum() < max(10, 0.5 * ok.sum()):
                return (1.0, 0.0, 0.0)
            s = float(np.hypot(M[0, 0], M[1, 0]))
            tx, ty = float(M[0, 2]) * k, float(M[1, 2]) * k
            if not (0.85 < s < 1.18) or abs(tx) > 0.25 * W or abs(ty) > 0.25 * H:
                return (1.0, 0.0, 0.0)
            return (s, tx, ty)
        except cv2.error:
            return (1.0, 0.0, 0.0)


def cam_world(z, n):
    """분석 결과의 cam → 프레임 i 좌표 = S[i]·(기준 좌표) + T[i]. cam 이 없으면(옛 캐시) S=1, T=0.
    기준 좌표 = 카메라 움직임을 뺀 좌표(프레임 0 기준). 트랙 잇기·빈 구간·떨림 보정을 여기서 하면 팬·줌을 따라간다."""
    S = np.ones(n, np.float64)
    T = np.zeros((n, 2), np.float64)
    cam = z.get("cam") if isinstance(z, dict) else None
    if cam is None or len(cam) == 0:
        return S, T
    cam = np.asarray(cam, np.float64)
    for i in range(1, min(n, len(cam))):
        s, tx, ty = cam[i]
        S[i] = s * S[i - 1]
        T[i] = s * T[i - 1] + (tx, ty)
    if len(cam) < n:
        S[len(cam):], T[len(cam):] = S[len(cam) - 1], T[len(cam) - 1]
    return S, T


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


BEAUTY_K = 8          # 뷰티 동시 얼굴 최대 수
_B_HOLD = 12          # 잠깐 놓친 슬롯을 몇 프레임까지 ROI 로 쫓을지
DET_EVERY = 10        # 추적이 다 잘 될 때 새 얼굴 검출 간격(프레임)


def _pts_box(pts):
    o = pts[models.FACE_OVAL]
    x0, y0 = o[:, 0].min(), o[:, 1].min()
    x1, y1 = o[:, 0].max(), o[:, 1].max()
    return x0, y0, x1, y1


def _pick_slot(free, box, last_box, last_seen, i, prev):
    """새 얼굴에 줄 슬롯(v0.7.55). ① 같은 자리·비슷한 크기로 최근(10초 안)에 있던 슬롯 = 같은 사람 → 그 번호
    ② 아니면 한 번도 안 쓴 슬롯 ③ 그것도 없으면 가장 오래전에 쓴 슬롯.
    (예전: 맨 앞 빈 슬롯 — 나간 사람의 번호를 다른 사람이 바로 받아 타원별 편집값이 옮겨 감)"""
    x0, y0, x1, y1 = box
    cx, cy, w = (x0 + x1) / 2, (y0 + y1) / 2, max(1.0, x1 - x0)
    best, bd = None, 1e9
    for k in free:
        b = last_box[k]
        if b is None or i - last_seen[k] > 300:
            continue
        bw = max(1.0, b[2] - b[0])
        d = np.hypot((b[0] + b[2]) / 2 - cx, (b[1] + b[3]) / 2 - cy) / max(w, bw)
        if d < 1.0 and max(w, bw) / min(w, bw) < 1.6 and d < bd:
            best, bd = k, d
    if best is not None:
        return best
    fresh = [k for k in free if last_box[k] is None]
    if fresh:
        return fresh[0]
    idle = [k for k in free if prev[k] is None] or free       # 잠깐 놓쳐 쫓는 중인 슬롯은 되도록 남겨 둠
    return min(idle, key=lambda k: last_seen[k])


def analyze_beauty(info: ff.Info, progress=None, cancel=None, hw=False):
    """여러 얼굴을 추적해 각 슬롯별 랜드마크를 저장. lm 형상 (N, K, 468, 2)."""
    n = max(1, info.frames)
    K = BEAUTY_K
    mesh = models.mesh()
    det = models.detector()
    lm = np.full((n, K, 468, 2), np.nan, np.float32)
    r = ff.Reader(info, 0, 8, hw=hw)
    prev = [None] * K          # 슬롯별 직전 랜드마크(추적 ROI 용)
    miss = [0] * K             # 슬롯별 연속 놓친 프레임 수
    last_box = [None] * K      # 슬롯별 마지막으로 본 얼굴 상자(같은 사람이 돌아오면 같은 번호로)
    last_seen = [-10 ** 9] * K
    last_det = -10 ** 9
    ring = {}                  # 최근 DET_EVERY 프레임(새 얼굴의 등장 구간을 거꾸로 채우는 용)
    i = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            fr = r.read()
            if fr is None:
                break
            if i >= n:
                lm = np.concatenate([lm, np.full((n, K, 468, 2), np.nan, np.float32)])
                n *= 2
            cur = [None] * K
            boxes = []          # 이미 이 프레임에서 잡은 얼굴 박스(중복 방지)
            # 1) 기존 슬롯을 직전 랜드마크 ROI 로 추적(안정적·빠름)
            for k in range(K):
                if prev[k] is None:
                    continue
                pts, conf = mesh.run(fr, *models.roi_from_landmarks(prev[k]))
                if conf > 0.5:
                    cur[k] = pts
                    boxes.append(_pts_box(pts))
            # 2) 새 얼굴 검출 → 빈 슬롯에 배정(이미 추적 중인 영역은 건너뜀)
            #    v0.7.4: 추적이 다 잘 되는 동안은 DET_EVERY 프레임마다만(검출이 추적보다 2배 느림).
            #    얼굴이 없거나 하나라도 놓치면 매 프레임 검출.
            tracking = any(p is not None for p in prev)
            lost = any(prev[k] is not None and cur[k] is None for k in range(K))
            if tracking and not lost and i - last_det < DET_EVERY:
                found = []
            else:
                found = det.detect(fr, 0.5)
                last_det = i
            for d in sorted(found, key=lambda d: -(d.box[2] - d.box[0])):
                cx, cy = (d.box[0] + d.box[2]) / 2, (d.box[1] + d.box[3]) / 2
                if any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in boxes):
                    continue
                free = [k for k in range(K) if cur[k] is None]
                if not free:
                    break
                pts, conf = mesh.run(fr, *models.roi_from_det(d))
                if conf < 0.5:
                    continue
                slot = _pick_slot(free, _pts_box(pts), last_box, last_seen, i, prev)
                cur[slot] = pts
                boxes.append(_pts_box(pts))
                # v0.7.55 등장 구간 거꾸로 채움: 검출을 건너뛴 직전 프레임들(최대 DET_EVERY)을 뒤로 추적
                # (예전: 새 얼굴이 최대 10프레임 동안 보정 없이 나오다 갑자기 보정됨)
                back = pts
                for j in range(i - 1, i - DET_EVERY - 1, -1):
                    fj = ring.get(j)
                    if fj is None or not np.isnan(lm[j, slot, 0, 0]):
                        break
                    bp, bc = mesh.run(fj, *models.roi_from_landmarks(back))
                    if bc <= 0.5:
                        break
                    bx0, by0, bx1, by1 = _pts_box(bp)
                    cxj, cyj = (bx0 + bx1) / 2, (by0 + by1) / 2
                    taken = False
                    for k2 in range(K):                     # 그 프레임에 이미 다른 슬롯이 잡은 얼굴이면 중단
                        if k2 != slot and not np.isnan(lm[j, k2, 0, 0]):
                            ox0, oy0, ox1, oy1 = _pts_box(lm[j, k2])
                            if ox0 <= cxj <= ox1 and oy0 <= cyj <= oy1:
                                taken = True
                                break
                    if taken:
                        break
                    lm[j, slot] = bp
                    back = bp
            # 3) 슬롯 유지: 잡았으면 저장·리셋, 놓쳤으면 hold 동안 직전값으로 계속 추적 시도
            for k in range(K):
                if cur[k] is not None:
                    lm[i, k] = cur[k]
                    prev[k] = cur[k]
                    miss[k] = 0
                    last_box[k] = _pts_box(cur[k])
                    last_seen[k] = i
                elif prev[k] is not None:
                    miss[k] += 1
                    if miss[k] > _B_HOLD:
                        prev[k] = None
            ring[i] = fr
            ring.pop(i - DET_EVERY, None)
            i += 1
            if progress and i % 15 == 0:
                progress(min(1.0, i / max(1, info.frames)))
    finally:
        r.close()
    if cancel is not None and cancel.is_set():       # 종료 중 ffmpeg 강제 종료 → 부분 결과 저장 안 함
        raise Cancelled()
    if i == 0:
        raise RuntimeError("프레임을 읽지 못했습니다")
    return {"lm": lm[:i].astype(np.float16), "n": np.array(i), "k": np.array(K)}


# ------------------------------------------------------------ 군중(블러): 프레임별 검출
KIND_FACE, KIND_SMALL, KIND_BODY = 0, 1, 2
HIST = 16          # 옷 색(색상4×채도4) — 끊긴 트랙 다시 잇기용


def _hist(hsv, x0, y0, x1, y1):
    H, W = hsv.shape[:2]
    x0, y0 = int(max(0, x0)), int(max(0, y0))
    x1, y1 = int(min(W, x1)), int(min(H, y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return np.zeros(HIST, np.float32)
    h = cv2.calcHist([hsv[y0:y1, x0:x1]], [0, 1], None, [4, 4], [0, 180, 0, 256]).flatten()
    return (h / max(1.0, h.sum())).astype(np.float32)


def _nms_rows(rows, thr=0.4):
    """rows: [x0,y0,x1,y1,score,kind] — 겹치면 점수 높은 것(같은 점수대면 얼굴 우선)."""
    if not rows:
        return []
    b = np.array([r[:4] for r in rows], np.float32)
    s = np.array([r[4] + (0.2 if r[5] == KIND_FACE else 0) for r in rows], np.float32)
    keep = models._nms_xyxy(b, s, thr, "min")
    return [rows[i] for i in keep]


def blur_frame(rgb, small=True, body=True, small_min=SMALL_MIN, bottom_skip=0.0):
    """한 프레임: 얼굴(BlazeFace 타일) + 작은 얼굴(MTCNN) + 몸→머리(NanoDet).
    반환 행: x0,y0,x1,y1,score,kind,hist(16)  — 상자는 '얼굴 상자' 기준(몸에서 추정한 머리도 같은 기준으로 환산).
    bottom_skip>0: 화면 하단 그 비율의 '작은 얼굴' 검색(타일·MTCNN)을 생략해 속도↑. 전체화면 통과는 그대로라 큰 얼굴은 보존."""
    H, W = rgb.shape[:2]
    top = max(0.1, 1.0 - max(0.0, min(0.9, bottom_skip)))   # 작은얼굴 검색을 상단 top 비율까지만
    ytop = int(round(H * top))
    rows = []
    for d in models.detector().detect(rgb, LOW_SCORE, tiles=True, min_size=6, tile_top=top):
        if d.score < 0.7:       # 애매한 것은 랜드마크 모델로 재확인(무늬 오검출 제거)
            _, conf = models.mesh().run(rgb, *models.roi_from_det(d))
            if conf < 0.05:
                continue
        rows.append([*d.box, d.score, KIND_FACE])
    if small and small_min < 64:       # 작은 얼굴: [최소 크기] 아래는 안 찾음(분석 하한). 하단 제외면 상단만.
        sub = rgb[:ytop] if bottom_skip > 0 else rgb     # MTCNN = 분석 시간의 대부분 → 입력을 줄여 크게 절약
        for x0, y0, x1, y1, sc in models.small_detector().detect(sub, small_min, 64):
            rows.append([x0, y0, x1, y1, sc * 0.9, KIND_SMALL])
    rows = _nms_rows(rows)
    if body:
        faces = [(((r[0] + r[2]) / 2), ((r[1] + r[3]) / 2)) for r in rows]
        for x0, y0, x1, y1, sc in models.person_detector().detect(rgb, 0.4):
            bw, bh = x1 - x0, y1 - y0
            if bw < 12 or bh < 24:
                continue
            # 이 사람 상자 위쪽에 얼굴이 이미 있으면 건너뜀(얼굴이 더 정확)
            if any(x0 <= fx <= x1 and y0 - bh * 0.05 <= fy <= y0 + bh * 0.45 for fx, fy in faces):
                continue
            cx, cy, fw, fh = models.head_from_person(x0, y0, x1, y1, cut_bottom=y1 >= H - 3)
            rows.append([cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2, sc, KIND_BODY])
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    out = []
    for r in rows:
        w, h = r[2] - r[0], r[3] - r[1]
        cx = (r[0] + r[2]) / 2
        hist = _hist(hsv, cx - w * 0.9, r[3] + h * 0.4, cx + w * 0.9, r[3] + h * 2.2)   # 얼굴 아래 = 옷
        out.append(np.r_[np.array(r[:6], np.float32), hist])
    return out


def analyze_blur(info: ff.Info, progress=None, cancel=None, hw=False, workers=None, small_min=SMALL_MIN, bottom_skip=0.0):
    """프레임마다 독립 검출 → 여러 스레드로 병렬."""
    from concurrent.futures import ThreadPoolExecutor
    from collections import deque
    workers = workers or max(2, (os.cpu_count() or 4) - 2)      # v0.7.4: 상한 16 없앰(32코어면 30)
    rows = []      # frame, x0, y0, x1, y1, score
    r = ff.Reader(info, 0, 8, hw=hw)
    pool = ThreadPoolExecutor(workers)
    q = deque()
    i = 0
    cam, cams = CamEst(), []

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
            cams.append(cam.step(fr))
            q.append((i, pool.submit(blur_frame, fr.copy(), True, True, small_min, bottom_skip)))
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
    if cancel is not None and cancel.is_set():       # 종료 중 강제 종료 → 부분 결과 저장 안 함
        raise Cancelled()
    if i == 0:
        raise RuntimeError("프레임을 읽지 못했습니다")
    return {"det": np.array(rows, np.float32).reshape(-1, 7 + HIST), "n": np.array(i),
            "cam": np.array(cams, np.float32).reshape(-1, 3)}


_inflight_guard = threading.Lock()
_inflight: dict = {}                 # 같은 소스·모드 분석이 두 번 돌지 않게(렌더 ↔ 편집·AUTO 분석)
_BLUR_SEM = threading.Semaphore(1)   # 블러 분석은 안에서 코어를 다 씀 → 한 번에 하나


def _acquire(lock, cancel):
    """취소를 보면서 잠금 대기."""
    while not lock.acquire(timeout=0.2):
        if cancel is not None and cancel.is_set():
            raise Cancelled()


def analyze(info: ff.Info, mode: str, progress=None, cancel=None, hw=False, workers=None, level=0, bottom_skip=0.0):
    level = max(SMALL_MIN, level) if mode == "blur" else 0
    bottom_skip = bottom_skip if mode == "blur" else 0.0
    z = load(info, mode, level, bottom_skip)
    if z is not None:
        return z
    with _inflight_guard:
        lk = _inflight.setdefault((info.path, mode, round(bottom_skip, 3)), threading.Lock())
    _acquire(lk, cancel)               # 다른 곳에서 같은 분석 중이면 끝날 때까지 기다렸다가 캐시 사용
    try:
        z = load(info, mode, level, bottom_skip)
        if z is not None:
            return z
        if mode == "beauty":
            z = analyze_beauty(info, progress, cancel, hw)       # 추적이라 순차(작업자 수 무관) → 여러 클립 동시 가능
        elif mode == "sign":
            from . import sign
            _acquire(_BLUR_SEM, cancel)                          # 안에서 코어를 다 씀 → 블러와 함께 한 번에 하나
            try:
                z = sign.analyze_sign(info, progress, cancel, hw, workers)
            finally:
                _BLUR_SEM.release()
        else:
            _acquire(_BLUR_SEM, cancel)
            try:
                z = analyze_blur(info, progress, cancel, hw, workers, small_min=level, bottom_skip=bottom_skip)
            finally:
                _BLUR_SEM.release()
        if mode == "blur":
            z["level"] = np.array(level)
        f = cache_file(info, mode, level, bottom_skip)
        tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp.npz")
        np.savez_compressed(tmp, **z)
        os.replace(tmp, f)
        return z
    finally:
        lk.release()


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
    N = shp[0]

    def conv(c):          # 길이 유지(커널이 신호보다 길어도)
        return np.convolve(c, k, "full")[r:r + N]
    num = np.apply_along_axis(conv, 0, flat) if flat.shape[1] < 64 else \
        cv2.filter2D(flat, -1, k[:, None], borderType=cv2.BORDER_CONSTANT)
    den = conv(w)
    out = num / np.maximum(den, 1e-6)[:, None]
    return out.reshape(shp)


def stab_sigma(v):
    return float(v) / 100 * 4.0       # 0~4 프레임


# ------------------------------------------------------------ 뷰티 트랙
@dataclass
class BeautyTrack:
    lm: np.ndarray        # (N,K,468,2) 슬롯별 보정 랜드마크 (없으면 NaN)
    presence: np.ndarray  # (N,K) 0~1 (페이드)
    n: int

    def at(self, i):
        """반환 [(slot, pts, presence)] — 그 프레임에 있는 얼굴들."""
        if i < 0 or i >= self.n:
            return []
        out = []
        for k in range(self.lm.shape[1]):
            if self.presence[i, k] > 0.001:
                out.append((k, self.lm[i, k], float(self.presence[i, k])))
        return out


def _track_one(lm_k, stabilize, hold):
    """한 슬롯 (N,468,2) → (보정 랜드마크, presence)."""
    n = lm_k.shape[0]
    valid = ~np.isnan(lm_k[:, 0, 0])
    idx = np.where(valid)[0]
    filled = lm_k.copy()
    if len(idx):
        gaps = np.diff(idx)
        for a, g in zip(idx[:-1], gaps):
            if 1 < g <= hold + 1:
                b = a + g
                # v0.7.55 빈 구간 양끝이 서로 다른 사람(자리·크기가 크게 다름)이면 잇지 않음(얼굴이 날아가는 보간 방지)
                ca, cb = lm_k[a].mean(0), lm_k[b].mean(0)
                wa, wb = float(np.ptp(lm_k[a][:, 0])), float(np.ptp(lm_k[b][:, 0]))
                if np.hypot(*(ca - cb)) > 0.8 * max(wa, wb) or max(wa, wb) > 1.6 * max(1e-3, min(wa, wb)):
                    continue
                for t in range(1, g):
                    u = t / g
                    filled[a + t] = lm_k[a] * (1 - u) + lm_k[b] * u
    v2 = ~np.isnan(filled[:, 0, 0])
    sm = _smooth_series(np.nan_to_num(filled), v2, stab_sigma(stabilize))
    sm[~v2] = np.nan
    # 페이드 없음: 얼굴이 있는(보간 포함) 프레임은 100% 강도. 끝 프레임도 약해지지 않음.
    # 전환부(얼굴이 들어오고 나가는 지점)는 사용자가 키프레임·강도로 직접 조절.
    pres = v2.astype(np.float32)
    return sm, pres


def beauty_track(z, params, hold=15) -> BeautyTrack:
    lm = z["lm"].astype(np.float32)
    if lm.ndim == 3:                         # 옛 캐시(단일 얼굴) 호환 → 슬롯축 추가
        lm = lm[:, None, :, :]
    n, K = lm.shape[0], lm.shape[1]
    sm = np.full_like(lm, np.nan)
    pres = np.zeros((n, K), np.float32)
    stab = params.get("stabilize", 50)
    for k in range(K):
        sm[:, k], pres[:, k] = _track_one(lm[:, k], stab, hold)
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
    tracks: dict          # id -> {"f0": 시작 프레임, "box": (L,4) cx,cy,w,h, "obs": (L,) bool}

    def at(self, i):
        if 0 <= i < self.n:
            return self.frames[i]
        return []


def score_thr(sens):
    return 0.75 - float(sens) / 100 * 0.45       # 민감도 0→0.75, 100→0.30


def _hungarian(C):
    """최소 비용 배정(행 ≤ 열). 반환: 행마다 배정된 열."""
    n, m = C.shape
    u, v = np.zeros(n + 1), np.zeros(m + 1)
    p, way = np.zeros(m + 1, int), np.zeros(m + 1, int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, np.inf)
        used = np.zeros(m + 1, bool)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], np.inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = C[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    out = np.full(n, -1, int)
    for j in range(1, m + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def _assign(pairs):
    """후보 (비용, 트랙, 관측) → 배정 [(트랙, 관측)]. 서로 얽힌 묶음마다 전체 비용이 가장 작은 조합(v0.7.55).
    (예전: 가까운 순 — 두 사람이 엇갈릴 때 한 쌍이 먼저 잘못 묶이면 나머지도 뒤바뀜)"""
    if not pairs:
        return []
    par = {}

    def find(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for _, ti, oi in pairs:
        par[find(("t", ti))] = find(("o", oi))
    comps = {}
    for c, ti, oi in pairs:
        comps.setdefault(find(("t", ti)), []).append((c, ti, oi))
    out = []
    BIG = 1e6
    for ps in comps.values():
        ts = sorted({ti for _, ti, _ in ps})
        os_ = sorted({oi for _, _, oi in ps})
        if len(ts) == 1 or len(os_) == 1 or len(ts) * len(os_) > 900:
            used_t, used_o = set(), set()
            for c, ti, oi in sorted(ps):                    # 단순(또는 너무 큼) → 가까운 순
                if ti not in used_t and oi not in used_o:
                    used_t.add(ti); used_o.add(oi)
                    out.append((ti, oi))
            continue
        C = np.full((len(ts), len(os_)), BIG)
        for c, ti, oi in ps:
            C[ts.index(ti), os_.index(oi)] = c
        flip = C.shape[0] > C.shape[1]
        sol = _hungarian(C.T if flip else C)
        for a, b in enumerate(sol):
            r, k = (b, a) if flip else (a, b)
            if b >= 0 and C[r, k] < BIG:
                out.append((ts[r], os_[k]))
    return out


def _hist_d(a, b):
    """옷 색 차이 0(같음)~1. 한쪽이라도 색 정보가 없으면(화면 밖 등) 0 = 판단 안 함."""
    if a is None or b is None or a.sum() < 0.5 or b.sum() < 0.5:
        return 0.0
    return float(np.abs(a - b).sum()) / 2


def _greedy_tracks(per, n, hold, dist_gate=1.0, ratio_gate=1.8):
    """dist_gate·ratio_gate: 일반(얼굴) 매칭 허용치. 간판은 깜빡임·상자 출렁임이 커서 더 느슨하게 넘김.
    v0.7.55: 비용 = 거리 + 옷 색 차이, 얽힌 묶음은 최적 배정(_assign)."""
    tracks = []
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
                body = o[5] == KIND_BODY or t.get("kind") == KIND_BODY
                if dist < (1.8 if body else dist_gate) + 0.15 * dt and ratio < (2.5 if body else ratio_gate):
                    pairs.append((dist + (0.3 if body else 0) + 0.8 * _hist_d(t.get("hist"), o[6:6 + HIST]), ti, oi))
        used_o = set()
        for ti, oi in _assign(pairs):
            used_o.add(oi)
            t = live[ti]; o = obs[oi]
            dt = f - t["last"]
            v = (o[:2] - t["state"][:2]) / dt
            if o[5] == KIND_BODY:      # 몸 상자는 프레임마다 흔들림 → 속도는 아주 천천히, 크기 대비 제한
                t["vel"] = t["vel"] * 0.9 + v * 0.1
                lim = 0.15 * o[2]
                sp = float(np.hypot(*t["vel"]))
                if sp > lim:
                    t["vel"] *= lim / sp
            else:
                t["vel"] = t["vel"] * 0.6 + v * 0.4
            t["state"] = o[:4].copy(); t["last"] = f; t["obs"][f] = o; t["kind"] = o[5]
            h = o[6:6 + HIST]
            if h.sum() >= 0.5:
                t["hist"] = h.copy() if t.get("hist") is None else t["hist"] * 0.8 + h * 0.2
        for oi, o in enumerate(obs):
            if oi not in used_o:
                h = o[6:6 + HIST]
                tracks.append({"last": f, "state": o[:4].copy(), "vel": np.zeros(2, np.float32), "obs": {f: o},
                               "kind": o[5], "hist": h.copy() if h.sum() >= 0.5 else None})
    return tracks


def _link(tracks, hold, link_max):
    """끊긴 트랙 다시 잇기: A 끝 → B 시작 사이가 hold~link_max 프레임이고 위치(등속 예측)·크기·옷 색이 맞으면 같은 사람."""
    info = []
    for t in tracks:
        fs = sorted(t["obs"])
        a, b = fs[0], fs[-1]
        oa, ob = t["obs"][a], t["obs"][b]
        # 끝 속도(마지막 10프레임)
        back = [f for f in fs if f >= b - 10]
        v = (ob[:2] - t["obs"][back[0]][:2]) / max(1, b - back[0])
        hist_end = np.mean([t["obs"][f][6:6 + HIST] for f in fs[-10:]], 0)       # 간판 행은 뒤에 quad 가 더 있음
        hist_start = np.mean([t["obs"][f][6:6 + HIST] for f in fs[:10]], 0)
        info.append({"a": a, "b": b, "oa": oa, "ob": ob, "v": v, "he": hist_end, "hs": hist_start, "next": None,
                     "prev": None})
    cand = []
    for i, A in enumerate(info):
        for j, B in enumerate(info):
            g = B["a"] - A["b"]
            if i == j or g <= 0 or g > link_max:
                continue
            p = A["ob"][:2] + A["v"] * min(g, 30)
            size = max(A["ob"][2], B["oa"][2])
            dist = np.hypot(*(B["oa"][:2] - p)) / size
            ratio = max(A["ob"][2], B["oa"][2]) / max(1e-3, min(A["ob"][2], B["oa"][2]))
            hd = float(np.abs(A["he"] - B["hs"]).sum()) / 2          # 0(같음)~1
            lim = 1.2 + 0.04 * g
            if dist < lim and ratio < 1.7 and hd < 0.45:
                cand.append((dist / lim + hd + g / link_max * 0.3, i, j))
    cand.sort()
    for _, i, j in cand:
        if info[i]["next"] is None and info[j]["prev"] is None:
            # 순환 방지
            k = i
            while info[k]["prev"] is not None:
                k = info[k]["prev"]
            if k == j:
                continue
            info[i]["next"] = j
            info[j]["prev"] = i
    chains = []
    for i, I in enumerate(info):
        if I["prev"] is None:
            ch = [i]
            while info[ch[-1]]["next"] is not None:
                ch.append(info[ch[-1]]["next"])
            chains.append(ch)
    out = []
    for ch in chains:
        obs = {}
        for k in ch:
            obs.update(tracks[k]["obs"])
        out.append({"obs": obs, "linked": [(info[ch[x]]["b"], info[ch[x + 1]]["a"]) for x in range(len(ch) - 1)]})
    return out


def _dedupe(tracks):
    """같은 사람이 트랙 여러 개로 갈린 경우(몸 상자가 프레임마다 머리를 넣었다 뺐다 함) 하나로.
    같은 프레임에 함께 있는 시간의 절반 이상 가까이 붙어 있으면 합침. 둘 다 있으면 얼굴 검출 쪽을 씀."""
    tracks = sorted(tracks, key=lambda t: -len(t["obs"]))
    out = []
    for t in tracks:
        merged = False
        for u in out:
            pairs = []
            for f in t["obs"]:          # 같은 프레임 또는 앞뒤 2프레임 안의 상대 관측
                for d in (0, -1, 1, -2, 2):
                    if f + d in u["obs"]:
                        pairs.append((t["obs"][f], u["obs"][f + d]))
                        break
            if len(pairs) < 3:
                continue
            near = 0
            for a, b in pairs:
                size = max(a[2], b[2])
                dx, dy = abs(a[0] - b[0]) / size, abs(a[1] - b[1]) / size
                if a[5] == KIND_BODY and b[5] == KIND_BODY:      # 몸 상자 둘: 가로는 같고 세로만 어긋남
                    ok = dx < 0.6 and dy < 2.6
                elif a[5] == KIND_BODY or b[5] == KIND_BODY:
                    ok = np.hypot(dx, dy) < 1.6
                else:
                    ok = np.hypot(dx, dy) < 0.6
                near += ok
            common = pairs
            if near >= 0.5 * len(common):
                for f, o in t["obs"].items():
                    cur = u["obs"].get(f)
                    if cur is None or (cur[5] == KIND_BODY and o[5] != KIND_BODY) or \
                            (cur[5] == KIND_BODY and o[5] == KIND_BODY and o[2] > cur[2]):
                        u["obs"][f] = o          # 몸 상자 둘이면 큰 쪽(머리까지 들어간 상자)
                u["linked"] = u.get("linked", []) + t.get("linked", [])
                merged = True
                break
        if not merged:
            out.append(t)
    return out


def blur_track(z, params, two_pass=True) -> BlurTrack:
    det = z["det"]
    n = int(z["n"])
    thr = score_thr(params.get("sensitivity", 75))
    mins = float(params.get("min_size", 16))
    hold = int(params.get("hold", 15))
    link_max = int(params.get("link", 90))
    if len(det):
        body = det[:, 6] == KIND_BODY
        # 몸 검출은 점수 척도가 달라 기준을 낮춤(검출기 자체 기준 0.4)
        sthr = np.where(body, min(thr, 0.4 + (thr - 0.3) * 0.5), thr)
        keep = (det[:, 5] >= sthr) & ((det[:, 3] - det[:, 1]) >= mins)
        if not params.get("body", True):
            keep &= det[:, 6] != KIND_BODY
        det = det[keep]
    # v0.7.55 카메라 움직임을 뺀 '기준 좌표'에서 잇기·빈 구간·떨림 보정 → 끝에 프레임 좌표로 되돌림.
    # 놓친 구간·앞뒤 여유 구간의 블러가 팬·줌을 따라감(예전: 마지막 검출 자리에 멈춤).
    S, T = cam_world(z, n)
    per = [[] for _ in range(n)]
    for row in det:
        f = int(row[0])
        if f < n:
            x0, y0, x1, y1, sc = row[1:6]
            per[f].append(np.r_[np.array([((x0 + x1) / 2 - T[f, 0]) / S[f], ((y0 + y1) / 2 - T[f, 1]) / S[f],
                                          (x1 - x0) / S[f], (y1 - y0) / S[f], sc], np.float32),
                                row[6:7], row[7:]])
    raw = _greedy_tracks(per, n, hold)
    raw = [t for t in raw if len(t["obs"]) >= 3 or max(o[4] for o in t["obs"].values()) >= 0.6]
    linked = _link(raw, hold, link_max) if link_max > hold else [{"obs": t["obs"], "linked": []} for t in raw]
    linked = _dedupe(linked)
    frames = [[] for _ in range(n)]
    tracks = {}
    sig = stab_sigma(params.get("stabilize", 50))
    out_id = 0
    for t in sorted(linked, key=lambda t: min(t["obs"])):
        fs = sorted(t["obs"])
        a, b = fs[0], fs[-1]
        pre = hold if two_pass else 0
        a2, b2 = max(0, a - pre), min(n - 1, b + hold)
        L = b2 - a2 + 1
        arr = np.zeros((L, 4), np.float32); val = np.zeros(L, bool)
        for f in fs:
            arr[f - a2] = t["obs"][f][:4]; val[f - a2] = True
        idx = np.where(val)[0]
        for c in range(4):
            arr[:, c] = np.interp(np.arange(L), idx, arr[idx, c])
        okm = np.ones(L, bool)
        bridged = {(p - a2, q - a2) for p, q in t["linked"]}
        for p, q in zip(idx[:-1], idx[1:]):
            if q - p - 1 > hold and (p, q) not in bridged:
                okm[p + 1:q] = False
        nb = sum(1 for f in fs if t["obs"][f][5] == KIND_BODY)
        sg = max(sig * 3, 3.0) if nb > len(fs) / 2 else sig     # 몸 위주 트랙은 더 부드럽게
        arr = _smooth_series(arr, okm, sg)
        Sk = S[a2:a2 + L, None].astype(np.float32)
        arr[:, :2] = arr[:, :2] * Sk + T[a2:a2 + L].astype(np.float32)      # 기준 좌표 → 프레임 좌표
        arr[:, 2:] = arr[:, 2:] * Sk
        out_id += 1
        # 의심 트랙(검수용 표시, 자동 삭제 안 함): 관측이 아주 짧거나 평균 점수가 낮음
        msc = float(np.mean([t["obs"][f][4] for f in fs]))
        sus = len(fs) < 8 or msc < (0.5 if nb > len(fs) / 2 else 0.6)
        tracks[out_id] = {"f0": a2, "box": arr, "obs": val, "ok": okm, "sus": bool(sus), "score": msc}
        for k in range(L):
            if okm[k]:
                frames[a2 + k].append((out_id, *arr[k], bool(val[k])))
    return BlurTrack(frames, n, out_id, tracks)


def face_to_head(cx, cy, w, h, margin=160):
    """BlazeFace 얼굴 상자 → 머리 타원 (cx, cy, 지름w, 지름h, 0)."""
    s = margin / 100.0
    hw, hh = w * s, h * s * 1.25
    return (cx, cy - h * 0.12 * s, hw, hh, 0.0)
