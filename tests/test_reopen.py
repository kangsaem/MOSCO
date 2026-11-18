"""v0.8.1 프로젝트를 저장 → (새로 켠 것처럼 분석 기억을 지우고) 다시 열면 레이어가 바로 보여야 함.
분석 저장 파일은 바로 읽고(계산 없음), 분석 전에도 추가 레이어는 레인에 보임."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtWidgets import QApplication  # noqa: E402


def main():
    vid, work = sys.argv[1], Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    from mosco.ui_main import MainWindow

    def pump(s, until=None):
        t = time.time()
        while time.time() - t < s:
            app.processEvents()
            time.sleep(0.02)
            if until and until():
                return True
        return False
    w = MainWindow()
    w.show()
    st = w.state
    st.add_files([vid])
    assert pump(30, lambda: st.infos.get(vid) is not None)
    st.set_blur(False, True, active="sign")
    s = st.source()
    st.ensure_analysis(s)
    assert pump(300, lambda: (st.analysis(s, "sign") or {}).get("state") == "done"), "분석 안 끝남"
    st.add_ellipse_box(10, 800, 500, 200, 80)
    pump(0.5)
    before = sorted((x["mode"], x["label"]) for x in st.ellipse_spans(st.source()))
    assert any(lbl == "+" for _, lbl in before), before
    f = work / "reopen.mosco.json"
    st.project.save(str(f))
    st.dirty = False
    # 새로 켠 것처럼: 분석·트랙 기억 지움
    st.an.clear()
    st._tracks.clear()
    st._res.clear()
    st._spans_cache = (None,)
    t0 = time.time()
    w._load(str(f))
    ok = pump(20, lambda: sorted((x["mode"], x["label"]) for x in st.ellipse_spans(st.source())) == before)
    after = sorted((x["mode"], x["label"]) for x in st.ellipse_spans(st.source()))
    assert ok, f"다시 열었더니 레이어가 다름/없음 {before} → {after}"
    print(f"reopen layers OK ({time.time() - t0:.1f}s) {len(after)}")
    # 분석 전에도 추가 레이어는 레인에
    st.an.clear()
    st._tracks.clear()
    st._res.clear()
    st._spans_cache = (None,)
    sp = st.ellipse_spans(st.source())
    assert [x["label"] for x in sp] == ["+"], sp
    print("manual lane before analysis OK")
    print("REOPEN OK")
    os._exit(0)


if __name__ == "__main__":
    main()
