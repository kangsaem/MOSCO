"""v0.8.0 모니터·레인 조작 시험(오프스크린, 영상 없이):
화면 이동 = 휠 클릭·스페이스+드래그만 · 빈 곳 드래그 = 한 레이어 꼭짓점 고무줄 선택 → 함께 이동 ·
레인 Shift = 종류 상관없이 사이 전부 · 키 우클릭 메뉴(이후 키 삭제) 항목 · 패널 B안 제목."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosco.ui_monitor import EllipseLanes, Monitor  # noqa: E402


def img_to_widget(m, x, y):
    pane = m.panes()[0]
    s, c = m.xform(pane)
    return QPoint(int(pane.center().x() + (x - c.x()) * s), int(pane.center().y() + (y - c.y()) * s))


def main():
    app = QApplication.instance() or QApplication([])
    m = Monitor()
    m.resize(960, 540)
    m.show()
    m.set_images([np.zeros((1080, 1920, 3), np.uint8)], (1920, 1080))
    quad = [(400.0, 300.0), (800.0, 320.0), (800.0, 500.0), (400.0, 480.0)]
    other = [(1200.0, 300.0), (1500.0, 300.0), (1500.0, 450.0), (1200.0, 450.0)]
    ell = (600.0, 400.0, 400.0, 200.0, 0.0)
    m.set_overlay([{"key": ("g", 1), "label": "1", "ell": ell, "quad": quad, "mode": "sign"},
                   {"key": ("g", 2), "label": "2", "ell": (1350.0, 375.0, 300.0, 150.0, 0.0), "quad": other,
                    "mode": "sign"}], True, True)
    app.processEvents()
    sel, edited = [], []
    m.selected.connect(lambda k, md: sel.append(k))
    m.edited.connect(lambda k, v, md: edited.append((k, v)))

    # 1) 빈 곳 왼쪽 드래그 = 화면 이동 안 함(고무줄)
    c0 = (m.mode, m.c.x(), m.c.y())
    QTest.mousePress(m, Qt.LeftButton, Qt.NoModifier, img_to_widget(m, 100, 900))
    QTest.mouseMove(m, img_to_widget(m, 300, 1000))
    QTest.mouseRelease(m, Qt.LeftButton, Qt.NoModifier, img_to_widget(m, 300, 1000))
    assert (m.mode, m.c.x(), m.c.y()) == c0 and m.mode != "manual", "빈 곳 왼쪽 드래그로 화면이 움직임"
    # 2) 휠(가운데) 클릭 드래그 = 화면 이동
    p0 = img_to_widget(m, 960, 540)
    QTest.mousePress(m, Qt.MiddleButton, Qt.NoModifier, p0)
    QTest.mouseMove(m, p0 + QPoint(80, 40))
    QTest.mouseRelease(m, Qt.MiddleButton, Qt.NoModifier, p0 + QPoint(80, 40))
    assert m.mode == "manual", "휠 클릭 드래그로 화면이 안 움직임"
    m.reset_view()
    app.processEvents()
    # 3) 스페이스 + 왼쪽 드래그 = 화면 이동, space_used 표시(떼도 재생 안 함)
    m.set_space(True)
    p0 = img_to_widget(m, 960, 540)
    QTest.mousePress(m, Qt.LeftButton, Qt.NoModifier, p0)
    QTest.mouseMove(m, p0 + QPoint(60, 0))
    QTest.mouseRelease(m, Qt.LeftButton, Qt.NoModifier, p0 + QPoint(60, 0))
    assert m.mode == "manual" and m.space_used, "스페이스 + 드래그 이동 안 됨"
    m.set_space(False)
    m.reset_view()
    app.processEvents()
    print("pan OK")

    # 4) 고무줄: 레이어 1 의 오른쪽 두 꼭짓점(1, 2)만 감싸기 → 그 레이어 선택 + 꼭짓점 2개
    QTest.mousePress(m, Qt.LeftButton, Qt.NoModifier, img_to_widget(m, 760, 280))
    QTest.mouseMove(m, img_to_widget(m, 850, 400))
    QTest.mouseMove(m, img_to_widget(m, 850, 540))
    QTest.mouseRelease(m, Qt.LeftButton, Qt.NoModifier, img_to_widget(m, 850, 540))
    assert m.vsel == (("g", 1), "sign", {1, 2}), m.vsel
    assert sel and sel[-1] == ("g", 1), sel
    # 다른 레이어 꼭짓점까지 감싸도 한 레이어만(가장 많이 든 쪽)
    m.set_selected(None, None)
    QTest.mousePress(m, Qt.LeftButton, Qt.NoModifier, img_to_widget(m, 380, 280))
    QTest.mouseMove(m, img_to_widget(m, 1250, 520))
    QTest.mouseRelease(m, Qt.LeftButton, Qt.NoModifier, img_to_widget(m, 1250, 520))
    assert m.vsel[0] == ("g", 1) and m.vsel[2] == {0, 1, 2, 3}, m.vsel
    m.vsel = (("g", 1), "sign", {1, 2})
    m.set_selected(("g", 1), "sign")
    m.vsel = (("g", 1), "sign", {1, 2})
    # 5) 고른 꼭짓점 하나를 끌면 둘 다 같이 이동, 나머지는 그대로
    a = img_to_widget(m, 800, 320)
    QTest.mousePress(m, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(m, a + QPoint(20, 10))
    QTest.mouseMove(m, a + QPoint(40, 20))
    QTest.mouseRelease(m, Qt.LeftButton, Qt.NoModifier, a + QPoint(40, 20))
    assert edited, "꼭짓점 이동이 저장 안 됨"
    v = np.asarray(edited[-1][1], np.float32).reshape(4, 2)
    q = np.asarray(quad, np.float32)
    d = v - q
    assert np.abs(d[0]).max() < 0.01 and np.abs(d[3]).max() < 0.01, d
    assert d[1][0] > 30 and np.allclose(d[1], d[2], atol=0.01), d
    print("vertex band select OK")

    # 6) 레인 Shift = 종류 상관없이 사이 전부
    ln = EllipseLanes()
    ln.resize(600, 200)
    spans = [{"key": ("g", 1), "mode": "blur", "label": "1", "intervals": [(0, 99)], "keys": [0, 50], "ukeys": [],
              "n": 100},
             {"key": ("g", 2), "mode": "blur", "label": "2", "intervals": [(0, 99)], "keys": [0], "ukeys": [], "n": 100},
             {"key": ("g", 1), "mode": "sign", "label": "1", "intervals": [(0, 99)], "keys": [0], "ukeys": [], "n": 100},
             {"key": ("g", 3), "mode": "sign", "label": "3", "intervals": [(0, 99)], "keys": [0], "ukeys": [], "n": 100}]
    ln.set_data(spans, 100, 0, ("g", 1), "blur")
    ln._pick(3, Qt.ShiftModifier)
    assert ln.multi == {("blur", ("g", 2)), ("sign", ("g", 1)), ("sign", ("g", 3))}, ln.multi
    pairs = ln.sel_pairs()
    assert pairs[0] == ("blur", ("g", 1)) and len(pairs) == 4, pairs
    # 키 우클릭 판정(레인 0 의 50 프레임 키)
    y = ln._lane_rect(0)
    hit = ln._key_hit(QPointF(ln._x(50.5), (y[0] + y[1]) / 2))
    assert hit is not None and hit[2] == 50 and hit[3] == "auto", hit
    print("lanes shift/menu OK")
    print("CANVAS OK")
    os._exit(0)


if __name__ == "__main__":
    main()
