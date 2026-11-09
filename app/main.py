"""MOSCO 실행 진입점. 영상 파일을 끌어다 놓거나 인자로 주면 바로 목록에 추가."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosco.ui_main import MainWindow  # noqa: E402
from mosco.ui_side import VIDEO_EXT  # noqa: E402


def dark(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    c = {QPalette.Window: "#232323", QPalette.WindowText: "#e6e6e6", QPalette.Base: "#1a1a1a",
         QPalette.AlternateBase: "#262626", QPalette.Text: "#e6e6e6", QPalette.Button: "#2e2e2e",
         QPalette.ButtonText: "#e6e6e6", QPalette.Highlight: "#4a6fa5", QPalette.HighlightedText: "#ffffff",
         QPalette.ToolTipBase: "#333", QPalette.ToolTipText: "#eee", QPalette.PlaceholderText: "#777"}
    for role, col in c.items():
        p.setColor(role, QColor(col))
    app.setPalette(p)


def main():
    from mosco import ff
    ff.low_priority()                          # 다른 프로그램(크롬·에펙)에 CPU 양보 — 놀 때는 그대로 다 씀
    app = QApplication(sys.argv)
    app.setApplicationName("MOSCO")
    dark(app)
    w = MainWindow()
    from mosco import models
    models.set_gpu(w.state.settings.render.get("gpu", False))
    args = [a for a in sys.argv[1:] if Path(a).suffix.lower() in VIDEO_EXT]
    if os.environ.get("MOSCO_SELFTEST") or args:
        w.showMaximized()                      # 빌드 점검·파일을 끌어다 실행 = 바로 편집 화면(전체 창)
    else:
        # 시작 창 [AUTO]/[편집]. 창 사이를 오가므로 '마지막 창 닫힘 = 종료'는 끄고 직접 종료
        from mosco.ui_start import StartWindow
        app.setQuitOnLastWindowClosed(False)
        start = StartWindow(w)
        start.center()
        start.show()
    if os.environ.get("MOSCO_SELFTEST"):       # 빌드 점검: 모델 로드 + 창 띄우고 2초 뒤 종료
        from PySide6.QtCore import QTimer
        from mosco import models
        ok = True
        try:
            models.detector(); models.mesh()
        except Exception as e:  # noqa: BLE001
            print("model load failed:", e)
            ok = False
        QTimer.singleShot(2000, lambda: app.exit(0 if ok else 3))
    if args:
        w.state.add_files(args)
    code = app.exec()
    # 종료 시 프로세스를 확실히 끝냄: 남은 ffmpeg·상주 디코더 정리 후 os._exit.
    # (데몬/네이티브 스레드·onnxruntime GPU 컨텍스트가 남아 프로세스가 안 죽고 GPU·화면이 계속 점유되던 것 방지.
    #  PyAV/Qt teardown 경쟁으로 종료 때 세그폴트 나던 것도 함께 우회 — 테스트의 os._exit 과 같은 이유.)
    try:
        from mosco import decoder, ff
        decoder.close_all()
        ff.kill_all()
    except Exception:  # noqa: BLE001
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code if isinstance(code, int) else 0)


if __name__ == "__main__":
    main()
