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

import bisect
import math
from dataclasses import dataclass, field, replace

from .validation import DomainError, finite_all, integer, number

COMMON_CAP_UF = [10, 22, 47, 100, 220, 330, 470, 680, 1000, 2200, 4700]


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
    # measured load: when set, replaces the base/burst model (t from 0, amps)
    profile_t: tuple[float, ...] | None = None
    profile_i: tuple[float, ...] | None = None
    profile_label: str = ""

    @property
    def has_profile(self) -> bool:
        return bool(self.profile_t)

    @property
    def duration_s(self) -> float:
        if self.has_profile:
            return self.profile_t[-1]
        return self.t_first_s + self.n_bursts * self.period_s


def with_profile(p: SimParams, t: list[float], i: list[float], label: str = "measured") -> SimParams:
    """Drive the simulation with a measured current waveform instead of square bursts."""
    if len(t) != len(i) or len(t) < 2:
        raise SimulationError("a profile needs at least 2 (time, current) points of equal length")
    t = finite_all("profile time", t, SimulationError)
    i = finite_all("profile current", i, SimulationError)
    # small negative readings are meter noise around zero; big ones mean a reversed shunt
    noise = max(0.05 * max(i), 0.001)
    if min(i) < -noise:
        raise SimulationError(f"profile has currents down to {min(i) * 1000:.0f} mA: is the shunt wired backwards?")
    t0 = t[0]
    tt = tuple(x - t0 for x in t)
    ii = tuple(max(x, 0.0) for x in i)
    return replace(p, profile_t=tt, profile_i=ii, profile_label=label, i_base_a=min(ii), i_peak_a=max(ii))


def without_profile(p: SimParams) -> SimParams:
    return replace(p, profile_t=None, profile_i=None, profile_label="")


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
    if p.profile_t:
        k = bisect.bisect_right(p.profile_t, t) - 1      # sample-and-hold between measured points
        return p.profile_i[min(max(k, 0), len(p.profile_i) - 1)]
    # only the requested bursts: the window ends exactly where burst n+1 would begin, and a
    # rounding-dependent final sample must not simulate a sliver of a burst nobody asked for
    if p.t_first_s <= t < p.t_first_s + p.n_bursts * p.period_s:
        phase = (t - p.t_first_s) % p.period_s
        if phase < p.burst_s:
            return p.i_peak_a
    return p.i_base_a


class SimulationError(DomainError):
    """Parameters the simulator can't model. The GUI shows these instead of crashing."""


def validate(p: SimParams) -> None:
    """Exhaustive, finite-aware parameter check: nothing invalid reaches the integrator."""
    E = SimulationError
    if p.reg_kind not in ("ldo", "switcher", "none"):
        raise E("regulator type must be ldo, switcher or none")
    number("source voltage", p.v_src, gt=0, le=1000, error=E)
    number("source resistance", p.r_src, gt=0, le=1e6, error=E)
    number("input capacitance", p.c_in_f, ge=0, le=10, error=E)
    number("input cap ESR", p.esr_in, ge=0, le=1e3, error=E)
    number("output voltage", p.v_set, gt=0, le=1000, error=E)
    number("LDO dropout", p.dropout_v, ge=0, le=100, error=E)
    number("rated current", p.i_rated_a, gt=0, le=1000, error=E)
    number("regulator output impedance", p.r_out, gt=0, le=1e6, error=E)
    number("response time", p.t_response_s, gt=0, le=1, error=E)
    number("current limit", p.i_limit_a, gt=0, le=1000, error=E)
    number("quiescent current", p.iq_a, ge=0, le=100, error=E)
    number("switcher efficiency", p.efficiency, gt=0, le=1, error=E)
    number("switcher minimum input", p.vin_min, ge=0, le=1000, error=E)
    number("output capacitance", p.c_out_f, ge=1e-12, le=10, error=E)
    number("output cap ESR", p.esr_out, ge=0, le=1e3, error=E)
    number("brown-out threshold", p.v_threshold, ge=0, le=1000, error=E)
    if p.profile_t is not None or p.profile_i is not None:
        if not p.profile_t or not p.profile_i or len(p.profile_t) != len(p.profile_i) or len(p.profile_t) < 2:
            raise E("measured profile is empty or malformed")
        ts = finite_all("profile time", p.profile_t, E)
        cs = finite_all("profile current", p.profile_i, E)
        if any(b < a for a, b in zip(ts, ts[1:])) or ts[-1] <= 0:
            raise E("measured profile times must increase")
        if min(cs) < 0 or max(cs) > 1000:
            raise E("measured profile currents must be between 0 and 1000 A")
        if ts[-1] > 120:
            raise E("measured profile is longer than 120 s: select a shorter stretch")
    else:
        number("base current", p.i_base_a, ge=0, le=1000, error=E)
        number("burst peak current", p.i_peak_a, ge=0, le=1000, error=E)
        number("burst length", p.burst_s, gt=0, error=E)
        number("burst period", p.period_s, gt=0, error=E)
        number("first burst time", p.t_first_s, ge=0, error=E)
        integer("number of bursts", p.n_bursts, ge=1, le=10_000, error=E)
        if p.period_s <= p.burst_s:
            raise E("burst length must be shorter than the period")
        if p.i_peak_a < p.i_base_a:
            raise E("peak current must be at least the base current")
        if p.duration_s > 120:
            raise E(f"that's {p.duration_s:.0f} s of simulated time: keep it under 120 s")


def simulate(p: SimParams, max_points: int = 2000, max_steps: int = 400_000) -> SimResult:
    validate(p)
    r_out_eff = p.r_out if p.reg_kind != "none" else 0.001
    lag = p.reg_kind != "none"
    tau_reg = max(p.t_response_s, 1e-7)
    if lag:
        tau_out = (r_out_eff + p.esr_out) * p.c_out_f
        tau_in = (p.r_src + p.esr_in) * p.c_in_f if p.c_in_f > 0 else float("inf")
        loop_gain = 1 + p.r_src / r_out_eff        # source resistance feeds back through the regulator
        dt = 0.1 * min(tau_out, tau_in, _event_scale(p), tau_reg / loop_gain)
    else:
        tau_out = max(p.esr_out, 1e-3) * p.c_out_f
        tau_in = max(p.esr_in, 1e-3) * p.c_in_f if p.c_in_f > 0 else float("inf")
        dt = 0.1 * min(tau_out, tau_in, _event_scale(p))
    # dt is chosen for accuracy only: every update below is implicit (backward Euler or an exact
    # exponential), so it stays stable even when a time constant is far shorter than the step.
    dt = min(max(dt, 20e-9), 2e-6)
    dur = p.duration_s
    steps = int(dur / dt)
    if steps > max_steps:
        dt = dur / max_steps
        steps = max_steps
    stride = max(steps // max_points, 1)

    has_cin = p.c_in_f > 0
    # start at steady state for the load at t = 0
    i0 = _load_current(p, 0.0) if p.has_profile else p.i_base_a
    i_in = i0 + p.iq_a
    if p.reg_kind == "switcher":
        i_in = p.v_set * i0 / (p.efficiency * max(p.v_src, 0.5)) + p.iq_a
    v_cin = p.v_src - p.r_src * i_in
    i_reg = i0
    if p.reg_kind == "ldo":
        ratio0 = min(i0 / p.i_rated_a, 1.5) if p.i_rated_a > 0 else 1.0
        v_t0 = max(min(p.v_set, v_cin - p.dropout_v * (0.75 + 0.25 * ratio0)), 0.0)
        v_cout = v_t0 - p.r_out * i0
    elif p.reg_kind == "switcher":
        v_cout = p.v_set - p.r_out * i0
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
            # No regulator: the source resistance feeds one node holding both capacitors.
            # Backward Euler: each cap (ESR + C) looks like a conductance 1/(ESR + dt/C) to the
            # node from its old voltage, which gives the node voltage in closed form.
            go = 1 / (p.esr_out + dt / p.c_out_f)
            gi = 1 / (p.esr_in + dt / p.c_in_f) if has_cin else 0.0
            num = p.v_src / p.r_src - i_load + go * v_cout + gi * v_cin
            v_node = max(num / (1 / p.r_src + go + gi), 0.0)
            v_in = v_out = v_node
            i_in = (p.v_src - v_node) / p.r_src
            v_cout = max(v_cout + go * (v_node - v_cout) * dt / p.c_out_f, 0.0)
            if has_cin:
                v_cin = max(v_cin + gi * (v_node - v_cin) * dt / p.c_in_f, 0.0)
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

            # Regulator current lags its command (tau_reg) and charges the output cap. Both are
            # linear, so take the backward-Euler step for the pair in closed form; if the command
            # would sit outside [0, current limit], redo the step with it saturated there.
            k, a = dt / p.c_out_f, dt / tau_reg
            esr = p.esr_out
            i_new = ((i_reg + a * (v_target - v_cout + (k + esr) * i_load) / r_out_eff)
                     / (1 + a + a * (k + esr) / r_out_eff))
            v_c_new = v_cout + k * (i_new - i_load)
            i_cmd = (v_target - v_c_new - esr * (i_new - i_load)) / r_out_eff
            if i_cmd > p.i_limit_a:
                i_new = (i_reg + a * p.i_limit_a) / (1 + a)
            elif i_cmd < 0:
                i_new = i_reg / (1 + a)
            i_reg = min(max(i_new, 0.0), p.i_limit_a)
            v_out = v_cout + k * (i_reg - i_load) + esr * (i_reg - i_load)

            # current the regulator draws from its input
            if p.reg_kind == "switcher":
                i_in_new = v_out * i_reg / (p.efficiency * max(v_in, 0.5)) + p.iq_a
            else:
                i_in_new = i_reg + p.iq_a
            if has_cin:
                # input cap relaxes towards (Vsrc − R·Iin) with tau = (R + ESR)·C: exact update
                target = p.v_src - p.r_src * i_in_new
                tau_c = (p.r_src + p.esr_in) * p.c_in_f
                v_cin = max(target + (v_cin - target) * math.exp(-dt / tau_c), 0.0)
            i_in = i_in_new
            # Nothing in this topology can drive the rail negative: once the load has pulled the
            # output cap flat, the rail sits at 0 V (the chip is long since in brown-out).
            v_cout = max(v_cout + k * (i_reg - i_load), 0.0)
            v_out = max(v_out, 0.0)

        if nominal is None:
            nominal = v_out
        v_out_min = min(v_out_min, v_out)
        v_in_min = min(v_in_min, v_in)
        if v_out < p.v_threshold and n < steps:          # count intervals, not sample points
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


def _event_scale(p: SimParams) -> float:
    """Shortest load feature the time step must resolve."""
    if p.has_profile:
        gaps = [b - a for a, b in zip(p.profile_t, p.profile_t[1:]) if b > a]
        return max(min(gaps) if gaps else p.profile_t[-1], 1e-6)
    return p.burst_s / 5


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
    if p.has_profile:
        res.notes.append(("ok", f"Load is a measured trace ({p.profile_label or 'capture'}: "
                                f"{len(p.profile_t)} points over {p.profile_t[-1] * 1000:.1f} ms, "
                                f"{p.i_base_a * 1000:.0f}–{p.i_peak_a * 1000:.0f} mA)."))
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
                   vin_min=rail.min_vin if rail.kind == "boost" else rail.v_out + rail.dropout_v)
