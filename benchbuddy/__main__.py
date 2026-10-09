import sys

from PyQt6.QtWidgets import QApplication

from .gui.main_window import MainWindow, apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
