"""v0.7.55 트래킹 단위 테스트(모델·영상 없이): 카메라 움직임 추정·보정, 옷 색 + 최적 배정, 의심 트랙, 뷰티 슬롯 규칙."""
import itertools
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from mosco import analyze as A  # noqa: E402
from mosco import sign  # noqa: E402
from mosco.project import BLUR_DEFAULT, SIGN_DEFAULT  # noqa: E402


def cam_est():
    rng = np.random.default_rng(0)
    base = cv2.GaussianBlur((rng.random((1300, 2300)) * 255).astype(np.uint8), (0, 0), 2)
    base = np.dstack([base] * 3)
    est = A.CamEst()
    assert est.step(base[100:1180, 100:2020]) == (1.0, 0.0, 0.0)
    worst = 0.0
    for dx, dy in ((13, -7), (-22, 4), (0, 0), (5, 11)):
        est = A.CamEst()
        est.step(base[100:1180, 100:2020])
        s, tx, ty = est.step(base[100 + dy:1180 + dy, 100 + dx:2020 + dx])     # 카메라가 (dx,dy) 이동 → 화면은 반대로
        worst = max(worst, abs(tx + dx), abs(ty + dy), abs(s - 1) * 1000)
    assert worst < 1.5, f"카메라 이동 추정 오차 {worst}"
    # 장면 전환(전혀 다른 그림) → 움직임 없음으로 처리
    est = A.CamEst()
    est.step(base[100:1180, 100:2020])
    other = np.dstack([cv2.GaussianBlur((rng.random((1080, 1920)) * 255).astype(np.uint8), (0, 0), 2)] * 3)
    assert est.step(other) == (1.0, 0.0, 0.0), "장면 전환을 움직임으로 착각"
    print(f"cam est OK  오차 {worst:.2f}px")


def _row(f, cx, cy, w, sc=0.9, kind=A.KIND_FACE, hist=None):
    h = np.zeros(A.HIST, np.float32) if hist is None else hist
    return np.r_[[f, cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2, sc, kind], h].astype(np.float32)


def cam_follow():
    """세상에 가만히 있는 얼굴 + 카메라 팬(중간에 속도 바뀜). 검출은 일부 프레임만 → 놓친 구간·뒤 여유가 팬을 따라가야 함."""
    n = 90
    speed = np.r_[0, np.full(29, 6.0), np.full(30, 18.0), np.full(30, 3.0)]      # 프레임별 화면 이동(px)
    off = np.cumsum(speed)
    cam = np.c_[np.ones(n), -speed, np.zeros(n)].astype(np.float32)             # 화면은 왼쪽으로 흐름
    truth = 1500 - off
    seen = [f for f in range(n) if f <= 20 or 40 <= f <= 60]
    det = np.array([_row(f, truth[f], 500, 80) for f in seen], np.float32)
    p = dict(BLUR_DEFAULT, hold=24, stabilize=50)
    res = {}
    for name, z in (("cam", {"det": det, "n": np.array(n), "cam": cam}), ("old", {"det": det, "n": np.array(n)})):
        tr = A.blur_track(z, p)
        assert tr.ids == 1, (name, tr.ids)
        err = {}
        for f in (30, 70, 84):
            fr = tr.at(f)
            assert fr, (name, f)
            err[f] = abs(fr[0][1] - truth[f])
        res[name] = err
    assert max(res["cam"].values()) < 4, res
    assert res["old"][84] > 60, res            # 예전 방식은 뒤 여유 구간에서 제자리 → 크게 벗어남
    print(f"cam follow OK  오차 새 {max(res['cam'].values()):.1f}px / 예전 {max(res['old'].values()):.0f}px")
    # 간판도 같은 방식(4꼭짓점)
    rows = []
    for f in seen:
        x0, y0, x1, y1 = truth[f] - 150, 300, truth[f] + 150, 380
        q = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
        rows.append(np.r_[[f, x0, y0, x1, y1, 0.95, sign.KIND_TEXT], np.zeros(A.HIST), q.reshape(-1), [60]])
    zs = {"det": np.array(rows, np.float32), "n": np.array(n), "cam": cam}
    ts = sign.sign_track(zs, dict(SIGN_DEFAULT, hold=24))
    assert ts.ids == 1
    e = max(abs(ts.at(f)[0][1].mean(0)[0] - truth[f]) for f in (30, 70, 84))
    assert e < 4, e
    print(f"cam follow(sign) OK  오차 {e:.1f}px")


def match():
    for _ in range(200):                       # 최적 배정 = 전수 조사와 같은 비용
        rng = np.random.default_rng(_)
        r, c = int(rng.integers(1, 5)), int(rng.integers(1, 6))
        if r > c:
            r, c = c, r
        C = rng.random((r, c))
        sol = A._hungarian(C)
        best = min(sum(C[i, p[i]] for i in range(r)) for p in itertools.permutations(range(c), r))
        assert abs(sum(C[i, sol[i]] for i in range(r)) - best) < 1e-9
    # 두 사람이 다가왔다가 가려진 뒤 되돌아감: 위치·속도만 보면 뒤바뀌고, 옷 색을 보면 맞음
    red = np.zeros(A.HIST, np.float32); red[0] = 1
    blue = np.zeros(A.HIST, np.float32); blue[9] = 1
    rows = []
    for f in range(0, 26):
        rows += [_row(f, 200 + f * 10, 400, 100, hist=red), _row(f, 800 - f * 10, 400, 100, hist=blue)]
    for f in range(32, 60):                    # 26~31 가려짐. 되돌아감(A 는 다시 왼쪽으로)
        rows += [_row(f, 470 - (f - 32) * 10, 400, 100, hist=red), _row(f, 530 + (f - 32) * 10, 400, 100, hist=blue)]
    tr = A.blur_track({"det": np.array(rows, np.float32), "n": np.array(60)}, dict(BLUR_DEFAULT, stabilize=0))
    left0 = min(tr.at(5), key=lambda e: e[1])[0]
    left1 = min(tr.at(55), key=lambda e: e[1])[0]
    assert tr.ids == 2 and left0 == left1, f"엇갈림에서 ID 뒤바뀜 ids={tr.ids} {left0}->{left1}"
    print("match OK  최적 배정 · 옷 색으로 ID 유지")


def suspicious():
    rows = [_row(f, 400, 400, 80, 0.9) for f in range(40)] + [_row(f, 1200, 300, 40, 0.45) for f in range(10, 15)]
    tr = A.blur_track({"det": np.array(rows, np.float32), "n": np.array(40)}, dict(BLUR_DEFAULT, sensitivity=90))
    flags = sorted((round(float(t["box"][0][0])), t["sus"]) for t in tr.tracks.values())
    assert flags == [(400, False), (1200, True)], flags
    print("suspicious OK")


def slots():
    K = 4
    lb = [None] * K
    ls = [-10 ** 9] * K
    prev = [None] * K
    box = (100, 100, 200, 220)
    assert A._pick_slot([0, 1, 2, 3], box, lb, ls, 0, prev) == 0
    lb[0], ls[0] = box, 50                     # 0번을 쓰던 사람이 나감
    far = (900, 100, 1000, 220)
    assert A._pick_slot([0, 1, 2, 3], far, lb, ls, 60, prev) == 1, "다른 사람이 나간 사람 번호를 받음"
    assert A._pick_slot([0, 1, 2, 3], (110, 105, 210, 225), lb, ls, 60, prev) == 0, "같은 사람이 다른 번호를 받음"
    # 뷰티 빈 구간: 양끝이 다른 사람이면 잇지 않음
    lm = np.full((20, 468, 2), np.nan, np.float32)
    g = np.stack(np.meshgrid(np.linspace(0, 100, 26), np.linspace(0, 120, 18)), -1).reshape(-1, 2).astype(np.float32)
    lm[:5] = g + (100, 100)
    lm[10:] = g + (900, 100)
    _, pres = A._track_one(lm, 50, 15)
    assert pres[5:10].sum() == 0, "다른 사람 사이를 보간함"
    lm[10:] = g + (120, 100)
    _, pres = A._track_one(lm, 50, 15)
    assert pres[5:10].sum() == 5
    print("slots OK")


if __name__ == "__main__":
    cam_est()
    cam_follow()
    match()
    suspicious()
    slots()
    print("TRACK OK")
