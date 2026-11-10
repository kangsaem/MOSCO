"""AUTO [샘플]: 클립마다 고르게 N장, 반반(왼쪽 원본·오른쪽 보정·가운데 흰 선) PNG/JPG 저장. 클립 값은 안 바꿈."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import analyze, decoder, edits, ff, fx
from .render import make_track


def _face_rect(items, W, H, pad=1.9):
    """가장 큰 타원(얼굴/머리)을 중심으로 한 정사각 크롭 영역. 얼굴 없으면 중앙 크롭."""
    best, area = None, -1.0
    for it in items:
        cx, cy, w, h, _ = it.ell
        a = float(w) * float(h)
        if a > area:
            area, best = a, (float(cx), float(cy), float(w), float(h))
    if best is None:
        s = int(min(W, H) * 0.8)
        x0 = (W - s) // 2
        y0 = (H - s) // 2
        return x0, y0, x0 + s, y0 + s
    cx, cy, w, h = best
    half = max(max(w, h) * 0.5 * pad, 80.0)
    half = min(half, min(W, H) / 2)           # 프레임보다 크지 않게
    x0 = cx - half
    y0 = cy - half
    side = half * 2
    x0 = max(0.0, min(x0, W - side))          # 경계 안으로 밀기
    y0 = max(0.0, min(y0, H - side))
    return int(round(x0)), int(round(y0)), int(round(x0 + side)), int(round(y0 + side))


def _full(out16, size_w, p16):
    """블러 샘플: 전체 보정본(크롭·반반 없음). BGR 저장용."""
    h, w = out16.shape[:2]
    if p16:
        bgr = np.ascontiguousarray(out16[..., ::-1])
    else:
        bgr = np.ascontiguousarray((out16 >> 8).astype(np.uint8)[..., ::-1])
    sw = size_w or w
    if sw != w:
        nh = round(h * sw / w)
        bgr = cv2.resize(bgr, (sw, nh), interpolation=cv2.INTER_AREA if sw < w else cv2.INTER_CUBIC)
    return bgr


def _compose_crop(orig16, out16, rect, size_w, p16):
    """얼굴 크롭을 확대해 왼쪽 원본 | 오른쪽 보정본 반반(가운데 흰 선). BGR 저장용."""
    x0, y0, x1, y1 = rect
    o = orig16[y0:y1, x0:x1]
    c = out16[y0:y1, x0:x1]
    h, w = o.shape[:2]
    half = w // 2
    comp = c.copy()
    comp[:, :half] = o[:, :half]              # 왼쪽 원본 / 오른쪽 보정본
    if p16:
        bgr = np.ascontiguousarray(comp[..., ::-1])              # RGB16 → BGR16
        cv2.line(bgr, (half, 0), (half, h), (65535, 65535, 65535), 2)
    else:
        bgr = np.ascontiguousarray((comp >> 8).astype(np.uint8)[..., ::-1])   # RGB16 → BGR8
        cv2.line(bgr, (half, 0), (half, h), (255, 255, 255), 2)
    sw = size_w or 1000                        # 기본 1000px 폭(정사각)으로 통일
    if sw != w:
        nh = round(h * sw / w)
        bgr = cv2.resize(bgr, (sw, nh), interpolation=cv2.INTER_AREA if sw < w else cv2.INTER_CUBIC)
    return bgr


def imwrite(path: Path, img, params=None):
    """유니코드(한글) 경로 안전 저장.

    cv2.imwrite 는 윈도우에서 비ASCII 경로에 조용히 실패(False 반환, 파일 생성 안 됨, 예외 없음)한다.
    → cv2.imencode 로 메모리에 인코딩한 뒤 numpy.tofile 로 쓰면 유니코드 경로도 정상 저장된다.
    """
    ok, buf = cv2.imencode(path.suffix, img, params or [])
    if not ok:
        raise RuntimeError(f"이미지 인코딩 실패: {path.name}")
    buf.tofile(str(path))
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"샘플 저장 실패(경로 확인): {path}")


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        p = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not p.exists():
            return p
        i += 1


def filename(src, opts, idx):
    import datetime
    base = (opts.get("name") or "").strip()
    stem = f"{base}_{src.name}" if base else src.name
    tags = []
    if opts.get("date"):
        tags.append(datetime.datetime.now().strftime("%y%m%d_%H%M"))
    if opts.get("num", True):
        tags.append(str(idx))
    name = stem + ("_" + "_".join(tags) if tags else "")
    ext = {"png8": ".png", "png16": ".png", "jpg": ".jpg"}.get(opts.get("fmt", "png8"), ".png")
    return name + ext


def render_samples(src, info: ff.Info, opts: dict, progress=None, cancel=None):
    """opts: out_dir, name, size(가로 px|0=원본), fmt(png8/png16/jpg), num, count. 반환 저장 경로 목록."""
    out_dir = Path(opts["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    count = int(opts.get("count", 6))
    n = max(1, info.frames)
    idxs = [int(round((k + 0.5) * n / count)) for k in range(count)]
    idxs = [min(n - 1, max(0, i)) for i in idxs]
    bskip = float(opts.get("bottom_skip", 0.0) or 0.0)
    # 종류별(뷰티 / 얼굴블러 / 간판블러, 동시 블러면 둘 다) 분석 캐시·트랙·리졸버 — 렌더 build_plan 과 같은 규칙
    kinds = src.blur_kinds() or ["beauty"]
    plan = []
    for kind in kinds:
        kb = bskip if kind != "beauty" else 0.0
        z = analyze.load(info, kind, analyze.level_for(kind, src.params_for(kind)), kb)
        track = make_track(src, z, {}, kind) if z is not None else None
        plan.append((kind, track, edits.Resolver(src, track, kind)))
    p16 = opts.get("fmt") == "png16"
    saved = []
    for k, i in enumerate(idxs):
        if cancel is not None and cancel.is_set():
            raise analyze.Cancelled()
        fr = decoder.grab(info, i, 16)

        def items_of(kind, track, resolver):
            items = resolver.items(i)
            if track is None:
                # 분석 전: 그 프레임만 검출
                from .ui_main import temp_items     # 지연 import(순환 방지)
                items = temp_items(src, (fr >> 8).astype(np.uint8), mode=kind) + items
            return items
        sw = opts.get("size") or 0
        if kinds == ["beauty"]:
            # 뷰티: 얼굴 확대 반반(왼쪽 원본 / 오른쪽 보정본)
            items = items_of(*plan[0])
            r = fx.beauty(fr, items, src.beauty, i)
            ell_items = [it for it, _ in r.ells]
            rect = _face_rect(ell_items or items, info.w, info.h)
            bgr = _compose_crop(fr, r.out, rect, sw, p16)
        else:
            # 블러: 전체 블러 보정본만(크롭·반반 없음). 종류마다 자기 설정값으로 이어서 블러(얼굴 → 간판)
            out = fr
            for kind, track, resolver in plan:
                out, _, _ = fx.blur(out, items_of(kind, track, resolver), src.params_for(kind), False)
            bgr = _full(out, sw, p16)
        path = unique(out_dir / filename(src, opts, k + 1))
        q = [cv2.IMWRITE_JPEG_QUALITY, 95] if opts.get("fmt") == "jpg" else []
        imwrite(path, bgr, q)
        saved.append(str(path))
        if progress:
            progress((k + 1) / count)
    return saved
