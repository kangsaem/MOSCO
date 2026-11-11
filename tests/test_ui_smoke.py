"""화면 흐름 점검(오프스크린). 사용: python tests/test_ui_smoke.py 영상1 [영상2] [캡처폴더]"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosco.ui_main import MainWindow  # noqa: E402


def pump(app, sec, until=None):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return False


def sign_ui(app, w, st, vids, out):
    """v0.7.8 간판: 대상 [간판] → 분석 → 사각형 표시 → 꼭짓점 끌기(모니터 신호) → 8값 키 저장. signs.mov 있을 때만."""
    if not vids:
        return
    clip = Path(vids[0]).with_name("signs.mov")
    if not clip.exists():
        print("sign ui SKIP (signs.mov 없음)")
        return
    st.add_files([str(clip)])
    st.select(len(st.project.sources) - 1)
    pump(app, 1.0)
    w.panel.t_sign.setChecked(True)      # v0.7.31: 대상 = 독립 토글(간판 켜기)
    s = st.source()
    assert s.mode == "sign" and w.panel.b_add.text() == "사각형 추가"
    st.ensure_analysis(s)
    assert pump(app, 240, lambda: st.analysis(s) is not None and st.analysis(s)["state"] == "done"), "간판 분석 안 끝남"
    w.seek(15)
    assert pump(app, 20, lambda: w.result is not None and w.result.get("i") == 15)
    ents = [e for e in w.mon.ells if e.get("quad")]
    assert len(ents) >= 3, f"사각형 표시 {len(ents)}"
    e = ents[0]
    q = [v for p in e["quad"] for v in p]
    q[4] += 20
    q[5] += 15
    w.mon.edited.emit(e["key"], tuple(q), e.get("mode"))   # 꼭짓점 하나 끌기 끝(보기 좌표)
    pump(app, 1.0)
    from mosco import edits
    gs = [g for g in edits.get(s)["groups"] if "15" in g.get("keys", {})]
    assert gs and len(gs[0]["keys"]["15"]) == 8, "꼭짓점 키(8값) 저장 안 됨"
    it = next(i for i in st.resolver(s).items(15) if i.gid == gs[0]["gid"])
    sc = w._vscale()
    assert abs(float(it.pts[2][0]) - q[4] / sc) < 1.5, (it.pts[2], q[4] / sc)
    w.grab().save(str(out / "9_sign.png"))
    # v0.7.67 레인 끝 손잡이: 끝에 키 ◆·재생선이 겹쳐 있어도 손잡이가 먼저 잡혀 길이를 줄일 수 있어야 함
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    ln = w.lanes
    pump(app, 0.5)
    d0 = ln.spans[0]
    a0, b0 = d0["intervals"][0]
    w.seek(a0)                                   # 재생선을 구간 시작에 겹쳐 둠
    pump(app, 0.5)
    y = ln.PAD + ln.LANE_H / 2

    def mouse(t, x):
        pos = QPointF(x, y)
        btn_ = Qt.LeftButton if t != QEvent.MouseMove else Qt.NoButton
        ln_ev = QMouseEvent(t, pos, ln.mapToGlobal(pos), btn_, Qt.LeftButton, Qt.NoModifier)
        {QEvent.MouseButtonPress: ln.mousePressEvent, QEvent.MouseMove: ln.mouseMoveEvent,
         QEvent.MouseButtonRelease: ln.mouseReleaseEvent}[t](ln_ev)
    x_in = ln._x(a0)
    mouse(QEvent.MouseButtonPress, x_in + 1)
    assert ln._drag is not None and ln._drag[1][0] == "seg_in", "끝 손잡이가 안 잡힘(재생선·키가 먼저 잡힘)"
    tgt = a0 + max(3, (b0 - a0) // 3)
    mouse(QEvent.MouseMove, ln._x(tgt + 0.5))
    mouse(QEvent.MouseButtonRelease, ln._x(tgt + 0.5))
    pump(app, 1.0)
    starts0 = sorted(x["intervals"][0][0] for x in ln.spans)
    assert any(abs(x["intervals"][0][0] - tgt) <= 1 for x in ln.spans), f"시작 지점이 안 바뀜 {a0} → 목표 {tgt}, 지금 {starts0}"
    st.undo()
    pump(app, 0.5)
    # v0.7.14 AE 보내기: 실제 간판 트랙 → .jsx (사각형 마스크, 고친 꼭짓점 키 포함)
    jsx = out / "sign_AE.jsx"
    j = w.export_ae(str(jsx))
    assert j is not None and pump(app, 120, lambda: jsx.exists() and jsx.stat().st_size > 0), "AE 보내기 파일 없음"
    pump(app, 0.5)
    txt = jsx.read_text(encoding="ascii")
    assert txt.count('{name:"') >= 3 and "ADBE Mask Shape" in txt, txt[:400]
    print("sign ui OK (AE 보내기", jsx.stat().st_size, "bytes)")


def extras(app, w, st, vids, out):
    """v0.7.56 다시 실행 · 원본 다시 연결 · 자동 저장 복구."""
    import shutil
    st.select(0)
    s0 = st.source()
    key = "strength"
    v0 = s0.params()[key]
    st.set_param(key, 11, undo=True)
    assert st.undo() and st.source().params()[key] == v0
    assert st.redo() and st.source().params()[key] == 11, "다시 실행 안 됨"
    assert st.undo() and not st.redo_stack == [] and st.source().params()[key] == v0
    st.set_param(key, 22, undo=True)
    assert not st.redo(), "새 편집 뒤에는 다시 실행 기록이 없어야 함"
    st.undo()
    # 원본 다시 연결: 원본을 다른 폴더로 옮긴 프로젝트 → 폴더 지정 → 같은 이름으로 연결, 편집값 유지
    a_dir, b_dir = out / "relink_a", out / "relink_b" / "sub"
    a_dir.mkdir(exist_ok=True)
    b_dir.mkdir(parents=True, exist_ok=True)
    moved = a_dir / "moved_clip.mov"
    shutil.copy(vids[0], moved)
    n0 = len(st.project.sources)
    st.add_files([str(moved)])
    pump(app, 5, lambda: st.info(str(moved)) is not None)
    st.select(n0)
    st.set_param(key, 33, undo=True)
    st.select(0)                                   # 다른 소스를 보는 상태에서(실사용: 프로그램을 끈 사이 원본이 옮겨짐)
    pump(app, 1.0)
    from mosco import decoder
    decoder.close_all()                            # 윈도우는 열려 있는 파일을 못 옮김 → 디코더를 닫고 옮김
    shutil.move(str(moved), str(b_dir / "MOVED_clip.mov"))
    assert [x.path for x in w._missing()] == [str(moved)]
    assert w.relink_missing(str(out / "relink_b")) == 1
    src = st.project.sources[n0]
    assert Path(src.path).exists() and src.params()[key] == 33 and not w._missing(), "다시 연결 실패"
    assert pump(app, 10, lambda: st.info(src.path) is not None), "다시 연결한 원본 정보 없음"
    # 자동 저장 → (튕김) → 복구
    w._clear_autosave()
    assert w.autosave_pending() is None
    st.dirty = True
    w._do_autosave()
    meta = w.autosave_pending()
    assert meta and meta["n"] == len(st.project.sources), meta
    paths_before = [x.path for x in st.project.sources]
    from mosco.project import Project
    st.set_project(Project())                      # 새로 켠 상태 흉내
    assert w.restore_autosave() and [x.path for x in st.project.sources] == paths_before and st.dirty
    assert st.project.sources[n0].params()[key] == 33
    w._clear_autosave()
    assert w.autosave_pending() is None
    # v0.7.60 프리셋 창: 현재 클립 설정(약·중·강)을 선택된 상태로 보여 줌, 직접 조정했으면 선택 없음
    from mosco import autodetect as ad
    from mosco.ui_panel import PresetGridPopup
    st.select(0)
    st.set_params("beauty", ad.to_params("beauty", "strong")[1])
    dlg = PresetGridPopup(w.panel, "beauty", st.source())
    assert dlg.sseg.value() == "strong" and dlg.b_apply.isEnabled(), dlg.sseg.value()
    st.set_param("strength", 61)
    dlg = PresetGridPopup(w.panel, "beauty", st.source())
    assert dlg.sseg.value() is None and not dlg.b_apply.isEnabled()
    # v0.7.63 버튼 클릭 신호(checked=False)가 인자를 덮던 버그: 저장된 프리셋 버튼 · 팝업 버튼
    dlg = PresetGridPopup(w.panel, "beauty", st.source())
    nm = next(iter(dlg.named))
    dlg.named[nm].click()
    assert dlg.label == nm and dlg.result is not None, dlg.label
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QPushButton
    from mosco.ui_widgets import info_popup

    def press():
        for d2 in app.topLevelWidgets():
            if isinstance(d2, QDialog) and d2.isVisible():
                [b for b in d2.findChildren(QPushButton) if b.text() == "복구"][0].click()
    QTimer.singleShot(300, press)
    assert info_popup(w, "t", "m", [("버리기", "drop", False, 80), ("복구", "restore", True, 88)]) == "restore"
    # v0.7.63 피부색 추가(최대 2개) · 칩 · 눈 제외 크기 슬라이더
    assert ("beauty", "eye_hole") in w.panel.rows and not hasattr(w.panel, "t_eye")
    st.select(0)
    w.seek(5)
    assert pump(app, 10, lambda: w.frame_cache is not None and w.result is not None)
    for k in range(3):
        w.panel.b_addskin.setChecked(True)
        assert w.panel.pick_target == "extra"
        w.on_pick(200 + k * 40, 200)
    assert len(st.source().beauty["skin_extra"]) == 2 and not w.panel.b_addskin.isEnabled(), st.source().beauty["skin_extra"]
    w.panel.chips_extra[0].click()
    assert len(st.source().beauty["skin_extra"]) == 1 and w.panel.b_addskin.isEnabled()
    w.panel.b_pick.setChecked(True)
    w.on_pick(300, 300)
    assert st.source().beauty.get("skin_ref") and len(st.source().beauty["skin_extra"]) == 1
    st.set_param("skin_extra", [])
    st.set_param("skin_ref", None)
    # v0.7.64 여러 소스 선택 → 바꾼 값 하나가 같은 작업의 선택 소스에 모두 · 타원·피부색은 잠금
    n = len(st.project.sources)
    assert n >= 2
    for x in st.project.sources:
        x.mode = "beauty"
    st.project.sources[-1].mode = "blur"
    st.project.sources[1].beauty["wrinkle"] = 7
    w.sources.refresh()
    w.sources.selectAll()
    st.select(0)
    pump(app, 0.3)
    assert len(st.multi_selected()) == n
    assert not w.panel.sec_bell.isEnabled() and not w.panel.sec_edit.isEnabled() and not w.panel.b_pick.isEnabled()
    assert w.panel.multi_lb.isVisible()
    st.set_param("strength", 66, undo=True)
    got = [(x.mode, x.params_for("beauty")["strength"]) for x in st.project.sources]
    assert all(v == 66 for m, v in got if m == "beauty") and got[-1] == ("blur", st.project.sources[-1].beauty["strength"]) \
        and got[-1][1] != 66, got
    assert st.project.sources[1].beauty["wrinkle"] == 7, "다른 값까지 복사됨"
    assert st.project.sources[-1].blur["strength"] != 66, "다른 작업 클립에 들어감"
    st.set_param("skin_ref", [0.0, 0.0])
    assert st.project.sources[1].beauty.get("skin_ref") is None, "피부색은 한꺼번에 넣지 않음"
    st.undo()
    assert st.project.sources[1].beauty["strength"] != 66
    w.sources.clearSelection()
    w.sources.setCurrentRow(0)
    pump(app, 0.3)
    assert w.panel.sec_bell.isEnabled() and w.panel.b_pick.isEnabled() and not w.panel.multi_lb.isVisible()
    # v0.7.68 뷰티: 얼굴이 클립 전체에 안 깔린 소스 = 노랑 표시 판정 · 렌더 대기 항목의 분석 진행 표시
    import numpy as np
    s0 = st.project.sources[0]
    s0.mode = "beauty"
    def fake(miss):
        lm = np.zeros((60, 4, 468, 2), np.float16)
        lm[:, 1:] = np.nan
        lm[miss, 0] = np.nan
        st.an[(s0.path, "beauty")] = {"z": {"lm": lm, "n": np.array(60)}, "p": 1.0, "state": "done", "err": "", "level": 0, "bskip": 0.0}
    keep = st.an.get((s0.path, "beauty"))
    fake(slice(0, 0)); assert not st.beauty_gap(s0)
    fake(slice(20, 30)); assert not st.beauty_gap(s0), "짧은 빈 구간(놓침 유지 이내)은 노랑 아님"
    fake(slice(20, 45)); assert st.beauty_gap(s0), "긴 빈 구간"
    fake(slice(0, 8)); assert st.beauty_gap(s0), "처음에 얼굴 없음"
    fake(slice(50, 60)); assert st.beauty_gap(s0), "끝에 얼굴 없음"
    w.sources.viewport().repaint()
    from mosco.ui_side import RenderItem as _RI
    it2 = _RI(s0, {}, str(out))
    it2.job = object()
    st.an[(s0.path, "beauty")] = {"z": None, "p": 0.4, "state": "run", "err": "", "level": 0, "bskip": 0.0}
    assert abs(w._render_analysis_p(it2) - 0.4) < 1e-6
    st.an.pop((s0.path, "beauty"), None)
    assert w._render_analysis_p(it2) is None
    if keep is not None:
        st.an[(s0.path, "beauty")] = keep
    print("redo · relink · autosave · preset · skin extra · multi · gap OK")


def main():
    vids = [a for a in sys.argv[1:] if Path(a).suffix.lower() in (".mov", ".mp4")]
    out = Path(next((a for a in sys.argv[1:] if not a.lower().endswith((".mov", ".mp4"))), "ui_shots"))
    out.mkdir(exist_ok=True)
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(1536, 864)
    w.show()
    w.state.add_files(vids)
    st = w.state
    # 불러온 직후에는 자동 분석을 하지 않아야 함
    pump(app, 2)
    assert all(st.analysis(s) is None for s in st.project.sources), "불러오자마자 분석이 돌고 있음"
    # 명시적으로 분석(이 테스트는 트랙 기반 동작도 확인)
    for s in st.project.sources:
        st.analyze_now(s)
    ok = pump(app, 180, lambda: all(st.analysis(s) and st.analysis(s)["state"] == "done" for s in st.project.sources))
    assert ok, "분석이 끝나지 않음"
    if len(vids) > 1:
        st.select(1)
        st.set_mode("blur")
        st.analyze_now(st.source())
        pump(app, 120, lambda: (st.analysis(st.source()) or {}).get("state") == "done")
        st.select(0)
    pump(app, 3, lambda: w.result is not None)
    w.seek(40)
    assert pump(app, 20, lambda: w.result is not None and w.result.get("i") == 40), "미리보기 없음"
    w.grab().save(str(out / "1_beauty_fx.png"))
    w.panel.view.set("split")
    w.show_result()
    pump(app, 0.5)
    w.grab().save(str(out / "2_beauty_split.png"))
    # v0.7.54 키는 볼 때만 계산 · [분석] 버튼은 스크롤 밖(항상 보임) · 피부색 찍기 동작
    assert "final" not in w.result, "키 보기가 꺼져 있는데 키를 계산함"
    assert not w.panel.b_analyze.visibleRegion().isEmpty(), "[분석] 버튼이 안 보임"
    w.panel.set_key("final")
    assert pump(app, 10, lambda: w.result is not None and "final" in w.result), "키를 켰는데 계산 안 됨"
    w.grab().save(str(out / "3_beauty_split_key.png"))
    w.on_pick(300, 300)
    assert st.source().beauty.get("skin_ref"), "피부색 찍기 안 됨"
    st.set_param("skin_ref", None)
    pump(app, 0.5)
    from mosco.ui_side import RenderItem, eta_text
    import time as _t
    _it = RenderItem(st.source(), {}, str(out))
    _it.p, _it.t0 = 0.25, _t.perf_counter() - 60
    assert "분" in eta_text(_it), eta_text(_it)
    w.panel.set_key("skin")
    pump(app, 0.3)
    w.mon.grab().save(str(out / "4_skin_key.png"))
    w.panel.set_key("off")
    w.mon.zoom_step(1)
    w.mon.zoom_step(1)
    pump(app, 0.3)
    w.mon.grab().save(str(out / "5_zoom.png"))
    # 값 바꾸기 → 미리보기 갱신
    w.panel.rows[("beauty", "strength")].s.setValue(90)
    assert pump(app, 10, lambda: w.result is not None and w.result.get("i") == 40 and w.state.source().beauty["strength"] == 90)
    assert w.state.undo(), "실행취소 실패"
    assert w.state.source().beauty["strength"] == 50, "실행취소 값 복원 실패"
    # 타원 편집: 모니터에서 끌어 옮기기 → 키, 타원 추가, 타원별 값, 키 삭제, 실행취소
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    w.panel.view.set("fx")
    w.show_result()
    w.mon.reset_view()
    if not pump(app, 10, lambda: w.result is not None and w.result.get("ells")):
        # 얼굴 없는 영상(CI 합성): 타원을 하나 추가해서 그걸로 끌기 점검
        info = w._info()
        w.state.add_ellipse(w.f, info.w / 2, info.h / 2, info.h * 0.3)
        assert pump(app, 20, lambda: w.result is not None and w.result.get("ells")), "추가한 타원이 안 보임"
    w.show_result()
    pump(app, 0.3)
    ents = w.result["ells"]
    if ents:
        ent = ents[0]
        pane = w.mon.panes()[0]
        s, c = w.mon.xform(pane)
        cx, cy = ent["ell"][:2]
        p0 = QPoint(int(pane.center().x() + (cx - c.x()) * s), int(pane.center().y() + (cy - c.y()) * s))
        QTest.mousePress(w.mon, Qt.LeftButton, Qt.NoModifier, p0)
        QTest.mouseMove(w.mon, p0 + QPoint(30, 10))
        QTest.mouseRelease(w.mon, Qt.LeftButton, Qt.NoModifier, p0 + QPoint(30, 10))
        pump(app, 0.5)
        src = w.state.source()
        gs = src.edits.get(src.mode, {}).get("groups", [])
        g = next((g for g in gs if str(w.f) in g["keys"]), None)
        assert g is not None, f"드래그로 키가 안 생김 {gs}"
        assert w.f in w.bar.keys, w.bar.keys
        w.panel.ov_rows["strength"].s.setValue(80)
        pump(app, 0.3)
        assert g["ov"].get("strength") == 80
        assert w.state.delete_key(w.f)
        print("edit drag OK")
    w.panel.b_add.toggle()
    pump(app, 0.1)
    QTest.mouseClick(w.mon, Qt.LeftButton, Qt.NoModifier, QPoint(w.mon.width() // 3, w.mon.height() // 2))
    pump(app, 0.5)
    src = w.state.source()
    assert any(g.get("manual") for g in src.edits[src.mode]["groups"]), "타원 추가 안 됨"
    assert w.panel.b_add.isChecked(), "연속 그리기: 추가 모드가 켠 채로 유지돼야 함"
    w.panel.b_add.toggle()                 # 끄기
    pump(app, 0.1)
    assert w.state.undo()
    assert not any(g.get("manual") for g in w.state.source().edits[src.mode]["groups"]), "추가 실행취소 안 됨"
    print("edit add OK")
    # 드래그로 그리기(연속) → 수동 타원 1개
    w.panel.b_add.toggle()
    pump(app, 0.1)
    c = QPoint(w.mon.width() // 2, w.mon.height() // 2)
    QTest.mousePress(w.mon, Qt.LeftButton, Qt.NoModifier, c)
    QTest.mouseMove(w.mon, c + QPoint(60, 70))
    QTest.mouseRelease(w.mon, Qt.LeftButton, Qt.NoModifier, c + QPoint(60, 70))
    pump(app, 0.3)
    src = w.state.source()
    man = [g for g in src.edits[src.mode]["groups"] if g.get("manual")]
    assert man, "드래그로 그린 타원 없음"
    assert w.panel.b_add.isChecked(), "그린 뒤에도 추가 모드 유지"
    w.panel.b_add.toggle()
    pump(app, 0.1)
    # 삭제 모드: 그린 타원을 클릭해 삭제
    key = ("g", man[0]["gid"])
    w.on_delete(key)
    pump(app, 0.2)
    assert not any(g.get("manual") for g in w.state.source().edits[src.mode]["groups"]), "삭제 안 됨"
    print("draw+delete OK")
    pump(app, 1.0)
    w.grab().save(str(out / "8_edit.png"))
    # 재생
    f0 = w.f
    w.toggle_play()
    pump(app, 4, lambda: w.f > f0 + 5)
    assert w.f > f0, f"재생 안 됨 f={w.f} f0={f0}"
    w.toggle_play()
    if len(vids) > 1:
        st.select(1)
        w.panel.view.set("fx")
        assert pump(app, 20, lambda: w.result is not None and w.result.get("i") is not None)
        w.seek(35)
        assert pump(app, 20, lambda: w.result is not None and w.result.get("i") == 35)
        w.grab().save(str(out / "6_blur.png"))
        w.panel.view.set("split")
        w.show_result()
        pump(app, 0.3)
        w.grab().save(str(out / "7_blur_split.png"))
    sign_ui(app, w, st, vids, out)
    extras(app, w, st, vids, out)
    print("UI OK", out)
    w.state.dirty = False
    w.close()


if __name__ == "__main__":
    import os
    import mosco.ff as ff
    main()
    # Qt/PyAV 종료 정리에서 간헐 세그폴트가 나므로(판정은 위에서 끝남) 즉시 정상 종료
    ff.kill_all()
    sys.stdout.flush()
    os._exit(0)
