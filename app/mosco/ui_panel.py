"""왼쪽 보정 패널: 리셋·기본값·프리셋 / 뷰티|블러 / 보정|원본|반반 · 타원 · 키 · ID / 묶음별 슬라이더."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import autodetect as ad
from . import project as P
from .ui_widgets import BTN, BTN_MAIN, DragMove, Section, Seg, SliderRow, Toggle, btn, set_on, tone

KEYS = [("off", "끔"), ("final", "최종 키"), ("skin", "피부 키"), ("alpha", "타원 알파")]


OV_SLIDERS = [("size", "크기", 50, 250, "%"), ("feather", "페더", 0, 50, "%"), ("strength", "강도", 0, 100, "")]


class ControlPanel(QWidget):
    view_changed = Signal()          # 보정/원본/반반·타원·키·ID
    analyze_clicked = Signal()       # [분석] — 현재 클립 분석(패널 하단 큰 버튼)
    pick_skin = Signal(bool)
    tool = Signal(object)            # None | "add" | "merge"
    del_key = Signal()
    off = Signal(str)                # from | to | all | on | remove
    minus = Signal(bool)             # 선택 타원: 블러 제외(True) / 블러(False)
    ov_pressed = Signal()
    ov_changed = Signal(str, object)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setMinimumWidth(340)          # DTME 보정 패널 폭에 맞춤(이전 300은 내용이 잘림)
        self.setStyleSheet("ControlPanel{background:#202020}")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        r = QHBoxLayout()
        r.setSpacing(4)
        r.addWidget(btn("리셋", "조정값 초기화(공장값)", on_click=self.reset))
        r.addWidget(btn("기본값", "이 클립을 기본값으로", on_click=self.to_default))
        r.addStretch(1)
        r.addWidget(btn("기본값으로 저장", "이 클립 값을 기본값으로", w=112, on_click=self.save_default))
        outer.addLayout(r)

        r = QHBoxLayout()
        r.setSpacing(6)
        self.b_preset = btn("프리셋", "프리셋 창 (작업·강도 / 저장된 프리셋, 오른쪽 클릭 = 이름 바꾸기·삭제)",
                            on_click=self._open_grid)
        self.b_preset.setCheckable(True)
        self.b_preset.setMinimumWidth(0)
        self.b_preset.setMaximumWidth(16777215)
        from PySide6.QtWidgets import QSizePolicy
        self.b_preset.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.b_preset.setStyleSheet("text-align:left; padding-left:10px")
        r.addWidget(self.b_preset, 1)
        r.addWidget(btn("저장…", "현재 값을 프리셋으로", w=56, on_click=self._preset_save))
        outer.addLayout(r)

        self._sync = False               # refresh 중 토글 신호 무시
        self.mode = Seg([("beauty", "뷰티"), ("blur", "블러")])
        self.mode.changed.connect(self._on_workmode)
        outer.addWidget(self.mode)
        # 블러 대상(v0.7.31): 얼굴 / 간판 — 둘 다 켤 수 있음(동시 블러). 활성 편집 종류 = 마지막 켠 쪽.
        rt = QHBoxLayout()
        rt.setSpacing(6)
        lt = QLabel("대상")
        lt.setStyleSheet("color:#aaa")
        self.t_face = Toggle("얼굴", w=66, tip="얼굴 블러")
        self.t_sign = Toggle("간판", w=66, tip="간판 블러(글자 검출 → 원근 사각형). 얼굴과 함께 켤 수 있음")
        self.t_face.toggled2.connect(lambda on: self._set_target("blur", on))
        self.t_sign.toggled2.connect(lambda on: self._set_target("sign", on))
        rt.addWidget(lt)
        rt.addWidget(self.t_face)
        rt.addWidget(self.t_sign)
        rt.addStretch(1)
        self.w_target = QWidget()
        self.w_target.setLayout(rt)
        outer.addWidget(self.w_target)

        r = QHBoxLayout()
        r.setSpacing(6)
        self.view = Seg([("fx", "보정"), ("orig", "원본"), ("split", "반반")])
        self.view.set("fx")
        self.view.changed.connect(lambda _: self.view_changed.emit())
        self.view.setToolTip("보기: 보정 / 원본 / 반반  (B = 원본 ↔ 보정)")
        r.addWidget(self.view, 1)
        self.t_ell = Toggle("타원", w=50, tip="타원 표시")
        self.t_ell.setChecked(True)
        self.t_ell.toggled2.connect(lambda _: self.view_changed.emit())
        r.addWidget(self.t_ell)
        self.key = QToolButton()
        self.key.setFixedSize(54, 26)
        self.key.setPopupMode(QToolButton.InstantPopup)
        self.key.setFocusPolicy(Qt.NoFocus)
        self.key.setToolTip("키 보기")
        self.key_mode = "off"
        self._key_menu()
        r.addWidget(self.key)
        self.t_id = Toggle("ID", w=40, tip="ID 표시")
        self.t_id.setChecked(True)
        self.t_id.toggled2.connect(lambda _: self.view_changed.emit())
        r.addWidget(self.t_id)
        outer.addLayout(r)

        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setFrameShape(QScrollArea.NoFrame)
        sc.setStyleSheet("QScrollArea{background:#202020}")
        body = QWidget()
        body.setObjectName("pbody")
        body.setStyleSheet("QWidget#pbody{background:#202020}")
        self.bl = QVBoxLayout(body)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(6)
        sc.setWidget(body)
        outer.addWidget(sc, 1)

        self.rows = {}
        # 뷰티
        self.sec_beauty = Section("뷰티")
        rr = QHBoxLayout()
        rr.setSpacing(4)
        rr.addStretch(1)
        self.b_pick = btn("피부색 찍기", "모니터에서 피부를 클릭 (W)", w=88)
        self.b_pick.setCheckable(True)
        self.b_pick.toggled.connect(self._pick)
        rr.addWidget(self.b_pick)
        rr.addWidget(btn("자동", "피부색 자동(얼굴에서)", w=48, on_click=lambda: self.state.set_param("skin_ref", None, undo=True)))
        self.b_addskin = btn("피부색 추가", "피부색이 아닌 화장(섀도·볼터치 등)을 모니터에서 클릭 → 그 색도 피부로 보정. 최대 2개.\n"
                             "검은색(아이라인·속눈썹)은 찍지 마세요 — 머리카락·눈썹까지 피부가 됩니다", w=88)
        self.b_addskin.setCheckable(True)
        self.b_addskin.toggled.connect(self._pick_extra)
        rr.addWidget(self.b_addskin)
        w = QWidget()
        w.setLayout(rr)
        self.sec_beauty.add(w)
        # 피부색 칩: 기준색 1 + 추가색 2 (v0.7.63)
        self.pick_target = "base"
        self.auto_ref = None                  # 자동일 때 마지막으로 계산된 피부색(표시용)
        cr_ = QHBoxLayout()
        cr_.setSpacing(4)
        cr_.addStretch(1)
        lb = QLabel("피부색")
        lb.setStyleSheet("color:#9a9a9a;font-size:11px")
        cr_.addWidget(lb)
        self.chip_base = QPushButton()
        self.chips_extra = [QPushButton(), QPushButton()]
        for k, c in enumerate([self.chip_base] + self.chips_extra):
            c.setFixedSize(22, 18)
            c.setFocusPolicy(Qt.NoFocus)
            if k == 1:
                plus = QLabel("+")
                plus.setStyleSheet("color:#9a9a9a;font-size:11px")
                cr_.addWidget(plus)
            cr_.addWidget(c)
        self.chip_base.setEnabled(False)
        for k, c in enumerate(self.chips_extra):
            c.clicked.connect(lambda _=False, k=k: self._del_extra(k))
        wc = QWidget()
        wc.setLayout(cr_)
        self.sec_beauty.add(wc)
        self._rows(self.sec_beauty, "beauty", P.BEAUTY_SLIDERS)
        for k, tip in (("soft", "소프트: 피부만 아주 약하게 흐림"), ("toneup", "톤업: 피부 밝기를 올림(밝은 곳은 덜)"),
                       ("shine", "번들거림 줄이기: 피부의 밝게 번들거리는 부분을 누름"),
                       ("tint", "톤: 피부색을 옮김 — 왼쪽(−) 노랑 · 오른쪽(+) 분홍"),
                       ("eye_hole", "눈 제외 크기: 100 = 기본, 0 = 눈 구멍 없음"),
                       ("wrinkle", "주름 메우기: 어두운 골을 주변 밝기까지 메움")):
            if ("beauty", k) in self.rows:
                self.rows[("beauty", k)].setToolTip(tip)
        self.sec_bell = Section("타원")
        self._rows(self.sec_bell, "beauty", P.BEAUTY_ELLIPSE)
        # 블러
        self.sec_blur = Section("블러")
        self.kind = Seg([("blur", "블러"), ("mosaic", "모자이크")])
        self.kind.changed.connect(lambda k: self.state.set_param("kind", k, undo=True))
        self.sec_blur.add(self.kind)
        self._rows(self.sec_blur, "blur", P.BLUR_SLIDERS)
        self.sec_xell = Section("타원")
        self._rows(self.sec_xell, "blur", P.BLUR_ELLIPSE)
        self.sec_det = Section("검출")
        self._rows(self.sec_det, "blur", P.BLUR_DETECT)
        self.t_body = Toggle("몸으로 머리 찾기", w=130, tip="뒤통수·옆얼굴: 사람 몸에서 머리 위치 추정")
        self.t_body.toggled2.connect(lambda v: self.state.set_param("body", v, undo=True))
        rb = QHBoxLayout()
        rb.addStretch(1)
        rb.addWidget(self.t_body)
        wb = QWidget()
        wb.setLayout(rb)
        self.sec_det.add(wb)
        # 뷰티: 눈·입 제외
        self.t_feat = Toggle("눈·입 제외", w=88, tip="눈·눈썹·입은 보정하지 않음. 눈 구멍 크기는 [눈 제외 크기] 슬라이더로(0 = 눈 구멍 없음)")
        self.t_feat.toggled2.connect(lambda v: self.state.set_param("features", v, undo=True))
        rr2 = QHBoxLayout()
        rr2.addStretch(1)
        rr2.addWidget(self.t_feat)
        wf = QWidget()
        wf.setLayout(rr2)
        self.sec_beauty.add(wf)
        # 타원 편집(공통)
        # 간판 블러(얼굴 블러와 같은 구성 + 최대 개수)
        self.sec_sblur = Section("블러")
        self.skind = Seg([("blur", "블러"), ("mosaic", "모자이크")])
        self.skind.changed.connect(lambda k: self.state.set_param("kind", k, undo=True))
        self.sec_sblur.add(self.skind)
        self._rows(self.sec_sblur, "sign", P.SIGN_SLIDERS)
        self.sec_sell = Section("사각형")
        self._rows(self.sec_sell, "sign", P.SIGN_ELLIPSE)
        self.sec_sdet = Section("검출")
        self._rows(self.sec_sdet, "sign", P.SIGN_DETECT)
        self.sign_secs = (self.sec_sblur, self.sec_sell, self.sec_sdet)
        self.sec_edit = Section("타원 편집")
        r1 = QHBoxLayout()
        r1.setSpacing(4)
        self.b_add = Toggle("타원 추가", w=72, tip="모니터에서 드래그해 새 타원. 켠 채로 계속 그림 (A)")
        self.b_del = Toggle("삭제", w=48, tip="켜고 타원을 클릭해 삭제(오검출 제거). 자동 타원은 전 구간 숨김\n선택한 타원: Del = 삭제 · Shift+Del = 현재 프레임 키 삭제 · X = 블러 제외 타원으로 · Esc = 선택 해제")
        self.b_merge = Toggle("합치기", w=54, tip="선택한 타원과 같은 사람: 다른 프레임의 타원을 클릭")
        self.b_add.toggled2.connect(lambda v: self._tool("add", v))
        self.b_del.toggled2.connect(lambda v: self._tool("del", v))
        self.b_merge.toggled2.connect(lambda v: self._tool("merge", v))
        self.b_delkey = btn("키 삭제", "고른 키 삭제 — 오토 키(흰 ◆) = 펴기, 수동 키(노랑) = 그 키. 고른 키 없으면 현재 프레임 (Shift+Del)", w=58, on_click=self.del_key.emit)
        self.b_off = QToolButton()
        self.b_off.setText("끄기 ▾")
        self.b_off.setFixedSize(56, 26)
        self.b_off.setPopupMode(QToolButton.InstantPopup)
        self.b_off.setFocusPolicy(Qt.NoFocus)
        m = QMenu(self)
        for k, t in (("from", "여기부터 끄기"), ("to", "여기까지 끄기"), ("all", "전체 끄기"), ("on", "다시 켜기"),
                     ("remove", "추가 타원 삭제")):
            m.addAction(t, lambda k=k: self.off.emit(k))
        self.b_off.setMenu(m)
        for w in (self.b_add, self.b_del, self.b_merge, self.b_delkey, self.b_off):
            r1.addWidget(w)
        r1.addStretch(1)
        w1 = QWidget()
        w1.setLayout(r1)
        self.sec_edit.add(w1)
        # 블러: 선택 타원 [블러 | 제외] — 제외 = 이 영역은 블러 안 함(4444 알파에서도 빠짐)
        rm = QHBoxLayout()
        rm.setSpacing(6)
        self.l_sel = lm = QLabel("선택 타원")
        lm.setStyleSheet("color:#aaa")
        self.sel_kind = Seg([("blur", "블러"), ("minus", "제외")])
        self.sel_kind.setFixedWidth(120)
        self.sel_kind.setToolTip("제외 = 이 타원 영역은 블러하지 않음(다른 타원이 겹쳐도). 4444 알파에서도 빠짐 (X)")
        self.sel_kind.changed.connect(lambda k: self.minus.emit(k == "minus"))
        rm.addWidget(lm)
        rm.addStretch(1)
        rm.addWidget(self.sel_kind)
        self.w_minus = QWidget()
        self.w_minus.setLayout(rm)
        self.sec_edit.add(self.w_minus)
        self.ov_rows = {}
        for key, label, lo, hi, unit in OV_SLIDERS:
            row = SliderRow(key, label, lo, hi, unit)
            row.pressed.connect(lambda k: self.ov_pressed.emit())
            row.changed.connect(lambda k, v: self.ov_changed.emit(k, v))
            self.sec_edit.add(row)
            self.ov_rows[key] = row
        r2 = QHBoxLayout()
        r2.addStretch(1)
        r2.addWidget(btn("값 초기화", "선택한 타원의 크기·페더·강도를 전체 값으로", w=76,
                         on_click=lambda: self.ov_changed.emit("reset", None)))
        w2 = QWidget()
        w2.setLayout(r2)
        self.sec_edit.add(w2)
        for s in (self.sec_beauty, self.sec_bell, self.sec_blur, self.sec_xell, self.sec_det, *self.sign_secs,
                  self.sec_edit):
            self.bl.addWidget(s)
        # [분석] — 타원 편집 묶음 바로 아래(v0.7.70 사용자: 맨 아래 고정은 눈에 안 띔), 크고 파랗게(분석=측정 색)
        from PySide6.QtWidgets import QSizePolicy
        self.b_analyze = btn("분석", "이 클립을 분석(얼굴·트래킹) — 타원·검수·재생이 정확해집니다",
                             size=BTN_MAIN, on_click=self.analyze_clicked.emit)
        tone(self.b_analyze, "blue")
        self.b_analyze.setMinimumWidth(10)
        self.b_analyze.setMaximumWidth(16777215)
        self.b_analyze.setFixedHeight(BTN_MAIN[1] + 8)
        self.b_analyze.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _ar = QHBoxLayout()
        _ar.setContentsMargins(6, 10, 6, 6)
        _ar.addWidget(self.b_analyze)
        _aw = QWidget()
        _aw.setLayout(_ar)
        self.multi_lb = QLabel("")
        self.multi_lb.setStyleSheet("background:#3a3320;color:#f0d37a;border-radius:3px;padding:4px 8px;font-size:11px")
        self.multi_lb.setVisible(False)
        self.bl.addWidget(_aw)               # 타원 편집 묶음 바로 아래(스크롤 안)
        outer.addWidget(self.multi_lb)
        self.bl.addStretch(1)

        self.state.current_changed.connect(self.refresh)
        self.state.changed.connect(self.refresh)
        self.refresh_presets()
        self.refresh()

    # ------------------------------------------------------------ 구성
    def _rows(self, sec, mode, defs):
        for key, label, lo, hi, unit in defs:
            row = SliderRow(key, label, lo, hi, unit)
            row.pressed.connect(lambda k: self.state.push_undo())
            row.changed.connect(lambda k, v: self.state.set_param(k, v))
            sec.add(row)
            self.rows[(mode, key)] = row

    def _key_menu(self):
        m = QMenu(self)
        for k, t in KEYS:
            m.addAction(t, lambda k=k: self.set_key(k))
        self.key.setMenu(m)
        self._key_text()

    def set_key(self, k):
        self.key_mode = k
        self._key_text()
        self.view_changed.emit()

    def _key_text(self):
        t = dict(KEYS)[self.key_mode]
        self.key.setText("키 ▾" if self.key_mode == "off" else t.replace(" 키", "").replace("타원 ", "") + " ▾")
        set_on(self.key, self.key_mode != "off")

    def _pick(self, v):
        set_on(self.b_pick, v)
        if v:
            self.pick_target = "base"
            if self.b_addskin.isChecked():
                self.b_addskin.setChecked(False)
        self.pick_skin.emit(v or self.b_addskin.isChecked())

    def _pick_extra(self, v):
        set_on(self.b_addskin, v)
        if v:
            self.pick_target = "extra"
            if self.b_pick.isChecked():
                self.b_pick.setChecked(False)
        self.pick_skin.emit(v or self.b_pick.isChecked())

    def stop_pick(self):
        for b in (self.b_pick, self.b_addskin):
            if b.isChecked():
                b.setChecked(False)
        self.stop_tool()
        self._chips()

    def _del_extra(self, k):
        s = self.state.source()
        ex = list(s.beauty.get("skin_extra") or []) if s else []
        if k < len(ex):
            ex.pop(k)
            self.state.set_param("skin_extra", ex, undo=True)

    def set_auto_ref(self, ref):
        """자동 피부색일 때 방금 계산된 값(칩 표시용)."""
        if ref is not None and (self.auto_ref is None or tuple(ref[:2]) != tuple(self.auto_ref[:2])):
            self.auto_ref = tuple(ref)
            self._chips()

    def _chips(self):
        from . import fx
        s = self.state.source()
        b = s.beauty if s else {}
        ref = b.get("skin_ref")

        def paint(c, rgb, dashed=False, tip=""):
            if rgb is None:
                c.setStyleSheet("QPushButton{background:#1c1c1c;border:1px dashed #555;border-radius:2px}")
            else:
                c.setStyleSheet(f"QPushButton{{background:rgb({rgb[0]},{rgb[1]},{rgb[2]});"
                                f"border:1px {'dashed' if dashed else 'solid'} #cfcfcf;border-radius:2px}}")
            c.setToolTip(tip)
        if ref:
            paint(self.chip_base, fx.ref_rgb(ref), tip="기준 피부색(찍은 색)")
        elif self.auto_ref is not None:
            paint(self.chip_base, fx.ref_rgb(self.auto_ref), True, "기준 피부색: 자동(얼굴에서 계산)")
        else:
            paint(self.chip_base, None, tip="기준 피부색: 자동")
        ex = list(b.get("skin_extra") or [])
        for k, c in enumerate(self.chips_extra):
            if k < len(ex):
                paint(c, fx.ref_rgb(ex[k]), tip="추가 피부색 — 클릭하면 지움")
                c.setEnabled(True)
            else:
                paint(c, None, tip="추가 피부색 없음([피부색 추가]로 찍기)")
                c.setEnabled(False)
        self.b_addskin.setEnabled(len(ex) < 2 or self.b_addskin.isChecked())

    def _tool(self, name, v):
        tog = {"add": self.b_add, "del": self.b_del, "merge": self.b_merge}
        if v:
            for k, b in tog.items():
                if k != name and b.isChecked():
                    b.blockSignals(True)
                    b.setChecked(False)
                    set_on(b, False)
                    b.blockSignals(False)
        self.tool.emit(name if v else None)

    def stop_tool(self):
        for b in (self.b_add, self.b_del, self.b_merge):
            if b.isChecked():
                b.blockSignals(True)
                b.setChecked(False)
                set_on(b, False)
                b.blockSignals(False)
        self.tool.emit(None)

    def show_selection(self, ov, p, minus=False):
        """선택한 타원의 값(없으면 전체 값으로 표시, 비활성)."""
        en = ov is not None
        self.sel_kind.setEnabled(en)
        self.sel_kind.blockSignals(True)
        self.sel_kind.set("minus" if (en and minus) else "blur")
        self.sel_kind.blockSignals(False)
        for k, row in self.ov_rows.items():
            row.setEnabled(en)
            if k == "size":
                v = (ov or {}).get("size", 100)
            else:
                v = (ov or {}).get(k, p.get(k, 0) if p else 0)
            row.set(v)
        self.b_delkey.setEnabled(en)
        self.b_off.setEnabled(en)
        self.b_merge.setEnabled(en or self.b_merge.isChecked())

    # ------------------------------------------------------------ 갱신
    def _on_workmode(self, k):
        if k == "beauty":
            self.state.set_mode("beauty")
        else:
            face, sign = self.t_face.isChecked(), self.t_sign.isChecked()
            if not face and not sign:
                face = True
            self.state.set_blur(face, sign, active=("sign" if (sign and not face) else "blur"))

    def _set_target(self, which, on):
        if self._sync:
            return
        face, sign = self.t_face.isChecked(), self.t_sign.isChecked()
        if not face and not sign:                 # 마지막 하나는 못 끔
            self._sync = True
            (self.t_face if which == "blur" else self.t_sign).setChecked(True)
            self._sync = False
            return
        self.state.set_blur(face, sign, active=which)

    def refresh(self):
        s = self.state.source()
        en = s is not None
        for w in (self.mode, self.w_target, self.sec_beauty, self.sec_bell, self.sec_blur, self.sec_xell,
                  self.sec_det, *self.sign_secs):
            w.setEnabled(en)
        mode = s.mode if s else self.state.settings.d.get("default_mode", "beauty")
        self.mode.set("beauty" if mode == "beauty" else "blur")
        self._sync = True                 # 토글 상태 = s.mode/both 반영(신호 무시)
        self.t_face.setChecked(bool(s) and (mode == "blur" or s.both))
        self.t_sign.setChecked(bool(s) and (mode == "sign" or s.both))
        self._sync = False
        beauty = mode == "beauty"
        for w in (self.sec_beauty, self.sec_bell):
            w.setVisible(beauty)
        for w in (self.sec_blur, self.sec_xell, self.sec_det):
            w.setVisible(mode == "blur")
        for w in self.sign_secs:
            w.setVisible(mode == "sign")
        for w in (self.w_target, self.w_minus):
            w.setVisible(not beauty)
        self.b_add.setText("사각형 추가" if mode == "sign" else "타원 추가")
        self.sec_edit.set_title("사각형 편집" if mode == "sign" else "타원 편집")
        self.l_sel.setText("선택 사각형" if mode == "sign" else "선택 타원")
        self.t_id.setVisible(not beauty)
        if beauty and self.key_mode == "skin":
            pass
        if not beauty and self.key_mode == "skin":
            self.key_mode = "off"
            self._key_text()
        acts = self.key.menu().actions()
        acts[2].setVisible(beauty)
        if s:
            for (m, k), row in self.rows.items():
                row.set(s.params_for(m)[k])
            self.kind.set(s.blur["kind"])
            self.skind.set(s.sign.get("kind", "blur"))
            for t, v in ((self.t_body, s.blur.get("body", True)), (self.t_feat, s.beauty.get("features", True))):
                t.blockSignals(True)
                t.setChecked(bool(v))
                set_on(t, bool(v))
                t.blockSignals(False)
            self._chips()
        # v0.7.64 여러 소스 선택: 바꾼 값이 같은 작업의 선택 클립에 모두 들어감 → 클립마다 다른 것(타원·피부색)은 잠금
        multi = self.state.multi_selected()
        one = en and not multi
        for w in (self.sec_bell, self.sec_xell, self.sec_sell, self.sec_edit):
            w.setEnabled(one)
        self.b_pick.setEnabled(one)
        if not one:
            self.b_addskin.setEnabled(False)
        same = sum(1 for t in multi if s is not None and t.mode == s.mode)
        self.multi_lb.setVisible(bool(multi))
        if multi:
            self.multi_lb.setText(f"소스 {len(multi)}개 선택 — 바꾼 값이 같은 작업 {same}개에 모두 적용")
        self._sync_preset(s)

    def _sync_preset(self, s):
        dlg = getattr(self, "_preset_dlg", None)          # v0.7.77 툴박스 프리셋 창이 떠 있으면 현재 클립 따라가기
        if dlg is not None and dlg.isVisible():
            dlg.set_clip(s)

    # ------------------------------------------------------------ 값 버튼
    def reset(self):
        s = self.state.source()
        if s:
            d = dict({"beauty": P.BEAUTY_DEFAULT, "sign": P.SIGN_DEFAULT}.get(s.mode, P.BLUR_DEFAULT))
            self.state.set_params(s.mode, d)

    def to_default(self):
        s = self.state.source()
        if s:
            self.state.set_params(s.mode, self.state.settings.defaults(s.mode), custom=False)

    def save_default(self):
        s = self.state.source()
        if not s:
            return
        self.state.settings.d[s.mode] = dict(s.params())
        self.state.settings.d["default_mode"] = s.mode
        self.state.settings.save()
        self.state.message.emit("기본값으로 저장했습니다")

    # ------------------------------------------------------------ 프리셋
    def refresh_presets(self, select=None):
        P.ensure_builtin_presets()
        if select:
            self.b_preset.setText(f"프리셋: {select}")

    def _preset_save(self):
        s = self.state.source()
        if not s:
            return
        name, ok = QInputDialog.getText(self, "프리셋 저장", "이름")
        name = name.strip()
        if ok and name:
            P.save_preset(name, s.mode, dict(s.params()))
            self.refresh_presets(name)

    def preset_menu(self, name, global_pos):
        """저장된 프리셋 오른쪽 클릭 메뉴(프리셋 창 안 버튼). 바뀌면 True."""
        m = QMenu(self)
        acts = {m.addAction("이름 바꾸기"): self._p_rename, m.addAction("덮어쓰기"): self._p_over,
                m.addAction("기본값으로 쓰기"): self._p_default, m.addAction("삭제"): self._p_del}
        a = m.exec(global_pos)
        if a is None:
            return False
        before = set(P.list_presets())
        acts[a](name)
        return set(P.list_presets()) != before

    def _p_rename(self, name):
        new, ok = QInputDialog.getText(self, "이름 바꾸기", "새 이름", text=name)
        if ok and new.strip() and new.strip() != name:
            P.rename_preset(name, new.strip())
            self.refresh_presets(new.strip())

    def _p_over(self, name):
        s = self.state.source()
        if s:
            P.save_preset(name, s.mode, dict(s.params()))
            self.state.message.emit(f"프리셋 덮어씀: {name}")

    def _p_default(self, name):
        d = P.load_preset(name)
        mode = d.get("mode", "beauty")
        self.state.settings.d[mode] = {**self.state.settings.defaults(mode), **d.get("params", {})}
        self.state.settings.save()
        self.state.message.emit(f"기본값으로: {name}")

    def _p_del(self, name):
        if QMessageBox.question(self, "삭제", f"프리셋 '{name}' 을(를) 지울까요?") == QMessageBox.Yes:
            P.delete_preset(name)
            self.refresh_presets()

    def _open_grid(self):
        dlg = getattr(self, "_preset_dlg", None)
        if dlg is not None and dlg.isVisible():           # v0.7.77 [프리셋] 다시 누르면 닫힘(토글)
            dlg.close()
            return
        s = self.state.source()
        if not s:
            self.state.message.emit("클립을 먼저 불러오세요")
            return
        set_on(self.b_preset, True)                       # 창이 떠 있는 동안 켜짐(파랑)
        dlg = PresetGridPopup(self, s.mode, s)            # v0.7.77 툴박스: 띄워둔 채 다른 조작 가능(비모달·항상 위)
        dlg.on_apply = self._apply_preset                 # [적용] = 창을 닫지 않고 현재(선택) 클립에 적용
        dlg.on_close = self._grid_closed
        dlg.move(self.b_preset.mapToGlobal(self.b_preset.rect().bottomLeft()) + QPoint(0, 4))
        self._preset_dlg = dlg
        dlg.show()

    def _grid_closed(self):
        self.b_preset.setChecked(False)
        set_on(self.b_preset, False)
        self._preset_dlg = None

    def _apply_preset(self, mode, params, label):
        sel = [self.state.cur] + list(getattr(self, "extra_selected", lambda: [])())
        cur = self.state.cur
        for idx in dict.fromkeys(sel):
            self.state.cur = idx
            self.state.set_params(mode, params)
        self.state.cur = cur
        self.state.current_changed.emit()
        self.state.message.emit(f"프리셋 적용: {label}")
        self.b_preset.setText(f"프리셋: {label}")

    # ------------------------------------------------------------ 상태 읽기
    def view_mode(self):
        return self.view.value() or "fx"


class PresetGridPopup(DragMove, QDialog):          # 빈 바탕을 끌어 옮김(DTME 팝업과 같음)
    """프리셋 격자. 작업(뷰티/블러) × 강도(약/중/강/강+) 또는 저장된 프리셋을 고르고 [적용].

    v0.7.76(사용자 요청): 열면 아무것도 고르지 않은 상태 · 고르기만 해서는 적용도 닫힘도 없음 ·
    [적용] = 창을 닫지 않고 현재(선택) 클립에 적용 · 닫기는 오른쪽 위 ✕ 뿐(Esc·바깥 클릭으로 안 닫힘).
    현재 클립의 설정은 글자 줄로만 알려 줌. 장면 자동판정(시장/정면 …)은 부정확해서 쓰지 않는다.
    """

    def __init__(self, parent, mode, src=None):
        super().__init__(parent)
        self.on_apply = None           # (mode, params, label) → 적용
        self.mode = mode if mode in ("beauty", "blur", "sign") else "beauty"
        self.src = src                 # 현재 클립 — '현재 클립: …' 글자 표시용
        self.pick = None               # 고른 것: ("level", 강도) | ("named", 이름) | None
        # v0.7.77 툴박스: 띄워둔 채 다른 조작 가능(비모달) · 항상 위 · 작업표시줄에 안 뜸
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self.on_close = None
        self.setStyleSheet("PresetGridPopup{background:#262626;border:1px solid #555;border-radius:6px}"
                           "QLabel{color:#ccc;font-size:12px}"
                           "QPushButton#x{color:#bbb;background:transparent;border:none;font-size:13px;padding:0}"
                           "QPushButton#x:hover{color:#fff;background:#c0392b;border-radius:3px}")
        self.setFixedWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 12, 14)
        root.setSpacing(10)
        hr = QHBoxLayout()
        hr.setContentsMargins(0, 0, 0, 0)
        head = QLabel("프리셋")
        head.setStyleSheet("color:#fff;font-size:13px;font-weight:bold")
        hr.addWidget(head)
        hr.addStretch(1)
        self.b_x = QPushButton("✕")
        self.b_x.setObjectName("x")
        self.b_x.setFixedSize(20, 20)
        self.b_x.setToolTip("닫기")
        self.b_x.setCursor(Qt.PointingHandCursor)
        self.b_x.setFocusPolicy(Qt.NoFocus)
        self.b_x.clicked.connect(self._close)
        hr.addWidget(self.b_x)
        root.addLayout(hr)
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 0, 4, 0)
        inner.setSpacing(10)
        root.addLayout(inner)

        wr = QHBoxLayout()
        wr.setSpacing(8)
        wlb = QLabel("작업")
        wlb.setFixedWidth(44)
        wr.addWidget(wlb)
        self.wseg = Seg([("beauty", "뷰티"), ("blur", "얼굴 블러"), ("sign", "간판 블러")])
        self.wseg.set(self.mode)
        self.wseg.changed.connect(self._on_mode)
        wr.addWidget(self.wseg, 1)
        inner.addLayout(wr)

        sr = QHBoxLayout()
        sr.setSpacing(8)
        slb = QLabel("강도")
        slb.setFixedWidth(44)
        sr.addWidget(slb)
        self.sseg = Seg([("weak", "약"), ("mid", "중"), ("strong", "강"), ("strongplus", "강+")])
        self.sseg.changed.connect(self._on_strength)
        sr.addWidget(self.sseg, 1)
        inner.addLayout(sr)
        self.cur_lb = QLabel("")
        self.cur_lb.setStyleSheet("color:#8f8f8f;font-size:11px;margin-left:52px")
        inner.addWidget(self.cur_lb)
        self.named = {}

        # 저장된 프리셋(고른 뒤 [적용]). 이름 바꿈·삭제 뒤에는 이 칸만 다시 만듦(창은 그대로)
        self.named_box = QWidget()
        self.named_lay = QVBoxLayout(self.named_box)
        self.named_lay.setContentsMargins(0, 0, 0, 0)
        self.named_lay.setSpacing(10)
        inner.addWidget(self.named_box)
        self._build_named()

        br = QHBoxLayout()
        br.addWidget(btn("폴더", "프리셋이 저장된 폴더 열기(내 문서\\MOSCO\\presets) — 백업하거나 다른 PC 로 옮길 때", w=56,
                         on_click=self._open_folder))
        self.done_lb = QLabel("")
        self.done_lb.setStyleSheet("color:#7fd18b;font-size:11px;margin-left:6px")
        br.addWidget(self.done_lb, 1)
        self.b_apply = btn("적용", "고른 강도·프리셋을 현재(선택) 클립에 적용 — 창은 그대로", w=84, red=True, on_click=self._apply)
        br.addWidget(self.b_apply)
        inner.addLayout(br)
        self._show_current()
        self._set_pick(None)

    def _build_named(self):
        while self.named_lay.count():
            it = self.named_lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
            elif it.layout() is not None:
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w is not None:
                        w.deleteLater()
        self.named = {}
        names = P.list_presets()
        self.named_box.setVisible(bool(names))
        if not names:
            return
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color:#3a3a3a")
        self.named_lay.addWidget(div)
        self.named_lay.addWidget(QLabel("저장된 프리셋"))
        wrap = QGridLayout()
        wrap.setHorizontalSpacing(6)
        wrap.setVerticalSpacing(6)
        for i, n in enumerate(names):
            b = btn(n, f"'{n}' 고르기 — [적용] 을 눌러야 적용됩니다", w=104, on_click=lambda _=False, n=n: self._use_named(n))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, n=n, b=b: self._named_menu(n, b.mapToGlobal(pos)))
            wrap.addWidget(b, i // 3, i % 3)
            self.named[n] = b
        self.named_lay.addLayout(wrap)

    def _close(self):
        self.close()

    def reject(self):                  # Esc 로는 닫히지 않음(닫기 = ✕ 만)
        pass

    def closeEvent(self, e):           # ✕·Alt+F4 등 닫기 → 버튼 상태 되돌림
        if self.on_close is not None:
            self.on_close()
        e.accept()

    def set_clip(self, src):           # v0.7.77 현재(선택) 클립이 바뀌면 작업 종류·표시 갱신
        self.src = src
        if src is not None:
            m = src.mode if src.mode in ("beauty", "blur", "sign") else "beauty"
            if m != self.mode:
                self.mode = m
                self.wseg.set(m)
        self._show_current()

    def _open_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from . import paths
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.preset_dir())))

    def _set_pick(self, pick):
        """고른 것 표시: 강도 칸 또는 저장된 프리셋 하나만 켜짐. None = 아무것도 안 고름."""
        self.pick = pick
        lv = pick[1] if pick and pick[0] == "level" else None
        self.sseg.group.setExclusive(False)
        for k, b in self.sseg.btns.items():
            b.setChecked(k == lv)
        self.sseg.group.setExclusive(True)
        for n, b in self.named.items():
            set_on(b, bool(pick and pick[0] == "named" and pick[1] == n))
        self.b_apply.setEnabled(pick is not None)

    def _on_mode(self, v):
        self.mode = v
        self._show_current()
        if self.pick and self.pick[0] == "named":      # 저장된 프리셋은 자기 작업 종류가 있음 → 고른 것 해제
            self._set_pick(None)

    def _on_strength(self, v):
        self.done_lb.setText("")
        self._set_pick(("level", v))

    def _show_current(self):
        """현재 클립의 설정을 글자로만 알려 줌(칸은 켜지 않음): 약·중·강 / 같은 저장 프리셋 / 직접 조정."""
        cur = self.src.params_for(self.mode) if self.src is not None else None
        if not cur:
            self.cur_lb.setText("")
            return
        lv = ad.level_of(self.mode, cur)
        same = []
        for n in P.list_presets():
            try:
                d = P.load_preset(n)
                pr = d.get("params", {})
                if d.get("mode", "beauty") == self.mode and pr and all(cur.get(k) == v for k, v in pr.items()):
                    same.append(n)
            except Exception:  # noqa: BLE001
                pass
        if lv is not None:
            self.cur_lb.setText(f"현재 클립: {ad.LABEL[lv]}" + (f" · 프리셋 '{same[0]}'" if same else ""))
        else:
            self.cur_lb.setText("현재 클립: " + (f"프리셋 '{same[0]}'" if same else "직접 조정한 값(약·중·강과 다름)"))

    def _apply(self):
        if self.pick is None:
            return
        if self.pick[0] == "level":
            mode, params = ad.to_params(self.mode, self.pick[1])
            label = f"{ad.LABEL.get(self.mode, self.mode)} · {ad.LABEL.get(self.pick[1], self.pick[1])}"
        else:
            try:
                d = P.load_preset(self.pick[1])
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "프리셋", str(e))
                return
            mode, params, label = d.get("mode", "beauty"), d.get("params", {}), self.pick[1]
        if self.on_apply is not None:
            self.on_apply(mode, params, label)
        self.done_lb.setText(f"적용됨: {label}")
        self._show_current()

    def _named_menu(self, name, gpos):
        panel = self.parent()
        if hasattr(panel, "preset_menu") and panel.preset_menu(name, gpos):
            self._build_named()           # 목록이 바뀜(이름 바꿈·삭제) → 이 칸만 다시(창은 안 닫음)
            self._set_pick(self.pick if self.pick and (self.pick[0] != "named" or self.pick[1] in self.named) else None)
            self._show_current()
            self.adjustSize()

    def _use_named(self, name):
        self.done_lb.setText("")
        try:
            d = P.load_preset(name)
            m = d.get("mode", "beauty")
            if m in ("beauty", "blur", "sign") and m != self.mode:    # 그 프리셋의 작업 종류로 표시를 맞춤
                self.mode = m
                self.wseg.set(m)
                self._show_current()
        except Exception:  # noqa: BLE001
            pass
        self._set_pick(("named", name))


_ = BTN
