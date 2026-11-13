"""v0.7.89 얼굴·간판 번호가 겹쳐도(얼굴 7·간판 7) 한쪽만 선택 — 모니터·레인."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from mosco.ui_monitor import Monitor, EllipseLanes

app = QApplication.instance() or QApplication([])
K = 7                                                   # 같은 번호(키)
face = {"key": K, "mode": "blur", "ell": (100, 100, 40, 50, 0), "quad": None}
sign = {"key": K, "mode": "sign", "ell": (300, 300, 60, 30, 0), "quad": [(270, 285), (330, 285), (330, 315), (270, 315)]}
m = Monitor()
m.ells = [face, sign]
m.set_selected(K, "sign")
assert m._is_sel(sign) and not m._is_sel(face), "모니터: 간판 7 선택 시 얼굴 7 도 선택됨"
m.set_selected(K, "blur")
assert m._is_sel(face) and not m._is_sel(sign), "모니터: 얼굴 7 선택 시 간판 7 도 선택됨"
m.set_selected(K, None)                                 # 단일 종류(뷰티 등) 호환
assert m._is_sel(face) and m._is_sel(sign)

def span(mode, quad):
    return {"key": K, "mode": mode, "label": "7", "intervals": [(0, 9)], "keys": [0, 9], "ukeys": [], "n": 10,
            "minus": False, "quad": quad, "manual": False, "sus": False, "partial": False, "note": ""}
L = EllipseLanes()
L.set_data([span("blur", False), span("sign", True)], 10, 0, K, "sign")
assert [L._is_sel(d) for d in L.spans] == [False, True], "레인: 간판 7 선택 시 얼굴 7 레인도 선택됨"
L._pick(0, Qt.NoModifier)                                         # 얼굴 레인 클릭
assert [L._is_sel(d) for d in L.spans] == [True, False], "레인: 얼굴 7 클릭 시 간판 7 레인도 선택됨"
L.grab()                                                # paint 경로 오류 없는지
m.grab()
print("selmode OK")

# v0.7.89 얼굴 | 간판 사이 빈 줄 + 점선: 빈 줄 클릭 = 레인 없음, 아래 레인 위치는 한 줄씩 밀림
spans = [span("blur", False), dict(span("blur", False), key=8, label="8"), span("sign", True)]
L.set_data(spans, 10, 0, None)
assert L._gap() == 2 and L.rows() == 4
H, P = L.LANE_H, L.PAD
assert L._idx_at(P + 1 * H + 5) == 1 and L._idx_at(P + 2 * H + 5) == -1 and L._idx_at(P + 3 * H + 5) == 2
assert L._lane_rect(2)[0] == P + 3 * H
L.set_data(spans[:2], 10, 0, None)
assert L._gap() is None and L.rows() == 2           # 한 종류뿐이면 빈 줄 없음
L.set_data([span("sign", True)], 10, 0, None)
assert L._gap() is None
L.set_data(spans, 10, 0, None)
L.resize(600, L.height())
L.grab().save("/tmp/lanes_gap.png")
print("lane gap OK")

from mosco.ui_monitor import _lane_color, LAYER_COLORS
sus_full = dict(span("blur", False), sus=True)
assert _lane_color(sus_full, False).name() == LAYER_COLORS["face"][0].lower(), "레인에 주황이 남음"
assert _lane_color(dict(sus_full, partial=True), False).name() == LAYER_COLORS["gray"][0].lower()
print("lane no orange OK")
