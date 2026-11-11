"""v0.7.58 윈도우 전용: 다른 프로그램(메모장)이 앞에 있을 때 MOSCO 창이 맨 앞으로 오는지.
예전 방식(v0.7.57: 형식 지정 없는 SetWindowPos)과 새 방식(ui_widgets.bring_front)을 비교해 출력."""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mosco.ui_widgets import _user32, bring_front  # noqa: E402

u = _user32()
u.FindWindowW.restype = wt.HWND
u.GetWindow.argtypes = [wt.HWND, wt.UINT]
u.GetWindow.restype = wt.HWND
u.GetTopWindow.argtypes = [wt.HWND]
u.GetTopWindow.restype = wt.HWND


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.02)


def above(a, b):
    """z 순서에서 a 가 b 보다 위인가."""
    h = u.GetTopWindow(None)
    while h:
        if h == a:
            return True
        if h == b:
            return False
        h = u.GetWindow(h, 2)            # GW_HWNDNEXT
    return False


def notepad_front():
    p = subprocess.Popen(["notepad.exe"])
    for _ in range(100):
        h = u.FindWindowW("Notepad", None)
        if h and u.GetForegroundWindow() == h:
            return p, h
        if h:
            u.SetForegroundWindow(h)
        time.sleep(0.1)
    return p, u.FindWindowW("Notepad", None)


def old_way(w):
    w.raise_()
    w.activateWindow()
    uu = ctypes.windll.user32
    hwnd = int(w.winId())
    flags = 0x0001 | 0x0002 | 0x0040
    uu.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
    uu.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)
    uu.SetForegroundWindow(hwnd)


def trial(app, name, fn):
    p, np_h = notepad_front()
    w = QWidget()
    w.setWindowTitle(f"front test {name}")
    w.resize(300, 200)
    w.show()
    pump(app, 0.5)
    np_fg = u.GetForegroundWindow() == np_h
    fn(w)
    pump(app, 1.6)
    me = int(w.winId())
    fg = u.GetForegroundWindow() == me
    top = above(me, np_h) if np_h else None
    print(f"{name}: 메모장이 앞={np_fg}  → 포커스={fg}  메모장보다 위={top}")
    w.close()
    p.kill()
    pump(app, 0.3)
    return fg or bool(top)


def main():
    app = QApplication(sys.argv)
    old = trial(app, "예전(v0.7.57)", old_way)
    new = trial(app, "새(v0.7.58)", bring_front)
    print(f"FRONT {'OK' if new else 'FAIL'} (예전 {old})")
    sys.exit(0 if new else 1)


if __name__ == "__main__":
    main()
