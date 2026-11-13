"""메인 창: 상단(새 프로젝트·저장하기·불러오기·실행취소) + 3열(보정 | 모니터 | 소스·렌더)."""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QScrollArea, QSplitter, QToolBar, QVBoxLayout, QWidget)

from . import __version__, analyze, decoder, edits, ff, fx, jobs, paths
from .project import Project, out_stem
from .render import render as do_render
from .state import AppState
from .ui_monitor import EllipseLanes, Monitor, PlayheadOverlay, TimeBar, TimeRuler
from .ui_panel import ControlPanel
from .ui_side import VIDEO_EXT, RenderItem, RenderPanel, RenderSettings, SourceList, stat_line
from .ui_widgets import BTN, BTN_MAIN, QSS, arrow_icon, btn


def confirm_save(parent, text="변경 내용을 저장할까요?") -> str:
    """닫기/새 프로젝트 전 저장 확인 — MOSCO 다크 테마 통일(프레임리스). 반환 save|discard|cancel."""
    dlg = QDialog(parent)
    dlg.setWindowFlag(Qt.FramelessWindowHint)
    dlg.setModal(True)
    dlg.setStyleSheet("QDialog{background:#262626;border:1px solid #555;border-radius:6px}"
                      "QLabel{color:#e6e6e6;font-size:13px}")
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(24, 22, 24, 18)
    lay.setSpacing(20)
    lay.addWidget(QLabel(text))
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addStretch(1)
    res = {"v": "cancel"}

    def pick(v):
        res["v"] = v
        dlg.accept()
    row.addWidget(btn("취소", w=72, on_click=lambda: pick("cancel")))
    row.addWidget(btn("저장 안 함", w=92, on_click=lambda: pick("discard")))
    row.addWidget(btn("저장", w=80, red=True, on_click=lambda: pick("save")))
    lay.addLayout(row)
    dlg.exec()
    return res["v"]

PROJ_FILTER = "MOSCO 프로젝트 (*.mosco.json *.json)"
VID_FILTER = "영상 (" + " ".join("*" + e for e in sorted(VIDEO_EXT)) + ")"


def find_videos(folder):
    """폴더 안(하위 폴더 포함) 영상 파일. 숨김·MOSCO 출력 파일은 뺌, 이름순."""
    out = []
    for x in sorted(Path(folder).rglob("*"), key=lambda p: str(p).lower()):
        if x.is_file() and x.suffix.lower() in VIDEO_EXT and not x.name.startswith(".") \
                and not any(t in x.stem for t in ("_MOSCO", "_BLUR4444", "_CHECK")):
            out.append(str(x))
    return out


def to8(a16):
    return (a16 >> 8).astype(np.uint8)


def gray8(m):
    return np.clip(m * 255 + 0.5, 0, 255).astype(np.uint8)


# ------------------------------------------------------------ 미리보기 계산(작업 스레드)
def temp_items(src, rgb8, scale=1.0, mode=None):
    """분석 전: 이 프레임만 빠르게 검출(가볍게). 좌표는 rgb8(=보기 화질) 기준.
    블러는 기본 얼굴 검출만(작은 얼굴·몸·타일 제외) — 정확한 건 [분석]·렌더 때.
    min_size 는 원본 px 기준이므로 scale 을 곱해 비교. mode = 종류(기본 활성 모드, 동시 블러용)."""
    mode = mode or src.mode
    p = src.params_for(mode)
    out = []
    if mode == "beauty":
        pts, _ = analyze.beauty_frame(rgb8, None)
        if pts is not None:
            out.append(edits.Item(("t", 1), 1, analyze.head_ellipse(pts, p["size"], p["neck"]), pts))
    elif mode == "sign":
        from . import sign
        for k, row in enumerate(sign.sign_frame(rgb8), 1):
            if row[4] >= sign.score_thr(p["sensitivity"]) and row[-1] >= p["min_size"] * scale:
                q = sign.grow(row[-9:-1].reshape(4, 2), p["margin"] / 100 * row[-1])
                out.append(edits.Item(("t", k), k, sign.quad_ell(q), q))
    else:
        from . import models
        thr = analyze.score_thr(p["sensitivity"])
        k = 0
        for d in models.detector().detect(rgb8, max(0.3, thr), tiles=False, min_size=p["min_size"] * scale):
            x0, y0, x1, y1 = d.box
            if d.score >= thr:
                k += 1
                out.append(edits.Item(("t", k), k, analyze.face_to_head((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0,
                                                                         y1 - y0, p["margin"])))
    return out


def _scale_items(items, scale):
    """트랙(원본 좌표) 아이템을 보기 화질 좌표로 축소."""
    if scale == 1.0:
        return items
    out = []
    for it in items:
        cx, cy, w, h, ang = it.ell
        ni = edits.Item(it.key, it.tid, (cx * scale, cy * scale, w * scale, h * scale, ang),
                        it.pts * scale if it.pts is not None else None, it.presence, dict(it.ov), it.manual,
                        it.gid, it.keyed, it.minus)
        out.append(ni)
    return out


def entries(ells, p=None, mode=None):
    """[(item, 실제 적용 타원)] → 모니터 표시용. 간판은 4꼭짓점(quad)도. mode = 이 타원이 속한 종류(동시 블러 라우팅용)."""
    out = []
    for it, ell in ells:
        quad = None
        if fx._is_quad(it):
            q = fx.item_quad(it, p) if p is not None else np.asarray(it.pts, np.float32).reshape(4, 2)
            quad = [(float(x), float(y)) for x, y in q]
        lab = str(it.tid) if it.tid is not None else ("+" if it.manual else None)
        if it.minus:
            lab = "−" + (lab if lab and lab != "+" else "")
        out.append({"key": it.key, "label": lab, "ell": tuple(float(v) for v in ell), "manual": it.manual,
                    "keyed": it.keyed, "minus": it.minus, "quad": quad, "mode": mode, "sus": it.sus})
    return out


def item_entries(items, p, mode=None):
    from .sign import quad_ell
    return entries([(it, quad_ell(fx.item_quad(it, p)) if fx._is_quad(it) else fx._item_ell(it, p)) for it in items],
                   p, mode)


def compute_preview(src, frame16, plan, i, scale=1.0, keys=True):
    """한 프레임: 원본·보정·타원 (+ keys 면 키 3종). plan = [(resolver, mode), ...] — 동시 블러는 종류별로 이어서 블러.
    keys=False(키 보기 꺼짐)면 뷰티 키(피부·타원·최종)를 계산하지 않음 → 슬라이더 반응이 빨라짐."""
    H, W = frame16.shape[:2]
    out = {"orig": to8(frame16)}
    if src.mode == "beauty":
        p = src.beauty
        items = _scale_items(plan[0][0].items(i), scale)
        r = fx.beauty(frame16, items, p, i, want_keys=keys)
        out["fx"] = to8(r.out)
        out["ells"] = entries(r.ells)
        out["ref"] = r.ref                           # 이 프레임에서 쓴 기준 피부색(자동이면 계산값) — 칩 표시용
        focus = next((e for it, e in r.ells if not it.manual), r.ells[0][1] if r.ells else None)
        out["focus"] = focus
        if keys:
            out["final"] = gray8(r.region)
            out["alpha"] = gray8(r.alpha)
            pts = next((it.pts for it in items if it.pts is not None), None)
            fw = float(np.ptp(pts[:, 0])) if pts is not None else 300.0
            out["skin"] = gray8(fx.skin_key_full(frame16, p, r.ref, fw))
        out["faces"] = len(items)
    else:
        o, a_all, all_entries, nfac = frame16, None, [], 0
        for res, mode in plan:                          # 얼굴 → 간판 순으로 이어서 블러
            items = _scale_items(res.items(i), scale)
            o, a, ells = fx.blur(o, items, src.params_for(mode), True)
            all_entries += entries(ells, src.params_for(mode), mode)
            nfac += len(items)
            if a is not None:
                a_all = a if a_all is None else np.maximum(a_all, a)
        out["fx"] = to8(o)
        out["ells"] = all_entries
        out["focus"] = None
        a_all = a_all if a_all is not None else np.zeros((H, W), np.float32)
        out["alpha"] = out["final"] = out["skin"] = gray8(a_all)
        out["faces"] = nfac
    return out


class Player:
    """재생: ffmpeg 파이프(보기 화질) → 큐 → 타이머로 표시. 보정 없이 원본만, 소리 없음."""

    def __init__(self, info, start, on_frame, on_end, size, loop=True):
        self.info = info
        self.i = start
        self.start0 = start
        self.size = size
        self.loop = loop
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
        start = self.start0
        while not self.stop_ev.is_set():
            r = ff.Reader(self.info, start, 8, size=self.size)
            try:
                while not self.stop_ev.is_set():
                    fr = r.read()
                    if fr is None:
                        break
                    while not self.stop_ev.is_set():
                        try:
                            self.q.put(fr, timeout=0.1)
                            break
                        except queue.Full:
                            pass
            finally:
                r.close()
            if self.stop_ev.is_set():
                break
            if self.loop:
                self.q.put("LOOP")       # 끝 → 처음부터 다시(루프)
                start = 0
            else:
                self.q.put(None)
                break

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
            if isinstance(x, str) and x == "LOOP":     # 처음부터 다시 — 타이밍·프레임 리셋
                self.i, self.f0, self.t0 = 0, 0, now
                want = 0
                continue
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
        self.frame_cache = None        # (path, i, scale, frame16)
        self.result = None             # compute_preview 결과
        self.pos = {}                  # 소스별 마지막 위치

        tb = QToolBar("main")
        tb.setMovable(False)
        tb.setStyleSheet("QToolBar{spacing:6px;padding:3px;background:#232323;border-bottom:1px solid #333}"
                         "QToolButton{padding:4px 12px}")        # 맨 위 줄도 입체감(DTME)
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
            a.setToolTip(QKeySequence(key).toString() + (" · 다시 실행 Ctrl+Shift+Z" if fn == self.undo else ""))
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

        mid = QVBoxLayout()
        mid.setSpacing(4)
        # v0.7.70 안내·진행 글자 줄을 미리보기 위로(예전: 맨 아래라 잘 안 보였음)
        self.status = QLabel("")
        self.status.setFixedHeight(22)
        self.status.setStyleSheet("background:#1c1c1c;border-radius:3px;color:#7fd18b;padding-left:8px;font-size:12px")
        mid.addWidget(self.status)
        self.mon = Monitor()
        self.bar = TimeBar()
        self.lanes = EllipseLanes()
        self.lanes_box = QScrollArea()           # 레인 많으면(8개 초과) 스크롤 — 아래 레인도 작업 가능
        self.lanes_box.setWidgetResizable(True)
        self.lanes_box.setFrameShape(QScrollArea.NoFrame)
        self.lanes_box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lanes_box.setWidget(self.lanes)
        self.lanes_box.setMinimumHeight(EllipseLanes.PAD * 2 + 2 * EllipseLanes.LANE_H)   # 최소 2레인
        # v0.7.91 최대 높이 제한 없음: 모니터를 줄이면 늘어난 만큼 레이어 칸이 차지(다 들어가면 스크롤바 없음).
        # 처음 자동 높이(_autosize_lanes)만 30줄까지.
        self.lanes_box.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lanes_box.setStyleSheet("QScrollArea{background:#1a1a1a}")
        self.ruler = TimeRuler()
        # 모니터 ↕ (바+레인+눈금) 사이 드래그 — 레인 창을 높게 볼 수 있음(여기서 키프레임·타원 수정 후 AE)
        _bot = QWidget()
        _botl = QVBoxLayout(_bot)
        _botl.setContentsMargins(0, 0, 0, 0)
        _botl.setSpacing(4)
        _botl.addWidget(self.lanes_box, 1)      # 레이어(타원·사각형)를 소스 바 위로
        # v0.7.76 레인이 많아 세로 스크롤바가 생기면 레인 폭만 그만큼 줄어, 아래 소스 바·눈금과 프레임 위치가 어긋났음
        # → 스크롤바가 보이는 동안 소스 바·눈금 오른쪽에 같은 폭을 비워 x 를 맞춤
        self._tl_pad = QVBoxLayout()
        self._tl_pad.setContentsMargins(0, 0, 0, 0)
        self._tl_pad.setSpacing(4)
        self._tl_pad.addWidget(self.bar)        # 소스 바(재생 위치)
        self._tl_pad.addWidget(self.ruler)
        _botl.addLayout(self._tl_pad)
        self.lanes_box.verticalScrollBar().installEventFilter(self)
        # v0.7.90 빨간 재생선 = 레인·소스 바·눈금을 끊김 없이 한 줄(덮개). 틈·레인 아래 빈 곳에서도 잡아 끌기
        self._bot = _bot
        self.playhead = PlayheadOverlay(_bot, self.lanes, self.bar)
        self._ph_drag = False
        for o in (_bot, self.lanes_box.viewport(), self.lanes):
            o.installEventFilter(self)
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.addWidget(self.mon)
        self.vsplit.addWidget(_bot)
        self.vsplit.setStretchFactor(0, 1)       # 기본은 모니터가 큼
        self._vsplit_user = False                # 사용자가 직접 끌면 자동 높이 멈춤
        self.vsplit.splitterMoved.connect(lambda *_: setattr(self, "_vsplit_user", True))
        mid.addWidget(self.vsplit, 1)
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
                self.b_play.setMinimumWidth(64)          # v0.7.59 창이 좁으면 버튼이 겹치지 않고 줄어듦
                self.b_play.setMaximumWidth(BTN_MAIN[0])
                tr.addWidget(self.b_play, 2)
                continue
            sb = btn(t, tip, on_click=fn)
            sb.setMinimumWidth(40)
            sb.setMaximumWidth(BTN[0])
            tr.addWidget(sb, 2)
        self.b_play.clicked.connect(self.toggle_play)
        self.tc = QLabel("00:00:00;00")
        self.tc.setStyleSheet("background:#000;color:#e8e8e8;font:300 14px Consolas,monospace;padding:4px 10px;"
                              "border-radius:2px;margin-left:10px")
        from PySide6.QtGui import QFont, QFontMetrics
        _f = QFont("Consolas"); _f.setStyleHint(QFont.Monospace); _f.setPixelSize(14)
        self.tc.setMinimumWidth(QFontMetrics(_f).horizontalAdvance("00:00:00;00") + 34)   # 글자 + 여백 20 + 왼쪽 10 → 잘리지 않게(DTME 처럼 다 보임)
        tr.addWidget(self.tc)
        tr.addStretch(1)
        self.b_analyze = self.panel.b_analyze       # 분석 버튼은 패널 하단(타원 편집 아래)으로 이동 — 크고 파랗게
        self.panel.analyze_clicked.connect(self.analyze_current)
        self.state.set_view_scale(1.0)              # 미리보기는 항상 100%(화질 선택창 없앰)
        self.b_toqueue = btn("", "렌더 목록으로 보내기 (현재/선택 소스). 실제 렌더는 오른쪽 [렌더] 버튼",
                             size=BTN_MAIN, w=88, red=True, on_click=self.queue_to_render)
        self.b_toqueue.setIcon(arrow_icon())
        self.b_toqueue.setIconSize(QSize(26, 14))
        tr.addWidget(self.b_toqueue)
        mid.addLayout(tr)
        midw = QWidget()
        midw.setLayout(mid)
        # v0.7.59 최소 폭 = 재생 줄이 겹치지 않는 폭(예전 360 고정 → 창을 줄이면 버튼이 겹쳐 엉망)
        midw.setMinimumWidth(max(360, tr.minimumSize().width() + 8))

        right = QWidget()
        right.setMinimumWidth(340)             # v0.7.59 [▼][⚙][AE][렌더] 줄이 눌리지 않는 폭(예전 260 → 줄이면 버튼 크기가 달라짐)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(3)
        arow = QHBoxLayout()
        arow.setSpacing(6)
        arow.addStretch(1)
        b1 = btn("파일 추가", "파일 또는 폴더(하위 폴더 포함)", size=BTN_MAIN, w=76)
        from PySide6.QtWidgets import QMenu
        m = QMenu(b1)
        m.addAction("파일…", self.add_dialog)
        m.addAction("폴더…", self.add_folder)
        b1.setMenu(m)
        b1.setStyleSheet("QPushButton::menu-indicator{width:0px}")
        self.b_add_src = b1
        self.b_auto = btn("AUTO", "목록의 클립을 장면 인식해 한 번에 보정 (AUTO)", size=BTN_MAIN, red=True, w=88,
                          on_click=self.open_auto)
        b2 = btn("삭제", "선택한 소스 삭제 (Del)", size=BTN_MAIN, w=76, on_click=self.remove_sources)
        arow.addWidget(b1)
        arow.addWidget(self.b_auto)
        arow.addWidget(b2)
        arow.addStretch(1)
        rv.addLayout(arow)
        self.auto_bar = QWidget()
        self.auto_bar.setFixedHeight(3)
        self.auto_bar.setStyleSheet("background:#2a2a2a;border-radius:2px")
        self.auto_bar.setVisible(False)
        rv.addWidget(self.auto_bar)
        self.sources = SourceList(self.state)
        self.sources.add_dialog = self.add_dialog
        self.sources.queue_requested.connect(self.queue_to_render)   # 소스 우클릭 → 대기목록
        self.sources.relink_requested.connect(self.relink_missing)   # 소스 우클릭 → 원본 다시 연결
        self.state.multi_sel = self.sources.selected                 # 여러 개 선택 → 값 하나를 한꺼번에(v0.7.64)
        self.sources.itemSelectionChanged.connect(self.panel.refresh)
        rv.addWidget(self.sources, 1)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#333")
        rv.addWidget(line)
        self.rpanel = RenderPanel()
        self.rpanel.setFixedHeight(300)
        self.rpanel.settings_clicked.connect(self.render_settings)
        self.rpanel.render_clicked.connect(self.render_all)
        self.rpanel.ae_clicked.connect(self.export_ae)
        self.rpanel.queue_clicked.connect(self.queue_to_render)       # ▼ 선택 소스를 대기목록으로
        self.rpanel.on_rerender = self.rerender
        self.rpanel.analysis_p = self._render_analysis_p
        self.state.analysis_changed.connect(lambda *_: self.rpanel.list.viewport().update())
        rv.addWidget(self.rpanel)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)       # 핸들은 Fusion 기본(그립) — DTME와 동일
        for wdg in (self.panel, midw, right):
            split.addWidget(wdg)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)       # 모니터가 남는 공간을 차지
        split.setStretchFactor(2, 0)
        split.setSizes([350, 1250, 300])    # 1920 기준: 보정 350 · 모니터 · 소스/렌더 300
        h.addWidget(split)
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
        self.bar.del_key.connect(lambda k: self._del_key_button())   # 시간바도 고른 오토 키 처리(v0.7.81)
        self.lanes.select.connect(self.on_select)
        self.lanes.segments_changed.connect(self._on_segments)
        self.lanes.seek.connect(self.seek)               # 레인 재생선 드래그 → 프레임 이동
        self.lanes.del_key.connect(self._del)            # 레인: 고른 키 → 그 키, 아니면 고른 레이어 삭제
        self.ruler.seek.connect(self.seek)
        self.panel.tool.connect(self.on_tool)
        self.mon.add_at.connect(self.on_add)
        self.mon.drawn.connect(self.on_draw)
        self.mon.delete_ell.connect(self.on_delete)
        self.mon.selected.connect(self.on_select)
        self.mon.edited.connect(self.on_edited)
        self.mon.merge_with.connect(self.on_merge)
        self.panel.del_key.connect(self._del_key_button)             # [키 삭제] 버튼: 오토·수동 키 다 처리(v0.7.81)
        self.panel.off.connect(lambda how: self.state.off(how, self.f, self._n()))
        self.panel.minus.connect(lambda v: self.state.set_minus(v, self.f))
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
        sc(QKeySequence(Qt.CTRL | Qt.Key_R), self.render_all)
        sc(Qt.Key_Delete, self._del)
        sc(QKeySequence(Qt.SHIFT | Qt.Key_Delete), self._delete_keyframe)
        sc(Qt.Key_A, lambda: self.panel.b_add.toggle())
        sc(Qt.Key_X, self._toggle_minus)
        sc(Qt.Key_Escape, self._esc)
        sc(QKeySequence(Qt.CTRL | Qt.SHIFT | Qt.Key_Z), self.redo)
        sc(QKeySequence(Qt.CTRL | Qt.Key_Y), self.redo)

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
        sc = self._vscale()
        size = info.h * (0.3 if s.mode == "beauty" else 0.09)       # 원본 좌표
        self.state.add_ellipse(self.f, x / sc, y / sc, size)        # 클릭은 보기 좌표 → 원본
        # 연속 그리기: 도구를 끄지 않는다(버튼을 다시 눌러야 끝)

    def on_draw(self, cx, cy, w, h):
        if not self.state.source():
            return
        sc = self._vscale()
        self.state.add_ellipse_box(self.f, cx / sc, cy / sc, w / sc, h / sc)
        # 연속 그리기: 그린 뒤에도 계속 그릴 수 있게 도구 유지

    def on_delete(self, key, mode=None):
        if mode in ("blur", "sign"):
            self.state.set_active(mode)      # 동시 블러: 그 타원이 속한 종류로 전환해 삭제
        self.state.delete_ellipse(key, self.f)
        # 삭제 모드 유지(여러 개 연속 삭제)

    def on_edited(self, key, ell, mode=None):
        if mode in ("blur", "sign"):
            self.state.set_active(mode)
        self.state.edit_ellipse(key, self.f, self._to_full(ell))

    def on_merge(self, key_b, mode=None):
        if mode in ("blur", "sign"):
            self.state.set_active(mode)
        if self.state.merge(self.state.sel, self._merge_anchor, key_b, self.f):
            self.flash("같은 사람으로 합쳤습니다")
        self.panel.stop_tool()

    def on_select(self, key, mode=None):
        if mode and mode in ("blur", "sign"):
            self.state.set_active(mode)      # 다른 종류 레인 클릭 = 그 종류로 전환(편집 가능)
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
        smode = s.mode if (sel is not None and s.mode in ("blur", "sign")) else None   # v0.7.89 종류+번호로 선택
        self.mon.set_selected(sel, smode)
        self.bar.set_keys([])                    # v0.7.90 소스 바에는 키 표시 안 함(레인에서만 — 숨은 홀드까지 보여 어긋났음)
        self.lanes.sel, self.lanes._sel_mode = sel, smode
        self.lanes.update()
        self.panel.show_selection(self.state.ov_of(sel, self.f) if sel else None, s.params(),
                                  self.state.minus_of(sel, self.f) if sel else False)

    def _toggle_minus(self):
        s = self.state.source()
        if s is None or s.mode not in ("blur", "sign") or self.state.sel is None:
            return
        self.state.set_minus(not self.state.minus_of(self.state.sel, self.f), self.f)

    def _esc(self):
        self.panel.stop_pick()
        if self.state.sel is not None:
            self.state.sel = None
            self._sel_refresh()

    def _toggle_orig(self):
        v = self.panel.view_mode()
        self.panel.view.set("orig" if v != "orig" else "fx")
        self.show_result()

    def _del(self):
        """Del: 목록 포커스면 목록 항목 삭제, 아니면 고른 키(오토·수동)·없으면 선택한 '타원'을 삭제."""
        if self.rpanel.list.hasFocus():
            self.rpanel.remove_rows(sorted(self.rpanel.list.row(i) for i in self.rpanel.list.selectedItems()))
        elif self.sources.hasFocus():
            self.remove_sources()
        elif self.state.sel is not None:
            if self._del_selected_key():                 # 레인에서 클릭해 고른 키(오토·수동) → 그 키만(v0.7.73)
                return
            more = self.lanes.multi_keys()               # Ctrl·Shift 로 더 고른 레이어까지 한 번에(v0.7.76)
            self.lanes.multi = set()
            n = self.state.delete_ellipses([self.state.sel] + more, self.f)
            if n > 1:
                self.flash(f"레이어 {n}개 삭제 (Ctrl+Z 되돌리기)")

    def _del_selected_key(self) -> bool:
        """고른 키를 삭제. 오토 키(흰 ◆) = 펴기, 수동 키(노랑) = 그 키. → 고른 키가 있어 처리했으면 True.
        [키 삭제] 버튼·시간바·레인 Del 공용(v0.7.81: 예전엔 버튼이 delete_key 만 불러 오토 키는 안 빠졌음)."""
        kf = self.lanes.sel_kf
        if kf is None or kf[0] != self.state.sel:
            return False
        self.lanes.sel_kf = None
        if kf[3] == "user":
            self.state.delete_key(kf[2]) or self.flash("이 프레임에 키가 없습니다")
        else:
            why = self.state.delete_auto_key(kf[0], kf[2])
            self.flash(why or "오토 키 삭제 — 옆 키 값으로 이었습니다 (구간 처음·끝 = 옆 키 값 고정 · Ctrl+Z 되돌리기)")
        return True

    def _del_key_button(self):
        """[키 삭제] 버튼·시간바: 고른 키(오토·수동)가 있으면 그 키, 없으면 현재 프레임 수동 키."""
        if self.state.sel is not None and self._del_selected_key():
            return
        if self.state.sel is None or not self.state.delete_key(self.f):
            self.flash("이 프레임에 키가 없습니다")

    def _delete_keyframe(self):
        """Shift+Del: 고른 키(오토·수동)·없으면 현재 프레임 키 삭제(타원 삭제와 분리)."""
        self._del_key_button()

    def _sec(self):
        info = self._info()
        return int(round(info.fps_f)) if info else 30

    # ------------------------------------------------------------ 상태 표시
    def _title(self):
        f = Path(self.state.project.file).name if self.state.project.file else "새 프로젝트"
        r = f"렌더 {self._rtext} — " if getattr(self, "_rtext", "") else ""       # 렌더 중: 제목에도 전체 진행
        self.setWindowTitle(f"MOSCO {__version__} — {r}{f}{' *' if self.state.dirty else ''}")

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
            cur = self.state.source()
            t += f" · {'간판' if cur and cur.mode == 'sign' else '얼굴'} {self.result.get('faces', 0)}"
        s = self.state.source()
        tr = self.state.track(s) if s else None
        if s and s.mode in ("blur", "sign") and tr is not None:
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
        if s is None:                               # v0.7.79 새 프로젝트 등으로 소스 0개 → 타임라인(레이어·바·눈금)도 비움
            self.state.sel = None
            self.mon.clear()
            self.bar.set_data(0)
            self.lanes.set_data([], 0, 0, None)
            self.ruler.set_data(0, 30.0)
            self._sel_refresh()
            self.update_status()
            return
        self.mon.clear(loading=True)
        self.mon.reset_view()
        self.state.sel = None
        self.panel.stop_tool()
        self._sel_refresh()
        self._refresh_analyze_btn()
        info = self.state.info(s.path)
        self._bar()
        if info is None:
            self.update_status()
            return
        self.seek(self.pos.get(s.path, 0), force=True)

    def analyze_current(self):
        s = self.state.source()
        if s is None:
            return
        a = self.state.analysis(s)
        if a is not None and a.get("state") == "done":       # v0.7.83 이미 분석됨 → 확인 후 다시 분석
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, "다시 분석", "이미 분석된 클립입니다. 다시 분석하시겠습니까?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.state.reanalyze(s)
        else:
            self.state.analyze_now(s)
        self._refresh_analyze_btn()
        self.flash("분석을 시작합니다")

    def _refresh_analyze_btn(self):
        s = self.state.source()
        a = self.state.analysis(s) if s else None
        if a is None:
            self.b_analyze.setText("분석")
            self.b_analyze.setEnabled(s is not None)
        elif a["state"] == "run":
            self.b_analyze.setText(f"{int(a['p'] * 100)}%")
            self.b_analyze.setEnabled(False)
        elif a["state"] == "done":
            self.b_analyze.setText("분석됨")
            self.b_analyze.setEnabled(True)                  # v0.7.83 다시 눌러 재분석(확인창)
        else:
            self.b_analyze.setText("분석")
            self.b_analyze.setEnabled(True)

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
                pres = tr.presence
                any_face = pres.max(axis=1) if pres.ndim == 2 else pres     # 슬롯 축 합치기(다중 얼굴)
                cov = (any_face > 0).astype(np.int8)
            else:
                cov = np.zeros(tr.n, np.int8)
                for i, fs in enumerate(tr.frames):
                    if fs:
                        cov[i] = 1 if all(f[-1] for f in fs) else 2      # 마지막 = 관측여부(블러·간판 공통)
        elif a:
            prog = a["p"]
        self.bar.set_data(n, cov, prog, self.state.errors.get(s.path, ""))
        self.bar.set_cur(self.f)
        self.ruler.set_data(n, float(info.fps))
        self.ruler.set_cur(self.f)
        self._refresh_lanes()

    def _refresh_lanes(self):
        s = self.state.source()
        spans = self.state.ellipse_spans(s) if s else []    # 동시 블러면 두 종류 레인 포함
        n = spans[0]["n"] if spans else 0
        self.lanes.set_data(spans, n, self.f, self.state.sel,
                            s.mode if (s is not None and s.mode in ("blur", "sign")) else None)
        self._autosize_lanes(self.lanes.rows())
        self.playhead.update()

    def _autosize_lanes(self, count):
        """기본은 레이어가 다 보이게(최대 30레인), 넘으면 스크롤. 사용자가 스플리터를 직접 끌면 멈춤."""
        if self._vsplit_user or count <= 0:
            return
        from .ui_monitor import EllipseLanes as _EL
        want = _EL.PAD * 2 + min(count, 30) * _EL.LANE_H + self.bar.height() + self.ruler.height() + 10
        total = sum(self.vsplit.sizes()) or self.vsplit.height()
        want = min(want, max(120, total - 240))          # 모니터 최소 240 확보
        self.vsplit.blockSignals(True)                   # 자동 조정은 'user' 플래그 안 켜지게
        self.vsplit.setSizes([max(120, total - want), want])
        self.vsplit.blockSignals(False)

    def _on_segments(self, key, segments):
        self.state.set_spans(key, segments)      # 구간별 양끝 편집·틈 메우기
        self._bar()
        self._sel_refresh()

    def on_analysis(self, path):
        s = self.state.source()
        if s and s.path == path:
            self._bar()
            self._refresh_analyze_btn()
            self.update_status()

    def on_params(self):
        s = self.state.source()
        if s is not None and (s.path, s.mode) in self.state.an and self.state.analysis(s) is None:
            self.state.ensure_analysis(s)      # 블러 [최소 크기]를 분석 하한보다 낮춤 → 다시 분석
        self._bar()
        self._sel_refresh()
        self._overlay_from_track(self.f)
        self._refresh_analyze_btn()            # v0.7.83 모드 전환(블러↔뷰티 등) 시 [분석] 버튼 상태 갱신
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
        self.lanes.set_cur(i)
        self.ruler.set_cur(i)
        self.playhead.update()
        if self.state.sel is not None:
            self.panel.show_selection(self.state.ov_of(self.state.sel, i), self.state.source().params(),
                                      self.state.minus_of(self.state.sel, i))
        self.tc.setText(ff.frames_to_tc(ff.tc_to_frames(info.timecode, info.fps) + i, info.fps))
        self._quick(i)
        self._full_t.start()

    def step(self, d):
        self.seek(self.f + d)

    # ------------------------------------------------------------ 보기 화질(좌표계)
    def _vscale(self):
        return self.state.view_scale

    def _vsize(self, info):
        sc = self._vscale()
        return (max(16, round(info.w * sc)), max(16, round(info.h * sc)))

    def _play_size(self, info):
        """재생 전용 디코딩 크기: 긴 변 640 상한(프리뷰보다 크지 않게). 저해상도로 열어 즉시·부드럽게 재생.
        표시 논리 크기는 _vsize 그대로라 오버레이(타원·사각형)는 정확히 정렬되고, 원본 프레임만 부드럽게 확대됨."""
        vw, vh = self._vsize(info)
        longside = max(vw, vh)
        cap = 640
        if longside <= cap:
            return (vw, vh)
        k = cap / longside
        return (max(16, round(vw * k)), max(16, round(vh * k)))

    def _to_full(self, ell):
        """모니터(보기 좌표)에서 받은 타원 → 원본 좌표(편집 저장·렌더용)."""
        sc = self._vscale()
        if len(ell) >= 8:                       # 간판 4꼭짓점
            return tuple(v / sc for v in ell[:8])
        cx, cy, w, h = ell[:4]
        return (cx / sc, cy / sc, w / sc, h / sc, ell[4] if len(ell) > 4 else 0.0)

    def _quick(self, i):
        """즉시: 작은 원본 프레임 + 타원(트랙이 있으면)."""
        info = self._info()
        self.gen += 1
        g = self.gen
        path = info.path
        sz = self._vsize(info)

        def fn():
            if g != self.gen:               # 차례가 왔을 때 이미 다른 프레임으로 넘어감 → 디코딩하지 않고 버림
                return None
            return decoder.grab(info, i, 8, sz)
        j = jobs.Job(fn)
        j.signals.done.connect(lambda a: self._quick_done(g, path, i, a))
        jobs.submit(jobs.PREVIEW_POOL, j, 5)

    def _quick_done(self, g, path, i, a):
        if a is None or g != self.gen or self.result is not None and self.result.get("i") == i:
            return
        info = self._info()
        if not info or info.path != path:
            return
        self.mon.set_images([a], self._vsize(info))
        self._overlay_from_track(i)

    def _overlay_from_track(self, i):
        s = self.state.source()
        ells = []
        if s is not None:
            for res, _m in self.state.blur_resolvers(s):
                if res.track is not None:
                    its = res.items(i)
                    if its:
                        ells += item_entries(_scale_items(its, self._vscale()), s.params_for(_m), _m)
        self.mon.quad_mode = s is not None and s.mode == "sign"
        self.mon.set_overlay(ells, self.panel.t_ell.isChecked(), s is not None and s.mode in ("blur", "sign") and
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
        # 동시 블러: 종류별 (리졸버, mode) 목록. 미리보기가 종류별로 이어서 블러.
        plan = [(edits.Resolver(src, self.state.track(s, m), m), m) for _r, m in self.state.blur_resolvers(s)]
        keys = self.panel.key_mode != "off"
        sc = self._vscale()
        sz = self._vsize(info)
        fc = self.frame_cache if (self.frame_cache and self.frame_cache[:3] == (info.path, i, sc)) else None

        def fn():
            if g != self.gen:               # 밀린 요청 — 계산하지 않고 버림
                return None
            fr = fc[3] if fc else decoder.grab(info, i, 16, sz)
            res = compute_preview(src, fr, plan, i, sc, keys)
            res["frame16"] = fr
            res["i"] = i
            return res
        j = jobs.Job(fn)
        j.signals.done.connect(lambda r: self._full_done(g, info.path, r))
        j.signals.error.connect(lambda e: self.flash(f"미리보기 오류: {e}"))
        jobs.submit(jobs.PREVIEW_POOL, j, 3)

    def _full_done(self, g, path, r):
        info = self._info()
        if r is None or g != self.gen or not info or info.path != path:
            return
        self.frame_cache = (path, r["i"], self._vscale(), r.pop("frame16"))
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
        if r.get("ref") is not None:
            self.panel.set_auto_ref(r["ref"])
        k = self.panel.key_mode
        if k != "off" and "final" not in r:      # 키를 방금 켬 — 이 프레임의 키를 계산(그동안은 보정 화면)
            self._param_t.start()
        main = r["fx"] if k == "off" else r.get(k, r.get("final", r["fx"]))
        if v == "orig":
            imgs = [r["orig"]]
        elif v == "split":
            imgs = [r["orig"], main]
        else:
            imgs = [main]
        self.mon.set_default("face" if (v == "split" and s.mode == "beauty") else "fit")
        self.mon.set_focus_ellipse(r.get("focus"))
        self.mon.set_images(imgs, self._vsize(info))
        self.mon.quad_mode = s.mode == "sign"
        self.mon.set_overlay(r["ells"], self.panel.t_ell.isChecked(),
                             s.mode in ("blur", "sign") and self.panel.t_id.isChecked())

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
        self._play_fx_off, self._play_slow = False, 0
        self.mon.playing = True                # 재생 중 부드러운 확대 끔(빠르게)
        self.player = Player(info, self.f, self._play_frame, self._play_end, self._play_size(info))  # 저해상도=즉시 재생
        self.b_play.setProperty("on", True)
        self.b_play.style().polish(self.b_play)

    def _play_frame(self, i, fr):
        info = self._info()
        if not info:
            return
        self.f = i
        self.bar.set_cur(i)
        self.lanes.set_cur(i)
        self.ruler.set_cur(i)
        self.playhead.update()
        self.tc.setText(ff.frames_to_tc(ff.tc_to_frames(info.timecode, info.fps) + i, info.fps))
        self.mon.set_images([self._play_fx(info, i, fr)], self._vsize(info))
        self._overlay_from_track(i)

    def _play_fx(self, info, i, fr):
        """재생 중 블러 미리보기(v0.7.54): 분석된 블러·간판 소스는 재생 화면(작은 크기)에 블러를 입혀 보여 줌.
        '원본' 보기·뷰티·분석 전에는 원본 그대로. 한 프레임이 오래 걸리면(느린 PC) 그 재생 동안은 꺼서 끊김 방지."""
        s = self.state.source()
        if (s is None or s.mode == "beauty" or self.panel.view_mode() == "orig"
                or getattr(self, "_play_fx_off", False)):
            return fr
        try:
            t0 = time.perf_counter()
            k = fr.shape[1] / float(info.w)
            out = None
            for res, mode in self.state.blur_resolvers(s):
                if res.track is None:
                    continue
                items = res.items(i)
                if not items:
                    continue
                if out is None:
                    out = fr.astype(np.uint16) << 8
                out, _, _ = fx.blur(out, _scale_items(items, k), s.params_for(mode), False)
            if out is None:
                return fr
            if time.perf_counter() - t0 > 0.6 / max(1.0, info.fps_f):
                self._play_slow = getattr(self, "_play_slow", 0) + 1
                if self._play_slow >= 5:
                    self._play_fx_off = True
            return np.ascontiguousarray((out >> 8).astype(np.uint8))
        except Exception:  # noqa: BLE001
            self._play_fx_off = True
            return fr

    def _play_end(self):
        self.player = None
        self.mon.playing = False
        self._unplay_btn()
        self.seek(self.f, force=True)

    def stop_play(self):
        if self.player:
            self.player.stop()
            self.player = None
            self.mon.playing = False
            self._unplay_btn()

    def _unplay_btn(self):
        self.b_play.setProperty("on", False)
        self.b_play.style().polish(self.b_play)

    # ------------------------------------------------------------ 피부색 찍기
    def on_pick(self, x, y):
        if not self.frame_cache:
            return
        ref = fx.pick_skin(self.frame_cache[3], x, y)      # (path, i, scale, frame16) → [cb, cr, y]
        if self.panel.pick_target == "extra":              # v0.7.63 추가 피부색(최대 2개)
            s = self.state.source()
            ex = [list(e) for e in (s.beauty.get("skin_extra") or [])] if s else []
            if len(ex) >= 2:
                self.panel.stop_pick()
                self.flash("추가 피부색은 2개까지 — 칩을 눌러 지운 뒤 다시 찍으세요")
                return
            self.state.set_param("skin_extra", ex + [ref], undo=True)
            self.panel.stop_pick()
            self.flash(f"추가 피부색 {len(ex) + 1}/2: Cb {ref[0]:+.3f} · Cr {ref[1]:+.3f}"
                       + (" — 어두운 색입니다(머리카락·눈썹까지 들어갈 수 있음)" if ref[2] < 0.12 else ""))
            return
        self.state.set_param("skin_ref", ref, undo=True)
        self.panel.stop_pick()
        self.flash(f"피부색: Cb {ref[0]:+.3f} · Cr {ref[1]:+.3f}")

    # ------------------------------------------------------------ 소스 추가·삭제
    def pick_files(self, parent=None):
        """파일 선택 창 → 영상 경로 목록(취소면 [])."""
        start = self.state.settings.d.get("last_folder", "")
        files, _ = QFileDialog.getOpenFileNames(parent or self, "영상 추가", start, VID_FILTER)
        if files:
            self.state.settings.d["last_folder"] = str(Path(files[0]).parent)
            self.state.settings.save()
        return files

    def pick_folder(self, parent=None):
        """폴더 선택 창 → 그 안(하위 포함) 영상 경로 목록. 취소면 None, 영상 없으면 []."""
        start = self.state.settings.d.get("last_folder", "")
        d = QFileDialog.getExistingDirectory(parent or self, "폴더 추가(하위 폴더 포함)", start)
        if not d:
            return None
        self.state.settings.d["last_folder"] = d
        self.state.settings.save()
        return find_videos(d)

    def add_dialog(self):
        files = self.pick_files()
        if files:
            self.state.add_files(files)

    def add_folder(self):
        vids = self.pick_folder()
        if vids is None:
            return
        if vids:
            self.state.add_files(vids)
            self.flash(f"{len(vids)}개 추가")
        else:
            self.flash("이 폴더에 영상이 없습니다")

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
            from . import models
            models.set_gpu(self.state.settings.render.get("gpu", False))
            self.state._tracks.clear()
            self.on_params()

    def _out_dir(self):
        """기억해 둔 폴더가 있으면 그대로(➜ 로 목록에 보낼 때 등 '묻지 않는' 경로용)."""
        rs = self.state.settings.render
        if rs.get("out_dir") and Path(rs["out_dir"]).is_dir():
            return rs["out_dir"]
        return self._ask_out_dir()

    def _ask_out_dir(self):
        """저장폴더를 반드시 물어봄(지난 폴더를 초기 위치로). 취소 = None."""
        rs = self.state.settings.render
        start = rs.get("out_dir") if rs.get("out_dir") and Path(rs["out_dir"]).is_dir() \
            else self.state.settings.d.get("last_folder", "")
        d = QFileDialog.getExistingDirectory(self, "저장폴더 선택", start)
        if not d:
            return None
        rs["out_dir"] = d
        self.state.settings.save()
        return d

    def queue_to_render(self):
        """➜ : 현재/선택 소스를 렌더 '목록'에 추가(대기). 렌더는 시작하지 않음."""
        rows = self.sources.selected() or ([self.state.cur] if self.state.cur >= 0 else [])
        if not rows:
            self.flash("보낼 소스를 선택하세요")
            return
        out = self._out_dir()
        if not out:
            return
        for r in rows:
            s = self.state.project.sources[r]
            self.rpanel.add(RenderItem(s, self.state.settings.render, out))   # 기본 상태 '대기'
        self.rpanel.refresh()
        self.flash(f"렌더 목록에 {len(rows)}개 추가 — [렌더]로 실행")

    def render_all(self):
        """[렌더] : 렌더 중이면 전체 취소, 아니면 저장폴더를 물어 목록 전체를 렌더(v0.7.80)."""
        if getattr(self, "_run_items", None):          # 렌더 중 = 같은 버튼이 취소(2-A)
            self.cancel_renders()
            return
        if not self.rpanel.items:
            self.flash("렌더 목록이 비어 있습니다 — ▼ 로 먼저 보내세요")
            return
        todo = [it for it in self.rpanel.items if it.state in ("대기", "취소", "오류")]
        if not todo:
            self.flash("이미 모두 렌더됨 — 다시 하려면 우클릭 '다시 렌더'")
            return
        out = self._ask_out_dir()                      # v0.7.80 [렌더] 누를 때마다 반드시 저장폴더 물어봄(1-A)
        if not out:
            return
        for it in todo:
            it.state, it.p, it.err, it.out_dir = "대기", 0.0, "", out
        for it in todo:
            self._start_render(it)

    def cancel_renders(self):
        """진행 중·대기 중 렌더를 모두 멈춤(완료분은 그대로). 버튼 취소·closeEvent 공용."""
        run = getattr(self, "_run_items", None)
        if not run:
            return
        for it in list(run):
            if it.job is not None and it.state in ("진행", "대기"):
                it.job.cancel.set()
        self.flash("렌더 취소 중…")

    # ------------------------------------------------------------ AE 보내기(v0.7.14)
    def export_ae(self, path=None):
        """렌더 대기목록의 소스(없으면 현재 소스)를 각 원본 폴더 옆에 .jsx 로 저장. 동시 블러면 두 종류 다. 렌더 없음.
        path 가 주어지면(테스트) 현재 소스 1개를 그 경로로."""
        import copy

        from . import aefx
        # 대상 소스 목록: 렌더 대기목록이 있으면 그 소스들(중복 경로 1개), 없으면 현재 소스
        if path is None and self.rpanel.items:
            seen, srcs = set(), []
            for it in self.rpanel.items:
                live = next((x for x in self.state.project.sources if x.path == it.src.path), None)
                sx = live or it.src
                if sx.path not in seen:
                    seen.add(sx.path)
                    srcs.append(sx)
        else:
            s = self.state.source()
            if s is None:
                self.flash("보낼 소스를 선택하세요")
                return None
            srcs = [s]
        # 각 소스: (track, mode) 쌍 + 원본 폴더 옆 경로
        jobsq = []
        for s in srcs:
            info = self.state.info(s.path)
            if info is None:
                continue
            pairs = [(self.state.track(s, m), m) for m in (s.blur_kinds() or [s.mode])]
            if all(tr is None for tr, _ in pairs) and not edits.get(s).get("groups"):
                continue                                 # 분석·수정 둘 다 없음 → 건너뜀
            out = path if (path and len(srcs) == 1) else str(Path(s.path).with_name(f"{out_stem(s.name)}_AE.jsx"))
            jobsq.append((copy.deepcopy(s), info, pairs, out))
        if not jobsq:
            self.flash("먼저 분석하세요 — 트랙이 없습니다")
            return None
        self.flash(f"AE 보내는 중… {len(jobsq)}개")

        def work(progress, cancel):
            done = 0
            for src, info, pairs, out in jobsq:
                if cancel.is_set():
                    break
                aefx.export(src, info, pairs, out)
                done += 1
                progress(done / len(jobsq))
            return done

        j = jobs.Job(work, with_progress=True)
        j.signals.done.connect(lambda n: self.flash(f"AE 보내기 완료 — {n}개 .jsx (소스 원본 폴더 옆)"))
        j.signals.error.connect(lambda e: self.flash(f"AE 보내기 오류: {e}"))
        jobs.submit(jobs.ANALYZE_POOL, j, 5)
        return j

    # ------------------------------------------------------------ AUTO
    def open_auto(self):
        if not self.state.project.sources:
            self.flash("먼저 영상을 추가하세요")
            return
        from .ui_auto import AutoDialog
        dlg = AutoDialog(self, self.state)
        dlg.move(self.geometry().center() - dlg.rect().center())
        dlg.exec()

    def render_auto(self, idxs, out_dir, name, prores=None):
        """AUTO 보정+렌더: 렌더 목록에 넣고 바로 시작. 만든 RenderItem 목록을 돌려줌(AUTO 창이 진행률 표시)."""
        made = []
        rs = dict(self.state.settings.render)
        if name:
            rs["name"] = name
        for idx in idxs:
            if 0 <= idx < len(self.state.project.sources):
                s = self.state.project.sources[idx]
                it = RenderItem(s, dict(rs, prores=ff.prores_kind((prores or {}).get(idx, rs.get("prores")))), out_dir)
                self.rpanel.add(it)
                self._start_render(it)
                made.append(it)
        if idxs:
            self.flash(f"AUTO 적용 완료 — {len(idxs)}개 렌더 시작")
        return made

    def rerender(self, rows):
        for r in rows:
            it = self.rpanel.items[r]
            if it.state != "진행":
                it.state, it.p, it.err = "대기", 0.0, ""
                self._start_render(it)
        self.rpanel.refresh()

    def _start_render(self, it: RenderItem):
        # v0.7.4: 렌더 차례를 기다리는 동안 분석을 미리(분석 풀 3 — 뷰티 분석은 코어를 조금만 씀).
        # 렌더 쪽 analyze() 는 같은 분석이 돌고 있으면 기다렸다가 캐시를 쓴다(중복 분석 없음).
        live = next((x for x in self.state.project.sources if x.path == it.src.path and x.mode == it.src.mode), None)
        if live is not None and analyze.level_for(live.mode, live.params()) == analyze.level_for(it.src.mode,
                                                                                                    it.src.params()):
            self.state.ensure_analysis(live)

        def fn(prog, cancel):
            it.stats = {}
            return do_render(it.src, it.rs, it.out_dir, prog, cancel, stats=it.stats)
        j = jobs.Job(fn, with_progress=True)
        it.job = j

        self._render_batch = True            # 완료 팝업용: 렌더가 한 번이라도 시작됨
        run = getattr(self, "_run_items", None)
        if not run:                          # 새 묶음 시작(전체 진행 = [렌더] 버튼 막대 · 창 제목 · 작업 표시줄)
            run = self._run_items = []
            self._run_t0 = time.time()
        if it not in run:
            run.append(it)

        it.t0 = None

        def started(v):
            if it.t0 is None:
                it.t0 = time.perf_counter()
            it.state = "진행"
            it.p = v
            self.rpanel.refresh()             # 진행은 렌더목록 바에만(소스목록 아님)
            self._render_overall()
        j.signals.progress.connect(started)
        j.signals.done.connect(lambda outs: self._render_done(it, outs))
        j.signals.error.connect(lambda e: self._render_err(it, e))
        jobs.submit(jobs.RENDER_POOL, j)
        self.rpanel.refresh()
        self._render_overall()

    # ------------------------------------------------------------ 전체 렌더 진행(v0.7.69, DTME 와 같은 방식)
    def _render_overall(self):
        """이번 묶음 전체 진행(프레임 수 가중) → [렌더] 버튼 막대 '끝난 수/전체 · NN%' · 창 제목 · 작업 표시줄."""
        run = getattr(self, "_run_items", None)
        if not run:
            return
        from . import taskbar

        def weight(it):
            info = self.state.infos.get(it.src.path)
            return max(1, int(getattr(info, "frames", 0) or 0)) if info is not None else 1
        tot = sum(weight(it) for it in run)
        got = sum(weight(it) * (1.0 if it.state in ("완료", "오류", "취소") else (it.p if it.state == "진행" else 0.0))
                  for it in run)
        frac = got / tot if tot else 0.0
        k = sum(it.state in ("완료", "오류", "취소") for it in run)
        bad = any(it.state == "오류" for it in run)
        text = f"{k}/{len(run)} · {int(frac * 100)}%"
        left = self._render_left(frac)
        b = self.rpanel.b_render
        b.set_progress(frac, text, bad)
        b.setToolTip((f"남은 시간 {left} · " if left else "렌더 중 · ") + "누르면 취소")
        self._rtext = f"{text} · 남은 {left}" if left else text
        self._title()
        taskbar.set_progress(self, frac, bad)

    def _render_left(self, frac: float) -> str:
        """남은 시간: 지금까지 속도로 어림. 처음 10초·3% 전에는 안 보임(초반 값이 크게 흔들림)."""
        el = time.time() - getattr(self, "_run_t0", time.time())
        if el < 10 or frac < 0.03 or frac >= 1:
            return ""
        s = int(el * (1 - frac) / frac)
        s = (s + 4) // 5 * 5 if s < 120 else (s + 29) // 30 * 30          # 숫자가 매초 바뀌지 않게
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _render_overall_end(self):
        from . import taskbar
        self._run_items = []
        self._rtext = ""
        b = self.rpanel.b_render
        b.set_progress(None)
        b.setToolTip(self.rpanel.render_tip)
        self._title()
        taskbar.clear(self)
        taskbar.flash(self)                    # 창이 앞에 없으면 작업 표시줄 아이콘 깜빡임

    def _render_analysis_p(self, it):
        """렌더 대기 항목의 분석 진행(돌고 있는 것 중 가장 덜 된 것). 분석이 안 돌고 있으면 None."""
        ps = [a["p"] for (path, _m), a in self.state.an.items() if path == it.src.path and a.get("state") == "run"]
        return min(ps) if ps else None

    def _render_done(self, it, outs):
        it.state, it.p, it.outs = "완료", 1.0, outs
        self.rpanel.refresh()
        self.flash(f"렌더 완료: {it.src.name}" + (f" — {stat_line(it.stats)}" if it.stats else ""))
        self._render_check_all_done()

    def _render_err(self, it, e):
        it.state = "취소" if e == "취소됨" else "오류"
        it.err = e
        self.rpanel.refresh()
        if it.state == "오류":
            self.flash(f"렌더 오류: {it.src.name} — {e[:200]}")
        self._render_check_all_done()

    def _render_check_all_done(self):
        """진행·대기 중인 렌더가 하나도 없으면(= 전부 끝남) 결과 팝업 1번."""
        if not getattr(self, "_render_batch", False):
            return
        self._render_overall()
        run = getattr(self, "_run_items", None)
        if run and all(it.state in ("완료", "오류", "취소") for it in run):
            self._render_overall_end()          # 이번에 돌린 것이 다 끝남 → 버튼을 원래대로
        if any(it.state in ("진행", "대기") for it in self.rpanel.items):
            return
        self._render_batch = False
        if getattr(self, "_suppress_render_popup", False):
            return                                  # 시작창 AUTO 흐름 = AUTO 창이 직접 완료 팝업을 띄움
        done = sum(it.state == "완료" for it in self.rpanel.items)
        err = sum(it.state == "오류" for it in self.rpanel.items)
        cancel = sum(it.state == "취소" for it in self.rpanel.items)
        msg = f"렌더 완료: {done}개"
        if err:
            msg += f" · 오류 {err}개"
        if cancel:
            msg += f" · 취소 {cancel}개"
        self.flash(msg)
        if QApplication.platformName() == "offscreen":
            return                              # 테스트/헤드리스: 모달 팝업이 블록되므로 생략
        from .ui_widgets import info_popup
        info_popup(self, "렌더 완료", msg, [("확인", "ok", True, 80)])   # 다른 팝업과 다크 테마 통일

    # ------------------------------------------------------------ 프로젝트
    def undo(self):
        if not self.state.undo():
            self.flash("더 이상 취소할 작업이 없습니다")

    def redo(self):
        if not self.state.redo():
            self.flash("다시 실행할 작업이 없습니다")

    # ------------------------------------------------------------ 원본 다시 연결(v0.7.56)
    def _missing(self):
        return [s for s in self.state.project.sources if not Path(s.path).exists()]

    def relink_missing(self, folder=None):
        """없는 원본을 폴더(하위 포함)에서 같은 파일 이름으로 찾아 한꺼번에 연결. 편집값·설정은 유지."""
        miss = self._missing()
        if not miss:
            self.flash("없는 원본이 없습니다")
            return 0
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, f"원본이 있는 폴더 (없는 원본 {len(miss)}개)",
                                                      self.state.settings.d.get("last_folder", ""))
        if not folder:
            return 0
        import os
        want = {Path(s.path).name.lower(): s.path for s in miss}
        found = {}
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                old = want.get(fn.lower())
                if old is not None and old not in found:
                    found[old] = str(Path(root) / fn)
            if len(found) == len(want):
                break
        n = self.state.relink(found)
        left = len(miss) - n
        self._title()
        self.flash(f"원본 연결 {n}개" + (f" · 못 찾음 {left}개" if left else "") + " — 분석은 다시 필요합니다" if n
                   else "이 폴더에서 같은 이름의 파일을 찾지 못했습니다")
        return n

    def _warn_missing(self):
        miss = self._missing()
        if not miss or QApplication.platformName() == "offscreen":
            return
        from .ui_widgets import info_popup
        names = "\n".join(s.name for s in miss[:12]) + (f"\n… 외 {len(miss) - 12}개" if len(miss) > 12 else "")
        r = info_popup(self, "원본 없음", f"원본 파일을 찾을 수 없습니다 ({len(miss)}개):\n{names}\n\n"
                       "원본이 있는 폴더를 지정하면 같은 이름의 파일로 다시 연결합니다.",
                       [("닫기", "no", False, 72), ("폴더 지정", "pick", True, 96)])
        if r == "pick":
            self.relink_missing()

    # ------------------------------------------------------------ 자동 저장·복구(v0.7.56)
    @staticmethod
    def _autosave_file():
        return paths.cache_dir() / "autosave.mosco.json"

    def _clear_autosave(self):
        try:
            self._autosave_file().unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def autosave_pending(self):
        """지난번에 저장 못 하고 끝난(튕긴) 작업이 있으면 {time, n, file}. 정상 종료·저장 때는 파일을 지우므로 없음."""
        f = self._autosave_file()
        try:
            if not f.exists():
                return None
            d = json.loads(f.read_text(encoding="utf-8"))
            n = len(d.get("sources", []))
            if n == 0:
                return None
            meta = d.get("autosave", {})
            return {"time": meta.get("time") or time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                    "n": n, "file": meta.get("file", "")}
        except Exception:  # noqa: BLE001
            return None

    def restore_autosave(self) -> bool:
        meta = self.autosave_pending()
        if meta is None:
            return False
        try:
            p = Project.load(self._autosave_file())
        except Exception:  # noqa: BLE001
            return False
        p.file = meta["file"] or ""            # 원래 프로젝트 파일(있었다면) — [저장하기]가 거기에 저장
        self.state.set_project(p)
        self.state.dirty = True                # 아직 저장 안 된 작업
        self._title()
        self.flash("자동 저장된 작업을 복구했습니다 — [저장하기]로 저장하세요")
        return True

    def check_autosave(self, parent=None) -> bool:
        """시작할 때 한 번: 복구할지 묻는다. 복구하면 True. 버리면 자동 저장 파일 삭제."""
        meta = self.autosave_pending()
        if meta is None:
            return False
        from .ui_widgets import info_popup
        name = f"\n프로젝트: {Path(meta['file']).name}" if meta["file"] else ""
        r = info_popup(parent or self, "저장 안 된 작업",
                       f"지난번에 저장하지 못하고 끝난 작업이 있습니다.\n자동 저장: {meta['time']} · 소스 {meta['n']}개{name}",
                       [("버리기", "drop", False, 80), ("복구", "restore", True, 88)])
        if r == "restore" and self.restore_autosave():
            self._warn_missing()
            return True
        if r == "drop":
            self._clear_autosave()
        return False

    def _confirm_discard(self) -> bool:
        if not self.state.dirty:
            return True
        r = confirm_save(self)
        if r == "save":
            return self.save_project()
        return r == "discard"

    def new_project(self):
        if self._confirm_discard():
            self.state.set_project(Project())
            self._clear_autosave()
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
        self.state.set_project(p)
        self._clear_autosave()
        self._title()
        self._warn_missing()                    # 없는 원본 → [폴더 지정]으로 다시 연결

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
        self._clear_autosave()
        self._title()
        self.flash(f"저장됨: {f}")
        return True

    def _do_autosave(self):
        """1분마다(바뀐 게 있을 때만). 저장·정상 종료 때 지우므로, 파일이 남아 있으면 = 튕김 → 다음 실행 때 복구를 물음."""
        if self.state.dirty and self.state.project.sources:
            try:
                d = self.state.project.to_dict()
                d["autosave"] = {"time": time.strftime("%Y-%m-%d %H:%M"), "file": self.state.project.file or ""}
                f = self._autosave_file()
                tmp = f.with_name(f.name + ".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(f)                  # 쓰는 도중 튕겨도 이전 자동 저장이 깨지지 않게
            except Exception:  # noqa: BLE001
                pass

    def _cancel_renders(self):
        for it in self.rpanel.items:
            if it.job is not None:
                it.job.cancel.set()

    def eventFilter(self, o, e):
        T = e.type()
        if o is self.lanes_box.verticalScrollBar() and T in (T.Show, T.Hide):
            w = o.sizeHint().width() if T == T.Show else 0
            self._tl_pad.setContentsMargins(0, 0, w, 0)
        ph = getattr(self, "playhead", None)
        if ph is not None:
            if T == T.Resize and o is self._bot:
                ph.setGeometry(self._bot.rect())
                ph.raise_()
            if T in (T.Resize, T.Move) and o is self.lanes:
                ph.update()
            if o in (self._bot, self.lanes_box.viewport()) and T in (T.MouseButtonPress, T.MouseMove,
                                                                     T.MouseButtonRelease):
                # 레인·소스 바·눈금 '사이 틈'과 레인 아래 빈 곳: 재생선 ±6px 를 누르면 잡아 끌기
                x = o.mapTo(self._bot, e.position().toPoint()).x()
                if T == T.MouseButtonPress and e.button() == Qt.LeftButton:
                    lx = ph.line_x()
                    if lx is not None and abs(x - lx) <= 6:
                        self._ph_drag = True
                        self.seek(ph.frame_at(x))
                        return True
                elif T == T.MouseMove and self._ph_drag:
                    self.seek(ph.frame_at(x))
                    return True
                elif T == T.MouseButtonRelease and self._ph_drag:
                    self._ph_drag = False
                    return True
        return super().eventFilter(o, e)

    def showEvent(self, e):
        super().showEvent(e)
        from .ui_widgets import tint_title
        tint_title(self)                       # v0.7.73 헤더·창 테두리 = 프로그램과 같은 진회색

    def closeEvent(self, e):
        if self._confirm_discard():
            # v0.7.71 정리 단계 하나가 오류를 내도 나머지와 종료는 반드시 진행
            # (예전: 여기서 오류가 나면 창만 닫히고 프로세스는 창 없이 남아 폴더를 잠글 수 있었음)
            for step in (self.stop_play, self.state.cancel_all, self._cancel_renders,
                         self._clear_autosave,   # 정상 종료(저장했거나 버리기로 함) → 복구할 것 없음
                         decoder.close_all,      # 상주 디코더 닫기
                         ff.kill_all):           # 남은 ffmpeg 프로세스 강제 종료(종료 후 버벅임 방지)
                try:
                    step()
                except Exception:  # noqa: BLE001
                    pass
            e.accept()
            if getattr(self, "quit_on_close", False):      # 시작 창 흐름(main.py)에서는 본 창을 닫으면 종료
                from PySide6.QtWidgets import QApplication
                QApplication.instance().quit()
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
                vids += find_videos(pth)
            elif pth.suffix.lower() in VIDEO_EXT:
                vids.append(f)
        projs = [f for f in files if f.endswith(".json")]
        if vids:
            self.state.add_files(vids)
        elif projs and self._confirm_discard():
            self._load(projs[0])
