"""타원 수정(키프레임 보정량·추가 타원·끄기·합치기·다시 잇기) 점검 — 가짜 트랙으로. CI 에서도 실행."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import numpy as np  # noqa: E402

from mosco import analyze, edits  # noqa: E402
from mosco.project import Source  # noqa: E402


def fake_det(n=120):
    """사람 2명: A 는 0~119 오른쪽으로 이동, B 는 0~39 그리고 70~119(30프레임 사라짐, 같은 옷색)."""
    rows = []
    hist_a = np.zeros(16, np.float32); hist_a[3] = 1
    hist_b = np.zeros(16, np.float32); hist_b[9] = 1
    for f in range(n):
        x = 200 + f * 3
        rows.append(np.r_[np.float32(f), [x - 20, 300, x + 20, 340, 0.9, 0], hist_a])
        if f < 40 or f >= 70:
            bx = 900 + f * 2
            rows.append(np.r_[np.float32(f), [bx - 15, 500, bx + 15, 530, 0.9, 0], hist_b])
    return {"det": np.array(rows, np.float32), "n": np.array(n)}


def main():
    z = fake_det()
    s = Source("x.mov", "blur")
    tr = analyze.blur_track(z, s.blur)
    assert tr.ids == 2, f"다시 잇기 실패: ids={tr.ids}"
    assert len(tr.at(55)) == 2, "사라진 구간이 메워지지 않음"
    s.blur["link"] = 0
    tr0 = analyze.blur_track(z, s.blur)
    assert tr0.ids == 3, f"잇기 끔인데 ids={tr0.ids}"
    print("link OK")
    s.blur["link"] = 90
    r = edits.Resolver(s, tr)
    items = r.items(20)
    a = min(items, key=lambda it: it.ell[0])
    # 보정량 키: 20프레임에서 A 를 위로 50px
    g = edits.ensure_group(s, a, 20)
    edits.set_key(g, 20, (a.ell[0], a.ell[1] - 50, a.ell[2], a.ell[3]))
    r = edits.Resolver(s, tr)
    a20 = next(it for it in r.items(20) if it.gid == g["gid"])
    assert abs(a20.ell[1] - (a.ell[1] - 50)) < 0.5 and a20.keyed
    a26 = next(it for it in r.items(26) if it.gid == g["gid"])
    base26 = next(e for t, e, _, _ in r.auto_at(26) if t == a20.tid)
    assert -50 < a26.ell[1] - base26[1] < -10, "키 밖에서 보정량이 줄어들며 남아야 함"
    a60 = next(it for it in r.items(60) if it.gid == g["gid"])
    base60 = next(e for t, e, _, _ in r.auto_at(60) if t == a20.tid)
    assert abs(a60.ell[1] - base60[1]) < 1e-3, "멀리서는 자동 그대로"
    # 자동이 계속 따라가는지(x 이동)
    assert a60.ell[0] > a20.ell[0] + 100
    print("offset key OK")
    # 끄기
    edits.off_range(g, 80, 119)
    r = edits.Resolver(s, tr)
    assert all(it.gid != g["gid"] for it in r.items(90)) and any(it.gid == g["gid"] for it in r.items(70))
    print("off OK")
    # 추가 타원: 30 에서 시작, 50 에 키, 이후 유지
    m = edits.add_manual(s, 30, (100, 100, 60, 75))
    edits.set_key(m, 50, (300, 100, 60, 75))
    r = edits.Resolver(s, tr)
    assert not any(it.gid == m["gid"] for it in r.items(29))
    m40 = next(it for it in r.items(40) if it.gid == m["gid"])
    assert abs(m40.ell[0] - 200) < 1e-3
    m100 = next(it for it in r.items(100) if it.gid == m["gid"])
    assert abs(m100.ell[0] - 300) < 1e-3
    print("manual OK")
    # 합치기: 잇기를 끈 트랙에서 B 앞·뒤 조각을 손으로 합침
    s2 = Source("x.mov", "blur")
    s2.blur["link"] = 0
    tr2 = analyze.blur_track(z, s2.blur)
    r2 = edits.Resolver(s2, tr2)
    b_first = max(r2.items(10), key=lambda it: it.ell[0])
    b_second = max(r2.items(100), key=lambda it: it.ell[0])
    assert b_first.tid != b_second.tid
    edits.add_merge(s2, (10, b_first.ell[0], b_first.ell[1]), (100, b_second.ell[0], b_second.ell[1]))
    r2 = edits.Resolver(s2, tr2)
    ids = {it.tid for it in r2.items(10)} | {it.tid for it in r2.items(100)}
    assert len(ids) == 2, ids
    assert len(r2.items(55)) == 2, "합친 사이 구간 보간 없음"
    print("merge OK")
    # 뷰티 가짜 트랙: 랜드마크 대신 타원만 확인(추가 타원 + 타원별 값)
    sb = Source("x.mov", "beauty")
    mb = edits.add_manual(sb, 0, (500, 400, 300, 380))
    mb["ov"] = {"size": 120, "feather": 30, "strength": 80}
    rb = edits.Resolver(sb, None)
    it = rb.items(10)[0]
    from mosco import fx
    assert abs(fx._item_ell(it, sb.beauty)[2] - 360) < 1e-3
    frame = np.full((1080, 1920, 3), 40000, np.uint16)
    frame[..., 1] = 30000
    frame[..., 2] = 26000
    res = fx.beauty(frame, rb.items(10), sb.beauty, 10, want_keys=True)
    assert res.alpha.max() > 0.9
    print("beauty manual OK")
    print("EDITS OK")


if __name__ == "__main__":
    main()
