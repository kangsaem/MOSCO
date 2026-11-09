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
        from .ui_widgets import tone
        self.minus = QPushButton("−")
        self.pct = QPushButton("맞춤")
        self.plus = QPushButton("+")
        for b, w in ((self.minus, 24), (self.pct, 52), (self.plus, 24)):
            b.setFixedSize(w, 22)
            tone(b, "overlay")                    # 모니터 위 = 반투명 입체감(DTME)
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
    selected = Signal(object, object)   # 타원 key(또는 None), 소스 모드(blur/sign/None)
    edited = Signal(object, object, object)  # key, (cx, cy, w, h, ang), 소스 모드  — 드래그 끝
    add_at = Signal(float, float)       # 타원 추가 위치(클릭)
    drawn = Signal(float, float, float, float)   # 드래그로 그린 새 타원 cx,cy,w,h(원본 좌표)
    delete_ell = Signal(object, object)  # 삭제할 타원 key, 소스 모드
    merge_with = Signal(object, object)  # 합치기 상대 key, 소스 모드

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.full = (1920, 1080)
        self.imgs: list[QImage] = []
        self.ells = []          # [{"key", "label", "ell", "manual", "keyed"}]
        self.sel = None
        self.tool = None        # None | "add" | "del" | "merge"
        self._edit = None       # (key, mode, p0, ell0)  mode: move | w | h
        self._draw = None       # (q0, q1) 드래그로 타원 그리는 중(원본 좌표)
        self.show_ell = True
        self.show_ids = False
        self.quad_mode = False      # 간판 모드: 새로 그리는 모양 = 사각형
        self.focus_ell = None   # 반반 얼굴 자동 확대용
        self.mode = "fit"       # fit | face | manual
        self.default_mode = "fit"
        self.s = 1.0
        self.c = QPointF(960, 540)
        self.loading = False
        self.picking = False
        self.playing = False        # 재생 중에는 부드러운 확대 끔(빠르게)
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
            if e.get("quad") is not None:          # 간판: 꼭짓점 4개 손잡이
                for k, (x, y) in enumerate(e["quad"]):
                    if abs(q.x() - x) <= tol and abs(q.y() - y) <= tol:
                        return e, f"c{k}"
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
            if e.get("quad") is not None:
                import numpy as np
                import cv2
                inside = cv2.pointPolygonTest(np.asarray(e["quad"], np.float32), (q.x(), q.y()), False) >= 0
                if inside and (best is None or w * h < best["ell"][2] * best["ell"][3]):
                    best = e
                continue
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
            # 드래그로 크기 지정하며 그리기 시작(놓을 때 drawn 발생). 켠 채로 계속 그림.
            self._draw = [q, q]
            self.update()
            return
        if e.button() == Qt.LeftButton and q is not None and self.tool == "del" and self.show_ell:
            s, _ = self.xform(pane)
            ent, _ = self._hit(q, s)
            if ent is not None:
                self.delete_ell.emit(ent["key"], ent.get("mode"))
            return
        if e.button() == Qt.LeftButton and q is not None and self.show_ell:
            s, _ = self.xform(pane)
            ent, mode = self._hit(q, s)
            if self.tool == "merge":
                if ent is not None:
                    self.merge_with.emit(ent["key"], ent.get("mode"))
                return
            if ent is not None:
                if ent["key"] != self.sel:
                    self.sel = ent["key"]
                    self.selected.emit(ent["key"], ent.get("mode"))
                g0 = tuple(v for p in ent["quad"] for v in p) if ent.get("quad") is not None else tuple(ent["ell"])
                self._edit = [ent["key"], mode, q, g0, s, False, ent.get("mode")]
                self.update()
                return
            if self.sel is not None:
                self.sel = None
                self.selected.emit(None, None)
        if e.button() in (Qt.LeftButton, Qt.MiddleButton):
            pane = self.panes()[0]
            s, c = self.xform(pane)
            self._drag = (e.position(), s, QPointF(c))

    def mouseMoveEvent(self, e):
        if self._draw is not None:
            q, _ = self.to_img(e.position())
            if q is not None:
                self._draw[1] = q
                self.update()
            return
        if self._edit:
            import math
            key, mode, q0, ell0, s, _, _smode = self._edit
            q, _ = self.to_img(e.position())
            if q is None:
                return
            if len(ell0) == 8:                       # 간판 4꼭짓점: 꼭짓점 하나 또는 통째로 이동
                v = list(ell0)
                if mode == "move":
                    dx, dy = q.x() - q0.x(), q.y() - q0.y()
                    v = [v[i] + (dx if i % 2 == 0 else dy) for i in range(8)]
                else:
                    k = int(mode[1])
                    v[2 * k], v[2 * k + 1] = q.x(), q.y()
                for ent in self.ells:
                    if ent["key"] == key:
                        ent["quad"] = [(v[2 * i], v[2 * i + 1]) for i in range(4)]
                        xs, ys = v[0::2], v[1::2]
                        ent["ell"] = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(xs) - min(xs),
                                      max(ys) - min(ys), 0.0)
                self._edit[5] = True
                self.update()
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
        if self._draw is not None:
            q0, q1 = self._draw
            self._draw = None
            cx, cy = (q0.x() + q1.x()) / 2, (q0.y() + q1.y()) / 2
            w, h = abs(q1.x() - q0.x()), abs(q1.y() - q0.y())
            if w < 12 or h < 12:                 # 거의 클릭 → 기본 크기 타원
                self.add_at.emit(q0.x(), q0.y())
            else:
                self.drawn.emit(cx, cy, w, h)
            self.update()
            return
        if self._edit:
            key, mode, q0, ell0, s, moved, smode = self._edit
            self._edit = None
            if moved:
                for ent in self.ells:
                    if ent["key"] == key:
                        if ent.get("quad") is not None:
                            self.edited.emit(key, tuple(v for p in ent["quad"] for v in p), smode)     # 8값
                        else:
                            self.edited.emit(key, tuple(ent["ell"]), smode)
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
        p.setRenderHint(QPainter.SmoothPixmapTransform, not self.playing)   # 재생 중 = 빠른 확대
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
                    # 종류별 색: 선택=흰·제외=초록·간판=청록·추가=파랑·얼굴=노랑
                    col = (QColor("#ffffff") if sel else QColor("#6fd38a") if ent.get("minus")
                           else QColor("#3fd8c4") if ent.get("quad") is not None
                           else QColor("#7fd1ff") if ent.get("manual") else QColor("#f2c94c"))
                    pen = QPen(col, 2 if not sel else 2.5, Qt.SolidLine if sel else Qt.DashLine)
                    p.setPen(pen)
                    if ent.get("quad") is not None:          # 간판 4꼭짓점
                        pts = [QPointF(ox + x * s, oy + y * s) for x, y in ent["quad"]]
                        p.setBrush(Qt.NoBrush)
                        p.drawPolygon(pts)
                        if sel:
                            p.setBrush(QColor("#ffffff"))
                            for pt in pts:
                                p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
                    else:
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
            if self._draw is not None and pane is self.panes()[0]:
                q0, q1 = self._draw
                cx, cy = (q0.x() + q1.x()) / 2, (q0.y() + q1.y()) / 2
                ew, eh = abs(q1.x() - q0.x()), abs(q1.y() - q0.y())
                p.setPen(QPen(QColor("#7fd1ff"), 2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                if self.quad_mode:
                    p.drawRect(QRectF(ox + (cx - ew / 2) * s, oy + (cy - eh / 2) * s, ew * s, eh * s))
                else:
                    p.drawEllipse(QPointF(ox + cx * s, oy + cy * s), ew / 2 * s, eh / 2 * s)
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


# 레이어(타원·사각형) 종류별 색 — 오버레이·레인 공통. (sel = 선택 시 밝게)
LAYER_COLORS = {
    "minus":  ("#3f7a52", "#6fd38a"),    # 제외 = 초록
    "sign":   ("#2e6a64", "#3fb8a0"),    # 간판 = 청록
    "manual": ("#3d6690", "#5aa0f0"),    # 추가 타원 = 파랑
    "face":   ("#8a7324", "#e6c24a"),    # 얼굴(자동) = 노랑
}


def layer_kind(d) -> str:
    if d.get("minus"):
        return "minus"
    if d.get("quad"):
        return "sign"
    if d.get("manual"):
        return "manual"
    return "face"


def _lane_color(d, sel):
    lo, hi = LAYER_COLORS[layer_kind(d)]
    return QColor(hi if sel else lo)


# ------------------------------------------------------------ 타원별 레인(타임라인)
class EllipseLanes(QWidget):
    """타원마다 가는 레인: 존재 구간 바 + 키 ◆ + 재생선. 양 끝 핸들 드래그로 in/out(처음/끝까지).
    레인(바 가운데) 클릭 = 그 타원 선택."""
    select = Signal(object, object)          # (타원 key, mode) — 동시 블러 때 그 종류로 전환
    segments_changed = Signal(object, object)  # key, [(in, out), ...] — 구간별 양끝 편집·틈 메우기

    LANE_H = 16
    PAD = 3

    def __init__(self):
        super().__init__()
        self.spans = []          # [{key,label,intervals,keys,n}]
        self.n = 0
        self.cur = 0
        self.sel = None
        self.setMouseTracking(True)
        self._drag = None        # (lane_idx, "in"|"out")
        self._preview = None     # (key, f_in, f_out) 드래그 중 미리보기
        self._fit()

    def _fit(self):
        # 전체 레인 높이로 키움(8개 초과분은 바깥 QScrollArea 가 스크롤). 예전엔 8개로 잘라 아래 레인이 안 보였음.
        self.setFixedHeight(self.PAD * 2 + max(1, len(self.spans)) * self.LANE_H)

    def set_data(self, spans, n, cur, sel):
        self.spans, self.n, self.cur, self.sel = spans or [], n, cur, sel
        self._preview = None
        self._fit()
        self.update()

    def set_cur(self, i):
        self.cur = i
        self.update()

    def _x(self, i):
        return i / max(1, self.n) * self.width()

    def _frame(self, x):
        return int(max(0, min(self.n - 1, x / max(1, self.width()) * self.n)))

    def _span_of(self, d):
        ints = d["intervals"]
        return ints[0][0], ints[-1][1]

    def _lane_rect(self, idx):
        y = self.PAD + idx * self.LANE_H
        return y, y + self.LANE_H - 3

    def _hit(self, pos):
        """→ (lane_idx, ("seg_in"|"seg_out", 구간번호) 또는 'mid')."""
        idx = int((pos.y() - self.PAD) // self.LANE_H)
        if not (0 <= idx < len(self.spans)):
            return None, None
        d = self.spans[idx]
        for si, (a, b) in enumerate(d["intervals"]):     # 구간마다 양끝 손잡이
            if abs(pos.x() - self._x(a)) <= 7:
                return idx, ("seg_in", si)
            if abs(pos.x() - self._x(b + 1)) <= 7:
                return idx, ("seg_out", si)
        return idx, "mid"

    def mousePressEvent(self, e):
        idx, where = self._hit(e.position())
        if idx is None:
            return
        d = self.spans[idx]
        if isinstance(where, tuple):                     # 구간 끝 끌기
            self._drag = (idx, where)
        else:
            self.sel = d["key"]
            self.select.emit(d["key"], d.get("mode"))
            self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        idx, (edge, si) = self._drag
        d = self.spans[idx]
        segs = [list(iv) for iv in d["intervals"]]
        n = self.n
        f = self._frame(e.position().x())
        a, b = segs[si]
        if edge == "seg_in":
            lo = (segs[si - 1][1] + 1) if si > 0 else 0   # 앞 구간은 넘지 않게(넘으면 합쳐짐)
            segs[si][0] = max(lo, min(f, b))
        else:
            hi = (segs[si + 1][0] - 1) if si + 1 < len(segs) else (n - 1)
            segs[si][1] = min(hi, max(f, a))
        self._preview = (d["key"], [tuple(x) for x in segs])
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None and self._preview is not None:
            key, segs = self._preview
            self.segments_changed.emit(key, segs)
        self._drag = None
        self._preview = None

    def contextMenuEvent(self, e):
        from PySide6.QtWidgets import QMenu
        idx, _ = self._hit(e.pos())
        if idx is None:
            return
        d = self.spans[idx]
        if len(d["intervals"]) < 2:
            return
        m = QMenu(self)
        m.addAction("구간 합치기 (틈 메우기)",
                    lambda: self.segments_changed.emit(d["key"], [(d["intervals"][0][0], d["intervals"][-1][1])]))
        m.exec(e.globalPos())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1a1a1a"))
        if not self.spans or not self.n:
            return
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(p.font())
        f.setPointSize(8)
        p.setFont(f)
        for idx, d in enumerate(self.spans):
            y0, y1 = self._lane_rect(idx)
            sel = d["key"] == self.sel
            p.fillRect(QRectF(0, y0 - 1, self.width(), self.LANE_H - 1), QColor("#242424" if idx % 2 else "#202020"))
            pv = self._preview if (self._preview and self._preview[0] == d["key"]) else None
            bar = _lane_color(d, sel)       # 종류별 색: 얼굴=노랑·간판=청록·추가=파랑·제외=초록
            ivs = pv[1] if pv else d["intervals"]       # 드래그 중이면 미리보기 구간
            for (a, b) in d["intervals"]:
                xa, xb = self._x(a), self._x(b + 1)
                p.fillRect(QRectF(xa, y0, max(2, xb - xa), y1 - y0), bar)
            if pv:                                  # 드래그 미리보기(구간 양끝 이동)
                p.setPen(QPen(QColor("#f2c94c"), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                for (a, b) in ivs:
                    xa, xb = self._x(a), self._x(b + 1)
                    p.drawRect(QRectF(xa, y0, max(2, xb - xa), y1 - y0 - 1))
            # 구간마다 양끝 손잡이
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#cfe0f0") if sel else QColor("#8aa6c4"))
            for (a, b) in ivs:
                for fx in (self._x(a), self._x(b + 1)):
                    p.drawRect(QRectF(fx - 2, y0, 4, y1 - y0))
            # 키 ◆
            p.setBrush(QColor("#ffffff"))
            for k in d["keys"]:
                kx = self._x(k + 0.5)
                cy = (y0 + y1) / 2
                p.drawPolygon([QPointF(kx, cy - 4), QPointF(kx + 4, cy), QPointF(kx, cy + 4), QPointF(kx - 4, cy)])
            # 라벨
            p.setPen(QColor("#e6e6e6") if sel else QColor("#9aa0a6"))
            p.drawText(QRectF(4, y0, 40, y1 - y0), Qt.AlignVCenter | Qt.AlignLeft, d["label"])
        x = self._x(self.cur + 0.5)
        p.fillRect(QRectF(x - 1, 0, 2, self.height()), QColor("#e5484d"))


# ------------------------------------------------------------ 타임라인 눈금(초)
class TimeRuler(QWidget):
    """소스 바 아래 시간 눈금(0s·2s·…). 타임바와 같은 x 매핑, 클릭/드래그로 이동."""
    seek = Signal(int)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(18)
        self.n = 0
        self.fps = 30.0
        self.cur = 0

    def set_data(self, n, fps):
        self.n = int(n)
        self.fps = float(fps) or 30.0
        self.update()

    def set_cur(self, i):
        self.cur = i
        self.update()

    def _x(self, i):
        return i / max(1, self.n) * self.width()

    def mousePressEvent(self, e):
        if self.n:
            self.seek.emit(int(max(0, min(self.n - 1, e.position().x() / max(1, self.width()) * self.n))))

    def mouseMoveEvent(self, e):
        if self.n and (e.buttons() & Qt.LeftButton):
            self.seek.emit(int(max(0, min(self.n - 1, e.position().x() / max(1, self.width()) * self.n))))

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#141414"))
        if not self.n:
            return
        total = self.n / self.fps                    # 전체 초
        # 라벨이 ~10개쯤 되도록 보기 좋은 간격(초) 선택
        for step in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if total / step <= 12:
                break
        f = QFont(p.font())
        f.setPointSize(8)
        p.setFont(f)
        t = 0.0
        while t <= total + 1e-6:
            x = self._x(int(round(t * self.fps)))
            p.setPen(QColor("#4a4a4a"))
            p.drawLine(int(x), 2, int(x), 7)
            p.setPen(QColor("#8a8a8a"))
            lbl = f"{int(t)}s" if t < 60 else f"{int(t) // 60}:{int(t) % 60:02d}"
            p.drawText(QRectF(x + 2, 2, 60, 14), Qt.AlignVCenter | Qt.AlignLeft, lbl)
            # 보조 눈금(절반)
            xh = self._x(int(round((t + step / 2) * self.fps)))
            p.setPen(QColor("#333"))
            p.drawLine(int(xh), 4, int(xh), 7)
            t += step
        x = self._x(self.cur)
        p.fillRect(QRectF(x - 1, 0, 2, self.height()), QColor("#e5484d"))
