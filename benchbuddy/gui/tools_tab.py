"""Ohm's law, wiring voltage drop, GPIO limits, battery / deep-sleep calculators."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from ..core import power
from ..core.units import format_value
from .widgets import guarded, ResultView, ValueEdit, form, group, status_html


class ToolsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.addWidget(self._ohm_group(), 0, 0)
        grid.addWidget(self._wire_group(), 0, 1)
        grid.addWidget(self._gpio_group(), 1, 0, 1, 2)

    # ------------------------------------------------------------------ Ohm
    def _ohm_group(self) -> QWidget:
        self.o_v = ValueEdit("V", "5")
        self.o_i = ValueEdit("A")
        self.o_r = ValueEdit("Ω", "250")
        self.o_p = ValueEdit("W")
        self.o_out = ResultView(90)
        for e in (self.o_v, self.o_i, self.o_r, self.o_p):
            e.textChanged.connect(self._recalc_ohm)
        lay = QVBoxLayout()
        lay.addWidget(QLabel("Fill in any two fields:"))
        lay.addLayout(form(("Voltage (V):", self.o_v), ("Current (A):", self.o_i),
                           ("Resistance (Ω):", self.o_r), ("Power (W):", self.o_p)))
        lay.addWidget(self.o_out)
        self._recalc_ohm()
        return group("Ohm's law && power", lay)

    @guarded("o_out")
    def _recalc_ohm(self) -> None:
        texts = {"v": self.o_v, "i": self.o_i, "r": self.o_r, "p": self.o_p}
        given = {k: e.value() for k, e in texts.items() if e.text().strip()}
        if any(v is None for v in given.values()):
            self.o_out.show_error("Couldn't read one of the values.")
            return
        if len(given) != 2:
            self.o_out.show_html("Enter exactly <b>two</b> values to solve for the other two.")
            return
        try:
            r = power.ohms_law(**given)
        except (ValueError, ZeroDivisionError) as exc:
            self.o_out.show_error(str(exc))
            return
        self.o_out.show_html(
            f"V = <b>{format_value(r['v'], 'V')}</b> · I = <b>{format_value(r['i'], 'A')}</b><br>"
            f"R = <b>{format_value(r['r'], 'Ω')}</b> · P = <b>{format_value(r['p'], 'W')}</b>")

    # ----------------------------------------------------------------- wire
    def _wire_group(self) -> QWidget:
        self.w_awg = QComboBox()
        for a in sorted(power.AWG_OHM_PER_KM, reverse=True):
            self.w_awg.addItem(f"AWG {a}", a)
        self.w_awg.setCurrentText("AWG 24")
        self.w_len = ValueEdit("m", "0.5")
        self.w_i = ValueEdit("A or mA", "1")
        self.w_v = ValueEdit("V", "5")
        self.w_out = ResultView(110)
        self.w_awg.currentIndexChanged.connect(self._recalc_wire)
        for e in (self.w_len, self.w_i, self.w_v):
            e.textChanged.connect(self._recalc_wire)
        lay = QVBoxLayout()
        lay.addLayout(form(("Wire gauge:", self.w_awg), ("One-way length (m):", self.w_len),
                           ("Current:", self.w_i), ("Supply voltage (V):", self.w_v)))
        lay.addWidget(self.w_out)
        lay.addWidget(QLabel("Dupont jumper wires are about AWG 26-28: fine for signals, poor for amps."))
        self._recalc_wire()
        return group("Wire / jumper voltage drop (power out + ground back)", lay)

    @guarded("w_out")
    def _recalc_wire(self) -> None:
        length, amps, v = self.w_len.value(), self.w_i.value(), self.w_v.value()
        if not length or not amps:
            self.w_out.show_html("Enter length and current.")
            return
        awg = self.w_awg.currentData()
        drop, r = power.wire_drop(awg, length, amps)
        html = f"<p>Loop resistance {format_value(r, 'Ω')} → drop <b>{drop:.3f} V</b>"
        if v:
            pct = drop / v * 100
            html += f" ({pct:.1f}% of {v:g} V)</p>"
            level = "error" if pct > 10 else "warn" if pct > 3 else "ok"
            html += status_html(level, {"ok": "Negligible drop.", "warn": "Noticeable: thicker/shorter wire is better.",
                                        "error": "Large drop: the load will brown out."}[level])
            thin = power.smallest_awg_for_drop(length, amps, v * 0.03)
            html += (f"<p>For ≤ 3% drop use <b>AWG {thin}</b> or thicker.</p>" if thin else
                     "<p>No standard AWG here keeps the drop under 3%: shorten the run or raise the voltage.</p>")
        else:
            html += "</p>"
        if amps > power.wire_ampacity_a(awg):
            html += status_html("error", f"{amps:g} A exceeds the ≈{power.wire_ampacity_a(awg):g} A safe rating for AWG {awg}: it will heat up.")
        self.w_out.show_html(html)

    # ----------------------------------------------------------------- GPIO
    def _gpio_group(self) -> QWidget:
        self.g_board = QComboBox()
        self.g_board.addItems(power.GPIO_LIMITS)
        self.g_in = ValueEdit("e.g. 10m, 20m, 40m (comma separated)", "10m, 20m, 35m")
        self.g_out = ResultView(120)
        self.g_board.currentTextChanged.connect(self._recalc_gpio)
        self.g_in.textChanged.connect(self._recalc_gpio)
        self.g_in.setClearButtonEnabled(True)
        lay = QVBoxLayout()
        lay.addLayout(form(("Board / chip:", self.g_board),
                           ("Current drawn from each GPIO:", self.g_in)))
        lay.addWidget(self.g_out)
        lay.addWidget(QLabel("Typical datasheet limits: confirm for your exact chip. "
                             "Anything above the recommended value should go through a transistor or MOSFET."))
        self._recalc_gpio()
        return group("GPIO current check", lay)

    @guarded("g_out")
    def _recalc_gpio(self) -> None:
        from ..core.units import parse_value
        raw = self.g_in.text().replace(",", " ").split()
        try:
            amps = [parse_value(x) for x in raw]
        except ValueError:
            self.g_out.show_error("Use values like 10m, 20m or 0.02 (A). Plain numbers over 1 are read as mA.")
            return
        # plain numbers > 1 are obviously mA
        ma = [a if a > 1 else a * 1000 for a in amps]
        if not ma:
            self.g_out.show_html("Enter the current each GPIO pin supplies, one value per pin.")
            return
        self.g_out.show_html("".join(status_html(l, t) for l, t in power.gpio_check(self.g_board.currentText(), ma)))


class BatteryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.b_active = ValueEdit("mA", "80")
        self.b_active_s = ValueEdit("seconds", "2")
        self.b_sleep = ValueEdit("mA", "0.01")
        self.b_sleep_s = ValueEdit("seconds", "598")
        self.b_cap = ValueEdit("mAh", "2600")
        self.b_usable = ValueEdit("%", "80")
        self.b_out = ResultView(140)
        for e in (self.b_active, self.b_active_s, self.b_sleep, self.b_sleep_s, self.b_cap, self.b_usable):
            e.textChanged.connect(self._recalc)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Model a wake / sleep cycle (e.g. ESP32 deep-sleep sensor node):"))
        lay.addLayout(form(
            ("Active current (mA):", self.b_active), ("Active time per cycle (s):", self.b_active_s),
            ("Sleep current (mA):", self.b_sleep), ("Sleep time per cycle (s):", self.b_sleep_s),
            ("Battery capacity (mAh):", self.b_cap), ("Usable capacity (%):", self.b_usable)))
        lay.addWidget(self.b_out, 1)
        lay.addWidget(QLabel("Remember to include regulator quiescent current and any always-on parts "
                             "(USB-UART chip, power LED, sensors). Dev boards often draw 5-30 mA even when the chip sleeps."))
        self._recalc()

    @guarded("b_out")
    def _recalc(self) -> None:
        vals = [e.value() for e in (self.b_active, self.b_active_s, self.b_sleep, self.b_sleep_s)]
        if None in vals:
            self.b_out.show_html("Fill in the four cycle values.")
            return
        usable = (self.b_usable.value() or 80) / 100
        try:
            r = power.duty_cycle_average(*vals, capacity_mah=self.b_cap.value(), usable=min(max(usable, 0.01), 1))
        except ValueError as exc:
            self.b_out.show_error(str(exc))
            return
        html = (f"<p>Average current <b>{r.avg_ma:.4g} mA</b> · awake {r.duty * 100:.2f}% of the time</p>")
        if r.runtime_h is not None:
            html += (f"<h2 style='margin:0'>≈ {r.runtime_h:,.0f} hours ({r.runtime_days:,.1f} days, "
                     f"{r.runtime_days / 365:.2f} years)</h2>"
                     f"<p class='muted'>Real life is usually shorter: cold temperatures, battery self-discharge and "
                     f"regulator losses all eat into this.</p>")
        self.b_out.show_html(html)
