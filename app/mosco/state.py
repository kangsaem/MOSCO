"""공유 상태: 프로젝트, 현재 소스, 분석 결과·진행, 실행취소."""
from __future__ import annotations

import copy
import json
import numpy as np

from PySide6.QtCore import QObject, Signal

from . import analyze, decoder, edits, ff, jobs
from .project import Project, Settings, Source

UNDO_MAX = 80


def _z_level(z, mode):
    if mode != "blur":
        return 0
    try:
        return int(z["level"]) if "level" in z else analyze.SMALL_MIN
    except Exception:  # noqa: BLE001
        return analyze.SMALL_MIN


def _lane_geo(it, p, fx):
    """레인 오토 키 계산용 기하 벡터 (cx, cy, w, h, 회전길이px). 간판(quad)은 외접 타원으로."""
    import math
    if fx._is_quad(it):
        from .sign import quad_ell
        cx, cy, w, h, ang = quad_ell(fx.item_quad(it, p))
    else:
        cx, cy, w, h, ang = fx._item_ell(it, p)
    return (cx, cy, w, h, math.radians(ang) * 0.5 * max(w, h))   # 회전 1° ≈ 호 길이(px)로 환산


def _auto_keys(frames, geo, intervals):
    """오토 트랙의 키 위치(근사): 구간마다 기하를 선형보간 오차 ≤ TOL 로 줄인 프레임.
    AE 내보내기(aefx.reduce_keys)와 같은 기준이라 실제 키가 찍히는 곳과 거의 일치(샘플 간격 한도)."""
    from .aefx import TOL, reduce_keys
    if not frames:
        return []
    fa = np.asarray(frames)
    out = []
    for a, b in intervals:
        m = (fa >= a) & (fa <= b)
        idx = np.nonzero(m)[0]
        if idx.size == 0:
            continue
        sub = [geo[j] for j in idx]
        if len(sub) <= 2:
            out += [int(frames[j]) for j in idx]
            continue
        for ki in reduce_keys(sub, TOL):
            out.append(int(frames[idx[ki]]))
    return sorted(set(out))


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
        self.redo_stack = []           # 다시 실행(Ctrl+Shift+Z). 새 편집을 하면 비움
        self.multi_sel = lambda: []    # 소스 목록의 선택 행(MainWindow 가 연결)
        self.infos: dict[str, ff.Info] = {}
        self.errors: dict[str, str] = {}
        self.an: dict = {}             # (path, mode) -> {"z", "p", "state", "err", "job"}
        self.busy: dict = {}           # path -> (kind, p, 글자|None) 샘플·렌더 진행(소스 목록 표시용)
        self._tracks = {}
        self._res = {}
        self.sel = None                # 선택한 타원 key
        self.view_scale = float(self.settings.d.get("view_scale", 0.5))   # 보기 화질 1 / 0.5 / 0.25
        # 얼굴블러 하단 제외(작은 얼굴 검색 생략으로 분석 가속). 기본 1/4 제외.
        self.bottom_skip = float(self.settings.d.get("bottom_skip", 0.25))
        self.settings.render["bottom_skip"] = self.bottom_skip

    def set_bottom_skip(self, frac):
        """얼굴블러 분석에서 하단 frac(0~0.9) 비율의 '작은 얼굴' 검색을 생략. 0 = 끔(전체)."""
        self.bottom_skip = max(0.0, min(0.9, float(frac)))
        self.settings.d["bottom_skip"] = self.bottom_skip
        self.settings.render["bottom_skip"] = self.bottom_skip
        self.settings.save()

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
        for a in assigns:
            if len(a) == 4:                         # (idx, mode, both, {kind: params}) — 동시 블러
                idx, mode, both, pbk = a
            else:                                   # 옛 형식 (idx, mode, params)
                idx, mode, params = a
                both, pbk = False, {mode: params}
            if not (0 <= idx < len(self.project.sources)):
                continue
            s = self.project.sources[idx]
            s.mode = mode
            s.both = bool(both)
            for k, params in pbk.items():
                s.params_for(k).update(copy.deepcopy(params))
                if not keep_keys:
                    s.edits.pop(k, None)
            s.custom = True
        self._tracks.clear()
        self._res.clear()
        self.dirty = True
        self.changed.emit()
        self.current_changed.emit()
        self.params_changed.emit()

    def analyze_now(self, s: Source = None):
        """명시적으로 분석 시작(없으면 현재 소스). 동시 블러면 종류별로(얼굴·간판) 다. 이미 하는 중/끝났으면 그대로."""
        s = s or self.source()
        if s is None:
            return False
        for m in (s.blur_kinds() or [s.mode]):
            self.ensure_analysis(s, m)
        return self.analysis(s) is not None

    # ------------------------------------------------------------ 소스
    def source(self) -> Source | None:
        if 0 <= self.cur < len(self.project.sources):
            return self.project.sources[self.cur]
        return None

    def info(self, path) -> ff.Info | None:
        return self.infos.get(path)

    def set_project(self, p: Project):
        decoder.keep_only([s.path for s in p.sources])
        keep = {s.path for s in p.sources}
        self.project = p
        self.cur = 0 if p.sources else -1
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.sel = None
        self._tracks.clear()                       # v0.7.79 이전 프로젝트의 트랙·해석·스팬 캐시 비움
        self._res.clear()
        self._spans_cache = (None,)
        for k in [k for k in self.an if k[0] not in keep]:   # 안 쓰는 분석 결과 정리
            self.an.pop(k, None)
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
            s = Source(f, mode, self.settings.defaults("beauty"), self.settings.defaults("blur"),
                       sign=self.settings.defaults("sign"))
            self.project.sources.append(s)
            self._probe(f)
        if self.cur < 0:
            self.cur = 0
        self._apply_disk_mode()
        self.dirty = True
        self.changed.emit()

    def _apply_disk_mode(self):
        """원본이 HDD 면 읽는 풀 동시성을 낮춤(같은 디스크 동시 읽기로 느려지는 것 방지)."""
        try:
            from . import disk
            jobs.set_disk_mode(disk.any_hdd([s.path for s in self.project.sources]))
        except Exception:  # noqa: BLE001
            pass
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
            s = self.source()
            if s is not None:
                decoder.focus(s.path)        # 다른 소스의 프레임 캐시는 조금만 남김(램 보호)
            self.current_changed.emit()

    # ------------------------------------------------------------ 값
    def set_param(self, key, val, *, undo=False):
        s = self.source()
        if s is None:
            return
        if undo:
            self.push_undo()
        for t in self.param_targets(key):
            t.params_for(s.mode)[key] = copy.deepcopy(val)
            t.custom = True
        self.dirty = True
        self.params_changed.emit()
        self.changed.emit()

    # 여러 소스를 선택해도 한꺼번에 넣지 않는 값: 클립마다 다른 것(피부색)·타원 묶음(여러 개 선택 시 비활성)
    SINGLE_KEYS = ("skin_ref", "skin_extra", "size", "feather", "neck", "stabilize", "margin", "profile")

    def multi_selected(self):
        """소스 목록에서 여러 개(2개 이상)를 선택했으면 그 소스들, 아니면 []."""
        try:
            idx = [i for i in self.multi_sel() if 0 <= i < len(self.project.sources)]
        except Exception:  # noqa: BLE001
            idx = []
        return [self.project.sources[i] for i in idx] if len(idx) > 1 else []

    def param_targets(self, key):
        """이 값을 바꿀 소스들(v0.7.64): 현재 소스 + (여러 개 선택 중이면) 같은 작업 종류의 선택 소스.
        바꾼 값 하나만 들어가고 나머지 값은 클립마다 그대로."""
        s = self.source()
        if s is None:
            return []
        out = [s]
        if key not in self.SINGLE_KEYS:
            out += [t for t in self.multi_selected() if t is not s and t.mode == s.mode]
        return out

    def set_mode(self, mode):
        s = self.source()
        if s is None or s.mode == mode:
            return
        self.push_undo()
        s.mode = mode
        self.dirty = True
        self.changed.emit()
        self.params_changed.emit()

    def set_blur(self, face, sign, active=None):
        """동시 블러: 어떤 종류를 켤지(얼굴·간판). 둘 다면 both=True, 활성 편집 = active. 최소 하나는 켬."""
        s = self.source()
        if s is None:
            return
        if not face and not sign:
            face = True
        self.push_undo()
        if face and sign:
            s.both = True
            s.mode = active if active in ("blur", "sign") else (s.mode if s.mode in ("blur", "sign") else "blur")
        else:
            s.both = False
            s.mode = "blur" if face else "sign"
        self.dirty = True
        self.changed.emit()
        self.params_changed.emit()

    def set_active(self, mode):
        """동시 블러: 편집 활성 종류만 바꿈(되돌리기·dirty 없음, 보기는 둘 다 유지)."""
        s = self.source()
        if s is None or s.mode == mode or mode not in ("blur", "sign"):
            return
        s.mode = mode
        self.changed.emit()
        self.params_changed.emit()

    def set_params(self, mode, params, *, custom=True):
        s = self.source()
        if s is None:
            return
        self.push_undo()
        s.mode = mode
        s.params_for(mode).update(copy.deepcopy(params))
        s.custom = custom
        self.dirty = True
        self.changed.emit()
        self.params_changed.emit()

    # ------------------------------------------------------------ 실행취소
    def push_undo(self):
        self.undo_stack.append((self.project.snapshot(), self.cur))
        if len(self.undo_stack) > UNDO_MAX:
            self.undo_stack.pop(0)
        self.redo_stack.clear()                 # 새 편집 → 다시 실행 기록은 버림

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append((self.project.snapshot(), self.cur))
        return self._apply_snap(*self.undo_stack.pop())

    def redo(self) -> bool:
        """다시 실행(v0.7.56): 실행취소로 되돌린 것을 다시 적용."""
        if not self.redo_stack:
            return False
        self.undo_stack.append((self.project.snapshot(), self.cur))
        return self._apply_snap(*self.redo_stack.pop())

    def relink(self, mapping: dict) -> int:
        """원본 다시 연결(v0.7.56): {옛 경로: 새 경로}. 편집값·설정은 그대로, 경로만 바꿈. 반환 = 바뀐 소스 수."""
        todo = [s for s in self.project.sources if s.path in mapping and mapping[s.path] != s.path]
        if not todo:
            return 0
        self.push_undo()
        for s in todo:
            old = s.path
            s.path = mapping[old]
            self.infos.pop(old, None)
            self.errors.pop(old, None)
            for k in [k for k in self.an if k[0] == old]:
                self.an.pop(k)
            self._probe(s.path)
        decoder.keep_only([s.path for s in self.project.sources])
        self.dirty = True
        self.changed.emit()
        self.current_changed.emit()
        return len(todo)

    def _apply_snap(self, snap, cur) -> bool:
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
        # 영상을 넣을 때는 자동 분석하지 않음(분석은 [분석] 버튼·재생 전·렌더 때). 단 프로젝트를 열었을 때는 v0.8.1 자동.
        if path in getattr(self, "_autoload", set()):
            self._autoload.discard(path)
            self._load_analysis(path)
        self.changed.emit()
        if self.source() and self.source().path == path:
            self.current_changed.emit()

    def _err(self, path, e):
        self.errors[path] = str(e)
        self.changed.emit()

    def open_loaded(self):
        """v0.8.1 프로젝트를 열었을 때·자동 저장 복구 때: 분석 저장 파일이 있으면 바로 읽어 레이어를 보여 주고,
        없으면 분석을 시작(현재 소스 먼저). 예전엔 [분석]을 누르기 전까지 레이어가 하나도 안 보였음."""
        self._autoload = set()
        for s in self.project.sources:
            if s.path in self.infos:
                self._load_analysis(s.path)
            else:
                self._autoload.add(s.path)            # 영상 정보(probe)가 오면 _probed 에서

    def _load_analysis(self, path):
        cur = self.source()
        srcs = [s for s in self.project.sources if s.path == path]
        srcs.sort(key=lambda s: s is not cur, reverse=False)
        for s in srcs:
            for m in (s.blur_kinds() or [s.mode]):
                self.ensure_analysis(s, m)           # 저장 파일 있으면 즉시 읽음, 없으면 분석 시작

    def retry(self, path):
        self.errors.pop(path, None)
        self.infos.pop(path, None)
        for k in [k for k in self.an if k[0] == path]:
            self.an.pop(k)
        self._probe(path)

    def reanalyze(self, s: Source):
        """이미 분석된 클립을 처음부터 다시 분석(디스크 캐시도 무시). v0.7.83 [분석됨] 재클릭·확인 후."""
        for m in (s.blur_kinds() or [s.mode]):
            key = (s.path, m)
            a = self.an.get(key)
            if a and a.get("job") is not None:
                a["job"].cancel.set()
            self.an.pop(key, None)
            self._tracks.pop(key, None)
        self._res.clear()
        self._spans_cache = (None,)
        for m in (s.blur_kinds() or [s.mode]):
            self.ensure_analysis(s, m, force=True)
        return self.analysis(s) is not None

    def ensure_analysis(self, s: Source, mode=None, force=False):
        mode = mode or s.mode
        info = self.infos.get(s.path)
        if info is None:
            return
        key = (s.path, mode)
        level = analyze.level_for(mode, s.params_for(mode))
        bskip = self.bottom_skip if mode == "blur" else 0.0
        a = self.an.get(key)
        if a and not force and a["state"] in ("run", "done"):
            # 하한이 충분하고 하단제외 설정도 같으면 그대로 사용. 다르면 취소 후 다시 분석.
            if a.get("level", 0) <= level and abs(a.get("bskip", 0.0) - bskip) < 1e-6:
                return
            if a.get("job") is not None:
                a["job"].cancel.set()
            self.an.pop(key, None)
        z = None if force else analyze.load(info, mode, level, bskip)
        if z is not None:
            self.an[key] = {"z": z, "p": 1.0, "state": "done", "err": "", "level": _z_level(z, mode), "bskip": bskip}
            self.analysis_changed.emit(s.path)
            return
        rec = {"z": None, "p": 0.0, "state": "run", "err": "", "level": level, "bskip": bskip}
        self.an[key] = rec
        hw = bool(self.settings.render.get("hw"))

        def fn(prog, cancel):
            return analyze.analyze(info, mode, prog, cancel, hw, level=level, bottom_skip=bskip)
        j = jobs.Job(fn, with_progress=True)
        rec["job"] = j
        # 같은 키에 새 분석이 들어왔을 수 있음(하한 변경) → 자기 기록일 때만 반영
        j.signals.progress.connect(lambda v, k=key, r=rec: self.an.get(k) is r and self._an_prog(k, v))
        j.signals.done.connect(lambda z, k=key, r=rec: self.an.get(k) is r and self._an_done(k, z))
        j.signals.error.connect(lambda e, k=key, r=rec: self.an.get(k) is r and self._an_err(k, e))
        jobs.submit(jobs.ANALYZE_POOL, j, 5 if s is self.source() else 0)

    def _an_prog(self, key, v):
        if key in self.an:
            self.an[key]["p"] = v
            self.analysis_changed.emit(key[0])

    def _an_done(self, key, z):
        if key in self.an:
            getattr(self, "_an_retried", set()).discard(key)
            self._log_analyze(key, z, "done")
            self.an[key].update(z=z, p=1.0, state="done", level=_z_level(z, key[1]))
            self._tracks.pop(key, None)
            self.analysis_changed.emit(key[0])
            self.params_changed.emit()

    def _log_analyze(self, key, z, how, err=""):
        """단계별 시간 로그(logs/analyze_log.csv) — HDD 효과를 사용자 PC 로그로 숫자 확인용."""
        try:
            import csv
            import time as _t
            from . import disk, jobs, models, paths
            path, mode = key
            rec = self.an.get(key) or {}
            j = rec.get("job")
            now = _t.perf_counter()
            wait = round((j.start_t - j.submit_t), 2) if (j and j.submit_t and j.start_t) else ""
            run = round((now - j.start_t), 2) if (j and j.start_t) else ""
            frames = int(z["n"]) if (z is not None and "n" in z) else ""
            fp = paths.log_dir() / "analyze_log.csv"
            head = not fp.exists()
            with open(fp, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                if head:
                    w.writerow(["시각", "소스", "모드", "디스크", "검출장치", "결과", "대기초", "분석초", "프레임",
                                "분석fps", "동시(분석풀)"])
                fps = round(frames / run, 2) if (frames and run) else ""
                w.writerow([_t.strftime("%Y-%m-%d %H:%M:%S"), __import__("os").path.basename(path), mode,
                            "HDD" if disk.is_hdd(path) else "SSD", models.active_device(),
                            how if not err else f"{how}:{err}", wait, run, frames, fps,
                            jobs.ANALYZE_POOL.maxThreadCount()])
            if err:                                  # v0.7.82 실패는 전체 추적까지 errors.log 에
                tr = (j.err_trace if j is not None else "") or ""
                with open(paths.log_dir() / "errors.log", "a", encoding="utf-8") as f:
                    f.write(f"\n===== {_t.strftime('%Y-%m-%d %H:%M:%S')} · {__import__('os').path.basename(path)} · "
                            f"{mode} · {err} =====\n{tr}\n")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def log_error(what, name, msg, trace=""):
        """errors.log 에 한 건(v0.7.98: 트랙 계산 실패·AUTO 오류 요약도 남김)."""
        try:
            import os as _os
            import time as _t

            from . import paths
            with open(paths.log_dir() / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n===== {_t.strftime('%Y-%m-%d %H:%M:%S')} · {_os.path.basename(name)} · {what} · {msg} =====\n"
                        f"{trace}\n")
        except Exception:  # noqa: BLE001
            pass

    def _an_err(self, key, e):
        if e == "취소됨":                          # 취소는 오류 아님 → 다음에 다시 분석
            if key in self.an and self.an[key].get("state") == "run":
                self.an.pop(key, None)
            self.analysis_changed.emit(key[0])
            return
        self._log_analyze(key, None, "err", err=e)     # v0.7.82 실패도 로그(CSV + errors.log)
        retried = getattr(self, "_an_retried", set())
        self._an_retried = retried
        rec = self.an.get(key)
        if key not in retried and rec is not None:     # v0.7.82 실패한 파일만 한 번 더(뒤 순서로 순차)
            retried.add(key)
            src = next((s for s in self.project.sources if s.path == key[0]), None)
            self.an.pop(key, None)
            if src is not None:
                self.ensure_analysis(src, key[1])
                self.analysis_changed.emit(key[0])
                return
            self.an[key] = rec                         # 소스를 못 찾으면 원복해 오류로
        if key in self.an:
            self.an[key].update(state="err", err=e)
            self.errors[key[0]] = f"분석 실패: {e}"
            self.analysis_changed.emit(key[0])
            self.changed.emit()

    def analysis(self, s: Source, mode=None):
        """지금 값에 쓸 수 있는 분석(블러: 분석 하한이 [최소 크기] 기준 이하). 아니면 None → 다시 분석 대상."""
        mode = mode or s.mode
        a = self.an.get((s.path, mode))
        if a is not None and a.get("level", 0) > analyze.level_for(mode, s.params_for(mode)):
            return None
        return a

    def _track_sig(self, s: Source, mode=None):
        mode = mode or s.mode
        p = s.params_for(mode)
        rs = self.settings.render
        keys = ("stabilize",) if mode == "beauty" else ("stabilize", "sensitivity", "min_size", "hold", "link", "profile") \
            if mode == "blur" else ("stabilize", "sensitivity", "min_size", "hold", "link", "margin", "max_count")
        return json.dumps([p.get(x) for x in keys]) + str(rs.get("two_pass", True))

    def track(self, s: Source, mode=None):
        """현재 값 기준 트랙(캐시). 분석 전이면 None.
        캐시 = 소스·모드마다 최신 1개(예전: 전체 8개 넘으면 통째로 비움 → AUTO 로 9개 넘게 미리 만든 트랙이 지워졌음)."""
        mode = mode or s.mode
        a = self.analysis(s, mode)
        if not a or a["state"] != "done":
            return None
        sig = self._track_sig(s, mode)
        k = (s.path, mode)
        hit = self._tracks.get(k)
        if hit is not None and hit[0] == sig:
            return hit[1]
        from .render import make_track
        t = make_track(s, a["z"], self.settings.render, mode)
        self._tracks[k] = (sig, t)
        return t

    def beauty_gap(self, s: Source) -> bool:
        """뷰티: 분석은 끝났는데 얼굴(타원 레이어)이 클립 전체에 깔리지 않았는가(v0.7.68, 소스 목록 노란색 표시용).
        얼굴을 못 찾은 프레임이 처음·끝에 있거나(3프레임 이상), 중간에 '놓침 유지'보다 길게 비면 True.
        타원 편집으로 끈 구간은 세지 않음(자동 검출 기준)."""
        if s is None or s.mode != "beauty":
            return False
        a = self.an.get((s.path, "beauty"))
        z = a.get("z") if a and a.get("state") == "done" else None
        if z is None or "lm" not in z:
            return False
        hold = 15
        if "_gap" not in a:                      # 분석 기록에 한 번만 계산해 둠(새로 분석하면 기록이 새로 생김)
            lm = z["lm"]
            miss = np.isnan(lm[:, :, 0, 0]).all(1) if lm.ndim == 4 else np.isnan(lm[:, 0, 0])
            gap = False
            n = len(miss)
            if n:
                idx = np.where(~miss)[0]
                if len(idx) == 0:
                    gap = True
                else:
                    gap = idx[0] >= 3 or (n - 1 - idx[-1]) >= 3 or bool((np.diff(idx) - 1 > hold).any())
            a["_gap"] = bool(gap)
        return a["_gap"]

    def has_track(self, s: Source, mode=None) -> bool:
        mode = mode or s.mode
        hit = self._tracks.get((s.path, mode))
        return hit is not None and hit[0] == self._track_sig(s, mode)

    def blur_resolvers(self, s: Source):
        """동시 블러: 처리할 각 종류의 (Resolver, mode). 트랙 없는 종류는 track=None 으로 포함(표시만).
        뷰티는 단일. 미리보기·오버레이·레인이 공통으로 씀."""
        if s.mode == "beauty":
            return [(edits.Resolver(s, self.track(s), "beauty"), "beauty")]
        return [(edits.Resolver(s, self.track(s, m), m), m) for m in s.blur_kinds()]

    def prepare_track(self, s: Source, on_done=None, on_error=None, mode=None):
        """분석이 끝난 소스의 트랙을 백그라운드에서 미리 계산해 캐시에 넣는다(AUTO [보정+편집])."""
        mode = mode or s.mode
        a = self.analysis(s, mode)
        if not a or a["state"] != "done":
            return None
        if self.has_track(s, mode):
            if on_done:
                on_done()
            return None
        sig = self._track_sig(s, mode)
        k = (s.path, mode)
        src = copy.deepcopy(s)
        z, rs = a["z"], dict(self.settings.render)

        def fn():
            from .render import make_track
            return make_track(src, z, rs, mode)

        def done(t):
            self._tracks[k] = (sig, t)
            if on_done:
                on_done()

        def fail(e, j_=None):
            # v0.7.98 트랙 계산 실패도 errors.log 에(예전엔 기록·재시도 없음) + 한 번 다시, 그래도 실패면 소스 목록 오류 표시
            jj = j_ or j
            self.log_error(f"{mode} 트랙", s.path, e, getattr(jj, "err_trace", ""))
            if e != "취소됨" and not getattr(jj, "_retried", False):
                j2 = jobs.Job(fn)
                j2._retried = True
                j2.signals.done.connect(done)
                j2.signals.error.connect(lambda e2: fail(e2, j2))
                jobs.submit(jobs.ANALYZE_POOL, j2, 5)
                return
            if e != "취소됨":
                self.errors[s.path] = f"트랙 계산 실패: {e}"
                self.changed.emit()
            if on_error:
                on_error(e)
        j = jobs.Job(fn)
        j.signals.done.connect(done)
        j.signals.error.connect(fail)
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
        if len(ell) >= 8:                     # 간판 4꼭짓점(표시는 크기 % 적용됨 → 되돌려 저장)
            from . import sign
            vec = tuple(sign.scale_quad(np.asarray(ell[:8], np.float32), 1 / sz).reshape(-1))
        else:
            vec = (ell[0], ell[1], ell[2] / sz, ell[3] / sz)
        edits.set_key(g, frame, self._geo_inv(s, g, frame, vec))
        self.sel = ("g", g["gid"])
        self._edited()

    def _geo_inv(self, s, g, frame, vec):
        """v0.8.0 표시값(레이어 여유·크기·목 적용됨) → 저장값(전체 값 기준). 레이어 모양 값이 없으면 그대로."""
        ov = g.get("ov") or {}
        if not any(k in ov for k in edits.GEO_KEYS):
            return tuple(vec)
        r = self.resolver(s)
        it = self._item(s, ("g", g["gid"]), frame)
        tid = it.tid if it is not None else r.g_tid.get(g["gid"])
        if len(vec) >= 8:
            q = np.asarray(vec[:8], np.float32).reshape(4, 2)
            _, q2 = r.geo(g, tid, frame, None, q, inverse=True)
            return tuple(float(x) for x in np.asarray(q2).reshape(-1))
        pts = it.pts if it is not None else None
        e2, _ = r.geo(g, tid, frame, (*vec[:4], 0.0), pts, inverse=True)
        return tuple(float(x) for x in e2[:4])

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
        return self.delete_ellipses([key], frame)

    def _by_mode(self, pairs):
        """[(mode, key)] → {mode: [key…]} (순서 유지, 중복 제거). mode 가 None 이면 지금 편집 종류."""
        s = self.source()
        out = {}
        for m, k in pairs:
            if k is None:
                continue
            m = m or (s.mode if s else None)
            if k not in out.setdefault(m, []):
                out[m].append(k)
        return out

    def delete_layers(self, pairs, frame):
        """v0.8.0 종류가 섞인 여러 레이어 삭제(Shift 범위 선택이 얼굴·간판을 함께 고름). 실행취소 한 번."""
        s = self.source()
        groups = self._by_mode(pairs)
        if s is None or not groups:
            return 0
        if len(groups) == 1:
            m, keys = next(iter(groups.items()))
            if m in ("blur", "sign"):
                self.set_active(m)
            return self.delete_ellipses(keys, frame)
        orig = s.mode
        self.push_undo()
        s = self.source()
        n = 0
        for m, keys in groups.items():
            s.mode = m
            self._res.clear()
            for k in keys:
                f = self._frame_with(s, k, frame)
                s, g = self._group_for(k, f)
                if g is None:
                    continue
                if g.get("manual"):
                    edits.remove_group(s, g["gid"])
                else:
                    g["off_all"] = True
                n += 1
                self._res.clear()
        s.mode = orig
        self._res.clear()
        if not n:
            self.undo_stack.pop()
            return 0
        self.sel = None
        self._edited()
        return n

    def duplicate_layers(self, pairs, frame):
        """v0.8.0 Ctrl+D 레이어 복사 = 독립 사본(수동 레이어). 원본의 움직임을 키로 구움(자동 키·사용자 키·구간 끝 프레임),
        자기 값(ov)·제외 여부 복사, 원본이 없는 구간은 꺼 둠. 같은 자리에 겹쳐 생김. → 새 (mode, key) 목록."""
        s = self.source()
        groups = self._by_mode(pairs)
        if s is None or not groups:
            return []
        orig = s.mode
        self.push_undo()
        s = self.source()
        made = []
        for m, keys in groups.items():
            s.mode = m
            self._res.clear()
            e = edits.get(s, m)
            for k in keys:
                d = self._span(s, k)
                if d is None or not d["intervals"]:
                    continue
                frs = set(d["keys"]) | set(d.get("ukeys", ()))
                for a, b in d["intervals"]:
                    frs |= {a, b}
                kv, ov, minus = {}, {}, False
                for f in sorted(frs):
                    it = self._item(s, k, f)
                    if it is None:
                        continue
                    if m == "sign" and it.pts is not None:
                        vec = np.asarray(it.pts, np.float32).reshape(-1)
                    else:
                        vec = np.asarray(it.ell[:4], np.float32)
                    kv[str(int(f))] = [float(x) for x in vec]
                    ov = {k2: v2 for k2, v2 in it.ov.items() if k2 not in edits.GEO_KEYS}   # 모양 값은 키에 이미 구워짐
                    minus = bool(it.minus)
                if not kv:
                    continue
                n = d["n"]
                off, prev = [], -1
                for a, b in d["intervals"]:
                    if a > prev + 1:
                        off.append([prev + 1, a - 1])
                    prev = b
                if prev < n - 1:
                    off.append([prev + 1, n - 1])
                f0 = min(int(x) for x in kv)
                v0 = kv[str(f0)]
                cx, cy = (float(np.mean(v0[0::2])), float(np.mean(v0[1::2]))) if len(v0) >= 8 else (v0[0], v0[1])
                g = {"gid": e["next"], "manual": True, "anchor": [f0, cx, cy], "keys": kv, "off": off,
                     "ov": ov, "minus": minus}
                e["next"] += 1
                e["groups"].append(g)
                made.append((m, ("g", g["gid"])))
                self._res.clear()
        s.mode = orig
        self._res.clear()
        if not made:
            self.undo_stack.pop()
            return []
        self.sel = made[0][1]
        self._edited()
        return made

    def delete_ellipses(self, keys, frame):
        """여러 타원을 한 번에 삭제(실행취소 한 번, v0.7.76). → 지운 개수."""
        s = self.source()
        keys = [k for k in dict.fromkeys(keys) if k is not None]
        if s is None or not keys:
            return 0
        frames = [self._frame_with(s, k, frame) for k in keys]          # 재생선이 구간 밖이어도 삭제(v0.7.73)
        self.push_undo()
        n = 0
        for k, f in zip(keys, frames):
            s, g = self._group_for(k, f)
            if g is None:
                continue
            if g.get("manual"):
                edits.remove_group(s, g["gid"])
            else:
                g["off_all"] = True
            n += 1
            self._res.clear()                    # 다음 타원을 찾을 때 방금 바뀐 편집이 반영되게
        if not n:
            self.undo_stack.pop()
            return 0
        self.sel = None
        self._edited()
        return n

    def _span(self, s, key):
        return next((d for d in self.ellipse_spans(s) if d["key"] == key and d.get("mode", s.mode) == s.mode), None)

    def _frame_with(self, s, key, frame):
        """key 타원이 실제로 있는 프레임: 지금 프레임에 있으면 그대로, 없으면 그 레인의 첫 구간 시작."""
        if self._item(s, key, frame) is not None:
            return frame
        d = self._span(s, key)
        return d["intervals"][0][0] if d and d["intervals"] else frame

    def _bake_akeys(self, s, g, key, d):
        """오토 트랙의 현재 키들을 '고정 키프레임'(g["akeys"])으로 한 번 구움. 이미 있으면 그대로.
        이후 삭제·편집은 이 키프레임만 건드리므로 다른 키가 안 움직임(v0.7.83)."""
        if edits.baked(g):
            return
        it = self._item(s, key, d["intervals"][0][0] if d["intervals"] else 0)
        tid = key[1] if key[0] == "t" else (it.tid if it is not None else None)
        r = self.resolver(s)
        if tid is None:
            tid = r.g_tid.get(g["gid"])
        ak = {}
        for k in d["keys"]:
            v = r._auto_vec(tid, int(k)) if tid is not None else None
            if v is not None:
                ak[str(int(k))] = [float(x) for x in v]      # 그때의 검출값을 그대로 저장
        g["akeys"] = ak
        g.pop("flat", None)                                  # 예전 방식 제거(이중 적용 방지)
        g.pop("hold", None)

    def delete_auto_key(self, key, frame):
        """오토 키 ◆ 삭제(v0.7.83 '키 고정'): 처음 지우면 현재 오토 키를 진짜 키프레임으로 구운 뒤,
        그 키 하나만 제거. 다른 키는 그대로, 사이는 직선으로 이어짐. → "" 또는 못 지운 이유."""
        s = self.source()
        d = self._span(s, key) if s is not None else None
        if d is None:
            return "키를 찾지 못했습니다"
        frame = int(frame)
        if frame not in d["keys"]:
            return "키를 찾지 못했습니다"
        if len(d["keys"]) + len(d.get("ukeys", ())) < 2:
            return "키가 하나뿐이라 지울 수 없습니다"
        self.push_undo()
        s, g = self._group_for(key, frame)
        if g is None or g.get("manual"):
            self.undo_stack.pop()
            return "키를 찾지 못했습니다"
        self._bake_akeys(s, g, key, d)
        ak = g.get("akeys") or {}
        # v0.8.0 마지막 오토 키도 지울 수 있음(사용자 키가 남아 있으면) — 키가 하나도 안 남는 것만 막음
        if str(frame) not in ak or len(ak) + len(g.get("keys", {})) < 2:
            self.undo_stack.pop()
            self._res.clear()
            self._spans_cache = (None,)
            return "키가 하나뿐이라 지울 수 없습니다" if str(frame) in ak else "키를 찾지 못했습니다"
        del ak[str(frame)]
        g["akeys"] = ak
        self._res.clear()                        # 리졸버 캐시 비움(akeys 반영)
        self.sel = ("g", g["gid"])
        self._edited()
        return ""

    def delete_after(self, keys, frame, everything=False):
        """v0.8.0 키 우클릭 '이후 자동 키 삭제'(everything=False) / '이후 키 모두 삭제'(True).
        keys = 대상 레이어들(누른 레이어 + 함께 선택된 레이어). frame 보다 뒤의 키만 지움(누른 키는 남김).
        자동 키를 지우면 그 레이어는 고정 키프레임 모드(bake) → 남은 키로만 움직이고 마지막 키 뒤는 그 값 유지(AE 방식).
        키가 하나도 안 남게 되면 가장 앞 키 하나는 남김. → 지운 키 수."""
        s = self.source()
        if s is None:
            return 0
        self.push_undo()
        frame, n_del = int(frame), 0
        for key in keys:
            d = self._span(s, key)
            if d is None:
                continue
            s, g = self._group_for(key, self._frame_with(s, key, frame))
            if g is None:
                continue
            if not g.get("manual"):
                if any(k > frame for k in d["keys"]) or (everything and any(k > frame for k in d.get("ukeys", ()))):
                    self._bake_akeys(s, g, key, d)
                ak = g.get("akeys")
                if ak is not None:
                    for k in [k for k in ak if int(k) > frame]:
                        del ak[k]
                        n_del += 1
            if everything:
                uk = g.get("keys", {})
                for k in [k for k in uk if int(k) > frame]:
                    del uk[k]
                    n_del += 1
            left = len(g.get("akeys") or {}) + len(g.get("keys", {}))
            if left == 0 and not g.get("manual"):           # 다 지워짐(누른 지점이 모든 키 앞) → 그 프레임 값으로 키 하나
                self._res.clear()
                it = self._item(s, ("g", g["gid"]), frame)
                if it is not None:
                    vec = np.asarray(it.pts, np.float32).reshape(-1) if (s.mode == "sign" and it.pts is not None) \
                        else np.asarray(it.ell[:4], np.float32)
                    g.setdefault("keys", {})[str(frame)] = [float(x) for x in self._geo_inv(s, g, frame, tuple(vec))]
        if n_del == 0:
            self.undo_stack.pop()
            return 0
        self._res.clear()
        self._edited()
        return n_del

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

    def set_minus(self, v, frame):
        """선택 타원을 블러 제외(True) / 블러(False)로. 자동 타원도 그룹을 만들어 트랙을 따라감."""
        self.push_undo()
        s, g = self._group_for(self.sel, frame)
        if g is None or bool(g.get("minus")) == bool(v):
            self.undo_stack.pop()
            return
        g["minus"] = bool(v)
        self.sel = ("g", g["gid"])
        self._edited()

    def minus_of(self, key, frame):
        s = self.source()
        it = self._item(s, key, frame) if (s and key) else None
        return bool(it and it.minus)

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
                if s.mode == "sign" and it.pts is not None:
                    return tuple(np.asarray(it.pts, np.float32).reshape(-1)), f
                return tuple(it.ell[:4]), f
        return None, None

    def ellipse_spans(self, s):
        """타원별 {key,mode,label,intervals,keys,n}. 동시 블러면 두 종류(얼굴·간판) 레인을 함께. 캐시."""
        resolvers = []
        info = self.infos.get(s.path)
        for r, m in self.blur_resolvers(s):
            if r.track is not None:
                resolvers.append((r, m))
            elif info is not None and any(g.get("manual") for g in edits.get(s, m)["groups"]):
                r.n = int(info.frames)               # v0.8.1 분석 전에도 추가·복사 레이어는 레인에(수동은 트랙 없이 그려짐)
                resolvers.append((r, m))
        if not resolvers:
            return []
        n = max(r.n for r, _ in resolvers)
        sig = (s.path, tuple((m, id(r.track)) for r, m in resolvers), n,
               tuple(json.dumps(edits.get(s, m), sort_keys=True) for _, m in resolvers))
        if getattr(self, "_spans_cache", (None,))[0] == sig:
            return self._spans_cache[1]
        from . import fx                              # 지연 import(순환 방지)
        step = max(1, n // 800)
        seen, order = {}, []
        for res, mode in resolvers:
            p = s.params_for(mode)
            for i in range(0, n, step):
                for it in res.items(i):
                    sk = (mode, it.key)                 # 종류로 구분(얼굴·간판 키가 겹칠 수 있음)
                    if sk not in seen:
                        lbl = str(it.tid) if it.tid is not None else ("+" if it.manual else "·")
                        if it.minus:
                            lbl = "−" + (lbl if lbl not in ("+", "·") else "")
                        tinfo = res.track.tracks.get(it.tid) if (it.tid is not None and hasattr(res.track, "tracks")) else None
                        seen[sk] = {"label": lbl, "frames": [], "geo": [], "minus": it.minus, "mode": mode,
                                    "tid": it.tid, "ord": len(order), "res": res,
                                    "real": [],
                                    "sus": bool(tinfo and tinfo.get("sus")) and not it.manual and not it.minus,
                                    "note": (tinfo or {}).get("note", ""),
                                    "quad": bool(getattr(it, "pts", None) is not None
                                                 and np.asarray(it.pts).size == 8), "manual": it.manual}
                        order.append(sk)
                    seen[sk]["frames"].append(i)
                    seen[sk]["geo"].append(_lane_geo(it, p, fx))   # 오토 키 계산용 기하
                    seen[sk]["real"].append(it.tid is not None and not it.manual
                                            and res._auto_vec(it.tid, i) is not None)   # 진짜 검출?
        # v0.7.84 레인 순서(사용자 규칙): ① 타원(얼굴)이 항상 위, 사각형(간판)이 아래 ② 각 묶음 안에서
        # 나타난 순서(트랙 번호)대로 ③ 선택·편집·그룹화해도 안 바뀜(트랙 번호·ord 는 불변이라 안정적).
        order.sort(key=lambda sk: (1 if seen[sk]["quad"] else 0,
                                   seen[sk]["tid"] if seen[sk]["tid"] is not None else 10 ** 6 + seen[sk]["ord"]))
        out = []
        for sk in order:
            mode, k = sk
            d = seen[sk]
            fr = d["frames"]
            intervals, a, prev = [], fr[0], fr[0]
            for f in fr[1:]:
                if f - prev > step * 2:
                    intervals.append((a, prev))
                    a = f
                prev = f
            intervals.append((a, prev))
            groups = {g["gid"]: g for g in edits.get(s, mode)["groups"]}
            g = groups.get(k[1]) if k[0] == "g" else None
            ukeys = sorted(int(x) for x in g.get("keys", {})) if g else []
            if g is not None and g.get("manual"):       # 추가 타원 = 자동 없음, 사용자 키만
                keys = []
            elif g is not None and edits.baked(g):      # v0.7.83 고정 키프레임 = 그대로(다른 키 안 움직임)
                keys = [k for k in sorted(int(f) for f in g["akeys"])
                        if any(a <= k <= b for a, b in intervals)]
            else:                                       # 오토 트랙 = 실제 검출 프레임에서만 자동 키(늘린 구간 제외)
                rfr = [f for f, ok in zip(fr, d["real"]) if ok]
                rgeo = [gg for gg, ok in zip(d["geo"], d["real"]) if ok]
                keys = _auto_keys(rfr, rgeo, intervals) if rfr else []
            # v0.7.87 클립 처음~끝을 연속으로 안 덮으면(시작이 0 이 아님·끝이 n-1 이 아님·중간 틈) 미완성 → 회색
            partial = (not d["manual"] and not d["minus"]
                       and (intervals[0][0] != 0 or intervals[-1][1] != n - 1 or len(intervals) != 1))
            lk = {edits.ov_key(mode, x) for x in edits.LAYER_KEYS.get(mode, ())}
            own = bool(g and any(x in (g.get("ov") or {}) for x in lk))      # v0.8.0 자기 값 가진 레이어(레인 점)
            out.append({"key": k, "mode": mode, "label": d["label"], "intervals": intervals, "keys": keys, "own": own,
                        "ukeys": ukeys, "n": n, "minus": d["minus"], "quad": d["quad"], "manual": d["manual"],
                        "sus": d["sus"], "partial": partial, "note": d["note"] if d["sus"] else ""})
        self._spans_cache = (sig, out)
        return out

    def set_spans(self, key, segments):
        """타원을 여러 구간 [(in,out), …] 에만 존재하게(구간 양끝 홀드, 틈·바깥은 끄기).
        맞닿거나 겹친 구간은 합쳐짐(틈 메우기). 구간별 양끝을 각각 끌어 편집."""
        s = self.source()
        tr = self.track(s)
        if s is None or tr is None or key is None or not segments:
            return
        n = tr.n
        segs = []
        for a, b in segments:
            a = max(0, min(int(a), n - 1))
            b = max(a, min(int(b), n - 1))
            segs.append((a, b))
        segs.sort()
        merged = []
        for a, b in segs:                      # 맞닿음(+1 이내)·겹침 → 합침
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        e0, af = self._present_ell(s, key, merged[0][0], n)
        if e0 is None:
            return
        self.push_undo()
        _, g = self._group_for(key, af)
        if g is None:
            self.undo_stack.pop()
            return
        # v0.7.91 구간 드래그는 키를 전혀 만들지 않음: 구간만 기록(g["spans"]) → 리졸버가 구간 안의 빈 프레임을
        # '가장 가까운 값(검출·사용자 키) 유지/사이 보간'으로 채움. (예전 v0.7.85 숨은 홀드 키는 끝 프레임에서
        # 옮긴 값이 숨겨지고, 다시 끌면 지워지는 문제가 있었음)
        g.pop("hold_frames", None)
        g["spans"] = [[int(a), int(b)] for a, b in merged]
        self._res.clear()
        g["off_all"] = False
        g["off"] = []
        prev = 0
        for a, b in merged:                    # 틈·바깥 = 끄기(여집합)
            if a > prev:
                edits.off_range(g, prev, a - 1)
            prev = b + 1
        if prev <= n - 1:
            edits.off_range(g, prev, n - 1)
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

    def set_layer_values(self, keys, frame, k, v):
        """v0.8.0 왼쪽 패널(B안): 선택한 레이어들에만 값(k = 패널 키, v = None 이면 그 값 지움).
        실행취소는 부른 쪽(슬라이더 누를 때 push_undo). → {옛 key: 새 key}(자동 트랙 ('t', tid) → 그룹 ('g', gid))."""
        s = self.source()
        if s is None:
            return {}
        ok = edits.ov_key(s.mode, k)
        moved = {}
        for key in keys:
            f = self._frame_with(s, key, frame)
            s, g = self._group_for(key, f)
            if g is None:
                continue
            if v is None:
                g.setdefault("ov", {}).pop(ok, None)
            else:
                g.setdefault("ov", {})[ok] = v
            moved[key] = ("g", g["gid"])
            self._res.clear()
        if self.sel in moved:
            self.sel = moved[self.sel]
        self._edited()
        return moved

    def clear_layer_values(self, keys, frame):
        """[전체 값으로]: 선택한 레이어들의 자기 값(패널 값) 모두 지움(타원 배율 size·제외 여부는 유지)."""
        s = self.source()
        if s is None:
            return
        self.push_undo()
        drop = {edits.ov_key(s.mode, k) for k in edits.LAYER_KEYS.get(s.mode, ())}
        for key in keys:
            f = self._frame_with(s, key, frame)
            s, g = self._group_for(key, f, create=False)
            if g is None:
                continue
            for k in drop:
                g.setdefault("ov", {}).pop(k, None)
            self._res.clear()
        self._edited()

    def layer_values(self, key, frame):
        """선택 레이어의 패널 값(자기 값이 없으면 전체 값). → {패널 키: 값}, {자기 값인 패널 키}."""
        s = self.source()
        if s is None or key is None:
            return {}, set()
        p = s.params_for(s.mode)
        f = self._frame_with(s, key, frame)
        _, g = self._group_for(key, f, create=False)
        ov = (g or {}).get("ov") or {}
        vals, own = {}, set()
        for k in edits.LAYER_KEYS.get(s.mode, ()):
            ok = edits.ov_key(s.mode, k)
            if ok in ov:
                vals[k] = ov[ok]
                own.add(k)
            else:
                vals[k] = p.get(k)
        return vals, own

    def ov_of(self, key, frame):
        s = self.source()
        it = self._item(s, key, frame) if s and key else None
        return dict(it.ov) if it else None

    def cancel_all(self):
        for a in self.an.values():
            j = a.get("job")
            if j is not None:
                j.cancel.set()
