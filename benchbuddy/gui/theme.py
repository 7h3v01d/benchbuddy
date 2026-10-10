"""Dark industrial theme: colour tokens, bundled font and the app-wide stylesheet.

Every colour the GUI uses comes from this module, so the look can be tuned in one place.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

# ------------------------------------------------------------------ tokens
OBSIDIAN = "#0b0f14"   # window / deepest background
PANEL = "#11161d"      # group boxes, headers, buttons
RAISED = "#161d26"     # hover / alternate rows
BORDER = "#1e2831"     # all hairlines
BORDER_HI = "#2c3a46"  # stronger edge (focus-adjacent, scrollbar handles)
TEAL = "#2fd6c3"       # primary accent
PHOSPHOR = "#4be08a"   # ok
AMBER = "#ffb454"      # warn
RED = "#ff6b6b"        # error
TEXT = "#c8d3da"
TEXT_HI = "#e8eef2"    # emphasis / headings
MUTED = "#6f808c"      # secondary text, notes
DISABLED = "#3d4a55"

STATUS = {"ok": PHOSPHOR, "warn": AMBER, "error": RED}

FONT_DIR = Path(__file__).with_name("fonts")
ICON_DIR = Path(__file__).with_name("icons")
FONT_FAMILIES = ("JetBrains Mono NL", "JetBrains Mono", "Cascadia Mono", "Consolas", "monospace")
FONT_PT = 9.0


def tint(hex_color: str, alpha: int) -> str:
    """CSS rgba() string for a token at the given alpha (0-255)."""
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def load_fonts() -> str:
    """Register the bundled JetBrains Mono files and return the family to use."""
    if FONT_DIR.is_dir():
        for ttf in sorted(FONT_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(ttf))
    available = set(QFontDatabase.families())
    return next((f for f in FONT_FAMILIES if f in available), "monospace")


# Rich-text defaults for ResultView / QTextBrowser documents.
DOC_CSS = f"""
body, div, p, li, td {{ color: {TEXT}; }}
b, strong {{ color: {TEXT_HI}; font-weight: 600; }}
h2 {{ color: {TEAL}; font-weight: 600; }}
h3, h4 {{ color: {TEXT_HI}; font-weight: 600; }}
th {{ color: {MUTED}; font-weight: 600; text-align: left; }}
a {{ color: {TEAL}; }}
pre, code {{ color: {AMBER}; }}
.muted {{ color: {MUTED}; font-weight: normal; }}
"""


def _icon(name: str) -> str:
    return (ICON_DIR / f"{name}.svg").as_posix()


def stylesheet(family: str) -> str:
    sel = tint(TEAL, 40)
    return f"""
* {{
    font-family: "{family}";
    font-size: {FONT_PT}pt;
    outline: 0;
}}
QWidget {{
    color: {TEXT};
    selection-background-color: {sel};
    selection-color: {TEXT_HI};
}}
QToolTip {{
    background: {PANEL}; color: {TEXT}; border: 1px solid {TEAL}; padding: 4px 6px;
}}

/* ---------- menu + status bar ---------- */
QMenuBar {{ background: {OBSIDIAN}; border-bottom: 1px solid {BORDER}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; color: {MUTED}; }}
QMenuBar::item:selected {{ background: {PANEL}; color: {TEAL}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER_HI}; padding: 4px 0; }}
QMenu::item {{ padding: 5px 22px 5px 14px; }}
QMenu::item:selected {{ background: {sel}; color: {TEAL}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 0; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ background: transparent; color: {MUTED}; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; top: -1px; }}
QTabBar {{ background: {OBSIDIAN}; }}
QTabBar::tab {{
    background: {OBSIDIAN}; color: {MUTED};
    border: none; border-bottom: 2px solid transparent;
    padding: 7px 13px; margin-right: 1px;
    font-weight: 600; letter-spacing: 1px;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: {PANEL}; }}
QTabBar::tab:selected {{ color: {TEAL}; background: {PANEL}; border-bottom: 2px solid {TEAL}; }}
QTabBar::scroller {{ width: 22px; }}

/* ---------- group boxes ---------- */
QGroupBox {{
    background: {PANEL}; border: 1px solid {BORDER};
    margin-top: 14px; padding: 8px 6px 6px 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 0px; padding: 0 0 2px 0;
    color: {TEAL}; background: transparent;
    font-weight: 600; letter-spacing: 1px;
}}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QCheckBox, QRadioButton {{ background: transparent; spacing: 6px; }}

/* ---------- buttons ---------- */
QPushButton, QToolButton {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER_HI}; border-radius: 0;
    padding: 4px 12px; min-height: 18px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {TEAL}; color: {TEAL}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {TEAL}; color: {OBSIDIAN}; border-color: {TEAL}; }}
QPushButton:checked {{ background: {sel}; color: {TEAL}; border-color: {TEAL}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {DISABLED}; border-color: {BORDER}; background: {PANEL}; }}
QPushButton:default {{ border-color: {TEAL}; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {OBSIDIAN}; color: {TEXT_HI};
    border: 1px solid {BORDER_HI}; border-radius: 0;
    padding: 4px 6px; min-height: 18px;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {MUTED}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {TEAL}; }}
QLineEdit[invalid="true"] {{ border-color: {RED}; background: {tint(RED, 20)}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {DISABLED}; border-color: {BORDER}; }}
QLineEdit[readOnly="true"] {{ background: {PANEL}; color: {TEXT}; }}

QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: none; border-left: 1px solid {BORDER_HI}; width: 20px; }}
QComboBox::down-arrow {{ image: url({_icon("arrow-down-muted")}); width: 10px; height: 9px; }}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{ image: url({_icon("arrow-down-teal")}); }}
QComboBox::down-arrow:disabled {{ image: url({_icon("arrow-down-dis")}); }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {TEAL};
    selection-background-color: {sel}; selection-color: {TEAL}; padding: 0;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {PANEL}; border: none; border-left: 1px solid {BORDER_HI}; width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {sel}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_icon("arrow-up-muted")}); width: 8px; height: 7px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_icon("arrow-down-muted")}); width: 8px; height: 7px; }}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{ image: url({_icon("arrow-up-teal")}); }}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{ image: url({_icon("arrow-down-teal")}); }}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 12px; height: 12px; background: {OBSIDIAN}; border: 1px solid {BORDER_HI}; border-radius: 0;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {TEAL}; }}
QCheckBox::indicator:checked {{ background: {TEAL}; border-color: {TEAL}; image: url({_icon("check")}); }}
QRadioButton::indicator:checked {{ background: {TEAL}; border-color: {TEAL}; }}

/* ---------- tables / lists / rich text ---------- */
QTableView, QTableWidget, QListView, QListWidget, QTreeView, QTreeWidget {{
    background: {OBSIDIAN}; alternate-background-color: {PANEL};
    border: 1px solid {BORDER}; gridline-color: {BORDER};
}}
QTableView::item, QListView::item {{ padding: 2px 4px; }}
QTableView::item:selected, QListView::item:selected, QTreeView::item:selected {{
    background: {sel}; color: {TEXT_HI};
}}
QTableView QComboBox, QTableWidget QComboBox {{ border: none; background: transparent; padding: 1px 16px 1px 4px; min-height: 0; }}
QTableView QComboBox::drop-down {{ border: none; width: 14px; }}
QTableView QLineEdit {{ border: 1px solid {TEAL}; padding: 0 4px; }}
QHeaderView {{ background: {PANEL}; }}
QHeaderView::section {{
    background: {PANEL}; color: {MUTED};
    border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER_HI};
    padding: 5px 6px; font-weight: 600;
}}
QHeaderView::section:hover {{ color: {TEXT}; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER_HI}; }}
QTextBrowser {{ background: {OBSIDIAN}; border: 1px solid {BORDER}; padding: 4px; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: {OBSIDIAN}; width: 10px; margin: 0; border: none; }}
QScrollBar:horizontal {{ background: {OBSIDIAN}; height: 10px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; min-height: 28px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {TEAL}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: {OBSIDIAN}; border: none; }}

/* ---------- splitters / misc ---------- */
QSplitter::handle {{ background: {OBSIDIAN}; }}
QSplitter::handle:hover {{ background: {TEAL}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QScrollArea {{ border: none; }}
QProgressBar {{ background: {OBSIDIAN}; border: 1px solid {BORDER_HI}; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {TEAL}; }}
QSlider::groove:horizontal {{ background: {BORDER_HI}; height: 3px; }}
QSlider::handle:horizontal {{ background: {TEAL}; width: 10px; margin: -6px 0; }}
QMainWindow, QDialog, QMessageBox {{ background: {OBSIDIAN}; }}
QMessageBox QLabel {{ color: {TEXT}; }}

/* ---------- header strip ---------- */
QWidget#header {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QLabel#wordmark {{ color: {TEAL}; font-weight: 700; letter-spacing: 3px; }}
QPushButton#headerStatus {{
    background: transparent; border: 1px solid {BORDER_HI}; padding: 3px 10px;
    font-weight: 600; letter-spacing: 1px; min-height: 0;
}}
QPushButton#headerStatus[level="ok"]    {{ color: {PHOSPHOR}; border-color: {tint(PHOSPHOR, 120)}; }}
QPushButton#headerStatus[level="warn"]  {{ color: {AMBER};    border-color: {tint(AMBER, 140)}; }}
QPushButton#headerStatus[level="error"] {{ color: {RED};      border-color: {tint(RED, 160)}; background: {tint(RED, 22)}; }}
QPushButton#headerStatus:hover {{ border-color: {TEAL}; }}

/* ---------- status banner (power tab) ---------- */
QLabel#banner {{ font-weight: 700; padding: 4px 12px; letter-spacing: 1px; border: 1px solid {BORDER_HI}; }}
QLabel#banner[level="ok"]    {{ color: {PHOSPHOR}; border-color: {PHOSPHOR}; background: {tint(PHOSPHOR, 22)}; }}
QLabel#banner[level="warn"]  {{ color: {AMBER};    border-color: {AMBER};    background: {tint(AMBER, 22)}; }}
QLabel#banner[level="error"] {{ color: {RED};      border-color: {RED};      background: {tint(RED, 26)}; }}
"""


def palette() -> QPalette:
    """Palette for anything QSS doesn't reach (custom-painted widgets, item delegates)."""
    p = QPalette()
    R = QPalette.ColorRole
    for role, col in (
        (R.Window, OBSIDIAN), (R.WindowText, TEXT), (R.Base, OBSIDIAN), (R.AlternateBase, PANEL),
        (R.Text, TEXT), (R.BrightText, TEXT_HI), (R.Button, RAISED), (R.ButtonText, TEXT),
        (R.ToolTipBase, PANEL), (R.ToolTipText, TEXT), (R.Highlight, TEAL), (R.HighlightedText, OBSIDIAN),
        (R.PlaceholderText, MUTED), (R.Link, TEAL), (R.Mid, BORDER), (R.Dark, BORDER), (R.Light, BORDER_HI),
        (R.Midlight, BORDER_HI), (R.Shadow, OBSIDIAN),
    ):
        p.setColor(role, QColor(col))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    return p


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    family = load_fonts()
    f = QFont(family)
    f.setPointSizeF(FONT_PT)
    f.setStyleHint(QFont.StyleHint.Monospace)
    app.setFont(f)
    app.setPalette(palette())
    app.setStyleSheet(stylesheet(family))


def repolish(widget) -> None:
    """Re-apply QSS after a dynamic property change."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
