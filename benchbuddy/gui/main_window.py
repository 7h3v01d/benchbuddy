"""Main window: tabs, menus and status bar."""

from __future__ import annotations

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from .. import __version__
from ..core.partsdb import PartsDB
from .calcs_tab import CalcsTab
from .capacitor_tab import CapacitorTab
from .parts_tab import PartsTab
from .power_tab import PowerTab
from .resistor_tab import ResistorTab
from .sim_tab import SimTab
from .theme import apply_theme  # noqa: F401  (re-exported for __main__)
from .tools_tab import BatteryTab, ToolsTab


class MainWindow(QMainWindow):
    def __init__(self, db: PartsDB | None = None):
        super().__init__()
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
        self.tabs.addTab(BatteryTab(), "BATTERY && SLEEP")
        self.parts_tab = PartsTab(self.db)
        self.tabs.addTab(self.parts_tab, "PARTS LOOKUP")
        self.setCentralWidget(self.tabs)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)
        self.statusBar().showMessage(
            "Tip: type what's printed on a part into the Parts lookup tab to identify it.", 8000)

    def about(self) -> None:
        QMessageBox.information(
            self, "About BenchBuddy",
            f"BenchBuddy {__version__}\n\nPower budgets, resistor/capacitor helpers and a parts library "
            "for Arduino / ESP32 projects.\n\nAll preset values are typical figures: check the datasheet "
            "or measure before trusting a design.")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.db.close()
        super().closeEvent(event)
