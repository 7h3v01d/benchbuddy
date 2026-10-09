"""Power budget tab: rails (supplies / regulators), loads, live analysis."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QSpinBox, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import power
from ..core.presets import (LOAD_PRESETS, REGULATOR_PRESETS, SOURCE_PRESETS, make_load,
                            make_rail)
from ..core.units import parse_value
from . import theme
from .widgets import STATUS_COLORS, STATUS_ICONS, ResultView, group, status_html

RAIL_COLS = ["Name", "Type", "Parent", "Vout (V)", "Max (mA)", "Vmin (V)", "R int (Ω)",
             "Capacity (mAh)", "Dropout / Min Vin (V)", "Eff (%)", "Iq (mA)", "θJA (°C/W)"]
RAIL_TIPS = {
    "Vmin (V)": "Lowest voltage the supply reaches in use (e.g. battery cut-off, USB minimum).",
    "R int (Ω)": "Source + cable resistance. Causes the voltage to sag when current peaks.",
    "Max (mA)": "Output rating of the supply or regulator.",
    "Dropout / Min Vin (V)": "LDO/buck: Vin must be ≥ Vout + this. Boost: minimum input voltage.",
    "θJA (°C/W)": "Thermal resistance used to estimate LDO temperature rise.",
}
LOAD_COLS = ["Name", "Rail", "Qty", "Active (mA)", "Peak (mA)", "Sleep (mA)", "Duty (%)"]
LOAD_TIPS = {
    "Active (mA)": "Typical current while running.",
    "Peak (mA)": "Short bursts (Wi-Fi TX, motor stall...). Peaks of all loads are assumed to coincide.",
    "Sleep (mA)": "Current while idle / asleep.",
    "Duty (%)": "Percentage of time spent active (100 = always on).",
}
# which rail columns apply to which rail type
ENABLED = {
    "supply": {"Vout (V)", "Max (mA)", "Vmin (V)", "R int (Ω)", "Capacity (mAh)"},
    "ldo": {"Vout (V)", "Max (mA)", "Dropout / Min Vin (V)", "Iq (mA)", "θJA (°C/W)"},
    "buck": {"Vout (V)", "Max (mA)", "Dropout / Min Vin (V)", "Eff (%)", "Iq (mA)"},
    "boost": {"Vout (V)", "Max (mA)", "Dropout / Min Vin (V)", "Eff (%)", "Iq (mA)"},
}


def example_project() -> power.Project:
    usb = make_rail(SOURCE_PRESETS["USB 2.0 port (5 V, 500 mA)"], "USB 5V")
    ldo = make_rail(REGULATOR_PRESETS["AMS1117-3.3 (LDO, 800 mA)"], "3V3", parent="USB 5V")
    loads = [
        make_load("ESP32 DevKit (WROOM-32)", "3V3"),
        make_load("SSD1306 OLED 128x64 (I2C)", "3V3"),
        make_load("BME280 / BMP280", "3V3"),
    ]
    return power.Project([usb, ldo], loads)


class PowerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = example_project()
        self._busy = False

        # ---------------------------------------------------------- toolbar
        bar = QHBoxLayout()
        for text, fn in (("New", self.on_new), ("Open…", self.on_open), ("Save…", self.on_save),
                         ("Load example", self.on_example)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        bar.addWidget(self.banner)

        # ------------------------------------------------------------ rails
        self.rail_table = QTableWidget(0, len(RAIL_COLS))
        self.rail_table.setHorizontalHeaderLabels(RAIL_COLS)
        self._style_table(self.rail_table, RAIL_TIPS, RAIL_COLS)
        self.rail_table.itemChanged.connect(self.on_rail_item_changed)

        self.source_combo = QComboBox()
        self.source_combo.addItems(SOURCE_PRESETS)
        add_src = QPushButton("Add supply")
        add_src.clicked.connect(self.add_supply)
        self.reg_combo = QComboBox()
        self.reg_combo.addItems(REGULATOR_PRESETS)
        add_reg = QPushButton("Add regulator (fed from selected rail)")
        add_reg.clicked.connect(self.add_regulator)
        del_rail = QPushButton("Remove rail")
        del_rail.clicked.connect(self.remove_rail)
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        r1.addWidget(self.source_combo, 1)
        r1.addWidget(add_src)
        r2.addWidget(self.reg_combo, 1)
        r2.addWidget(add_reg)
        r2.addWidget(del_rail)
        rails_lay = QVBoxLayout()
        rails_lay.addWidget(self.rail_table)
        rails_lay.addLayout(r1)
        rails_lay.addLayout(r2)

        # ------------------------------------------------------------ loads
        self.load_table = QTableWidget(0, len(LOAD_COLS))
        self.load_table.setHorizontalHeaderLabels(LOAD_COLS)
        self._style_table(self.load_table, LOAD_TIPS, LOAD_COLS)
        self.load_table.itemChanged.connect(self.on_load_item_changed)

        self.load_combo = QComboBox()
        self.load_combo.addItems(LOAD_PRESETS)
        self.load_combo.setEditable(True)
        self.load_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.load_rail_combo = QComboBox()
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 1000)
        add_load = QPushButton("Add load")
        add_load.clicked.connect(self.add_load)
        add_custom = QPushButton("Add blank")
        add_custom.clicked.connect(self.add_blank_load)
        del_load = QPushButton("Remove load")
        del_load.clicked.connect(self.remove_load)
        self.preset_note = QLabel()
        self.preset_note.setWordWrap(True)
        self.preset_note.setProperty("role", "muted")
        self.load_combo.currentTextChanged.connect(self.show_preset_note)
        l1 = QHBoxLayout()
        l1.addWidget(self.load_combo, 2)
        l1.addWidget(QLabel("on"))
        l1.addWidget(self.load_rail_combo, 1)
        l1.addWidget(QLabel("×"))
        l1.addWidget(self.qty_spin)
        l1.addWidget(add_load)
        l1.addWidget(add_custom)
        l1.addWidget(del_load)
        loads_lay = QVBoxLayout()
        loads_lay.addWidget(self.load_table)
        loads_lay.addLayout(l1)
        loads_lay.addWidget(self.preset_note)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(group("Supplies && regulators (power tree)", rails_lay), 1)
        left_lay.addWidget(group("Loads", loads_lay), 1)

        # ---------------------------------------------------------- results
        self.result_table = QTableWidget(0, 8)
        self.result_table.setHorizontalHeaderLabels(
            ["Rail", "Status", "Avg (mA)", "Peak (mA)", "Limit (mA)", "Peak %", "V @ peak", "Heat / runtime"])
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.details = ResultView(160)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(group("Results", self._wrap(self.result_table)), 1)
        right_lay.addWidget(group("What this means", self._wrap(self.details)), 2)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 4)
        split.setSizes([700, 580])

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(split, 1)

        self.refresh_all()
        self.show_preset_note(self.load_combo.currentText())

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _wrap(w: QWidget) -> QVBoxLayout:
        lay = QVBoxLayout()
        lay.addWidget(w)
        return lay

    @staticmethod
    def _style_table(table: QTableWidget, tips: dict[str, str], cols: list[str]) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for i, c in enumerate(cols):
            if c in tips:
                table.horizontalHeaderItem(i).setToolTip(tips[c])

    @staticmethod
    def _fmt(v: float | None) -> str:
        if v is None:
            return ""
        return f"{v:g}"

    @staticmethod
    def _num(text: str) -> float:
        t = text.strip()
        try:
            return float(t)
        except ValueError:
            return parse_value(t)

    def rail_names(self) -> list[str]:
        return [r.name for r in self.project.rails]

    def show_preset_note(self, name: str) -> None:
        p = LOAD_PRESETS.get(name)
        if p:
            self.preset_note.setText(f"Typical for {p['v']:g} V: active {p['active']:g} mA, peak {p['peak']:g} mA, "
                                     f"sleep {p['sleep']:g} mA. {p['note']}")
        else:
            self.preset_note.setText("Custom part: add it, then fill in its currents from the datasheet.")

    # ---------------------------------------------------------------- refresh
    def refresh_all(self) -> None:
        self._busy = True
        try:
            self._fill_rails()
            self._fill_loads()
            self._sync_rail_combo()
        finally:
            self._busy = False
        self.recalc()

    def _sync_rail_combo(self) -> None:
        cur = self.load_rail_combo.currentText()
        self.load_rail_combo.clear()
        self.load_rail_combo.addItems(self.rail_names())
        if cur in self.rail_names():
            self.load_rail_combo.setCurrentText(cur)

    def _fill_rails(self) -> None:
        t = self.rail_table
        t.setRowCount(len(self.project.rails))
        for row, r in enumerate(self.project.rails):
            enabled = ENABLED[r.kind]
            third = r.min_vin if r.kind == "boost" else r.dropout_v
            values = {
                "Name": r.name, "Vout (V)": self._fmt(r.v_out), "Max (mA)": self._fmt(r.max_ma),
                "Vmin (V)": self._fmt(r.v_min), "R int (Ω)": self._fmt(r.r_internal_ohm),
                "Capacity (mAh)": self._fmt(r.capacity_mah), "Dropout / Min Vin (V)": self._fmt(third),
                "Eff (%)": self._fmt(r.efficiency * 100), "Iq (mA)": self._fmt(r.iq_ma),
                "θJA (°C/W)": self._fmt(r.theta_ja),
            }
            for col, name in enumerate(RAIL_COLS):
                if name in ("Type", "Parent"):
                    continue
                item = QTableWidgetItem(values[name])
                if name != "Name" and name not in enabled:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setBackground(QBrush(QColor(theme.PANEL)))
                    item.setText("")
                t.setItem(row, col, item)
            kind = QComboBox()
            kind.addItems(power.KINDS)
            kind.setCurrentText(r.kind)
            kind.currentTextChanged.connect(lambda text, rr=r: self.on_kind_changed(rr, text))
            t.setCellWidget(row, 1, kind)
            parent = QComboBox()
            parent.addItem("—")
            parent.addItems([n for n in self.rail_names() if n != r.name])
            parent.setCurrentText(r.parent or "—")
            parent.setEnabled(r.kind != "supply")
            parent.currentTextChanged.connect(lambda text, rr=r: self.on_parent_changed(rr, text))
            t.setCellWidget(row, 2, parent)
        t.resizeColumnsToContents()
        self._fit_widget_columns(t, (1, 2))

    def _fill_loads(self) -> None:
        t = self.load_table
        t.setRowCount(len(self.project.loads))
        for row, l in enumerate(self.project.loads):
            vals = [l.name, None, str(l.qty), self._fmt(l.i_active_ma), self._fmt(l.i_peak_ma),
                    self._fmt(l.i_sleep_ma), self._fmt(l.duty * 100)]
            for col, v in enumerate(vals):
                if v is not None:
                    t.setItem(row, col, QTableWidgetItem(v))
            combo = QComboBox()
            combo.addItems(self.rail_names())
            combo.setCurrentText(l.rail)
            combo.currentTextChanged.connect(lambda text, ll=l: self.on_load_rail_changed(ll, text))
            t.setCellWidget(row, 1, combo)
        t.resizeColumnsToContents()
        self._fit_widget_columns(t, (1,))

    @staticmethod
    def _fit_widget_columns(t: QTableWidget, cols: tuple[int, ...]) -> None:
        """resizeColumnsToContents ignores cell widgets; widen combo columns so their text isn't clipped."""
        fm = t.fontMetrics()
        for col in cols:
            need = 0
            for r in range(t.rowCount()):
                w = t.cellWidget(r, col)
                if isinstance(w, QComboBox):
                    longest = max((fm.horizontalAdvance(w.itemText(i)) for i in range(w.count())), default=0)
                    need = max(need, longest + 44)   # text + padding + drop-down arrow
            if need:
                # ResizeToContents would ignore setColumnWidth, so pin this column
                t.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
                t.setColumnWidth(col, need)

    # ------------------------------------------------------------------ edits
    def on_kind_changed(self, rail: power.Rail, kind: str) -> None:
        if self._busy or kind == rail.kind:
            return
        rail.kind = kind
        if kind == "supply":
            rail.parent = None
        elif rail.parent is None:
            others = [n for n in self.rail_names() if n != rail.name]
            rail.parent = others[0] if others else None
        self.refresh_all()

    def on_parent_changed(self, rail: power.Rail, text: str) -> None:
        if self._busy:
            return
        rail.parent = None if text == "—" else text
        self.recalc()

    def on_load_rail_changed(self, load: power.Load, text: str) -> None:
        if self._busy or not text:
            return
        load.rail = text
        self.recalc()

    def on_rail_item_changed(self, item: QTableWidgetItem) -> None:
        if self._busy:
            return
        rail = self.project.rails[item.row()]
        col = RAIL_COLS[item.column()]
        text = item.text()
        try:
            if col == "Name":
                new = text.strip()
                if not new or (new != rail.name and new in self.rail_names()):
                    raise ValueError("name must be unique and non-empty")
                old, rail.name = rail.name, new
                for r in self.project.rails:
                    if r.parent == old:
                        r.parent = new
                for l in self.project.loads:
                    if l.rail == old:
                        l.rail = new
                self.refresh_all()
                return
            if col == "Vmin (V)":
                rail.v_min = self._num(text) if text.strip() else None
            elif col == "Capacity (mAh)":
                rail.capacity_mah = self._num(text) if text.strip() else None
            elif col == "Eff (%)":
                rail.efficiency = min(max(self._num(text) / 100, 0.01), 1.0)
            elif col == "Dropout / Min Vin (V)":
                if rail.kind == "boost":
                    rail.min_vin = self._num(text)
                else:
                    rail.dropout_v = self._num(text)
            else:
                attr = {"Vout (V)": "v_out", "Max (mA)": "max_ma", "R int (Ω)": "r_internal_ohm",
                        "Iq (mA)": "iq_ma", "θJA (°C/W)": "theta_ja"}[col]
                setattr(rail, attr, self._num(text))
        except (ValueError, KeyError) as exc:
            self.status_message(f"Couldn't use '{text}': {exc}")
            self.refresh_all()
            return
        self.recalc()

    def on_load_item_changed(self, item: QTableWidgetItem) -> None:
        if self._busy:
            return
        load = self.project.loads[item.row()]
        col = LOAD_COLS[item.column()]
        text = item.text()
        try:
            if col == "Name":
                load.name = text
            elif col == "Qty":
                load.qty = max(int(self._num(text)), 0)
            elif col == "Duty (%)":
                load.duty = min(max(self._num(text) / 100, 0.0), 1.0)
            else:
                attr = {"Active (mA)": "i_active_ma", "Peak (mA)": "i_peak_ma", "Sleep (mA)": "i_sleep_ma"}[col]
                setattr(load, attr, max(self._num(text), 0.0))
        except (ValueError, KeyError) as exc:
            self.status_message(f"Couldn't use '{text}': {exc}")
            self.refresh_all()
            return
        self.recalc()

    def status_message(self, text: str) -> None:
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text, 6000)

    # ---------------------------------------------------------------- actions
    def _unique(self, base: str) -> str:
        name, n = base, 2
        while name in self.rail_names():
            name = f"{base} {n}"
            n += 1
        return name

    def add_supply(self) -> None:
        preset = SOURCE_PRESETS[self.source_combo.currentText()]
        self.project.rails.append(make_rail(preset, self._unique(preset.name)))
        self.refresh_all()

    def add_regulator(self) -> None:
        if not self.project.rails:
            QMessageBox.information(self, "Add a supply first", "A regulator needs a supply or rail to feed it.")
            return
        row = self.rail_table.currentRow()
        parent = self.project.rails[row if row >= 0 else 0].name
        preset = REGULATOR_PRESETS[self.reg_combo.currentText()]
        self.project.rails.append(make_rail(preset, self._unique(preset.name), parent=parent))
        self.refresh_all()

    def remove_rail(self) -> None:
        row = self.rail_table.currentRow()
        if row < 0:
            return
        rail = self.project.rails[row]
        n_loads = sum(1 for l in self.project.loads if l.rail == rail.name)
        n_kids = sum(1 for r in self.project.rails if r.parent == rail.name)
        if n_loads or n_kids:
            ans = QMessageBox.question(
                self, "Remove rail",
                f"'{rail.name}' has {n_loads} load(s) and {n_kids} regulator(s) attached. "
                f"Remove it and everything hanging off it?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        doomed = {rail.name}
        changed = True
        while changed:
            changed = False
            for r in self.project.rails:
                if r.parent in doomed and r.name not in doomed:
                    doomed.add(r.name)
                    changed = True
        self.project.rails = [r for r in self.project.rails if r.name not in doomed]
        self.project.loads = [l for l in self.project.loads if l.rail not in doomed]
        self.refresh_all()

    def add_load(self) -> None:
        rail = self.load_rail_combo.currentText()
        if not rail:
            QMessageBox.information(self, "No rail", "Add a supply or regulator first.")
            return
        name = self.load_combo.currentText().strip()
        if name in LOAD_PRESETS:
            self.project.loads.append(make_load(name, rail, self.qty_spin.value()))
        else:
            self.project.loads.append(power.Load(name or "Custom load", rail, self.qty_spin.value(), 10, 10, 0, 1.0))
        self.refresh_all()

    def add_blank_load(self) -> None:
        rail = self.load_rail_combo.currentText()
        if not rail:
            return
        self.project.loads.append(power.Load("New load", rail, 1, 10, 10, 0, 1.0))
        self.refresh_all()

    def remove_load(self) -> None:
        row = self.load_table.currentRow()
        if row >= 0:
            del self.project.loads[row]
            self.refresh_all()

    def on_new(self) -> None:
        self.project = power.Project()
        self.refresh_all()

    def on_example(self) -> None:
        self.project = example_project()
        self.refresh_all()

    def on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save power budget", "power_budget.json", "JSON (*.json)")
        if path:
            self.project.save(path)
            self.status_message(f"Saved {path}")

    def on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open power budget", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.project = power.Project.load(path)
        except Exception as exc:  # noqa: BLE001 - show any file problem to the user
            QMessageBox.warning(self, "Couldn't open", str(exc))
            return
        self.refresh_all()

    # --------------------------------------------------------------- analysis
    def recalc(self) -> None:
        try:
            report = power.analyse(self.project)
        except power.ProjectError as exc:
            self.result_table.setRowCount(0)
            self.details.show_error(str(exc))
            self._set_banner("error", "Fix the power tree")
            return
        self._show_report(report)

    def _set_banner(self, level: str, text: str) -> None:
        self.banner.setText(f"{STATUS_ICONS[level]} {text}")
        self.banner.setProperty("level", level)
        theme.repolish(self.banner)

    def _show_report(self, report: power.PowerReport) -> None:
        if not report.rails:
            self.result_table.setRowCount(0)
            self.details.show_html("Add a supply to begin: pick one from the list and press <b>Add supply</b>.")
            self._set_banner("ok", "Empty project")
            return
        t = self.result_table
        t.setRowCount(len(report.rails))
        html = []
        for row, rr in enumerate(report.rails):
            r = rr.rail
            extra = ""
            if rr.runtime_h is not None:
                extra = f"{rr.runtime_h:.1f} h" if rr.runtime_h < 48 else f"{rr.runtime_h / 24:.1f} days"
            elif r.kind == "ldo" and rr.temp_rise_avg_c is not None:
                extra = f"{rr.dissipation_avg_w * 1000:.0f} mW (+{rr.temp_rise_avg_c:.0f} °C)"
            elif r.kind in ("buck", "boost"):
                extra = f"{rr.dissipation_avg_w * 1000:.0f} mW loss"
            cells = [
                ("  " if r.parent else "") + r.name,
                f"{STATUS_ICONS[rr.status]} {rr.status.upper()}",
                f"{rr.avg_ma:.1f}", f"{rr.peak_ma:.0f}", f"{r.max_ma:g}", f"{rr.util_peak_pct:.0f}%",
                f"{rr.v_peak_v:.2f} V" if rr.v_peak_v is not None else "—", extra,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(QBrush(QColor(STATUS_COLORS[rr.status])))
                t.setItem(row, col, item)
            html.append(f"<h4 style='margin:10px 0 2px 0'>{r.name} "
                        f"<span class='muted'>({r.kind}, {r.v_out:g} V)</span></h4>")
            for level, text in rr.messages:
                html.append(status_html(level, text))
        t.resizeColumnsToContents()
        self.details.show_html("".join(html))
        worst = report.status
        label = {"ok": "Power budget looks healthy", "warn": "Works, but check the warnings",
                 "error": "Problems found: this will brown out or overheat"}[worst]
        self._set_banner(worst, label)
