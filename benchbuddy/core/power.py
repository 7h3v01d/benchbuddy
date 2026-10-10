"""Power budget engine.

Model
-----
A *project* is a tree of **rails**. Root rails are real supplies (USB port, battery,
wall adapter). Child rails are regulators (LDO / buck / boost) fed from a parent rail.
**Loads** (ESP32, servo, sensor ...) hang off a rail.

For every rail we work out
  * average current   - used for battery runtime and regulator heat
  * peak current      - used for sizing / brown-out checks (all peaks assumed to
                        coincide, which is deliberately conservative)
  * voltage sag at peak load from the source's internal / cable resistance
  * whether each regulator still has enough input voltage (dropout) at that sag
  * LDO dissipation and temperature rise
  * suggested bulk capacitance to ride through load steps
  * battery runtime
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .capacitors import bulk_cap_for_transient

OK, WARN, ERROR = "ok", "warn", "error"
_LEVEL_ORDER = {OK: 0, WARN: 1, ERROR: 2}

KINDS = ("supply", "ldo", "buck", "boost")
COMMON_CAP_UF = [10, 22, 47, 100, 220, 330, 470, 680, 1000, 2200, 4700]


# --------------------------------------------------------------------- data model

@dataclass
class Load:
    name: str
    rail: str
    qty: int = 1
    i_active_ma: float = 0.0     # typical current while running
    i_peak_ma: float = 0.0       # short bursts (Wi-Fi TX, motor start/stall ...)
    i_sleep_ma: float = 0.0      # current while idle / asleep
    duty: float = 1.0            # fraction of time spent active (0..1)

    @property
    def avg_ma(self) -> float:
        d = min(max(self.duty, 0.0), 1.0)
        return self.qty * (self.i_active_ma * d + self.i_sleep_ma * (1 - d))

    @property
    def peak_ma(self) -> float:
        return self.qty * max(self.i_peak_ma, self.i_active_ma)


@dataclass
class Rail:
    name: str
    v_out: float
    kind: str = "supply"             # supply | ldo | buck | boost
    parent: str | None = None
    max_ma: float = 1000.0           # output rating (supply limit or regulator rating)
    v_min: float | None = None       # supply only: lowest voltage in use (battery cut-off)
    r_internal_ohm: float = 0.0      # supply only: source + cable resistance
    capacity_mah: float | None = None
    dropout_v: float = 1.0           # ldo / buck: Vin must be >= Vout + dropout
    min_vin: float = 2.0             # boost: minimum input voltage
    efficiency: float = 0.9          # buck / boost
    iq_ma: float = 0.0               # regulator quiescent current
    theta_ja: float = 60.0           # degC/W, for LDO temperature estimate
    note: str = ""


@dataclass
class Project:
    rails: list[Rail] = field(default_factory=list)
    loads: list[Load] = field(default_factory=list)

    # -- persistence
    def to_dict(self) -> dict:
        return {"rails": [asdict(r) for r in self.rails],
                "loads": [asdict(l) for l in self.loads]}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls([Rail(**r) for r in d.get("rails", [])],
                   [Load(**l) for l in d.get("loads", [])])

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ------------------------------------------------------------------------ reports

@dataclass
class RailReport:
    rail: Rail
    own_avg_ma: float = 0.0          # loads attached directly to this rail
    own_peak_ma: float = 0.0
    avg_ma: float = 0.0              # total drawn from this rail's output
    peak_ma: float = 0.0
    in_avg_ma: float = 0.0           # what this rail draws from its parent
    in_peak_ma: float = 0.0
    util_peak_pct: float = 0.0
    util_avg_pct: float = 0.0
    v_peak_v: float | None = None    # supply: voltage at the terminals at peak load
    dissipation_avg_w: float = 0.0
    dissipation_peak_w: float = 0.0
    temp_rise_avg_c: float | None = None
    temp_rise_peak_c: float | None = None
    runtime_h: float | None = None
    suggested_bulk_uf: int | None = None
    messages: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        worst = OK
        for level, _ in self.messages:
            if _LEVEL_ORDER[level] > _LEVEL_ORDER[worst]:
                worst = level
        return worst

    def add(self, level: str, text: str) -> None:
        self.messages.append((level, text))


@dataclass
class PowerReport:
    rails: list[RailReport]

    @property
    def status(self) -> str:
        worst = OK
        for r in self.rails:
            if _LEVEL_ORDER[r.status] > _LEVEL_ORDER[worst]:
                worst = r.status
        return worst

    @property
    def ok(self) -> bool:
        return self.status != ERROR

    def by_name(self, name: str) -> RailReport:
        for r in self.rails:
            if r.rail.name == name:
                return r
        raise KeyError(name)


# -------------------------------------------------------------------- validation

class ProjectError(ValueError):
    pass


def validate(project: Project) -> None:
    names = [r.name for r in project.rails]
    if len(set(names)) != len(names):
        raise ProjectError("rail names must be unique")
    by_name = {r.name: r for r in project.rails}
    for r in project.rails:
        if r.kind not in KINDS:
            raise ProjectError(f"rail '{r.name}': unknown kind '{r.kind}'")
        if r.kind == "supply" and r.parent:
            raise ProjectError(f"rail '{r.name}': a supply can't have a parent")
        if r.kind != "supply":
            if not r.parent:
                raise ProjectError(f"rail '{r.name}': a {r.kind} regulator needs a parent rail")
            if r.parent not in by_name:
                raise ProjectError(f"rail '{r.name}': parent '{r.parent}' doesn't exist")
        if r.kind in ("buck", "boost") and not 0 < r.efficiency <= 1:
            raise ProjectError(f"rail '{r.name}': efficiency must be between 0 and 1")
    # cycles
    for r in project.rails:
        seen = set()
        cur = r
        while cur.parent:
            if cur.name in seen:
                raise ProjectError("rails form a loop")
            seen.add(cur.name)
            cur = by_name[cur.parent]
    for l in project.loads:
        if l.rail not in by_name:
            raise ProjectError(f"load '{l.name}': rail '{l.rail}' doesn't exist")


# ---------------------------------------------------------------------- analysis

def _round_up_cap(uf: float) -> int:
    for c in COMMON_CAP_UF:
        if c >= uf:
            return c
    return int(math.ceil(uf / 1000.0) * 1000)


def analyse(project: Project, usable_capacity: float = 0.8,
            step_response_s: float = 100e-6, allowed_droop_v: float = 0.15) -> PowerReport:
    """Run the full power budget. Raises ProjectError for structural problems."""
    validate(project)
    rails = {r.name: r for r in project.rails}
    children: dict[str, list[Rail]] = {r.name: [] for r in project.rails}
    for r in project.rails:
        if r.parent:
            children[r.parent].append(r)

    reports: dict[str, RailReport] = {}

    def solve(rail: Rail) -> RailReport:
        rep = RailReport(rail)
        own = [l for l in project.loads if l.rail == rail.name]
        rep.own_avg_ma = sum(l.avg_ma for l in own)
        rep.own_peak_ma = sum(l.peak_ma for l in own)
        avg, peak = rep.own_avg_ma, rep.own_peak_ma
        for child in children[rail.name]:
            cr = solve(child)
            avg += cr.in_avg_ma
            peak += cr.in_peak_ma
        rep.avg_ma, rep.peak_ma = avg, peak
        rep.util_avg_pct = avg / rail.max_ma * 100 if rail.max_ma else 0.0
        rep.util_peak_pct = peak / rail.max_ma * 100 if rail.max_ma else 0.0
        reports[rail.name] = rep

        parent = rails.get(rail.parent) if rail.parent else None
        p_nom = parent.v_out if parent else rail.v_out
        p_worst = (parent.v_min if parent and parent.v_min else p_nom) if parent else rail.v_out

        # ---- current drawn from the parent
        if rail.kind == "ldo":
            rep.in_avg_ma = avg + rail.iq_ma
            rep.in_peak_ma = peak + rail.iq_ma
            # an LDO in dropout passes Vin straight through: the headroom it burns can't go negative
            headroom = max(p_nom - rail.v_out, 0.0)
            rep.dissipation_avg_w = headroom * avg / 1000 + p_nom * rail.iq_ma / 1000
            rep.dissipation_peak_w = headroom * peak / 1000 + p_nom * rail.iq_ma / 1000
            if rail.theta_ja:
                rep.temp_rise_avg_c = rep.dissipation_avg_w * rail.theta_ja
                rep.temp_rise_peak_c = rep.dissipation_peak_w * rail.theta_ja
        elif rail.kind in ("buck", "boost"):
            eff = rail.efficiency
            rep.in_avg_ma = rail.v_out * avg / (eff * p_nom) + rail.iq_ma
            rep.in_peak_ma = rail.v_out * peak / (eff * max(p_worst, 0.1)) + rail.iq_ma
            rep.dissipation_avg_w = rail.v_out * avg / 1000 * (1 / eff - 1)
            rep.dissipation_peak_w = rail.v_out * peak / 1000 * (1 / eff - 1)
        else:
            rep.in_avg_ma, rep.in_peak_ma = avg, peak

        # ---- rating checks
        if rail.max_ma:
            if avg > rail.max_ma:
                rep.add(ERROR, f"Average draw {avg:.0f} mA is over the {rail.max_ma:.0f} mA rating "
                               f"({rep.util_avg_pct:.0f}%). This will overheat, current-limit or brown out.")
            elif peak > 1.5 * rail.max_ma:
                rep.add(ERROR, f"Peak draw {peak:.0f} mA is far above the {rail.max_ma:.0f} mA rating "
                               f"({rep.util_peak_pct:.0f}%). Expect resets / shutdown.")
            elif peak > rail.max_ma:
                rep.add(WARN, f"Peak draw {peak:.0f} mA exceeds the {rail.max_ma:.0f} mA rating "
                              f"({rep.util_peak_pct:.0f}%). Only OK if the bursts are very short and "
                              f"backed by bulk capacitance.")
            elif peak > 0.8 * rail.max_ma:
                rep.add(WARN, f"Peak draw {peak:.0f} mA is {rep.util_peak_pct:.0f}% of the "
                              f"{rail.max_ma:.0f} mA rating. Little headroom left.")
            else:
                rep.add(OK, f"Peak {peak:.0f} mA / avg {avg:.0f} mA of {rail.max_ma:.0f} mA "
                            f"({rep.util_peak_pct:.0f}% at peak). Healthy headroom.")

        # ---- source sag
        if rail.kind == "supply":
            v_worst = rail.v_min if rail.v_min else rail.v_out
            if rail.r_internal_ohm > 0:
                sag = peak / 1000 * rail.r_internal_ohm
                rep.v_peak_v = v_worst - sag
                sag_pct = sag / rail.v_out * 100
                lvl = ERROR if sag_pct > 10 else WARN if sag_pct > 5 else OK
                rep.add(lvl, f"Voltage at peak load ≈ {rep.v_peak_v:.2f} V "
                             f"(sag {sag:.2f} V = {sag_pct:.1f}% across {rail.r_internal_ohm:g} Ω source/cable "
                             f"resistance).")
            else:
                rep.v_peak_v = v_worst

        # ---- runtime
        if rail.kind == "supply" and rail.capacity_mah:
            if avg > 0:
                rep.runtime_h = rail.capacity_mah * usable_capacity / avg
                days = rep.runtime_h / 24
                extra = f" (≈ {days:.1f} days)" if rep.runtime_h >= 48 else ""
                rep.add(OK, f"Estimated runtime {rep.runtime_h:.1f} h{extra} using "
                            f"{usable_capacity * 100:.0f}% of {rail.capacity_mah:.0f} mAh.")

        # ---- regulator heat
        if rail.kind == "ldo" and rep.temp_rise_avg_c is not None:
            t = rep.temp_rise_avg_c
            msg = (f"Dissipates {rep.dissipation_avg_w * 1000:.0f} mW on average "
                   f"({rep.dissipation_peak_w * 1000:.0f} mW peak) → ≈ {t:.0f} °C above ambient "
                   f"(θJA {rail.theta_ja:g} °C/W).")
            lvl = ERROR if t > 90 else WARN if t > 50 else OK
            if lvl != OK:
                p_out = rail.v_out * avg / 1000
                p_buck = p_out * (1 / 0.9 - 1)
                msg += (f" A buck converter would waste only ≈ {p_buck * 1000:.0f} mW here.")
            rep.add(lvl, msg)
        elif rail.kind in ("buck", "boost") and rep.dissipation_avg_w > 0:
            rep.add(OK, f"Converter loss ≈ {rep.dissipation_avg_w * 1000:.0f} mW at {rail.efficiency * 100:.0f}% "
                        f"efficiency; draws {rep.in_avg_ma:.0f} mA avg / {rep.in_peak_ma:.0f} mA peak "
                        f"from '{rail.parent}'.")

        # ---- bulk capacitance
        step = peak - avg
        if step >= 100:
            uf = bulk_cap_for_transient(step / 1000, step_response_s, allowed_droop_v) * 1e6
            rep.suggested_bulk_uf = _round_up_cap(uf)
            rep.add(OK, f"Load steps of up to {step:.0f} mA: put ≥ {rep.suggested_bulk_uf} µF of low-ESR "
                        f"bulk capacitance close to the load on this rail (plus 100 nF per IC).")

        # ---- children: can they still work at our worst-case voltage?
        v_avail = rep.v_peak_v if rail.kind == "supply" and rep.v_peak_v is not None else (
            rail.v_min if rail.v_min else rail.v_out)
        for child in children[rail.name]:
            if child.kind in ("ldo", "buck"):
                need = child.v_out + child.dropout_v
                if v_avail < need:
                    rep.add(ERROR, f"'{child.name}' needs ≥ {need:.2f} V in "
                                   f"({child.v_out:g} V out + {child.dropout_v:g} V dropout) but this rail only "
                                   f"reaches {v_avail:.2f} V at peak load. Its output will droop → brown-out.")
                elif v_avail < need + 0.25:
                    rep.add(WARN, f"'{child.name}' has only {v_avail - need:.2f} V of input headroom above its "
                                  f"dropout at peak load ({v_avail:.2f} V available, {need:.2f} V needed).")
            elif child.kind == "boost":
                if v_avail < child.min_vin:
                    rep.add(ERROR, f"Boost '{child.name}' needs ≥ {child.min_vin:g} V in but this rail falls to "
                                   f"{v_avail:.2f} V at peak load.")
                if rail.v_out >= child.v_out:
                    rep.add(WARN, f"Boost '{child.name}' outputs {child.v_out:g} V, which isn't above its "
                                  f"{rail.v_out:g} V input; a boost converter can't regulate down.")
        return rep

    ordered: list[RailReport] = []

    def preorder(rail: Rail) -> None:
        ordered.append(reports[rail.name])
        for c in children[rail.name]:
            preorder(c)

    for root in [r for r in project.rails if r.kind == "supply"]:
        solve(root)
        preorder(root)
    return PowerReport(ordered)


# --------------------------------------------------------------------- wiring

AWG_OHM_PER_KM = {
    30: 338.6, 28: 213.2, 26: 133.8, 24: 84.2, 22: 52.96, 20: 33.31,
    18: 20.95, 16: 13.17, 14: 8.286, 12: 5.211, 10: 3.277,
}


def wire_drop(awg: int, length_m: float, amps: float, round_trip: bool = True) -> tuple[float, float]:
    """(volts dropped, loop resistance in ohms) for a copper wire run."""
    if awg not in AWG_OHM_PER_KM:
        raise ValueError(f"AWG {awg} not in table")
    r = AWG_OHM_PER_KM[awg] / 1000 * length_m * (2 if round_trip else 1)
    return amps * r, r


def smallest_awg_for_drop(length_m: float, amps: float, max_drop_v: float,
                          round_trip: bool = True) -> int | None:
    """Thinnest standard AWG that keeps the drop under max_drop_v (None if even AWG 10 fails)."""
    for awg in sorted(AWG_OHM_PER_KM, reverse=True):          # 30 (thin) ... 10 (thick)
        if wire_drop(awg, length_m, amps, round_trip)[0] <= max_drop_v:
            return awg
    return None


def wire_ampacity_a(awg: int) -> float:
    """Rough chassis-wiring ampacity (conservative)."""
    table = {30: 0.5, 28: 0.8, 26: 1.2, 24: 2.1, 22: 3.0, 20: 5.0, 18: 7.0, 16: 10.0, 14: 15.0, 12: 20.0, 10: 30.0}
    return table[awg]


# ------------------------------------------------------------------------- GPIO

GPIO_LIMITS = {
    "ESP32 / S2 / S3 / C3 (3.3 V)": dict(rec_ma=20, abs_ma=40, total_ma=None),
    "ESP8266 (3.3 V)": dict(rec_ma=12, abs_ma=12, total_ma=None),
    "Arduino Uno / Nano (ATmega328P, 5 V)": dict(rec_ma=20, abs_ma=40, total_ma=200),
    "Raspberry Pi Pico (RP2040, 3.3 V)": dict(rec_ma=12, abs_ma=12, total_ma=50),
}


def gpio_check(board: str, pin_currents_ma: list[float]) -> list[tuple[str, str]]:
    """Check per-pin and total GPIO current against typical datasheet limits."""
    lim = GPIO_LIMITS[board]
    out: list[tuple[str, str]] = []
    for i, ma in enumerate(pin_currents_ma, 1):
        if ma > lim["abs_ma"]:
            out.append((ERROR, f"Pin {i}: {ma:g} mA is above the absolute max of {lim['abs_ma']} mA. Use a transistor/MOSFET."))
        elif ma > lim["rec_ma"]:
            out.append((WARN, f"Pin {i}: {ma:g} mA is above the recommended {lim['rec_ma']} mA. Works but shortens life / droops the pin."))
        else:
            out.append((OK, f"Pin {i}: {ma:g} mA is fine (recommended ≤ {lim['rec_ma']} mA)."))
    total = sum(pin_currents_ma)
    if lim["total_ma"]:
        if total > lim["total_ma"]:
            out.append((ERROR, f"Total {total:g} mA exceeds the chip's {lim['total_ma']} mA total I/O limit."))
        else:
            out.append((OK, f"Total {total:g} mA of {lim['total_ma']} mA chip limit."))
    return out


# --------------------------------------------------------------- battery / duty

@dataclass
class DutyResult:
    avg_ma: float
    duty: float
    runtime_h: float | None
    runtime_days: float | None


def duty_cycle_average(active_ma: float, active_s: float, sleep_ma: float, sleep_s: float,
                       capacity_mah: float | None = None, usable: float = 0.8) -> DutyResult:
    """Average current for a wake / sleep cycle and optional battery runtime."""
    period = active_s + sleep_s
    if period <= 0:
        raise ValueError("cycle time must be positive")
    avg = (active_ma * active_s + sleep_ma * sleep_s) / period
    hours = capacity_mah * usable / avg if capacity_mah and avg > 0 else None
    return DutyResult(avg, active_s / period, hours, hours / 24 if hours is not None else None)


def ohms_law(v: float | None = None, i: float | None = None, r: float | None = None,
             p: float | None = None) -> dict[str, float]:
    """Solve V, I, R, P from any two known values."""
    known = {k: x for k, x in dict(v=v, i=i, r=r, p=p).items() if x is not None}
    if len(known) != 2:
        raise ValueError("enter exactly two of V, I, R, P")
    keys = set(known)
    if keys == {"v", "i"}:
        v, i = known["v"], known["i"]
        r, p = v / i, v * i
    elif keys == {"v", "r"}:
        v, r = known["v"], known["r"]
        i, p = v / r, v * v / r
    elif keys == {"v", "p"}:
        v, p = known["v"], known["p"]
        i, r = p / v, v * v / p
    elif keys == {"i", "r"}:
        i, r = known["i"], known["r"]
        v, p = i * r, i * i * r
    elif keys == {"i", "p"}:
        i, p = known["i"], known["p"]
        v, r = p / i, p / (i * i)
    else:  # r, p
        r, p = known["r"], known["p"]
        v, i = math.sqrt(p * r), math.sqrt(p / r)
    return {"v": v, "i": i, "r": r, "p": p}
