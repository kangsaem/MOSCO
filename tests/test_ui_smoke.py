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
    # v0.7.14 AE 보내기: 실제 간판 트랙 → .jsx (사각형 마스크, 고친 꼭짓점 키 포함)
    jsx = out / "sign_AE.jsx"
    j = w.export_ae(str(jsx))
    assert j is not None and pump(app, 120, lambda: jsx.exists() and jsx.stat().st_size > 0), "AE 보내기 파일 없음"
    pump(app, 0.5)
    txt = jsx.read_text(encoding="ascii")
    assert txt.count('{name:"') >= 3 and "ADBE Mask Shape" in txt, txt[:400]
    print("sign ui OK (AE 보내기", jsx.stat().st_size, "bytes)")


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
    w.panel.set_key("final")
    pump(app, 0.5)
    w.grab().save(str(out / "3_beauty_split_key.png"))
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
