"""공유 상태: 프로젝트, 현재 소스, 분석 결과·진행, 실행취소."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QObject, Signal

from . import analyze, decoder, edits, ff, jobs
from .project import Project, Settings, Source

UNDO_MAX = 80


class AppState(QObject):
    changed = Signal()                 # 목록·값 변경(화면 갱신)
    current_changed = Signal()         # 선택 소스 바뀜
    params_changed = Signal()          # 현재 소스 값 바뀜(미리보기 다시)
    analysis_changed = Signal(str)     # 분석 진행·완료 (경로)
    busy_changed = Signal(str)         # 샘플·렌더 진행 (경로) — 소스 목록 표시만
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
        self.busy: dict = {}           # path -> (kind, p, 글자|None) 샘플·렌더 진행(소스 목록 표시용)
        self._tracks = {}
        self._res = {}
        self.sel = None                # 선택한 타원 key
        self.view_scale = float(self.settings.d.get("view_scale", 0.5))   # 보기 화질 1 / 0.5 / 0.25

    def set_view_scale(self, v):
        self.view_scale = v
        self.settings.d["view_scale"] = v
        self.settings.save()
        self.params_changed.emit()

    def apply_auto(self, assigns, keep_keys=True):
        """AUTO 적용: [(idx, mode, params)] 를 한 번의 실행취소로. keep_keys=False 면 그 모드의 타원 수정 지움."""
        if not assigns:
            return
        self.push_undo()
        for idx, mode, params in assigns:
            if not (0 <= idx < len(self.project.sources)):
                continue
            s = self.project.sources[idx]
            s.mode = mode
            (s.beauty if mode == "beauty" else s.blur).update(copy.deepcopy(params))
            s.custom = True
            if not keep_keys:
                s.edits.pop(mode, None)
        self._tracks.clear()
        self._res.clear()
        self.dirty = True
        self.changed.emit()
        self.current_changed.emit()
        self.params_changed.emit()

    def analyze_now(self, s: Source = None):
        """명시적으로 분석 시작(없으면 현재 소스). 이미 하는 중/끝났으면 그대로."""
        s = s or self.source()
        if s is not None:
            self.ensure_analysis(s)
        return s is not None and self.analysis(s) is not None

    # ------------------------------------------------------------ 소스
    def source(self) -> Source | None:
        if 0 <= self.cur < len(self.project.sources):
            return self.project.sources[self.cur]
        return None

    def info(self, path) -> ff.Info | None:
        return self.infos.get(path)

    def set_project(self, p: Project):
        decoder.keep_only([s.path for s in p.sources])
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
        decoder.keep_only([s.path for s in self.project.sources])
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
        before = ([s.path for s in self.project.sources], self.cur)
        self.project.restore(snap)
        self.cur = min(cur, len(self.project.sources) - 1)
        self.dirty = True
        self.changed.emit()
        if before != ([s.path for s in self.project.sources], self.cur):
            self.current_changed.emit()       # 소스 목록·선택이 바뀐 경우만 화면 다시 읽기
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
        # 불러올 때 자동 분석하지 않음. 분석은 [분석] 버튼·재생 전·렌더 때만.
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
            self._tracks.pop(key, None)
            self.analysis_changed.emit(key[0])
            self.params_changed.emit()

    def _an_err(self, key, e):
        if e == "취소됨":                          # 취소는 오류 아님 → 다음에 다시 분석
            if key in self.an and self.an[key].get("state") == "run":
                self.an.pop(key, None)
            self.analysis_changed.emit(key[0])
            return
        if key in self.an:
            self.an[key].update(state="err", err=e)
            self.errors[key[0]] = f"분석 실패: {e}"
            self.analysis_changed.emit(key[0])
            self.changed.emit()

    def analysis(self, s: Source):
        return self.an.get((s.path, s.mode))

    def _track_sig(self, s: Source):
        p = s.params()
        rs = self.settings.render
        keys = ("stabilize",) if s.mode == "beauty" else ("stabilize", "sensitivity", "min_size", "hold")
        return json.dumps([p.get(x) for x in keys]) + str(rs.get("two_pass", True))

    def track(self, s: Source):
        """현재 값 기준 트랙(캐시). 분석 전이면 None.
        캐시 = 소스·모드마다 최신 1개(예전: 전체 8개 넘으면 통째로 비움 → AUTO 로 9개 넘게 미리 만든 트랙이 지워졌음)."""
        a = self.analysis(s)
        if not a or a["state"] != "done":
            return None
        sig = self._track_sig(s)
        k = (s.path, s.mode)
        hit = self._tracks.get(k)
        if hit is not None and hit[0] == sig:
            return hit[1]
        from .render import make_track
        t = make_track(s, a["z"], self.settings.render)
        self._tracks[k] = (sig, t)
        return t

    def has_track(self, s: Source) -> bool:
        hit = self._tracks.get((s.path, s.mode))
        return hit is not None and hit[0] == self._track_sig(s)

    def prepare_track(self, s: Source, on_done=None, on_error=None):
        """분석이 끝난 소스의 트랙을 백그라운드에서 미리 계산해 캐시에 넣는다(AUTO [보정+편집])."""
        a = self.analysis(s)
        if not a or a["state"] != "done":
            return None
        if self.has_track(s):
            if on_done:
                on_done()
            return None
        sig = self._track_sig(s)
        k = (s.path, s.mode)
        src = copy.deepcopy(s)
        z, rs = a["z"], dict(self.settings.render)

        def fn():
            from .render import make_track
            return make_track(src, z, rs)

        def done(t):
            self._tracks[k] = (sig, t)
            if on_done:
                on_done()
        j = jobs.Job(fn)
        j.signals.done.connect(done)
        if on_error:
            j.signals.error.connect(on_error)
        jobs.submit(jobs.ANALYZE_POOL, j, 5)
        return j

    # ------------------------------------------------------------ 진행 표시(소스 목록)
    def set_busy(self, path, kind, p, label=None):
        """샘플·렌더 진행을 소스 목록 줄에 표시(kind = ui_widgets.PROG 키)."""
        self.busy[path] = (kind, p, label)
        self.busy_changed.emit(path)

    def clear_busy(self, path, kind=None):
        b = self.busy.get(path)
        if b and (kind is None or b[0] == kind):
            self.busy.pop(path, None)
            self.busy_changed.emit(path)

    # ------------------------------------------------------------ 타원 수정
    def resolver(self, s):
        tr = self.track(s)
        p = s.params()
        sig = json.dumps([edits.get(s), p.get("margin"), p.get("size"), p.get("neck")], sort_keys=True)
        k = (s.path, s.mode, id(tr), sig)
        r = self._res.get(k)
        if r is None:
            if len(self._res) > 8:
                self._res.clear()
            r = edits.Resolver(s, tr)
            self._res[k] = r
        return r

    def _item(self, s, key, frame):
        for it in self.resolver(s).items(frame):
            if it.key == key:
                return it
        return None

    def _edited(self):
        s = self.source()
        s.custom = True
        self.dirty = True
        self.params_changed.emit()
        self.changed.emit()

    def _group_for(self, key, frame, create=True):
        s = self.source()
        if s is None or key is None:
            return None, None
        it = self._item(s, key, frame)
        if it is None and key[0] == "g":
            g = next((g for g in edits.get(s)["groups"] if g["gid"] == key[1]), None)
            return s, g
        if it is None:
            return s, None
        if not create and it.gid is None:
            return s, None
        g = edits.ensure_group(s, it, frame)
        return s, g

    def edit_ellipse(self, key, frame, ell):
        """모니터에서 옮기거나 크기를 바꾼 타원(타원별 크기 % 를 뺀 값으로 저장) → 현재 프레임 키."""
        self.push_undo()
        s, g = self._group_for(key, frame)
        if g is None:
            self.undo_stack.pop()
            return
        sz = g.get("ov", {}).get("size", 100) / 100
        edits.set_key(g, frame, (ell[0], ell[1], ell[2] / sz, ell[3] / sz))
        self.sel = ("g", g["gid"])
        self._edited()

    def add_ellipse(self, frame, x, y, size):
        s = self.source()
        if s is None:
            return
        self.push_undo()
        w = size
        g = edits.add_manual(s, frame, (x, y, w, w * 1.25))
        self.sel = ("g", g["gid"])
        self._edited()

    def add_ellipse_box(self, frame, cx, cy, w, h):
        """드래그로 그린 새 타원(크기 지정). 연속 그리기 모드에서 계속 호출됨."""
        s = self.source()
        if s is None:
            return
        self.push_undo()
        g = edits.add_manual(s, frame, (cx, cy, max(12.0, w), max(12.0, h)))
        self.sel = ("g", g["gid"])
        self._edited()

    def delete_ellipse(self, key, frame):
        """타원 삭제: 추가(수동) 타원은 그룹째 제거, 자동 타원은 전 구간 숨김(off_all)."""
        s, g = self._group_for(key, frame)
        if g is None:
            return
        self.push_undo()
        if g.get("manual"):
            edits.remove_group(s, g["gid"])
        else:
            g["off_all"] = True
        self.sel = None
        self._edited()

    def delete_key(self, frame):
        s, g = self._group_for(self.sel, frame, create=False)
        if g is None or str(int(frame)) not in g.get("keys", {}):
            return False
        self.push_undo()
        edits.del_key(g, frame)
        if g.get("manual") and not g["keys"]:
            edits.remove_group(s, g["gid"])
            self.sel = None
        self._edited()
        return True

    def off(self, how, frame, n):
        self.push_undo()
        s, g = self._group_for(self.sel, frame)
        if g is None:
            self.undo_stack.pop()
            return
        if how == "from":
            edits.off_range(g, frame, n - 1)
        elif how == "to":
            edits.off_range(g, 0, frame)
        elif how == "all":
            g["off_all"] = True
        elif how == "on":
            edits.on_all(g)
        elif how == "remove" and g.get("manual"):
            edits.remove_group(s, g["gid"])
            self.sel = None
        self.sel = ("g", g["gid"]) if self.sel is not None and how != "remove" else self.sel
        self._edited()

    def merge(self, key_a, anchor_a, key_b, frame_b):
        s = self.source()
        it = self._item(s, key_b, frame_b) if s else None
        if s is None or it is None or anchor_a is None:
            return False
        self.push_undo()
        edits.add_merge(s, anchor_a, (frame_b, it.ell[0], it.ell[1]))
        self._edited()
        return True

    def anchor_of(self, key, frame):
        s = self.source()
        it = self._item(s, key, frame) if s else None
        return (int(frame), float(it.ell[0]), float(it.ell[1])) if it else None

    # ------------------------------------------------------------ 타원별 타임라인(레인)
    def _present_ell(self, s, key, near, n):
        """key 타원이 near 근처에서 실제로 존재하는 ell 과 그 프레임(없으면 None)."""
        res = self.resolver(s)
        order = [near] + [f for d in range(1, n) for f in (near - d, near + d) if 0 <= f < n]
        for f in order:
            it = next((it for it in res.items(f) if it.key == key), None)
            if it is not None:
                return tuple(it.ell[:4]), f
        return None, None

    def ellipse_spans(self, s):
        """현재 소스의 타원별 {key,label,intervals,keys,n}. 리졸버 바뀌면 다시 계산(캐시)."""
        tr = self.track(s)
        if tr is None:
            return []
        n = tr.n
        res = self.resolver(s)
        sig = (s.path, s.mode, id(res), n)
        if getattr(self, "_spans_cache", (None,))[0] == sig:
            return self._spans_cache[1]
        step = max(1, n // 800)
        seen, order = {}, []
        for i in range(0, n, step):
            for it in res.items(i):
                k = it.key
                if k not in seen:
                    lbl = str(it.tid) if it.tid is not None else ("+" if it.manual else "·")
                    seen[k] = {"label": lbl, "frames": []}
                    order.append(k)
                seen[k]["frames"].append(i)
        out = []
        groups = {g["gid"]: g for g in edits.get(s)["groups"]}
        for k in order:
            fr = seen[k]["frames"]
            intervals, a, prev = [], fr[0], fr[0]
            for f in fr[1:]:
                if f - prev > step * 2:
                    intervals.append((a, prev))
                    a = f
                prev = f
            intervals.append((a, prev))
            keys = sorted(int(x) for x in groups[k[1]].get("keys", {})) if k[0] == "g" and k[1] in groups else []
            out.append({"key": k, "label": seen[k]["label"], "intervals": intervals, "keys": keys, "n": n})
        self._spans_cache = (sig, out)
        return out

    def set_span(self, key, f_in, f_out):
        """타원을 [f_in, f_out] 구간에만 존재하게(양 끝 위치 홀드 + 바깥은 끄기). 처음·끝까지 연장 포함."""
        s = self.source()
        tr = self.track(s)
        if s is None or tr is None or key is None:
            return
        n = tr.n
        f_in = max(0, min(int(f_in), n - 1))
        f_out = max(f_in, min(int(f_out), n - 1))
        e_in, af = self._present_ell(s, key, f_in, n)
        if e_in is None:
            return
        e_out, _ = self._present_ell(s, key, f_out, n)
        self.push_undo()
        _, g = self._group_for(key, af)        # 존재하는 프레임에서 그룹 확보
        if g is None:
            self.undo_stack.pop()
            return
        edits.set_key(g, f_in, e_in)
        edits.set_key(g, f_out, e_out or e_in)
        g["off_all"] = False
        g["off"] = []
        if f_in > 0:
            edits.off_range(g, 0, f_in - 1)
        if f_out < n - 1:
            edits.off_range(g, f_out + 1, n - 1)
        self.sel = ("g", g["gid"])
        self._edited()

    def set_ov(self, key, frame, k, v, *, undo=True):
        if undo:
            self.push_undo()
        s, g = self._group_for(key, frame)
        if g is None:
            if undo:
                self.undo_stack.pop()
            return
        if v is None:
            g.setdefault("ov", {}).pop(k, None)
        else:
            g.setdefault("ov", {})[k] = v
        self.sel = ("g", g["gid"])
        self._edited()

    def ov_of(self, key, frame):
        s = self.source()
        it = self._item(s, key, frame) if s and key else None
        return dict(it.ov) if it else None

    def cancel_all(self):
        for a in self.an.values():
            j = a.get("job")
            if j is not None:
                j.cancel.set()
