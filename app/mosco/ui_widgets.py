"""공통 위젯: 버튼(크기 규칙), 토글, 묶음 버튼, 슬라이더 줄, 접는 묶음."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

BTN = (76, 28)
BTN_MAIN = (88, 32)

QSS = """
QWidget { font-family: 'Malgun Gothic', 'Noto Sans CJK KR', sans-serif; font-size: 12px; color: #e6e6e6; }
QPushButton, QToolButton { background: #3a3a3a; border: 1px solid #555; border-radius: 3px; padding: 0 6px; }
QPushButton:hover, QToolButton:hover { background: #444; }
QPushButton:pressed, QToolButton:pressed { background: #2f2f2f; }
QPushButton:disabled { color: #777; }
QPushButton[on="true"], QToolButton[on="true"] { background: #5a4a1a; border-color: #f2c94c; color: #f2c94c; }
QPushButton[red="true"] { background: #cf3b2b; border-color: #e0574a; color: #fff; }
QPushButton[red="true"]:hover { background: #db4636; }
QPushButton[seg="true"] { background: #2a2a2a; border-radius: 0; }
QPushButton[seg="true"]:checked { background: #2f4f78; color: #fff; }
QToolButton::menu-indicator { image: none; }
QComboBox { background: #2a2a2a; border: 1px solid #4a4a4a; border-radius: 3px; padding: 2px 8px; min-height: 20px; }
QComboBox QAbstractItemView { background: #262626; selection-background-color: #2f4f78; }
QSlider::groove:horizontal { height: 4px; background: #3c3c3c; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4f7fb8; border-radius: 2px; }
QSlider::handle:horizontal { width: 10px; margin: -5px 0; background: #5a5a5a; border: 1px solid #777; border-radius: 2px; }
QLineEdit { background: #111; border: 1px solid #4a4a4a; border-radius: 2px; padding: 1px 4px; }
QLineEdit[val="true"] { background: transparent; border: 1px solid transparent; }
QLineEdit[val="true"]:focus { background: #111; border-color: #4a8fe0; }
QListWidget { background: #1a1a1a; border: none; outline: none; }
QMenu { background: #262626; border: 1px solid #4a4a4a; }
QMenu::item { padding: 4px 18px; }
QMenu::item:selected { background: #2f4f78; }
QCheckBox::indicator { width: 14px; height: 14px; }
QToolTip { background: #333; color: #eee; border: 1px solid #555; }
QStatusBar { background: #1c1c1c; }
"""


def btn(text, tip="", size=BTN, red=False, w=None, on_click=None) -> QPushButton:
    b = QPushButton(text)
    b.setFixedSize(w or size[0], size[1])
    if tip:
        b.setToolTip(tip)
    if red:
        b.setProperty("red", True)
    b.setFocusPolicy(Qt.NoFocus)
    if on_click:
        b.clicked.connect(on_click)
    return b


def set_on(b, on: bool):
    b.setProperty("on", bool(on))
    b.style().unpolish(b)
    b.style().polish(b)


class Toggle(QPushButton):
    """켜짐 = 노란색."""
    toggled2 = Signal(bool)

    def __init__(self, text, w=60, tip=""):
        super().__init__(text)
        self.setCheckable(True)
        self.setFixedSize(w, 26)
        self.setFocusPolicy(Qt.NoFocus)
        if tip:
            self.setToolTip(tip)
        self.toggled.connect(self._t)

    def _t(self, v):
        set_on(self, v)
        self.toggled2.emit(v)


class Seg(QWidget):
    """[가|나|다] 하나만 선택."""
    changed = Signal(str)

    def __init__(self, items, h=26):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.btns = {}
        for key, text in items:
            b = QPushButton(text)
            b.setProperty("seg", True)
            b.setCheckable(True)
            b.setFixedHeight(h)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            lay.addWidget(b)
            self.btns[key] = b

    def set(self, key):
        if key in self.btns:
            self.btns[key].setChecked(True)

    def value(self):
        for k, b in self.btns.items():
            if b.isChecked():
                return k
        return None


class SliderRow(QWidget):
    """이름(72px 오른쪽 정렬) · 슬라이더 · 숫자(클릭 = 직접 입력)."""
    changed = Signal(str, int)
    pressed = Signal(str)

    def __init__(self, key, label, lo, hi, unit=""):
        super().__init__()
        self.key, self.unit, self.lo, self.hi = key, unit, lo, hi
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lb = QLabel(label)
        lb.setFixedWidth(72)
        lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lb.setStyleSheet("color:#d0d0d0")
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(lo, hi)
        self.s.setFocusPolicy(Qt.NoFocus)
        self.v = QLineEdit()
        self.v.setProperty("val", True)
        self.v.setFixedWidth(46)
        self.v.setAlignment(Qt.AlignRight)
        self.v.setValidator(QIntValidator(lo, hi))
        lay.addWidget(lb)
        lay.addWidget(self.s, 1)
        lay.addWidget(self.v)
        self.s.valueChanged.connect(self._slid)
        self.v.editingFinished.connect(self._typed)
        self._mute = False
        # 연속 조작(드래그·휠) 한 번 = 실행취소 1단계
        from PySide6.QtCore import QTimer
        self._burst = QTimer(self, singleShot=True, interval=800)

    def _fmt(self, v):
        return f"{v}{self.unit}"

    def set(self, v):
        self._mute = True
        self.s.setValue(int(v))
        self.v.setText(self._fmt(int(v)))
        self._mute = False

    def _slid(self, v):
        self.v.setText(self._fmt(v))
        if not self._mute:
            if not self._burst.isActive():
                self.pressed.emit(self.key)
            self._burst.start()
            self.changed.emit(self.key, v)

    def _typed(self):
        t = self.v.text().rstrip(self.unit).strip()
        try:
            v = max(self.lo, min(self.hi, int(t)))
        except ValueError:
            v = self.s.value()
        self.v.clearFocus()
        if v != self.s.value():
            self.s.setValue(v)
        else:
            self.v.setText(self._fmt(v))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.set(self.s.value())
            self.v.clearFocus()
            return
        super().keyPressEvent(e)


class Section(QWidget):
    """▾ 제목 — 눌러서 접기/펴기."""

    def __init__(self, title):
        super().__init__()
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.head = QPushButton(f"▾  {title}")
        self.head.setFixedHeight(22)
        self.head.setStyleSheet("QPushButton{background:#2b2b2b;border:none;border-radius:3px;text-align:left;"
                                "font-weight:700;padding-left:8px;color:#e0e0e0}")
        self.head.setFocusPolicy(Qt.NoFocus)
        self.head.clicked.connect(self.toggle)
        self.body = QWidget()
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(6)
        lay.addWidget(self.head)
        lay.addWidget(self.body)
        self.open = True

    def add(self, w):
        self.bl.addWidget(w)
        return w

    def toggle(self):
        self.set_open(not self.open)

    def set_open(self, v):
        self.open = v
        self.body.setVisible(v)
        self.head.setText(f"{'▾' if v else '▸'}  {self.title}")
