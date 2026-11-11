"""v0.7.71 종료 안전장치: 보이는 창이 없는 상태가 이어지면 프로그램이 스스로 끝나는지 · 창을 닫을 때 정리 단계가
오류를 내도 종료까지 가는지. 사용: python tests/test_exit.py"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import main as M  # noqa: E402
from mosco.ui_main import MainWindow  # noqa: E402


def run():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)          # 시작 창 흐름과 같은 조건
    # 1) 창을 닫을 때 정리 단계가 오류를 내도 닫히고 종료 호출까지 감
    w = MainWindow()
    w.quit_on_close = True
    w.show()
    w.state.cancel_all = lambda: (_ for _ in ()).throw(RuntimeError("정리 중 오류"))
    quit_called = []
    app.aboutToQuit.connect(lambda: quit_called.append(1))
    QTimer.singleShot(300, w.close)
    QTimer.singleShot(20000, lambda: app.exit(9))  # 안전장치가 안 듣는 경우의 마지막 탈출
    t0 = time.time()
    code = app.exec()
    assert code != 9 and not w.isVisible(), "창 닫기에서 종료까지 못 감"
    print(f"close with error OK ({time.time() - t0:.1f}s)")
    # 2) 종료 호출이 빠진 채 창만 사라진 경우 → 감시가 끝냄
    x = QWidget()
    x.show()
    M._watch_windows(app)
    QTimer.singleShot(500, x.hide)
    QTimer.singleShot(20000, lambda: app.exit(9))
    t0 = time.time()
    code = app.exec()
    el = time.time() - t0
    assert code != 9 and 4.0 < el < 12.0, (code, el)
    print(f"watchdog OK ({el:.1f}s)")
    # 3) 창이 보이는 동안(최소화 포함)은 끝내지 않음
    y = QWidget()
    y.show()
    y.showMinimized()
    QTimer.singleShot(9000, lambda: app.exit(7))
    assert app.exec() == 7, "창이 있는데 끝냄"
    print("EXIT OK")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    run()
