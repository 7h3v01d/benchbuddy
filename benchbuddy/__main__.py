import sys

from PyQt6.QtWidgets import QApplication

from benchbuddy.gui.theme import apply_theme
from benchbuddy.gui.main_window import MainWindow  # absolute: also runs frozen (PyInstaller)


def main() -> int:
    if "--self-test" in sys.argv:
        from benchbuddy.selftest import run
        k = sys.argv.index("--self-test")
        report = sys.argv[k + 1] if k + 1 < len(sys.argv) and not sys.argv[k + 1].startswith("-") else None
        return run(report)
    if sys.platform == "win32":
        # own taskbar group + icon instead of python.exe's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BenchBuddy.BenchBuddy")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("BenchBuddy")
    apply_theme(app)
    from benchbuddy.gui.main_window import APP_ICON
    from PyQt6.QtGui import QIcon
    app.setWindowIcon(QIcon(str(APP_ICON)))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
