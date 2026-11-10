"""상주 디코더(PyAV): 파일을 한 번 열어두고 아무 프레임이나 수 ms 로 읽는다.

ProRes 는 모든 프레임이 독립이라 seek 가 싸고, 스크럽마다 ffmpeg 를 새로 띄우던 ~100ms 오버헤드가 사라진다.
작은 LRU 램 캐시(원본 16bit, 플레이헤드 주변만)로 되돌아오기·값 변경 때 재디코딩도 피한다.
PyAV 가 없거나 실패하면 ff.grab(서브프로세스)로 폴백.
"""
from __future__ import annotations

import threading

import numpy as np

from . import ff

try:
    import av
    av.logging.set_level(av.logging.ERROR)
    HAVE_AV = True
except Exception:  # noqa: BLE001
    HAVE_AV = False

CACHE_FRAMES = 300          # 소스당 원본 16bit 캐시 상한(장수)
CACHE_BYTES = 3_800_000_000  # 소스당 용량 상한(FHD 300장 ≈ 3.7GB 그대로, 4K 는 ≈ 76장) — 해상도가 커도 램이 안 불어나게
IDLE_FRAMES = 24            # 지금 보고 있지 않은 소스는 이만큼만 남김(소스 수만큼 캐시가 쌓이는 것 방지)


class FrameSource:
    def __init__(self, path):
        self.path = path
        self.c = av.open(path)
        self.st = self.c.streams.video[0]
        try:
            self.st.thread_type = "AUTO"       # 멀티스레드 디코딩
        except Exception:  # noqa: BLE001
            pass
        self.tb = self.st.time_base
        self.rate = self.st.average_rate
        self.lock = threading.Lock()
        self.cache: dict[int, np.ndarray] = {}
        self.order: list[int] = []

    def _store(self, n, arr):
        if n in self.cache:
            return
        self.cache[n] = arr
        self.order.append(n)
        limit = max(8, min(CACHE_FRAMES, CACHE_BYTES // max(1, arr.nbytes)))
        while len(self.order) > limit:
            self.cache.pop(self.order.pop(0), None)

    def trim(self, keep):
        """캐시를 최근 keep 장만 남김. 디코딩 중이면(잠김) 건너뜀 — 화면을 기다리게 하지 않음."""
        if not self.lock.acquire(timeout=0.02):
            return
        try:
            while len(self.order) > keep:
                self.cache.pop(self.order.pop(0), None)
        finally:
            self.lock.release()

    def _idx(self, fr):
        if fr.pts is None:
            return None
        return int(round(float(fr.pts * self.tb) * float(self.rate)))

    def _decode_to(self, n):
        ts = int(n / float(self.rate) / self.tb)
        self.c.seek(max(0, ts), stream=self.st, backward=True, any_frame=False)
        got = None
        for fr in self.c.decode(self.st):
            idx = self._idx(fr)
            if idx is None:
                continue
            arr = fr.to_ndarray(format="rgb48le")        # (H,W,3) uint16
            self._store(idx, arr)
            if idx >= n:
                got = self.cache.get(n, arr if idx == n else None)
                break
        return got if got is not None else self.cache.get(n)

    def frame(self, n, depth=16, size=None):
        n = max(0, int(n))
        with self.lock:
            a = self.cache.get(n)
            if a is None:
                a = self._decode_to(n)
            else:
                try:                              # LRU 갱신
                    self.order.remove(n)
                    self.order.append(n)
                except ValueError:
                    pass
        if a is None:
            return None
        if size is not None and (size[0] != a.shape[1] or size[1] != a.shape[0]):
            import cv2
            a = cv2.resize(a, size, interpolation=cv2.INTER_AREA if size[0] < a.shape[1] else cv2.INTER_CUBIC)
        if depth == 8:
            return np.ascontiguousarray((a >> 8).astype(np.uint8))
        return np.ascontiguousarray(a)

    def close(self):
        try:
            self.c.close()
        except Exception:  # noqa: BLE001
            pass
        self.cache.clear()
        self.order.clear()


_sources: dict[str, FrameSource] = {}
_lock = threading.Lock()


def source(path) -> FrameSource | None:
    if not HAVE_AV:
        return None
    with _lock:
        s = _sources.get(path)
        if s is None:
            try:
                s = FrameSource(path)
            except Exception:  # noqa: BLE001
                return None
            _sources[path] = s
        return s


def grab(info, n, depth=16, size=None):
    """미리보기용 프레임. PyAV 상주 디코더 → 실패하면 ff.grab 폴백."""
    s = source(info.path)
    if s is not None:
        try:
            a = s.frame(n, depth, size)
            if a is not None:
                return a
        except Exception:  # noqa: BLE001
            pass
    return ff.grab(info, n, depth, size)


def focus(path):
    """지금 보는 소스만 캐시를 넉넉히 — 나머지 소스는 IDLE_FRAMES 장으로 줄임."""
    with _lock:
        others = [s for p, s in _sources.items() if p != path]
    for s in others:
        s.trim(IDLE_FRAMES)


def keep_only(paths):
    """현재 프로젝트에 없는 소스의 디코더·캐시 정리."""
    keep = set(paths)
    with _lock:
        gone = [p for p in _sources if p not in keep]
        for p in gone:
            _sources.pop(p).close()


def close_all():
    with _lock:
        for s in _sources.values():
            s.close()
        _sources.clear()
