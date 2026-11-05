"""메인 창: 상단(새 프로젝트·저장하기·불러오기·실행취소) + 3열(보정 | 모니터 | 소스·렌더)."""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QToolBar, QVBoxLayout,
                               QWidget)

from . import __version__, analyze, edits, ff, fx, jobs, paths
from .project import Project
from .render import render as do_render
from .state import AppState
from .ui_monitor import Monitor, TimeBar
from .ui_panel import ControlPanel
from .ui_side import VIDEO_EXT, RenderItem, RenderPanel, RenderSettings, SourceList
from .ui_widgets import BTN_MAIN, QSS, btn

PROJ_FILTER = "MOSCO 프로젝트 (*.mosco.json *.json)"
VID_FILTER = "영상 (" + " ".join("*" + e for e in sorted(VIDEO_EXT)) + ")"
PLAY_SIZE = (960, 540)


def to8(a16):
    return (a16 >> 8).astype(np.uint8)


def gray8(m):
    return np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8)


# ------------------------------------------------------------ 미리보기 계산(작업 스레드)
def temp_items(src, rgb8):
    """분석이 끝나기 전: 이 프레임만 바로 검출."""
    p = src.params()
    out = []
    if src.mode == "beauty":
        pts, _ = analyze.beauty_frame(rgb8, None)
        if pts is not None:
            out.append(edits.Item(("t", 1), 1, analyze.head_ellipse(pts, p["size"], p["neck"]), pts))
    else:
        thr = analyze.score_thr(p["sensitivity"])
        k = 0
        for r in analyze.blur_frame(rgb8, body=p.get("body", True)):
            x0, y0, x1, y1, sc = r[:5]
            if sc >= thr and (y1 - y0) >= p["min_size"]:
                k += 1
                out.append(edits.Item(("t", k), k, analyze.face_to_head((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0,
                                                                         y1 - y0, p["margin"])))
    return out


def entries(ells):
    """[(item, 실제 적용 타원)] → 모니터 표시용."""
    out = []
    for it, ell in ells:
        lab = str(it.tid) if it.tid is not None else ("+" if it.manual else None)
        out.append({"key": it.key, "label": lab, "ell": tuple(float(v) for v in ell), "manual": it.manual,
                    "keyed": it.keyed})
    return out


def item_entries(items, p):
    return entries([(it, fx._item_ell(it, p)) for it in items])


def compute_preview(src, frame16, resolver, has_track, i):
    """한 프레임: 원본·보정·키 3종·타원."""
    H, W = frame16.shape[:2]
    out = {"orig": to8(frame16)}
    p = src.params()
    items = resolver.items(i)
    if not has_track:
        items = temp_items(src, out["orig"]) + items
    if src.mode == "beauty":
        r = fx.beauty(frame16, items, p, i, want_keys=True)
        out["fx"] = to8(r.out)
        out["ells"] = entries(r.ells)
        focus = next((e for it, e in r.ells if not it.manual), r.ells[0][1] if r.ells else None)
        out["focus"] = focus
        out["final"] = gray8(r.region)
        out["alpha"] = gray8(r.alpha)
        pts = next((it.pts for it in items if it.pts is not None), None)
        fw = float(np.ptp(pts[:, 0])) if pts is not None else 300.0
        out["skin"] = gray8(fx.skin_key_full(frame16, p, r.ref, fw))
        out["faces"] = len(items)
    else:
        o, a, ells = fx.blur(frame16, items, p, True)
        out["fx"] = to8(o)
        out["ells"] = entries(ells)
        out["focus"] = None
        out["alpha"] = out["final"] = gray8(a)
        out["skin"] = out["alpha"]
        out["faces"] = len(items)
    return out


class Player:
    """재생: ffmpeg 파이프(960×540) → 큐 → 타이머로 표시. 소리 없음."""

    def __init__(self, info, start, on_frame, on_end):
        self.info = info
        self.i = start
        self.q = queue.Queue(maxsize=12)
        self.stop_ev = threading.Event()
        self.on_frame, self.on_end = on_frame, on_end
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.t0 = None
        self.timer.start(max(5, int(500 / info.fps_f)))

    def _run(self):
        r = ff.Reader(self.info, self.i, 8, size=PLAY_SIZE)
        try:
            while not self.stop_ev.is_set():
                fr = r.read()
                if fr is None:
                    self.q.put(None)
                    break
                while not self.stop_ev.is_set():
                    try:
                        self.q.put(fr, timeout=0.1)
                        break
                    except queue.Full:
                        pass
        finally:
            r.close()

    def _tick(self):
        now = time.perf_counter()
        if self.t0 is None:
            if self.q.qsize() < 3 and self.t.is_alive():
                return
            self.t0 = now - 0.0001
            self.f0 = self.i
        want = self.f0 + int((now - self.t0) * self.info.fps_f)
        fr = None
        while self.i <= want:
            try:
                x = self.q.get_nowait()
            except queue.Empty:
                break
            if x is None:
                self.stop()
                self.on_end()
                return
            fr = x
            self.i += 1
        if fr is not None:
            self.on_frame(self.i - 1, fr)

    def stop(self):
        self.stop_ev.set()
        self.timer.stop()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.state = AppState()
        self.resize(1700, 980)
        self.setAcceptDrops(True)
        self.setStyleSheet(QSS)
        self.f = 0
        self.gen = 0
        self.player = None
        self.frame_cache = None        # (path, i, frame16)
        self.result = None             # compute_preview 결과
        self.pos = {}                  # 소스별 마지막 위치

        tb = QToolBar("main")
        tb.setMovable(False)
        tb.setStyleSheet("QToolBar{spacing:6px;padding:3px;background:#232323;border-bottom:1px solid #333}"
                         "QToolButton{padding:4px 12px;background:transparent;border:none}"
                         "QToolButton:hover{background:#333}")
        self.addToolBar(tb)
        a = QAction("새 프로젝트", self)
        a.triggered.connect(self.new_project)
        tb.addAction(a)
        tb.addSeparator()
        for text, key, fn in (("저장하기", QKeySequence.Save, self.save_project),
                              ("불러오기", QKeySequence.Open, self.open_project),
                              ("실행취소", QKeySequence.Undo, self.undo)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.setShortcutContext(Qt.ApplicationShortcut)
            a.setToolTip(QKeySequence(key).toString())
            a.triggered.connect(fn)
            tb.addAction(a)

        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet("QWidget#root{background:#1b1b1b}")
        h = QHBoxLayout(root)
        h.setContentsMargins(4, 4, 4, 4)
        h.setSpacing(4)
        self.panel = ControlPanel(self.state)
        self.panel.extra_selected = lambda: self.sources.selected()
        h.addWidget(self.panel)

        mid = QVBoxLayout()
        mid.setSpacing(4)
        self.mon = Monitor()
        mid.addWidget(self.mon, 1)
        self.bar = TimeBar()
        mid.addWidget(self.bar)
        tr = QHBoxLayout()
        tr.setSpacing(4)
        tr.addStretch(1)
        self.b_play = btn("▶ / ❚❚", "재생/정지 (Space)", size=BTN_MAIN)
        for t, tip, fn in (("I◀", "처음 (Home)", lambda: self.seek(0)),
                           ("◀◀", "1초 뒤 (Shift+←)", lambda: self.step(-self._sec())),
                           ("◀|", "1프레임 뒤 (←)", lambda: self.step(-1)),
                           (None, None, None),
                           ("|▶", "1프레임 앞 (→)", lambda: self.step(1)),
                           ("▶▶", "1초 앞 (Shift+→)", lambda: self.step(self._sec())),
                           ("▶I", "끝 (End)", lambda: self.seek(10 ** 9))):
            if t is None:
                tr.addWidget(self.b_play)
                continue
            tr.addWidget(btn(t, tip, on_click=fn))
        self.b_play.clicked.connect(self.toggle_play)
        self.tc = QLabel("00:00:00;00")
        self.tc.setStyleSheet("background:#000;color:#e8e8e8;font:300 14px Consolas,monospace;padding:4px 10px;"
                              "border-radius:2px;margin-left:10px")
        tr.addWidget(self.tc)
        tr.addStretch(1)
        mid.addLayout(tr)
        self.status = QLabel("")
        self.status.setFixedHeight(20)
        self.status.setStyleSheet("background:#1c1c1c;border-radius:3px;color:#7fd18b;padding-left:8px")
        mid.addWidget(self.status)
        h.addLayout(mid, 1)

        right = QWidget()
        right.setFixedWidth(220)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(3)
        self.sources = SourceList(self.state)
        self.sources.add_dialog = self.add_dialog
        rv.addWidget(self.sources, 1)
        r = QHBoxLayout()
        r.setSpacing(4)
        b1 = btn("영상 추가", on_click=self.add_dialog)
        b2 = btn("삭제", "선택한 소스 삭제 (Del)", on_click=self.remove_sources)
        b1.setFixedWidth(106)
        b2.setFixedWidth(106)
        r.addWidget(b1)
        r.addWidget(b2)
        rv.addLayout(r)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#333")
        rv.addWidget(line)
        self.rpanel = RenderPanel()
        self.rpanel.setFixedHeight(300)
        self.rpanel.settings_clicked.connect(self.render_settings)
        self.rpanel.render_clicked.connect(self.render_selected)
        self.rpanel.on_rerender = self.rerender
        rv.addWidget(self.rpanel)
        h.addWidget(right)
        self.setCentralWidget(root)

        # 신호
        self.state.current_changed.connect(self.on_current)
        self.state.params_changed.connect(self.on_params)
        self.state.analysis_changed.connect(self.on_analysis)
        self.state.changed.connect(self._title)
        self.state.message.connect(self.flash)
        self.panel.view_changed.connect(self.show_result)
        self.panel.pick_skin.connect(self.mon.set_picking)
        self.mon.picked.connect(self.on_pick)
        self.bar.seek.connect(self.seek)
        self.bar.del_key.connect(lambda k: self.state.delete_key(k))
        self.panel.tool.connect(self.on_tool)
        self.mon.add_at.connect(self.on_add)
        self.mon.selected.connect(self.on_select)
        self.mon.edited.connect(lambda key, ell: self.state.edit_ellipse(key, self.f, ell))
        self.mon.merge_with.connect(self.on_merge)
        self.panel.del_key.connect(lambda: self.state.delete_key(self.f) or self.flash("이 프레임에 키가 없습니다"))
        self.panel.off.connect(lambda how: self.state.off(how, self.f, self._n()))
        self.panel.ov_pressed.connect(self.state.push_undo)
        self.panel.ov_changed.connect(self.on_ov)
        self._merge_anchor = None

        self._full_t = QTimer(self, singleShot=True, interval=350, timeout=self.request_full)
        self._param_t = QTimer(self, singleShot=True, interval=120, timeout=self.request_full)
        self._flash_t = QTimer(self, singleShot=True, interval=6000, timeout=self.update_status)
        self._autosave = QTimer(self, interval=60_000, timeout=self._do_autosave)
        self._autosave.start()
        self._keys()
        self._title()
        self.update_status()

    # ------------------------------------------------------------ 단축키
    def _keys(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)
        sc(Qt.Key_Space, self.toggle_play)
        sc(Qt.Key_Left, lambda: self.step(-1))
        sc(Qt.Key_Right, lambda: self.step(1))
        sc(QKeySequence(Qt.SHIFT | Qt.Key_Left), lambda: self.step(-self._sec()))
        sc(QKeySequence(Qt.SHIFT | Qt.Key_Right), lambda: self.step(self._sec()))
        sc(Qt.Key_Home, lambda: self.seek(0))
        sc(Qt.Key_End, lambda: self.seek(10 ** 9))
        sc(Qt.Key_Plus, lambda: self.mon.zoom_step(1))
        sc(Qt.Key_Equal, lambda: self.mon.zoom_step(1))
        sc(Qt.Key_Minus, lambda: self.mon.zoom_step(-1))
        sc(Qt.Key_0, self.mon.reset_view)
        sc(Qt.Key_W, lambda: self.panel.b_pick.toggle())
        sc(Qt.Key_B, self._toggle_orig)
        sc(QKeySequence(Qt.CTRL | Qt.Key_R), self.render_selected)
        sc(Qt.Key_Delete, self._delete_key)
        sc(Qt.Key_A, lambda: self.panel.b_add.toggle())
        sc(Qt.Key_Escape, self._esc)

    # ------------------------------------------------------------ 타원 편집
    def _n(self):
        info = self._info()
        return info.frames if info else 0

    def on_tool(self, t):
        self.mon.set_tool(t)
        if t == "merge":
            self._merge_anchor = self.state.anchor_of(self.state.sel, self.f) if self.state.sel else None
            if self._merge_anchor is None:
                self.flash("먼저 타원을 선택하세요")
                self.panel.stop_tool()

    def on_add(self, x, y):
        info = self._info()
        s = self.state.source()
        if not info or not s:
            return
        size = info.h * (0.3 if s.mode == "beauty" else 0.09)
        self.state.add_ellipse(self.f, x, y, size)
        self.panel.stop_tool()

    def on_merge(self, key_b):
        if self.state.merge(self.state.sel, self._merge_anchor, key_b, self.f):
            self.flash("같은 사람으로 합쳤습니다")
        self.panel.stop_tool()

    def on_select(self, key):
        self.state.sel = key
        self._sel_refresh()

    def on_ov(self, k, v):
        if k == "reset":
            self.state.push_undo()
            for kk in ("size", "feather", "strength"):
                self.state.set_ov(self.state.sel, self.f, kk, None, undo=False)
            return
        self.state.set_ov(self.state.sel, self.f, k, v, undo=False)

    def _sel_refresh(self):
        s = self.state.source()
        if s is None:
            self.bar.set_keys([])
            self.panel.show_selection(None, None)
            return
        sel = self.state.sel
        if sel is not None and sel[0] == "g" and not any(g["gid"] == sel[1] for g in edits.get(s)["groups"]):
            sel = self.state.sel = None          # 실행취소 등으로 없어진 타원
        self.mon.set_selected(sel)
        r = self.state.resolver(s)
        self.bar.set_keys(r.keys_of(sel))
        self.panel.show_selection(self.state.ov_of(sel, self.f) if sel else None, s.params())

    def _esc(self):
        self.panel.stop_pick()
        if self.state.sel is not None:
            self.state.sel = None
            self._sel_refresh()

    def _toggle_orig(self):
        v = self.panel.view_mode()
        self.panel.view.set("orig" if v != "orig" else "fx")
        self.show_result()

    def _delete_key(self):
        if self.rpanel.list.hasFocus():
            self.rpanel.remove_rows(sorted(self.rpanel.list.row(i) for i in self.rpanel.list.selectedItems()))
        elif self.sources.hasFocus():
            self.remove_sources()
        elif self.state.sel is not None:
            self.state.delete_key(self.f)

    def _sec(self):
        info = self._info()
        return int(round(info.fps_f)) if info else 30

    # ------------------------------------------------------------ 상태 표시
    def _title(self):
        f = Path(self.state.project.file).name if self.state.project.file else "새 프로젝트"
        self.setWindowTitle(f"MOSCO {__version__} — {f}{' *' if self.state.dirty else ''}")

    def flash(self, t):
        self.status.setText(t)
        self._flash_t.start()

    def update_status(self):
        info = self._info()
        if not info:
            self.status.setText("")
            return
        fps = f"{info.fps_f:.3f}".rstrip("0").rstrip(".")
        t = f"{info.w}×{info.h} {fps} · {ff.frames_to_tc(max(0, info.frames - 1), info.fps)}"
        if self.result is not None:
            t += f" · 얼굴 {self.result.get('faces', 0)}"
        s = self.state.source()
        tr = self.state.track(s) if s else None
        if s and s.mode == "blur" and tr is not None:
            t += f" · ID {tr.ids}"
        a = self.state.analysis(s) if s else None
        if a and a["state"] == "run":
            t += f" · 분석 {int(a['p'] * 100)}%"
        self.status.setText(t)

    def _info(self):
        s = self.state.source()
        return self.state.info(s.path) if s else None

    # ------------------------------------------------------------ 소스 전환
    def on_current(self):
        self.stop_play()
        s = self.state.source()
        self.result = None
        self.frame_cache = None
        if s is None:
            self.mon.clear()
            self.bar.set_data(0)
            self.update_status()
            return
        self.mon.clear(loading=True)
        self.mon.reset_view()
        self.state.sel = None
        self.panel.stop_tool()
        self._sel_refresh()
        info = self.state.info(s.path)
        self._bar()
        if info is None:
            self.update_status()
            return
        self.seek(self.pos.get(s.path, 0), force=True)

    def _bar(self):
        s = self.state.source()
        info = self._info()
        if s is None or info is None:
            self.bar.set_data(0, error=self.state.errors.get(s.path, "") if s else "")
            return
        a = self.state.analysis(s)
        n = info.frames
        cov = None
        prog = None
        if a and a["state"] == "done":
            tr = self.state.track(s)
            n = tr.n
            if s.mode == "beauty":
                cov = (tr.presence > 0).astype(np.int8)
            else:
                cov = np.zeros(tr.n, np.int8)
                for i, fs in enumerate(tr.frames):
                    if fs:
                        cov[i] = 1 if all(f[5] for f in fs) else 2
        elif a:
            prog = a["p"]
        self.bar.set_data(n, cov, prog, self.state.errors.get(s.path, ""))
        self.bar.set_cur(self.f)

    def on_analysis(self, path):
        s = self.state.source()
        if s and s.path == path:
            self._bar()
            self.update_status()

    def on_params(self):
        self._bar()
        self._sel_refresh()
        self._overlay_from_track(self.f)
        self._param_t.start()

    # ------------------------------------------------------------ 이동
    def seek(self, i, force=False):
        info = self._info()
        if info is None:
            return
        n = max(1, info.frames)
        i = int(max(0, min(n - 1, i)))
        if i == self.f and not force and self.result is not None:
            return
        if self.player:
            self.stop_play()
        self.f = i
        self.pos[info.path] = i
        self.bar.set_cur(i)
        if self.state.sel is not None:
            self.panel.show_selection(self.state.ov_of(self.state.sel, i), self.state.source().params())
        self.tc.setText(ff.frames_to_tc(ff.tc_to_frames(info.timecode, info.fps) + i, info.fps))
        self._quick(i)
        self._full_t.start()

    def step(self, d):
        self.seek(self.f + d)

    def _quick(self, i):
        """즉시: 작은 원본 프레임 + 타원(트랙이 있으면)."""
        s = self.state.source()
        info = self._info()
        self.gen += 1
        g = self.gen
        path = info.path

        def fn():
            return ff.grab(info, i, 8, PLAY_SIZE)
        j = jobs.Job(fn)
        j.signals.done.connect(lambda a: self._quick_done(g, path, i, a))
        jobs.submit(jobs.PREVIEW_POOL, j, 5)
        _ = s

    def _quick_done(self, g, path, i, a):
        if g != self.gen or self.result is not None and self.result.get("i") == i:
            return
        info = self._info()
        if not info or info.path != path:
            return
        self.mon.set_images([a], (info.w, info.h))
        self._overlay_from_track(i)

    def _overlay_from_track(self, i):
        s = self.state.source()
        ells = []
        if s is not None and self.state.track(s) is not None:
            ells = item_entries(self.state.resolver(s).items(i), s.params())
        self.mon.set_overlay(ells, self.panel.t_ell.isChecked(), s is not None and s.mode == "blur" and
                             self.panel.t_id.isChecked())

    def request_full(self):
        s = self.state.source()
        info = self._info()
        if s is None or info is None or self.player:
            return
        self.gen += 1
        g = self.gen
        i = self.f
        import copy
        src = copy.deepcopy(s)
        track = self.state.track(s)
        res_ = edits.Resolver(src, track)
        fc = self.frame_cache if (self.frame_cache and self.frame_cache[:2] == (info.path, i)) else None

        def fn():
            fr = fc[2] if fc else ff.grab(info, i, 16)
            res = compute_preview(src, fr, res_, track is not None, i)
            res["frame16"] = fr
            res["i"] = i
            return res
        j = jobs.Job(fn)
        j.signals.done.connect(lambda r: self._full_done(g, info.path, r))
        j.signals.error.connect(lambda e: self.flash(f"미리보기 오류: {e}"))
        jobs.submit(jobs.PREVIEW_POOL, j, 3)

    def _full_done(self, g, path, r):
        info = self._info()
        if g != self.gen or not info or info.path != path:
            return
        self.frame_cache = (path, r["i"], r.pop("frame16"))
        self.result = r
        self.show_result()
        self.update_status()

    def show_result(self):
        r = self.result
        s = self.state.source()
        info = self._info()
        if r is None or s is None or info is None:
            if s is not None:
                self._overlay_from_track(self.f)
            return
        v = self.panel.view_mode()
        k = self.panel.key_mode
        main = r["fx"] if k == "off" else r.get(k, r["final"])
        if v == "orig":
            imgs = [r["orig"]]
        elif v == "split":
            imgs = [r["orig"], main]
        else:
            imgs = [main]
        self.mon.set_default("face" if (v == "split" and s.mode == "beauty") else "fit")
        self.mon.set_focus_ellipse(r.get("focus"))
        self.mon.set_images(imgs, (info.w, info.h))
        self.mon.set_overlay(r["ells"], self.panel.t_ell.isChecked(),
                             s.mode == "blur" and self.panel.t_id.isChecked())

    # ------------------------------------------------------------ 재생
    def toggle_play(self):
        if self.player:
            self.stop_play()
            self.seek(self.f, force=True)
            return
        info = self._info()
        if info is None:
            return
        if self.f >= info.frames - 1:
            self.f = 0
        self.result = None
        self.gen += 1
        self.mon.set_default("fit")
        self.player = Player(info, self.f, self._play_frame, self._play_end)
        self.b_play.setProperty("on", True)
        self.b_play.style().polish(self.b_play)

    def _play_frame(self, i, fr):
        info = self._info()
        if not info:
            return
        self.f = i
        self.bar.set_cur(i)
        self.tc.setText(ff.frames_to_tc(ff.tc_to_frames(info.timecode, info.fps) + i, info.fps))
        self.mon.set_images([fr], (info.w, info.h))
        self._overlay_from_track(i)

    def _play_end(self):
        self.player = None
        self._unplay_btn()
        self.seek(self.f, force=True)

    def stop_play(self):
        if self.player:
            self.player.stop()
            self.player = None
            self._unplay_btn()

    def _unplay_btn(self):
        self.b_play.setProperty("on", False)
        self.b_play.style().polish(self.b_play)

    # ------------------------------------------------------------ 피부색 찍기
    def on_pick(self, x, y):
        if not self.frame_cache:
            return
        ref = fx.pick_skin(self.frame_cache[2], x, y)
        self.state.set_param("skin_ref", ref, undo=True)
        self.panel.stop_pick()
        self.flash(f"피부색: Cb {ref[0]:+.3f} · Cr {ref[1]:+.3f}")

    # ------------------------------------------------------------ 소스 추가·삭제
    def add_dialog(self):
        start = self.state.settings.d.get("last_folder", "")
        files, _ = QFileDialog.getOpenFileNames(self, "영상 추가", start, VID_FILTER)
        if files:
            self.state.settings.d["last_folder"] = str(Path(files[0]).parent)
            self.state.settings.save()
            self.state.add_files(files)

    def remove_sources(self):
        rows = self.sources.selected() or ([self.state.cur] if self.state.cur >= 0 else [])
        if rows:
            self.state.remove(rows)

    # ------------------------------------------------------------ 렌더
    def render_settings(self):
        d = RenderSettings(self, self.state.settings.render)
        if d.exec():
            self.state.settings.render.update(d.value())
            self.state.settings.save()
            self.state._tracks.clear()
            self.on_params()

    def _out_dir(self):
        rs = self.state.settings.render
        if rs.get("out_dir") and Path(rs["out_dir"]).is_dir():
            return rs["out_dir"]
        d = QFileDialog.getExistingDirectory(self, "저장폴더", self.state.settings.d.get("last_folder", ""))
        if not d:
            return None
        rs["out_dir"] = d
        self.state.settings.save()
        return d

    def render_selected(self):
        rows = self.sources.selected() or ([self.state.cur] if self.state.cur >= 0 else [])
        if not rows:
            return
        out = self._out_dir()
        if not out:
            return
        for r in rows:
            s = self.state.project.sources[r]
            it = RenderItem(s, self.state.settings.render, out)
            self.rpanel.add(it)
            self._start_render(it)

    def rerender(self, rows):
        for r in rows:
            it = self.rpanel.items[r]
            if it.state != "진행":
                it.state, it.p, it.err = "대기", 0.0, ""
                self._start_render(it)
        self.rpanel.refresh()

    def _start_render(self, it: RenderItem):
        def fn(prog, cancel):
            return do_render(it.src, it.rs, it.out_dir, prog, cancel)
        j = jobs.Job(fn, with_progress=True)
        it.job = j

        def started(v):
            it.state = "진행"
            it.p = v
            self.rpanel.refresh()
        j.signals.progress.connect(started)
        j.signals.done.connect(lambda outs: self._render_done(it, outs))
        j.signals.error.connect(lambda e: self._render_err(it, e))
        jobs.submit(jobs.RENDER_POOL, j)
        self.rpanel.refresh()

    def _render_done(self, it, outs):
        it.state, it.p, it.outs = "완료", 1.0, outs
        self.rpanel.refresh()
        self.flash(f"렌더 완료: {it.src.name}")

    def _render_err(self, it, e):
        it.state = "취소" if e == "취소됨" else "오류"
        it.err = e
        self.rpanel.refresh()
        if it.state == "오류":
            self.flash(f"렌더 오류: {it.src.name} — {e[:200]}")

    # ------------------------------------------------------------ 프로젝트
    def undo(self):
        if not self.state.undo():
            self.flash("더 이상 취소할 작업이 없습니다")

    def _confirm_discard(self) -> bool:
        if not self.state.dirty:
            return True
        r = QMessageBox.question(self, "저장", "변경 내용을 저장할까요?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self):
        if self._confirm_discard():
            self.state.set_project(Project())
            self._title()

    def open_project(self):
        if not self._confirm_discard():
            return
        f, _ = QFileDialog.getOpenFileName(self, "불러오기", self.state.settings.d.get("last_folder", ""), PROJ_FILTER)
        if f:
            self._load(f)

    def _load(self, f):
        try:
            p = Project.load(f)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "불러오기 실패", str(e))
            return
        missing = [s.name for s in p.sources if not Path(s.path).exists()]
        self.state.set_project(p)
        self._title()
        if missing:
            QMessageBox.warning(self, "원본 없음", "원본 파일을 찾을 수 없습니다:\n" + "\n".join(missing))

    def save_project(self, save_as=False) -> bool:
        p = self.state.project
        f = p.file
        if save_as or not f:
            start = str(Path(p.sources[0].path).parent / "프로젝트.mosco.json") if p.sources else "프로젝트.mosco.json"
            f, _ = QFileDialog.getSaveFileName(self, "저장하기", start, PROJ_FILTER)
            if not f:
                return False
        p.save(f)
        self.state.dirty = False
        self._title()
        self.flash(f"저장됨: {f}")
        return True

    def _do_autosave(self):
        if self.state.dirty and self.state.project.sources:
            try:
                (paths.cache_dir() / "autosave.mosco.json").write_text(
                    json.dumps(self.state.project.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, e):
        if self._confirm_discard():
            self.stop_play()
            self.state.cancel_all()
            for it in self.rpanel.items:
                if it.job is not None:
                    it.job.cancel.set()
            e.accept()
        else:
            e.ignore()

    # ------------------------------------------------------------ 드래그 앤 드롭
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        vids = []
        for f in files:
            pth = Path(f)
            if pth.is_dir():
                vids += [str(x) for x in sorted(pth.iterdir()) if x.suffix.lower() in VIDEO_EXT]
            elif pth.suffix.lower() in VIDEO_EXT:
                vids.append(f)
        projs = [f for f in files if f.endswith(".json")]
        if vids:
            self.state.add_files(vids)
        elif projs and self._confirm_discard():
            self._load(projs[0])
