"""시작 창: [AUTO] / [편집]. AUTO = 파일·폴더 고르기 → AUTO 창만 → 보정+편집 = 본 편집 화면 / 보정+렌더 = 끝나면 종료.
v0.7.73: 윈도우 제목 표시줄 없앰 → 위쪽 정사각형 그림(assets/start.png, 400×400) + 그 위 왼쪽 위 'MOSCO 버전',
오른쪽 위 ✕(= 종료), 빈 곳을 끌어 옮김. v0.7.74: 1px 테두리 없앰(사용자 요청). 그림이 없으면 회색 자리."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from . import __version__, paths
from .ui_widgets import QSS, DragMove, btn

IMG = 400                                      # 그림 한 변(논리 px). 그림 파일은 800×800(고배율 화면용)
W, H = IMG, IMG + 160                          # v0.7.74 테두리 1px 없앰 → 400×560


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

        # 아래: 버튼(예전과 같음)
        lay.addStretch(1)
        self.b_auto = btn("AUTO", "파일·폴더를 골라 한 번에 보정", size=(160, 48), red=True, on_click=self.auto)
        f = self.b_auto.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.b_auto.setFont(f)
        self.b_edit = btn("편집", "편집 화면 열기", size=(160, 32), on_click=self.edit)
        lay.addWidget(self.b_auto, 0, Qt.AlignHCenter)
        lay.addSpacing(12)
        lay.addWidget(self.b_edit, 0, Qt.AlignHCenter)
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
        m = QMenu(self)
        a_file = m.addAction("파일…")
        a_dir = m.addAction("폴더…")
        a = m.exec(self.b_auto.mapToGlobal(QPoint(0, self.b_auto.height())))
        if a is a_file:
            files = self.mw.pick_files(self)
        elif a is a_dir:
            files = self.mw.pick_folder(self)
            if files == []:
                self.mw.flash("이 폴더에 영상이 없습니다")
        else:
            return
        if files:
            self.run_auto(files)

    def run_auto(self, files):
        """고른 파일을 목록에 넣고 그 파일만 AUTO 창으로. 보정+편집 → 본 편집 화면, 보정+렌더 → 종료, 취소 → 시작 창."""
        st = self.mw.state
        st.add_files(files)
        want = set(files)
        idxs = [i for i, s in enumerate(st.project.sources) if s.path in want]
        if not idxs:
            return
        from .ui_auto import AutoDialog
        dlg = AutoDialog(None, st, only=idxs, mw=self.mw)
        scr = QApplication.primaryScreen()
        if scr is not None:
            dlg.move(scr.availableGeometry().center() - dlg.rect().center())
        self.hide()
        dlg.exec()
        if dlg.outcome == "each":
            self.edit()
            return
        st.dirty = False                       # 시작 창 흐름의 렌더·샘플은 저장할 편집이 없음
        if dlg.outcome == "render":            # 보정+렌더가 다 끝나면 프로그램 종료
            self.exit_app()
            return
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
