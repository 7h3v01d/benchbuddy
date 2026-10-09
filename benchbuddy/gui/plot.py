"""Small dependency-free two-panel plot for the brown-out simulator."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..core.brownout import SimResult
from . import theme

C_VOUT, C_VIN, C_LOAD, C_IIN = theme.TEAL, theme.MUTED, theme.RED, theme.AMBER
C_THRESH = theme.RED


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.ceil(lo / step) * step
    out, v = [], start
    while v <= hi + step * 1e-9:
        out.append(round(v, 12))
        v += step
    return out


def fmt_tick(v: float, step: float) -> str:
    decimals = max(0, -int(math.floor(math.log10(step))) + (1 if step < 1 else 0)) if step else 0
    return f"{v:.{min(decimals, 4)}f}"


class PlotWidget(QWidget):
    LEFT, RIGHT, TOP, BOTTOM, GAP = 64, 16, 14, 40, 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: SimResult | None = None
        self._hover: float | None = None       # time in seconds
        self.setMinimumSize(520, 380)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_result(self, res: SimResult | None) -> None:
        self.result = res
        self.update()

    # ------------------------------------------------------------------ events
    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self.result and self.result.t:
            x0, x1 = self.LEFT, self.width() - self.RIGHT
            frac = (e.position().x() - x0) / max(x1 - x0, 1)
            self._hover = min(max(frac, 0), 1) * self.result.t[-1]
            self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = None
        self.update()

    # ----------------------------------------------------------------- drawing
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        p.fillRect(self.rect(), QColor(theme.OBSIDIAN))
        text = QColor(theme.MUTED)
        grid = QColor(theme.BORDER)
        if not self.result or len(self.result.t) < 2:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Press Simulate to see the rail voltage")
            p.end()
            return
        r = self.result
        w, h = self.width(), self.height()
        panel_h = (h - self.TOP - self.BOTTOM - self.GAP) * 0.62
        top_rect = QRectF(self.LEFT, self.TOP, w - self.LEFT - self.RIGHT, panel_h)
        bot_rect = QRectF(self.LEFT, self.TOP + panel_h + self.GAP, w - self.LEFT - self.RIGHT,
                          h - self.TOP - self.BOTTOM - self.GAP - panel_h)
        tmax = r.t[-1]
        unit, scale = ("ms", 1e3) if tmax >= 1e-3 else ("µs", 1e6)

        vmax = max(max(r.v_in), max(r.v_out), r.params.v_threshold) * 1.05
        vmin = max(min(min(r.v_out), r.params.v_threshold) - 0.3, 0.0)
        self._panel(p, top_rect, tmax, scale, vmin, vmax, "Volts", text, grid, bottom_axis=False,
                    series=[(r.t, r.v_in, C_VIN, 1.2), (r.t, r.v_out, C_VOUT, 2.0)],
                    hlines=[(r.params.v_threshold, C_THRESH)])
        imax = max(max(r.i_load), max(r.i_in)) * 1000 * 1.1
        self._panel(p, bot_rect, tmax, scale, 0, max(imax, 1), "mA", text, grid, bottom_axis=True,
                    series=[(r.t, [i * 1000 for i in r.i_in], C_IIN, 1.2),
                            (r.t, [i * 1000 for i in r.i_load], C_LOAD, 1.8)], hlines=[], xunit=unit)

        # legend
        f = QFont(self.font())
        f.setPointSizeF(max(f.pointSizeF() - 1, 7))
        p.setFont(f)
        self._legend_bg(p, top_rect, ("Vout", "Vin (regulator input)", "Brown-out threshold"))
        self._legend_bg(p, bot_rect, ("Load current", "Current from source"))
        x = top_rect.left() + 8
        for label, col in (("Vout", C_VOUT), ("Vin (regulator input)", C_VIN), (f"Brown-out threshold", C_THRESH)):
            p.setPen(QPen(QColor(col), 3))
            p.drawLine(QPointF(x, top_rect.top() + 12), QPointF(x + 16, top_rect.top() + 12))
            p.setPen(text)
            p.drawText(QPointF(x + 20, top_rect.top() + 16), label)
            x += 26 + p.fontMetrics().horizontalAdvance(label) + 12
        x = bot_rect.left() + 8
        for label, col in (("Load current", C_LOAD), ("Current from source", C_IIN)):
            p.setPen(QPen(QColor(col), 3))
            p.drawLine(QPointF(x, bot_rect.top() + 12), QPointF(x + 16, bot_rect.top() + 12))
            p.setPen(text)
            p.drawText(QPointF(x + 20, bot_rect.top() + 16), label)
            x += 26 + p.fontMetrics().horizontalAdvance(label) + 12

        # hover read-out
        if self._hover is not None:
            idx = min(range(len(r.t)), key=lambda i: abs(r.t[i] - self._hover))
            xx = top_rect.left() + r.t[idx] / tmax * top_rect.width()
            p.setPen(QPen(QColor(theme.TEAL), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(xx, top_rect.top()), QPointF(xx, bot_rect.bottom()))
            msg = (f"t = {r.t[idx] * scale:.3g} {unit}   Vout = {r.v_out[idx]:.3f} V   Vin = {r.v_in[idx]:.3f} V   "
                   f"load = {r.i_load[idx] * 1000:.0f} mA")
            p.setPen(QColor(theme.TEXT_HI))
            p.drawText(QPointF(top_rect.left() + 8, top_rect.bottom() - 8), msg)
        p.end()

    @staticmethod
    def _legend_bg(p: QPainter, rect: QRectF, labels) -> None:
        """Opaque strip behind the legend so traces never run through the labels."""
        fm = p.fontMetrics()
        width = sum(26 + fm.horizontalAdvance(lbl) + 12 for lbl in labels)
        bg = QColor(theme.PANEL)
        bg.setAlpha(235)
        p.fillRect(QRectF(rect.left() + 1, rect.top() + 1, width, 22), bg)

    def _panel(self, p: QPainter, rect: QRectF, tmax: float, scale: float, ymin: float, ymax: float,
               ylabel: str, text: QColor, grid: QColor, bottom_axis: bool, series, hlines, xunit: str = "") -> None:
        p.setPen(QPen(grid, 1))
        yticks = nice_ticks(ymin, ymax)
        ystep = (yticks[1] - yticks[0]) if len(yticks) > 1 else 1
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 1, 7))
        p.setFont(small)

        def X(t: float) -> float:
            return rect.left() + t / tmax * rect.width()

        def Y(v: float) -> float:
            return rect.bottom() - (v - ymin) / (ymax - ymin) * rect.height()

        for yt in yticks:
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(rect.left(), Y(yt)), QPointF(rect.right(), Y(yt)))
            p.setPen(text)
            p.drawText(QRectF(rect.left() - 58, Y(yt) - 8, 52, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_tick(yt, ystep))
        xticks = nice_ticks(0, tmax * scale)
        xstep = (xticks[1] - xticks[0]) if len(xticks) > 1 else 1
        for xt in xticks:
            xx = X(xt / scale)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(xx, rect.top()), QPointF(xx, rect.bottom()))
            if bottom_axis:
                p.setPen(text)
                p.drawText(QRectF(xx - 30, rect.bottom() + 3, 60, 16), Qt.AlignmentFlag.AlignCenter,
                           fmt_tick(xt, xstep))
        if bottom_axis:
            p.setPen(text)
            p.drawText(QRectF(rect.left(), rect.bottom() + 18, rect.width(), 18), Qt.AlignmentFlag.AlignCenter,
                       f"time ({xunit})")
        p.save()
        p.translate(14, rect.center().y())
        p.rotate(-90)
        p.setPen(text)
        p.drawText(QRectF(-40, -10, 80, 20), Qt.AlignmentFlag.AlignCenter, ylabel)
        p.restore()
        p.setPen(QPen(QColor(theme.BORDER_HI), 1))
        p.drawRect(rect)
        p.setClipRect(rect)
        for yv, col in hlines:
            if ymin <= yv <= ymax:
                p.setPen(QPen(QColor(col), 1.4, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(rect.left(), Y(yv)), QPointF(rect.right(), Y(yv)))
        for xs, ys, col, width in series:
            path = QPainterPath()
            for i, (t, v) in enumerate(zip(xs, ys)):
                pt = QPointF(X(t), Y(v))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(QColor(col), width))
            p.drawPath(path)
        p.setClipping(False)
