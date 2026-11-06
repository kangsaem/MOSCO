"""AUTO [샘플]: 클립마다 고르게 N장, 반반(왼쪽 원본·오른쪽 보정·가운데 흰 선) PNG/JPG 저장. 클립 값은 안 바꿈."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import analyze, decoder, edits, ff, fx
from .render import make_track


def _compose(orig16, out16, size_w):
    """원본|보정 반반(가운데 흰 2px). 8bit BGR 로 저장용."""
    h, w = orig16.shape[:2]
    half = w // 2
    comp = out16.copy()
    comp[:, :half] = orig16[:, :half]
    img = np.ascontiguousarray((comp >> 8).astype(np.uint8)[..., ::-1])   # RGB16 → BGR8
    cv2.line(img, (half, 0), (half, h), (255, 255, 255), 2)
    if size_w and size_w != w:
        nh = round(h * size_w / w)
        img = cv2.resize(img, (size_w, nh), interpolation=cv2.INTER_AREA if size_w < w else cv2.INTER_CUBIC)
    return img


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
    z = analyze.load(info, src.mode)
    track = make_track(src, z, {}) if z is not None else None
    resolver = edits.Resolver(src, track)
    p16 = opts.get("fmt") == "png16"
    saved = []
    for k, i in enumerate(idxs):
        if cancel is not None and cancel.is_set():
            raise analyze.Cancelled()
        fr = decoder.grab(info, i, 16)
        items = resolver.items(i)
        if track is None:
            # 분석 전: 그 프레임만 검출
            from .ui_main import temp_items     # 지연 import(순환 방지)
            items = temp_items(src, (fr >> 8).astype(np.uint8)) + items
        if src.mode == "beauty":
            out = fx.beauty(fr, items, src.beauty, i).out
        else:
            out, _, _ = fx.blur(fr, items, src.blur, False)
        path = unique(out_dir / filename(src, opts, k + 1))
        sw = opts.get("size") or info.w
        if p16:
            half = fr.shape[1] // 2
            comp = out.copy()
            comp[:, :half] = fr[:, :half]
            bgr = np.ascontiguousarray(comp[..., ::-1])       # RGB16 → BGR16
            cv2.line(bgr, (half, 0), (half, fr.shape[0]), (65535, 65535, 65535), 2)
            if sw != fr.shape[1]:
                nh = round(fr.shape[0] * sw / fr.shape[1])
                bgr = cv2.resize(bgr, (sw, nh), interpolation=cv2.INTER_AREA if sw < fr.shape[1] else cv2.INTER_CUBIC)
            cv2.imwrite(str(path), bgr)
        else:
            img = _compose(fr, out, sw)
            q = [cv2.IMWRITE_JPEG_QUALITY, 95] if opts.get("fmt") == "jpg" else []
            cv2.imwrite(str(path), img, q)
        saved.append(str(path))
        if progress:
            progress((k + 1) / count)
    return saved
