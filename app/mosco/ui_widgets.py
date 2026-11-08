"""공통 위젯: 버튼(크기 규칙), 토글, 묶음 버튼, 슬라이더 줄, 접는 묶음."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter
from PySide6.QtWidgets import (QFrame, QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

BTN = (76, 28)
BTN_MAIN = (88, 32)


def arrow_icon(w=34, h=18, color="#ffffff"):
    """DTME식 두께 있는 오른쪽 블록 화살표(벡터). 문자표 글리프 대신 사용."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
    pm = QPixmap(w * 2, h * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    W, H = w * 2, h * 2
    shaft_h, head_w = H * 0.46, W * 0.42
    p.drawPolygon(QPolygonF([QPointF(0, (H - shaft_h) / 2), QPointF(W - head_w, (H - shaft_h) / 2),
                             QPointF(W - head_w, 0), QPointF(W, H / 2), QPointF(W - head_w, H),
                             QPointF(W - head_w, (H + shaft_h) / 2), QPointF(0, (H + shaft_h) / 2)]))
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)

QSS = """
QWidget { color: #e6e6e6; }
/* ---- 버튼(DTME 와 같은 모양: 그림자 없음 · 버튼 안 입체감 · 켜짐 파랑) — design/gen/make_mosco_qss.py 로 만듦 ---- */
QPushButton, QToolButton{color:#e6e6e6;  border:1px solid #1c1c1c; border-top-color:#5c5c5c; border-bottom-color:#0e0e0e; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #4e4e4e, stop:0.48 #3f3f3f, stop:0.52 #383838, stop:1.0 #282828); padding:0 6px;}QPushButton:hover, QToolButton:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #585858, stop:0.48 #424242, stop:0.52 #383838, stop:1.0 #323232);}QPushButton:pressed, QToolButton:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #262626, stop:0.48 #323232, stop:0.52 #383838, stop:1.0 #434343); border-top-color:#0e0e0e; border-bottom-color:#4e4e4e;}QPushButton:disabled, QToolButton:disabled{color:#afafaf; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #393939, stop:0.48 #393939, stop:0.52 #393939, stop:1.0 #212121); border-color:#1d1d1d;}
QToolButton::menu-indicator { image: none; }
QPushButton[red="true"], QPushButton[tone="red"]{color:#ffffff; font-weight:700; border:1px solid #a41d0f; border-top-color:#e45d4f; border-bottom-color:#960f01; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #d64f41, stop:0.48 #c74032, stop:0.52 #c0392b, stop:1.0 #b0291b); }QPushButton[red="true"]:hover, QPushButton[tone="red"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #e0594b, stop:0.48 #ca4335, stop:0.52 #c0392b, stop:1.0 #ba3325);}QPushButton[red="true"]:pressed, QPushButton[tone="red"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #ae2719, stop:0.48 #ba3325, stop:0.52 #c0392b, stop:1.0 #cb4436); border-top-color:#960f01; border-bottom-color:#d64f41;}QPushButton[red="true"]:disabled, QPushButton[tone="red"]:disabled{color:#b9b9b9; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #763a33, stop:0.48 #763a33, stop:0.52 #763a33, stop:1.0 #5e221b); border-color:#5a1e17;}
QPushButton[tone="orange"]{color:#ffffff; font-weight:700; border:1px solid #bd660f; border-top-color:#fda64f; border-bottom-color:#af5801; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #ef9841, stop:0.48 #e08932, stop:0.52 #d9822b, stop:1.0 #c9721b); }QPushButton[tone="orange"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #f9a24b, stop:0.48 #e38c35, stop:0.52 #d9822b, stop:1.0 #d37c25);}QPushButton[tone="orange"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #c77019, stop:0.48 #d37c25, stop:0.52 #d9822b, stop:1.0 #e48d36); border-top-color:#af5801; border-bottom-color:#ef9841;}QPushButton[tone="orange"]:disabled{color:#b9b9b9; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #825a33, stop:0.48 #825a33, stop:0.52 #825a33, stop:1.0 #6a421b); border-color:#663e17;}
QPushButton[tone="light"]{color:#111111; font-weight:700; border:1px solid #adadad; border-top-color:#e1e1e1; border-bottom-color:#9f9f9f; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #d3d3d3, stop:0.48 #cccccc, stop:0.52 #c9c9c9, stop:1.0 #b9b9b9); }QPushButton[tone="light"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #dddddd, stop:0.48 #cfcfcf, stop:0.52 #c9c9c9, stop:1.0 #c3c3c3);}QPushButton[tone="light"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #b7b7b7, stop:0.48 #c3c3c3, stop:0.52 #c9c9c9, stop:1.0 #cecece); border-top-color:#9f9f9f; border-bottom-color:#d3d3d3;}QPushButton[tone="light"]:disabled{color:#5a5a5a; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #7a7a7a, stop:0.48 #7a7a7a, stop:0.52 #7a7a7a, stop:1.0 #626262); border-color:#5e5e5e;}
QPushButton[tone="blue"]{color:#ffffff; font-weight:700; border:1px solid #13335c; border-top-color:#53739c; border-bottom-color:#05254e; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #45658e, stop:0.48 #36567f, stop:0.52 #2f4f78, stop:1.0 #1f3f68); }QPushButton[tone="blue"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #4f6f98, stop:0.48 #395982, stop:0.52 #2f4f78, stop:1.0 #294972);}QPushButton[tone="blue"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #1d3d66, stop:0.48 #294972, stop:0.52 #2f4f78, stop:1.0 #3a5a83); border-top-color:#05254e; border-bottom-color:#45658e;}QPushButton[tone="blue"]:disabled{color:#b9b9b9; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #354356, stop:0.48 #354356, stop:0.52 #354356, stop:1.0 #1d2b3e); border-color:#19273a;}
QPushButton[tone="overlay"]{color:#dddddd;  border:1px solid rgba(10,10,10,225); border-top-color:rgba(74,74,74,225); border-bottom-color:rgba(0,0,0,225); border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 rgba(60,60,60,225), stop:0.48 rgba(45,45,45,225), stop:0.52 rgba(38,38,38,225), stop:1.0 rgba(22,22,22,225)); font-size:11px; padding:0;}QPushButton[tone="overlay"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 rgba(70,70,70,225), stop:0.48 rgba(48,48,48,225), stop:0.52 rgba(38,38,38,225), stop:1.0 rgba(32,32,32,225));}QPushButton[tone="overlay"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 rgba(20,20,20,225), stop:0.48 rgba(32,32,32,225), stop:0.52 rgba(38,38,38,225), stop:1.0 rgba(49,49,49,225)); border-top-color:rgba(0,0,0,225); border-bottom-color:rgba(60,60,60,225);}QPushButton[tone="overlay"]:disabled{color:#ababab; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 rgba(49,49,49,225), stop:0.48 rgba(49,49,49,225), stop:0.52 rgba(49,49,49,225), stop:1.0 rgba(25,25,25,225)); border-color:rgba(21,21,21,225);}
QPushButton[head="true"]{color:#e0e0e0; font-weight:700; border:1px solid #0f0f0f; border-top-color:#4f4f4f; border-bottom-color:#010101; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #414141, stop:0.48 #323232, stop:0.52 #2b2b2b, stop:1.0 #1b1b1b); text-align:left; padding-left:8px;}QPushButton[head="true"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #4b4b4b, stop:0.48 #353535, stop:0.52 #2b2b2b, stop:1.0 #252525);}QPushButton[head="true"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #191919, stop:0.48 #252525, stop:0.52 #2b2b2b, stop:1.0 #363636); border-top-color:#010101; border-bottom-color:#414141;}QPushButton[head="true"]:disabled{color:#acacac; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #333333, stop:0.48 #333333, stop:0.52 #333333, stop:1.0 #1b1b1b); border-color:#171717;}
QPushButton:checked, QPushButton[on="true"], QToolButton:checked, QToolButton[on="true"]{color:#ffffff;  border:1px solid #4a8fe0; border-top-color:#68adfe; border-radius:3px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #45658e, stop:0.48 #36567f, stop:0.52 #2f4f78, stop:1.0 #1f3f68); }QPushButton:checked:hover, QPushButton[on="true"]:hover, QToolButton:checked:hover, QToolButton[on="true"]:hover{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #4f6f98, stop:0.48 #395982, stop:0.52 #2f4f78, stop:1.0 #294972);}QPushButton:checked:pressed, QPushButton[on="true"]:pressed, QToolButton:checked:pressed, QToolButton[on="true"]:pressed{background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #1d3d66, stop:0.48 #294972, stop:0.52 #2f4f78, stop:1.0 #3a5a83);}QPushButton:checked:disabled, QPushButton[on="true"]:disabled, QToolButton:checked:disabled, QToolButton[on="true"]:disabled{color:#b9b9b9; background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0.0 #354356, stop:0.48 #354356, stop:0.52 #354356, stop:1.0 #1d2b3e); border-color:#19273a;}
/* 콤보·슬라이더 = Qt 기본(Fusion) + 어두운 팔레트 — DTME 와 같음 */
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


def add_shadow(w, blur=10, dy=2, alpha=170):
    """그림자 없음(DTME v0.16.5 기준: 버튼 아래 그림자 없음 · 입체감은 버튼 안 QSS 그라데이션).
    부르는 곳은 그대로 두고 아무것도 하지 않음(윈도우 지뢰였던 드롭섀도도 이걸로 사라짐)."""
    return w


def btn(text, tip="", size=BTN, red=False, w=None, on_click=None, shadow=True) -> QPushButton:
    b = QPushButton(text)
    b.setFixedSize(w or size[0], size[1])
    if tip:
        b.setToolTip(tip)
    if red:
        b.setProperty("red", True)
    b.setFocusPolicy(Qt.NoFocus)
    if on_click:
        b.clicked.connect(on_click)
    if shadow:
        add_shadow(b)
    return b


# ------------------------------------------------------------ 진행 표시 색(DTME 와 같음)
# 단계 → (줄 바탕 채움, 진행 막대·상태 글자). 위 = 줄 바탕 채움 · 아래 = 진행 막대.
PROG = {
    "measure": ("#2f3a4a", "#4a8fe0"),   # 측정(AUTO) = 파랑 (DTME 는 프록시)
    "analyze": ("#24443e", "#3fb8a0"),   # 타원 분석·트랙 = 청록
    "sample": ("#4a4128", "#d9a646"),    # 샘플 = 호박
    "render": ("#4a2e2c", "#d9604f"),    # 렌더 = 빨강
}
PROG_TXT = {"measure": "측정", "analyze": "분석", "sample": "샘플", "render": "렌더"}


class ProgBar(QFrame):
    """아래 진행 막대(6px) 하나 — 단계(kind)마다 색이 바뀐다."""

    def __init__(self, h=6):
        super().__init__()
        self.setFixedHeight(h)
        self.v, self.kind = 0.0, "measure"

    def set(self, v, kind="measure"):
        self.v, self.kind = max(0.0, min(1.0, v or 0.0)), kind
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2a2a"))
        p.drawRoundedRect(r, 3, 3)
        if self.v > 0:
            p.setBrush(QColor(PROG.get(self.kind, PROG["measure"])[1]))
            p.drawRoundedRect(0, 0, int(r.width() * self.v), r.height(), 3, 3)
        p.end()


class DragMove:
    """테두리 없는 창: 빈 바탕을 잡고 끌면 창이 움직임(DTME v0.16.3). 버튼·목록·입력란 위는 원래 동작."""
    _drag_off = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_off = None
        super().mouseReleaseEvent(e)


def tone(b, name: str):
    """버튼 색 이름(QSS [tone=…]: red · orange · light · blue · overlay). 크기는 그대로."""
    b.setProperty("tone", name or None)
    b.style().unpolish(b)
    b.style().polish(b)
    return b


def set_on(b, on: bool):
    b.setProperty("on", bool(on))
    b.style().unpolish(b)
    b.style().polish(b)


class Toggle(QPushButton):
    """켜짐 = 파랑(DTME 기준, QSS [on="true"])."""
    toggled2 = Signal(bool)

    def __init__(self, text, w=60, tip=""):
        super().__init__(text)
        self.setCheckable(True)
        self.setFixedSize(w, 26)
        self.setFocusPolicy(Qt.NoFocus)
        if tip:
            self.setToolTip(tip)
        add_shadow(self)
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
        lay.setSpacing(5)                     # DTME: 떨어진 둥근 버튼(이어 붙인 직각 아님)
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
        self.v.setFixedHeight(22)
        self.v.setAlignment(Qt.AlignRight)
        self.v.setValidator(QIntValidator(lo, hi))
        lay.addWidget(lb)
        lay.addWidget(self.s, 1)
        lay.addWidget(self.v)
        self.setFixedHeight(23)        # 줄 높이 23 + 섹션 간격 6 = 29px (DTME 슬라이더 줄과 비슷)
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
        self.head.setProperty("head", True)        # 묶음 머리 = 입체감 + 왼쪽 정렬(QSS [head="true"])
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

    def set_title(self, t):
        if t != self.title:
            self.title = t
            self.set_open(self.open)

    def set_open(self, v):
        self.open = v
        self.body.setVisible(v)
        self.head.setText(f"{'▾' if v else '▸'}  {self.title}")
