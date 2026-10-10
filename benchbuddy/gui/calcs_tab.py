"""Design calculators tab: ADC divider, trace width, inductors, I2C, base resistor,
555 timer, op-amp gain and addressable-LED power."""

from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QTabWidget,
                             QVBoxLayout, QWidget)

from ..core import calcs
from ..core.units import format_value
from .widgets import ResultView, guarded, ValueEdit, form, status_html


class CalcsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tabs = QTabWidget()
        tabs.addTab(self._adc_page(), "Battery ADC divider")
        tabs.addTab(self._trace_page(), "PCB trace width")
        tabs.addTab(self._inductor_page(), "Buck / boost inductor")
        tabs.addTab(self._i2c_page(), "I2C pull-ups")
        tabs.addTab(self._base_page(), "Transistor base resistor")
        tabs.addTab(self._timer_page(), "555 timer")
        tabs.addTab(self._opamp_page(), "Op-amp gain")
        tabs.addTab(self._led_page(), "LED strip power")
        self.sub_tabs = tabs
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

    @guarded("adc_out")
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

    @guarded("tr_out")
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

    @guarded("ind_out")
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

    @guarded("i2_out")
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

    @guarded("bs_out")
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

    # ---------------------------------------------------------------- 555 timer
    def _timer_page(self) -> QWidget:
        self.t5_mode = QComboBox()
        self.t5_mode.addItems(["Design: pick parts for a frequency", "Analyse: astable from R1/R2/C",
                               "Monostable (one-shot) pulse"])
        self.t5_f = ValueEdit("Hz", "1k")
        self.t5_duty = ValueEdit("%", "60")
        self.t5_r1 = ValueEdit("Ω", "1k")
        self.t5_r2 = ValueEdit("Ω", "10k")
        self.t5_c = ValueEdit("F", "100n")
        self.t5_diode = QCheckBox("Diode across R2 (allows duty ≤ 50 %)")
        self.t5_out = ResultView(160)
        for e in (self.t5_f, self.t5_duty, self.t5_r1, self.t5_r2, self.t5_c):
            e.textChanged.connect(self._recalc_555)
        self.t5_mode.currentIndexChanged.connect(self._recalc_555)
        self.t5_diode.toggled.connect(self._recalc_555)
        self.t5_form = form(("Mode:", self.t5_mode), ("Frequency (Hz):", self.t5_f), ("Duty cycle (%):", self.t5_duty),
                            ("R1 (Vcc → DIS):", self.t5_r1), ("R2 (DIS → THR/TRIG):", self.t5_r2),
                            ("C (timing):", self.t5_c), ("", self.t5_diode))
        note = QLabel("Timing follows t = ln2·R·C (the familiar 0.693). Real parts drift with C tolerance; use a "
                      "film or C0G cap for stable timing and add 10 nF on CTRL (pin 5). Keep R1 ≥ 1 kΩ.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        page = self._page(self.t5_form, self.t5_out, note)
        self._recalc_555()
        return page

    def _set_rows_visible(self, f, widgets_visible: dict) -> None:
        for w, vis in widgets_visible.items():
            f.setRowVisible(w, vis)

    @guarded("t5_out")
    def _recalc_555(self) -> None:
        mode = self.t5_mode.currentIndex()
        self._set_rows_visible(self.t5_form, {self.t5_f: mode == 0, self.t5_duty: mode == 0,
                                              self.t5_r1: mode in (1, 2), self.t5_r2: mode == 1,
                                              self.t5_c: mode in (1, 2), self.t5_diode: mode == 1})
        try:
            if mode == 0:
                f, duty = self.t5_f.value(), self.t5_duty.value()
                if not f or duty is None:
                    self.t5_out.show_html("Enter a frequency and duty cycle.")
                    return
                rows = calcs.ne555_astable_design(f, duty / 100)
                if not rows:
                    self.t5_out.show_error("No standard-value combination in 1 kΩ–1 MΩ hits that. Try another frequency.")
                    return
                body = "".join(
                    f"<tr><td>{format_value(d['r1'], 'Ω')}</td><td>{format_value(d['r2'], 'Ω')}</td>"
                    f"<td>{format_value(d['c'], 'F')}</td><td>{format_value(d['freq_hz'], 'Hz', 4)}</td>"
                    f"<td>{d['duty'] * 100:.1f} %</td><td>{d['freq_err_pct']:+.2f} %</td></tr>" for d in rows)
                diode = rows[0]["diode"]
                tip = ("<p>Duty ≤ 50 % needs a <b>diode across R2</b> (anode on DIS, cathode on THR/TRIG) so C charges "
                       "through R1 only.</p>" if diode else "")
                self.t5_out.show_html(
                    f"<h2 style='margin:0'>R1 {format_value(rows[0]['r1'], 'Ω')} · R2 {format_value(rows[0]['r2'], 'Ω')}"
                    f" · C {format_value(rows[0]['c'], 'F')}</h2>{tip}"
                    "<table cellspacing=8><tr><th>R1</th><th>R2</th><th>C</th><th>Frequency</th><th>Duty</th>"
                    f"<th>Error</th></tr>{body}</table>")
            elif mode == 1:
                r1, r2, c = self.t5_r1.value(), self.t5_r2.value(), self.t5_c.value()
                if not (r1 and r2 and c):
                    self.t5_out.show_html("Enter R1, R2 and C.")
                    return
                d = calcs.ne555_astable(r1, r2, c, self.t5_diode.isChecked())
                warn = status_html("warn", "R1 below 1 kΩ: the discharge pin sinks a lot of current.") if r1 < 1e3 else ""
                self.t5_out.show_html(
                    f"<h2 style='margin:0'>{format_value(d['freq_hz'], 'Hz', 4)} · {d['duty'] * 100:.1f} % duty</h2>"
                    f"<p>High {format_value(d['t_high_s'], 's')} · low {format_value(d['t_low_s'], 's')} · "
                    f"period {format_value(d['period_s'], 's')}</p>{warn}")
            else:
                r1, c = self.t5_r1.value(), self.t5_c.value()
                if not (r1 and c):
                    self.t5_out.show_html("Enter R and C.")
                    return
                t = calcs.ne555_monostable(r1, c)
                self.t5_out.show_html(f"<h2 style='margin:0'>Pulse {format_value(t, 's')}</h2>"
                                      f"<p>t = 1.1 · R · C with R = {format_value(r1, 'Ω')}, C = {format_value(c, 'F')}. "
                                      f"Hold TRIG low for less than the pulse length.</p>")
        except ValueError as exc:
            self.t5_out.show_error(str(exc))

    # ---------------------------------------------------------------- op-amp
    def _opamp_page(self) -> QWidget:
        self.oa_inv = QComboBox()
        self.oa_inv.addItems(["Non-inverting (G = 1 + Rf/Rg)", "Inverting (G = −Rf/Rin)"])
        self.oa_gain = ValueEdit("×", "11")
        self.oa_series = QComboBox()
        self.oa_series.addItems(["E24", "E12", "E96"])
        self.oa_vin = ValueEdit("V peak / level", "0.25")
        self.oa_vneg = ValueEdit("V", "0")
        self.oa_vpos = ValueEdit("V", "5")
        self.oa_head = ValueEdit("V", "1.5")
        self.oa_gbw = ValueEdit("Hz, optional", "1M")
        self.oa_slew = ValueEdit("V/µs, optional", "0.5")
        self.oa_freq = ValueEdit("Hz, optional", "1k")
        self.oa_out = ResultView(170)
        for e in (self.oa_gain, self.oa_vin, self.oa_vneg, self.oa_vpos, self.oa_head, self.oa_gbw,
                  self.oa_slew, self.oa_freq):
            e.textChanged.connect(self._recalc_opamp)
        self.oa_inv.currentIndexChanged.connect(self._recalc_opamp)
        self.oa_series.currentTextChanged.connect(self._recalc_opamp)
        rails = QHBoxLayout()
        for lbl, w in (("V−", self.oa_vneg), ("V+", self.oa_vpos), ("headroom", self.oa_head)):
            rails.addWidget(QLabel(lbl))
            rails.addWidget(w)
        dyn = QHBoxLayout()
        for lbl, w in (("GBW", self.oa_gbw), ("slew", self.oa_slew), ("signal", self.oa_freq)):
            dyn.addWidget(QLabel(lbl))
            dyn.addWidget(w)
        note = QLabel("Headroom: ~1.5 V for LM358/TL07x-class parts, ~0.1 V for rail-to-rail outputs (MCP6002, "
                      "TLV9062). LM358 can swing to its negative rail but not its positive one.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        page = self._page(form(("Stage:", self.oa_inv), ("Target gain:", self.oa_gain),
                               ("Resistor series:", self.oa_series), ("Input:", self.oa_vin),
                               ("Rails:", rails), ("Op-amp:", dyn)), self.oa_out, note)
        self._recalc_opamp()
        return page

    @guarded("oa_out")
    def _recalc_opamp(self) -> None:
        inverting = self.oa_inv.currentIndex() == 1
        g = self.oa_gain.value()
        if not g:
            self.oa_out.show_html("Enter the gain you want.")
            return
        try:
            rows = calcs.opamp_design(-abs(g) if inverting else g, inverting, self.oa_series.currentText())
        except ValueError as exc:
            self.oa_out.show_error(str(exc))
            return
        best = rows[0]
        names = ("Rf", "Rin") if inverting else ("Rf", "Rg")
        if best["rf"] == 0:
            head = "<h2 style='margin:0'>Voltage follower</h2><p>Output wired straight to IN−; no resistors.</p>"
            noise_gain = 1.0
        else:
            body = "".join(f"<tr><td>{format_value(d['rf'], 'Ω')}</td><td>{format_value(d['rg'], 'Ω')}</td>"
                           f"<td>{d['gain']:.4g}</td><td>{d['err_pct']:+.2f} %</td></tr>" for d in rows)
            head = (f"<h2 style='margin:0'>{names[0]} {format_value(best['rf'], 'Ω')} · {names[1]} "
                    f"{format_value(best['rg'], 'Ω')} → G = {best['gain']:.4g}</h2>"
                    f"<table cellspacing=8><tr><th>{names[0]}</th><th>{names[1]}</th><th>Gain</th><th>Error</th></tr>"
                    f"{body}</table>")
            noise_gain = calcs.opamp_gain(best["rf"], best["rg"], inverting)["noise_gain"]
        checks = ""
        vin, vn, vp, hd = self.oa_vin.value(), self.oa_vneg.value(), self.oa_vpos.value(), self.oa_head.value()
        if None not in (vin, vn, vp, hd) and vp > vn:
            msgs = calcs.opamp_check(best["gain"], noise_gain, vin, vn, vp, hd,
                                     self.oa_gbw.value(), self.oa_slew.value(), self.oa_freq.value())
            checks = "".join(status_html(lv, t) for lv, t in msgs)
        self.oa_out.show_html(head + checks)

    # --------------------------------------------------------- LED strip power
    def _led_page(self) -> QWidget:
        self.led_type = QComboBox()
        self.led_type.addItems(list(calcs.LED_TYPES))
        self.led_n = QSpinBox()
        self.led_n.setRange(1, 100000)
        self.led_n.setValue(60)
        self.led_mix = QComboBox()
        self.led_mix.addItems(list(calcs.LED_MIXES))
        self.led_bright = QSpinBox()
        self.led_bright.setRange(1, 100)
        self.led_bright.setValue(100)
        self.led_bright.setSuffix(" %")
        self.led_psu = ValueEdit("A, optional", "2")
        self.led_out = ResultView(170)
        for w in (self.led_n, self.led_bright):
            w.valueChanged.connect(self._recalc_led)
        for w in (self.led_type, self.led_mix):
            w.currentIndexChanged.connect(self._recalc_led)
        self.led_psu.textChanged.connect(self._recalc_led)
        note = QLabel("Per-channel currents are typical; newer WS2812B revisions draw less. Measure one LED at full "
                      "white to calibrate. 3.3 V boards need a level shifter (or a sacrificial first pixel) on DIN, "
                      "and a 300–500 Ω resistor in series plus 1000 µF across the strip's supply.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        page = self._page(form(("LED type:", self.led_type), ("Number of LEDs:", self.led_n),
                               ("Colour mix:", self.led_mix), ("Global brightness:", self.led_bright),
                               ("Your supply (A):", self.led_psu)), self.led_out, note)
        self._recalc_led()
        return page

    @guarded("led_out")
    def _recalc_led(self) -> None:
        spec = calcs.LED_TYPES[self.led_type.currentText()]
        ch = spec.get("channels", 3)
        mix = calcs.LED_MIXES[self.led_mix.currentText()]
        n, bright = self.led_n.value(), self.led_bright.value() / 100
        try:
            r = calcs.led_budget(n, spec["ma_per_ch"], ch, bright, mix, spec["idle_ma"], spec["v"])
        except ValueError as exc:
            self.led_out.show_error(str(exc))
            return
        html = (f"<h2 style='margin:0'>{r['total_ma'] / 1000:.2f} A · {r['power_w']:.1f} W at {spec['v']:g} V</h2>"
                f"<p>Worst case (full white at {bright * 100:.0f} %): <b>{r['worst_ma'] / 1000:.2f} A</b>. "
                f"Idle (all off): {r['idle_ma']:.0f} mA. Pick a supply of at least <b>{r['psu_a']:.1f} A</b> "
                f"(25 % margin).</p>")
        psu = self.led_psu.value()
        if psu:
            cap = calcs.led_max_brightness(n, spec["ma_per_ch"], psu * 1000, ch, spec["idle_ma"], mix=1.0)
            lvl = "ok" if r["worst_ma"] <= psu * 1000 else "warn" if r["total_ma"] <= psu * 1000 else "error"
            html += status_html(lvl, f"A {psu:g} A supply allows full white up to <b>{cap * 100:.0f} %</b> brightness. "
                                     f"FastLED: <b>FastLED.setMaxPowerInVoltsAndMilliamps({spec['v']:g}, "
                                     f"{int(psu * 1000 * 0.9)})</b> keeps you 10 % under it.")
        if r["total_ma"] > 3000:
            html += status_html("warn", "Over ~3 A: feed power in at both ends (or every 1–2 m) so the far LEDs don't "
                                        "sag towards red/yellow, and use thick wire for the injection runs.")
        self.led_out.show_html(html)
