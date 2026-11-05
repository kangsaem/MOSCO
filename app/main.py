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
    app = QApplication(sys.argv)
    app.setApplicationName("MOSCO")
    dark(app)
    w = MainWindow()
    w.show()
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
    args = [a for a in sys.argv[1:] if Path(a).suffix.lower() in VIDEO_EXT]
    if args:
        w.state.add_files(args)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
