"""Measure tab: live current from an INA219/INA226 meter (or the built-in demo device)."""

from __future__ import annotations

import bisect
import datetime as _dt
import time
from pathlib import Path

from PyQt6.QtCore import QMetaObject, QObject, QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                             QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ..core import meter as mt
from ..core import power
from ..core.units import format_value
from . import theme
from .plot import fmt_tick, nice_ticks
from .widgets import ResultView, ValueEdit, group, status_html

DEMO_PORT = "Demo device (simulated ESP32 + INA226)"
SPANS = {"20 ms": 0.02, "100 ms": 0.1, "500 ms": 0.5, "2 s": 2.0, "10 s": 10.0, "60 s": 60.0, "All": None}

try:                                    # pyserial is optional: without it only the demo device works
    import serial
    from serial.tools import list_ports
except ImportError:                     # pragma: no cover - depends on the environment
    serial = None
    list_ports = None


def available_ports() -> list[tuple[str, str]]:
    """(device, description) for real serial ports, plus the demo device."""
    ports: list[tuple[str, str]] = []
    if list_ports is not None:
        for p in sorted(list_ports.comports(), key=lambda p: p.device):
            ports.append((p.device, f"{p.device} · {p.description}" if p.description else p.device))
    ports.append((DEMO_PORT, DEMO_PORT))
    return ports


# ===================================================================== worker
class MeterWorker(QObject):
    """Lives in its own thread: owns the port, parses frames, hands batches to the GUI."""

    hello = pyqtSignal(dict)
    samples = pyqtSignal(list)          # [(t0_us, [(dt, shunt, bus), ...]), ...]
    log = pyqtSignal(str)
    failed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, port: str, baud: int = mt.DEFAULT_BAUD, chip_hint: str = "INA226", r_shunt: float = 0.1):
        super().__init__()
        self.port_name, self.baud = port, baud
        self.chip_hint, self.r_shunt = chip_hint, r_shunt
        self.port = None
        self.parser = mt.FrameParser()
        self.timer: QTimer | None = None
        self._last = 0.0
        self._hello_seen = False
        self._opened_at = 0.0

    @pyqtSlot()
    def start(self) -> None:
        try:
            if self.port_name == DEMO_PORT:
                self.port = mt.SimulatedMeter(self.chip_hint, self.r_shunt)
            else:
                if serial is None:
                    raise RuntimeError("pyserial isn't installed: pip install pyserial")
                self.port = serial.Serial(self.port_name, self.baud, timeout=0)
        except Exception as exc:  # noqa: BLE001 - surface any open failure in the GUI
            self.failed.emit(f"Couldn't open {self.port_name}: {exc}")
            self.closed.emit()
            return
        self._last = self._opened_at = time.monotonic()
        self.timer = QTimer(self)                      # parented: lives and dies in the worker thread
        self.timer.timeout.connect(self._tick)
        self.timer.start(20)
        self.send("STOP")
        self.send("HELLO")

    @pyqtSlot(str)
    def send(self, cmd: str) -> None:
        if self.port is None:
            return
        try:
            self.port.write((cmd.strip() + "\n").encode("ascii"))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Write failed: {exc}")

    def _tick(self) -> None:
        now = time.monotonic()
        try:
            if isinstance(self.port, mt.SimulatedMeter):
                self.port.advance(now - self._last)
            self._last = now
            n = self.port.in_waiting
            data = self.port.read(n) if n else b""
        except Exception as exc:  # noqa: BLE001 - unplugged cable etc.
            self.failed.emit(f"Lost the connection: {exc}")
            self.stop()
            return
        if not data:
            # the ESP32 reboots when the port opens; ask again once it's had time to boot
            if not self._hello_seen and now - self._opened_at > 1.5:
                self._opened_at = now
                self.send("HELLO")
            return
        batch = []
        for f in self.parser.feed(data):
            if f.ftype == mt.T_SAMPLES:
                try:
                    batch.append(mt.decode_samples(f.payload))
                except ValueError:
                    continue
            elif f.ftype == mt.T_HELLO:
                self._hello_seen = True
                self.hello.emit(mt.parse_hello(f.payload))
            elif f.ftype == mt.T_LOG:
                self.log.emit(f.payload.decode("ascii", "replace"))
        if batch:
            self.samples.emit(batch)

    @pyqtSlot()
    def stop(self) -> None:
        if self.timer:
            self.timer.stop()
        if self.port is not None:
            try:
                self.port.write(b"STOP\n")
                self.port.close()
            except Exception:  # noqa: BLE001 - closing a dead port
                pass
            self.port = None
        self.closed.emit()


# ====================================================================== plot
class TracePlot(QWidget):
    """Current (and optional bus voltage) vs time. Min/max per pixel column keeps spikes visible.

    Drag to select a stretch for stats / export; double-click clears the selection.
    """

    selectionChanged = pyqtSignal()
    LEFT, RIGHT, TOP, BOTTOM = 62, 52, 12, 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cap: mt.Capture | None = None
        self.span: float | None = 2.0
        self.t_end: float | None = None          # None = follow the newest sample
        self.show_v = True
        self.threshold: float | None = None
        self.full_scale: float | None = None
        self.selection: tuple[float, float] | None = None
        self._drag_from: float | None = None
        self._hover_x: float | None = None
        self.setMinimumSize(420, 260)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ----------------------------------------------------------- geometry
    def view(self) -> tuple[float, float]:
        if not self.cap or len(self.cap) < 2:
            return 0.0, self.span or 1.0
        end = self.cap.t[-1] if self.t_end is None else self.t_end
        if self.span is None:
            return self.cap.t[0], max(end, self.cap.t[0] + 1e-6)
        return end - self.span, end

    def _plot_rect(self) -> QRectF:
        return QRectF(self.LEFT, self.TOP, max(self.width() - self.LEFT - self.RIGHT, 10),
                      max(self.height() - self.TOP - self.BOTTOM, 10))

    def _t_at(self, x: float) -> float:
        r = self._plot_rect()
        t0, t1 = self.view()
        return t0 + (min(max(x, r.left()), r.right()) - r.left()) / r.width() * (t1 - t0)

    # -------------------------------------------------------------- input
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._plot_rect().contains(e.position()):
            self._drag_from = self._t_at(e.position().x())

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        self._hover_x = e.position().x()
        if self._drag_from is not None:
            t = self._t_at(e.position().x())
            self.selection = (min(self._drag_from, t), max(self._drag_from, t))
        self.update()

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            if self.selection and self.selection[1] - self.selection[0] <= 0:
                self.selection = None
            self.selectionChanged.emit()

    def mouseDoubleClickEvent(self, _e) -> None:  # noqa: N802
        self.selection = None
        self.selectionChanged.emit()
        self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover_x = None
        self.update()

    # ------------------------------------------------------------ drawing
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.OBSIDIAN))
        r = self._plot_rect()
        muted, grid = QColor(theme.MUTED), QColor(theme.BORDER)
        small = QFont(self.font())
        small.setPointSizeF(max(self.font().pointSizeF() - 1, 7))
        p.setFont(small)
        cap = self.cap
        if not cap or len(cap) < 2:
            p.setPen(muted)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Connect a meter (or the demo device) and press Record")
            p.end()
            return
        t0, t1 = self.view()
        lo = max(bisect.bisect_left(cap.t, t0) - 1, 0)
        hi = min(bisect.bisect_right(cap.t, t1) + 1, len(cap))
        if hi - lo < 1:
            p.end()
            return
        # column envelopes
        cols = int(r.width())
        imin = [None] * cols
        imax = [None] * cols
        vavg = [None] * cols
        tspan = (t1 - t0) or 1e-9
        k = lo
        for c in range(cols):
            ta = t0 + c / cols * tspan
            tb = t0 + (c + 1) / cols * tspan
            a = bisect.bisect_left(cap.t, ta, k, hi)
            b = bisect.bisect_left(cap.t, tb, a, hi)
            if b > a:
                seg = cap.i[a:b]
                imin[c], imax[c] = min(seg), max(seg)
                vavg[c] = cap.v[(a + b) // 2]
            k = a
        vals = [x for x in imax if x is not None] + [x for x in imin if x is not None]
        if not vals:
            p.end()
            return
        ymax = max(max(vals) * 1.1, 0.001)
        ymin = min(min(vals), 0.0)
        if self.full_scale and max(vals) > self.full_scale * 0.7:
            ymax = max(ymax, self.full_scale * 1.05)

        def X(t: float) -> float:
            return r.left() + (t - t0) / tspan * r.width()

        def Y(i: float) -> float:
            return r.bottom() - (i - ymin) / (ymax - ymin) * r.height()

        # grid + axes
        yt = nice_ticks(ymin * 1000, ymax * 1000)
        ystep = yt[1] - yt[0] if len(yt) > 1 else 1
        for v in yt:
            y = Y(v / 1000)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - 8, self.LEFT - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       fmt_tick(v, ystep))
        unit, scale = ("ms", 1e3) if tspan < 2 else ("s", 1.0)
        xt = nice_ticks(t0 * scale, t1 * scale)
        xstep = xt[1] - xt[0] if len(xt) > 1 else 1
        for v in xt:
            x = X(v / scale)
            if r.left() <= x <= r.right():
                p.setPen(QPen(grid, 1))
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.setPen(muted)
                p.drawText(QRectF(x - 32, r.bottom() + 3, 64, 14), Qt.AlignmentFlag.AlignCenter, fmt_tick(v, xstep))
        p.setPen(muted)
        p.drawText(QRectF(r.left(), r.bottom() + 16, r.width(), 16), Qt.AlignmentFlag.AlignCenter, f"time ({unit})")
        p.save()
        p.translate(12, r.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-40, -8, 80, 16), Qt.AlignmentFlag.AlignCenter, "mA")
        p.restore()

        # selection shading
        if self.selection:
            sa, sb = X(self.selection[0]), X(self.selection[1])
            shade = QColor(theme.TEAL)
            shade.setAlpha(28)
            p.fillRect(QRectF(sa, r.top(), sb - sa, r.height()), shade)
            p.setPen(QPen(QColor(theme.TEAL), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(sa, r.top()), QPointF(sa, r.bottom()))
            p.drawLine(QPointF(sb, r.top()), QPointF(sb, r.bottom()))

        p.setClipRect(r)
        # full scale / threshold
        if self.full_scale and self.full_scale <= ymax:
            p.setPen(QPen(QColor(theme.RED), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(r.left(), Y(self.full_scale)), QPointF(r.right(), Y(self.full_scale)))
        if self.threshold is not None and ymin <= self.threshold <= ymax:
            p.setPen(QPen(QColor(theme.AMBER), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(r.left(), Y(self.threshold)), QPointF(r.right(), Y(self.threshold)))
        # bus voltage on its own scale (right axis)
        vv = [x for x in vavg if x is not None]
        if self.show_v and vv and max(vv) > 0:
            vlo, vhi = min(vv), max(vv)
            pad = max((vhi - vlo) * 0.2, 0.02)
            vlo, vhi = vlo - pad, vhi + pad
            path = QPainterPath()
            started = False
            for c, v in enumerate(vavg):
                if v is None:
                    continue
                pt = QPointF(r.left() + c + 0.5, r.bottom() - (v - vlo) / (vhi - vlo) * r.height())
                path.lineTo(pt) if started else path.moveTo(pt)
                started = True
            col = QColor(theme.MUTED)
            col.setAlpha(160)
            p.setPen(QPen(col, 1))
            p.drawPath(path)
            p.setClipping(False)
            p.setPen(muted)
            for frac in (0.0, 0.5, 1.0):
                v = vlo + frac * (vhi - vlo)
                y = r.bottom() - frac * r.height()
                p.drawText(QRectF(r.right() + 4, y - 8, self.RIGHT - 4, 16),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{v:.2f}V")
            p.setClipRect(r)
        # current: through the samples when zoomed in, else a min..max bar per pixel column
        pen = QPen(QColor(theme.TEAL), 1.3)
        p.setPen(pen)
        prev = None
        if hi - lo < cols:
            path = QPainterPath()
            for k in range(lo, hi):
                pt = QPointF(X(cap.t[k]), Y(cap.i[k]))
                if k == lo:
                    path.moveTo(pt)
                else:
                    path.lineTo(QPointF(pt.x(), path.currentPosition().y()))   # sample-and-hold steps
                    path.lineTo(pt)
            p.drawPath(path)
            if (hi - lo) * 6 < cols:                                          # few points: mark them
                p.setBrush(QColor(theme.TEAL))
                for k in range(lo, hi):
                    p.drawEllipse(QPointF(X(cap.t[k]), Y(cap.i[k])), 1.8, 1.8)
                p.setBrush(Qt.BrushStyle.NoBrush)
            cols_iter = range(0)
        else:
            cols_iter = range(cols)
        for c in cols_iter:
            if imin[c] is None:
                prev = None
                continue
            x = r.left() + c + 0.5
            y0, y1 = Y(imin[c]), Y(imax[c])
            if prev is not None:
                p.drawLine(QPointF(x - 1, prev), QPointF(x, (y0 + y1) / 2))
            p.drawLine(QPointF(x, y0), QPointF(x, y1))
            prev = (y0 + y1) / 2
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.BORDER_HI), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        # hover read-out
        if self._hover_x is not None and r.left() <= self._hover_x <= r.right():
            t = self._t_at(self._hover_x)
            idx = min(max(bisect.bisect_left(cap.t, t), 0), len(cap) - 1)
            p.setPen(QPen(QColor(theme.TEXT), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(self._hover_x, r.top()), QPointF(self._hover_x, r.bottom()))
            p.setPen(QColor(theme.TEXT_HI))
            p.drawText(QPointF(r.left() + 8, r.top() + 14),
                       f"t = {cap.t[idx] * scale:.4g} {unit}   I = {cap.i[idx] * 1000:.2f} mA   V = {cap.v[idx]:.3f} V")
        p.end()


# ======================================================================= tab
class MeasureTab(QWidget):
    commandRequested = pyqtSignal(str)          # to the worker thread
    stopRequested = pyqtSignal()

    def __init__(self, power_tab=None, sim_tab=None, parent=None):
        super().__init__(parent)
        self.power_tab, self.sim_tab = power_tab, sim_tab
        self.cap = mt.Capture()
        self.hello: dict = {}
        self.recording = False
        self._thread: QThread | None = None
        self._worker: MeterWorker | None = None
        self._dirty = False

        # ------------------------------------------------------ connection
        self.port_combo = QComboBox()
        self.port_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.port_combo.setMinimumContentsLength(22)
        refresh = QPushButton("↻")
        refresh.setToolTip("Rescan serial ports")
        refresh.setFixedWidth(34)
        refresh.clicked.connect(self.refresh_ports)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["NORMAL", "FAST"])
        self.mode_combo.setToolTip("NORMAL: current + voltage (~1–1.5 kHz). FAST: current only (~6 kHz), "
                                   "for catching Wi-Fi bursts.")
        self.mode_combo.currentTextChanged.connect(lambda m: self.commandRequested.emit(f"MODE {m}"))
        self.shunt = ValueEdit("Ω", "0.1")
        self.shunt.setToolTip("Shunt resistor on your INA board: R100 = 0.1 Ω, R010 = 0.01 Ω")
        self.shunt.setMaximumWidth(90)
        self.shunt.textChanged.connect(self._shunt_changed)
        self.status = QLabel("Not connected")
        self.status.setProperty("role", "muted")
        top = QHBoxLayout()
        for w in (QLabel("Port:"), self.port_combo, refresh, self.connect_btn, QLabel("Mode:"), self.mode_combo,
                  QLabel("Shunt:"), self.shunt):
            top.addWidget(w)
        top.addWidget(self.status, 1)

        # ---------------------------------------------------------- record
        self.rec_btn = QPushButton("● Record")
        self.rec_btn.setCheckable(True)
        self.rec_btn.setEnabled(False)
        self.rec_btn.toggled.connect(self.set_recording)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        self.zero_btn = QPushButton("Zero")
        self.zero_btn.setToolTip("With nothing drawing current, record a moment and press Zero to remove the offset")
        self.zero_btn.clicked.connect(self.zero)
        self.span_combo = QComboBox()
        self.span_combo.addItems(SPANS)
        self.span_combo.setCurrentText("2 s")
        self.span_combo.currentTextChanged.connect(self._span_changed)
        self.follow = QCheckBox("Follow live")
        self.follow.setChecked(True)
        self.follow.toggled.connect(self._follow_changed)
        self.show_v = QCheckBox("Voltage")
        self.show_v.setChecked(True)
        self.show_v.toggled.connect(self._toggle_v)
        open_btn = QPushButton("Open CSV…")
        open_btn.clicked.connect(self.open_csv)
        save_btn = QPushButton("Save CSV…")
        save_btn.clicked.connect(self.save_csv)
        bar = QHBoxLayout()
        for w in (self.rec_btn, clear, self.zero_btn, QLabel("View:"), self.span_combo, self.follow, self.show_v):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(open_btn)
        bar.addWidget(save_btn)

        # ------------------------------------------------------------ body
        self.plot = TracePlot()
        self.plot.cap = self.cap
        self.plot.selectionChanged.connect(self.update_stats)
        hint = QLabel("Drag across the trace to analyse a stretch; double-click to clear. "
                      "Stats cover the selection, or the visible window when nothing is selected.")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        plot_box = QVBoxLayout()
        plot_box.addWidget(self.plot, 1)
        plot_box.addWidget(hint)

        self.stats_view = ResultView(220)
        self.apply_btn = QPushButton("Use as a load in the Power budget…")
        self.apply_btn.clicked.connect(self.apply_to_load)
        self.sim_btn = QPushButton("Replay in the Brown-out sim")
        self.sim_btn.clicked.connect(self.send_to_sim)
        for b in (self.apply_btn, self.sim_btn):
            b.setEnabled(False)
        right = QVBoxLayout()
        right.addWidget(self.stats_view, 1)
        right.addWidget(self.apply_btn)
        right.addWidget(self.sim_btn)

        lw, rw = QWidget(), QWidget()
        lw.setLayout(QVBoxLayout())
        lw.layout().setContentsMargins(0, 0, 0, 0)
        lw.layout().addWidget(group("Trace", plot_box))
        rw.setLayout(QVBoxLayout())
        rw.layout().setContentsMargins(0, 0, 0, 0)
        rw.layout().addWidget(group("Analysis", right))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(lw)
        split.addWidget(rw)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([880, 400])

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(bar)
        lay.addWidget(split, 1)

        self._repaint = QTimer(self)
        self._repaint.setInterval(100)
        self._repaint.timeout.connect(self._refresh_view)
        self._repaint.start()
        self.refresh_ports()
        self.update_stats()

    # ------------------------------------------------------------ helpers
    def _msg(self, text: str, ms: int = 6000) -> None:
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text, ms)

    def refresh_ports(self) -> None:
        cur = self.port_combo.currentData()
        self.port_combo.clear()
        for dev, desc in available_ports():
            self.port_combo.addItem(desc, dev)
        idx = self.port_combo.findData(cur) if cur else -1
        if idx < 0:                    # prefer a USB-serial adapter (CH340 on the D1 R32), else the demo
            usb = ("usb", "ch340", "ch910", "cp210", "ftdi", "uart")
            idx = next((k for k in range(self.port_combo.count())
                        if any(u in self.port_combo.itemText(k).lower() for u in usb)
                        and self.port_combo.itemData(k) != DEMO_PORT),
                       self.port_combo.findData(DEMO_PORT))
        self.port_combo.setCurrentIndex(idx)
        if serial is None:
            self.port_combo.setToolTip("pyserial isn't installed, so only the demo device is listed: pip install pyserial")

    def _r_shunt(self) -> float:
        v = self.shunt.value()
        return v if v and v > 0 else 0.1

    def _chip(self) -> mt.Chip:
        return mt.CHIPS.get(self.cap.chip, mt.CHIPS["INA226"])

    def _shunt_changed(self) -> None:
        if self.shunt.value() and self.shunt.value() > 0 and not len(self.cap):
            self.cap.r_shunt = self._r_shunt()
            self._update_status()

    def _span_changed(self, name: str) -> None:
        self.plot.span = SPANS[name]
        self.plot.update()
        self.update_stats()

    def _follow_changed(self, on: bool) -> None:
        self.plot.t_end = None if on else (self.cap.t[-1] if len(self.cap) else None)
        self.plot.update()

    def _toggle_v(self, on: bool) -> None:
        self.plot.show_v = on
        self.plot.update()

    # --------------------------------------------------------- connection
    @property
    def connected(self) -> bool:
        return self._worker is not None

    def toggle_connection(self) -> None:
        if self.connected:
            self.disconnect_meter()
        else:
            self.connect_meter(self.port_combo.currentData())

    def connect_meter(self, port: str) -> None:
        if self.connected or not port:
            return
        self.cap.r_shunt = self._r_shunt()
        self._thread = QThread(self)
        self._worker = MeterWorker(port, chip_hint="INA226", r_shunt=self.cap.r_shunt)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.deleteLater)
        self._worker.hello.connect(self._on_hello)
        self._worker.samples.connect(self._on_samples)
        self._worker.log.connect(lambda t: self._msg(f"Meter: {t}", 10000))
        self._worker.failed.connect(self._on_failed)
        self._worker.closed.connect(self._on_closed)
        self.commandRequested.connect(self._worker.send)
        self.stopRequested.connect(self._worker.stop)
        self._thread.start()
        self.connect_btn.setText("Disconnect")
        self.status.setText(f"Connecting to {port}…")

    def disconnect_meter(self) -> None:
        if not self.connected:
            return
        self.set_recording(False)
        self.stopRequested.emit()

    def _on_closed(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(2000)
        try:
            self.commandRequested.disconnect()
            self.stopRequested.disconnect()
        except TypeError:
            pass
        self._worker, self._thread = None, None
        self.hello = {}
        self.connect_btn.setText("Connect")
        self.rec_btn.blockSignals(True)
        self.rec_btn.setChecked(False)
        self.rec_btn.setText("● Record")
        self.rec_btn.blockSignals(False)
        self.rec_btn.setEnabled(False)
        self.recording = False
        self._update_status()

    def _on_failed(self, text: str) -> None:
        self.status.setText(text)
        self._msg(text, 10000)

    def _on_hello(self, info: dict) -> None:
        self.hello = info
        chip = info.get("chip", "NONE")
        if chip in mt.CHIPS:
            if len(self.cap) and self.cap.chip != chip:
                self.clear()
            self.cap.chip = chip
            self.rec_btn.setEnabled(True)
        else:
            self.rec_btn.setEnabled(False)
        mode = info.get("mode")
        if mode in ("NORMAL", "FAST") and self.mode_combo.currentText() != mode:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(mode)
            self.mode_combo.blockSignals(False)
        self._update_status()

    def _update_status(self) -> None:
        if not self.connected:
            self.status.setText("Not connected" + (f" · {len(self.cap)} samples loaded" if len(self.cap) else ""))
            return
        chip = self.hello.get("chip")
        if not chip:
            return
        if chip == "NONE":
            self.status.setText("Meter found, but no INA219/INA226 on its I2C bus: check the wiring")
            return
        c = mt.CHIPS[chip]
        r = self._r_shunt()
        period = int(self.hello.get("period_us", "0") or 0)
        rate = f" · ~{1e6 / period:,.0f} Hz" if period else ""
        demo = " · DEMO" if self.hello.get("sim") else ""
        self.status.setText(f"{chip} @ {self.hello.get('addr', '?')} · {self.hello.get('mode', '?')}{rate} · "
                            f"range ±{format_value(c.full_scale_a(r), 'A')} · LSB {format_value(c.lsb_a(r), 'A')}{demo}")

    # ---------------------------------------------------------- recording
    def set_recording(self, on: bool) -> None:
        if on and not self.connected:
            on = False
        self.recording = on
        self.rec_btn.blockSignals(True)
        self.rec_btn.setChecked(on)
        self.rec_btn.setText("■ Stop" if on else "● Record")
        self.rec_btn.blockSignals(False)
        if self.connected:
            self.commandRequested.emit("START" if on else "STOP")
        if on:
            self.cap.r_shunt = self._r_shunt()
            self.follow.setChecked(True)

    def _on_samples(self, batch: list) -> None:
        if not self.recording:
            return
        for t0, samples in batch:
            self.cap.add_frame(t0, samples)
        self._dirty = True

    def _refresh_view(self) -> None:
        if self._dirty:
            self._dirty = False
            self.plot.full_scale = self._chip().full_scale_a(self.cap.r_shunt)
            self.plot.update()
            self.update_stats()

    def clear(self) -> None:
        chip, r, zero = self.cap.chip, self.cap.r_shunt, self.cap.zero_a
        self.cap.clear()
        self.cap.chip, self.cap.r_shunt, self.cap.zero_a = chip, r, zero
        self.plot.selection = None
        self.plot.update()
        self.update_stats()

    def zero(self) -> None:
        lo, hi = self.analysis_range()
        try:
            off = self.cap.zero_from(lo, hi)
        except ValueError as exc:
            QMessageBox.information(self, "Zero", str(exc))
            return
        self._msg(f"Zeroed: removed a {off * 1e6:+.0f} µA offset (applies to new samples too).")
        self.plot.update()
        self.update_stats()

    # ------------------------------------------------------------ stats
    def analysis_range(self) -> tuple[int, int]:
        if self.plot.selection:
            return self.cap.window(*self.plot.selection)
        if not len(self.cap):
            return 0, 0
        return self.cap.window(*self.plot.view())

    def current_stats(self) -> mt.CaptureStats | None:
        lo, hi = self.analysis_range()
        if hi - lo < 2:
            return None
        try:
            return mt.capture_stats(self.cap, lo, hi)
        except ValueError:
            return None

    def update_stats(self) -> None:
        s = self.current_stats()
        has = s is not None
        self.apply_btn.setEnabled(has)
        self.sim_btn.setEnabled(has and self.sim_tab is not None)
        if not has:
            self.plot.threshold = None
            self.stats_view.show_html(
                "<p>No samples yet.</p><p class='muted'>Wire an INA226/INA219 to the D1 R32 (see firmware/README.md), "
                "flash the BenchBuddy meter sketch, pick its port and press Record. Or try the demo device.</p>")
            return
        self.plot.threshold = s.threshold_a if s.bursts else None
        what = "selection" if self.plot.selection else "visible window"
        ma = lambda a: format_value(a, "A", 4)  # noqa: E731
        rows = [("Average", ma(s.avg_a)), ("Peak (99th pct)", ma(s.p99_a)), ("Max sample", ma(s.max_a)),
                ("Min sample", ma(s.min_a)), ("RMS", ma(s.rms_a)),
                ("Charge", f"{s.charge_mah * 1000:.3g} µAh" if s.charge_mah < 0.1 else f"{s.charge_mah:.4g} mAh"),
                ("Energy", f"{s.energy_mwh:.4g} mWh"), ("Bus voltage", f"{s.avg_v:.3f} V (min {s.min_v:.3f} V)"),
                ("Active (above threshold)", f"{s.duty * 100:.1f} % at {ma(s.active_avg_a)}"),
                ("Idle", ma(s.idle_avg_a)),
                ("Bursts", f"{s.bursts} · avg {format_value(s.burst_avg_s, 's', 3)}" if s.bursts else "none"),
                ("Samples", f"{s.n:,} over {format_value(s.duration_s, 's', 3)} · {s.rate_hz:,.0f} Hz")]
        body = "".join(f"<tr><td class='muted'>{k}</td><td><b>{v}</b></td></tr>" for k, v in rows)
        notes = []
        chip, r = self._chip(), self.cap.r_shunt
        if self.cap.overrange_count:
            notes.append(status_html("error", f"OVER RANGE on {self.cap.overrange_count} samples: the shunt voltage hit "
                                              f"the {chip.name}'s ±{chip.shunt_fs_v * 1000:g} mV limit "
                                              f"(±{format_value(chip.full_scale_a(r), 'A')} with {r:g} Ω). "
                                              f"Use a smaller shunt for this load."))
        if s.bursts and s.burst_avg_s < 5 / s.rate_hz:
            per = s.burst_avg_s * s.rate_hz
            tip = ("Switch to FAST mode." if self.hello.get("mode") != "FAST"
                   else "That's the INA's limit even in FAST mode; trust the timing and average more than the peak.")
            notes.append(status_html("warn", f"Bursts last only ~{per:.1f} samples, so their peak is under-read. {tip}"))
        if abs(s.idle_avg_a) < 20 * chip.lsb_a(r):
            notes.append(status_html("warn", f"The idle current is within 20 LSB of zero "
                                             f"({format_value(chip.lsb_a(r), 'A')} per step): too small to trust with "
                                             f"this shunt. A larger shunt reads µA sleep currents properly."))
        if self.hello.get("mode") == "FAST":
            notes.append(status_html("ok", "FAST mode samples current only: the bus voltage (and so energy) uses "
                                           "the value read when FAST started."))
        if self.cap.dropped:
            notes.append(status_html("warn", f"Buffer full: the oldest {self.cap.dropped:,} samples were dropped."))
        self.stats_view.show_html(f"<p class='muted'>Stats for the {what}</p><table cellspacing=4>{body}</table>"
                                  + "".join(notes))

    # ----------------------------------------------------------- handoffs
    def apply_to_load(self) -> None:
        s = self.current_stats()
        if s is None or self.power_tab is None:
            return
        proj = self.power_tab.project
        new_item = "➕ New load from this measurement"
        items = [f"{k + 1}. {l.name} (on {l.rail})" for k, l in enumerate(proj.loads)] + [new_item]
        choice, ok = QInputDialog.getItem(self, "Use measurement",
                                          f"Average {s.avg_a * 1000:.1f} mA, peak {s.p99_a * 1000:.0f} mA, "
                                          f"{s.duty * 100:.1f} % active.\nWhich load should take these numbers?",
                                          items, len(items) - 1, False)
        if not ok:
            return
        vals = s.as_load()
        if choice == new_item:
            rails = [r.name for r in proj.rails]
            if not rails:
                QMessageBox.information(self, "Use measurement", "Add a supply in the Power budget tab first.")
                return
            rail, ok = QInputDialog.getItem(self, "Use measurement", "Put the new load on which rail?", rails, 0, False)
            if not ok:
                return
            name = f"Measured {_dt.datetime.now():%Y-%m-%d %H:%M}"
            proj.loads.append(power.Load(name, rail, 1, **vals))
        else:
            load = proj.loads[items.index(choice)]
            name = load.name
            load.i_active_ma, load.i_peak_ma = vals["i_active_ma"], vals["i_peak_ma"]
            load.i_sleep_ma, load.duty = vals["i_sleep_ma"], vals["duty"]
        self.power_tab.refresh_all()
        self._msg(f"'{name}' now uses the measured figures (per unit: check its Qty).", 8000)

    def send_to_sim(self) -> None:
        if self.sim_tab is None:
            return
        lo, hi = self.analysis_range()
        if hi - lo < 2:
            return
        t, i = mt.to_profile(self.cap, lo, hi)
        dur = self.cap.t[hi - 1] - self.cap.t[lo]
        self.sim_tab.set_profile(t, i, f"{self.cap.chip} capture, {format_value(dur, 's', 3)}")
        win = self.window()
        if hasattr(win, "tabs"):
            win.tabs.setCurrentWidget(self.sim_tab)

    # ---------------------------------------------------------------- csv
    def save_csv(self) -> None:
        if len(self.cap) < 2:
            return
        lo, hi = self.analysis_range() if self.plot.selection else (0, len(self.cap))
        path, _ = QFileDialog.getSaveFileName(self, "Save capture", f"capture_{_dt.datetime.now():%Y%m%d_%H%M%S}.csv",
                                              "CSV (*.csv)")
        if path:
            Path(path).write_text(self.cap.to_csv(lo, hi), encoding="utf-8")
            self._msg(f"Saved {hi - lo:,} samples to {path}")

    def open_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open capture", "", "CSV (*.csv)")
        if path:
            self.load_csv(path)

    def load_csv(self, path: str) -> None:
        try:
            cap = mt.Capture.from_csv(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Couldn't open capture", str(exc))
            return
        if self.connected:
            self.disconnect_meter()
        self.cap = cap
        self.plot.cap = cap
        self.plot.selection = None
        self.span_combo.setCurrentText("All")
        self.follow.setChecked(False)
        self.plot.t_end = None
        self.plot.full_scale = self._chip().full_scale_a(cap.r_shunt)
        self.shunt.setText(f"{cap.r_shunt:g}")
        self.plot.update()
        self.update_stats()
        self._update_status()

    def shutdown(self) -> None:
        """Called on window close: stop the worker in its own thread, then end the thread."""
        if self._worker is not None and self._thread is not None and self._thread.isRunning():
            QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.BlockingQueuedConnection)
            self._thread.quit()
            self._thread.wait(2000)
