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
    assert len(dlg.clips) == 2 and dlg.width() == 1040
    assert pump(app, 90, lambda: dlg._all_detected()), "측정이 끝나지 않음"
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

    # AUTO(개별 보정) → 적용 뒤 분석(트래킹)이 시작돼야 타원이 생긴다
    w.state.settings.render["out_dir"] = str(work)
    dlg2 = AutoDialog(w, w.state)
    assert pump(app, 90, dlg2._all_detected), "측정이 끝나지 않음"
    dlg2.keep_existing = False       # 두 클립 모두 이미 custom → 재적용해야 분석이 돈다
    dlg2._go("each")                 # 보정만(렌더 없이 적용+분석)
    assert pump(app, 30, lambda: w.state.analysis(w.state.project.sources[0]) is not None), \
        "AUTO 개별 보정인데 분석이 시작되지 않음"
    print("auto->analyze OK")

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
