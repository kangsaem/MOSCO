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
    # v0.7.26: 우선순위 '보통'(예전 '보통 이하'는 다른 작업 중일 때 양보가 과해 진행이 멈춤).
    # 프로세스는 기본(보통)으로 둔다 — 낮추지 않음.
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
        if not os.environ.get("MOSCO_SELFTEST"):
            from PySide6.QtCore import QTimer

            def _recover():                    # 지난번에 튕겨 저장 못 한 작업 → 복구하면 바로 편집 화면
                if w.check_autosave(start):
                    start.edit()
            QTimer.singleShot(200, _recover)
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
    _hard_exit(code if isinstance(code, int) else 0)


def _hard_exit(code):
    """종료 시 프로세스를 확실히·즉시 끝낸다. 남은 ffmpeg·상주 디코더를 먼저 정리.
    (데몬/네이티브 스레드·onnxruntime·ffmpeg(GPU 포함)가 남아 프로세스가 안 죽고 GPU·화면을 계속 점유하던 것 방지.)
    윈도우에서 os._exit 은 무거운 네이티브 DLL(onnxruntime·ffmpeg·Qt) 해제 중 로더 락 교착을 일으킬 수 있어
    TerminateProcess 로 DLL 해제를 건너뛰고 즉시 종료한다(PyAV/Qt teardown 세그폴트도 함께 우회)."""
    try:
        from mosco import decoder, ff
        decoder.close_all()
        ff.kill_all()
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            # 프로토타입을 지정해야 64비트에서 현재 프로세스 의사 핸들(-1)이 잘리지 않는다.
            # (안 하면 핸들이 32비트로 잘려 TerminateProcess 가 실패 → os._exit 로 떨어져
            #  네이티브 DLL 로더락 교착으로 프로세스가 안 죽고 폴더를 계속 잠그는 원인이 됨)
            k.GetCurrentProcess.restype = wintypes.HANDLE
            k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.TerminateProcess.restype = wintypes.BOOL
            k.TerminateProcess(k.GetCurrentProcess(), code & 0xFFFFFFFF)
        except Exception:  # noqa: BLE001
            pass
    os._exit(code)


if __name__ == "__main__":
    main()
