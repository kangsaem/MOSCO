"""백그라운드 작업(분석·프록시·미리보기·렌더). 화면이 멈추지 않도록 스레드 풀에서 실행."""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class JobSignals(QObject):
    progress = Signal(float)
    done = Signal(object)
    error = Signal(str)


class Job(QRunnable):
    """fn() 또는 fn(progress_cb, cancel_event) 실행."""

    def __init__(self, fn, *, with_progress: bool = False):
        super().__init__()
        self.fn = fn
        self.with_progress = with_progress
        self.cancel = threading.Event()
        self.signals = JobSignals()
        self.started = False

    def _emit(self, sig, *a):
        try:
            sig.emit(*a)
        except RuntimeError:      # 프로그램 종료 중(신호 객체 삭제됨)
            pass

    def run(self):
        self.started = True
        try:
            r = self.fn(lambda v: self._emit(self.signals.progress, v), self.cancel) if self.with_progress else self.fn()
            self._emit(self.signals.done, r)
        except Exception as e:  # noqa: BLE001
            msg = str(e) or e.__class__.__name__
            if e.__class__.__name__ == "Cancelled":
                msg = "취소됨"
            else:
                traceback.print_exc()
            self._emit(self.signals.error, msg)


_alive: set = set()


def submit(pool: QThreadPool, job: Job, priority: int = 0) -> Job:
    """작업이 끝날 때까지 참조를 유지(파이썬 GC 로 신호가 사라지는 것 방지)."""
    job.setAutoDelete(False)
    _alive.add(job)
    job.signals.done.connect(lambda *_: _alive.discard(job))
    job.signals.error.connect(lambda *_: _alive.discard(job))
    pool.start(job, priority)
    return job


def bump(pool: QThreadPool, job: Job, priority: int = 10) -> bool:
    """아직 시작 안 한 작업을 맨 앞으로."""
    if job.started:
        return False
    if pool.tryTake(job):
        pool.start(job, priority)
        return True
    return False


def make_pool(n: int) -> QThreadPool:
    p = QThreadPool()
    p.setMaxThreadCount(n)
    return p


PROBE_POOL = make_pool(4)     # ffprobe
ANALYZE_POOL = make_pool(3)   # 분석·트랙. 뷰티 분석은 순차라 여러 클립 동시(블러는 analyze 안에서 한 번에 하나)

PREVIEW_POOL = make_pool(4)

DETECT_POOL = make_pool(2)    # AUTO 장면인식(프리뷰와 분리 → 프리뷰가 밀리지 않음)

RENDER_POOL = make_pool(1)
