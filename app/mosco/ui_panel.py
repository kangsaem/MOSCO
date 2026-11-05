"""왼쪽 보정 패널: 리셋·기본값·프리셋 / 뷰티|블러 / 보정|원본|반반 · 타원 · 키 · ID / 묶음별 슬라이더."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QMenu, QMessageBox, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from . import project as P
from .ui_widgets import BTN, Section, Seg, SliderRow, Toggle, btn, set_on

KEYS = [("off", "끔"), ("final", "최종 키"), ("skin", "피부 키"), ("alpha", "타원 알파")]


class ControlPanel(QWidget):
    view_changed = Signal()          # 보정/원본/반반·타원·키·ID
    pick_skin = Signal(bool)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setFixedWidth(300)
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
        self.preset = QComboBox()
        self.preset.setFocusPolicy(Qt.NoFocus)
        self.preset.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preset.customContextMenuRequested.connect(self._preset_menu)
        self.preset.activated.connect(self._preset_apply)
        r.addWidget(self.preset, 1)
        r.addWidget(btn("저장…", "현재 값을 프리셋으로", w=60, on_click=self._preset_save))
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
        for s in (self.sec_beauty, self.sec_bell, self.sec_blur, self.sec_xell, self.sec_det):
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
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("프리셋")
        for n in P.list_presets():
            self.preset.addItem(n)
        if select:
            i = self.preset.findText(select)
            self.preset.setCurrentIndex(max(0, i))
        self.preset.blockSignals(False)

    def _preset_apply(self, i):
        if i <= 0:
            return
        try:
            d = P.load_preset(self.preset.itemText(i))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "프리셋", str(e))
            return
        sel = [self.state.cur] + list(getattr(self, "extra_selected", lambda: [])())
        cur = self.state.cur
        for idx in dict.fromkeys(sel):
            self.state.cur = idx
            self.state.set_params(d.get("mode", "beauty"), d.get("params", {}))
        self.state.cur = cur
        self.state.current_changed.emit()

    def _preset_save(self):
        s = self.state.source()
        if not s:
            return
        name, ok = QInputDialog.getText(self, "프리셋 저장", "이름")
        name = name.strip()
        if ok and name:
            P.save_preset(name, s.mode, dict(s.params()))
            self.refresh_presets(name)

    def _preset_menu(self, pos):
        name = self.preset.currentText()
        if self.preset.currentIndex() <= 0:
            return
        m = QMenu(self)
        m.addAction("이름 바꾸기", lambda: self._p_rename(name))
        m.addAction("덮어쓰기", lambda: self._p_over(name))
        m.addAction("기본값으로 쓰기", lambda: self._p_default(name))
        m.addAction("삭제", lambda: self._p_del(name))
        m.exec(self.preset.mapToGlobal(pos))

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

    # ------------------------------------------------------------ 상태 읽기
    def view_mode(self):
        return self.view.value() or "fx"


_ = BTN
