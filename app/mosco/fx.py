"""영상 처리: 뷰티(피부 보정), 블러/모자이크, 키 화면. 입력·출력 uint16 RGB(0~65535).

속도: 타원 영역(ROI)만 float 로 바꿔 처리하고 나머지 픽셀은 원본 그대로 통과.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import models

try:
    _guided = cv2.ximgproc.guidedFilter
except AttributeError:      # opencv-contrib 없음 → 양방향 필터로 대체
    _guided = None

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


def ellipse_alpha(ell, roi, feather_px):
    """ROI 안의 타원 알파(0~1). 페더 = 가장자리 가우시안 폭(px). 4배 해상도로 그린 뒤 줄여 계단 없음."""
    x0, y0, x1, y1 = roi
    cx, cy, ew, eh, ang = ell
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    sh = 4
    cv2.ellipse(m, (int((cx - x0) * 16), int((cy - y0) * 16)), (max(1, int(ew / 2 * 16)), max(1, int(eh / 2 * 16))),
                ang, 0, 360, 1.0, -1, cv2.LINE_AA, sh)
    if feather_px > 0.5:
        m = cv2.GaussianBlur(m, (0, 0), feather_px)
    return m


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
    """주름 골(어둡고 색이 다른 가는 선)이 빠지지 않게: 흐린 영상으로 색 판정 → 구멍 메움 → 부드럽게."""
    sig = max(1.0, face_w / 70)
    m = skin_mask(cv2.GaussianBlur(rgbf, (0, 0), sig), ref, rng)
    k = max(3, int(face_w / 30) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return cv2.GaussianBlur(m, (0, 0), sig)


def feature_mask(pts, roi, face_w):
    """눈·눈썹·입 제외 마스크(1=제외)."""
    x0, y0, x1, y1 = roi
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    off = np.array([x0, y0], np.float32)
    for grp in models.FEATURES:
        p = ((pts[grp] - off) * 16).astype(np.int32)
        cv2.fillPoly(m, [cv2.convexHull(p)], 1.0, cv2.LINE_AA, 4)
    k = max(1, int(face_w * 0.035))
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    return cv2.GaussianBlur(m, (0, 0), max(1.0, face_w * 0.02))


def _smooth_img(img, face_w):
    r = max(2, int(face_w / 45))
    guide = img[..., 1].copy()
    if _guided is not None:
        b = _guided(guide, img, r, 0.004)
        return _guided(guide, b, r, 0.004)
    return cv2.bilateralFilter(img, r * 2 + 1, 0.08, r * 1.5)


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
                r = max(3, int(face_w / 28))
                base = cv2.bilateralFilter((np.clip(img, 0, 1) * 255).astype(np.uint8),
                                           d=(r | 1), sigmaColor=42, sigmaSpace=r).astype(np.float32) / 255
                fine = img - cv2.GaussianBlur(img, (0, 0), max(0.8, face_w / 350))
                sm = base + (p["texture"] / 100) * fine
                target = img + (sm - img) * st
            # ② 주름 메우기: 주름은 어두운 골 → 그 음영만 끌어올림(블러 없음)
            if wf > 0:
                sig = max(2.0, face_w / 22)
                low = cv2.GaussianBlur(target, (0, 0), sig)
                detail = target - low
                g = cv2.cvtColor((np.clip(target, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
                gd = g.astype(np.float32) / 255 - cv2.GaussianBlur(g.astype(np.float32) / 255, (0, 0), sig)
                dark = np.clip(-gd * 6, 0, 1)[..., None]
                target = target + (-np.minimum(detail, 0)) * dark * wf
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
def blur(frame16, items, p, want_alpha=False):
    """items: edits.Item 목록. 반환 (out16, alpha(H,W) 또는 None, [(item, ell)])."""
    H, W = frame16.shape[:2]
    out = frame16.copy() if items else frame16
    full_a = np.zeros((H, W), np.float32) if want_alpha else None
    ells = []
    for it in items:
        ell = _item_ell(it, p)
        ells.append((it, ell))
        fe = it.ov.get("feather", p["feather"])
        feather = max(ell[2], ell[3]) * fe / 100 / 2
        roi = ellipse_roi(ell, feather, W, H)
        if roi is None:
            continue
        x0, y0, x1, y1 = roi
        a = ellipse_alpha(ell, roi, feather)
        img = out[y0:y1, x0:x1].astype(np.float32)
        size = max(ell[2], ell[3])
        st = max(1, it.ov.get("strength", p["strength"])) / 100
        if p["kind"] == "mosaic":
            blk = max(3, int(size * st * 0.14))
            sw, sh = max(1, (x1 - x0) // blk), max(1, (y1 - y0) // blk)
            small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
            bl = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        else:
            bl = cv2.GaussianBlur(img, (0, 0), max(1.0, size * st * 0.09))
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
        cx, cy, ew, eh, ang = ell
        cv2.ellipse(img, (int(cx), int(cy)), (int(ew / 2), int(eh / 2)), ang, 0, 360, color, thick, cv2.LINE_AA)
        if ids and fid is not None:
            cv2.putText(img, str(fid), (int(cx - ew / 2), int(cy - eh / 2) - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, color, 2, cv2.LINE_AA)
    return img
