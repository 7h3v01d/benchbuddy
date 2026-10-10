"""Power-tree diagram: supplies → regulators → loads, drawn left to right.

Rails sit in columns by depth; every load lines up in the last column so the
consumers read as one list. Each rail box shows its peak current against its
rating with a utilisation bar, and its border/edge colour carries the status.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..core import power
from . import theme

RAIL_W, RAIL_H = 158, 60
LOAD_W, LOAD_H = 172, 40
COL_GAP, ROW_GAP, MARGIN = 64, 8, 12
MIN_SCALE = 0.85   # below this the text gets too small; scroll instead


@dataclass
class Node:
    key: str                     # "rail:<name>" or "load:<index>"
    title: str
    lines: list[str]
    status: str = "ok"
    is_rail: bool = True
    util: float | None = None    # peak utilisation 0..1+ for the bar
    tip: str = ""
    children: list["Node"] = field(default_factory=list)
    flow_ma: float = 0.0         # current entering this node from its parent (peak)
    rect: QRectF = field(default_factory=QRectF)


def _fmt_ma(v: float) -> str:
    if v >= 1000:
        return f"{v / 1000:.2f} A"
    if v >= 10:
        return f"{v:.0f} mA"
    if v >= 1:
        return f"{v:.1f} mA"
    if v >= 0.001:
        return f"{v * 1000:.0f} µA"
    return "0"


def build_nodes(project: power.Project, report: power.PowerReport) -> list[Node]:
    """Turn a project + report into root nodes (one per supply)."""
    reports = {rr.rail.name: rr for rr in report.rails}
    nodes: dict[str, Node] = {}
    for r in project.rails:
        rr = reports.get(r.name)
        if rr is None:
            continue
        kind = "supply" if r.kind == "supply" else r.kind.upper()
        lines = [f"{kind} · {r.v_out:g} V",
                 f"pk {_fmt_ma(rr.peak_ma)} / {_fmt_ma(r.max_ma)}"]
        tip = "\n".join(f"{theme_icon(lv)} {txt}" for lv, txt in rr.messages) or "No issues."
        nodes[r.name] = Node(key=f"rail:{r.name}", title=r.name, lines=lines, status=rr.status,
                             util=(rr.util_peak_pct / 100.0), tip=f"{r.name}\n{tip}",
                             flow_ma=rr.in_peak_ma if r.parent else rr.peak_ma)
    roots: list[Node] = []
    for r in project.rails:
        if r.name not in nodes:
            continue
        if r.parent and r.parent in nodes:
            nodes[r.parent].children.append(nodes[r.name])
        else:
            roots.append(nodes[r.name])
    for i, l in enumerate(project.loads):
        parent = nodes.get(l.rail)
        if parent is None:
            continue
        qty = f" ×{l.qty}" if l.qty > 1 else ""
        parent.children.append(Node(
            key=f"load:{i}", title=f"{l.name}{qty}", is_rail=False,
            lines=[f"avg {_fmt_ma(l.avg_ma)} / pk {_fmt_ma(l.peak_ma)}"],
            tip=f"{l.name}{qty}\nactive {l.i_active_ma:g} mA, peak {l.i_peak_ma:g} mA, "
                f"sleep {l.i_sleep_ma:g} mA, duty {l.duty * 100:g} %",
            flow_ma=l.peak_ma))
    # rails first, then loads, inside each parent
    for n in nodes.values():
        n.children.sort(key=lambda c: not c.is_rail)
    return roots


def render_tree_image(project: power.Project, report: power.PowerReport, scale: float = 2.0) -> QImage:
    """Draw the power tree off-screen at full size (for reports)."""
    view = PowerTreeView()
    view.set_data(project, report)
    size = view.sizeHint()
    view.resize(size)
    img = QImage(int(size.width() * scale), int(size.height() * scale), QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(scale)
    img.fill(QColor(theme.OBSIDIAN))
    p = QPainter(img)
    view.render(p)
    p.end()
    return img


def image_png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def theme_icon(level: str) -> str:
    return {"ok": "✔", "warn": "⚠", "error": "✖"}[level]


class PowerTreeView(QWidget):
    """Custom-painted power tree. Click a rail to select it in the tables."""

    railClicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.roots: list[Node] = []
        self._all: list[Node] = []
        self._content = QSize(0, 0)
        self._hover: Node | None = None
        self._message = "Add a supply to see the power tree."
        self.setMouseTracking(True)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ----------------------------------------------------------------- data
    def set_data(self, project: power.Project, report: power.PowerReport) -> None:
        self.roots = build_nodes(project, report)
        self._message = "" if self.roots else "Add a supply to see the power tree."
        self._layout()
        self.update()

    def show_message(self, text: str) -> None:
        self.roots, self._all, self._message = [], [], text
        self._content = QSize(0, 0)
        self.setMinimumSize(0, 160)
        self.update()

    def node_keys(self) -> list[str]:
        return [n.key for n in self._all]

    # --------------------------------------------------------------- layout
    def _layout(self) -> None:
        self._all = []

        def rail_depth(n: Node, d: int = 0) -> int:
            return max([d] + [rail_depth(c, d + 1) for c in n.children if c.is_rail])

        max_depth = max((rail_depth(r) for r in self.roots), default=0)
        load_col = max_depth + 1
        cursor = [float(MARGIN)]

        def place(n: Node, depth: int) -> float:
            """Lay out subtree; return centre y of n."""
            col = depth if n.is_rail else load_col
            x = MARGIN + col * (RAIL_W + COL_GAP)
            w, h = (RAIL_W, RAIL_H) if n.is_rail else (LOAD_W, LOAD_H)
            if n.children:
                ys = [place(c, depth + 1) for c in n.children]
                cy = (ys[0] + ys[-1]) / 2
            else:
                # every leaf takes a full rail-height slot, so a parent centred on a
                # single child can never overlap its neighbour in the same column
                cy = cursor[0] + RAIL_H / 2
                cursor[0] += RAIL_H + ROW_GAP
            n.rect = QRectF(x, cy - h / 2, w, h)
            self._all.append(n)
            return cy

        for r in self.roots:
            place(r, 0)
            cursor[0] += ROW_GAP * 2          # breathing room between separate supplies
        width = MARGIN * 2 + load_col * (RAIL_W + COL_GAP) + LOAD_W
        height = int(cursor[0] + MARGIN)
        self._content = QSize(int(width), max(height, 0))
        self.setMinimumSize(int(width * MIN_SCALE), int(height * MIN_SCALE))
        self.updateGeometry()

    def _scale(self) -> float:
        if self._content.width() <= 0 or self._content.height() <= 0:
            return 1.0
        fit = min(self.width() / self._content.width(), self.height() / self._content.height())
        return max(MIN_SCALE, min(1.0, fit))

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._content if self._content.width() else QSize(400, 200)

    # ---------------------------------------------------------------- input
    def _node_at(self, pos: QPointF) -> Node | None:
        s = self._scale()
        p = QPointF(pos.x() / s, pos.y() / s)
        return next((n for n in self._all if n.rect.contains(p)), None)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        n = self._node_at(e.position())
        if n is not self._hover:
            self._hover = n
            self.setCursor(Qt.CursorShape.PointingHandCursor if n and n.is_rail else Qt.CursorShape.ArrowCursor)
            self.update()
        if n:
            QToolTip.showText(e.globalPosition().toPoint(), n.tip, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        n = self._node_at(e.position())
        if n and n.is_rail:
            self.railClicked.emit(n.title)

    # -------------------------------------------------------------- drawing
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.OBSIDIAN))
        if not self.roots:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            p.end()
            return
        s = self._scale()
        p.scale(s, s)
        base = QFont(self.font())
        small = QFont(base)
        small.setPointSizeF(max(base.pointSizeF() - 1, 7))
        bold = QFont(base)
        bold.setWeight(QFont.Weight.DemiBold)

        peak_max = max((n.flow_ma for n in self._all), default=1) or 1
        for n in self._all:
            for c in n.children:
                self._edge(p, n, c, peak_max, small)
        for n in self._all:
            self._box(p, n, bold, small)
        p.end()

    def _edge(self, p: QPainter, a: Node, b: Node, peak_max: float, font: QFont) -> None:
        col = QColor(theme.STATUS[b.status]) if b.is_rail else QColor(theme.BORDER_HI)
        width = 1.2 + 2.8 * min(b.flow_ma / peak_max, 1.0)
        x0, y0 = a.rect.right(), a.rect.center().y()
        x1, y1 = b.rect.left(), b.rect.center().y()
        xm = x0 + 12
        path = QPainterPath(QPointF(x0, y0))
        path.lineTo(xm, y0)
        path.lineTo(xm, y1)
        path.lineTo(x1, y1)
        p.setPen(QPen(col, width, cap=Qt.PenCapStyle.FlatCap, join=Qt.PenJoinStyle.MiterJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if not b.is_rail:
            return        # load boxes already show their own current
        # current drawn by the child regulator, on the run into it
        p.setFont(font)
        p.setPen(QColor(theme.MUTED))
        label = _fmt_ma(b.flow_ma)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(label)
        if x1 - xm - 6 >= tw:
            p.drawText(QPointF(x1 - tw - 4, y1 - 4), label)

    def _box(self, p: QPainter, n: Node, bold: QFont, small: QFont) -> None:
        r = n.rect
        hovered = n is self._hover
        status_col = QColor(theme.STATUS[n.status])
        p.setBrush(QColor(theme.PANEL if n.is_rail else theme.OBSIDIAN))
        edge = QColor(theme.TEAL) if hovered else (status_col if n.is_rail and n.status != "ok"
                                                   else QColor(theme.BORDER_HI))
        p.setPen(QPen(edge, 1))
        p.drawRect(r)
        if n.is_rail:   # status stripe
            p.fillRect(QRectF(r.left(), r.top(), 3, r.height()), status_col)
        tx = r.left() + (10 if n.is_rail else 8)
        tw = r.width() - (tx - r.left()) - 6
        p.setFont(bold)
        p.setPen(QColor(theme.TEXT_HI if n.is_rail else theme.TEXT))
        fm = QFontMetrics(bold)
        p.drawText(QPointF(tx, r.top() + 15), fm.elidedText(n.title, Qt.TextElideMode.ElideRight, int(tw)))
        p.setFont(small)
        fs = QFontMetrics(small)
        p.setPen(QColor(theme.MUTED))
        for i, line in enumerate(n.lines):
            p.drawText(QPointF(tx, r.top() + 30 + i * 14), fs.elidedText(line, Qt.TextElideMode.ElideRight, int(tw)))
        if n.is_rail and n.util is not None:
            bar = QRectF(tx, r.bottom() - 7, tw, 3)
            p.fillRect(bar, QColor(theme.BORDER))
            fill = QRectF(bar.left(), bar.top(), bar.width() * min(n.util, 1.0), bar.height())
            p.fillRect(fill, status_col)
            if n.util > 1.0:   # overload tick at the end
                p.fillRect(QRectF(bar.right() - 3, bar.top() - 2, 3, bar.height() + 4), QColor(theme.RED))
