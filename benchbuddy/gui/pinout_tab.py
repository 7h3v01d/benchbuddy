"""Pinout tab: per-board pin map with gotchas and a "find me a pin" filter."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ..core import pinout
from . import theme
from .widgets import ResultView, group

SEV_COLOR = {"ok": theme.PHOSPHOR, "caution": theme.AMBER, "avoid": theme.RED}
CAP_LABELS = {pinout.IN: "in", pinout.OUT: "out", pinout.PWM: "PWM", pinout.ADC: "ADC",
              pinout.ADC_WIFI: "ADC+WiFi", pinout.TOUCH: "touch", pinout.DAC: "DAC",
              pinout.WAKE: "wake", pinout.INT: "IRQ"}

TILE_W, TILE_H, GAP = 132, 58, 6


class PinGrid(QWidget):
    """Flowing grid of pin tiles. Matching pins are lit, the rest are dimmed."""

    pinSelected = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pins: list[pinout.Pin] = []
        self.lit: set[int] = set()
        self.selected: int | None = None
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_pins(self, pins: list[pinout.Pin], lit: set[int]) -> None:
        self.pins, self.lit = pins, lit
        if self.selected is not None and self.selected >= len(pins):
            self.selected = None
        self.updateGeometry()
        self.update()

    # layout -------------------------------------------------------------
    def _cols(self) -> int:
        return max(1, (self.width() + GAP) // (TILE_W + GAP))

    def _rect(self, i: int) -> QRectF:
        c = self._cols()
        return QRectF((i % c) * (TILE_W + GAP), (i // c) * (TILE_H + GAP), TILE_W, TILE_H)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        c = max(1, (w + GAP) // (TILE_W + GAP))
        rows = (len(self.pins) + c - 1) // c
        return max(rows * (TILE_H + GAP), TILE_H)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(4 * (TILE_W + GAP), self.heightForWidth(4 * (TILE_W + GAP)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_W, TILE_H)

    def resizeEvent(self, e) -> None:  # noqa: N802
        self.setMinimumHeight(self.heightForWidth(self.width()))
        super().resizeEvent(e)

    # input ---------------------------------------------------------------
    def _index_at(self, pos) -> int | None:
        for i in range(len(self.pins)):
            if self._rect(i).contains(pos):
                return i
        return None

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        i = self._index_at(e.position())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        i = self._index_at(e.position())
        if i is not None:
            self.select(i)

    def select(self, i: int) -> None:
        self.selected = i
        self.update()
        self.pinSelected.emit(self.pins[i])

    # paint ---------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1.5, 7))
        fb, fs = QFontMetrics(bold), QFontMetrics(small)
        for i, pin in enumerate(self.pins):
            r = self._rect(i)
            lit = i in self.lit
            sev = QColor(SEV_COLOR[pin.severity])
            text_hi = QColor(theme.TEXT_HI if lit else theme.DISABLED)
            text_lo = QColor(theme.MUTED if lit else theme.DISABLED)
            if not lit:
                sev.setAlpha(70)
            p.setBrush(QColor(theme.PANEL if lit else theme.OBSIDIAN))
            if i == self.selected:
                edge = QColor(theme.TEAL)
            elif i == self._hover:
                edge = QColor(theme.MUTED)
            else:
                edge = QColor(theme.BORDER_HI if lit else theme.BORDER)
            p.setPen(QPen(edge, 2 if i == self.selected else 1))
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
            p.fillRect(QRectF(r.left() + 1, r.top() + 1, 3, r.height() - 2), sev)
            tx, tw = r.left() + 10, r.width() - 16
            p.setFont(bold)
            p.setPen(text_hi)
            p.drawText(QRectF(tx, r.top() + 4, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                       fb.elidedText(pin.label, Qt.TextElideMode.ElideRight, int(tw)))
            p.setFont(small)
            p.setPen(text_lo)
            funcs = " · ".join(pin.funcs) if pin.funcs else ("—" if not pin.caps else "GPIO")
            p.drawText(QRectF(tx, r.top() + 21, tw, 15), Qt.AlignmentFlag.AlignVCenter,
                       fs.elidedText(funcs, Qt.TextElideMode.ElideRight, int(tw)))
            chips = [pinout.FLAGS[f][1] for f in sorted(pin.flags, key=lambda f: -pinout.SEVERITY_ORDER[pinout.FLAGS[f][0]])]
            p.setPen(QColor(SEV_COLOR[pin.severity]) if lit and chips else text_lo)
            line = " / ".join(chips) if chips else "no gotchas"
            p.drawText(QRectF(tx, r.top() + 37, tw, 15), Qt.AlignmentFlag.AlignVCenter,
                       fs.elidedText(line, Qt.TextElideMode.ElideRight, int(tw)))
        p.end()


class PinoutTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.board_combo = QComboBox()
        self.board_combo.addItems(pinout.BOARDS)
        self.board_combo.currentTextChanged.connect(self.on_board)
        self.hide_caution = QCheckBox("Only pins with no gotchas")
        self.hide_caution.toggled.connect(self.refresh)
        self.match_label = QLabel()
        self.match_label.setProperty("role", "muted")

        top = QHBoxLayout()
        top.addWidget(QLabel("Board:"))
        top.addWidget(self.board_combo, 1)
        top.addWidget(self.hide_caution)
        top.addStretch(1)
        top.addWidget(self.match_label)

        # requirement checkboxes, laid out in a grid; hidden when the board can't do it
        self.req_boxes: dict[str, QCheckBox] = {}
        req_grid = QGridLayout()
        req_grid.setHorizontalSpacing(18)
        for i, (key, (label, _)) in enumerate(pinout.REQUIREMENTS.items()):
            cb = QCheckBox(label)
            cb.toggled.connect(self.refresh)
            self.req_boxes[key] = cb
            req_grid.addWidget(cb, i // 5, i % 5)

        self.grid = PinGrid()
        self.grid.pinSelected.connect(self.show_pin)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        legend = QHBoxLayout()
        for sev, text in (("ok", "free to use"), ("caution", "usable, read the note"), ("avoid", "don't use")):
            sw = QLabel("■")
            sw.setStyleSheet(f"color:{SEV_COLOR[sev]};")
            legend.addWidget(sw)
            lbl = QLabel(text)
            lbl.setProperty("role", "muted")
            legend.addWidget(lbl)
            legend.addSpacing(14)
        legend.addStretch(1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(group("Find a pin that can do", req_grid))
        pins_lay = QVBoxLayout()
        pins_lay.addWidget(scroll, 1)
        pins_lay.addLayout(legend)
        ll.addWidget(group("Pins", pins_lay), 1)

        self.detail = ResultView(160)
        self.board_notes = ResultView(120)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(group("Selected pin", self._wrap(self.detail)), 3)
        rl.addWidget(group("Board notes", self._wrap(self.board_notes)), 2)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([780, 480])

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        self.on_board(self.board_combo.currentText())

    @staticmethod
    def _wrap(w: QWidget) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.addWidget(w)
        return lay

    @property
    def board(self) -> pinout.Board:
        return pinout.BOARDS[self.board_combo.currentText()]

    def on_board(self, _name: str) -> None:
        b = self.board
        for key, cb in self.req_boxes.items():
            possible = any(pinout.REQUIREMENTS[key][1](p) for p in b.pins)
            cb.setVisible(possible)
            if not possible:
                cb.setChecked(False)
        notes = "".join(f"<li>{n}</li>" for n in b.notes)
        self.board_notes.show_html(f"<p><b>{b.chip}</b> · <span class='muted'>{b.max_ma}</span></p><ul>{notes}</ul>")
        self.grid.selected = None
        self.refresh()
        self.detail.show_html("<span class='muted'>Click a pin to see what it does and what to watch out for.</span>")

    def requirements(self) -> list[str]:
        return [k for k, cb in self.req_boxes.items() if cb.isChecked() and not cb.isHidden()]

    def refresh(self) -> None:
        b = self.board
        reqs = self.requirements()
        ok_only = self.hide_caution.isChecked()
        lit = set()
        for i, p in enumerate(b.pins):
            if not p.matches(reqs):
                continue
            if ok_only and p.severity != "ok":
                continue
            if reqs and p.severity == "avoid":
                continue
            lit.add(i)
        self.grid.set_pins(b.pins, lit)
        if reqs or ok_only:
            best = [p.label for p in pinout.suggest(b, reqs, include_caution=not ok_only)]
            self.match_label.setText(f"{len(lit)} of {len(b.pins)} pins match"
                                     + (f" · best: {', '.join(best[:4])}" if best else ""))
        else:
            self.match_label.setText(f"{len(b.pins)} pins")

    def show_pin(self, pin: pinout.Pin) -> None:
        sev_col = SEV_COLOR[pin.severity]
        head = {"ok": "Free to use", "caution": "Usable, with care", "avoid": "Don't use"}[pin.severity]
        code = f"<p>In code: <b>{pin.code_name}</b></p>" if pin.gpio is not None else ""
        caps = ", ".join(CAP_LABELS[c] for c in sorted(pin.caps, key=list(CAP_LABELS).index)) or "none"
        funcs = ", ".join(pin.funcs) or "plain GPIO"
        flags = "".join(
            f"<p><b style='color:{SEV_COLOR[pinout.FLAGS[f][0]]}'>{pinout.FLAGS[f][1]}</b>: {pinout.FLAGS[f][2]}</p>"
            for f in sorted(pin.flags, key=lambda f: -pinout.SEVERITY_ORDER[pinout.FLAGS[f][0]]))
        note = f"<p>{pin.note}</p>" if pin.note else ""
        self.detail.show_html(
            f"<h2 style='margin:0'>{pin.label}</h2>"
            f"<p style='color:{sev_col}'><b style='color:{sev_col}'>{head}</b></p>{code}"
            f"<p><span class='muted'>Functions</span><br>{funcs}</p>"
            f"<p><span class='muted'>Can do</span><br>{caps}</p>{flags}{note}")

    def select_label(self, label: str) -> None:
        for i, p in enumerate(self.board.pins):
            if p.label == label:
                self.grid.select(i)
                return
        raise KeyError(label)
