"""Design calculators tab: ADC divider, trace width, inductors, I2C, base resistor."""

from __future__ import annotations

from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QTabWidget, QVBoxLayout, QWidget

from ..core import calcs
from ..core.units import format_value
from .widgets import STATUS_COLORS, ResultView, ValueEdit, form, status_html


class CalcsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tabs = QTabWidget()
        tabs.addTab(self._adc_page(), "Battery ADC divider")
        tabs.addTab(self._trace_page(), "PCB trace width")
        tabs.addTab(self._inductor_page(), "Buck / boost inductor")
        tabs.addTab(self._i2c_page(), "I2C pull-ups")
        tabs.addTab(self._base_page(), "Transistor base resistor")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

    @staticmethod
    def _page(*widgets) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        for x in widgets:
            if isinstance(x, QWidget):
                lay.addWidget(x, 1 if isinstance(x, ResultView) else 0)
            else:
                lay.addLayout(x)
        return w

    # ------------------------------------------------------------ ADC divider
    def _adc_page(self) -> QWidget:
        self.adc_vbat = ValueEdit("V", "4.2")
        self.adc_vmax = ValueEdit("V", "3.1")
        self.adc_series = QComboBox()
        self.adc_series.addItems(["E24", "E12", "E96"])
        self.adc_out = ResultView(180)
        for e in (self.adc_vbat, self.adc_vmax):
            e.textChanged.connect(self._recalc_adc)
        self.adc_series.currentTextChanged.connect(self._recalc_adc)
        note = QLabel("ESP32 ADC pins are only linear up to roughly 3.1 V (attenuation 11 dB) and never exceed 3.3 V. "
                      "Put 100 nF across the bottom resistor and keep the divider's source resistance under ~100 kΩ.")
        note.setWordWrap(True)
        page = self._page(form(("Battery voltage when full (V):", self.adc_vbat),
                               ("Highest ADC input to allow (V):", self.adc_vmax),
                               ("Resistor series:", self.adc_series)), self.adc_out, note)
        self._recalc_adc()
        return page

    def _recalc_adc(self) -> None:
        vb, vm = self.adc_vbat.value(), self.adc_vmax.value()
        if not vb or not vm:
            self.adc_out.show_html("Enter the full-battery voltage and the highest voltage your ADC should see.")
            return
        try:
            rows = calcs.adc_divider(vb, vm, self.adc_series.currentText())
        except ValueError as exc:
            self.adc_out.show_error(str(exc))
            return
        body = "".join(
            f"<tr><td>{format_value(d['r1'], 'Ω')}</td><td>{format_value(d['r2'], 'Ω')}</td>"
            f"<td>{d['vout_at_max']:.3f} V</td><td>{d['drain_ua']:.1f} µA</td>"
            f"<td>{format_value(d['source_ohms'], 'Ω')}</td></tr>" for d in rows)
        best = rows[0]
        self.adc_out.show_html(
            "<table cellspacing=8><tr><th>R1 (to battery)</th><th>R2 (to GND)</th><th>ADC sees at full</th>"
            f"<th>Constant drain</th><th>Source Z</th></tr>{body}</table>"
            f"<p>Scale factor to convert readings back: <b>× {(best['r1'] + best['r2']) / best['r2']:.4f}</b> "
            f"(pick the first row). A 1000 mAh cell would take "
            f"{1000 / (best['drain_ua'] / 1000) / 24 / 365:.1f} years of divider drain alone.</p>")

    # ------------------------------------------------------------ trace width
    def _trace_page(self) -> QWidget:
        self.tr_i = ValueEdit("A", "1")
        self.tr_dt = ValueEdit("°C", "10")
        self.tr_oz = QComboBox()
        self.tr_oz.addItems(["1", "0.5", "2", "3"])
        self.tr_ext = QCheckBox("Outer layer (uncheck for inner layer)")
        self.tr_ext.setChecked(True)
        self.tr_len = ValueEdit("mm, optional", "50")
        self.tr_out = ResultView(120)
        for e in (self.tr_i, self.tr_dt, self.tr_len):
            e.textChanged.connect(self._recalc_trace)
        self.tr_oz.currentTextChanged.connect(self._recalc_trace)
        self.tr_ext.toggled.connect(self._recalc_trace)
        page = self._page(form(("Current (A):", self.tr_i), ("Allowed temperature rise (°C):", self.tr_dt),
                               ("Copper weight (oz):", self.tr_oz), ("Layer:", self.tr_ext),
                               ("Trace length (mm):", self.tr_len)), self.tr_out,
                          QLabel("IPC-2221 estimate. 1 oz copper is 35 µm thick. Round up generously for hand-etched or "
                                 "enclosed boards, and use pours for anything over a few amps."))
        self._recalc_trace()
        return page

    def _recalc_trace(self) -> None:
        i, dt = self.tr_i.value(), self.tr_dt.value()
        if not i or not dt:
            self.tr_out.show_html("Enter the current and allowed temperature rise.")
            return
        try:
            r = calcs.trace_width(i, dt, float(self.tr_oz.currentText()), self.tr_ext.isChecked(),
                                  self.tr_len.value())
        except ValueError as exc:
            self.tr_out.show_error(str(exc))
            return
        html = (f"<h2 style='margin:0'>{r['width_mm']:.2f} mm ({r['width_mil']:.0f} mil)</h2>"
                f"<p>minimum width for {i:g} A at +{dt:g} °C.</p>")
        if "resistance_mohm" in r:
            html += (f"<p>Resistance over {self.tr_len.value():g} mm: {r['resistance_mohm']:.1f} mΩ → "
                     f"drop {r['drop_mv']:.1f} mV at {i:g} A.</p>")
        self.tr_out.show_html(html)

    # --------------------------------------------------------------- inductor
    def _inductor_page(self) -> QWidget:
        self.ind_type = QComboBox()
        self.ind_type.addItems(["Buck (step-down)", "Boost (step-up)"])
        self.ind_vin = ValueEdit("V", "12")
        self.ind_vout = ValueEdit("V", "5")
        self.ind_i = ValueEdit("A", "1")
        self.ind_f = ValueEdit("Hz", "500k")
        self.ind_eff = ValueEdit("0-1", "0.85")
        self.ind_ripple = ValueEdit("0-1", "0.3")
        self.ind_out = ResultView(150)
        self.ind_type.currentTextChanged.connect(self._recalc_ind)
        for e in (self.ind_vin, self.ind_vout, self.ind_i, self.ind_f, self.ind_eff, self.ind_ripple):
            e.textChanged.connect(self._recalc_ind)
        page = self._page(form(("Converter:", self.ind_type), ("Input voltage (V):", self.ind_vin),
                               ("Output voltage (V):", self.ind_vout), ("Output current (A):", self.ind_i),
                               ("Switching frequency (Hz):", self.ind_f),
                               ("Efficiency (boost only):", self.ind_eff),
                               ("Ripple current ratio (ΔI / I):", self.ind_ripple)), self.ind_out,
                          QLabel("Continuous-conduction estimates. Pick the next standard inductor value up, with a saturation "
                                 "current at or above the figure shown, and a low DC resistance."))
        self._recalc_ind()
        return page

    def _recalc_ind(self) -> None:
        vin, vout, i, f = (e.value() for e in (self.ind_vin, self.ind_vout, self.ind_i, self.ind_f))
        if None in (vin, vout, i, f):
            self.ind_out.show_html("Fill in voltages, current and frequency.")
            return
        ripple = self.ind_ripple.value() or 0.3
        try:
            if self.ind_type.currentText().startswith("Buck"):
                r = calcs.buck_inductor(vin, vout, i, f, ripple)
                extra = ""
            else:
                r = calcs.boost_inductor(vin, vout, i, f, self.ind_eff.value() or 0.85, ripple)
                extra = f"<p>Average input current: <b>{r['input_a']:.2f} A</b> (battery must supply this).</p>"
        except ValueError as exc:
            self.ind_out.show_error(str(exc))
            return
        self.ind_out.show_html(
            f"<h2 style='margin:0'>L ≈ {format_value(r['inductance_h'], 'H')}</h2>"
            f"<p>Duty cycle {r['duty'] * 100:.1f}% · ripple current {r['ripple_a'] * 1000:.0f} mA · "
            f"peak {r['peak_a']:.2f} A → choose Isat ≥ <b>{r['isat_a']:.2f} A</b></p>{extra}"
            f"<p>Output capacitor for ~50 mV ripple: ≈ {format_value(r['cout_f'], 'F')} (low-ESR).</p>")

    # -------------------------------------------------------------------- I2C
    def _i2c_page(self) -> QWidget:
        self.i2_vcc = ValueEdit("V", "3.3")
        self.i2_cb = ValueEdit("pF", "100")
        self.i2_mode = QComboBox()
        self.i2_mode.addItems(list(calcs.I2C_MODES))
        self.i2_out = ResultView(130)
        for e in (self.i2_vcc, self.i2_cb):
            e.textChanged.connect(self._recalc_i2c)
        self.i2_mode.currentTextChanged.connect(self._recalc_i2c)
        page = self._page(form(("Bus voltage (V):", self.i2_vcc), ("Total bus capacitance (pF):", self.i2_cb),
                               ("Speed mode:", self.i2_mode)), self.i2_out,
                          QLabel("Rough bus capacitance: ~10 pF per device + ~1 pF per 2.5 cm of wire. Breakout boards often "
                                 "already carry 4.7-10 kΩ pull-ups: several in parallel can push you below the minimum."))
        self._recalc_i2c()
        return page

    def _recalc_i2c(self) -> None:
        vcc, cb = self.i2_vcc.value(), self.i2_cb.value()
        if not vcc or not cb:
            self.i2_out.show_html("Enter bus voltage and capacitance.")
            return
        try:
            r = calcs.i2c_pullup(vcc, cb, self.i2_mode.currentText())
        except ValueError as exc:
            self.i2_out.show_error(str(exc))
            return
        self.i2_out.show_html(
            f"<p>Allowed range: <b>{format_value(r['r_min'], 'Ω')} to {format_value(r['r_max'], 'Ω')}</b></p>"
            f"<h2 style='margin:0'>Use ≈ {format_value(r['suggested'], 'Ω')} on SDA and SCL</h2>"
            f"<p>Low-level sink current about {r['current_ma_low']:.2f} mA. If several boards each add their own pull-ups, "
            f"work out the parallel total (Resistors tab) and compare it with the minimum above.</p>")

    # ---------------------------------------------------------- base resistor
    def _base_page(self) -> QWidget:
        self.bs_ic = ValueEdit("A or mA, e.g. 100m", "100m")
        self.bs_v = ValueEdit("V", "3.3")
        self.bs_beta = ValueEdit("", "10")
        self.bs_lim = ValueEdit("mA", "20")
        self.bs_out = ResultView(130)
        for e in (self.bs_ic, self.bs_v, self.bs_beta, self.bs_lim):
            e.textChanged.connect(self._recalc_base)
        page = self._page(form(("Load (collector) current:", self.bs_ic), ("GPIO drive voltage (V):", self.bs_v),
                               ("Forced beta (10 is a safe saturation value):", self.bs_beta),
                               ("GPIO current limit (mA):", self.bs_lim)), self.bs_out,
                          QLabel("For inductive loads (relays, motors, solenoids) always add a flyback diode (1N4007 or "
                                 "1N5819) across the coil, cathode to the positive side."))
        self._recalc_base()
        return page

    def _recalc_base(self) -> None:
        ic, v, beta, lim = (e.value() for e in (self.bs_ic, self.bs_v, self.bs_beta, self.bs_lim))
        if None in (ic, v):
            self.bs_out.show_html("Enter the load current and drive voltage.")
            return
        ic_a = ic if ic <= 10 else ic / 1000      # bare numbers above 10 are mA
        try:
            r = calcs.base_resistor(ic_a, v, forced_beta=beta or 10, gpio_limit_ma=lim or 20)
        except ValueError as exc:
            self.bs_out.show_error(str(exc))
            return
        html = (f"<h2 style='margin:0'>Use {format_value(r['chosen_ohms'], 'Ω')}</h2>"
                f"<p>Exact {format_value(r['exact_ohms'], 'Ω')}, rounded down so the transistor stays saturated. "
                f"Base current {r['actual_ib_ma']:.2f} mA.</p>")
        if r["gpio_ok"]:
            html += status_html("ok", "Within the GPIO current limit.")
        else:
            html += status_html("error", "Base current is above the GPIO limit: use a logic-level MOSFET "
                                         "(e.g. IRLZ44N, AO3400) or a Darlington instead.")
        self.bs_out.show_html(html)
