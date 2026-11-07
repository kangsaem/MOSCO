"""렌더: 분석(없으면) → 트랙 후처리 → 프레임별 처리(여러 스레드, 순서 유지) → ProRes."""
from __future__ import annotations

import csv
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import __version__, analyze, edits, ff, fx, paths
from .project import Source


def auto_workers() -> int:
    return max(2, min(12, (os.cpu_count() or 4) - 2))


def pick_workers(rs: dict) -> int:
    """렌더 설정 'workers' (0 = 자동). 1..코어 수로 제한."""
    try:
        w = int(rs.get("workers") or 0)
    except (TypeError, ValueError):
        w = 0
    return min(w, os.cpu_count() or w) if w > 0 else auto_workers()


LOG_HEAD = ["시각", "버전", "소스", "모드", "프레임", "분석초", "분석fps", "렌더초", "렌더fps",
            "작업자", "코어", "디코딩", "출력"]


def write_log(st: dict):
    """logs/render_log.csv 에 한 줄 추가(엑셀에서 한글 깨지지 않게 utf-8-sig). 실패해도 렌더엔 영향 없음."""
    try:
        f = paths.log_dir() / "render_log.csv"
        new = not f.exists()
        with f.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(LOG_HEAD)
            w.writerow([st.get(k, "") for k in LOG_HEAD])
    except Exception:  # noqa: BLE001
        pass


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


def render(src: Source, rs: dict, out_dir: str, progress=None, cancel=None, workers=None, stats=None):
    """stats(dict) 를 주면 프레임·분석/렌더 시간·fps·작업자 수를 채워 준다(logs/render_log.csv 에도 기록)."""
    info = ff.probe(src.path)
    hw = bool(rs.get("hw"))
    workers = workers or pick_workers(rs)
    level = analyze.level_for(src.mode, src.params())
    cached = analyze.load(info, src.mode, level) is not None
    w_an = 0.0 if cached else 0.35

    def p_an(v):
        if progress:
            progress(v * w_an)
    t0 = time.perf_counter()
    z = analyze.analyze(info, src.mode, p_an, cancel, hw, workers if int(rs.get("workers") or 0) > 0 else None,
                        level=level)
    t_an = 0.0 if cached else time.perf_counter() - t0
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
        t1 = time.perf_counter()
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
        t_r = time.perf_counter() - t1          # 인코딩 마무리(close)까지 포함
    except BaseException:
        for k, wtr in writers.items():
            wtr.kill()
            try:
                Path(names[k]).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        raise
    st = {"시각": time.strftime("%Y-%m-%d %H:%M:%S"), "버전": __version__, "소스": src.name, "모드": src.mode,
          "프레임": i, "분석초": round(t_an, 1), "분석fps": round(i / t_an, 2) if t_an > 0 else "캐시",
          "렌더초": round(t_r, 1), "렌더fps": round(i / t_r, 2) if t_r > 0 else 0,
          "작업자": workers, "코어": os.cpu_count() or 0, "디코딩": "NVDEC" if hw else "CPU",
          "출력": "+".join(k for k in ("main", "overlay", "review") if k in names and (
              (k == "main" and rs.get("main", True)) or (k == "overlay" and src.mode == "blur" and rs.get("overlay", True))
              or (k == "review" and rs.get("review"))))}
    write_log(st)
    if stats is not None:
        stats.update(st)
    if progress:
        progress(1.0)
    return {k: str(v) for k, v in names.items() if (k == "main" and rs.get("main", True)) or
            (k == "overlay" and src.mode == "blur" and rs.get("overlay", True)) or (k == "review" and rs.get("review"))}
