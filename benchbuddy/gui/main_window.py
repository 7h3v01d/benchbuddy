"""Main window: tabs, menus and status bar."""

from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from .. import __version__
from ..core.partsdb import PartsDB
from .calcs_tab import CalcsTab
from .capacitor_tab import CapacitorTab
from .measure_tab import MeasureTab
from .parts_tab import PartsTab
from .pinout_tab import PinoutTab
from .power_tab import PowerTab
from .resistor_tab import ResistorTab
from .sim_tab import SimTab
from . import theme
from .theme import apply_theme  # noqa: F401  (re-exported for __main__)
from .tools_tab import BatteryTab, ToolsTab


APP_ICON = theme.ICON_DIR / "app.svg"


class HeaderBar(QWidget):
    """Slim strip above the tabs: wordmark on the left, live power-budget verdict on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("header")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        mark = QLabel("BENCHBUDDY")
        mark.setObjectName("wordmark")
        ver = QLabel(f"v{__version__}")
        ver.setProperty("role", "muted")
        lay.addWidget(mark)
        lay.addWidget(ver)
        lay.addStretch(1)
        self.status = QPushButton()
        self.status.setObjectName("headerStatus")
        self.status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status.setToolTip("Power budget verdict: click to open the Power budget tab")
        lay.addWidget(self.status)

    def set_status(self, level: str, text: str) -> None:
        icon = {"ok": "✔", "warn": "⚠", "error": "✖"}.get(level, "")
        self.status.setText(f"POWER  {icon} {text}")
        self.status.setProperty("level", level)
        theme.repolish(self.status)


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
        self.measure_tab = MeasureTab(self.power_tab, self.sim_tab)
        self.tabs.addTab(self.measure_tab, "MEASURE")
        self.tabs.addTab(ResistorTab(), "RESISTORS")
        self.tabs.addTab(CapacitorTab(), "CAPACITORS")
        self.tabs.addTab(CalcsTab(), "DESIGN CALCS")
        self.tabs.addTab(ToolsTab(), "WIRING && GPIO")
        self.pinout_tab = PinoutTab()
        self.tabs.addTab(self.pinout_tab, "PINOUT")
        self.tabs.addTab(BatteryTab(), "BATTERY && SLEEP")
        self.parts_tab = PartsTab(self.db)
        self.tabs.addTab(self.parts_tab, "PARTS LOOKUP")
        self.header = HeaderBar()
        self.header.status.clicked.connect(lambda: self.tabs.setCurrentWidget(self.power_tab))
        self.power_tab.statusChanged.connect(self.header.set_status)
        self.power_tab.recalc()                     # replay the current verdict into the header
        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.header)
        col.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))

        view = self.menuBar().addMenu("&View")
        for i in range(self.tabs.count()):
            words = self.tabs.tabText(i).replace("&&", "&").split()
            name = " ".join(w if w in ("GPIO",) else w.capitalize() for w in words)
            act = QAction(name.replace("&", "&&"), self)
            if i < 10:
                act.setShortcut(QKeySequence(f"Ctrl+{(i + 1) % 10}"))      # Ctrl+1 … Ctrl+9, Ctrl+0
            act.triggered.connect(lambda _=False, idx=i: self.tabs.setCurrentIndex(idx))
            view.addAction(act)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)
        self.statusBar().showMessage(
            "Tip: Ctrl+1…0 jumps between tabs. Type what's printed on a part into Parts lookup to identify it.", 8000)
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
        self.measure_tab.shutdown()
        self.db.close()
        super().closeEvent(event)
