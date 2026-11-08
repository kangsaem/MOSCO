"""AUTO 창·샘플 창 (DTME AUTO_UI.md 틀). 분류·값은 MOSCO 것.

AUTO: 체크한 클립을 장면 인식(autodetect) → 속성 격자로 고치고 → 적용(바로 렌더 / 개별 보정) 또는 샘플.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QStyle, QVBoxLayout, QWidget)

from . import autodetect as ad
from . import ff, jobs
from .ui_widgets import PROG, PROG_TXT, QSS, DragMove, ProgBar, add_shadow, btn, set_on, tone

# 버튼 색 이름 — 모양은 ui_widgets.QSS(DTME buttons.qss) 한 곳
RED, ORANGE, GREY = "red", "orange", "light"
AUTO_LW = 40               # AUTO 창 이름칸 폭(작업·강도·출력·이름 = 두 글자)
LEFT_PAD = AUTO_LW + 5     # _seg 이름칸 폭 + 간격 = [약|중|강] 첫 버튼 왼쪽 끝


def _b(text, w=0, h=28, style="", tip="", on=None):
    b = QPushButton(text)
    b.setFixedHeight(h)
    if w:
        b.setFixedWidth(w)
    b.setFocusPolicy(Qt.NoFocus)
    if style:
        tone(b, style)
    if tip:
        b.setToolTip(tip)
    if on:
        b.clicked.connect(on)
    add_shadow(b)                 # 모든 버튼에 약한 볼록 그림자(DTME 통일)
    return b


def _seg(opts, cur, on_change, lw=76, label=""):
    """[a|b|c] 한 줄. opts = [(key,text)]. 반환 (widget, setter)."""
    row = QHBoxLayout()
    row.setSpacing(5)
    w = QWidget()
    w.setLayout(row)
    if label:
        lb = QLabel(label)
        lb.setFixedWidth(lw)
        lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lb.setStyleSheet("color:#d0d0d0")
        row.addWidget(lb)
    box = QWidget()
    hb = QHBoxLayout(box)
    hb.setContentsMargins(0, 0, 0, 0)
    hb.setSpacing(5)                      # DTME: 떨어진 둥근 버튼, 상자·그림자 없음
    grp = QButtonGroup(box)
    btns = {}
    for i, (k, t) in enumerate(opts):
        b = QPushButton(t)
        b.setCheckable(True)
        b.setFixedHeight(30)                  # DTME AUTO 창과 같은 높이
        b.setFocusPolicy(Qt.NoFocus)
        grp.addButton(b)
        hb.addWidget(b)
        btns[k] = b
        b.clicked.connect(lambda _=0, kk=k: on_change(kk))
    row.addWidget(box, 1)

    def setter(key):
        if key in btns:
            btns[key].setChecked(True)
    setter(cur)
    return w, setter


class ClipRow(QFrame):
    clicked = Signal(object, object)       # idx, modifiers

    def __init__(self, idx, name):
        super().__init__()
        self.idx = idx
        self.setFixedHeight(30)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(8)
        self.chk = QCheckBox()
        self.chk.setChecked(True)
        lay.addWidget(self.chk)
        self.name = QLabel(name)
        self.name.setStyleSheet("font-weight:700")
        lay.addWidget(self.name)
        self.tag = QLabel("")                 # 'C · 뷰티' 식 작은 표시
        self.tag.setStyleSheet("color:#9aa0a6;font-size:11px")
        lay.addWidget(self.tag)
        lay.addStretch(1)
        self.sub = QLabel("")                 # 측정 결과(작게)
        self.sub.setStyleSheet("color:#8a9bb0;font-size:11px")
        lay.addWidget(self.sub)
        self.out = QLabel("HQ")               # 출력 칩(오른쪽)
        self.out.setStyleSheet("background:#3a3a3a;border-radius:2px;padding:1px 7px;font-size:10px;color:#cfcfcf")
        lay.addWidget(self.out)
        self.sel = False
        self.prog = None
        self.kind = "measure"         # 진행 단계(색) — ui_widgets.PROG
        self._paint()

    def set_state(self, included, sel, c, preset, sub, out, prog, detected_auto, kind="measure"):
        self.sel = sel
        self.prog = prog
        self.kind = kind
        self.chk.blockSignals(True)
        self.chk.setChecked(included)
        self.chk.blockSignals(False)
        self.tag.setText((("C · " if c else "") + preset) if preset else ("C" if c else ""))
        self.sub.setStyleSheet("font-size:11px;color:%s" % (PROG[kind][1] if prog is not None else "#8a9bb0"))
        self.sub.setText(sub)                 # 글자는 스타일 다음에(먼저 넣으면 폭이 옛 글꼴로 잡혀 잘림)
        self.out.setText(out)
        self.name.setStyleSheet("font-weight:700;color:%s" % ("#eaeaea" if included else "#777"))
        self._paint()

    def _paint(self):
        # DTME 식: 차분한 행 배경 + 선택은 파란 '테두리'(채움 아님), 측정 중이면 아래 얇은 진행 느낌
        base = "#2d2d2c" if self.chk.isChecked() else "#242424"
        v, fill = self.prog, PROG.get(self.kind, PROG["measure"])[0]
        if v is not None and v < 1.0:
            p = max(0.0, min(1.0, v))
            bg = ("qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 %s,"
                  f"stop:{p:.3f} %s,stop:{min(0.999, p + 0.001):.3f} {base},stop:1 {base})") % (fill, fill)
        else:
            bg = base
        border = "1px solid #4a8fe0" if self.sel else "1px solid #333"
        self.setStyleSheet("ClipRow{border-radius:3px;background:%s;border:%s}"
                           "QLabel{background:transparent}" % (bg, border))   # 글자칸이 진행 채움을 가리지 않게

    def mousePressEvent(self, e):
        if self.childAt(e.position().toPoint()) is self.chk:
            return super().mousePressEvent(e)
        self.clicked.emit(self.idx, e.modifiers())


class AutoDialog(DragMove, QDialog):
    def __init__(self, parent, state, only=None, mw=None):
        """only = 이 클립 번호만 목록에(시작 창 AUTO = 방금 고른 파일만). outcome = 'each'·'render'·'' (닫힌 이유).
        mw = 본 창(parent 가 None 일 때 — 시작 창 흐름: 작업표시줄에 따로 보이게 부모 없이 띄움)."""
        super().__init__(parent)
        self.mw = mw or parent
        self.setWindowTitle("MOSCO AUTO")
        self.outcome = ""
        self.state = state
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setModal(True)
        # 부모 없이 뜨면(시작 창 흐름) 본 창의 버튼 QSS 를 못 물려받으므로 직접 붙임
        self.setStyleSheet(("" if parent is not None else QSS) +
                           "AutoDialog{background:#262626;border:1px solid #555;border-radius:6px}"
                           "QLabel{color:#ddd;font-size:12px}")
        self.setFixedSize(998, 720)          # 이름칸 76→40·가운데 간격 16→10 만큼 줄임(목록·버튼 크기 그대로)
        # 클립 상태
        rs = state.settings.render
        self.name = (rs.get("name") or "").strip()
        self.strength = state.settings.d.get("auto_strength", "mid")
        self.keep_existing = False          # 항상 '기존 보정 무시 · 키프레임 자동'으로 시작
        self.keep_keys = False
        self.after = state.settings.d.get("auto_after", "render")
        self.clips = []
        for i, s in enumerate(state.project.sources):
            if only is not None and i not in only:
                continue
            self.clips.append({"idx": i, "name": s.name, "inc": True, "mode": s.mode,
                               "measured": None, "prog": None, "c": s.custom, "outp": "HQ"})
        self.selected = {self.clips[0]["idx"]} if self.clips else set()
        self.rows = []
        self._jobs = {}
        self._closed = False
        self._renders = []            # 보정+렌더 중인 RenderItem
        self._phase = None            # None · measure · analyze · sample · render
        self._an = []                 # [보정+편집] 분석·트랙 대상 [(clip, source, 트랙 요청함)]
        self._build()
        self._refresh()
        # 창을 열 때는 아무 작업도 안 함(v0.7.3) — 버튼을 누르면 측정부터 시작.
        # 막 추가한 파일은 정보(probe)가 늦게 옴 → 측정 단계에서 오면 그때 측정 시작
        state.changed.connect(self._on_state_changed)

    # ------------------------------------------------------------ 구성
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)
        mid = QHBoxLayout()
        mid.setSpacing(10)
        # 왼쪽 클립 목록
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.NoFrame)
        sc.setStyleSheet("QScrollArea{background:#1e1e1e;border:1px solid #333;border-radius:3px}")
        body = QWidget()
        body.setStyleSheet("background:#1e1e1e")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(6, 6, 6, 6)
        bl.setSpacing(4)
        for c in self.clips:
            r = ClipRow(c["idx"], c["name"])
            r.clicked.connect(self._on_row)
            r.chk.toggled.connect(lambda v, i=c["idx"]: self._on_check(i, v))
            bl.addWidget(r)
            self.rows.append(r)
        bl.addStretch(1)
        sc.setWidget(body)
        mid.addWidget(sc, 1)
        # 오른쪽 격자+값
        right = QVBoxLayout()
        right.setSpacing(10)
        # 상단: 기존 보정 / 키프레임 (DTME 배치)
        toprow = QHBoxLayout()
        toprow.setSpacing(8)
        toprow.setContentsMargins(0, 0, 0, 0)
        toprow.addSpacing(LEFT_PAD)           # 아래 [약|중|강] 버튼 왼쪽 끝에 맞춤
        w1, self.set_keep = _seg([("keep", "기존 보정 유지"), ("drop", "기존 보정 무시")],
                                 "keep" if self.keep_existing else "drop", self._on_keep, label="")
        toprow.addWidget(w1)
        w2, self.set_keepk = _seg([("keys", "키프레임 유지"), ("autok", "키프레임 자동")],
                                  "keys" if self.keep_keys else "autok", self._on_keepk, label="")
        toprow.addWidget(w2)
        toprow.addStretch(1)
        for wx in (w1, w2):                   # DTME: [기존 보정 유지|무시] [키프레임 유지|자동] 최소 폭 100
            for bx in wx.findChildren(QPushButton):
                bx.setMinimumWidth(100)
        topw = QWidget()
        topw.setLayout(toprow)
        right.addWidget(topw)
        div0 = QFrame()
        div0.setFrameShape(QFrame.HLine)
        div0.setStyleSheet("color:#3a3a3a")
        right.addWidget(div0)
        self.seg_work, self.set_work = _seg([("beauty", "뷰티"), ("blur", "얼굴 블러"), ("sign", "간판 블러")], "beauty",
                                            self._on_work, lw=AUTO_LW, label="작업")
        right.addWidget(self.seg_work)
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color:#3a3a3a")
        right.addWidget(div)
        self.seg_strength, self.set_strength = _seg([("weak", "약"), ("mid", "중"), ("strong", "강")], self.strength,
                                                    self._on_strength, lw=AUTO_LW, label="강도")
        right.addWidget(self.seg_strength)
        self.seg_out, self.set_out = _seg([("hq", "422 HQ"), ("std", "422"), ("lt", "LT")],
                                          "hq", self._on_out, lw=AUTO_LW, label="출력")
        right.addWidget(self.seg_out)
        nrow = QHBoxLayout()
        nlb = QLabel("이름")
        nlb.setFixedWidth(AUTO_LW)
        nlb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nrow.addWidget(nlb)
        self.name_edit = QLineEdit(self.name)
        self.name_edit.setFixedWidth(160)
        self.name_edit.setPlaceholderText("MOSCO")
        self.name_edit.setStyleSheet("background:#1a1a1a;border:1px solid #555;border-radius:3px;padding:0 8px;color:#eee")
        self.name_edit.textChanged.connect(self._on_name)
        nrow.addWidget(self.name_edit)
        self.fname = QLabel("")
        self.fname.setStyleSheet("color:#c4cad1;font-family:Consolas,monospace")
        nrow.addWidget(self.fname)
        nrow.addStretch(1)
        nw = QWidget()
        nw.setLayout(nrow)
        right.addWidget(nw)
        right.addStretch(1)
        self.detail = QLabel("")
        self.detail.setTextFormat(Qt.RichText)
        self.detail.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detail.setStyleSheet("background:#1c1c1c;border:1px solid #333;border-radius:3px;padding:12px 16px;color:#ddd")
        self.detail.setFixedHeight(186)           # 6줄(뷰티 11칸) 안 잘림
        drow = QHBoxLayout()
        m = self.style().pixelMetric(QStyle.PM_LayoutLeftMargin)    # [약|중|강] 줄과 같은 바깥 여백
        drow.setContentsMargins(m, 0, m, 0)
        drow.addSpacing(LEFT_PAD)
        drow.addWidget(self.detail)
        dw = QWidget()
        dw.setLayout(drow)
        right.addWidget(dw)
        rw = QWidget()
        rw.setFixedWidth(494)          # 버튼 폭 그대로(530 에서 이름칸 줄인 36 만큼만 뺌)
        rw.setLayout(right)
        mid.addWidget(rw)
        root.addLayout(mid, 1)
        # 아래
        self.pbar = ProgBar(6)
        root.addWidget(self.pbar)
        btmrow = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#7fd18b")
        btmrow.addWidget(self.status)
        btmrow.addStretch(1)                       # 취소도 오른쪽 그룹에(DTME: 취소·샘플·보정+편집·보정+렌더)
        self.b_cancel = _b("취소", w=88, h=32, on=self._cancel)
        btmrow.addWidget(self.b_cancel)
        self.b_sample = _b("샘플", w=88, h=32, style=ORANGE, tip="렌더 없이 샘플 PNG 만 (마지막 샘플 설정)",
                           on=lambda: self._go("sample"))
        btmrow.addWidget(self.b_sample)
        self.b_correct = _b("보정+편집", w=96, h=32, style=GREY,
                            tip="적용 + 타원 분석까지 끝내고 편집 화면으로", on=lambda: self._go("each"))
        self.b_render = _b("보정+렌더", w=110, h=32, style=RED,
                           tip="적용하고 렌더 — 끝나면 창이 닫힘", on=lambda: self._go("render"))
        btmrow.addWidget(self.b_correct)
        btmrow.addWidget(self.b_render)
        bw = QWidget()
        bw.setLayout(btmrow)
        root.addWidget(bw)

    # ------------------------------------------------------------ 선택·적용
    def _targets(self):
        if self.selected:
            return [c for c in self.clips if c["idx"] in self.selected]
        return [c for c in self.clips if c["inc"]]

    def _on_row(self, idx, mods):
        if mods & Qt.ControlModifier:
            self.selected ^= {idx}
        else:
            self.selected = {idx}
        self._refresh()

    def _on_check(self, idx, v):
        for c in self.clips:
            if c["idx"] == idx:
                c["inc"] = v
        self._refresh()

    def _on_work(self, k):
        for c in self._targets():
            c["mode"] = k
        self._refresh()

    def _on_strength(self, k):
        self.strength = k
        self._refresh()

    def _resolved_work(self, c):
        return c["mode"]

    def _out_chip(self, c):
        return c["outp"] + ("+α" if self._resolved_work(c) in ("blur", "sign") else "")

    def _on_out(self, k):
        for c in self._targets():
            c["outp"] = {"hq": "HQ", "std": "422", "lt": "LT"}[k]
        self._refresh()

    def _on_name(self, t):
        self.name = t.strip()
        self._refresh()

    def _on_keep(self, k):
        self.keep_existing = k == "keep"

    def _on_keepk(self, k):
        self.keep_keys = k == "keys"


    # ------------------------------------------------------------ 표시
    def _primary(self):
        if self.selected:
            for c in self.clips:
                if c["idx"] in self.selected:
                    return c
        inc = [c for c in self.clips if c["inc"]]
        return inc[0] if inc else (self.clips[0] if self.clips else None)

    def _refresh(self):
        for c, r in zip(self.clips, self.rows):
            m = c["measured"]
            lbl = ad.LABEL.get(c["mode"], c["mode"])
            sub = ""
            kind = c.get("kind", "render" if self._renders else "measure")
            if c["prog"] is not None:
                # 진행 중: 단계 글자 + 퍼센트(샘플은 c['sub'] 에 'k/n')
                sub = (c.get("sub") if kind == "sample" else None) or f"{PROG_TXT[kind]} {int(c['prog'] * 100)}%"
            elif m and c["mode"] == "blur":
                bits = []
                if m.get("min_face"):
                    bits.append(f"최소 얼굴 {m['min_face']}px")
                bits.append(f"움직임 {m.get('motion', 0)}")
                sub = "측정: " + " · ".join(bits)
            r.set_state(c["inc"], c["idx"] in self.selected, c["c"], lbl, sub, self._out_chip(c), c["prog"], False,
                        kind)
        # primary 클립 기준 작업 버튼 상태
        p = self._primary()
        self.set_work(p["mode"] if p else "beauty")
        self.set_strength(self.strength)
        if p:
            mode, params = ad.to_params(p["mode"], self.strength, p["measured"])
            self.detail.setText(self._detail_text(p, mode, params))
            base = f"{self.name}_{p['name']}" if self.name else p["name"]
            self.fname.setText(base + "_MOSCO.mov")

    def _detail_text(self, clip, mode, params):
        title = ad.LABEL.get(mode, mode)
        if mode == "beauty":
            keys = [("매끈 강도", params["strength"]), ("주름 메우기", params["wrinkle"]),
                    ("질감 보존", params["texture"]), ("그레인", params["grain"]),
                    ("피부 범위", params["skin_range"]), ("색 통일", params["unify"]),
                    ("타원 크기", f"{params['size']}%"), ("페더", f"{params['feather']}%"),
                    ("목 확장", params["neck"]), ("떨림 보정", params["stabilize"]),
                    ("눈·입 제외", "켬" if params.get("features", True) else "끔")]
        elif mode == "sign":
            keys = [("민감도", params["sensitivity"]), ("최소 글자", f"{params['min_size']}px"),
                    ("최대 개수", params["max_count"]), ("여유", f"{params['margin']}%"),
                    ("페더", f"{params['feather']}%"), ("놓침 유지", f"{params['hold']}f"),
                    ("다시 잇기", f"{params['link']}f"), ("블러 강도", params["strength"]),
                    ("떨림 보정", params["stabilize"]), ("모자이크", "켬" if params["kind"] == "mosaic" else "끔")]
        else:
            keys = [("민감도", params["sensitivity"]), ("최소 크기", f"{params['min_size']}px"),
                    ("몸으로 머리", "켬" if params["body"] else "끔"), ("타원 여유", f"{params['margin']}%"),
                    ("페더", f"{params['feather']}%"), ("놓침 유지", f"{params['hold']}f"),
                    ("다시 잇기", f"{params['link']}f"), ("블러 강도", params["strength"]),
                    ("떨림 보정", params["stabilize"]), ("모자이크", "켬" if params["kind"] == "mosaic" else "끔")]
        # DTME식 2열 표
        cells = ""
        for i in range(0, len(keys), 2):
            lk, lv = keys[i]
            rk, rv = keys[i + 1] if i + 1 < len(keys) else ("", "")
            cells += (f"<tr>"
                      f"<td style='color:#9aa0a6;padding:2px 6px 2px 0'>{lk}</td>"
                      f"<td style='color:#eee;padding:2px 28px 2px 0'>{lv}</td>"
                      f"<td style='color:#9aa0a6;padding:2px 6px 2px 0'>{rk}</td>"
                      f"<td style='color:#eee;padding:2px 0'>{rv}</td></tr>")
        return (f"<div style='color:#fff;font-weight:bold;margin-bottom:6px'>● {title}</div>"
                f"<table cellspacing='0'>{cells}</table>")

    # ------------------------------------------------------------ 측정(백그라운드)
    def _need_measure(self, c):
        """측정은 블러만 쓴다(최소 얼굴·움직임 → 블러 자동값). 뷰티는 측정 없이 바로."""
        return c["inc"] and c["mode"] == "blur" and c["measured"] is None

    def _on_state_changed(self):
        if self._phase == "measure":
            self._start_detect()

    def _start_detect(self):
        if self._closed:
            return
        for c in self.clips:
            if not self._need_measure(c) or c["idx"] in self._jobs:
                continue
            path = self.state.project.sources[c["idx"]].path
            if path in self.state.errors:          # 못 읽는 파일 → 측정 없이 기본값(멈춰 기다리지 않게)
                c["measured"] = {}
                continue
            info = self.state.info(path)
            if info is None:
                continue
            c["prog"], c["kind"] = 0.0, "measure"
            idx = c["idx"]

            def fn(prog, cancel, info=info):
                return ad.measure(info, cancel, prog)
            j = jobs.Job(fn, with_progress=True)
            self._jobs[idx] = j
            j.signals.progress.connect(lambda v, i=idx: self._det_prog(i, v))
            j.signals.done.connect(lambda r, i=idx: self._det_done(i, r))
            j.signals.error.connect(lambda e, i=idx: self._det_err(i, e))
            jobs.submit(jobs.DETECT_POOL, j, 2)
        self._refresh()

    def _cancel_detect(self):
        """진행 중인 장면인식 작업을 모두 취소(창을 닫을 때). 안 하면 닫은 뒤에도 CPU 를 계속 쓴다."""
        for j in list(self._jobs.values()):
            try:
                j.cancel.set()                    # 실행 중이면 루프 다음 회차에 중단
                jobs.DETECT_POOL.tryTake(j)       # 큐에만 있던 건 아예 실행 안 함
            except Exception:  # noqa: BLE001
                pass
        self._jobs.clear()

    def done(self, r):
        # QDialog.accept()/reject() 가 모두 거쳐 가는 지점 → 어떤 식으로 닫히든 취소 보장
        self._closed = True
        self._cancel_detect()
        self._cancel_analysis()
        try:
            self.state.changed.disconnect(self._on_state_changed)
        except Exception:  # noqa: BLE001
            pass
        super().done(r)

    def _clip(self, idx):
        for c in self.clips:
            if c["idx"] == idx:
                return c
        return None

    def _det_prog(self, idx, v):
        if self._closed:
            return
        c = self._clip(idx)
        if c:
            c["prog"] = v
            self._update_bar()
            self._refresh()

    def _det_done(self, idx, res):
        if self._closed:
            return
        c = self._clip(idx)
        if c:
            c["measured"] = res
            c["prog"] = None
            self._jobs.pop(idx, None)
            self._update_bar()
            self._refresh()

    def _det_err(self, idx, e):
        if self._closed:
            return
        c = self._clip(idx)
        if c:
            c["prog"] = None
            c["measured"] = None if e == "취소됨" else {}     # 취소 = 다음에 다시 측정 / 오류 = 기본값으로 진행
            self._jobs.pop(idx, None)
            self._refresh()

    def _update_bar(self):
        """측정 단계 전체 막대(블러 클립만 측정)."""
        tg = [c for c in self.clips if c["inc"] and c["mode"] == "blur"]
        if self._phase != "measure" or not tg:
            return
        frac = sum(1.0 if c["measured"] is not None else (c["prog"] or 0.0) for c in tg) / len(tg)
        self.pbar.set(frac, "measure")
        done = sum(1 for c in tg if c["measured"] is not None)
        self._status(f"측정 {done}/{len(tg)}", "measure")

    def _all_detected(self):
        return not any(self._need_measure(c) for c in self.clips)

    def _status(self, text, kind=None):
        self.status.setStyleSheet("color:%s" % (PROG[kind][1] if kind else "#7fd18b"))
        self.status.setText(text)

    # ------------------------------------------------------------ 적용
    def _assignments(self):
        """적용할 (idx, mode, params) 목록. 기존 값 유지면 C 클립 건너뜀."""
        out = []
        for c in self.clips:
            if not c["inc"]:
                continue
            if self.keep_existing and c["c"]:
                continue
            mode, params = ad.to_params(c["mode"], self.strength, c["measured"])
            out.append((c["idx"], mode, params))
        return out

    def _persist_settings(self):
        d = self.state.settings.d
        d["auto_strength"] = self.strength
        d["auto_keep"] = self.keep_existing
        d["auto_keep_keys"] = self.keep_keys
        d["auto_after"] = self.after
        if self.name:
            self.state.settings.render["name"] = self.name
        self.state.settings.save()

    def _busy(self, on):
        for b in (self.b_sample, self.b_correct, self.b_render):
            b.setEnabled(not on)

    def _cancel(self):
        """취소: 진행 중이면 그 작업만 멈추고 창은 그대로(측정·분석·렌더), 아니면 창 닫기."""
        live = [it for it in self._renders if it.state in ("대기", "진행")]
        if live:
            for it in live:
                if it.job is not None:
                    it.job.cancel.set()
            self.status.setText("렌더 취소 중…")
            return
        if self._phase in ("measure", "analyze"):
            self._stop_phase()
            self._status("취소됨", None)
            self.status.setStyleSheet("color:#c8c8c8")
            return
        super().reject()

    def _stop_phase(self):
        """측정·분석 단계를 멈추고 대기 상태로(창 유지)."""
        for t in ("_wait", "_at"):
            tm = getattr(self, t, None)
            if tm is not None:
                tm.stop()
        self._cancel_detect()
        self._cancel_analysis()
        self._phase = None
        for c in self.clips:
            c["prog"] = None
        self.pbar.set(0.0)
        self._busy(False)
        self._refresh()

    def reject(self):
        if any(it.state in ("대기", "진행") for it in self._renders) or self._phase in ("measure", "analyze"):
            self._cancel()                        # 진행 중 Esc = 그 작업 취소(창은 유지)
            return
        super().reject()

    def _go(self, after):
        """버튼 공통: 측정(블러만) → 보정+편집(each) / 보정+렌더(render) / 샘플(sample).
        렌더·샘플은 저장폴더가 필요하다."""
        if self._phase is not None or not any(c["inc"] for c in self.clips):
            return
        out_dir = None
        if after in ("render", "sample"):
            out_dir = self.mw._out_dir()
            if not out_dir:
                return
        if after != "sample":
            self.after = after
        self._persist_settings()
        self._busy(True)
        self._pending = (after, out_dir)
        self._phase = "measure"
        self._start_detect()
        self._update_bar()
        from PySide6.QtCore import QTimer
        self._wait = QTimer(self, interval=150, timeout=self._check_ready)
        self._wait.start()
        self._check_ready()

    def _check_ready(self):
        if self._phase != "measure" or not self._all_detected():
            return
        self._wait.stop()
        self._phase = None
        after, out_dir = self._pending
        if after == "sample":
            self._sample(out_dir)
        else:
            self._apply(out_dir)

    def _apply(self, out_dir):
        assigns = self._assignments()
        self.state.apply_auto(assigns, keep_keys=self.keep_keys)
        mw = self.mw
        idxs = [c["idx"] for c in self.clips if c["inc"]]
        if self.after == "render":
            # 렌더: 창을 띄운 채 진행률 표시 → 다 끝나면 닫힘(오류가 있으면 남아서 알림)
            self._phase = "render"
            self._renders = mw.render_auto(idxs, out_dir, self.name)
            from PySide6.QtCore import QTimer
            self._rt = QTimer(self, interval=250, timeout=self._render_tick)
            self._rt.start()
            self._render_tick()
            return
        # 보정+편집: 이 창에서 타원 분석 + 트랙 계산까지 끝내고 편집 화면으로(편집 화면에서 다시 기다리지 않게)
        self._phase = "analyze"
        srcs = self.state.project.sources
        self._an = [[c, srcs[c["idx"]], False] for c in self.clips if c["inc"] and c["idx"] < len(srcs)]
        for c, s, _ in self._an:
            c["kind"], c["prog"] = "analyze", 0.0
            self.state.ensure_analysis(s)
        from PySide6.QtCore import QTimer
        self._at = QTimer(self, interval=150, timeout=self._an_tick)
        self._at.start()
        self._an_tick()

    def _an_state(self, ent):
        """대상 하나의 진행(0~1)과 상태: run · done · err."""
        c, s, asked = ent
        if s.path in self.state.errors:
            return 1.0, "err"
        a = self.state.analysis(s)
        if a is None:
            self.state.ensure_analysis(s)          # 파일 정보가 늦게 온 경우
            return 0.0, "run"
        if a["state"] == "err":
            return 1.0, "err"
        if a["state"] != "done":
            return 0.95 * float(a.get("p") or 0.0), "run"     # 마지막 5% = 트랙 계산
        if self.state.has_track(s):
            return 1.0, "done"
        if not asked:
            ent[2] = True
            self.state.prepare_track(s, on_error=lambda e, ent=ent: ent.__setitem__(2, "err"))
        if ent[2] == "err":
            return 1.0, "err"
        return 0.95, "run"

    def _an_tick(self):
        if self._phase != "analyze":
            return
        st = [self._an_state(e) for e in self._an]
        for (c, _, _), (v, k) in zip(self._an, st):
            c["prog"] = v if k == "run" else None
        n = max(1, len(st))
        self.pbar.set(sum(v for v, _ in st) / n, "analyze")
        done = sum(1 for _, k in st if k != "run")
        run = [(i, v) for i, (v, k) in enumerate(st) if k == "run"]
        cur = f" ({run[0][0] + 1}번 {int(run[0][1] * 100)}%)" if run else ""
        self._status(f"분석 {done}/{len(st)}{cur}", "analyze")
        self._refresh()
        if run:
            return
        self._at.stop()
        self._phase = None
        bad = [c for (c, _, _), (_, k) in zip(self._an, st) if k == "err"]
        if bad:
            self._status(f"분석 {len(st) - len(bad)}/{len(st)} 완료 · 오류 {len(bad)} — "
                         + ", ".join(c["name"] for c in bad[:3]))
            self.status.setStyleSheet("color:#e06c6c")
            self.pbar.set(0.0)
            self._busy(False)
            return
        self.outcome = "each"
        idxs = [c["idx"] for c, _, _ in self._an]
        self.accept()
        mw = self.mw
        mw.state.select(idxs[0] if idxs else 0)
        mw.flash("AUTO 적용 완료 — 타원 준비됨")

    def _cancel_analysis(self):
        """[보정+편집] 분석 중 취소·창 닫기: 돌고 있는 분석 작업 멈춤."""
        for c, s, _ in self._an:
            a = self.state.an.get((s.path, s.mode))
            if a and a.get("state") == "run" and a.get("job") is not None:
                a["job"].cancel.set()
                try:
                    jobs.ANALYZE_POOL.tryTake(a["job"])
                except Exception:  # noqa: BLE001
                    pass
                self.state.an.pop((s.path, s.mode), None)
                self.state.analysis_changed.emit(s.path)
        self._an = []

    def _render_tick(self):
        if not self._renders:
            self._rt.stop()
            self._phase = None
            self._busy(False)
            return
        by_path = {}
        for it in self._renders:
            by_path[it.src.path] = it
        for c in self.clips:
            it = by_path.get(self.state.project.sources[c["idx"]].path) if c["idx"] < len(self.state.project.sources) else None
            if it is not None:
                c["kind"] = "render"
                c["prog"] = it.p if it.state in ("대기", "진행") else None
                an = self.state.an.get((it.src.path, it.src.mode))
                if c["prog"] is not None and an and an.get("state") == "run" and (an.get("p") or 0) > 0:
                    c["kind"], c["prog"] = "analyze", an["p"]     # 렌더 차례 전 미리 분석 중 = 청록
        n = len(self._renders)
        frac = sum(1.0 if it.state in ("완료", "오류", "취소") else it.p for it in self._renders) / n
        self._set_bar(frac, "render")
        done = sum(1 for it in self._renders if it.state == "완료")
        run = [(i, it.p) for i, it in enumerate(self._renders) if it.state == "진행"]
        self._status(f"렌더 {done}/{n}" + (f" ({run[0][0] + 1}번 {int(run[0][1] * 100)}%)" if run else ""), "render")
        self._refresh()
        if all(it.state in ("완료", "오류", "취소") for it in self._renders):
            self._rt.stop()
            bad = [it for it in self._renders if it.state != "완료"]
            if not bad:
                self.outcome = "render"
                self.accept()
                return
            err = sum(1 for it in bad if it.state == "오류")
            self.status.setText(f"렌더 {done}/{n} 완료 · " + (f"오류 {err}" if err else "취소됨"))
            self.status.setStyleSheet("color:#e06c6c" if err else "color:#c8c8c8")
            self._phase = None
            self.pbar.set(0.0)
            self._busy(False)
            self._renders = []
            for c in self.clips:
                c["prog"] = None
            self._refresh()

    def _set_bar(self, frac, kind="render"):
        self.pbar.set(frac, kind)

    def _sample(self, out_dir):
        """샘플: (측정 뒤) 샘플 설정 창 → 만드는 동안 이 창 목록·막대에 호박색 진행 → 끝나면 AUTO 창으로 돌아와 대기."""
        inc = [c for c in self.clips if c["inc"]]
        # 샘플은 지금 격자 값을 임시 적용한 복사본으로
        srcs = []
        for c in inc:
            s = copy.deepcopy(self.state.project.sources[c["idx"]])
            if not (self.keep_existing and c["c"]):
                mode, params = ad.to_params(c["mode"], self.strength, c["measured"])
                s.mode = mode
                s.params_for(mode).update(params)
            srcs.append(s)
        self._phase = "sample"
        self._sample_clips = inc
        dlg = SampleDialog(self, self.state, srcs, out_dir, self.name, on_prog=self._sample_prog)
        dlg.move(self.geometry().center() - dlg.rect().center())
        dlg.exec()
        self._phase = None
        for c in inc:
            c["prog"] = None
            self.state.clear_busy(self.state.project.sources[c["idx"]].path, "sample")
        self.pbar.set(0.0)
        self._busy(False)
        self._refresh()
        if dlg.saved:
            self._status(f"샘플 {len(dlg.saved)}장 저장")
        else:
            self.status.setText("")

    def _sample_prog(self, v, count):
        """샘플 전체 진행 v(0~1) → 지금 클립 k 의 'a/count' 표시."""
        inc = self._sample_clips
        total = max(1, len(inc))
        k = min(total - 1, int(v * total))
        within = v * total - k
        got = min(count, int(within * count + 1e-6))
        for j, c in enumerate(inc):
            path = self.state.project.sources[c["idx"]].path
            c["kind"] = "sample"
            if j == k and v < 1.0:
                c["prog"], c["sub"] = within, f"샘플 {got}/{count}"
                self.state.set_busy(path, "sample", within, f"샘플 {got}/{count}")
            else:
                c["prog"], c["sub"] = None, None
                self.state.clear_busy(path, "sample")
        self.pbar.set(v, "sample")
        self._status(f"샘플 {min(k + (1 if v >= 1.0 else 0), total)}/{total}"
                     + (f" ({k + 1}번 {got}/{count})" if v < 1.0 else ""), "sample")
        self._refresh()


# ------------------------------------------------------------ 샘플 창
class SampleDialog(DragMove, QDialog):
    def __init__(self, parent, state, srcs, out_dir, name, on_prog=None):
        super().__init__(parent)
        self.state = state
        self.srcs = srcs
        self.on_prog = on_prog          # (전체 0~1, 클립당 장수) → AUTO 창 목록·막대(호박색)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setModal(True)
        self.setStyleSheet("SampleDialog{background:#262626;border:1px solid #555;border-radius:6px}QLabel{color:#ddd}")
        self.setFixedWidth(560)
        ss = state.settings.d.get("sample", {})
        self.opts = {"out_dir": out_dir, "name": name, "size": ss.get("size", 1920),
                     "fmt": ss.get("fmt", "png8"), "num": ss.get("num", True), "date": ss.get("date", False),
                     "count": 6}
        self.saved = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        def field(label, inner):
            r = QHBoxLayout()
            lb = QLabel(label)
            lb.setFixedWidth(76)
            lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            r.addWidget(lb)
            if isinstance(inner, QWidget):
                r.addWidget(inner, 1)
            else:
                r.addLayout(inner, 1)
            w = QWidget()
            w.setLayout(r)
            return w
        drow = QHBoxLayout()
        self.dir = QLineEdit(self.opts["out_dir"])
        self.dir.setStyleSheet("background:#1a1a1a;border:1px solid #555;border-radius:3px;padding:0 8px;color:#eee")
        self.dir.setFixedHeight(28)
        drow.addWidget(self.dir, 1)
        drow.addWidget(_b("선택…", on=self._browse))
        root.addWidget(field("저장폴더", drow))
        self.seg_size, _ = _seg([("1280", "1280"), ("1920", "1920"), ("2560", "2560"), ("4k", "4K")],
                                {1280: "1280", 1920: "1920", 2560: "2560"}.get(self.opts["size"], "4k"),
                                self._on_size, label="크기")
        root.addWidget(self.seg_size)
        self.seg_fmt, _ = _seg([("png8", "PNG 8bit"), ("png16", "PNG 16bit"), ("jpg", "JPG")],
                               self.opts["fmt"], self._on_fmt, label="형식")
        root.addWidget(self.seg_fmt)
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color:#3a3a3a")
        root.addWidget(div)
        self.name_edit = QLineEdit(self.opts["name"])
        self.name_edit.setFixedWidth(180)
        self.name_edit.setPlaceholderText("MOSCO")
        self.name_edit.setStyleSheet("background:#1a1a1a;border:1px solid #555;border-radius:3px;padding:0 8px;color:#eee")
        self.name_edit.setFixedHeight(28)
        self.name_edit.textChanged.connect(self._preview)
        root.addWidget(field("이름", self.name_edit))
        trow = QHBoxLayout()
        trow.setSpacing(6)
        self.t_date = _b("날짜시간", w=76, on=self._toggle)     # DTME 처럼 뺌(숨김, 값은 항상 끔)
        self.t_date.setCheckable(True)
        self.t_date.setChecked(False)
        self.t_date.hide()
        self.opts["date"] = False
        self.t_num = _b("번호", w=64, on=self._toggle)
        self.t_num.setCheckable(True)
        self.t_num.setChecked(self.opts["num"])
        trow.addWidget(self.t_num)
        trow.addStretch(1)
        root.addWidget(field("붙이기", trow))
        self.ex = QLabel("")
        self.ex.setStyleSheet("color:#c4cad1;font-family:Consolas,monospace")
        root.addWidget(field("", self.ex))
        brow = QHBoxLayout()
        brow.addStretch(1)
        brow.addWidget(_b("취소", w=88, h=32, on=self.reject))
        self.b_go = _b("샘플", w=88, h=32, style=ORANGE, on=self._go)
        brow.addWidget(self.b_go)
        bw = QWidget()
        bw.setLayout(brow)
        root.addWidget(bw)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#7fd18b")
        root.addWidget(self.status)
        self._toggle()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "저장폴더", self.dir.text())
        if d:
            self.dir.setText(d)

    def _on_size(self, k):
        self.opts["size"] = {"1280": 1280, "1920": 1920, "2560": 2560, "4k": 3840}[k]
        self._preview()

    def _on_fmt(self, k):
        self.opts["fmt"] = k
        self._preview()

    def _toggle(self):
        self.opts["num"] = self.t_num.isChecked()
        self.opts["date"] = self.t_date.isChecked()
        for b, on in ((self.t_num, self.opts["num"]), (self.t_date, self.opts["date"])):
            set_on(b, on)                                    # 켜짐 = 파랑(QSS)
        self._preview()

    def _preview(self):
        self.opts["name"] = self.name_edit.text().strip()
        s = self.srcs[0]
        from . import sample as smp
        a = smp.filename(s, self.opts, 1)
        b = smp.filename(s, self.opts, 6)
        self.ex.setText(f"{a} … {b.rsplit('_', 1)[-1] if self.opts['num'] else b}")

    def _go(self):
        self.opts["out_dir"] = self.dir.text().strip()
        self.opts["name"] = self.name_edit.text().strip()
        self.state.settings.d["sample"] = {"size": self.opts["size"], "fmt": self.opts["fmt"],
                                           "num": self.opts["num"], "date": self.opts["date"]}
        self.state.settings.save()
        self.b_go.setEnabled(False)
        self.status.setText("샘플 만드는 중…")
        from . import ff as ff_
        from . import sample as smp
        total = len(self.srcs)
        self._done = 0
        self._saved = []

        def run(prog, cancel):
            allp = []
            for k, s in enumerate(self.srcs):
                info = ff_.probe(s.path)
                allp += smp.render_samples(s, info, self.opts, lambda v, k=k: prog((k + v) / total), cancel)
            return allp
        j = jobs.Job(run, with_progress=True)
        j.signals.progress.connect(self._prog)
        j.signals.done.connect(self._finished)
        j.signals.error.connect(lambda e: (self.status.setText(f"오류: {e}"), self.b_go.setEnabled(True)))
        jobs.submit(jobs.RENDER_POOL, j)

    def _prog(self, v):
        self.status.setText(f"샘플 {int(v * 100)}%")
        if self.on_prog:
            self.on_prog(v, int(self.opts.get("count", 6)))

    def _finished(self, paths):
        self.saved = paths
        self.status.setText(f"{len(paths)}장 저장 완료")
        import os
        if paths and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            from pathlib import Path
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))
        self.accept()


_ = (ff, btn)
