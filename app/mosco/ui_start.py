"""시작 창: [AUTO] 하나(v0.7.96 [편집] 삭제). [AUTO] = 빈 AUTO 창 → 끌어다 놓기·우클릭으로 영상 추가 → [AUTO] = 본 편집 화면,
프로젝트 파일을 놓거나 열면 바로 편집 화면(v0.7.94 보정+렌더·자동 종료 삭제).
v0.7.73: 윈도우 제목 표시줄 없앰 → 위쪽 정사각형 그림(assets/start.png, 400×400) + 그 위 왼쪽 위 'MOSCO 버전',
오른쪽 위 ✕(= 종료), 빈 곳을 끌어 옮김. v0.7.74: 1px 테두리 없앰(사용자 요청). 그림이 없으면 회색 자리.
v0.7.92: 새 그림(왼쪽 하늘·오른쪽 어둠) + 제목 뒤 하늘 쪽(왼쪽 절반)만 위 90px 파랑 그라데이션(흰 글자 잘 보이게)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from . import __version__, paths
from .ui_widgets import QSS, DragMove, btn

IMG = 400                                      # 그림 한 변(논리 px). 그림 파일은 800×800(고배율 화면용)
W, H = IMG, IMG + 160                          # v0.7.74 테두리 1px 없앰 → 400×560
GRAD_W, GRAD_H = IMG // 2, 90                 # v0.7.92 제목 뒤 그라데이션(하늘 쪽 절반 · 높이 90)


class StartWindow(DragMove, QWidget):
    def __init__(self, mw):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.mw = mw
        mw.quit_on_close = True
        self.setWindowTitle(f"MOSCO {__version__}")    # 작업 표시줄 이름
        self.setFixedSize(W, H)
        self.setObjectName("start")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(QSS + "QWidget#start{background:#262626;border:none}"
                           "QLabel#title{color:#ffffff;font-size:30px;font-weight:bold;letter-spacing:3px;background:transparent}"
                           "QLabel#ver{color:#e6e6e6;font-size:13px;padding-left:2px;background:transparent}"
                           "QPushButton#close{color:#ffffff;background:rgba(0,0,0,90);border:none;border-radius:13px;"
                           "font-size:14px;padding:0}"
                           "QPushButton#close:hover{background:#c0392b}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 위: 정사각형 그림 + 제목·버전(왼쪽 위) + ✕(오른쪽 위)
        self.img = QLabel(self)
        self.img.setFixedSize(IMG, IMG)
        self.img.setAlignment(Qt.AlignCenter)
        pm = QPixmap(str(paths.asset("start.png")))
        if pm.isNull():
            self.img.setStyleSheet("background:#3a3a3a")
        else:
            dpr = self.devicePixelRatioF() or 1.0
            pm = pm.scaled(round(IMG * dpr), round(IMG * dpr), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self.img.setPixmap(pm)
        lay.addWidget(self.img)

        if not pm.isNull():                    # v0.7.92 제목 뒤 파랑 그라데이션(위 진하게 → 아래 투명), 글자보다 먼저 만들어 아래에 깔림
            grad = QWidget(self.img)
            grad.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            grad.setAttribute(Qt.WA_StyledBackground, True)
            grad.setGeometry(0, 0, GRAD_W, GRAD_H)
            grad.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgba(30,120,230,235),"
                               "stop:0.55 rgba(45,140,240,120),stop:1 rgba(45,140,240,0))")

        head = QWidget(self.img)
        head.setAttribute(Qt.WA_TransparentForMouseEvents, True)     # 글자 위를 잡아도 창이 끌림
        hl = QVBoxLayout(head)                 # 제목 아래 버전(그림 왼쪽 위 파란 단색 칸 안에 둠)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        t = QLabel("MOSCO", head)
        t.setObjectName("title")
        v = QLabel(f"v{__version__}", head)
        v.setObjectName("ver")
        hl.addWidget(t)
        hl.addWidget(v)
        head.adjustSize()
        head.move(18, 12)

        self.b_close = QPushButton("✕", self.img)
        self.b_close.setObjectName("close")
        self.b_close.setToolTip("종료")
        self.b_close.setCursor(Qt.PointingHandCursor)
        self.b_close.setFixedSize(26, 26)
        self.b_close.move(IMG - 26 - 10, 10)
        self.b_close.clicked.connect(self.close)

        # 아래: [AUTO] 하나(v0.7.96)
        lay.addStretch(1)
        self.b_auto = btn("AUTO", "AUTO 창 열기", size=(160, 48), red=True, on_click=self.auto)
        f = self.b_auto.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.b_auto.setFont(f)
        lay.addWidget(self.b_auto, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        self._going = False

    def center(self):
        scr = QApplication.primaryScreen()
        if scr is not None:
            self.move(scr.availableGeometry().center() - self.rect().center())

    # ------------------------------------------------------------ 편집
    def edit(self):
        self._going = True
        self.hide()
        self.mw.showMaximized()                # 편집 화면 = 전체 창(v0.7.13)

    # ------------------------------------------------------------ AUTO
    def auto(self):
        self.run_auto()                        # v0.7.96 파일·폴더를 먼저 묻지 않음 → 빈 AUTO 창

    def run_auto(self, files=None):
        """AUTO 창(files 가 있으면 그 파일만, 없으면 빈 목록). [AUTO]·프로젝트 열기 → 본 편집 화면, 취소 → 시작 창."""
        st = self.mw.state
        idxs = []
        if files:
            st.add_files(files)
            want = set(files)
            idxs = [i for i, s in enumerate(st.project.sources) if s.path in want]
        from .ui_auto import AutoDialog
        dlg = AutoDialog(None, st, only=idxs, mw=self.mw)
        scr = QApplication.primaryScreen()
        if scr is not None:
            dlg.move(scr.availableGeometry().center() - dlg.rect().center())
        self.hide()
        dlg.exec()
        if dlg.outcome in ("each", "project"):
            self.edit()
            return
        # 취소: 넣었던 영상은 버림(다음 AUTO 창은 다시 빈 목록, 편집 화면에 남지 않게). 샘플·AE 는 저장할 편집 없음
        from .project import Project
        st.set_project(Project())
        st.dirty = False
        self.show()

    def exit_app(self):
        self.close()

    def closeEvent(self, e):
        if self._going:
            e.accept()
            return
        self.mw.state.dirty = False
        self.mw.close()                        # 디코더·ffmpeg 정리 + 종료
        e.accept()
        QApplication.instance().quit()
