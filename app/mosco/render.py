"""렌더: 분석(없으면) → 트랙 후처리 → 프레임별 처리(여러 스레드, 순서 유지) → ProRes."""
from __future__ import annotations

import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import analyze, edits, ff, fx
from .project import Source


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        p = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not p.exists():
            return p
        i += 1


def out_names(src: Source, rs: dict, out_dir: str):
    base = (rs.get("name") or "").strip()
    stem = f"{base}_{src.name}" if base else src.name
    d = Path(out_dir)
    return {"main": unique(d / f"{stem}_MOSCO.mov"),
            "overlay": unique(d / f"{stem}_BLUR4444.mov"),
            "review": unique(d / f"{stem}_CHECK.mp4")}


def make_track(src: Source, z, rs=None):
    rs = rs or {}
    if src.mode == "beauty":
        return analyze.beauty_track(z, src.beauty)
    return analyze.blur_track(z, src.blur, rs.get("two_pass", True))


def process(src: Source, resolver, frame16, i, want_alpha=False):
    """프레임 1장 처리 → (out16, alpha 또는 None, 검수용 타원 [(item, ell)])."""
    items = resolver.items(i)
    if src.mode == "beauty":
        r = fx.beauty(frame16, items, src.beauty, i)
        return r.out, None, r.ells
    return fx.blur(frame16, items, src.blur, want_alpha)


def render(src: Source, rs: dict, out_dir: str, progress=None, cancel=None, workers=None):
    info = ff.probe(src.path)
    hw = bool(rs.get("hw"))
    w_an = 0.35 if analyze.load(info, src.mode) is None else 0.0

    def p_an(v):
        if progress:
            progress(v * w_an)
    z = analyze.analyze(info, src.mode, p_an, cancel, hw)
    track = make_track(src, z, rs)
    resolver = edits.Resolver(src, track)
    names = out_names(src, rs, out_dir)
    writers = {}
    try:
        if rs.get("main", True):
            writers["main"] = ff.Writer(str(names["main"]), info, rs.get("prores", "422 HQ"), audio=rs.get("audio", True))
        if src.mode == "blur" and rs.get("overlay", True):
            writers["overlay"] = ff.Writer(str(names["overlay"]), info, alpha=True, audio=False)
        if rs.get("review"):
            writers["review"] = ff.Writer(str(names["review"]), info, h264=True, audio=rs.get("audio", True))
        if not writers:
            raise RuntimeError("출력할 파일이 없습니다(렌더 설정)")
        want_a = "overlay" in writers
        reader = ff.Reader(info, 0, 16, hw=hw)
        n = int(z["n"])
        workers = workers or max(2, min(12, (os.cpu_count() or 4) - 2))
        pool = ThreadPoolExecutor(workers)
        q = deque()
        i = 0
        done = 0

        def emit(fut):
            nonlocal done
            out, a, ells = fut.result()
            if "main" in writers:
                writers["main"].write(out)
            if "overlay" in writers:
                if a is None:
                    a = np.zeros(out.shape[:2], np.float32)
                writers["overlay"].write(fx.rgba_overlay(out, a))
            if "review" in writers:
                rv = (out >> 8).astype(np.uint8)
                fx.draw_review(rv, ells, color=(255, 200, 76), ids=src.mode == "blur")
                writers["review"].write(rv)
            done += 1
            if progress and done % 5 == 0:
                progress(w_an + (1 - w_an) * min(1.0, done / max(1, n)))
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise analyze.Cancelled()
                fr = reader.read()
                if fr is None:
                    break
                q.append(pool.submit(process, src, resolver, fr, i, want_a))
                i += 1
                while len(q) >= workers * 2:
                    emit(q.popleft())
            while q:
                emit(q.popleft())
        finally:
            reader.close()
            pool.shutdown(cancel_futures=True)
        if i == 0:
            raise RuntimeError("프레임을 읽지 못했습니다")
        for k, wtr in list(writers.items()):
            wtr.close()
            writers.pop(k)
    except BaseException:
        for k, wtr in writers.items():
            wtr.kill()
            try:
                Path(names[k]).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        raise
    if progress:
        progress(1.0)
    return {k: str(v) for k, v in names.items() if (k == "main" and rs.get("main", True)) or
            (k == "overlay" and src.mode == "blur" and rs.get("overlay", True)) or (k == "review" and rs.get("review"))}
