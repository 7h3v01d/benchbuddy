"""Capacitor information tab."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from ..core import capacitors as cap
from ..core.units import format_value, parse_value
from .widgets import guarded, ResultView, ValueEdit, form, group


class CapacitorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tabs = QTabWidget()
        tabs.addTab(self._marking_page(), "Marking decoder")
        tabs.addTab(self._rc_page(), "RC timing / filter")
        tabs.addTab(self._reactance_page(), "Reactance && energy")
        tabs.addTab(self._combine_page(), "Series / parallel")
        tabs.addTab(self._holdup_page(), "Decoupling && hold-up")
        tabs.addTab(self._types_page(), "Types cheat-sheet")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

    # ---------------------------------------------------------------- marking
    def _marking_page(self) -> QWidget:
        self.mk_in = ValueEdit("e.g. 104, 104J, 22, 4n7, 0.1, 473K")
        self.mk_out = ResultView(150)
        self.mk_in.textChanged.connect(self._recalc_marking)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Marking on the capacitor:", self.mk_in)))
        lay.addWidget(self.mk_out, 1)
        self._recalc_marking()
        return w

    @guarded("mk_out")
    def _recalc_marking(self) -> None:
        code = self.mk_in.text().strip()
        if not code:
            letters = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in cap.TOLERANCE_LETTERS.items())
            self.mk_out.show_html(
                "<b>3 digits</b> = picofarads: first two digits × 10<sup>third</sup> → <b>104</b> = 10 × 10⁴ pF = 100 nF; "
                "<b>473</b> = 47 nF; <b>102</b> = 1 nF.<br><b>1-2 digits</b> = pF directly (22 = 22 pF).<br>"
                "<b>4n7 / 2u2 / p33</b>: the letter is the decimal point.<br>"
                "A trailing letter is the tolerance:<table cellspacing=6>" + letters + "</table>"
                "<p class='muted'>Electrolytics are printed with µF and voltage directly. The stripe marks the negative lead.</p>")
            return
        try:
            d = cap.decode_cap_code(code)
        except ValueError as exc:
            self.mk_out.show_error(str(exc))
            return
        tol = f"<p>Tolerance: <b>{d.tolerance}</b></p>" if d.tolerance else ""
        self.mk_out.show_html(
            f"<h2 style='margin:0'>{format_value(d.farads, 'F')}</h2>"
            f"<p>{d.farads * 1e6:.6g} µF · {d.farads * 1e9:.6g} nF · {d.farads * 1e12:.6g} pF</p>{tol}"
            f"<p class='muted'>{d.note}</p>")

    # --------------------------------------------------------------------- RC
    def _rc_page(self) -> QWidget:
        self.rc_r = ValueEdit("Ω", "10k")
        self.rc_c = ValueEdit("F", "100n")
        self.rc_out = ResultView(150)
        for e in (self.rc_r, self.rc_c):
            e.textChanged.connect(self._recalc_rc)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Resistance (Ω):", self.rc_r), ("Capacitance (F):", self.rc_c)))
        lay.addWidget(self.rc_out, 1)
        self._recalc_rc()
        return w

    @guarded("rc_out")
    def _recalc_rc(self) -> None:
        r, c = self.rc_r.value(), self.rc_c.value()
        if not r or not c:
            self.rc_out.show_html("Enter R and C, for example <b>10k</b> and <b>100n</b>.")
            return
        tau = cap.rc_time_constant(r, c)
        self.rc_out.show_html(
            f"<p><b>Time constant τ = RC</b> = {format_value(tau, 's')}</p>"
            f"<p>63% charged after 1τ · 90% after {format_value(cap.rc_charge_time(r, c, 0, 0.9), 's')} (2.3τ) · "
            f"99% after {format_value(5 * tau, 's')} (5τ)</p>"
            f"<p>Low-pass / high-pass cutoff <b>fc = {format_value(cap.rc_cutoff_hz(r, c), 'Hz')}</b></p>"
            f"<p>10-90% rise time: {format_value(2.197 * tau, 's')}</p>"
            f"<p class='muted'>Reset/debounce circuits: pick τ longer than the bounce/settling time you need.</p>")

    # --------------------------------------------------------------- reactance
    def _reactance_page(self) -> QWidget:
        self.xc_c = ValueEdit("F", "100n")
        self.xc_f = ValueEdit("Hz", "1k")
        self.xc_v = ValueEdit("V", "5")
        self.xc_out = ResultView(150)
        for e in (self.xc_c, self.xc_f, self.xc_v):
            e.textChanged.connect(self._recalc_xc)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form(("Capacitance (F):", self.xc_c), ("Frequency (Hz):", self.xc_f),
                           ("Voltage across cap (V):", self.xc_v)))
        lay.addWidget(self.xc_out, 1)
        self._recalc_xc()
        return w

    @guarded("xc_out")
    def _recalc_xc(self) -> None:
        c, f, v = self.xc_c.value(), self.xc_f.value(), self.xc_v.value()
        if not c or not f:
            self.xc_out.show_html("Enter capacitance and frequency.")
            return
        html = f"<p><b>Reactance Xc</b> = {format_value(cap.capacitor_reactance(c, f), 'Ω')} at {format_value(f, 'Hz')}</p>"
        if v:
            html += f"<p><b>Stored energy</b> = {format_value(cap.capacitor_energy(c, v), 'J')} at {v:g} V</p>"
        self.xc_out.show_html(html)

    # ----------------------------------------------------------- series/parallel
    def _combine_page(self) -> QWidget:
        self.cc_in = QPlainTextEdit()
        self.cc_in.setPlainText("10u\n100n")
        self.cc_in.setPlaceholderText("One capacitor per line, e.g. 10u, 100n, 4n7")
        self.cc_out = ResultView(100)
        self.cc_in.textChanged.connect(self._recalc_combine)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Capacitor values:"))
        lay.addWidget(self.cc_in, 1)
        lay.addWidget(self.cc_out, 1)
        self._recalc_combine()
        return w

    @guarded("cc_out")
    def _recalc_combine(self) -> None:
        try:
            vals = [parse_value(x) for x in self.cc_in.toPlainText().replace(",", " ").split()]
        except ValueError as exc:
            self.cc_out.show_error(str(exc))
            return
        if not vals or any(v <= 0 for v in vals):
            self.cc_out.show_html("Enter one or more positive values.")
            return
        self.cc_out.show_html(f"<p><b>Parallel:</b> {format_value(cap.parallel_capacitance(vals), 'F')}</p>"
                              f"<p><b>Series:</b> {format_value(cap.series_capacitance(vals), 'F')}</p>")

    # ------------------------------------------------------------------ hold-up
    def _holdup_page(self) -> QWidget:
        self.hu_i = ValueEdit("A or mA, e.g. 100m", "100m")
        self.hu_t = ValueEdit("s, e.g. 10m", "10m")
        self.hu_v1 = ValueEdit("V", "5")
        self.hu_v2 = ValueEdit("V", "4.4")
        self.hu_out = ResultView(90)
        for e in (self.hu_i, self.hu_t, self.hu_v1, self.hu_v2):
            e.textChanged.connect(self._recalc_holdup)
        self.st_di = ValueEdit("A or mA", "300m")
        self.st_t = ValueEdit("s", "100u")
        self.st_droop = ValueEdit("V", "0.15")
        self.st_out = ResultView(90)
        for e in (self.st_di, self.st_t, self.st_droop):
            e.textChanged.connect(self._recalc_step)
        self.esp_out = ResultView(120)
        self.esp_out.show_html("<ul>" + "".join(f"<li>{t}</li>" for t in cap.esp32_decoupling_advice()) + "</ul>")
        w = QWidget()
        lay = QVBoxLayout(w)
        a = QVBoxLayout()
        a.addLayout(form(("Load current:", self.hu_i), ("Hold time:", self.hu_t),
                         ("Start voltage (V):", self.hu_v1), ("Minimum allowed (V):", self.hu_v2)))
        a.addWidget(self.hu_out)
        b = QVBoxLayout()
        b.addLayout(form(("Load step ΔI:", self.st_di), ("Step duration:", self.st_t),
                         ("Allowed droop (V):", self.st_droop)))
        b.addWidget(self.st_out)
        c = QVBoxLayout()
        c.addWidget(self.esp_out)
        lay.addWidget(group("Hold-up: ride through a supply interruption", a))
        lay.addWidget(group("Bulk capacitor for a load step (Wi-Fi burst, motor start)", b))
        lay.addWidget(group("ESP32 / microcontroller decoupling rules of thumb", c), 1)
        self._recalc_holdup()
        self._recalc_step()
        return w

    @guarded("hu_out")
    def _recalc_holdup(self) -> None:
        i, t, v1, v2 = self.hu_i.value(), self.hu_t.value(), self.hu_v1.value(), self.hu_v2.value()
        if None in (i, t, v1, v2):
            self.hu_out.show_html("Fill in all four values.")
            return
        try:
            r = cap.holdup_capacitance(i, t, v1, v2)
        except ValueError as exc:
            self.hu_out.show_error(str(exc))
            return
        self.hu_out.show_html(f"<b>C ≥ {format_value(r.farads_needed, 'F')}</b> "
                              f"(use ≈ {format_value(r.farads_needed * 2, 'F')} with a 2× margin). "
                              f"<span class='muted'>{r.note}</span>")

    @guarded("st_out")
    def _recalc_step(self) -> None:
        di, t, dv = self.st_di.value(), self.st_t.value(), self.st_droop.value()
        if None in (di, t, dv):
            self.st_out.show_html("Fill in all three values.")
            return
        try:
            c = cap.bulk_cap_for_transient(di, t, dv)
        except ValueError as exc:
            self.st_out.show_error(str(exc))
            return
        self.st_out.show_html(f"<b>C ≥ {format_value(c, 'F')}</b> close to the load. "
                              f"<span class='muted'>Also check ESR: a step of {format_value(di, 'A')} across "
                              f"0.5 Ω of ESR drops {format_value(cap.esr_droop(di, 0.5), 'V')} instantly.</span>")

    # -------------------------------------------------------------------- types
    def _types_page(self) -> QWidget:
        rows = "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in cap.DIELECTRICS.items())
        view = ResultView(200)
        view.show_html(
            "<h3>Ceramic (MLCC) dielectrics</h3><table cellspacing=6>" + rows + "</table>"
            "<h3>Which to use</h3><ul>"
            "<li><b>100 nF ceramic (X7R)</b> across every IC's supply pins, as close as possible.</li>"
            "<li><b>10 µF ceramic or 100-470 µF low-ESR electrolytic</b> as bulk near regulators and radios.</li>"
            "<li><b>C0G/NP0</b> for oscillator loads, timing and filters (values barely drift).</li>"
            "<li><b>Electrolytics</b>: polarised, wrong way = may vent. Derate to 50-70% of rated voltage.</li>"
            "<li><b>Ceramic DC-bias effect</b>: a 10 µF 6.3 V X5R can lose 60-70% of its capacitance at 5 V.</li>"
            "<li><b>Tantalum</b>: fails short (and can burn) if over-volted or hit with inrush. Avoid if unsure.</li>"
            "</ul>")
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(view)
        return w
