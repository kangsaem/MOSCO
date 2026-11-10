"""영상 처리: 뷰티(피부 보정), 블러/모자이크, 키 화면. 입력·출력 uint16 RGB(0~65535).

속도: 타원 영역(ROI)만 float 로 바꿔 처리하고 나머지 픽셀은 원본 그대로 통과.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import models

K = 1 / 65535.0


# ------------------------------------------------------------ 공통
def ellipse_roi(ell, feather_px, w, h):
    cx, cy, ew, eh, ang = ell
    r = max(ew, eh) / 2 + feather_px * 2 + 2
    x0, y0 = int(max(0, cx - r)), int(max(0, cy - r))
    x1, y1 = int(min(w, cx + r)), int(min(h, cy + r))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1, y1


def _ellipse_alpha_full(ell, roi, feather_px):
    """원래 방식(전체 해상도). 페더가 작을 때 + 테스트 기준값."""
    x0, y0, x1, y1 = roi
    cx, cy, ew, eh, ang = ell
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    sh = 4
    cv2.ellipse(m, (int((cx - x0) * 16), int((cy - y0) * 16)), (max(1, int(ew / 2 * 16)), max(1, int(eh / 2 * 16))),
                ang, 0, 360, 1.0, -1, cv2.LINE_AA, sh)
    if feather_px > 0.5:
        m = cv2.GaussianBlur(m, (0, 0), feather_px)
    return m


ALPHA_SMALL_FROM = 8.0     # 페더가 이보다 크면 축소해서 계산


def ellipse_alpha(ell, roi, feather_px):
    """ROI 안의 타원 알파(0~1). 페더 = 가장자리 가우시안 폭(px).
    v0.7.6: 페더가 크면(뷰티 기본 σ≈70px) 1/q 크기에서 그리고 흐린 뒤 키움 — 원래 부드러운 신호라 차이 ≈0
    (8bit 기준 최대 ~1단계). 예전엔 이 블러 하나가 뷰티 렌더 1프레임 시간의 ~55%."""
    if feather_px <= ALPHA_SMALL_FROM:
        return _ellipse_alpha_full(ell, roi, feather_px)
    x0, y0, x1, y1 = roi
    W, H = x1 - x0, y1 - y0
    q = int(max(2, min(8, feather_px / 6)))
    cx, cy, ew, eh, ang = ell
    # 화면 가장자리에서 잘린 타원도 '화면 밖으로 이어지는' 그대로 흐리도록, 잘리기 전 범위로 격자를 잡고
    # 나중에 ROI 만 잘라 씀(예전 전체 해상도 방식은 잘린 경계에서 반사로 채움).
    R = max(ew, eh) / 2 + 3 * feather_px + 2 * q
    lx = int(np.ceil(max(0.0, x0 - (cx - R)) / q))
    ty = int(np.ceil(max(0.0, y0 - (cy - R)) / q))
    gx0, gy0 = x0 - lx * q, y0 - ty * q
    w = -(-(int(max(x1, cx + R)) - gx0) // q) + 1
    h = -(-(int(max(y1, cy + R)) - gy0) // q) + 1
    x0, y0 = gx0, gy0
    # 작은 격자 화소 j 의 중심 = 원래 좌표 j*q + (q-1)/2 (resize INTER_LINEAR 와 정렬).
    # cv2.ellipse 를 작은 격자에 그리면 AA 테두리(≈0.5화소)가 q 배로 커져 타원이 부풀므로 →
    # 화소마다 4×4 점을 찍어 타원 안쪽 비율(면적)을 직접 계산.
    ss = 4
    off = (np.arange(ss, dtype=np.float32) + 0.5) * (q / ss) - q / 2
    gx = (np.arange(w, dtype=np.float32) * q + (q - 1) / 2 - (cx - x0))[:, None] + off[None, :]   # (w, ss)
    gy = (np.arange(h, dtype=np.float32) * q + (q - 1) / 2 - (cy - y0))[:, None] + off[None, :]   # (h, ss)
    a = np.deg2rad(ang)
    ca, sa = np.float32(np.cos(a)), np.float32(np.sin(a))
    X = gx[None, None, :, :]            # (1,1,w,ss)
    Y = gy[:, :, None, None]            # (h,ss,1,1)
    u = (X * ca + Y * sa) / max(ew / 2, 1e-3)
    v = (-X * sa + Y * ca) / max(eh / 2, 1e-3)
    m = ((u * u + v * v) <= 1.0).mean(axis=(1, 3)).astype(np.float32)      # (h, w)
    m = cv2.GaussianBlur(m, (0, 0), feather_px / q)
    big = cv2.resize(m, (w * q, h * q), interpolation=cv2.INTER_LINEAR)
    return big[ty * q:ty * q + H, lx * q:lx * q + W]


def low_pass(img, sig, q=4):
    """큰 σ 가우시안(저주파) — 1/q 크기에서 계산 후 키움(v0.7.6, 주름 메우기). σ 가 작으면 그대로."""
    if sig < 8:
        return cv2.GaussianBlur(img, (0, 0), sig)
    h, w = img.shape[:2]
    sm = cv2.resize(img, (max(1, w // q), max(1, h // q)), interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.GaussianBlur(sm, (0, 0), sig / q), (w, h), interpolation=cv2.INTER_LINEAR)


def _ycc(rgb):
    """BT.709 Y, Cb, Cr (0~1, 크로마 중심 0)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - y) / 1.8556
    cr = (r - y) / 1.5748
    return y, cb, cr


def _rgb(y, cb, cr):
    r = y + 1.5748 * cr
    b = y + 1.8556 * cb
    g = (y - 0.2126 * r - 0.0722 * b) / 0.7152
    return np.stack([r, g, b], -1)


# ------------------------------------------------------------ 뷰티
def skin_ref_auto(rgbf, pts, x0, y0):
    """볼·이마 랜드마크 주변 피부색(Cb, Cr) 중앙값."""
    h, w = rgbf.shape[:2]
    vals = []
    for i in models.CHEEKS:
        x, y = int(pts[i, 0] - x0), int(pts[i, 1] - y0)
        if 2 <= x < w - 2 and 2 <= y < h - 2:
            vals.append(rgbf[y - 2:y + 3, x - 2:x + 3].reshape(-1, 3))
    if not vals:
        return None
    v = np.concatenate(vals)
    _, cb, cr = _ycc(v)
    return float(np.median(cb)), float(np.median(cr))


def skin_mask(rgbf, ref, rng):
    """피부색 키(0~1). ref=(cb,cr), rng=피부 범위 0~100."""
    y, cb, cr = _ycc(rgbf)
    sig = 0.012 + rng / 100 * 0.05
    d2 = (cb - ref[0]) ** 2 + (cr - ref[1]) ** 2
    m = np.exp(-d2 / (2 * sig * sig))
    m *= np.clip((y - 0.04) / 0.08, 0, 1)            # 아주 어두운 곳 제외
    return m.astype(np.float32)


def skin_mask_soft(rgbf, ref, rng, face_w):
    """주름 골(어둡고 색이 다른 가는 선)이 빠지지 않게: 흐린 영상으로 색 판정 → 구멍 메움 → 부드럽게.
    v0.7.17: 피부로 판정된 안쪽은 음영이 있어도 고르게 희게(ease-out), 바깥(0)은 그대로."""
    sig = max(1.0, face_w / 70)
    m = skin_mask(cv2.GaussianBlur(rgbf, (0, 0), sig), ref, rng)
    k = max(3, int(face_w / 30) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    m = cv2.GaussianBlur(m, (0, 0), sig)
    return m * (2.0 - m)      # 흰 영역 음영 줄이기: 중간값을 흰쪽으로 밀되 0·1 은 유지(경계 보존)


def face_region_mask(pts, roi, face_w, neck=0, feather_px=None):
    """얼굴 윤곽(랜드마크) 안쪽만 1 — 머리카락·배경 제외. 목 확장(neck)만큼 턱 아래로 내려 목 피부 유지.
    경계(특히 목 아래)는 타원 페더값(feather_px)으로 부드럽게 끊음."""
    x0, y0, x1, y1 = roi
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    off = np.array([x0, y0], np.float32)
    oval = (pts[models.FACE_OVAL] - off).astype(np.float32)
    polys = [oval]
    top, chin = pts[10] - off, pts[152] - off     # 이마 위·턱 끝
    axis = chin - top
    flen = float(np.hypot(*axis))
    if neck > 0 and flen > 1:                      # 아래쪽(턱) 윤곽점을 얼굴 축 방향으로 내려 목 사다리꼴 추가
        d = axis / flen
        proj = (oval - top) @ d
        low = oval[proj > flen * 0.55]
        if len(low):
            polys.append(low + d * (flen * neck / 100 * 1.1))   # 더 길게(목 확장 ×1.1)
    hull = cv2.convexHull((np.vstack(polys) * 16).astype(np.int32))
    cv2.fillConvexPoly(m, hull, 1.0, cv2.LINE_AA, 4)
    fp = feather_px if (feather_px and feather_px > 1) else face_w * 0.03
    k = max(1, int(fp * 0.6))              # 경계를 페더만큼 바깥으로 넓힌 뒤
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    return cv2.GaussianBlur(m, (0, 0), max(1.0, fp))   # 페더값으로 부드럽게 끊음


def _fill_groups(m, pts, off, groups, pad_px):
    """groups 각 볼록다각형 → m 에 채우고 pad_px 만큼만 넓힘."""
    g = np.zeros_like(m)
    for grp in groups:
        p = ((pts[grp] - off) * 16).astype(np.int32)
        cv2.fillPoly(g, [cv2.convexHull(p)], 1.0, cv2.LINE_AA, 4)
    k = max(1, int(pad_px))
    g = cv2.dilate(g, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    np.maximum(m, g, out=m)


def feature_mask(pts, roi, face_w):
    """눈·눈썹·입 제외 마스크(1=제외). v0.7.21: 눈·눈썹 제외 영역을 작게 — 눈가·눈밑 주름은 보정에 포함.
    (눈꺼풀 자체만 가리고, 바깥쪽 crow's feet·눈밑은 살림. 입은 그대로.)"""
    x0, y0, x1, y1 = roi
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    off = np.array([x0, y0], np.float32)
    _fill_groups(m, pts, off, (models.LEFT_EYE, models.RIGHT_EYE), face_w * 0.012)   # 눈: 아주 작게
    _fill_groups(m, pts, off, (models.LEFT_BROW, models.RIGHT_BROW), face_w * 0.015)  # 눈썹: 작게
    _fill_groups(m, pts, off, (models.LIPS,), face_w * 0.015)                         # 입: 작게(입가·팔자 주름 살림, 입술만 가림)
    return cv2.GaussianBlur(m, (0, 0), max(1.0, face_w * 0.012))


def nostril_mask(yimg, pts, x0, y0, face_w):
    """콧구멍 제외(1=제외): 콧구멍 랜드마크 둘레 작은 창 안에서 '주변 피부보다 어두운' 화소만 부드럽게.
    고정 원이 아니라 실제 어두운 그림자 모양을 따라감. 창 2개(작음)라 비용은 사실상 0.
    (v0.7.43: feature_mask 의 고정 원을 대체)"""
    h, w = yimg.shape
    m = np.zeros((h, w), np.float32)
    R = max(5, int(face_w * 0.11))               # v0.7.44 창 확대(콧구멍 그림자 더 넓게)
    for a, b in ((98, 97), (327, 326)):          # (오른 콧방울·콧구멍하단), (왼 …) — MediaPipe 468
        cx = int(round((pts[a, 0] + pts[b, 0]) / 2 - x0))
        cy = int(round((pts[a, 1] + pts[b, 1]) / 2 - y0))
        xa, xb = max(0, cx - R), min(w, cx + R + 1)
        ya, yb = max(0, cy - R), min(h, cy + R + 1)
        if xb <= xa or yb <= ya:
            continue
        patch = yimg[ya:yb, xa:xb]
        if patch.size == 0:
            continue
        skin_l = float(np.median(patch[patch >= np.percentile(patch, 60)]))   # 창 안 피부(밝은 쪽) 기준
        if skin_l <= 1e-3:
            continue
        lo, hi = skin_l * 0.60, skin_l * 0.92     # v0.7.44 피부 대비 60~92% 를 제외(어두운 영역 더 많이 반영)
        dark = np.clip((hi - patch) / max(1e-4, hi - lo), 0.0, 1.0)
        yy, xx = np.ogrid[ya:yb, xa:xb]           # 창 가장자리 원형 페더(사각형 티 제거)
        rad = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        fall = np.clip((R - rad) / max(1.0, R * 0.4), 0.0, 1.0)
        reg = m[ya:yb, xa:xb]
        np.maximum(reg, (dark * fall).astype(np.float32), out=reg)
    return cv2.GaussianBlur(m, (0, 0), max(1.0, face_w * 0.01))


class BeautyResult:
    __slots__ = ("out", "region", "skin", "alpha", "ells", "ref")

    def __init__(self):
        self.out = self.region = self.skin = self.alpha = self.ref = None
        self.ells = []


def _item_ell(it, p):
    s = it.ov.get("size", 100) / 100
    cx, cy, w, h, ang = it.ell
    return (cx, cy, w * s, h * s, ang)


def _center_ref(img, ell, x0, y0):
    """랜드마크가 없는 타원(추가 타원): 가운데 부분 피부색."""
    cx, cy, w, h, _ = ell
    r = max(2, int(min(w, h) * 0.12))
    x, y = int(cx - x0), int(cy - y0 + h * 0.05)
    H, W = img.shape[:2]
    if not (r <= x < W - r and r <= y < H - r):
        return None
    _, cb, cr = _ycc(img[y - r:y + r, x - r:x + r].reshape(-1, 3))
    return float(np.median(cb)), float(np.median(cr))


def beauty(frame16, items, p, frame_no=0, want_keys=False) -> BeautyResult:
    """frame16: (H,W,3) uint16. items: edits.Item 목록(자동·추가 타원). p: 뷰티 값."""
    res = BeautyResult()
    res.out = frame16
    H, W = frame16.shape[:2]
    if want_keys:
        res.region = np.zeros((H, W), np.float32)
        res.alpha = np.zeros((H, W), np.float32)
    o = None
    for it in items:
        if it.presence <= 0:
            continue
        ell = _item_ell(it, p)
        res.ells.append((it, ell))
        fe = it.ov.get("feather", p["feather"])
        feather = max(ell[2], ell[3]) * fe / 100 / 2
        roi = ellipse_roi(ell, feather, W, H)
        if roi is None:
            continue
        x0, y0, x1, y1 = roi
        src = (o if o is not None else frame16)[y0:y1, x0:x1]
        img = src.astype(np.float32) * K
        pts = it.pts
        face_w = float(np.ptp(pts[models.FACE_OVAL, 0])) if pts is not None else ell[2] / 1.5
        alpha = ellipse_alpha(ell, roi, feather) * it.presence
        if p.get("skin_ref"):
            ref = tuple(p["skin_ref"])
        elif pts is not None:
            ref = skin_ref_auto(img, pts, x0, y0)
        else:
            ref = _center_ref(img, ell, x0, y0)
        if ref is None:
            ref = (-0.06, 0.07)
        res.ref = res.ref or ref
        skin = skin_mask_soft(img, ref, p["skin_range"], face_w)
        if pts is not None:
            # 목 유지(더 길게) + 경계는 타원 페더로 부드럽게 끊음
            skin = skin * face_region_mask(pts, roi, face_w, p.get("neck", 0), feather)
        # 콧구멍·깊은 구멍 같은 아주 어두운 화소 보호(밝아짐 방지) — 흐리지 않은 원래 밝기로 판정
        yimg = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
        skin = skin * np.clip((yimg - 0.04) / 0.07, 0, 1)
        if pts is not None and p.get("features", True):
            skin = skin * (1 - nostril_mask(yimg, pts, x0, y0, face_w))   # 콧구멍 어두운 그림자 따라 제외
        region = alpha * skin
        if pts is not None and p.get("features", True):
            region = region * (1 - feature_mask(pts, roi, face_w))
        if want_keys:
            np.maximum(res.region[y0:y1, x0:x1], region, out=res.region[y0:y1, x0:x1])
            np.maximum(res.alpha[y0:y1, x0:x1], alpha, out=res.alpha[y0:y1, x0:x1])
        st = it.ov.get("strength", p["strength"]) / 100          # 매끈 강도(양방향)
        wf = it.ov.get("wrinkle", p.get("wrinkle", 0)) / 100     # 주름 메우기(어두운 골만)
        if st <= 0 and wf <= 0 and p["unify"] <= 0:
            continue
        res_img = img
        if st > 0 or wf > 0:
            target = img
            # ① 매끈: 양방향 필터(엣지 보존 → 구조 안 뭉개짐) + 질감 되살리기
            if st > 0:
                r = max(3, int(face_w / 26))                    # v0.7.36 반경 ↑(매끈 강화)
                base = cv2.bilateralFilter((np.clip(img, 0, 1) * 255).astype(np.uint8),
                                           d=(r | 1), sigmaColor=54, sigmaSpace=r).astype(np.float32) / 255
                fine = img - cv2.GaussianBlur(img, (0, 0), max(0.8, face_w / 350))
                sm = base + (p["texture"] / 100) * fine
                target = img + (sm - img) * st
            # ② 주름 메우기: 주름은 어두운 골 → 그 음영만 끌어올림(블러 없음)
            if wf > 0:
                sig = max(2.0, face_w / 22)
                low = low_pass(target, sig)
                detail = target - low
                g = cv2.cvtColor((np.clip(target, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
                gf = g.astype(np.float32) / 255
                gd = gf - low_pass(gf, sig)
                dark = np.clip(-gd * 8, 0, 1)[..., None]        # v0.7.36 골 검출 ↑(더 많은 주름 포착)
                target = target + (-np.minimum(detail, 0)) * dark * wf * 1.4   # v0.7.36 메우기량 ×1.4
            if p["grain"] > 0:
                rng = np.random.default_rng(frame_no * 131 + x0)
                target = target + rng.normal(0, p["grain"] / 100 * 0.03, img.shape[:2]).astype(np.float32)[..., None]
            res_img = img + (target - img) * region[..., None]
        if p["unify"] > 0:
            y, cb, cr = _ycc(res_img)
            u = region * (p["unify"] / 100 * 0.6)
            res_img = _rgb(y, cb + (ref[0] - cb) * u, cr + (ref[1] - cr) * u)
        if o is None:
            o = frame16.copy()
        o[y0:y1, x0:x1] = np.clip(res_img * 65535 + 0.5, 0, 65535).astype(np.uint16)
    if o is not None:
        res.out = o
    return res


def skin_key_full(frame16, p, ref=None, face_w=300):
    """피부 키(타원 무시) — 화면 전체. 미리보기용."""
    img = frame16.astype(np.float32) * K
    if ref is None:
        ref = tuple(p["skin_ref"]) if p.get("skin_ref") else (-0.06, 0.07)
    return skin_mask_soft(img, ref, p["skin_range"], face_w)


def pick_skin(frame16, x, y):
    """피부색 찍기: (x,y) 주변 7x7 평균 Cb, Cr."""
    h, w = frame16.shape[:2]
    x, y = int(np.clip(x, 3, w - 4)), int(np.clip(y, 3, h - 4))
    v = frame16[y - 3:y + 4, x - 3:x + 4].reshape(-1, 3).astype(np.float32) * K
    _, cb, cr = _ycc(v)
    return [round(float(np.median(cb)), 4), round(float(np.median(cr)), 4)]


# ------------------------------------------------------------ 블러
def _is_quad(it):
    """간판 아이템: pts = 4꼭짓점(8값). 뷰티 랜드마크(468점)·얼굴 블러(None)와 구분."""
    return it.pts is not None and np.asarray(it.pts).size == 8


def item_quad(it, p):
    """간판 4꼭짓점에 타원별 크기 % 적용(중심 기준)."""
    from .sign import scale_quad
    return scale_quad(it.pts, it.ov.get("size", 100) / 100)


def _shape(it, p, W, H):
    """아이템 → (표시 ell, roi, 알파, 기준 크기). 간판 = 4꼭짓점, 그 외 = 타원."""
    fe = it.ov.get("feather", p["feather"])
    if _is_quad(it):
        from .sign import quad_alpha, quad_ell
        q = item_quad(it, p)
        ell = quad_ell(q)
        size = min(ell[2], ell[3])                 # 간판: 짧은 쪽(≈ 글자 높이 + 여유) 기준
        feather = size * fe / 100 / 2
        roi = ellipse_roi(ell, feather, W, H)
        return ell, roi, (quad_alpha(q, roi, feather) if roi else None), size
    ell = _item_ell(it, p)
    feather = max(ell[2], ell[3]) * fe / 100 / 2
    roi = ellipse_roi(ell, feather, W, H)
    return ell, roi, (ellipse_alpha(ell, roi, feather) if roi else None), max(ell[2], ell[3])


def blur(frame16, items, p, want_alpha=False):
    """items: edits.Item 목록. 반환 (out16, alpha(H,W) 또는 None, [(item, ell)]).
    얼굴 블러 = 타원, 간판 블러(v0.7.8) = 4꼭짓점 사각형(it.pts)."""
    H, W = frame16.shape[:2]
    out = frame16.copy() if items else frame16
    full_a = np.zeros((H, W), np.float32) if want_alpha else None
    ells = []
    # v0.7.7 제외 타원: 그 영역은 어떤 블러도 안 함(4444 알파에서도 빠짐). 가장자리 = 그 타원의 페더.
    minus = None
    for it in items:
        if not getattr(it, "minus", False):
            continue
        ell, roi, a, _ = _shape(it, p, W, H)
        ells.append((it, ell))
        if roi is None:
            continue
        if minus is None:
            minus = np.zeros((H, W), np.float32)
        x0, y0, x1, y1 = roi
        np.maximum(minus[y0:y1, x0:x1], a * it.presence, out=minus[y0:y1, x0:x1])
    for it in items:
        if getattr(it, "minus", False):
            continue
        ell, roi, a, size = _shape(it, p, W, H)
        ells.append((it, ell))
        if roi is None:
            continue
        x0, y0, x1, y1 = roi
        if minus is not None:
            a = a * (1.0 - minus[y0:y1, x0:x1])
        img = out[y0:y1, x0:x1].astype(np.float32)
        st = max(1, it.ov.get("strength", p["strength"])) / 100
        quad = _is_quad(it)
        if p["kind"] == "mosaic":
            blk = max(3, int(size * st * (0.3 if quad else 0.14)))
            sw, sh = max(1, (x1 - x0) // blk), max(1, (y1 - y0) // blk)
            small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
            bl = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        else:
            bl = cv2.GaussianBlur(img, (0, 0), max(1.0, size * st * (0.25 if quad else 0.09)))
        res = img + (bl - img) * a[..., None]
        out[y0:y1, x0:x1] = np.clip(res + 0.5, 0, 65535).astype(np.uint16)
        if full_a is not None:
            np.maximum(full_a[y0:y1, x0:x1], a, out=full_a[y0:y1, x0:x1])
    return out, full_a, ells


def rgba_overlay(out16, alpha):
    """4444 오버레이: 블러된 RGB + 알파(블러 영역 밖은 알파 0)."""
    a16 = np.clip(alpha * 65535 + 0.5, 0, 65535).astype(np.uint16)
    return np.dstack([out16, a16])


# ------------------------------------------------------------ 검수 표시(타원·ID)
def draw_review(img, ells, color=(65535, 51400, 19500), ids=True, thick=2):
    """img uint8 RGB 위에 타원·ID. ells: [(item 또는 id, ell)]"""
    for it, ell in ells:
        fid = getattr(it, "tid", it)
        mi = getattr(it, "minus", False)
        col = (111, 211, 138) if mi else color                 # 제외 타원 = 초록(8bit 검수본)
        cx, cy, ew, eh, ang = ell
        if getattr(it, "pts", None) is not None and np.asarray(it.pts).size == 8:      # 간판 4꼭짓점
            cv2.polylines(img, [np.round(np.asarray(it.pts, np.float32).reshape(4, 2)).astype(np.int32)], True, col,
                          thick, cv2.LINE_AA)
        else:
            cv2.ellipse(img, (int(cx), int(cy)), (int(ew / 2), int(eh / 2)), ang, 0, 360, col, thick, cv2.LINE_AA)
        if ids and (fid is not None or mi):
            cv2.putText(img, ("-" if mi else "") + (str(fid) if fid is not None else ""),
                        (int(cx - ew / 2), int(cy - eh / 2) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2, cv2.LINE_AA)
    return img
