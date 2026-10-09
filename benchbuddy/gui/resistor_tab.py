"""Resistor information tab."""

from __future__ import annotations

from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout,
                             QWidget)

from ..core import resistors as res
from ..core.units import format_rkm, format_value, parse_value
from .widgets import (STATUS_COLORS, ResistorWidget, ResultView, ValueEdit, form, group, status_html)

BAND_COUNTS = ["4", "5", "6", "3"]


class ResistorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tabs = QTabWidget()
        tabs.addTab(self._decode_page(), "Colour code → value")
        tabs.addTab(self._encode_page(), "Value → colour code")
        tabs.addTab(self._smd_page(), "SMD marking")
        tabs.addTab(self._standard_page(), "Standard values")
        tabs.addTab(self._combine_page(), "Series / parallel")
        tabs.addTab(self._divider_page(), "Voltage divider")
        tabs.addTab(self._led_page(), "LED resistor")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

    # ------------------------------------------------------- colour → value
    def _decode_page(self) -> QWidget:
        self.dec_count = QComboBox()
        self.dec_count.addItems(BAND_COUNTS)
        self.dec_widget = ResistorWidget()
        self.dec_combos: list[QComboBox] = []
        self.dec_row = QHBoxLayout()
        self.dec_out = ResultView(110)
        self.dec_count.currentTextChanged.connect(self._rebuild_dec_bands)
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Number of bands:"))
        top.addWidget(self.dec_count)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(self.dec_widget)
        lay.addLayout(self.dec_row)
        lay.addWidget(self.dec_out, 1)
        self._rebuild_dec_bands()
        return w

    def _band_choices(self, n: int, idx: int) -> list[str]:
        digits = res.DIGIT_COLORS
        mults = list(res.MULTIPLIER_COLORS)
        tols = list(res.TOLERANCE_COLORS)
        if n == 3:
            layout = [digits, digits, mults]
        elif n == 4:
            layout = [digits, digits, mults, tols]
        elif n == 5:
            layout = [digits, digits, digits, mults, tols]
        else:
            layout = [digits, digits, digits, mults, tols, list(res.TEMPCO_COLORS)]
        return layout[idx]

    def _rebuild_dec_bands(self) -> None:
        n = int(self.dec_count.currentText())
        while self.dec_row.count():
            item = self.dec_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.dec_combos = []
        defaults = {3: ["yellow", "violet", "red"], 4: ["yellow", "violet", "red", "gold"],
                    5: ["yellow", "violet", "black", "brown", "brown"],
                    6: ["yellow", "violet", "black", "brown", "brown", "red"]}[n]
        for i in range(n):
            col = QVBoxLayout()
            names = ["Digit 1", "Digit 2", "Digit 3", "Multiplier", "Tolerance", "Temp. coeff."]
            label = (["Digit 1", "Digit 2", "Multiplier", "Tolerance", "Temp. coeff."] if n == 4 else
                     ["Digit 1", "Digit 2", "Multiplier"] if n == 3 else names)[i]
            col.addWidget(QLabel(label))
            cb = QComboBox()
            cb.addItems(self._band_choices(n, i))
            cb.setCurrentText(defaults[i])
            cb.currentTextChanged.connect(self._recalc_decode)
            self.dec_combos.append(cb)
            col.addWidget(cb)
            holder = QWidget()
            holder.setLayout(col)
            self.dec_row.addWidget(holder)
        self._recalc_decode()

    def _recalc_decode(self) -> None:
        bands = [c.currentText() for c in self.dec_combos]
        self.dec_widget.set_bands(bands)
        try:
            d = res.decode_color_bands(bands)
        except ValueError as exc:
            self.dec_out.show_error(str(exc))
            return
        html = (f"<h2 style='margin:0'>{format_value(d.ohms, 'Ω')} ± {d.tolerance_pct:g}%</h2>"
                f"<p>Written as <b>{format_rkm(d.ohms)}</b> · range {format_value(d.min_ohms, 'Ω')} "
                f"to {format_value(d.max_ohms, 'Ω')}</p>")
        if d.tempco_ppm:
            html += f"<p>Temperature coefficient: {d.tempco_ppm} ppm/°C</p>"
        lo, hi = res.standard_neighbours(d.ohms, "E24")
        html += f"<p class='muted'>Nearest E24 values: {format_value(lo, 'Ω')} / {format_value(hi, 'Ω')}.</p>"
        html += ("<p class='muted'>Tip: read from the end that has the bands bunched closer together. "
                 "A gold or silver band is always the tolerance (last).</p>")
        self.dec_out.show_html(html)

    # ---------------------------------------------------------- value → colour
    def _encode_page(self) -> QWidget:
        self.enc_value = ValueEdit("e.g. 4k7, 220, 1M", "4k7")
        self.enc_bands = QComboBox()
        self.enc_bands.addItems(["4", "5"])
        self.enc_tol = QComboBox()
        self.enc_tol.addItems(["5", "1", "2", "10", "0.5", "0.25", "0.1"])
        self.enc_widget = ResistorWidget()
        self.enc_out = ResultView(100)
        for sig in (self.enc_value.textChanged, self.enc_bands.currentTextChanged, self.enc_tol.currentTextChanged):
            sig.connect(self._recalc_encode)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Resistance (Ω):", self.enc_value), ("Bands:", self.enc_bands),
                           ("Tolerance (%):", self.enc_tol)))
        lay.addWidget(self.enc_widget)
        lay.addWidget(self.enc_out, 1)
        self._recalc_encode()
        return w

    def _recalc_encode(self) -> None:
        v = self.enc_value.value()
        if v is None:
            self.enc_widget.set_bands([])
            self.enc_out.show_html("Enter a resistance such as <b>4k7</b>, <b>330</b> or <b>2.2M</b>.")
            return
        try:
            bands = res.encode_color_bands(v, int(self.enc_bands.currentText()), float(self.enc_tol.currentText()))
        except ValueError as exc:
            self.enc_widget.set_bands([])
            self.enc_out.show_error(str(exc))
            return
        self.enc_widget.set_bands(bands)
        e24 = res.nearest_standard(v, "E24")
        note = "" if abs(e24 - v) / v < 1e-3 else (
            f"<p style='color:{STATUS_COLORS['warn']}'>Not an E24 value; nearest is {format_value(e24, 'Ω')}.</p>")
        self.enc_out.show_html(f"<h3 style='margin:0'>{' – '.join(b.capitalize() for b in bands)}</h3>"
                               f"<p>{format_value(v, 'Ω')} ({format_rkm(v)})</p>{note}")

    # ----------------------------------------------------------------- SMD
    def _smd_page(self) -> QWidget:
        self.smd_in = ValueEdit("e.g. 472, 1002, 4R7, 01C, 68X")
        self.smd_out = ResultView(120)
        self.smd_in.textChanged.connect(self._recalc_smd)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Marking on the resistor:", self.smd_in)))
        lay.addWidget(self.smd_out, 1)
        self._recalc_smd()
        return w

    def _recalc_smd(self) -> None:
        code = self.smd_in.text().strip()
        if not code:
            self.smd_out.show_html(
                "<b>3 digits:</b> first two = digits, third = zeros → <b>472</b> = 47 × 10² = 4.7 kΩ<br>"
                "<b>4 digits:</b> first three = digits, fourth = zeros → <b>1002</b> = 100 × 10² = 10 kΩ<br>"
                "<b>R</b> marks the decimal point → <b>4R7</b> = 4.7 Ω<br>"
                "<b>000</b> = 0 Ω link (jumper)<br>"
                "<b>2 digits + letter</b> = EIA-96 (1 % parts): code → value table, letter → multiplier "
                "(A×1, B×10, C×100, D×1k, E×10k, F×100k, X×0.1, Y×0.01, Z×0.001)")
            return
        try:
            ohms = res.decode_smd_code(code)
        except ValueError as exc:
            self.smd_out.show_error(str(exc))
            return
        txt = "0 Ω (jumper)" if ohms == 0 else f"{format_value(ohms, 'Ω')} ({format_rkm(ohms)})"
        self.smd_out.show_html(f"<h2 style='margin:0'>{txt}</h2>"
                               f"<p class='muted'>If this is a capacitor marking instead, use the Capacitors tab.</p>")

    # ---------------------------------------------------------- standard values
    def _standard_page(self) -> QWidget:
        self.std_in = ValueEdit("e.g. 4800, 3.9k", "4800")
        self.std_out = ResultView(150)
        self.std_in.textChanged.connect(self._recalc_standard)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Value you need (Ω):", self.std_in)))
        lay.addWidget(self.std_out, 1)
        self._recalc_standard()
        return w

    def _recalc_standard(self) -> None:
        v = self.std_in.value()
        if not v or v <= 0:
            self.std_out.show_html("Enter the resistance you calculated and see which standard parts to buy.")
            return
        rows = []
        for series, tol in (("E12", "10%"), ("E24", "5%"), ("E96", "1%")):
            lo, hi = res.standard_neighbours(v, series)
            near = res.nearest_standard(v, series)
            err = (near - v) / v * 100
            rows.append(f"<tr><td><b>{series}</b> ({tol})</td><td>{format_value(lo, 'Ω')}</td>"
                        f"<td>{format_value(hi, 'Ω')}</td><td><b>{format_value(near, 'Ω')}</b> ({err:+.1f}%)</td></tr>")
        self.std_out.show_html(
            "<table cellspacing=8><tr><th>Series</th><th>Next lower</th><th>Next higher</th><th>Nearest</th></tr>"
            + "".join(rows) + "</table>")

    # ----------------------------------------------------------- series/parallel
    def _combine_page(self) -> QWidget:
        self.comb_in = QPlainTextEdit()
        self.comb_in.setPlaceholderText("One resistor per line or separated by spaces/commas.\nExample:\n4k7\n10k\n330")
        self.comb_in.setPlainText("10k\n10k\n4k7")
        self.comb_out = ResultView(100)
        self.comb_in.textChanged.connect(self._recalc_combine)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Resistor values:"))
        lay.addWidget(self.comb_in, 1)
        lay.addWidget(self.comb_out, 1)
        self._recalc_combine()
        return w

    def _recalc_combine(self) -> None:
        raw = self.comb_in.toPlainText().replace(",", " ").split()
        try:
            vals = [parse_value(x) for x in raw]
        except ValueError as exc:
            self.comb_out.show_error(str(exc))
            return
        if not vals:
            self.comb_out.show_html("Enter two or more values.")
            return
        try:
            par = res.parallel_resistance(vals)
        except ValueError as exc:
            self.comb_out.show_error(str(exc))
            return
        self.comb_out.show_html(f"<p><b>Series:</b> {format_value(res.series_resistance(vals), 'Ω')}</p>"
                                f"<p><b>Parallel:</b> {format_value(par, 'Ω')}</p>")

    # -------------------------------------------------------------- divider
    def _divider_page(self) -> QWidget:
        self.div_vin = ValueEdit("V", "5")
        self.div_r1 = ValueEdit("Ω (top)", "10k")
        self.div_r2 = ValueEdit("Ω (bottom)", "20k")
        self.div_load = ValueEdit("Ω, optional")
        self.div_out = ResultView(110)
        self.fnd_vin = ValueEdit("V", "5")
        self.fnd_vout = ValueEdit("V", "3.3")
        self.fnd_series = QComboBox()
        self.fnd_series.addItems(["E24", "E12", "E96"])
        self.fnd_out = ResultView(150)
        for e in (self.div_vin, self.div_r1, self.div_r2, self.div_load):
            e.textChanged.connect(self._recalc_divider)
        for e in (self.fnd_vin, self.fnd_vout):
            e.textChanged.connect(self._recalc_divider_find)
        self.fnd_series.currentTextChanged.connect(self._recalc_divider_find)
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        left.addLayout(form(("Vin (V):", self.div_vin), ("R1 top (Ω):", self.div_r1),
                            ("R2 bottom (Ω):", self.div_r2), ("Load across R2 (Ω):", self.div_load)))
        left.addWidget(self.div_out, 1)
        right = QVBoxLayout()
        right.addLayout(form(("Vin (V):", self.fnd_vin), ("Wanted Vout (V):", self.fnd_vout),
                             ("Series:", self.fnd_series)))
        right.addWidget(self.fnd_out, 1)
        lay.addWidget(group("Calculate output", left))
        lay.addWidget(group("Find resistor pair", right))
        self._recalc_divider()
        self._recalc_divider_find()
        return w

    def _recalc_divider(self) -> None:
        vin, r1, r2 = self.div_vin.value(), self.div_r1.value(), self.div_r2.value()
        if None in (vin, r1, r2):
            self.div_out.show_html("Enter Vin, R1 and R2.")
            return
        try:
            d = res.divider_vout(vin, r1, r2, self.div_load.value())
        except ValueError as exc:
            self.div_out.show_error(str(exc))
            return
        self.div_out.show_html(
            f"<h2 style='margin:0'>Vout = {d.vout:.3f} V</h2>"
            f"<p>Current {format_value(d.current_a, 'A')} · power {format_value(d.power_total_w, 'W')}</p>"
            f"<p class='muted'>ESP32 ADC pins are 3.3 V max (and non-linear above ~3.1 V). "
            f"Keep R1+R2 ≥ 10 kΩ for battery monitoring so the divider doesn't drain the cell.</p>")

    def _recalc_divider_find(self) -> None:
        vin, vout = self.fnd_vin.value(), self.fnd_vout.value()
        if not vin or not vout:
            self.fnd_out.show_html("Enter Vin and wanted Vout.")
            return
        try:
            combos = res.divider_find(vin, vout, self.fnd_series.currentText())
        except ValueError as exc:
            self.fnd_out.show_error(str(exc))
            return
        rows = "".join(f"<tr><td>{format_value(a, 'Ω')}</td><td>{format_value(b, 'Ω')}</td>"
                       f"<td>{c:.3f} V</td><td>{e:+.2f}%</td></tr>" for a, b, c, e in combos[:6])
        self.fnd_out.show_html("<table cellspacing=8><tr><th>R1</th><th>R2</th><th>Vout</th><th>Error</th></tr>"
                               f"{rows}</table>")

    # ------------------------------------------------------------------ LED
    def _led_page(self) -> QWidget:
        self.led_vs = ValueEdit("V", "3.3")
        self.led_vf = ValueEdit("V", "2.0")
        self.led_i = ValueEdit("A or mA, e.g. 10m", "10m")
        self.led_series = QComboBox()
        self.led_series.addItems(["E24", "E12", "E96"])
        self.led_out = ResultView(150)
        for e in (self.led_vs, self.led_vf, self.led_i):
            e.textChanged.connect(self._recalc_led)
        self.led_series.currentTextChanged.connect(self._recalc_led)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Supply voltage (V):", self.led_vs),
                           ("LED forward voltage (V):", self.led_vf),
                           ("Wanted current:", self.led_i), ("Series:", self.led_series)))
        lay.addWidget(self.led_out, 1)
        lay.addWidget(QLabel("Typical Vf: red 1.8-2.0 V · yellow/green 2.0-2.2 V · blue/white 3.0-3.3 V. "
                             "A GPIO should source ≤ 20 mA (ESP32/Uno) or ≤ 12 mA (ESP8266/Pico)."))
        self._recalc_led()
        return w

    def _recalc_led(self) -> None:
        vs, vf, i = self.led_vs.value(), self.led_vf.value(), self.led_i.value()
        if None in (vs, vf, i):
            self.led_out.show_html("Enter supply voltage, LED forward voltage and the current you want.")
            return
        try:
            r = res.led_resistor(vs, vf, i, self.led_series.currentText())
        except ValueError as exc:
            self.led_out.show_error(str(exc))
            return
        html = (f"<h2 style='margin:0'>Use {format_value(r.chosen_ohms, 'Ω')}</h2>"
                f"<p>Calculated {format_value(r.exact_ohms, 'Ω')}; rounded <b>up</b> to the next standard value so the "
                f"LED never exceeds the wanted current.</p>"
                f"<p>Actual current <b>{format_value(r.actual_current_a, 'A')}</b> · resistor dissipates "
                f"{format_value(r.resistor_power_w, 'W')} (a {format_value(r.recommended_power_rating_w, 'W')}+ rating gives "
                f"2× margin; a ¼ W resistor is plenty up to 250 mW).</p>")
        if r.actual_current_a > 0.020:
            html += status_html("warn", "Above 20 mA: don't drive this straight from a microcontroller pin.")
        self.led_out.show_html(html)
