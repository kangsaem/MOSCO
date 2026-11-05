"""공유 상태: 프로젝트, 현재 소스, 분석 결과·진행, 실행취소."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QObject, Signal

from . import analyze, ff, jobs
from .project import Project, Settings, Source

UNDO_MAX = 80


class AppState(QObject):
    changed = Signal()                 # 목록·값 변경(화면 갱신)
    current_changed = Signal()         # 선택 소스 바뀜
    params_changed = Signal()          # 현재 소스 값 바뀜(미리보기 다시)
    analysis_changed = Signal(str)     # 분석 진행·완료 (경로)
    message = Signal(str)

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.project = Project()
        self.cur = -1
        self.dirty = False
        self.undo_stack = []
        self.infos: dict[str, ff.Info] = {}
        self.errors: dict[str, str] = {}
        self.an: dict = {}             # (path, mode) -> {"z", "p", "state", "err", "job"}
        self._tracks = {}

    # ------------------------------------------------------------ 소스
    def source(self) -> Source | None:
        if 0 <= self.cur < len(self.project.sources):
            return self.project.sources[self.cur]
        return None

    def info(self, path) -> ff.Info | None:
        return self.infos.get(path)

    def set_project(self, p: Project):
        self.project = p
        self.cur = 0 if p.sources else -1
        self.undo_stack.clear()
        self.dirty = False
        for s in p.sources:
            self._probe(s.path)
        self.changed.emit()
        self.current_changed.emit()

    def add_files(self, files):
        have = {s.path for s in self.project.sources}
        new = [f for f in files if f not in have]
        if not new:
            return
        self.push_undo()
        mode = self.settings.d.get("default_mode", "beauty")
        for f in new:
            s = Source(f, mode, self.settings.defaults("beauty"), self.settings.defaults("blur"))
            self.project.sources.append(s)
            self._probe(f)
        if self.cur < 0:
            self.cur = 0
        self.dirty = True
        self.changed.emit()
        self.current_changed.emit()

    def remove(self, idxs):
        idxs = sorted(set(i for i in idxs if 0 <= i < len(self.project.sources)), reverse=True)
        if not idxs:
            return
        self.push_undo()
        for i in idxs:
            self.project.sources.pop(i)
        self.cur = min(self.cur, len(self.project.sources) - 1)
        self.dirty = True
        self.changed.emit()
        self.current_changed.emit()

    def select(self, i):
        if i != self.cur:
            self.cur = i
            self.current_changed.emit()

    # ------------------------------------------------------------ 값
    def set_param(self, key, val, *, undo=False):
        s = self.source()
        if s is None:
            return
        if undo:
            self.push_undo()
        s.params()[key] = val
        s.custom = True
        self.dirty = True
        self.params_changed.emit()
        self.changed.emit()

    def set_mode(self, mode):
        s = self.source()
        if s is None or s.mode == mode:
            return
        self.push_undo()
        s.mode = mode
        self.dirty = True
        self.ensure_analysis(s)
        self.changed.emit()
        self.params_changed.emit()

    def set_params(self, mode, params, *, custom=True):
        s = self.source()
        if s is None:
            return
        self.push_undo()
        s.mode = mode
        (s.beauty if mode == "beauty" else s.blur).update(copy.deepcopy(params))
        s.custom = custom
        self.dirty = True
        self.ensure_analysis(s)
        self.changed.emit()
        self.params_changed.emit()

    # ------------------------------------------------------------ 실행취소
    def push_undo(self):
        self.undo_stack.append((self.project.snapshot(), self.cur))
        if len(self.undo_stack) > UNDO_MAX:
            self.undo_stack.pop(0)

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        snap, cur = self.undo_stack.pop()
        self.project.restore(snap)
        self.cur = min(cur, len(self.project.sources) - 1)
        self.dirty = True
        self.changed.emit()
        self.current_changed.emit()
        self.params_changed.emit()
        return True

    # ------------------------------------------------------------ 정보·분석
    def _probe(self, path):
        if path in self.infos:
            return

        def fn():
            return ff.probe(path)
        j = jobs.Job(fn)
        j.signals.done.connect(lambda info, p=path: self._probed(p, info))
        j.signals.error.connect(lambda e, p=path: self._err(p, e))
        jobs.submit(jobs.PROBE_POOL, j)

    def _probed(self, path, info):
        self.infos[path] = info
        self.errors.pop(path, None)
        for s in self.project.sources:
            if s.path == path:
                self.ensure_analysis(s)
        self.changed.emit()
        if self.source() and self.source().path == path:
            self.current_changed.emit()

    def _err(self, path, e):
        self.errors[path] = str(e)
        self.changed.emit()

    def retry(self, path):
        self.errors.pop(path, None)
        self.infos.pop(path, None)
        for k in [k for k in self.an if k[0] == path]:
            self.an.pop(k)
        self._probe(path)

    def ensure_analysis(self, s: Source):
        info = self.infos.get(s.path)
        if info is None:
            return
        key = (s.path, s.mode)
        a = self.an.get(key)
        if a and a["state"] in ("run", "done"):
            return
        z = analyze.load(info, s.mode)
        if z is not None:
            self.an[key] = {"z": z, "p": 1.0, "state": "done", "err": ""}
            self.analysis_changed.emit(s.path)
            return
        rec = {"z": None, "p": 0.0, "state": "run", "err": ""}
        self.an[key] = rec
        hw = bool(self.settings.render.get("hw"))
        mode = s.mode

        def fn(prog, cancel):
            return analyze.analyze(info, mode, prog, cancel, hw)
        j = jobs.Job(fn, with_progress=True)
        rec["job"] = j
        j.signals.progress.connect(lambda v, k=key: self._an_prog(k, v))
        j.signals.done.connect(lambda z, k=key: self._an_done(k, z))
        j.signals.error.connect(lambda e, k=key: self._an_err(k, e))
        jobs.submit(jobs.ANALYZE_POOL, j, 5 if s is self.source() else 0)

    def _an_prog(self, key, v):
        if key in self.an:
            self.an[key]["p"] = v
            self.analysis_changed.emit(key[0])

    def _an_done(self, key, z):
        if key in self.an:
            self.an[key].update(z=z, p=1.0, state="done")
            self._tracks = {k: v for k, v in self._tracks.items() if k[:2] != key}
            self.analysis_changed.emit(key[0])
            self.params_changed.emit()

    def _an_err(self, key, e):
        if key in self.an:
            self.an[key].update(state="err", err=e)
            self.errors[key[0]] = f"분석 실패: {e}"
            self.analysis_changed.emit(key[0])
            self.changed.emit()

    def analysis(self, s: Source):
        return self.an.get((s.path, s.mode))

    def track(self, s: Source):
        """현재 값 기준 트랙(캐시). 분석 전이면 None."""
        a = self.analysis(s)
        if not a or a["state"] != "done":
            return None
        p = s.params()
        rs = self.settings.render
        keys = ("stabilize",) if s.mode == "beauty" else ("stabilize", "sensitivity", "min_size", "hold")
        sig = json.dumps([p.get(x) for x in keys]) + str(rs.get("two_pass", True))
        k = (s.path, s.mode, sig)
        t = self._tracks.get(k)
        if t is None:
            from .render import make_track
            t = make_track(s, a["z"], rs)
            if len(self._tracks) > 8:
                self._tracks.clear()
            self._tracks[k] = t
        return t

    def cancel_all(self):
        for a in self.an.values():
            j = a.get("job")
            if j is not None:
                j.cancel.set()
