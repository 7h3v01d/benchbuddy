"""Time-domain brown-out simulation.

Circuit modelled::

   V_src --R_src--+--(C_in + ESR)--GND
                  |
              [regulator]--+--(C_out + ESR)--GND
                           |
                         load (base current + repeating bursts)

What it captures: cable/battery sag, input and output capacitance, capacitor ESR,
LDO dropout (which shrinks as current falls), regulator current limit, a
simplified switching regulator that draws constant power, and the regulator's
finite response time (during which the output capacitor supplies load steps).

What it does NOT capture: the regulator's real control loop. The response is a
single first-order lag, so stability, ringing and phase margin with different
output capacitors are not modelled. Treat results as a good first look at *why*
a rail dips and *roughly how much* capacitance helps, not a replacement for
measuring with a scope.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

COMMON_CAP_UF = [10, 22, 47, 100, 220, 330, 470, 680, 1000, 2200, 4700]
ESR_FLOOR = 0.005     # ohms; keeps the no-regulator solver well behaved


@dataclass
class SimParams:
    v_src: float = 5.0            # open-circuit source voltage
    r_src: float = 0.3            # source + cable resistance (ohms)
    c_in_f: float = 10e-6         # capacitance at the regulator input
    esr_in: float = 0.05
    reg_kind: str = "ldo"         # "ldo" | "switcher" | "none"
    v_set: float = 3.3
    dropout_v: float = 1.1        # LDO dropout at rated current
    i_rated_a: float = 0.8
    r_out: float = 0.15           # regulator output impedance
    t_response_s: float = 5e-6    # regulator loop response time (output current lag)
    i_limit_a: float = 1.0
    iq_a: float = 0.005
    efficiency: float = 0.9       # switcher only
    vin_min: float = 3.0          # switcher only: below this its output collapses
    c_out_f: float = 100e-6
    esr_out: float = 0.05
    i_base_a: float = 0.08
    i_peak_a: float = 0.5
    burst_s: float = 400e-6
    period_s: float = 5e-3
    t_first_s: float = 1e-3
    n_bursts: int = 3
    v_threshold: float = 3.0      # below this the chip browns out / resets

    @property
    def duration_s(self) -> float:
        return self.t_first_s + self.n_bursts * self.period_s


@dataclass
class SimResult:
    params: SimParams
    t: list[float]
    v_out: list[float]
    v_in: list[float]
    i_load: list[float]
    i_in: list[float]
    v_out_min: float
    v_out_nominal: float
    v_in_min: float
    time_below_s: float
    notes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def droop_v(self) -> float:
        return self.v_out_nominal - self.v_out_min

    @property
    def brownout(self) -> bool:
        return self.v_out_min < self.params.v_threshold


def _load_current(p: SimParams, t: float) -> float:
    if t >= p.t_first_s:
        phase = (t - p.t_first_s) % p.period_s
        if phase < p.burst_s:
            return p.i_peak_a
    return p.i_base_a


def validate(p: SimParams) -> None:
    if p.reg_kind not in ("ldo", "switcher", "none"):
        raise ValueError("regulator type must be ldo, switcher or none")
    if min(p.r_src, p.r_out) <= 0:
        raise ValueError("source and regulator output resistances must be above 0 Ω")
    if p.c_out_f <= 0:
        raise ValueError("output capacitance must be above 0")
    if p.esr_out < 0 or p.esr_in < 0 or p.c_in_f < 0:
        raise ValueError("ESR and input capacitance can't be negative")
    if p.burst_s <= 0 or p.period_s <= p.burst_s:
        raise ValueError("burst length must be positive and shorter than the period")
    if p.i_peak_a < p.i_base_a:
        raise ValueError("peak current must be at least the base current")
    if p.v_src <= 0 or p.v_set <= 0:
        raise ValueError("voltages must be positive")
    if p.n_bursts < 1:
        raise ValueError("simulate at least one burst")


def simulate(p: SimParams, max_points: int = 2000, max_steps: int = 400_000) -> SimResult:
    validate(p)
    r_out_eff = p.r_out if p.reg_kind != "none" else 0.001
    lag = p.reg_kind != "none"
    tau_reg = max(p.t_response_s, 1e-7)
    if lag:
        tau_out = (r_out_eff + p.esr_out) * p.c_out_f
        tau_in = (p.r_src + p.esr_in) * p.c_in_f if p.c_in_f > 0 else float("inf")
        loop_gain = 1 + p.r_src / r_out_eff        # source resistance feeds back through the regulator
        dt = 0.1 * min(tau_out, tau_in, p.burst_s / 5, tau_reg / loop_gain)
    else:
        e_out = max(p.esr_out, ESR_FLOOR)
        e_in = max(p.esr_in, ESR_FLOOR)
        tau_out = e_out * p.c_out_f
        tau_in = e_in * p.c_in_f if p.c_in_f > 0 else float("inf")
        dt = 0.1 * min(tau_out, tau_in, p.burst_s / 5)
    dt = min(max(dt, 20e-9), 2e-6)
    dur = p.duration_s
    steps = int(dur / dt)
    if steps > max_steps:
        dt = dur / max_steps
        steps = max_steps
    stride = max(steps // max_points, 1)

    has_cin = p.c_in_f > 0
    # start at steady state for the base load
    i_in = p.i_base_a + p.iq_a
    if p.reg_kind == "switcher":
        i_in = p.v_set * p.i_base_a / (p.efficiency * max(p.v_src, 0.5)) + p.iq_a
    v_cin = p.v_src - p.r_src * i_in
    i_reg = p.i_base_a
    if p.reg_kind == "ldo":
        ratio0 = min(p.i_base_a / p.i_rated_a, 1.5) if p.i_rated_a > 0 else 1.0
        v_t0 = max(min(p.v_set, v_cin - p.dropout_v * (0.75 + 0.25 * ratio0)), 0.0)
        v_cout = v_t0 - p.r_out * p.i_base_a
    elif p.reg_kind == "switcher":
        v_cout = p.v_set - p.r_out * p.i_base_a
    else:
        v_cout = v_cin

    t_list, vo_l, vi_l, il_l, ii_l = [], [], [], [], []
    v_out_min = float("inf")
    v_in_min = float("inf")
    below = 0.0
    nominal = None
    for n in range(steps + 1):
        t = n * dt
        i_load = _load_current(p, t)

        if not lag:
            # no regulator: source resistance feeds one node that holds both capacitors
            e_in = max(p.esr_in, ESR_FLOOR)
            e_out = max(p.esr_out, ESR_FLOOR)
            g = 1 / p.r_src + 1 / e_out + (1 / e_in if has_cin else 0.0)
            num = p.v_src / p.r_src - i_load + v_cout / e_out + (v_cin / e_in if has_cin else 0.0)
            v_node = max(num / g, 0.0)
            v_in = v_out = v_node
            i_in = (p.v_src - v_node) / p.r_src
            v_cout += dt * (v_node - v_cout) / (e_out * p.c_out_f)
            if has_cin:
                v_cin += dt * (v_node - v_cin) / (e_in * p.c_in_f)
        else:
            # input node terminal voltage (uses last step's regulator input current)
            if has_cin:
                v_in = (v_cin + p.esr_in * (p.v_src / p.r_src - i_in)) / (1 + p.esr_in / p.r_src)
            else:
                v_in = p.v_src - p.r_src * i_in
            v_in = max(v_in, 0.0)

            # regulator target
            if p.reg_kind == "ldo":
                ratio = min(i_reg / p.i_rated_a, 1.5) if p.i_rated_a > 0 else 1.0
                v_target = max(min(p.v_set, v_in - p.dropout_v * (0.75 + 0.25 * ratio)), 0.0)
            else:
                v_target = p.v_set if v_in >= p.vin_min else p.v_set * max(v_in, 0.0) / max(p.vin_min, 1e-6)

            # regulator output current with first-order response lag
            v_term = v_cout + p.esr_out * (i_reg - i_load)
            i_cmd = min(max((v_target - v_term) / r_out_eff, 0.0), p.i_limit_a)
            i_reg += dt / tau_reg * (i_cmd - i_reg)
            i_reg = min(max(i_reg, 0.0), p.i_limit_a)
            v_out = v_cout + p.esr_out * (i_reg - i_load)

            # current the regulator draws from its input
            if p.reg_kind == "switcher":
                i_in_new = v_out * i_reg / (p.efficiency * max(v_in, 0.5)) + p.iq_a
            else:
                i_in_new = i_reg + p.iq_a
            if has_cin:
                i_src = (p.v_src - v_in) / p.r_src
                v_cin += dt * (i_src - i_in_new) / p.c_in_f
            i_in = i_in_new
            v_cout += dt * (i_reg - i_load) / p.c_out_f

        if nominal is None:
            nominal = v_out
        v_out_min = min(v_out_min, v_out)
        v_in_min = min(v_in_min, v_in)
        if v_out < p.v_threshold:
            below += dt
        if n % stride == 0:
            t_list.append(t)
            vo_l.append(v_out)
            vi_l.append(v_in)
            il_l.append(i_load)
            ii_l.append(i_in)

    res = SimResult(p, t_list, vo_l, vi_l, il_l, ii_l, v_out_min, nominal or p.v_set,
                    v_in_min, below)
    _annotate(res)
    return res


def _annotate(res: SimResult) -> None:
    p = res.params
    step = p.i_peak_a - p.i_base_a
    esr_drop = step * p.esr_out
    src_drop = p.i_peak_a * p.r_src
    res.notes.append(("ok", f"Rail idles at {res.v_out_nominal:.2f} V; lowest point {res.v_out_min:.2f} V "
                            f"(dip of {res.droop_v * 1000:.0f} mV). Input node bottoms out at {res.v_in_min:.2f} V."))
    if p.reg_kind == "ldo":
        need = p.v_set + p.dropout_v
        if res.v_in_min < need:
            res.notes.append(("warn", f"The input dips to {res.v_in_min:.2f} V, below the {need:.2f} V the LDO needs "
                                      f"for {p.v_set:g} V out. The output follows the input down while it is in dropout."))
    if res.brownout:
        ms = res.time_below_s * 1000
        res.notes.append(("error", f"Brown-out: the rail falls below {p.v_threshold:g} V for about {ms:.2f} ms per run. "
                                   f"The chip would reset or glitch."))
    elif res.droop_v > 0.2 * p.v_set:
        res.notes.append(("warn", f"No brown-out at a {p.v_threshold:g} V threshold, but the dip is large "
                                  f"({res.droop_v:.2f} V). Little margin for cold, ageing or a flat battery."))
    else:
        res.notes.append(("ok", f"No brown-out: the lowest point is {res.v_out_min - p.v_threshold:.2f} V above "
                                f"the {p.v_threshold:g} V threshold."))
    if step > 0:
        res.notes.append(("ok", f"Where the dip comes from: output-cap ESR drops {esr_drop * 1000:.0f} mV instantly on the "
                                f"load step; source/cable resistance drops {src_drop * 1000:.0f} mV on the input at peak "
                                f"current."))


def suggest_output_cap(p: SimParams) -> tuple[int | None, SimResult | None]:
    """Smallest common output capacitance (uF) that avoids the brown-out, keeping other settings."""
    for uf in COMMON_CAP_UF:
        if uf * 1e-6 < p.c_out_f:
            continue
        r = simulate(replace(p, c_out_f=uf * 1e-6), max_points=400)
        if not r.brownout:
            return uf, r
    return None, None


def from_power_rail(project, rail_name: str) -> SimParams:
    """Build simulation parameters from a power-budget rail (see core.power)."""
    from . import power
    report = power.analyse(project)
    rr = report.by_name(rail_name)
    rail = rr.rail
    base = max(rr.avg_ma, 0.1) / 1000
    peak = max(rr.peak_ma / 1000, base)
    if rail.kind == "supply":
        return SimParams(v_src=rail.v_out, r_src=max(rail.r_internal_ohm, 0.01), reg_kind="none",
                         c_out_f=10e-6, esr_out=0.02, i_base_a=base, i_peak_a=peak, v_set=rail.v_out,
                         v_threshold=rail.v_out * 0.9)
    parent = next(r for r in project.rails if r.name == rail.parent)
    if parent.kind == "supply":
        src, r_src = parent.v_out, parent.r_internal_ohm
    else:                              # fed from another regulator: treat its output as a stiff source
        src, r_src = parent.v_out, 0.05
    base_p = SimParams(v_src=src, r_src=max(r_src, 0.01), v_set=rail.v_out, i_base_a=base, i_peak_a=peak,
                       i_rated_a=rail.max_ma / 1000, i_limit_a=max(rail.max_ma / 1000 * 1.3, peak),
                       iq_a=rail.iq_ma / 1000, v_threshold=max(rail.v_out * 0.9, 0.5))
    if rail.kind == "ldo":
        return replace(base_p, reg_kind="ldo", dropout_v=rail.dropout_v)
    return replace(base_p, reg_kind="switcher", efficiency=rail.efficiency,
                   vin_min=rail.min_vin if rail.kind == "boost" else rail.v_out + 0.5)
