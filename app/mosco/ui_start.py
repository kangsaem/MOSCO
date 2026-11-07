"""시작 창: [AUTO] / [편집]. AUTO = 파일·폴더 고르기 → AUTO 창만 → 보정만 = 본 편집 화면 / 보정+렌더 = 끝나면 종료."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

from . import __version__
from .ui_widgets import QSS, btn


class StartWindow(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        mw.quit_on_close = True
        self.setWindowTitle(f"MOSCO {__version__}")
        self.setFixedSize(400, 220)
        self.setObjectName("start")
        self.setStyleSheet(QSS + "QWidget#start{background:#232323}")      # 본 창과 같은 버튼 모양
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addStretch(1)
        self.b_auto = btn("AUTO", "파일·폴더를 골라 한 번에 보정", size=(160, 48), red=True, on_click=self.auto)
        f = self.b_auto.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.b_auto.setFont(f)
        self.b_edit = btn("편집", "편집 화면 열기", size=(160, 32), on_click=self.edit)
        lay.addWidget(self.b_auto, 0, Qt.AlignHCenter)
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
        self.mw.show()

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
        """고른 파일을 목록에 넣고 그 파일만 AUTO 창으로. 보정만 → 본 편집 화면, 보정+렌더 → 종료, 취소 → 시작 창."""
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
