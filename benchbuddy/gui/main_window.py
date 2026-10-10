"""Main window: tabs, menus and status bar."""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from .. import __version__
from ..core.partsdb import PartsDB
from .calcs_tab import CalcsTab
from .capacitor_tab import CapacitorTab
from .parts_tab import PartsTab
from .pinout_tab import PinoutTab
from .power_tab import PowerTab
from .resistor_tab import ResistorTab
from .sim_tab import SimTab
from .theme import apply_theme  # noqa: F401  (re-exported for __main__)
from .tools_tab import BatteryTab, ToolsTab


class MainWindow(QMainWindow):
    def __init__(self, db: PartsDB | None = None, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("BenchBuddy", "BenchBuddy")
        self.setWindowTitle(f"BenchBuddy {__version__} · electronics bench companion")
        self.resize(1280, 800)
        self.db = db or PartsDB()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.power_tab = PowerTab()
        self.tabs.addTab(self.power_tab, "POWER BUDGET")
        self.sim_tab = SimTab(self.power_tab)
        self.tabs.addTab(self.sim_tab, "BROWN-OUT SIM")
        self.tabs.addTab(ResistorTab(), "RESISTORS")
        self.tabs.addTab(CapacitorTab(), "CAPACITORS")
        self.tabs.addTab(CalcsTab(), "DESIGN CALCS")
        self.tabs.addTab(ToolsTab(), "WIRING && GPIO")
        self.pinout_tab = PinoutTab()
        self.tabs.addTab(self.pinout_tab, "PINOUT")
        self.tabs.addTab(BatteryTab(), "BATTERY && SLEEP")
        self.parts_tab = PartsTab(self.db)
        self.tabs.addTab(self.parts_tab, "PARTS LOOKUP")
        self.setCentralWidget(self.tabs)

        view = self.menuBar().addMenu("&View")
        for i in range(self.tabs.count()):
            words = self.tabs.tabText(i).replace("&&", "&").split()
            name = " ".join(w if w in ("GPIO",) else w.capitalize() for w in words)
            act = QAction(name.replace("&", "&&"), self)
            if i < 9:
                act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            act.triggered.connect(lambda _=False, idx=i: self.tabs.setCurrentIndex(idx))
            view.addAction(act)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)
        self.statusBar().showMessage(
            "Tip: Ctrl+1…9 jumps between tabs. Type what's printed on a part into Parts lookup to identify it.", 8000)
        self._restore_state()

    # ------------------------------------------------------------ settings
    def _restore_state(self) -> None:
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        try:
            idx = int(self.settings.value("window/tab", 0))
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)
        board = self.settings.value("pinout/board")
        if board and self.pinout_tab.board_combo.findText(board) >= 0:
            self.pinout_tab.board_combo.setCurrentText(board)

    def _save_state(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/tab", self.tabs.currentIndex())
        self.settings.setValue("pinout/board", self.pinout_tab.board_combo.currentText())
        self.settings.sync()

    def about(self) -> None:
        QMessageBox.information(
            self, "About BenchBuddy",
            f"BenchBuddy {__version__}\n\nPower budgets, resistor/capacitor helpers and a parts library "
            "for Arduino / ESP32 projects.\n\nAll preset values are typical figures: check the datasheet "
            "or measure before trusting a design.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_state()
        self.db.close()
        super().closeEvent(event)
