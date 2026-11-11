"""v0.7.69 렌더 진행 표시(DTME 와 같은 방식): [렌더] 버튼 = 전체 진행 막대('1/2 · 40%') · 창 제목 · 끝나면 원래 버튼.
작업 표시줄 막대(taskbar.py)는 Windows 에서만(여기서는 오류 없이 지나가는지만).
사용: python tests/test_renderprog.py <영상> <작업폴더>"""
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosco.ui_main import MainWindow  # noqa: E402


def main():
    vid, work = sys.argv[1], Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    clips = []
    for k in range(2):
        c = work / f"rp_{k}.mov"
        if not c.exists():
            shutil.copy(vid, c)
        clips.append(str(c))
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(1536, 864)
    w.show()

    def wait(sec, cond=None):
        t = time.time()
        while time.time() - t < sec:
            app.processEvents()
            time.sleep(0.02)
            if cond and cond():
                return True
        return False
    st = w.state
    st.add_files(clips)
    assert wait(30, lambda: all(st.infos.get(c) is not None for c in clips))
    out = work / "out"
    out.mkdir(exist_ok=True)
    st.settings.render.update({"out_dir": str(out), "overlay": False, "review": False})
    w.sources.selectAll()
    w.queue_to_render()
    wait(0.3)
    rp = w.rpanel
    assert len(rp.items) == 2 and rp.b_render.frac is None and rp.b_render.text() == "렌더"
    seen = []
    w.render_all()
    assert rp.b_render.frac is not None, "렌더를 눌렀는데 버튼이 막대로 안 바뀜"

    def watch():
        if rp.b_render.frac is not None:
            seen.append((rp.b_render.frac, rp.b_render.ptext, w.windowTitle()))
        return any(0.1 < f < 1.0 for f, _t, _w in seen)
    assert wait(300, watch), "진행 막대가 안 움직임"
    f, text, title = next(x for x in seen if 0.1 < x[0] < 1.0)
    print("렌더 중 버튼:", text, "· 제목:", title)
    assert "/2 · " in text and "렌더 " in title
    assert wait(600, lambda: all(it.state == "완료" for it in rp.items)), [it.state for it in rp.items]
    wait(0.5)
    fr = [x[0] for x in seen]
    assert all(b >= a - 1e-6 for a, b in zip(fr, fr[1:])), "진행이 뒤로 감"
    print("끝: 버튼", rp.b_render.text(), rp.b_render.frac, "· 제목:", w.windowTitle())
    assert rp.b_render.frac is None and "렌더 " not in w.windowTitle()
    assert len(list(out.glob("rp_*.mov"))) == 2
    st.dirty = False
    print("RENDERPROG OK")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
