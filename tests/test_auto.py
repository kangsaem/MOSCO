"""AUTO 창·샘플 창 점검(오프스크린). 사용: python tests/test_auto.py 영상 [작업폴더]"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosco import autodetect as ad  # noqa: E402
from mosco.ui_auto import AutoDialog, SampleDialog  # noqa: E402
from mosco.ui_main import MainWindow  # noqa: E402


def pump(app, sec, until=None):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return False


def main():
    vid = next(a for a in sys.argv[1:] if a.lower().endswith((".mov", ".mp4")))
    work = Path(next((a for a in sys.argv[1:] if not a.lower().endswith((".mov", ".mp4"))), "auto_test"))
    work.mkdir(exist_ok=True)
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    w.state.add_files([vid, vid])          # 같은 영상 2개(목록·선택 확인용)
    pump(app, 2)
    # 불러오자마자 분석 안 함
    assert all(w.state.analysis(s) is None for s in w.state.project.sources)

    dlg = AutoDialog(w, w.state)
    assert len(dlg.clips) == 2 and dlg.width() == 998
    # v0.7.3: 창을 열 때는 아무 작업도 안 함(버튼을 눌러야 측정)
    pump(app, 1)
    assert not dlg._jobs and all(c["prog"] is None and c["measured"] is None for c in dlg.clips), "열자마자 측정함"
    assert dlg.b_correct.text() == "보정+편집"
    for c in dlg.clips:                    # 측정은 블러만 → 블러로 바꿔서 측정
        c["mode"] = "blur"
    dlg._phase = "measure"
    dlg._start_detect()
    assert pump(app, 90, lambda: dlg._all_detected()), "측정이 끝나지 않음"
    dlg._phase = None
    for c in dlg.clips:
        assert c["measured"] is not None and "min_face" in c["measured"]
    print("measure OK:", ad.LABEL.get(dlg.clips[0]["mode"]), dlg.clips[0]["measured"])

    # 작업 바꾸기: 두 번째 클립만 선택 → 블러
    dlg.selected = {1}
    dlg._on_work("blur")
    assert dlg.clips[1]["mode"] == "blur"
    # 출력 바꾸기 → 블러라 +α
    dlg._on_out("std")
    assert "+α" in dlg._out_chip(dlg.clips[1])
    # 기존 값 무시, 개별 보정
    dlg.keep_existing = False
    assigns = dlg._assignments()
    assert len(assigns) == 2
    w.state.apply_auto(assigns, keep_keys=True)
    assert w.state.project.sources[1].mode == "blur"
    assert w.state.project.sources[1].custom
    assert w.state.project.sources[1].blur["body"] is True
    assert 8 <= w.state.project.sources[1].blur["min_size"] <= 40      # 측정된 최소 얼굴 기반
    print("apply OK")
    dlg.reject()

    # AUTO [보정+편집] → 이 창에서 측정 → 분석 → 트랙까지 끝내고 닫힘(편집 화면에서 다시 안 기다림)
    w.state.settings.render["out_dir"] = str(work)
    import shutil as _sh
    from mosco import paths
    _sh.rmtree(paths.cache_dir() / "analysis", ignore_errors=True)    # 캐시 지움 → 실제 분석 단계를 거치게
    for k in list(w.state.an):
        w.state.an.pop(k)
    dlg2 = AutoDialog(w, w.state)
    dlg2.keep_existing = False       # 두 클립 모두 이미 custom → 재적용
    seen = set()
    dlg2._go("each")
    assert pump(app, 600, lambda: (seen.add(dlg2._phase), dlg2.outcome == "each")[1]), \
        f"보정+편집이 끝나지 않음: {dlg2.status.text()}"
    assert "analyze" in seen, seen
    for s in w.state.project.sources:
        a = w.state.analysis(s)
        assert a is not None and a["state"] == "done", "분석 안 끝남"
        assert w.state.has_track(s), "트랙 미리 계산 안 됨"
    print("auto->analyze->track OK", sorted(x for x in seen if x))

    # 샘플 창: 실제 PNG 6장
    import copy
    srcs = [copy.deepcopy(w.state.project.sources[1])]
    sd = SampleDialog(w, w.state, srcs, str(work), "MOSCO")
    sd.opts["count"] = 6
    sd.dir.setText(str(work))
    # _go 는 백그라운드 → 완료 신호에서 accept. 파일 확인
    before = set(os.listdir(work))
    sd._go()
    assert pump(app, 120, lambda: len(set(os.listdir(work)) - before) >= 6), "샘플 PNG 가 안 생김"
    pngs = sorted(f for f in os.listdir(work) if f.endswith(".png"))
    print("sample OK:", pngs[:3], "...", len(pngs), "장")

    # 시작 창 흐름: [AUTO] → (파일 고름) → AUTO 창만 → 샘플 설정 창 → 다시 AUTO 창 → 보정+렌더 → 끝나면 종료
    from PySide6.QtCore import QTimer
    from mosco.ui_start import StartWindow
    app.setQuitOnLastWindowClosed(False)
    w.hide()
    sw = StartWindow(w)
    sw.exit_app = lambda: log.__setitem__("exit", True)        # 테스트에선 실제 종료 대신 기록
    sw.show()
    log = {}
    vid2 = str(work / "synth_copy.mov")
    import shutil
    shutil.copy(vid, vid2)                  # 목록에 없는 새 파일 = 고른 파일만 AUTO 에 떠야 함

    def drive_render():
        d = QApplication.activeModalWidget()
        if not isinstance(d, AutoDialog) or not d._all_detected():
            QTimer.singleShot(100, drive_render)
            return
        log["clips"] = [c["name"] for c in d.clips]
        log["keep"] = (d.keep_existing, d.keep_keys)
        d.grab().save(str(work / "auto_dialog.png"))
        before = set(os.listdir(work))

        def drive_sample():
            sd = QApplication.activeModalWidget()
            if not isinstance(sd, SampleDialog):
                QTimer.singleShot(100, drive_sample)
                return
            sd.opts["count"] = 6
            sd._go()
        QTimer.singleShot(100, drive_sample)
        d._go("sample")                      # (측정 없음=뷰티) 샘플 창 exec → 끝나면 AUTO 창으로 돌아옴
        log["sample"] = len(set(os.listdir(work)) - before)
        log["open_after_sample"] = d.isVisible()
        d._go("render")
    QTimer.singleShot(100, drive_render)
    sw.run_auto([vid2])
    assert log.get("clips") == ["synth_copy"], log
    assert log["keep"] == (False, False), log           # 기본 = 기존 보정 무시 · 키프레임 자동
    assert log["sample"] >= 6 and log["open_after_sample"], log
    outs = [f for f in os.listdir(work) if f.startswith("synth_copy") and f.endswith("_MOSCO.mov")]
    assert outs, "보정+렌더 출력 없음"
    assert log.get("exit"), "보정+렌더가 끝나면 종료해야 함"
    print("start->auto->sample->render->exit OK", outs)

    def drive_each():
        d = QApplication.activeModalWidget()
        if not isinstance(d, AutoDialog) or not d._all_detected():
            QTimer.singleShot(100, drive_each)
            return
        d._go("each")
    QTimer.singleShot(100, drive_each)
    sw.run_auto([vid2])
    assert w.isVisible() and not sw.isVisible(), "보정+편집 → 본 편집 화면"
    s2 = next(s for s in w.state.project.sources if s.path == vid2)
    assert w.state.has_track(s2), "보정+편집 뒤 편집 화면에 트랙이 준비돼 있어야 함"
    print("start->auto->each OK")

    w.state.dirty = False
    print("AUTO TEST OK")
    sys.stdout.flush()
    sys.stderr.flush()
    # 깨끗이 정리: 진행 중 작업 취소 → ffmpeg 종료 → 스레드풀 비우기
    w.state.cancel_all()
    from mosco import ff, jobs
    ff.kill_all()
    for pool in (jobs.PREVIEW_POOL, jobs.RENDER_POOL, jobs.ANALYZE_POOL, jobs.PROBE_POOL):
        pool.clear()
        pool.waitForDone(2000)
    app.processEvents()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except BaseException:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)      # 스레드 조인 대기 없이 0 으로 종료
