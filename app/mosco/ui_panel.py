"""왼쪽 보정 패널: 리셋·기본값·프리셋 / 뷰티|블러 / 보정|원본|반반 · 타원 · 키 · ID / 묶음별 슬라이더."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QMessageBox, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import autodetect as ad
from . import project as P
from .ui_widgets import BTN, Section, Seg, SliderRow, Toggle, btn, set_on

KEYS = [("off", "끔"), ("final", "최종 키"), ("skin", "피부 키"), ("alpha", "타원 알파")]


OV_SLIDERS = [("size", "크기", 50, 250, "%"), ("feather", "페더", 0, 50, "%"), ("strength", "강도", 0, 100, "")]


class ControlPanel(QWidget):
    view_changed = Signal()          # 보정/원본/반반·타원·키·ID
    pick_skin = Signal(bool)
    tool = Signal(object)            # None | "add" | "merge"
    del_key = Signal()
    off = Signal(str)                # from | to | all | on | remove
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

        self.mode = Seg([("beauty", "뷰티"), ("blur", "블러")])
        self.mode.changed.connect(self.state.set_mode)
        outer.addWidget(self.mode)

        r = QHBoxLayout()
        r.setSpacing(6)
        self.view = Seg([("fx", "보정"), ("orig", "원본"), ("split", "반반")])
        self.view.set("fx")
        self.view.changed.connect(lambda _: self.view_changed.emit())
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
        rr.addWidget(btn("자동", "피부색 자동(얼굴에서)", on_click=lambda: self.state.set_param("skin_ref", None, undo=True)))
        w = QWidget()
        w.setLayout(rr)
        self.sec_beauty.add(w)
        self._rows(self.sec_beauty, "beauty", P.BEAUTY_SLIDERS)
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
        self.t_feat = Toggle("눈·입 제외", w=88, tip="눈·눈썹·입은 보정하지 않음")
        self.t_feat.toggled2.connect(lambda v: self.state.set_param("features", v, undo=True))
        rr2 = QHBoxLayout()
        rr2.addStretch(1)
        rr2.addWidget(self.t_feat)
        wf = QWidget()
        wf.setLayout(rr2)
        self.sec_beauty.add(wf)
        # 타원 편집(공통)
        self.sec_edit = Section("타원 편집")
        r1 = QHBoxLayout()
        r1.setSpacing(4)
        self.b_add = Toggle("타원 추가", w=72, tip="모니터에서 드래그해 새 타원. 켠 채로 계속 그림 (A)")
        self.b_del = Toggle("삭제", w=48, tip="켜고 타원을 클릭해 삭제(오검출 제거). 자동 타원은 전 구간 숨김")
        self.b_merge = Toggle("합치기", w=54, tip="선택한 타원과 같은 사람: 다른 프레임의 타원을 클릭")
        self.b_add.toggled2.connect(lambda v: self._tool("add", v))
        self.b_del.toggled2.connect(lambda v: self._tool("del", v))
        self.b_merge.toggled2.connect(lambda v: self._tool("merge", v))
        self.b_delkey = btn("키 삭제", "현재 프레임의 키 삭제 (Del: 모니터)", w=58, on_click=self.del_key.emit)
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
        for s in (self.sec_beauty, self.sec_bell, self.sec_blur, self.sec_xell, self.sec_det, self.sec_edit):
            self.bl.addWidget(s)
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
        self.pick_skin.emit(v)

    def stop_pick(self):
        if self.b_pick.isChecked():
            self.b_pick.setChecked(False)
        self.stop_tool()

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

    def show_selection(self, ov, p):
        """선택한 타원의 값(없으면 전체 값으로 표시, 비활성)."""
        en = ov is not None
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
    def refresh(self):
        s = self.state.source()
        en = s is not None
        for w in (self.mode, self.sec_beauty, self.sec_bell, self.sec_blur, self.sec_xell, self.sec_det):
            w.setEnabled(en)
        mode = s.mode if s else self.state.settings.d.get("default_mode", "beauty")
        self.mode.set(mode)
        beauty = mode == "beauty"
        for w in (self.sec_beauty, self.sec_bell):
            w.setVisible(beauty)
        for w in (self.sec_blur, self.sec_xell, self.sec_det):
            w.setVisible(not beauty)
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
                row.set((s.beauty if m == "beauty" else s.blur)[k])
            self.kind.set(s.blur["kind"])
            for t, v in ((self.t_body, s.blur.get("body", True)), (self.t_feat, s.beauty.get("features", True))):
                t.blockSignals(True)
                t.setChecked(bool(v))
                set_on(t, bool(v))
                t.blockSignals(False)
        self.sec_edit.setEnabled(en)
        self._sync_preset(s)

    def _sync_preset(self, s):
        pass

    # ------------------------------------------------------------ 값 버튼
    def reset(self):
        s = self.state.source()
        if s:
            d = dict(P.BEAUTY_DEFAULT if s.mode == "beauty" else P.BLUR_DEFAULT)
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
        s = self.state.source()
        if not s:
            self.state.message.emit("클립을 먼저 불러오세요")
            return
        set_on(self.b_preset, True)                       # 창이 떠 있는 동안 켜짐(파랑)
        dlg = PresetGridPopup(self, s.mode)
        dlg.move(self.b_preset.mapToGlobal(self.b_preset.rect().bottomLeft()) + QPoint(0, 4))
        ok = dlg.exec() == QDialog.Accepted
        self.b_preset.setChecked(False)
        set_on(self.b_preset, False)
        if ok and dlg.result is not None:
            mode, params = dlg.result
            sel = [self.state.cur] + list(getattr(self, "extra_selected", lambda: [])())
            cur = self.state.cur
            for idx in dict.fromkeys(sel):
                self.state.cur = idx
                self.state.set_params(mode, params)
            self.state.cur = cur
            self.state.current_changed.emit()
            self.state.message.emit(f"프리셋 적용: {dlg.label}")
            self.b_preset.setText(f"프리셋: {dlg.label}")

    # ------------------------------------------------------------ 상태 읽기
    def view_mode(self):
        return self.view.value() or "fx"


class PresetGridPopup(QDialog):
    """프리셋 격자. 작업(뷰티/블러) × 강도(약/중/강)를 골라 즉시 적용 + 저장된 프리셋 바로 적용.

    장면 자동판정(시장/정면 …)은 부정확해서 쓰지 않는다. 현재/선택 클립에 바로 적용.
    """

    def __init__(self, parent, mode):
        super().__init__(parent)
        self.result = None
        self.label = ""
        self.mode = mode if mode in ("beauty", "blur") else "beauty"
        self.strength = "mid"
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setModal(True)
        self.setStyleSheet("PresetGridPopup{background:#262626;border:1px solid #555;border-radius:6px}"
                           "QLabel{color:#ccc;font-size:12px}")
        self.setFixedWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        head = QLabel("프리셋")
        head.setStyleSheet("color:#fff;font-size:13px;font-weight:bold")
        root.addWidget(head)

        wr = QHBoxLayout()
        wr.setSpacing(8)
        wlb = QLabel("작업")
        wlb.setFixedWidth(44)
        wr.addWidget(wlb)
        self.wseg = Seg([("beauty", "뷰티"), ("blur", "블러")])
        self.wseg.set(self.mode)
        self.wseg.changed.connect(lambda v: setattr(self, "mode", v))
        wr.addWidget(self.wseg, 1)
        root.addLayout(wr)

        sr = QHBoxLayout()
        sr.setSpacing(8)
        slb = QLabel("강도")
        slb.setFixedWidth(44)
        sr.addWidget(slb)
        self.sseg = Seg([("weak", "약"), ("mid", "중"), ("strong", "강")])
        self.sseg.set(self.strength)
        self.sseg.changed.connect(lambda v: setattr(self, "strength", v))
        sr.addWidget(self.sseg, 1)
        root.addLayout(sr)

        # 저장된 프리셋(바로 적용)
        names = P.list_presets()
        if names:
            div = QFrame()
            div.setFrameShape(QFrame.HLine)
            div.setStyleSheet("color:#3a3a3a")
            root.addWidget(div)
            root.addWidget(QLabel("저장된 프리셋"))
            wrap = QGridLayout()
            wrap.setHorizontalSpacing(6)
            wrap.setVerticalSpacing(6)
            for i, n in enumerate(names):
                b = btn(n, f"'{n}' 적용", w=104, on_click=lambda n=n: self._use_named(n))
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda pos, n=n, b=b: self._named_menu(n, b.mapToGlobal(pos)))
                wrap.addWidget(b, i // 3, i % 3)
            root.addLayout(wrap)

        br = QHBoxLayout()
        br.addStretch(1)
        br.addWidget(btn("취소", "", w=64, on_click=self.reject))
        br.addWidget(btn("적용", "현재 클립에 적용", w=84, red=True, on_click=self._apply))
        root.addLayout(br)

    def _apply(self):
        self.result = ad.to_params(self.mode, self.strength)
        self.label = f"{ad.LABEL.get(self.mode, self.mode)} · {ad.LABEL.get(self.strength, self.strength)}"
        self.accept()

    def _named_menu(self, name, gpos):
        panel = self.parent()
        if hasattr(panel, "preset_menu") and panel.preset_menu(name, gpos):
            self.reject()                 # 목록이 바뀜(이름 바꿈·삭제) → 다시 열면 새 목록

    def _use_named(self, name):
        try:
            d = P.load_preset(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "프리셋", str(e))
            return
        self.result = (d.get("mode", "beauty"), d.get("params", {}))
        self.label = name
        self.accept()


_ = BTN
