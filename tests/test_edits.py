"""타원 수정(키프레임 보정량·추가 타원·끄기·합치기·다시 잇기) 점검 — 가짜 트랙으로. CI 에서도 실행."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import cv2  # noqa: E402
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


def minus_check():
    """v0.7.7 제외 타원: 그 안은 블러 안 함(겹친 타원이 있어도) · 4444 알파 0 · 저장·불러오기 유지."""
    import json
    from mosco import fx
    from mosco.project import Source as Src
    z = fake_det()
    s = Src("x.mov", "blur")
    tr = analyze.blur_track(z, s.blur)
    r = edits.Resolver(s, tr)
    a = min(r.items(20), key=lambda it: it.ell[0])
    g = edits.ensure_group(s, a, 20)
    g["minus"] = True
    cx, cy = a.ell[0], a.ell[1]
    edits.add_manual(s, 0, (cx + 25, cy, 120, 120))          # 겹치는 블러 타원
    from dataclasses import asdict
    s2 = Src.from_dict(json.loads(json.dumps(asdict(s))))       # 저장·불러오기(Project.save 와 같은 방식)
    items = edits.Resolver(s2, tr).items(20)
    assert sum(it.minus for it in items) == 1 and any(not it.minus for it in items), "제외 표시가 유지되지 않음"
    rng = np.random.default_rng(0)
    f = (rng.random((720, 1280, 3)) * 65535).astype(np.uint16)
    out, alpha, ells = fx.blur(f, items, s2.blur, True)
    m = next(it for it in items if it.minus)
    mx, my = int(m.ell[0]), int(m.ell[1])
    assert np.array_equal(out[my - 3:my + 3, mx - 3:mx + 3], f[my - 3:my + 3, mx - 3:mx + 3]), "제외 영역이 블러됨"
    assert alpha[my - 3:my + 3, mx - 3:mx + 3].max() < 1e-3, "제외 영역 알파가 0 이 아님"
    ox = int(cx + 25 + 45)                                      # 겹친 타원의 제외 바깥 쪽
    assert alpha[int(cy), ox] > 0.5, "제외 밖 블러가 사라짐"
    assert len(ells) == len(items)
    print("minus OK")


def sign_check():
    """v0.7.8 간판: 가짜 검출(오른쪽으로 이동하는 사다리꼴 1개) → 4꼭짓점 트랙 · 꼭짓점 키(8값) · 사각형 추가 · 저장."""
    import json
    from dataclasses import asdict
    from mosco import sign
    from mosco.project import Source as Src
    n = 60
    rows = []
    hist = np.zeros(16, np.float32); hist[5] = 1
    for f in range(n):
        q = np.float32([[100, 100], [400, 120], [400, 200], [100, 190]]) + (f * 2, 0)
        x0, y0 = q.min(0); x1, y1 = q.max(0)
        rows.append(np.r_[np.float32(f), [x0, y0, x1, y1, 0.9, sign.KIND_TEXT], hist, q.reshape(-1), [40.0]])
    z = {"det": np.array(rows, np.float32), "n": np.array(n)}
    s = Src("x.mov", "sign")
    s.sign["margin"] = 0
    tr = sign.sign_track(z, s.sign)
    assert tr.ids == 1, tr.ids
    r = edits.Resolver(s, tr)
    it = r.items(30)[0]
    q30 = np.asarray(it.pts)
    assert np.abs(q30[0] - (160, 100)).max() < 2 and np.abs(q30[2] - (460, 200)).max() < 2, q30
    # 여유: 변을 바깥으로 평행 이동(글자 높이 40 × 50% = 20px) → 왼쪽 위 꼭짓점이 바깥(왼쪽 위)으로
    s.sign["margin"] = 50
    q30m = np.asarray(edits.Resolver(s, sign.sign_track(z, s.sign)).items(30)[0].pts)
    assert q30m[0][0] < q30[0][0] - 15 and q30m[0][1] < q30[0][1] - 15, q30m
    s.sign["margin"] = 0
    # 꼭짓점 키: 30프레임에서 오른쪽 아래 꼭짓점만 +30 → 그 프레임 반영, 키 밖은 서서히 원래대로
    g = edits.ensure_group(s, it, 30)
    v = q30.copy(); v[2] += (30, 30)
    edits.set_key(g, 30, tuple(v.reshape(-1)))
    assert len(g["keys"]["30"]) == 8
    r = edits.Resolver(s, tr)
    k30 = np.asarray(next(i for i in r.items(30) if i.gid == g["gid"]).pts)
    assert np.abs(k30[2] - v[2]).max() < 0.5 and np.abs(k30[0] - q30[0]).max() < 0.5
    k50 = np.asarray(next(i for i in r.items(50) if i.gid == g["gid"]).pts)
    assert np.abs(k50[2] - (q30[2] + (40, 0))).max() < 1, "키 밖은 자동 그대로(이동은 따라감)"
    # 사각형 추가 → 직사각형 8값, 저장·불러오기 유지
    gm = edits.add_manual(s, 10, (900, 500, 200, 80))
    assert len(gm["keys"]["10"]) == 8
    s2 = Src.from_dict(json.loads(json.dumps(asdict(s))))
    assert s2.mode == "sign" and s2.sign["max_count"] == s.sign["max_count"]
    its = edits.Resolver(s2, tr).items(20)
    man = next(i for i in its if i.manual)
    assert np.abs(np.asarray(man.pts)[0] - (800, 460)).max() < 0.5
    # 최대 개수
    s.sign["max_count"] = 1
    assert all(len(tr2) <= 1 for tr2 in sign.sign_track(z, s.sign).frames)
    print("sign edits OK")


def flat_check():
    """v0.7.73 오토 키 삭제 = 앞뒤 키 사이 직선 · 재생선이 구간 밖이어도 레이어 삭제(상태 계층까지)."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    z = fake_det()
    d = z["det"]
    for f in range(50, 61):                                    # A 가 50~60 에서 위로 튐(삼각형, 최대 60px)
        m = (d[:, 0] == f) & (d[:, 1] < 700)
        d[m, 2] -= 60 * (1 - abs(f - 55) / 5); d[m, 4] -= 60 * (1 - abs(f - 55) / 5)
    s = Source("x.mov", "blur")
    s.blur["stabilize"] = 0
    tr = analyze.blur_track(z, s.blur)
    r = edits.Resolver(s, tr)
    a = min(r.items(55), key=lambda it: it.ell[0])
    y45 = next(it for it in r.items(45) if it.tid == a.tid).ell[1]
    assert a.ell[1] < y45 - 30, "튀는 가짜 자료가 안 만들어짐"
    g = edits.ensure_group(s, a, 55)
    edits.add_flat(g, 48, 62)
    edits.add_flat(g, 62, 70)                                  # 끝점만 닿음 → 따로
    assert g["flat"] == [[48, 62], [62, 70]]
    edits.add_flat(g, 60, 66)                                  # 겹침 → 하나로
    assert g["flat"] == [[48, 70]], g["flat"]
    r = edits.Resolver(s, tr)
    b = next(it for it in r.items(55) if it.gid == g["gid"])
    assert abs(b.ell[1] - y45) < 3, f"펴기 안 됨: {b.ell[1]} vs {y45}"
    x54, x56 = (next(it for it in r.items(f) if it.gid == g["gid"]).ell[0] for f in (54, 56))
    assert x54 < b.ell[0] < x56, "펴도 가로 이동은 따라가야 함"
    edits.set_key(g, 55, (b.ell[0], b.ell[1] + 20, b.ell[2], b.ell[3]))      # 편 구간 위에 수동 키
    r = edits.Resolver(s, tr)
    c = next(it for it in r.items(55) if it.gid == g["gid"])
    assert abs(c.ell[1] - (b.ell[1] + 20)) < 0.5, "편 구간의 수동 키 값이 어긋남"
    # 상태 계층: 레인 키 목록에서 가운데 키 삭제 · 구간 밖에서 레이어 삭제
    from PySide6.QtWidgets import QApplication
    from mosco.state import AppState
    app = QApplication.instance() or QApplication([])
    st = AppState()
    s2 = Source("y.mov", "blur")
    s2.blur["stabilize"] = 0
    st.project.sources.append(s2)
    st.cur = 0
    st.track = lambda src, mode=None: tr
    st.blur_resolvers = lambda src: [(st.resolver(src), "blur")]
    lane = next(x for x in st.ellipse_spans(s2) if x["label"] == str(a.tid))
    mid = [k for k in lane["keys"] if 50 <= k <= 60]
    assert mid, f"튀는 곳에 오토 키가 없음: {lane['keys']}"
    # v0.7.75 구간 처음·끝 키 삭제 = 옆 키 값으로 고정(구간 길이 그대로)
    k0, k1, kz, ky = lane["keys"][0], lane["keys"][1], lane["keys"][-1], lane["keys"][-2]
    ref1 = next(it for it in st.resolver(s2).items(k1) if it.key == lane["key"]).ell[:4]
    refy = next(it for it in st.resolver(s2).items(ky) if it.key == lane["key"]).ell[:4]
    assert st.delete_auto_key(lane["key"], k0) == "" and st.delete_auto_key(st.sel, kz) == ""
    s2 = st.source()
    lane2 = next(x for x in st.ellipse_spans(s2) if x["key"] == st.sel)
    assert lane2["intervals"] == lane["intervals"], "끝 키를 지워도 구간 길이는 그대로여야 함"
    e0 = next(it for it in st.resolver(s2).items(k0) if it.key == st.sel).ell[:4]
    ez = next(it for it in st.resolver(s2).items(kz) if it.key == st.sel).ell[:4]
    assert np.allclose(e0, ref1, atol=0.5) and np.allclose(ez, refy, atol=0.5), "끝 키가 옆 키 값으로 고정되지 않음"
    assert st.undo() and st.undo()
    s2 = st.source()
    lane = next(x for x in st.ellipse_spans(s2) if x["label"] == str(a.tid))
    n0 = len(lane["keys"])
    for _ in range(8):                                         # 튀는 구간의 키를 다 지울 때까지
        lane = next(x for x in st.ellipse_spans(s2) if x["key"] == st.sel or x["label"] == str(a.tid))
        mid = [k for k in lane["keys"] if 50 <= k <= 60]
        if not mid:
            break
        assert st.delete_auto_key(lane["key"], mid[0]) == ""
    assert not mid and len(lane["keys"]) < n0, "오토 키가 안 지워짐"
    y = next(it for it in st.resolver(s2).items(55) if it.key == st.sel).ell[1]
    assert abs(y - y45) < 6, f"키를 지웠는데 안 펴짐 {y} {y45}"
    # v0.7.83 고정(bake): 가운데 키를 지워도 '다른 키'는 위치·값이 그대로여야 함
    for _ in range(14):
        assert st.undo()                                      # 깨끗한 상태로
        if not any(x.get("akeys") for x in st.source().edits.get("blur", {}).get("groups", [])):
            break
    s2 = st.source()
    lane = next(x for x in st.ellipse_spans(s2) if x["label"] == str(a.tid))
    kk = lane["keys"]
    assert len(kk) >= 4
    tgt = kk[len(kk) // 2]
    others = [k for k in kk if k != tgt]
    vbefore = {k: tuple(round(v, 2) for v in next(it for it in st.resolver(s2).items(k) if it.key == lane["key"]).ell[:4])
               for k in others}
    assert st.delete_auto_key(lane["key"], tgt) == "", "가운데 키 삭제 실패"
    s2 = st.source()
    lane3 = next(x for x in st.ellipse_spans(s2) if x["key"] == st.sel)
    assert sorted(lane3["keys"]) == sorted(others), f"지운 키만 빠져야 하는데 키 집합이 바뀜 {lane3['keys']} vs {others}"
    for k in others:
        vnow = tuple(round(v, 2) for v in next(it for it in st.resolver(s2).items(k) if it.key == st.sel).ell[:4])
        assert vnow == vbefore[k], f"다른 키 {k} 의 값이 바뀜 {vbefore[k]} → {vnow}"
    assert st.delete_auto_key(st.sel, others[len(others) // 2]) == "", "연속 삭제 실패(항상 지워져야)"
    assert st.undo()
    s2 = st.source()                                           # 실행취소 = 프로젝트를 통째로 되돌림(새 객체)
    # B(40~69 사이 빈 구간이 이어진 사람) 말고, 재생선이 없는 곳에서 삭제: 수동 타원 + 자동 타원
    s2.edits.clear(); st._res.clear()
    tr2 = analyze.blur_track({"det": d[(d[:, 0] < 30) | (d[:, 1] < 700)], "n": z["n"]}, s2.blur)   # B 는 0~29 에만
    st.track = lambda src, mode=None: tr2
    lane_b = next(x for x in st.ellipse_spans(s2) if x["intervals"][-1][1] < 60)
    assert st._item(s2, lane_b["key"], 100) is None
    st.delete_ellipse(lane_b["key"], 100)                      # 재생선 = 100(그 타원 없음)
    assert all(x["intervals"][-1][1] >= 60 for x in st.ellipse_spans(s2)), "구간 밖에서 레이어 삭제 안 됨"
    assert st.undo() and any(x["intervals"][-1][1] < 60 for x in st.ellipse_spans(st.source()))
    print("flat / delete anywhere OK")


def main():
    flat_check()
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
    # 추가(그린) 타원: 클립 처음~끝 존재. 30·50 키, 그 사이 보간, 첫 키 이전엔 첫 값 유지
    m = edits.add_manual(s, 30, (100, 100, 60, 75))
    edits.set_key(m, 50, (300, 100, 60, 75))
    r = edits.Resolver(s, tr)
    m29 = next(it for it in r.items(29) if it.gid == m["gid"])
    assert abs(m29.ell[0] - 100) < 1e-3, "그린 타원은 첫 키 이전에도 존재(첫 값)"
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

    # 다중 얼굴 뷰티: 2개 얼굴 슬롯 → 자동 타원 2개
    from mosco import models
    N = 30
    lm = np.full((N, 2, 468, 2), np.nan, np.float32)
    ang = np.linspace(0, 2 * np.pi, len(models.FACE_OVAL), endpoint=False)
    for cx, cy, slot in ((500, 400, 0), (1300, 500, 1)):
        oval = np.stack([cx + 120 * np.cos(ang), cy + 160 * np.sin(ang)], 1).astype(np.float32)
        for f in range(N):
            lm[f, slot, :] = (cx, cy)                 # 전체 랜드마크 유효(0번 포함)
            lm[f, slot, models.FACE_OVAL] = oval      # 윤곽만 타원으로
    zb = {"lm": lm, "n": np.array(N), "k": np.array(2)}
    sbm = Source("x.mov", "beauty")
    trb = analyze.beauty_track(zb, sbm.beauty)
    assert len(trb.at(15)) == 2, "두 얼굴 슬롯이 다 안 잡힘"
    rbm = edits.Resolver(sbm, trb)
    its = rbm.items(15)
    assert len(its) == 2 and {it.tid for it in its} == {1, 2}, f"자동 타원 2개가 아님: {its}"
    print("beauty multiface OK")
    minus_check()
    sign_check()
    skin_range_migration()
    names_and_presets()
    eye_area()
    user_folder()
    print("EDITS OK")


def user_folder():
    """v0.7.72 프리셋·설정 공용 폴더(내 문서\\MOSCO): 처음에 프로그램 폴더 것을 복사 · 덮어쓰지 않음 · 못 만들면 예전 방식."""
    import os
    import tempfile
    from mosco import paths
    keep_dir, keep_app, keep_env = paths._USER_DIR, paths.app_dir, os.environ.get("MOSCO_USER_DIR")
    try:
        old = Path(tempfile.mkdtemp())                 # 가짜 프로그램 폴더(예전 버전)
        (old / "presets").mkdir()
        (old / "presets" / "내 프리셋.json").write_text('{"mode": "beauty", "params": {"strength": 61}}', encoding="utf-8")
        (old / "settings.json").write_text('{"default_mode": "blur"}', encoding="utf-8")
        paths.app_dir = lambda: old
        shared = Path(tempfile.mkdtemp()) / "문서" / "MOSCO"
        os.environ["MOSCO_USER_DIR"] = str(shared)
        paths._USER_DIR = None
        assert paths.user_dir() == shared and paths.preset_dir() == shared / "presets"
        assert (shared / "presets" / "내 프리셋.json").exists() and paths.settings_path().read_text(encoding="utf-8") == '{"default_mode": "blur"}'
        # 두 번째(다른 버전 폴더에서 실행): 공용 폴더에 이미 있으면 덮어쓰지 않음
        (shared / "presets" / "내 프리셋.json").write_text('{"mode": "beauty", "params": {"strength": 99}}', encoding="utf-8")
        old2 = Path(tempfile.mkdtemp())
        (old2 / "presets").mkdir()
        (old2 / "presets" / "다른 것.json").write_text("{}", encoding="utf-8")
        paths.app_dir = lambda: old2
        paths._USER_DIR = None
        paths.user_dir()
        assert '"strength": 99' in (shared / "presets" / "내 프리셋.json").read_text(encoding="utf-8")
        assert not (shared / "presets" / "다른 것.json").exists()
        # 공용 폴더를 만들 수 없으면 프로그램 폴더(예전 방식)
        blocker = Path(tempfile.mkdtemp()) / "file"
        blocker.write_text("x", encoding="utf-8")
        os.environ["MOSCO_USER_DIR"] = str(blocker / "MOSCO")
        paths._USER_DIR = None
        assert paths.user_dir() == old2
        print("user folder OK")
    finally:
        paths.app_dir = keep_app
        paths._USER_DIR = keep_dir
        if keep_env is None:
            os.environ.pop("MOSCO_USER_DIR", None)
        else:
            os.environ["MOSCO_USER_DIR"] = keep_env


def eye_area():
    """v0.7.63 추가 피부색(섀도 등) · 눈 제외 크기 슬라이더."""
    from mosco import fx, models
    from mosco.project import BEAUTY_DEFAULT
    rng = np.random.default_rng(3)
    pts = np.zeros((468, 2), np.float32)
    oval = np.array(models.FACE_OVAL)
    t = np.linspace(0, 2 * np.pi, len(oval), endpoint=False)
    pts[oval] = np.c_[300 + 150 * np.cos(t), 300 + 190 * np.sin(t)]
    for eye, brow, cx in ((models.LEFT_EYE, models.LEFT_BROW, 360), (models.RIGHT_EYE, models.RIGHT_BROW, 240)):
        te = np.linspace(0, 2 * np.pi, len(eye), endpoint=False)
        pts[eye] = np.c_[cx + 28 * np.cos(te), 260 + 10 * np.sin(te)]
        pts[brow] = np.c_[np.linspace(cx - 32, cx + 32, len(brow)), np.full(len(brow), 222)]
    pts[models.LIPS] = np.c_[300 + 40 * np.cos(np.linspace(0, 6.28, len(models.LIPS))),
                             400 + 12 * np.sin(np.linspace(0, 6.28, len(models.LIPS)))]
    roi = (0, 0, 600, 600)
    # 눈 제외 크기: 0 = 눈 구멍 없음 · 50 = 줄어듦 · 100 = 기본 · 200 = 넓어짐. 눈썹·입은 그대로
    fm = {e: fx.feature_mask(pts, roi, 300.0, e) for e in (0, 50, 100, 200)}
    assert fm[0][260, 360] < 0.05 and fm[100][260, 360] > 0.9, "눈 가운데"
    assert fm[50][260, 384] < 0.2 < fm[100][260, 384], "50% 면 눈 가장자리는 빠져야 함"
    assert fm[200][260, 398] > fm[100][260, 398] + 0.3, "200% 면 넓어져야 함"
    for e in fm:
        assert fm[e][400, 300] > 0.9 and fm[e][222, 360] > 0.5 and abs(fm[e][222, 360] - fm[100][222, 360]) < 1e-6, \
            "눈썹·입 제외는 그대로"
    skin = np.array([0.78, 0.60, 0.50], np.float32)
    img = np.tile(skin, (600, 600, 1)) + rng.normal(0, 0.01, (600, 600, 3)).astype(np.float32)
    img[232:300, 190:410] = (0.42, 0.26, 0.48)                      # 보라 섀도(피부색 아님)
    fr = (np.clip(img, 0, 1) * 65535).astype(np.uint16)
    it = edits.Item(("t", 1), 1, (300.0, 300.0, 420.0, 520.0, 0.0), pts)
    shadow = fx.pick_skin(fr, 300, 280)
    assert len(shadow) == 3 and fx.ref_rgb(shadow)[2] > fx.ref_rgb(shadow)[1], shadow
    base = dict(BEAUTY_DEFAULT, skin_ref=[-0.06, 0.07], unify=60)
    r0 = fx.beauty(fr, [it], base, 0, want_keys=True)
    r1 = fx.beauty(fr, [it], dict(base, skin_extra=[shadow]), 0, want_keys=True)
    assert r0.region[284, 300] < 0.2 and r1.region[284, 300] > 0.7, (r0.region[284, 300], r1.region[284, 300])
    assert abs(r1.region[340, 300] - r0.region[340, 300]) < 0.02, "섀도 밖이 바뀜"
    # 추가 피부색 영역은 색 통일 제외 → 섀도 색 유지
    def chroma(o):
        f = o[276:292, 280:320].astype(np.float32) / 65535
        return float((f[..., 2] - f[..., 1]).mean())
    nw = dict(base, wrinkle=0, grain=0)             # (주름 메우기는 이 합성의 각진 어두운 면을 골로 보고 올림 → 빼고 비교)
    kept = fx.beauty(fr, [it], dict(nw, skin_extra=[shadow]), 0).out
    assert abs(chroma(kept) - chroma(fr)) < 0.004, (chroma(kept), chroma(fr))
    # 눈 구멍 0 + 추가 피부색 → 눈 자리까지 보정 영역
    r2 = fx.beauty(fr, [it], dict(base, skin_extra=[shadow], eye_hole=0), 0, want_keys=True)
    assert r1.region[260, 360] < 0.2 < 0.7 < r2.region[260, 360]
    # v0.7.64 소프트(약한 블러) · 톤업(밝은 곳은 덜): 피부 안에서만, 기본 0 이면 그대로
    plain = dict(base, strength=0, wrinkle=0, grain=0, unify=0, features=False)
    assert np.array_equal(fx.beauty(fr, [it], plain, 0).out, fr), "아무 것도 안 켰는데 바뀜"
    tn = fx.beauty(fr, [it], dict(plain, toneup=60), 0).out.astype(np.float32)
    f0 = fr.astype(np.float32)
    up = float((tn[330:350, 280:320] / f0[330:350, 280:320]).mean())
    assert 1.01 < up < 1.12, up                                    # 피부(밝기 약 0.63): 조금 밝아짐
    assert abs(float((tn[20:40, 20:60] / f0[20:40, 20:60]).mean()) - 1) < 1e-3, "피부 밖이 바뀜"
    hi = fr.copy(); hi[:] = (np.array([0.97, 0.93, 0.90]) * 65535).astype(np.uint16)
    hu = fx.beauty(hi, [it], dict(plain, toneup=100, skin_ref=fx.pick_skin(hi, 300, 340)[:2]), 0).out
    assert float(hu[330:350, 280:320].max()) / 65535 < 1.0 and float((hu[330:350, 280:320].astype(np.float32) / hi[330:350, 280:320]).mean()) < 1.01, \
        "하이라이트가 날아감"
    sf = fx.beauty(fr, [it], dict(plain, soft=100), 0).out.astype(np.float32)
    n0, n1 = float(f0[330:350, 280:320, 1].std()), float(sf[330:350, 280:320, 1].std())
    assert n1 < n0 * 0.8 and abs(float(sf[330:350, 280:320].mean() - f0[330:350, 280:320].mean())) < 80, (n0, n1)
    # v0.7.65 번들거림 줄이기(밝은 점만 누름) · 톤(+분홍/−노랑, 밝기 유지) · 주름 메우기(골 깊이까지만 → 뒤집힘 없음)
    sp = img.copy()
    cv2.circle(sp, (300, 340), 7, (0.95, 0.80, 0.72), -1)             # 번들거리는 점
    sp = cv2.GaussianBlur(sp, (0, 0), 2)
    cv2.line(sp, (250, 330), (270, 356), (0.66, 0.50, 0.41), 2)       # 얕은 주름 골
    sp = cv2.GaussianBlur(sp, (0, 0), 0.8)
    fs = (np.clip(sp, 0, 1) * 65535).astype(np.uint16)
    lum = lambda o: 0.2126 * o[..., 0].astype(np.float32) + 0.7152 * o[..., 1] + 0.0722 * o[..., 2]
    sh = fx.beauty(fs, [it], dict(plain, shine=100), 0).out
    assert lum(sh)[340, 300] < lum(fs)[340, 300] * 0.97 and abs(lum(sh)[360, 330] / lum(fs)[360, 330] - 1) < 0.01, \
        (lum(sh)[340, 300] / lum(fs)[340, 300], lum(sh)[360, 330] / lum(fs)[360, 330])
    def cbcr(o):
        f = o[330:350, 320:340].astype(np.float32) / 65535
        yy = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
        return float(((f[..., 2] - yy) / 1.8556).mean()), float(yy.mean())
    c0, cp, cn = cbcr(fs), cbcr(fx.beauty(fs, [it], dict(plain, tint=100), 0).out), cbcr(fx.beauty(fs, [it], dict(plain, tint=-100), 0).out)
    assert 0.012 < cp[0] - c0[0] < 0.021 and -0.021 < cn[0] - c0[0] < -0.012 and abs(cp[1] - c0[1]) < 0.003, (c0, cp, cn)
    wr = fx.beauty(fs, [it], dict(plain, wrinkle=100), 0).out
    gap0 = lum(fs)[343, 260] / lum(fs)[343, 275]                       # 골 / 옆 피부
    gap1 = lum(wr)[343, 260] / lum(wr)[343, 275]
    assert gap0 < 0.95 and gap0 < gap1 <= 1.005, (gap0, gap1)         # 메워지되 주변보다 밝아지지 않음
    print("skin extra · eye hole · soft · toneup · shine · tint · wrinkle OK")


def names_and_presets():
    """v0.7.61 출력 이름: "(클린)" 제거 · "_MOSCO" 없음 · 원본 덮어쓰지 않음 / 깨진 기본 프리셋 자동 복구."""
    import tempfile
    from mosco import paths, render
    from mosco import project as P
    for a, b in (("A_0122(클린)", "A_0122"), ("A_0122 (클린)", "A_0122"), ("A_0122_(Clean)", "A_0122"),
                 ("A[클린]_v2", "A_v2"), ("(클린)", "(클린)"), ("클린룸 촬영", "클린룸 촬영"), ("B_0001", "B_0001")):
        assert P.out_stem(a) == b, (a, P.out_stem(a))
    d = Path(tempfile.mkdtemp())
    src = d / "C_01(클린).mov"
    src.write_bytes(b"x")
    n = render.out_names(Source(str(src), "beauty"), {}, str(d))
    assert n["main"].name == "C_01.mov" and n["overlay"].name == "C_01_BLUR4444.mov", n
    plain = d / "D_02.mov"
    plain.write_bytes(b"x")
    assert render.out_names(Source(str(plain), "beauty"), {}, str(d))["main"].name == "D_02_2.mov", "원본을 덮어씀"
    assert render.out_names(Source(str(plain), "beauty"), {"name": "최종"}, str(d))["main"].name == "최종_D_02.mov"
    # v0.7.68 명칭을 뒤에 붙이기
    back = render.out_names(Source(str(plain), "beauty"), {"name": "최종", "name_pos": "back"}, str(d))
    assert back["main"].name == "D_02_최종.mov" and back["overlay"].name == "D_02_최종_BLUR4444.mov", back
    assert render.out_names(Source(str(src), "beauty"), {"name": "v2", "name_pos": "back"}, str(d))["main"].name == "C_01_v2.mov"
    old_pd = paths.preset_dir
    try:
        paths.preset_dir = lambda: d
        (d / "인터뷰 기본.json").write_bytes(b"")                       # 빈 파일(깨짐)
        assert P.load_preset("인터뷰 기본")["params"]["strength"] == 50
        assert P.load_preset("인터뷰 기본")["mode"] == "beauty"            # 다시 써 둠
        (d / "bom.json").write_bytes(b"\xef\xbb\xbf" + '{"mode": "blur", "params": {}}'.encode())
        assert P.load_preset("bom")["mode"] == "blur"
        (d / "내것.json").write_bytes(b"{")
        try:
            P.load_preset("내것")
            raise AssertionError("깨진 프리셋인데 오류 없음")
        except ValueError as e:
            assert "읽을 수 없습니다" in str(e)
    finally:
        paths.preset_dir = old_pd
    print("names · presets OK")


def skin_range_migration():
    """v0.7.57 피부 범위 기본 50→40: 예전 기본값으로 저장된 값·기본 프리셋만 한 번 40으로."""
    import json
    import tempfile
    from mosco import paths
    old_sp, old_pd = paths.settings_path, paths.preset_dir
    try:
        from mosco import paths
        d=Path(tempfile.mkdtemp())
        paths.settings_path=lambda: d/"settings.json"; paths.preset_dir=lambda: (d/"presets").mkdir(exist_ok=True) or d/"presets"
        from mosco import project as P
        P.paths=paths
        # 예전 상태: 저장된 사용자 기본값 50, 기본 프리셋 50, 사용자 프리셋 50
        (d/"settings.json").write_text(json.dumps({"beauty":{"skin_range":50,"strength":60}}))
        for n in P.BUILTIN_BEAUTY+("내 프리셋",): P.save_preset(n,"beauty",{**P.BEAUTY_DEFAULT,"skin_range":50})
        s=P.Settings()
        assert s.d["beauty"]["skin_range"]==40 and s.d["beauty"]["strength"]==60
        assert all(P.load_preset(n)["params"]["skin_range"]==40 for n in P.BUILTIN_BEAUTY)
        assert P.load_preset("내 프리셋")["params"]["skin_range"]==50
        # 한 번만: 다시 50으로 바꾸면 그대로 유지
        j=json.loads((d/"settings.json").read_text()); j["beauty"]["skin_range"]=50; (d/"settings.json").write_text(json.dumps(j))
        assert P.Settings().d["beauty"]["skin_range"]==50
        # 직접 바꾼 값(55)은 안 건드림
        (d/"settings.json").write_text(json.dumps({"beauty":{"skin_range":55}}))
        assert P.Settings().d["beauty"]["skin_range"]==55
        print("skin_range migration OK")
    finally:
        paths.settings_path, paths.preset_dir = old_sp, old_pd


if __name__ == "__main__":
    main()
