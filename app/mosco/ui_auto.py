"""AUTO 창·샘플 창 (DTME AUTO_UI.md 틀). 분류·값은 MOSCO 것.

AUTO: 체크한 클립을 장면 인식(autodetect) → 속성 격자로 고치고 → 적용(바로 렌더 / 개별 보정) 또는 샘플.
"""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import autodetect as ad
from . import ff, jobs
from .ui_widgets import btn

RED = "background:#c0392b;border-color:#d64535;color:#fff;font-weight:700"
ORANGE = "background:#d9822b;border-color:#e8964a;color:#fff;font-weight:700"
GREY = "background:#c8c8c8;border-color:#b0b0b0;color:#222;font-weight:700"
ON = "background:#2f4f78;border-color:#4a8fe0;color:#fff"
AUTO_ON = "border:1px solid #f2c94c;color:#f2c94c;background:#3a3420"
AUTO_OFF = "border:1px dashed #666;color:#bbb;background:#2e2e2e"


def _b(text, w=0, h=28, style="", tip="", on=None):
    b = QPushButton(text)
    b.setFixedHeight(h)
    if w:
        b.setFixedWidth(w)
    b.setFocusPolicy(Qt.NoFocus)
    if style:
        b.setStyleSheet("QPushButton{%s;border-radius:3px;padding:0 10px}" % style)
    if tip:
        b.setToolTip(tip)
    if on:
        b.clicked.connect(on)
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
    hb.setSpacing(0)
    box.setStyleSheet("background:#2a2a2a;border:1px solid #555;border-radius:3px")
    grp = QButtonGroup(box)
    btns = {}
    for i, (k, t) in enumerate(opts):
        b = QPushButton(t)
        b.setCheckable(True)
        b.setFixedHeight(28)
        b.setFocusPolicy(Qt.NoFocus)
        b.setStyleSheet("QPushButton{background:#2a2a2a;border:none;color:#ddd;padding:0 14px}"
                        "QPushButton:checked{background:#2f4f78;color:#fff}")
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
        self.setFixedHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        self.chk = QCheckBox()
        self.chk.setChecked(True)
        lay.addWidget(self.chk)
        self.name = QLabel(name)
        self.name.setFixedWidth(104)
        self.name.setStyleSheet("font-weight:700")
        lay.addWidget(self.name)
        self.c = QLabel("")
        self.c.setFixedWidth(12)
        self.c.setStyleSheet("color:#f2c94c")
        lay.addWidget(self.c)
        self.preset = QLabel("자동")
        lay.addWidget(self.preset)
        self.sub = QLabel("")
        self.sub.setStyleSheet("color:#c4cad1;font-size:11px")
        lay.addWidget(self.sub)
        lay.addStretch(1)
        self.out = QLabel("HQ")
        self.out.setStyleSheet("background:#3a3a3a;border-radius:2px;padding:1px 6px;font-size:10px")
        lay.addWidget(self.out)
        self.sel = False
        self.prog = None
        self._paint()

    def set_state(self, included, sel, c, preset, sub, out, prog, detected_auto):
        self.sel = sel
        self.prog = prog
        self.chk.blockSignals(True)
        self.chk.setChecked(included)
        self.chk.blockSignals(False)
        self.c.setText("C" if c else "")
        self.preset.setText(preset)
        self.preset.setStyleSheet("color:%s" % ("#c4cad1" if detected_auto else "#fff"))
        self.sub.setText(sub)
        self.out.setText(out)
        self.name.setStyleSheet("font-weight:700;color:%s" % ("#fff" if included else "#8a8a8a"))
        self._paint()

    def _paint(self):
        if self.sel:
            bg = "#2f4f78"
        elif self.prog is not None:
            bg = "#3c3c3c"
        else:
            bg = "#6b6b6b" if self.chk.isChecked() else "#3c3c3c"
        extra = ""
        if self.prog is not None:
            pct = int(self.prog * 100)
            extra = (f",stop:0 #5d7896,stop:{self.prog:.3f} #5d7896,"
                     f"stop:{min(0.999, self.prog + 0.001):.3f} {bg},stop:1 {bg}")
            self.setStyleSheet("ClipRow{border-radius:3px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0%s)}" % extra)
        else:
            self.setStyleSheet("ClipRow{border-radius:3px;background:%s}" % bg)
        _ = pct if self.prog is not None else 0

    def mousePressEvent(self, e):
        if self.childAt(e.position().toPoint()) is self.chk:
            return super().mousePressEvent(e)
        self.clicked.emit(self.idx, e.modifiers())


class AutoDialog(QDialog):
    def __init__(self, parent, state):
        super().__init__(parent)
        self.state = state
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setModal(True)
        self.setStyleSheet("AutoDialog{background:#262626;border:1px solid #555;border-radius:6px}"
                           "QLabel{color:#ddd;font-size:12px}")
        self.setFixedSize(1040, 720)
        # 클립 상태
        rs = state.settings.render
        self.name = (rs.get("name") or "").strip()
        self.strength = state.settings.d.get("auto_strength", "mid")
        self.keep_existing = state.settings.d.get("auto_keep", True)
        self.keep_keys = state.settings.d.get("auto_keep_keys", True)
        self.after = state.settings.d.get("auto_after", "render")
        self.clips = []
        for i, s in enumerate(state.project.sources):
            self.clips.append({"idx": i, "name": s.name, "inc": True, "mode": s.mode,
                               "measured": None, "prog": None, "c": s.custom, "outp": "HQ"})
        self.selected = {0} if self.clips else set()
        self.rows = []
        self._jobs = {}
        self._closed = False
        self._build()
        self._refresh()
        self._start_detect()

    # ------------------------------------------------------------ 구성
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)
        mid = QHBoxLayout()
        mid.setSpacing(16)
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
        self.seg_work, self.set_work = _seg([("beauty", "뷰티"), ("blur", "블러")], "beauty",
                                            self._on_work, label="작업")
        right.addWidget(self.seg_work)
        wl = QLabel("선택한 클립의 작업을 정합니다. 장면 자동판정은 쓰지 않습니다(부정확).")
        wl.setStyleSheet("color:#8a8a8a;font-size:11px")
        wl.setWordWrap(True)
        right.addWidget(wl)
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color:#3a3a3a")
        right.addWidget(div)
        self.seg_strength, self.set_strength = _seg([("weak", "약"), ("mid", "중"), ("strong", "강")], self.strength,
                                                    self._on_strength, label="강도")
        right.addWidget(self.seg_strength)
        self.seg_out, self.set_out = _seg([("hq", "422 HQ"), ("std", "422"), ("lt", "LT")],
                                          "hq", self._on_out, label="출력")
        right.addWidget(self.seg_out)
        nrow = QHBoxLayout()
        nlb = QLabel("이름")
        nlb.setFixedWidth(76)
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
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("background:#1c1c1c;border:1px solid #333;border-radius:3px;padding:8px 12px;color:#ddd")
        self.detail.setFixedWidth(470)
        right.addWidget(self.detail)
        rw = QWidget()
        rw.setFixedWidth(470)
        rw.setLayout(right)
        mid.addWidget(rw)
        root.addLayout(mid, 1)
        # 아래
        self.pbar = QFrame()
        self.pbar.setFixedHeight(6)
        self.pbar.setStyleSheet("background:#2a2a2a;border-radius:3px")
        root.addWidget(self.pbar)
        exrow = QHBoxLayout()
        exlb = QLabel("기존 값")
        exlb.setFixedWidth(52)
        exlb.setAlignment(Qt.AlignRight)
        exrow.addWidget(exlb)
        w1, self.set_keep = _seg([("keep", "기존 보정 유지"), ("drop", "기존 보정 무시")],
                                 "keep" if self.keep_existing else "drop", self._on_keep, label="")
        exrow.addWidget(w1)
        w2, self.set_keepk = _seg([("keys", "키프레임 유지"), ("autok", "키프레임 자동")],
                                  "keys" if self.keep_keys else "autok", self._on_keepk, label="")
        exrow.addWidget(w2)
        exrow.addStretch(1)
        exw = QWidget()
        exw.setLayout(exrow)
        root.addWidget(exw)
        btmrow = QHBoxLayout()
        btmrow.addWidget(_b("취소", w=88, h=32, on=self.reject))
        btmrow.addStretch(1)
        btmrow.addWidget(_b("샘플", w=88, h=32, style=ORANGE, tip="렌더 없이 샘플 PNG 만", on=self._sample))
        self.b_correct = _b("보정만", w=96, h=32, style=GREY,
                            tip="적용 + 분석(타원 생성). 렌더는 안 함 — 개별 보정 이어서", on=lambda: self._go("each"))
        self.b_render = _b("보정+렌더", w=110, h=32, style=RED,
                           tip="적용하고 바로 렌더 시작", on=lambda: self._go("render"))
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
        return c["outp"] + ("+α" if self._resolved_work(c) == "blur" else "")

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
            if c["prog"] is not None:
                sub = f"측정 중 {int(c['prog'] * 100)}%"
            elif m and c["mode"] == "blur":
                bits = []
                if m.get("min_face"):
                    bits.append(f"최소 얼굴 {m['min_face']}px")
                bits.append(f"움직임 {m.get('motion', 0)}")
                sub = "측정: " + " · ".join(bits)
            r.set_state(c["inc"], c["idx"] in self.selected, c["c"], lbl, sub, self._out_chip(c), c["prog"], False)
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
            keys = [("강도", params["strength"]), ("질감 보존", params["texture"]), ("그레인", params["grain"]),
                    ("피부 범위", params["skin_range"]), ("색 통일", params["unify"]),
                    ("타원 크기", f"{params['size']}%"), ("페더", f"{params['feather']}%"),
                    ("목 확장", params["neck"]), ("떨림 보정", params["stabilize"]),
                    ("눈·입 제외", "켬" if params.get("features", True) else "끔")]
        else:
            keys = [("민감도", params["sensitivity"]), ("최소 크기", f"{params['min_size']}px"),
                    ("몸으로 머리", "켬" if params["body"] else "끔"), ("타원 여유", f"{params['margin']}%"),
                    ("페더", f"{params['feather']}%"), ("놓침 유지", f"{params['hold']}f"),
                    ("다시 잇기", f"{params['link']}f"), ("블러 강도", params["strength"]),
                    ("떨림 보정", params["stabilize"]), ("모자이크", "켬" if params["kind"] == "mosaic" else "끔")]
        rows = "   ".join(f"{k} {v}" for k, v in keys)
        return f"● {title}\n{rows}"

    # ------------------------------------------------------------ 측정(백그라운드)
    def _start_detect(self):
        for c in self.clips:
            if not c["inc"] or c["measured"] is not None:
                continue
            info = self.state.info(self.state.project.sources[c["idx"]].path)
            if info is None:
                continue
            c["prog"] = 0.0
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
            self._update_bar()
            self._refresh()

    def _det_err(self, idx, e):
        if self._closed:
            return
        c = self._clip(idx)
        if c:
            c["prog"] = None
            c["measured"] = {}
            self._refresh()

    def _update_bar(self):
        running = [c for c in self.clips if c["prog"] is not None]
        if running:
            avg = sum(c["prog"] for c in running) / len(running)
            done = sum(1 for c in self.clips if c["inc"] and c["measured"] is not None)
            total = sum(1 for c in self.clips if c["inc"])
            frac = (done + avg) / max(1, total)
            self.pbar.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4a8fe0,stop:%.3f #4a8fe0,"
                "stop:%.3f #2a2a2a,stop:1 #2a2a2a);border-radius:3px" % (frac, min(0.999, frac + 0.001)))
        else:
            self.pbar.setStyleSheet("background:#2a2a2a;border-radius:3px")

    def _all_detected(self):
        return all(c["measured"] is not None for c in self.clips if c["inc"])

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

    def _go(self, after):
        """보정만(each) / 보정+렌더(render). 렌더만 저장폴더가 필요하다."""
        if not any(c["inc"] for c in self.clips):
            return
        out_dir = None
        if after == "render":
            out_dir = self.parent()._out_dir()
            if not out_dir:
                return
        self.after = after
        self._persist_settings()
        for b in (self.b_correct, self.b_render):
            b.setEnabled(False)
        (self.b_render if after == "render" else self.b_correct).setText("측정 대기…")
        # 측정이 끝날 때까지 기다렸다가 적용
        if self._all_detected():
            self._apply_and_close(out_dir)
        else:
            self._pending_dir = out_dir
            from PySide6.QtCore import QTimer
            self._wait = QTimer(self, interval=200, timeout=self._check_ready)
            self._wait.start()

    def _check_ready(self):
        if self._all_detected():
            self._wait.stop()
            self._apply_and_close(self._pending_dir)

    def _apply_and_close(self, out_dir):
        assigns = self._assignments()
        self.state.apply_auto(assigns, keep_keys=self.keep_keys)
        self.accept()
        mw = self.parent()
        if self.after == "render":
            mw.render_auto([a[0] for a in assigns], out_dir, self.name)
        else:
            # 개별 보정: 적용값으로 분석(트래킹)을 돌려 타원이 생기게 한다
            for idx, _, _ in assigns:
                if 0 <= idx < len(mw.state.project.sources):
                    mw.state.analyze_now(mw.state.project.sources[idx])
            mw.state.select(assigns[0][0] if assigns else 0)
            mw.flash("AUTO 적용 완료 — 분석 시작(타원 생성)")

    def _sample(self):
        inc = [c for c in self.clips if c["inc"]]
        if not inc:
            return
        out_dir = self.parent()._out_dir()
        if not out_dir:
            return
        self._persist_settings()
        # 샘플은 지금 격자 값을 임시 적용한 복사본으로
        srcs = []
        for c in inc:
            s = copy.deepcopy(self.state.project.sources[c["idx"]])
            if not (self.keep_existing and c["c"]):
                mode, params = ad.to_params(c["mode"], self.strength, c["measured"])
                s.mode = mode
                (s.beauty if mode == "beauty" else s.blur).update(params)
            srcs.append(s)
        dlg = SampleDialog(self.parent(), self.state, srcs, out_dir, self.name)
        self.accept()
        dlg.exec()


# ------------------------------------------------------------ 샘플 창
class SampleDialog(QDialog):
    def __init__(self, parent, state, srcs, out_dir, name):
        super().__init__(parent)
        self.state = state
        self.srcs = srcs
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setModal(True)
        self.setStyleSheet("SampleDialog{background:#262626;border:1px solid #555;border-radius:6px}QLabel{color:#ddd}")
        self.setFixedWidth(560)
        ss = state.settings.d.get("sample", {})
        self.opts = {"out_dir": out_dir, "name": name, "size": ss.get("size", 1920),
                     "fmt": ss.get("fmt", "png8"), "num": ss.get("num", True), "date": ss.get("date", False),
                     "count": 6}
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
        self.t_date = _b("날짜시간", w=76, on=self._toggle)
        self.t_date.setCheckable(True)
        self.t_date.setChecked(self.opts.get("date", False))
        self.t_num = _b("번호", w=64, on=self._toggle)
        self.t_num.setCheckable(True)
        self.t_num.setChecked(self.opts["num"])
        trow.addWidget(self.t_date)
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
            b.setStyleSheet("QPushButton{%s;border-radius:3px;padding:0 10px}" % (ON if on else
                            "background:#3a3a3a;border:1px solid #555;color:#e6e6e6"))
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
        j.signals.progress.connect(lambda v: self.status.setText(f"샘플 {int(v * 100)}%"))
        j.signals.done.connect(self._finished)
        j.signals.error.connect(lambda e: (self.status.setText(f"오류: {e}"), self.b_go.setEnabled(True)))
        jobs.submit(jobs.RENDER_POOL, j)

    def _finished(self, paths):
        self.status.setText(f"{len(paths)}장 저장 완료")
        import os
        if paths and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            from pathlib import Path
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent)))
        self.accept()


_ = (ff, btn)
