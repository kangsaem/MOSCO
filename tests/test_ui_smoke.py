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
    # v0.7.73 오토 키 클릭 → 테두리 + Del = 그 키만 삭제 · 레이어 몸통 클릭 → 재생선이 구간 밖이어도 Del = 레이어 삭제
    def _mid_key(x):                             # 구간 처음·끝이 아니고 수동 키와 안 겹치는 오토 키
        ks = x["keys"]
        return next((k for k in ks[1:-1] if all(abs(k - u) > 8 for u in x.get("ukeys", ()))
                     and all(a < k < b for a, b in x["intervals"] if a <= k <= b)), None)
    lane_i = next((i for i, x in enumerate(ln.spans) if len(x["keys"]) >= 3 and _mid_key(x) is not None), None)
    if lane_i is not None:
        d = ln.spans[lane_i]
        y = ln.PAD + lane_i * ln.LANE_H + ln.LANE_H / 2
        kmid = _mid_key(d)
        mouse(QEvent.MouseButtonPress, ln._x(kmid + 0.5))
        mouse(QEvent.MouseButtonRelease, ln._x(kmid + 0.5))
        pump(app, 0.5)
        assert ln.sel_kf is not None and ln.sel_kf[2:] == (kmid, "auto"), f"오토 키가 안 골라짐 {ln.sel_kf}"
        w.grab().save(str(out / "9b_key_selected.png"))
        w._del()
        pump(app, 0.8)
        src_ = st.source()
        assert any(g.get("akeys") for g in src_.edits[src_.mode]["groups"]), "오토 키 삭제(고정)가 기록되지 않음"
        assert not any(g.get("off_all") for g in src_.edits[src_.mode]["groups"]), "키만 지워야 하는데 레이어가 지워짐"
        st.undo()
        pump(app, 0.5)
        # v0.7.81 패널 [키 삭제] 버튼도 고른 오토 키를 지워야 함(예전엔 delete_key 만 불러 안 빠졌음)
        d_ = ln.spans[lane_i]
        k2 = _mid_key(d_)
        if k2 is not None:
            w.seek(0)                            # 재생선을 키에서 떨어뜨림(안 그러면 클릭이 스크럽으로 잡힘)
            pump(app, 0.3)
            mouse(QEvent.MouseButtonPress, ln._x(k2 + 0.5))
            mouse(QEvent.MouseButtonRelease, ln._x(k2 + 0.5))
            pump(app, 0.4)
            assert ln.sel_kf is not None and ln.sel_kf[3] == "auto", f"오토 키 재선택 실패 {ln.sel_kf}"
            w.panel.del_key.emit()                   # = [키 삭제] 버튼
            pump(app, 0.8)
            src2 = st.source()
            assert any(g.get("akeys") for g in src2.edits[src2.mode]["groups"]), \
                "[키 삭제] 버튼이 오토 키를 안 지움"
            assert not any(g.get("off_all") for g in src2.edits[src2.mode]["groups"]), "[키 삭제]가 레이어를 지움"
            assert ln.sel_kf is None, "삭제 후 키 선택이 안 풀림"
            st.undo()
            pump(app, 0.5)
        print("auto key select+delete OK (Del + [키 삭제] 버튼)")
    else:
        print("auto key: 키 3개 이상인 레인 없음 — 건너뜀", [len(x["keys"]) for x in ln.spans])
    # v0.7.76 레이어 여러 개 선택(Ctrl·Shift) → Del 한 번에 삭제 · 고르거나 손대도 레인 순서 그대로 · 스크롤바 여백
    from PySide6.QtGui import QMouseEvent as _ME
    def lane_click(i, mods=Qt.NoModifier):
        dd = ln.spans[i]
        a_, b_ = dd["intervals"][0]
        cand = [f for f in range(a_ + 2, b_ - 1) if all(abs(f - k) > max(4, ln.n // 60) for k in list(dd["keys"]) + list(dd.get("ukeys", ())))
                and abs(ln._x(f + 0.5) - ln._x(ln.cur + 0.5)) > 10]
        pos = QPointF(ln._x(cand[len(cand) // 2] + 0.5), ln.PAD + i * ln.LANE_H + ln.LANE_H / 2)
        for t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            app.sendEvent(ln, _ME(t, pos, ln.mapToGlobal(pos), Qt.LeftButton,
                                  Qt.LeftButton if t == QEvent.MouseButtonPress else Qt.NoButton, mods))
        pump(app, 0.2)
    same = [i for i, x in enumerate(ln.spans) if x.get("mode") == ln.spans[0].get("mode")]
    if len(same) >= 3:
        labels0 = [x["label"] for x in ln.spans]
        lane_click(same[0])
        assert ln.sel == ln.spans[same[0]]["key"] and not ln.multi
        lane_click(same[2], Qt.ShiftModifier)
        assert len(ln.multi) == 2, ln.multi                     # 대표 + 2 = 3줄
        lane_click(same[1], Qt.ControlModifier)
        assert len(ln.multi) == 1, ln.multi                     # 가운데 빼기
        lane_click(same[1], Qt.ControlModifier)
        assert len(ln.multi) == 2
        assert [x["label"] for x in ln.spans] == labels0, "고르기만 했는데 레인 순서가 바뀜"
        n_before = len(ln.spans)
        w._del()
        pump(app, 0.8)
        assert len(ln.spans) == n_before - 3, f"여러 레이어 삭제 {n_before} → {len(ln.spans)}"
        st.undo()
        pump(app, 0.5)
        assert len(ln.spans) == n_before and [x["label"] for x in ln.spans] == labels0, "실행취소 한 번에 다 돌아와야 함"
        # 손대서 그룹이 되어도(구간을 줄여도) 위아래 순서는 그대로
        d1 = ln.spans[same[1]]
        a_, b_ = d1["intervals"][0]
        w.on_segments(d1["key"], [(a_ + (b_ - a_) // 2, b_)]) if hasattr(w, "on_segments") else st.set_spans(d1["key"], [(a_ + (b_ - a_) // 2, b_)])
        pump(app, 0.5)
        assert [x["label"] for x in ln.spans] == labels0, f"구간을 줄였더니 순서가 바뀜 {[x['label'] for x in ln.spans]}"
        st.undo()
        pump(app, 0.5)
        print("lane multi select · order OK")
    sb = w.lanes_box.verticalScrollBar()
    assert w.bar.width() == ln.width() or sb.isVisible(), (w.bar.width(), ln.width())
    old_max = w.lanes_box.maximumHeight()
    w.lanes_box.setMaximumHeight(ln.PAD * 2 + ln.LANE_H)         # 스크롤바가 생기게 억지로 낮춤
    w.lanes_box.setMinimumHeight(ln.PAD * 2 + ln.LANE_H)
    pump(app, 0.5)
    if sb.isVisible():
        assert abs(w.bar.width() - ln.width()) <= 1 and abs(w.ruler.width() - ln.width()) <= 1, \
            f"스크롤바가 있을 때 레인·소스 바 폭이 다름 {ln.width()} {w.bar.width()} {w.ruler.width()}"
        print("scrollbar pad OK", ln.width(), w.bar.width())
    w.lanes_box.setMaximumHeight(old_max)
    w.lanes_box.setMinimumHeight(ln.PAD * 2 + 2 * ln.LANE_H)
    pump(app, 0.5)
    assert w.bar.width() == ln.width() or sb.isVisible(), "스크롤바가 없어졌는데 여백이 남음"
    n_l = len(ln.spans)
    short = next((i for i, x in enumerate(ln.spans) if x["intervals"][-1][1] < w._n() - 3 or x["intervals"][0][0] > 2), None)
    if short is not None:
        d = ln.spans[short]
        outside = w._n() - 1 if d["intervals"][-1][1] < w._n() - 3 else 0
        w.seek(outside)
        pump(app, 0.5)
        y = ln.PAD + short * ln.LANE_H + ln.LANE_H / 2
        a_, b_ = d["intervals"][0]
        mouse(QEvent.MouseButtonPress, (ln._x(a_) + ln._x(b_ + 1)) / 2 + 0.3)
        mouse(QEvent.MouseButtonRelease, (ln._x(a_) + ln._x(b_ + 1)) / 2 + 0.3)
        pump(app, 0.3)
        if ln.sel_kf is None:                    # 가운데가 마침 키 위면 건너뜀
            w._del()
            pump(app, 0.8)
            assert len(ln.spans) == n_l - 1, f"재생선이 구간 밖일 때 레이어 삭제 안 됨 {n_l} → {len(ln.spans)}"
            st.undo()
            pump(app, 0.5)
            print("lane delete anywhere OK")
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
    # v0.7.76 프리셋 창: 열면 아무것도 안 고른 상태(현재 클립 설정은 글자로만) · 고르기만 해서는 적용·닫힘 없음 ·
    # [적용] = 닫지 않고 적용 · 닫기 = ✕ 만(Esc 무시)
    from mosco import autodetect as ad
    from mosco.ui_panel import PresetGridPopup
    st.select(0)
    st.set_params("beauty", ad.to_params("beauty", "strong")[1])
    dlg = PresetGridPopup(w.panel, "beauty", st.source())
    assert dlg.sseg.value() is None and not dlg.b_apply.isEnabled(), dlg.sseg.value()
    assert "강" in dlg.cur_lb.text(), dlg.cur_lb.text()
    got = []
    dlg.on_apply = lambda m, p, lb: (got.append(lb), w.panel._apply_preset(m, p, lb))
    dlg.show()
    pump(app, 0.2)
    dlg.sseg.btns["weak"].click()
    assert dlg.pick == ("level", "weak") and dlg.b_apply.isEnabled() and not got and dlg.isVisible()
    assert ad.level_of("beauty", st.source().params_for("beauty")) == "strong", "고르기만 했는데 적용됨"
    dlg.b_apply.click()
    pump(app, 0.3)
    assert got and dlg.isVisible(), "[적용] 뒤에도 창은 열려 있어야 함"
    assert ad.level_of("beauty", st.source().params_for("beauty")) == "weak"
    # 저장된 프리셋 버튼(v0.7.63 클릭 신호가 인자를 덮던 버그 확인 포함): 고르기만 → [적용] 에서 적용
    nm = next(iter(dlg.named))
    dlg.named[nm].click()
    assert dlg.pick == ("named", nm) and dlg.sseg.value() is None and len(got) == 1 and dlg.isVisible()
    dlg.b_apply.click()
    pump(app, 0.3)
    assert got[-1] == nm and dlg.isVisible()
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(dlg, _Qt.Key_Escape)
    pump(app, 0.2)
    assert dlg.isVisible(), "Esc 로 닫히면 안 됨"
    dlg.b_x.click()
    pump(app, 0.2)
    assert not dlg.isVisible(), "✕ 로 닫혀야 함"
    # v0.7.77 툴박스: 비모달로 띄워 둔 채 다른 조작 · 현재 클립 따라가기 · [프리셋] 토글
    w.panel.b_preset.click()                        # 열기(토글)
    pump(app, 0.3)
    tb = w.panel._preset_dlg
    assert tb is not None and tb.isVisible() and not tb.isModal(), "툴박스가 비모달로 안 뜸"
    assert w.panel.b_preset.property("on"), "열려 있는 동안 버튼이 켜져야 함"
    st.select(0)
    st.set_params("beauty", ad.to_params("beauty", "strong")[1])
    pump(app, 0.3)
    assert "강" in tb.cur_lb.text(), tb.cur_lb.text()
    if len(st.project.sources) > 1:                 # 다른 클립 선택 → 창이 따라감
        st.select(1)
        st.set_params("beauty", ad.to_params("beauty", "weak")[1])
        pump(app, 0.3)
        assert "약" in tb.cur_lb.text(), f"현재 클립 안 따라감: {tb.cur_lb.text()}"
    tb.sseg.btns["mid"].click()
    tb.b_apply.click()                              # 띄운 채 적용 → 현재 클립에 적용, 창은 유지
    pump(app, 0.3)
    assert tb.isVisible() and ad.level_of("beauty", st.source().params_for("beauty")) == "mid"
    w.panel.b_preset.click()                        # 다시 눌러 닫기(토글)
    pump(app, 0.3)
    assert w.panel._preset_dlg is None and not w.panel.b_preset.property("on"), "토글로 안 닫힘"
    st.set_params("beauty", ad.to_params("beauty", "strong")[1])     # 뒤 검사를 위해 뷰티로 되돌림
    st.set_param("strength", 61)
    pump(app, 0.3)
    # v0.7.76 목록 영역 선택(사각형) · 레이어 여러 개 선택
    assert w.sources.isSelectionRectVisible() and w.rpanel.list.isSelectionRectVisible()
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
    # v0.7.79 종료 않고 새 프로젝트 → 타임라인(레이어·바·눈금)·캐시 싹 비움 (extras 맨 끝: 프로젝트 비워도 됨)
    from mosco.project import Project
    if w.panel._preset_dlg is not None:
        w.panel._preset_dlg.close()
    st.select(0)
    w.lanes.set_data([{"key": ("t", 1), "mode": "blur", "label": "1", "intervals": [(0, 9)],
                       "keys": [0, 9], "ukeys": [], "n": 10, "minus": False, "quad": False,
                       "manual": False, "sus": False, "note": ""}], 10, 0, ("t", 1))
    w.bar.set_data(10)
    st._tracks[("x", "blur")] = object()
    st._res["x"] = object()
    pump(app, 0.2)
    assert w.lanes.spans, "확인 전제 설정 실패"
    st.dirty = False                                    # 저장 확인 창 없이(테스트)
    w.new_project()
    pump(app, 0.5)
    assert w.state.source() is None and not w.lanes.spans and w.lanes.n == 0, \
        f"새 프로젝트인데 레이어가 남음: {len(w.lanes.spans)}"
    assert w.bar.n == 0 and w.ruler.n == 0 and w.state.sel is None, "소스 바·눈금·선택이 안 비워짐"
    assert not w.state._tracks and not w.state._res, "트랙·해석 캐시가 안 비워짐"
    print("new project clears timeline OK")
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
    assert w.panel.b_analyze.parentWidget() is not None      # v0.7.70: [분석] 은 타원 편집 묶음 아래(스크롤 안)
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
