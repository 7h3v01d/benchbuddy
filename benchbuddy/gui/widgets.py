"""Small shared widgets and helpers."""

from __future__ import annotations

import functools
import html

from PyQt6.QtCore import QRectF, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QPainter, QPen
from PyQt6.QtWidgets import (QFormLayout, QGroupBox, QLineEdit, QSizePolicy,
                             QTextBrowser, QVBoxLayout, QWidget)

from ..core.resistors import COLOR_HEX
from ..core.units import parse_value
from . import theme

STATUS_COLORS = theme.STATUS
STATUS_ICONS = {"ok": "✔", "warn": "⚠", "error": "✖"}


class ValueEdit(QLineEdit):
    """Line edit that understands engineering notation (4k7, 100n, 2.2M ...)."""

    def __init__(self, placeholder: str = "", text: str = "", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        if text:
            self.setText(text)
        self.textChanged.connect(self._validate)

    def _validate(self) -> None:
        ok = self.value() is not None or not self.text().strip()
        if self.property("invalid") != (not ok):
            self.setProperty("invalid", not ok)
            theme.repolish(self)

    def value(self) -> float | None:
        t = self.text().strip()
        if not t:
            return None
        try:
            return parse_value(t)
        except ValueError:
            return None


class ResultView(QTextBrowser):
    """Read-only rich-text result box that auto-sizes to a sensible minimum."""

    def __init__(self, min_height: int = 90, parent=None):
        super().__init__(parent)
        # Links are handled here, not by Qt: only plain web links ever leave the app, so text that
        # came from an imported file can't launch file:, custom-protocol or other handlers.
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(open_web_link)
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.document().setDefaultStyleSheet(theme.DOC_CSS)

    def show_html(self, html: str) -> None:
        self.setHtml(f"<div>{html}</div>")

    def show_error(self, text: str) -> None:
        """Plain text in, always escaped: error messages often quote names the user typed."""
        self.show_html(f"<span style='color:{STATUS_COLORS['error']}'>{STATUS_ICONS['error']} "
                       f"{html.escape(str(text))}</span>")


def open_web_link(url: QUrl) -> bool:
    """Open http(s) links in the browser; refuse every other scheme. Returns whether it opened."""
    if url.scheme().lower() in ("http", "https") and url.host():
        return QDesktopServices.openUrl(url)
    return False


def guarded(view_attr: str):
    """Decorate a no-argument recalc slot: any domain or arithmetic failure is shown in the
    page's result box instead of escaping into Qt (where it would be lost or crash the app)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *_signal_args):
            try:
                return fn(self)
            except ValueError as exc:            # DomainError & friends: a clear reason
                getattr(self, view_attr).show_error(str(exc))
            except (ArithmeticError, IndexError):
                getattr(self, view_attr).show_error(
                    "Those values are outside what this calculator can handle (too large, too small or zero).")
        return wrapper
    return deco


def status_html(level: str, text: str) -> str:
    return (f"<p style='margin:3px 0'><span style='color:{STATUS_COLORS[level]};font-weight:bold'>"
            f"{STATUS_ICONS[level]}</span> {text}</p>")


def group(title: str, layout) -> QGroupBox:
    box = QGroupBox(title.upper())
    box.setLayout(layout)
    return box


def form(*rows) -> QFormLayout:
    f = QFormLayout()
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    for label, widget in rows:
        f.addRow(label, widget)
    return f


def wrap(*widgets) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    for x in widgets:
        lay.addWidget(x)
    return w


class ResistorWidget(QWidget):
    """Draws a resistor with colour bands."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bands: list[str] = []
        self.setMinimumSize(280, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_bands(self, bands: list[str]) -> None:
        self._bands = list(bands)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cy = h / 2
        lead_pen = QPen(QColor("#8a959c"), 3)
        p.setPen(lead_pen)
        p.drawLine(int(w * 0.04), int(cy), int(w * 0.96), int(cy))
        body = QRectF(w * 0.18, cy - 26, w * 0.64, 52)
        p.setPen(QPen(QColor("#8d6e3f"), 1.5))
        p.setBrush(QBrush(QColor("#e0c590")))
        p.drawRoundedRect(body, 18, 18)
        n = len(self._bands)
        if n:
            # leave a gap before the last (tolerance) band, like a real resistor
            slots = n + 1
            for i, name in enumerate(self._bands):
                pos = i if i < n - 1 else i + 0.8
                x = body.left() + body.width() * (0.14 + 0.72 * pos / max(slots - 1, 1))
                col = QColor(COLOR_HEX.get(name, "#cccccc"))
                p.setPen(QPen(QColor(0, 0, 0, 90), 0.8))
                p.setBrush(QBrush(col))
                p.drawRect(QRectF(x - 5, body.top() + 2, 10, body.height() - 4))
        p.end()
