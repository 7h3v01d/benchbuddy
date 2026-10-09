"""Brown-out simulator tab."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter,
                             QVBoxLayout, QWidget)

from ..core import brownout as bo
from ..core.units import format_value
from .plot import PlotWidget
from .widgets import ResultView, ValueEdit, form, group, status_html

PRESETS: dict[str, bo.SimParams] = {
    "ESP32 on USB through an AMS1117": bo.SimParams(),
    "ESP32 on a LiPo through an HT7333 (250 mA LDO)": bo.SimParams(
        v_src=3.9, r_src=0.15, c_in_f=10e-6, v_set=3.3, dropout_v=0.3, i_rated_a=0.25, r_out=0.3,
        i_limit_a=0.4, iq_a=0.000004, c_out_f=22e-6, esr_out=0.05, i_base_a=0.08, i_peak_a=0.5),
    "SIM800L modem straight from a LiPo (GSM bursts)": bo.SimParams(
        v_src=3.9, r_src=0.35, c_in_f=0, reg_kind="none", v_set=3.9, c_out_f=100e-6, esr_out=0.1,
        i_base_a=0.02, i_peak_a=2.0, burst_s=577e-6, period_s=4.615e-3, v_threshold=3.4),
    "ESP32-CAM with flash LED on weak USB": bo.SimParams(
        v_src=5.0, r_src=0.8, c_in_f=0, v_set=3.3, dropout_v=1.1, i_rated_a=0.8, c_out_f=47e-6,
        esr_out=0.1, i_base_a=0.2, i_peak_a=0.6, burst_s=2e-3, period_s=10e-3),
}


class SimTab(QWidget):
    def __init__(self, power_tab=None, parent=None):
        super().__init__(parent)
        self.power_tab = power_tab
        self.params = bo.SimParams()
        self.result: bo.SimResult | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.run)

        # ------------------------------------------------------------ inputs
        self.preset = QComboBox()
        self.preset.addItems(PRESETS)
        self.preset.currentTextChanged.connect(self.load_preset)
        self.rail_combo = QComboBox()
        imp = QPushButton("Import rail from Power budget")
        imp.clicked.connect(self.import_rail)

        self.f_vsrc = ValueEdit("V")
        self.f_rsrc = ValueEdit("Ω")
        self.f_cin = ValueEdit("F, 0 = none")
        self.f_esrin = ValueEdit("Ω")
        self.kind = QComboBox()
        self.kind.addItems(["ldo", "switcher", "none"])
        self.f_vset = ValueEdit("V")
        self.f_drop = ValueEdit("V")
        self.f_rated = ValueEdit("A or mA")
        self.f_rout = ValueEdit("Ω")
        self.f_ilim = ValueEdit("A or mA")
        self.f_tresp = ValueEdit("s")
        self.f_eff = ValueEdit("0-1")
        self.f_vinmin = ValueEdit("V")
        self.f_cout = ValueEdit("F")
        self.f_esrout = ValueEdit("Ω")
        self.f_base = ValueEdit("A or mA")
        self.f_peak = ValueEdit("A or mA")
        self.f_burst = ValueEdit("s")
        self.f_period = ValueEdit("s")
        self.f_first = ValueEdit("s")
        self.f_n = ValueEdit("")
        self.f_thr = ValueEdit("V")
        self._edits = [self.f_vsrc, self.f_rsrc, self.f_cin, self.f_esrin, self.f_vset, self.f_drop, self.f_rated,
                       self.f_rout, self.f_ilim, self.f_tresp, self.f_eff, self.f_vinmin, self.f_cout, self.f_esrout,
                       self.f_base, self.f_peak, self.f_burst, self.f_period, self.f_first, self.f_n, self.f_thr]
        for e in self._edits:
            e.textChanged.connect(self.schedule)
        self.kind.currentTextChanged.connect(self.schedule)

        src = form(("Source voltage (V):", self.f_vsrc), ("Source + cable resistance (Ω):", self.f_rsrc),
                   ("Input capacitor (F):", self.f_cin), ("Input cap ESR (Ω):", self.f_esrin))
        reg = form(("Regulator type:", self.kind), ("Output voltage (V):", self.f_vset),
                   ("LDO dropout at rated current (V):", self.f_drop), ("Rated current:", self.f_rated),
                   ("Output impedance (Ω):", self.f_rout), ("Current limit:", self.f_ilim),
                   ("Response time (s):", self.f_tresp), ("Switcher efficiency:", self.f_eff),
                   ("Switcher min input (V):", self.f_vinmin))
        out = form(("Output capacitor (F):", self.f_cout), ("Output cap ESR (Ω):", self.f_esrout))
        load = form(("Base current:", self.f_base), ("Burst peak current:", self.f_peak),
                    ("Burst length (s):", self.f_burst), ("Burst period (s):", self.f_period),
                    ("First burst at (s):", self.f_first), ("Number of bursts:", self.f_n),
                    ("Brown-out threshold (V):", self.f_thr))

        left = QWidget()
        ll = QVBoxLayout(left)
        row = QHBoxLayout()
        row.addWidget(QLabel("Scenario:"))
        row.addWidget(self.preset, 1)
        ll.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self.rail_combo, 1)
        row2.addWidget(imp)
        ll.addLayout(row2)
        ll.addWidget(group("Source", src))
        ll.addWidget(group("Regulator", reg))
        ll.addWidget(group("Output capacitor", out))
        ll.addWidget(group("Load", load))
        ll.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setMinimumWidth(440)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ----------------------------------------------------------- outputs
        self.plot = PlotWidget()
        self.verdict = ResultView(120)
        fix = QPushButton("Find the smallest output capacitor that fixes it")
        fix.clicked.connect(self.suggest)
        note = QLabel("Simplified model: the regulator is a first-order lag, so ringing and loop stability are not "
                      "modelled. Use it to see why a rail dips and roughly how much capacitance helps; confirm with a scope.")
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(self.plot, 3)
        rl.addWidget(self.verdict, 2)
        rl.addWidget(fix)
        rl.addWidget(note)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(scroll)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([470, 810])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self.load_preset(self.preset.currentText())

    # ----------------------------------------------------------------- params
    def showEvent(self, e) -> None:  # noqa: N802
        self.refresh_rails()
        super().showEvent(e)

    def refresh_rails(self) -> None:
        if not self.power_tab:
            return
        cur = self.rail_combo.currentText()
        self.rail_combo.blockSignals(True)
        self.rail_combo.clear()
        self.rail_combo.addItems([r.name for r in self.power_tab.project.rails])
        if cur:
            self.rail_combo.setCurrentText(cur)
        self.rail_combo.blockSignals(False)

    def set_params(self, p: bo.SimParams) -> None:
        for e in self._edits:
            e.blockSignals(True)
        self.kind.blockSignals(True)
        g = lambda x: f"{x:g}"  # noqa: E731
        self.f_vsrc.setText(g(p.v_src))
        self.f_rsrc.setText(g(p.r_src))
        self.f_cin.setText(g(p.c_in_f))
        self.f_esrin.setText(g(p.esr_in))
        self.kind.setCurrentText(p.reg_kind)
        self.f_vset.setText(g(p.v_set))
        self.f_drop.setText(g(p.dropout_v))
        self.f_rated.setText(g(p.i_rated_a))
        self.f_rout.setText(g(p.r_out))
        self.f_ilim.setText(g(p.i_limit_a))
        self.f_tresp.setText(g(p.t_response_s))
        self.f_eff.setText(g(p.efficiency))
        self.f_vinmin.setText(g(p.vin_min))
        self.f_cout.setText(g(p.c_out_f))
        self.f_esrout.setText(g(p.esr_out))
        self.f_base.setText(g(p.i_base_a))
        self.f_peak.setText(g(p.i_peak_a))
        self.f_burst.setText(g(p.burst_s))
        self.f_period.setText(g(p.period_s))
        self.f_first.setText(g(p.t_first_s))
        self.f_n.setText(g(p.n_bursts))
        self.f_thr.setText(g(p.v_threshold))
        for e in self._edits:
            e.blockSignals(False)
            e._validate()
        self.kind.blockSignals(False)
        self.run()

    @staticmethod
    def _amps(edit: ValueEdit) -> float | None:
        v = edit.value()
        if v is None:
            return None
        return v if v <= 10 else v / 1000          # bare numbers above 10 are mA

    def read_params(self) -> bo.SimParams | None:
        vals = dict(
            v_src=self.f_vsrc.value(), r_src=self.f_rsrc.value(), c_in_f=self.f_cin.value(),
            esr_in=self.f_esrin.value(), v_set=self.f_vset.value(), dropout_v=self.f_drop.value(),
            i_rated_a=self._amps(self.f_rated), r_out=self.f_rout.value(), i_limit_a=self._amps(self.f_ilim),
            t_response_s=self.f_tresp.value(), efficiency=self.f_eff.value(), vin_min=self.f_vinmin.value(),
            c_out_f=self.f_cout.value(), esr_out=self.f_esrout.value(), i_base_a=self._amps(self.f_base),
            i_peak_a=self._amps(self.f_peak), burst_s=self.f_burst.value(), period_s=self.f_period.value(),
            t_first_s=self.f_first.value(), v_threshold=self.f_thr.value())
        if any(v is None for v in vals.values()):
            return None
        n = self.f_n.value()
        if n is None:
            return None
        return bo.SimParams(reg_kind=self.kind.currentText(), n_bursts=int(n), **vals)

    # ---------------------------------------------------------------- actions
    def load_preset(self, name: str) -> None:
        if name in PRESETS:
            self.set_params(PRESETS[name])

    def import_rail(self) -> None:
        if not self.power_tab:
            return
        self.refresh_rails()
        name = self.rail_combo.currentText()
        if not name:
            self.verdict.show_html("Add a supply to the Power budget tab first.")
            return
        try:
            self.set_params(bo.from_power_rail(self.power_tab.project, name))
        except Exception as exc:  # noqa: BLE001 - show any structural problem to the user
            self.verdict.show_error(str(exc))
            return
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(
                f"Imported '{name}' from the power budget. Capacitors and response time are default guesses: set yours.",
                8000)

    def schedule(self) -> None:
        self._timer.start()

    def run(self) -> None:
        p = self.read_params()
        if p is None:
            self.verdict.show_html("Fill in every field with a number (units like 100u, 5m or 4k7 are fine).")
            return
        try:
            self.result = bo.simulate(p)
        except ValueError as exc:
            self.plot.set_result(None)
            self.verdict.show_error(str(exc))
            return
        self.params = p
        self.plot.set_result(self.result)
        html = "".join(status_html(level, text) for level, text in self.result.notes)
        if self.result.brownout:
            uf, _ = bo.suggest_output_cap(p)
            if uf:
                html += status_html("ok", f"Try ≥ {uf} µF of low-ESR capacitance on the output (press the button below to "
                                          f"confirm).")
            else:
                html += status_html("warn", "Even 4700 µF doesn't fix it: the problem is the source or the regulator "
                                            "(sag, dropout or current limit), not the capacitor.")
        self.verdict.show_html(html)

    def suggest(self) -> None:
        p = self.read_params()
        if p is None:
            return
        try:
            base = bo.simulate(p)
        except ValueError as exc:
            self.verdict.show_error(str(exc))
            return
        if not base.brownout:
            self.verdict.show_html(status_html("ok", "There is no brown-out with the current settings: nothing to fix."))
            return
        uf, res = bo.suggest_output_cap(p)
        if uf is None:
            self.verdict.show_html(status_html(
                "error", "No common capacitor value up to 4700 µF avoids the brown-out. Look at the source resistance, "
                         "the regulator's dropout / current limit, or add input-side capacitance."))
            return
        self.set_params(replace(p, c_out_f=uf * 1e-6))
        self.verdict.show_html(status_html(
            "ok", f"<b>Applied {uf} µF</b> on the output: the rail now stays at {res.v_out_min:.2f} V or higher "
                  f"(was {base.v_out_min:.2f} V). Use low-ESR types (ceramic or polymer) as close to the load as you can.")
            + "".join(status_html(level, text) for level, text in self.result.notes))
