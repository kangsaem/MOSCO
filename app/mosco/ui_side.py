"""오른쪽: 소스 목록(회색 바) + [영상 추가][삭제] / 렌더([⚙][렌더] + 렌더 리스트) / 렌더 설정 창."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QSpinBox, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from . import ff
from .ui_widgets import BTN_MAIN, PROG, PROG_TXT, DragMove, btn, tone

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".mts", ".m2ts", ".avi", ".mkv"}
MODE_TXT = {"beauty": "뷰티", "blur": "블러"}


class SourceDelegate(QStyledItemDelegate):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def sizeHint(self, opt, idx):
        return QSize(200, 27)

    def paint(self, p: QPainter, opt, idx):
        i = idx.row()
        srcs = self.state.project.sources
        if i >= len(srcs):
            return
        s = srcs[i]
        r = opt.rect.adjusted(0, 1, 0, -2)
        err = self.state.errors.get(s.path)
        a = self.state.analysis(s)
        # 진행 단계: 렌더·샘플(state.busy) > 분석(진행 중) — 색 = ui_widgets.PROG(DTME 와 같음)
        kind, prog, label = None, None, ""
        b = self.state.busy.get(s.path)
        if b:
            kind, prog = b[0], b[1]
            label = b[2] if len(b) > 2 and b[2] else f"{PROG_TXT[kind]} {int(prog * 100)}%"
        elif a is not None and a["state"] == "run":
            kind, prog = "analyze", a["p"]
            label = f"분석 {int(prog * 100)}%"
        if err:
            kind, prog, label = None, None, ("분석 실패" if str(err).startswith("분석 실패") else "읽기 실패")
        base = QColor("#c9c9c9") if err else QColor("#2d2d2c")     # 읽기 실패 = 밝은 회색(누르면 다시 읽기)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(r, 3, 3)
        if kind and prog is not None and not err:          # 줄 바탕을 진행만큼 채움
            p.setClipRect(QRect(r.left(), r.top(), int(r.width() * max(0.0, min(1.0, prog))), r.height()))
            p.setBrush(QColor(PROG[kind][0]))
            p.drawRoundedRect(r, 3, 3)
            p.setClipping(False)
        if opt.state & QStyle.State_Selected:
            p.setPen(QColor("#4a8fe0"))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        f = QFont(opt.font)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#111") if err else QColor("#eee"))
        rw = 58 + (p.fontMetrics().horizontalAdvance(label) + 10 if label else 0)
        p.drawText(r.adjusted(8, 0, -48, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(s.name, Qt.ElideMiddle, r.width() - rw))
        if label:
            f2 = QFont(opt.font)
            p.setFont(f2)
            p.setPen(QColor("#b83a2e") if err else QColor(PROG[kind][1]))
            p.drawText(r.adjusted(0, 0, -48, 0), Qt.AlignVCenter | Qt.AlignRight, label)
        tag = QRect(r.right() - 40, r.top() + 5, 34, r.height() - 10)
        p.setBrush(QColor("#555"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(tag, 2, 2)
        f.setBold(False)
        f.setPointSize(max(7, f.pointSize() - 2))
        p.setFont(f)
        p.setPen(QColor("#eee"))
        p.drawText(tag, Qt.AlignCenter, MODE_TXT[s.mode] + ("·C" if s.custom else ""))
        p.restore()


class SourceList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.setItemDelegate(SourceDelegate(state))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSpacing(1)
        self.setMouseTracking(True)
        self.itemSelectionChanged.connect(self._sel)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        state.changed.connect(self.refresh)
        state.analysis_changed.connect(lambda *_: self.viewport().update())
        state.busy_changed.connect(lambda *_: self.viewport().update())
        state.current_changed.connect(self._cur)

    def refresh(self):
        n = len(self.state.project.sources)
        if self.count() != n:
            self.blockSignals(True)
            self.clear()
            for s in self.state.project.sources:
                it = QListWidgetItem("")
                it.setToolTip(s.path)
                self.addItem(it)
            if 0 <= self.state.cur < n:
                self.setCurrentRow(self.state.cur)
            self.blockSignals(False)
        for i, s in enumerate(self.state.project.sources):
            it = self.item(i)
            err = self.state.errors.get(s.path)
            it.setToolTip(f"{s.path}\n{err}" if err else s.path)
        self.viewport().update()

    def _cur(self):
        if 0 <= self.state.cur < self.count() and self.currentRow() != self.state.cur:
            self.blockSignals(True)
            self.setCurrentRow(self.state.cur)
            self.blockSignals(False)
        self.viewport().update()

    def _sel(self):
        r = self.currentRow()
        if r >= 0:
            self.state.select(r)

    def selected(self):
        return sorted(self.row(i) for i in self.selectedItems())

    def mousePressEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if it is not None and e.button() == Qt.LeftButton:
            s = self.state.project.sources[self.row(it)]
            if s.path in self.state.errors:        # 밝은 회색 바(읽기 실패) 클릭 = 다시 시도
                self.state.retry(s.path)
        super().mousePressEvent(e)

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("불러오기", self.parent().add_dialog if hasattr(self.parent(), "add_dialog") else lambda: None)
        m.exec(self.mapToGlobal(pos))


class RenderItem:
    def __init__(self, src, rs, out_dir):
        self.src = copy.deepcopy(src)
        self.rs = dict(rs)
        self.out_dir = out_dir
        self.state = "대기"       # 대기 | 진행 | 완료 | 오류 | 취소
        self.p = 0.0
        self.err = ""
        self.outs = {}
        self.stats = {}           # 렌더 끝나면 프레임·fps·작업자 수(render.render 가 채움)
        self.job = None


class RenderDelegate(QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    def sizeHint(self, opt, idx):
        return QSize(200, 25)

    def paint(self, p, opt, idx):
        items = self.panel.items
        if idx.row() >= len(items):
            return
        it = items[idx.row()]
        r = opt.rect.adjusted(0, 1, 0, -2)
        done = it.state == "완료"
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#c9c9c9") if done else (QColor("#6a2a24") if it.state == "오류" else QColor("#3c3c3c")))
        p.drawRoundedRect(r, 3, 3)
        if it.state == "진행":
            p.setBrush(QColor("#cf3b2b"))
            p.drawRect(QRect(r.left(), r.bottom() - 2, int(r.width() * it.p), 3))
        if opt.state & QStyle.State_Selected:
            p.setPen(QColor("#4a8fe0"))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        f = QFont(opt.font)
        f.setPointSize(max(7, f.pointSize() - 1))
        p.setFont(f)
        p.setPen(QColor("#111") if done else QColor("#ddd"))
        st = f"{int(it.p * 100)}%" if it.state == "진행" else it.state
        p.drawText(r.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignRight, st)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r.adjusted(6, 0, -50, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(it.src.name, Qt.ElideMiddle, r.width() - 60))
        p.restore()


class RenderPanel(QWidget):
    settings_clicked = Signal()
    render_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.items: list[RenderItem] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)
        r = QHBoxLayout()
        r.setSpacing(4)
        self.b_set = btn("⚙", "렌더 설정", size=BTN_MAIN, w=40, on_click=self.settings_clicked.emit)
        self.b_render = btn("렌더", "렌더 목록(대기)을 모두 렌더 (Ctrl+R). ➜ 로 목록에 보낸 뒤 사용", size=BTN_MAIN, red=True, on_click=self.render_clicked.emit)
        r.addWidget(self.b_set)
        r.addStretch(1)
        r.addWidget(self.b_render)
        lay.addLayout(r)
        self.list = QListWidget()
        self.list.setItemDelegate(RenderDelegate(self))
        self.list.setSpacing(1)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.list, 1)
        self.on_remove = None
        self.on_rerender = None

    def add(self, item: RenderItem):
        self.items.append(item)
        li = QListWidgetItem("")
        li.setToolTip(item.src.path)
        self.list.addItem(li)

    def refresh(self):
        for i, it in enumerate(self.items):
            li = self.list.item(i)
            tip = it.src.path
            if it.outs:
                tip += "\n→ " + "\n→ ".join(it.outs.values())
            if it.stats:
                tip += "\n" + stat_line(it.stats)
            if it.err:
                tip += "\n" + it.err
            li.setToolTip(tip)
        self.list.viewport().update()

    def remove_rows(self, rows):
        for r in sorted(rows, reverse=True):
            it = self.items[r]
            if it.job is not None and it.state == "진행":
                it.job.cancel.set()
            self.items.pop(r)
            self.list.takeItem(r)

    def _menu(self, pos):
        rows = sorted(self.list.row(i) for i in self.list.selectedItems())
        if not rows:
            return
        m = QMenu(self)
        m.addAction("삭제", lambda: self.remove_rows(rows))
        m.addAction("다시 렌더", lambda: self.on_rerender and self.on_rerender(rows))
        m.exec(self.list.mapToGlobal(pos))

    def _open(self, li):
        it = self.items[self.list.row(li)]
        if it.outs:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(next(iter(it.outs.values()))).parent)))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.remove_rows(sorted(self.list.row(i) for i in self.list.selectedItems()))
            return
        super().keyPressEvent(e)


def stat_line(st: dict) -> str:
    a = st.get("분석fps")
    an = f" · 분석 {a}fps" if a not in (None, "", "캐시") else ""
    return (f"{st.get('프레임', 0)}f · 렌더 {st.get('렌더fps', 0)}fps{an}"
            f" · 작업자 {st.get('작업자', '?')}/{st.get('코어', '?')}코어")


class RenderSettings(DragMove, QDialog):
    def __init__(self, parent, rs: dict):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)     # DTME 렌더 설정 창과 같은 모양
        self.setWindowTitle("렌더 설정")
        self.setObjectName("RenderSet")
        self.setStyleSheet("#RenderSet{background:#262626; border:1px solid #555; border-radius:6px;}")
        self.setMinimumWidth(500)
        self.rs = dict(rs)
        f = QFormLayout(self)
        f.setContentsMargins(16, 12, 16, 14)
        title = QLabel("렌더 설정")
        title.setStyleSheet("font-weight:700; color:#e0e0e0;")
        f.addRow(title)
        f.setLabelAlignment(Qt.AlignRight)
        f.setHorizontalSpacing(10)
        f.setVerticalSpacing(10)
        r = QHBoxLayout()
        self.dir = QLineEdit(rs.get("out_dir", ""))
        r.addWidget(self.dir, 1)
        r.addWidget(btn("찾기…", on_click=self._browse))
        f.addRow("저장폴더", r)
        self.name = QLineEdit(rs.get("name", ""))
        self.name.setPlaceholderText("원본이름_MOSCO")
        f.addRow("명칭", self.name)
        r = QHBoxLayout()
        self.main = QCheckBox("ProRes")
        self.main.setChecked(rs.get("main", True))
        self.prores = QComboBox()
        self.prores.addItems(list(ff.PRORES))
        self.prores.setCurrentText(rs.get("prores", "422 HQ"))
        r.addWidget(self.main)
        r.addWidget(self.prores)
        r.addStretch(1)
        f.addRow("완성본", r)
        self.overlay = QCheckBox("ProRes 4444 알파")
        self.overlay.setChecked(rs.get("overlay", True))
        self.overlay.setToolTip("블러 모드만")
        f.addRow("블러 오버레이", self.overlay)
        self.review = QCheckBox("H.264 · 타원 · ID")
        self.review.setChecked(rs.get("review", False))
        f.addRow("검수 영상", self.review)
        self.audio = QCheckBox("원본 PCM")
        self.audio.setChecked(rs.get("audio", True))
        f.addRow("오디오", self.audio)
        self.hw = QComboBox()
        self.hw.addItems(["CPU", "GPU (NVDEC)"])
        self.hw.setCurrentIndex(1 if rs.get("hw") else 0)
        f.addRow("디코딩", self.hw)
        self.two = QComboBox()
        self.two.addItems(["앞뒤 2회", "1회"])
        self.two.setCurrentIndex(0 if rs.get("two_pass", True) else 1)
        self.two.setToolTip("앞뒤 2회 = 얼굴이 처음 잡히기 전 구간도 거슬러 덮음")
        f.addRow("블러 처리", self.two)
        from . import models
        self.gpu = QComboBox()
        gname = models.gpu_name()
        self.gpu.addItems(["CPU"] + ([gname] if gname else []))
        self.gpu.setCurrentIndex(1 if (rs.get("gpu") and gname) else 0)
        self.gpu.setEnabled(gname is not None)
        self.gpu.setToolTip("얼굴·사람 검출을 GPU 로. 분석·AUTO 가 빨라짐" + ("" if gname else " (이 PC에 쓸 수 있는 GPU 가속기가 없음)"))
        f.addRow("검출 장치", self.gpu)
        import os
        from .render import auto_workers
        self.workers = QSpinBox()
        self.workers.setRange(0, os.cpu_count() or 64)
        self.workers.setSpecialValueText(f"자동 ({auto_workers()})")
        self.workers.setValue(int(rs.get("workers") or 0))
        self.workers.setToolTip(f"렌더 동시 처리 프레임 수(스레드). 0 = 자동. 이 PC {os.cpu_count()}코어\n"
                                "속도 기록: logs/render_log.csv")
        r = QHBoxLayout()
        r.addWidget(self.workers)
        r.addStretch(1)
        f.addRow("렌더 작업자", r)
        r = QHBoxLayout()
        r.addStretch(1)
        r.addWidget(btn("취소", on_click=self.reject))
        r.addWidget(tone(btn("확인", on_click=self.accept), "blue"))      # DTME: 확인 = 파랑, 오른쪽
        f.addRow(r)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "저장폴더", self.dir.text())
        if d:
            self.dir.setText(d)

    def value(self):
        return {"out_dir": self.dir.text().strip(), "name": self.name.text().strip(), "main": self.main.isChecked(),
                "prores": self.prores.currentText(), "overlay": self.overlay.isChecked(),
                "review": self.review.isChecked(), "audio": self.audio.isChecked(),
                "hw": self.hw.currentIndex() == 1, "two_pass": self.two.currentIndex() == 0,
                "gpu": self.gpu.currentIndex() == 1, "workers": self.workers.value()}
