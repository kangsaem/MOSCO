"""모니터(한 화면 / 반반 두 칸, 확대·이동, 타원·ID 표시) + 시간 막대(검출 상태 줄 + 소스 바)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QWidget

ZOOMS = [0.25, 0.33, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]


def to_qimage(a: np.ndarray) -> QImage:
    a = np.ascontiguousarray(a)
    if a.ndim == 2:
        return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_Grayscale8).copy()
    return QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_RGB888).copy()


class ZoomBox(QWidget):
    """[−] 200% [+]"""
    step = Signal(int)
    pick = Signal(object)        # None = 맞춤, float = 배율

    def __init__(self, parent):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        st = ("QPushButton{background:rgba(20,20,20,200);border:1px solid #555;border-radius:3px;"
              "color:#ddd;font-size:11px;padding:0}")
        self.minus = QPushButton("−")
        self.pct = QPushButton("맞춤")
        self.plus = QPushButton("+")
        for b, w in ((self.minus, 24), (self.pct, 52), (self.plus, 24)):
            b.setFixedSize(w, 22)
            b.setStyleSheet(st)
            b.setFocusPolicy(Qt.NoFocus)
            lay.addWidget(b)
        self.minus.setToolTip("축소 (-)")
        self.plus.setToolTip("확대 (+)")
        self.pct.setToolTip("배율 (0 = 맞춤)")
        self.minus.clicked.connect(lambda: self.step.emit(-1))
        self.plus.clicked.connect(lambda: self.step.emit(1))
        m = QMenu(self)
        m.addAction("맞춤", lambda: self.pick.emit(None))
        for z in (1.0, 2.0, 4.0):
            m.addAction(f"{int(z * 100)}%", lambda z=z: self.pick.emit(z))
        self.pct.setMenu(m)
        self.adjustSize()

    def set_text(self, t):
        self.pct.setText(t)


class Monitor(QWidget):
    picked = Signal(float, float)       # 원본 좌표
    view_changed = Signal()
    selected = Signal(object)           # 타원 key 또는 None
    edited = Signal(object, object)     # key, (cx, cy, w, h, ang)  — 드래그 끝
    add_at = Signal(float, float)       # 타원 추가 위치
    merge_with = Signal(object)         # 합치기 상대 key

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.full = (1920, 1080)
        self.imgs: list[QImage] = []
        self.ells = []          # [{"key", "label", "ell", "manual", "keyed"}]
        self.sel = None
        self.tool = None        # None | "add" | "merge"
        self._edit = None       # (key, mode, p0, ell0)  mode: move | w | h
        self.show_ell = True
        self.show_ids = False
        self.focus_ell = None   # 반반 얼굴 자동 확대용
        self.mode = "fit"       # fit | face | manual
        self.default_mode = "fit"
        self.s = 1.0
        self.c = QPointF(960, 540)
        self.loading = False
        self.picking = False
        self._drag = None
        self.zoom = ZoomBox(self)
        self.zoom.step.connect(self.zoom_step)
        self.zoom.pick.connect(self.zoom_pick)

    # ------------------------------------------------------------ 데이터
    def set_images(self, imgs, full):
        """imgs: numpy RGB(또는 회색) 1장(한 화면) 또는 2장(반반). full = 원본 (w, h)."""
        self.imgs = [to_qimage(a) for a in imgs if a is not None]
        if full != self.full:
            self.full = full
            if self.mode == "manual":
                self.mode = self.default_mode
        self.loading = False
        self.update()
        self._label()

    def clear(self, loading=False):
        self.imgs = []
        self.ells = []
        self.loading = loading
        self.update()

    def set_overlay(self, ells, show_ell, show_ids):
        if self._edit is not None:
            return                 # 드래그 중에는 유지
        self.ells, self.show_ell, self.show_ids = ells or [], show_ell, show_ids
        self.update()

    def set_selected(self, key):
        self.sel = key
        self.update()

    def set_tool(self, tool):
        self.tool = tool
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)

    # ------------------------------------------------------------ 타원 맞추기
    def _hit(self, q: QPointF, s):
        """q: 원본 좌표. 반환 (entry, mode) — 선택된 타원 손잡이 우선."""
        import math
        tol = 8 / max(s, 1e-3)
        for e in self.ells:
            if e["key"] != self.sel:
                continue
            cx, cy, w, h, ang = e["ell"]
            a = math.radians(ang)
            ux, uy = math.cos(a), math.sin(a)
            for mode, (dx, dy) in (("w", (ux * w / 2, uy * w / 2)), ("w", (-ux * w / 2, -uy * w / 2)),
                                   ("h", (-uy * h / 2, ux * h / 2)), ("h", (uy * h / 2, -ux * h / 2))):
                if abs(q.x() - (cx + dx)) <= tol and abs(q.y() - (cy + dy)) <= tol:
                    return e, mode
        best = None
        for e in self.ells:
            cx, cy, w, h, ang = e["ell"]
            a = math.radians(-ang)
            x, y = q.x() - cx, q.y() - cy
            xr, yr = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
            d = (xr / max(w / 2, 1)) ** 2 + (yr / max(h / 2, 1)) ** 2
            if d <= 1.05 and (best is None or w * h < best["ell"][2] * best["ell"][3]):
                best = e
        return (best, "move") if best else (None, None)

    def set_default(self, mode):
        """반반 뷰티 = face, 나머지 = fit. 수동 확대 중이면 유지."""
        self.default_mode = mode
        if self.mode != "manual":
            self.mode = mode
        self.update()
        self._label()

    def set_focus_ellipse(self, ell):
        self.focus_ell = ell
        if self.mode == "face":
            self.update()
            self._label()

    def set_picking(self, v):
        self.picking = v
        self.setCursor(Qt.CrossCursor if v else Qt.ArrowCursor)

    # ------------------------------------------------------------ 좌표
    def panes(self):
        r = QRectF(self.rect())
        if len(self.imgs) >= 2:
            w = (r.width() - 4) / 2
            return [QRectF(0, 0, w, r.height()), QRectF(w + 4, 0, w, r.height())]
        return [r]

    def _fit(self, pane):
        W, H = self.full
        return min(pane.width() / W, pane.height() / H), QPointF(W / 2, H / 2)

    def xform(self, pane):
        if self.mode == "manual":
            return self.s, self.c
        if self.mode == "face" and self.focus_ell is not None:
            cx, cy, ew, eh, _ = self.focus_ell
            bw, bh = max(ew, eh * 0.2) * 1.12, eh * 1.12
            s = min(pane.width() / bw, pane.height() / bh, 6.0)
            return s, QPointF(cx, cy)
        return self._fit(pane)

    def to_img(self, pos: QPointF):
        for pane in self.panes():
            if pane.contains(pos):
                s, c = self.xform(pane)
                return QPointF(c.x() + (pos.x() - pane.center().x()) / s, c.y() + (pos.y() - pane.center().y()) / s), pane
        return None, None

    def _label(self):
        pane = self.panes()[0]
        s, _ = self.xform(pane)
        self.zoom.set_text("맞춤" if self.mode == "fit" else ("얼굴" if self.mode == "face" else f"{round(s * 100)}%"))

    def _set_manual(self, s, c):
        self.mode = "manual"
        self.s = max(0.05, min(16.0, s))
        self.c = c
        self.update()
        self._label()
        self.view_changed.emit()

    def zoom_step(self, d, at: QPointF | None = None):
        pane = self.panes()[0]
        s, c = self.xform(pane)
        cur = s
        if d > 0:
            ns = next((z for z in ZOOMS if z > cur * 1.01), cur * 1.25)
        else:
            ns = next((z for z in reversed(ZOOMS) if z < cur * 0.99), cur / 1.25)
        if at is not None:
            q, pn = self.to_img(at)
            if q is not None:
                pc = pn.center()
                nc = QPointF(q.x() - (at.x() - pc.x()) / ns, q.y() - (at.y() - pc.y()) / ns)
                self._set_manual(ns, nc)
                return
        self._set_manual(ns, c)

    def zoom_pick(self, z):
        if z is None:
            self.reset_view()
            return
        _, c = self.xform(self.panes()[0])
        self._set_manual(z, c)

    def reset_view(self):
        self.mode = self.default_mode
        self.update()
        self._label()
        self.view_changed.emit()

    # ------------------------------------------------------------ 입력
    def wheelEvent(self, e):
        d = 1 if e.angleDelta().y() > 0 else -1
        self.zoom_step(d, e.position())

    def mousePressEvent(self, e):
        q, pane = self.to_img(e.position())
        if e.button() == Qt.LeftButton and self.picking:
            if q is not None:
                self.picked.emit(q.x(), q.y())
            return
        if e.button() == Qt.LeftButton and q is not None and self.tool == "add":
            self.add_at.emit(q.x(), q.y())
            return
        if e.button() == Qt.LeftButton and q is not None and self.show_ell:
            s, _ = self.xform(pane)
            ent, mode = self._hit(q, s)
            if self.tool == "merge":
                if ent is not None:
                    self.merge_with.emit(ent["key"])
                return
            if ent is not None:
                if ent["key"] != self.sel:
                    self.sel = ent["key"]
                    self.selected.emit(ent["key"])
                self._edit = [ent["key"], mode, q, tuple(ent["ell"]), s, False]
                self.update()
                return
            if self.sel is not None:
                self.sel = None
                self.selected.emit(None)
        if e.button() in (Qt.LeftButton, Qt.MiddleButton):
            pane = self.panes()[0]
            s, c = self.xform(pane)
            self._drag = (e.position(), s, QPointF(c))

    def mouseMoveEvent(self, e):
        if self._edit:
            import math
            key, mode, q0, ell0, s, _ = self._edit
            q, _ = self.to_img(e.position())
            if q is None:
                return
            cx, cy, w, h, ang = ell0
            if mode == "move":
                ell = (cx + q.x() - q0.x(), cy + q.y() - q0.y(), w, h, ang)
            else:
                a = math.radians(ang)
                x, y = q.x() - cx, q.y() - cy
                if mode == "w":
                    w = max(8.0, 2 * abs(x * math.cos(a) + y * math.sin(a)))
                else:
                    h = max(8.0, 2 * abs(-x * math.sin(a) + y * math.cos(a)))
                ell = (cx, cy, w, h, ang)
            for ent in self.ells:
                if ent["key"] == key:
                    ent["ell"] = ell
            self._edit[5] = True
            self.update()
            return
        if self._drag:
            p0, s, c = self._drag
            d = e.position() - p0
            if self.mode != "manual" and d.manhattanLength() < 4:
                return
            self._set_manual(s, QPointF(c.x() - d.x() / s, c.y() - d.y() / s))

    def mouseReleaseEvent(self, e):
        if self._edit:
            key, mode, q0, ell0, s, moved = self._edit
            self._edit = None
            if moved:
                for ent in self.ells:
                    if ent["key"] == key:
                        self.edited.emit(key, tuple(ent["ell"]))
            return
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.reset_view()

    def resizeEvent(self, e):
        self.zoom.adjustSize()
        self.zoom.move(self.width() - self.zoom.width() - 8, self.height() - self.zoom.height() - 8)
        self._label()

    # ------------------------------------------------------------ 그리기
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0e0e0e"))
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        W, H = self.full
        if not self.imgs:
            if self.loading:
                p.setPen(QColor("#888"))
                p.drawText(self.rect(), Qt.AlignCenter, "로딩 중…")
            return
        for pane, img in zip(self.panes(), self.imgs):
            p.save()
            p.setClipRect(pane)
            p.fillRect(pane, QColor("#0e0e0e"))
            s, c = self.xform(pane)
            ox = pane.center().x() - c.x() * s
            oy = pane.center().y() - c.y() * s
            p.drawImage(QRectF(ox, oy, W * s, H * s), img)
            if self.show_ell and self.ells:
                f = QFont(p.font())
                f.setBold(True)
                f.setPointSize(9)
                p.setFont(f)
                for ent in self.ells:
                    cx, cy, ew, eh, ang = ent["ell"]
                    sel = ent["key"] == self.sel and self.sel is not None
                    col = QColor("#ffffff") if sel else (QColor("#7fd1ff") if ent.get("manual") else QColor("#f2c94c"))
                    pen = QPen(col, 2 if not sel else 2.5, Qt.SolidLine if sel else Qt.DashLine)
                    p.setPen(pen)
                    p.save()
                    p.translate(ox + cx * s, oy + cy * s)
                    p.rotate(ang)
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(QPointF(0, 0), ew / 2 * s, eh / 2 * s)
                    if sel:
                        p.setBrush(QColor("#ffffff"))
                        for hx, hy in ((ew / 2, 0), (-ew / 2, 0), (0, eh / 2), (0, -eh / 2)):
                            p.drawRect(QRectF(hx * s - 4, hy * s - 4, 8, 8))
                    p.restore()
                    if ent.get("keyed"):
                        p.setPen(Qt.NoPen)
                        p.setBrush(col)
                        kx, ky = ox + cx * s, oy + (cy - eh / 2) * s - 10
                        p.drawPolygon([QPointF(kx, ky - 5), QPointF(kx + 5, ky), QPointF(kx, ky + 5), QPointF(kx - 5, ky)])
                    lab = ent.get("label")
                    if self.show_ids and lab:
                        tx, ty = ox + (cx - ew / 2) * s, oy + (cy - eh / 2) * s - 4
                        r = QRectF(tx, ty - 14, 8 + 7 * len(lab), 15)
                        p.fillRect(r, col)
                        p.setPen(QColor("#111"))
                        p.drawText(r, Qt.AlignCenter, lab)
            p.restore()


# ------------------------------------------------------------ 시간 막대
class TimeBar(QWidget):
    """위: 검출 상태 줄(6px) · 아래: 소스 바(24px) + 재생 위치(빨간 선) + 선택 타원의 키(◆).
    클릭·드래그 = 이동, ◆ 우클릭 = 키 삭제."""
    seek = Signal(int)
    del_key = Signal(int)

    COLORS = {0: QColor("#555"), 1: QColor("#3d9a8b"), 2: QColor("#d9822b")}

    def __init__(self):
        super().__init__()
        self.setFixedHeight(32)
        self.n = 0
        self.cur = 0
        self.cov = None          # np.int8 per frame: 0 없음, 1 검출, 2 메움, -1 분석 전
        self.progress = None     # 분석 진행 0~1
        self.error = ""
        self.keys = []
        self.setMouseTracking(True)

    def set_keys(self, keys):
        self.keys = list(keys)
        self.update()

    def _key_at(self, x):
        for k in self.keys:
            if abs(self._x(k) - x) <= 6:
                return k
        return None

    def set_data(self, n, cov=None, progress=None, error=""):
        self.n, self.cov, self.progress, self.error = n, cov, progress, error
        self.setToolTip(error)
        self.update()

    def set_cur(self, i):
        self.cur = i
        self.update()

    def _x(self, i):
        return (i + 0.5) / max(1, self.n) * self.width()

    def mousePressEvent(self, e):
        k = self._key_at(e.position().x())
        if e.button() == Qt.RightButton:
            if k is not None:
                self.del_key.emit(k)
            return
        if k is not None:
            self.seek.emit(k)
            return
        self._go(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._go(e.position().x())

    def _go(self, x):
        if self.n:
            self.seek.emit(int(max(0, min(self.n - 1, x / max(1, self.width()) * self.n))))

    def paintEvent(self, e):
        p = QPainter(self)
        w = self.width()
        p.fillRect(0, 0, w, 6, QColor("#202020"))
        if self.cov is not None and self.n:
            cols = np.minimum((np.arange(w) / w * self.n).astype(int), self.n - 1)
            run_c, start = None, 0
            vals = self.cov[cols]
            for x in range(w + 1):
                v = vals[x] if x < w else None
                if v != run_c:
                    if run_c is not None and run_c >= 0:
                        p.fillRect(start, 0, x - start, 6, self.COLORS.get(int(run_c), QColor("#555")))
                    run_c, start = v, x
        elif self.progress is not None:
            p.fillRect(0, 0, int(w * self.progress), 6, QColor("#3a6f66"))
        bar = QColor("#a03030") if self.error else QColor("#5b7fa6")
        p.fillRect(0, 8, w, 24, bar)
        if self.n:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            for k in self.keys:
                kx = self._x(k)
                p.drawPolygon([QPointF(kx, 11), QPointF(kx + 5, 16), QPointF(kx, 21), QPointF(kx - 5, 16)])
            x = self._x(self.cur)
            p.fillRect(QRectF(x - 1, 8, 2, 24), QColor("#e5484d"))
