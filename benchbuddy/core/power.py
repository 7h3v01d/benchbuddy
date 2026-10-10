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
from .validation import DomainError, integer, non_negative, number, positive, reject_json_constant, text

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
    output_disconnect: bool = False  # boost: True = output switched off when it stops (synchronous parts
                                     # with true disconnect); False = diode path, Vin − 0.4 V flows through


@dataclass
class Project:
    rails: list[Rail] = field(default_factory=list)
    loads: list[Load] = field(default_factory=list)

    # -- persistence
    def to_dict(self) -> dict:
        return {"rails": [asdict(r) for r in self.rails],
                "loads": [asdict(l) for l in self.loads]}

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "Project":
        """Build a project from saved data. Raises ProjectError.

        Malformed data (wrong types, NaN/infinity, unknown fields) is always rejected.
        strict=True also rejects physically impossible values; the GUI opens with
        strict=False so a half-finished project can be loaded and fixed.
        """
        if not isinstance(d, dict):
            raise ProjectError("project file must contain an object with 'rails' and 'loads'")
        rails_in, loads_in = d.get("rails", []), d.get("loads", [])
        if not isinstance(rails_in, list) or not isinstance(loads_in, list):
            raise ProjectError("'rails' and 'loads' must be lists")
        try:
            rails = [Rail(**r) for r in rails_in]
            loads = [Load(**l) for l in loads_in]
        except TypeError as exc:          # unknown / missing fields, or a non-object entry
            raise ProjectError(f"project file has an unexpected entry: {exc}") from None
        proj = cls(rails, loads)
        check_types(proj)
        if strict:
            validate(proj)
        return proj

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, strict: bool = True) -> "Project":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_json_constant)
        except json.JSONDecodeError as exc:
            raise ProjectError(f"not a valid project file: {exc}") from None
        except DomainError as exc:
            raise ProjectError(str(exc)) from None
        return cls.from_dict(data, strict=strict)


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
    v_peak_v: float | None = None    # voltage this rail actually delivers at peak load
    v_in_peak_v: float | None = None # regulators: voltage at their input at peak load
    regulating: bool = True          # regulators: holds its set voltage at peak load
    reason: str = ""                 # why not, at peak load: dropout | uvlo | hiccup | overinput
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

class ProjectError(DomainError):
    """Structural or numeric problem with a project: it can't be analysed as given."""


def _check_rail(r: Rail) -> None:
    E = ProjectError
    text("rail name", r.name, error=E)
    who = f"rail '{r.name}'"
    if r.kind not in KINDS:
        raise E(f"{who}: unknown kind '{r.kind}'")
    number(f"{who}: output voltage", r.v_out, gt=0, error=E)
    number(f"{who}: current rating", r.max_ma, gt=0, error=E)
    number(f"{who}: quiescent current", r.iq_ma, ge=0, error=E)
    number(f"{who}: dropout", r.dropout_v, ge=0, error=E)
    number(f"{who}: minimum input", r.min_vin, ge=0, error=E)
    number(f"{who}: θJA", r.theta_ja, ge=0, error=E)
    number(f"{who}: efficiency", r.efficiency, gt=0, le=1, error=E)
    number(f"{who}: source resistance", r.r_internal_ohm, ge=0, error=E)
    number(f"{who}: capacity", r.capacity_mah, gt=0, allow_none=True, error=E)
    vmin = number(f"{who}: minimum voltage", r.v_min, gt=0, allow_none=True, error=E)
    if r.kind == "supply" and vmin is not None and vmin > r.v_out:
        raise E(f"{who}: minimum voltage {vmin:g} V is above its {r.v_out:g} V output")
    if r.parent is not None and not isinstance(r.parent, str):
        raise E(f"{who}: parent must be a rail name")


def _check_load(l: Load) -> None:
    E = ProjectError
    text("load name", l.name, error=E)
    who = f"load '{l.name}'"
    integer(f"{who}: quantity", l.qty, ge=1, le=100_000, error=E)
    number(f"{who}: active current", l.i_active_ma, ge=0, error=E)
    number(f"{who}: peak current", l.i_peak_ma, ge=0, error=E)
    number(f"{who}: sleep current", l.i_sleep_ma, ge=0, error=E)
    number(f"{who}: duty", l.duty, ge=0, le=1, error=E)
    if not isinstance(l.rail, str):
        raise E(f"{who}: rail must be a rail name")


_RAIL_NUMS = ("v_out", "max_ma", "r_internal_ohm", "dropout_v", "min_vin", "efficiency", "iq_ma", "theta_ja")
_RAIL_OPT = ("v_min", "capacity_mah")
_LOAD_NUMS = ("i_active_ma", "i_peak_ma", "i_sleep_ma", "duty")


def check_types(project: Project) -> None:
    """Every field has a usable type and every number is finite (no ranges checked)."""
    E = ProjectError
    for r in project.rails:
        text("rail name", r.name, error=E)
        if not isinstance(r.kind, str):
            raise E(f"rail '{r.name}': kind must be text")
        if r.parent is not None and not isinstance(r.parent, str):
            raise E(f"rail '{r.name}': parent must be a rail name")
        if not isinstance(r.note, str):
            raise E(f"rail '{r.name}': note must be text")
        if not isinstance(r.output_disconnect, bool):
            raise E(f"rail '{r.name}': output_disconnect must be true or false")
        for f in _RAIL_NUMS:
            number(f"rail '{r.name}': {f}", getattr(r, f), error=E)
        for f in _RAIL_OPT:
            number(f"rail '{r.name}': {f}", getattr(r, f), allow_none=True, error=E)
    for l in project.loads:
        text("load name", l.name, error=E)
        if not isinstance(l.rail, str):
            raise E(f"load '{l.name}': rail must be a rail name")
        integer(f"load '{l.name}': quantity", l.qty, error=E)
        for f in _LOAD_NUMS:
            number(f"load '{l.name}': {f}", getattr(l, f), error=E)


def validate(project: Project) -> None:
    """Reject anything the engine can't model: bad topology *and* impossible numbers."""
    for r in project.rails:
        _check_rail(r)
    for l in project.loads:
        _check_load(l)
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


@dataclass
class _Op:
    """Operating point of one rail in one load case (average or peak)."""
    i_out: float = 0.0          # mA delivered from the rail's output
    i_in: float = 0.0           # mA drawn from the parent (supply: = i_out)
    v_in: float | None = None   # V at the regulator's input (None for a supply)
    v_out: float = 0.0          # V actually delivered
    regulating: bool = True
    reason: str = ""            # "dropout" | "uvlo" | "hiccup" | "overinput" when not regulating
    on: bool = True             # boost: switching (False = stopped; a diode-path boost still passes Vin − Vf)
    over: bool = False          # boost: input too high to step up from, so it isn't switching


_VIN_FLOOR = 0.1               # a boost needs at least 10 % of its root source's voltage to run
_GRID = 512                    # coarse scan for the supply's operating point, then bisection
BOOST_DIODE_V = 0.4            # Schottky drop of a non-synchronous boost's rectifier (the bypass path)


def _nominal(r: Rail, peak: bool) -> float:
    return (r.v_min if peak and r.v_min else r.v_out) if r.kind == "supply" else r.v_out


def _boost_threshold(r: Rail, rails: dict[str, Rail], peak: bool) -> float:
    return max(r.min_vin, _VIN_FLOOR * _nominal(_source_of(r, rails), peak))


def _buck_ratio(r: Rail, vin: float) -> float:
    """Iin/Iout of a buck: Vset/(η·Vin) while regulating, rising to 1 (pass-through at 100 %
    duty) as Vin falls. Continuous, never above 1, and never decreasing as Vin drops."""
    return min(1.0, r.v_out / (r.efficiency * vin)) if vin > 0 else 1.0


def _boost_ratio(r: Rail, vin: float) -> float:
    """Iin/Iout of a switching boost: Vset/(η·Vin), never below 1. A boost's input current is its
    inductor current and only part of that reaches the output, so it can never draw less than it
    delivers - the floor stops a boost near Vin ≈ Vout from looking like a buck."""
    return max(1.0, r.v_out / (r.efficiency * vin)) if vin > 0 else 1.0


def _evaluate(sub: list[Rail], rails: dict[str, Rail], children: dict[str, list[Rail]],
              loads_on: dict[str, list[Load]], ops: dict[str, _Op], v_term: float, peak: bool,
              forced_off: set[str]) -> float:
    """Given the supply's terminal voltage, set every voltage and current below it; return
    the supply current (mA). Below a supply nothing has series resistance, so this is exact."""
    root = sub[0]
    ops[root.name].v_out = v_term
    for r in sub[1:]:                                           # voltages, top-down
        op = ops[r.name]
        vin = ops[r.parent].v_out
        op.v_in = vin
        if r.kind in ("ldo", "buck"):
            op.v_out = max(min(r.v_out, vin - r.dropout_v), 0.0)
        else:
            # A boost only steps up. Stopped (input under its minimum, hiccuping, or input already at or
            # above the set point) a diode-path boost passes Vin − Vf through its rectifier and a
            # true-disconnect boost delivers nothing; it never regulates down to Vset.
            through = 0.0 if r.output_disconnect else max(vin - BOOST_DIODE_V, 0.0)
            can_run = r.name not in forced_off and vin >= _boost_threshold(r, rails, peak)
            op.over = can_run and (vin >= r.v_out if r.output_disconnect else through > r.v_out)
            op.on = can_run and not op.over
            op.v_out = r.v_out if op.on else through
    for r in reversed(sub):                                     # currents, bottom-up
        op = ops[r.name]
        own = sum(l.peak_ma if peak else l.avg_ma for l in loads_on[r.name])
        op.i_out = own + sum(ops[c.name].i_in for c in children[r.name])
        if r.kind == "supply":
            op.i_in = op.i_out
        elif r.kind == "ldo":
            op.i_in = op.i_out + r.iq_ma
        elif r.kind == "buck":
            op.i_in = _buck_ratio(r, op.v_in) * op.i_out + r.iq_ma
        elif op.on:                                             # boost, switching
            op.i_in = _boost_ratio(r, op.v_in) * op.i_out + r.iq_ma
        elif not r.output_disconnect:                           # stopped, load fed through the diode
            # (constant-current loads keep drawing even if the rail has collapsed to 0 V, exactly as
            # through an LDO or a buck at 100 % duty; cutting the current at Vin = Vf would be a jump)
            op.i_in = op.i_out + r.iq_ma
        else:                                                   # stopped with output disconnected
            op.i_in = r.iq_ma
    return ops[root.name].i_out


def _solve(order: list[Rail], rails: dict[str, Rail], children: dict[str, list[Rail]],
           loads_on: dict[str, list[Load]], peak: bool) -> tuple[dict[str, _Op], bool]:
    """Operating point of every rail: for each supply solve  V = Vnom − R·I(V).

    I(V) is the exact draw of the whole subtree when the supply sits at V (constant-current
    loads; LDOs pass current; bucks draw Vset·Iout/(η·Vin), capped at Iout once they hit
    100 % duty, with Vout = Vin − dropout below regulation;
    boosts draw max(1, Vset/(η·Vin))·Iout while stepping up, Iout through the diode when stopped,
    and only Iq with the output disconnected). The
    highest root is the operating point a powered-up circuit settles into. It is bracketed on
    a grid and bisected, so it always converges. If the only sign change is a jump where a
    boost switches on - on, it drags V below its own threshold; off, V recovers - there is no
    steady state: the boost hiccups, and it is solved again with that boost off.
    """
    ops = {r.name: _Op(v_out=_nominal(r, peak)) for r in order}
    subtree: dict[str, list[Rail]] = {}
    for r in order:
        subtree.setdefault(_source_of(r, rails).name, []).append(r)
    hiccup: set[str] = set()
    for root_name, sub in subtree.items():
        root = rails[root_name]
        vn, res = _nominal(root, peak), root.r_internal_ohm
        while True:
            def g(v: float) -> float:
                return v - vn + res * _evaluate(sub, rails, children, loads_on, ops, v, peak, hiccup) / 1000
            if res == 0 or g(vn) <= 0:
                v_star = vn
            else:
                prev, v_star = vn, 0.0
                found = False
                for k in range(1, _GRID + 1):
                    v = vn * (1 - k / _GRID)
                    if g(v) <= 0:
                        lo, hi = v, prev                        # g(lo) <= 0 < g(hi)
                        for _ in range(80):
                            mid = 0.5 * (lo + hi)
                            if g(mid) <= 0:
                                lo = mid
                            else:
                                hi = mid
                        v_star, found = lo, True
                        break
                    prev = v
                if found:
                    gap = abs(g(hi) - g(lo))
                    if gap > 1e-6 * max(vn, 1.0):                # a jump, not a zero crossing
                        g(hi)                                   # state just above the jump
                        # Only a boost switching on/off makes g jump; the one(s) sitting on
                        # their threshold at the jump are the ones that can't stay on.
                        running = [r for r in sub if r.kind == "boost" and r.name not in hiccup
                                   and ops[r.name].on]
                        band = 1e-6 + 10 * (hi - lo)
                        culprits = {r.name for r in running
                                    if ops[r.name].v_in - _boost_threshold(r, rails, peak) <= band}
                        if not culprits and running:
                            culprits = {min(running, key=lambda r: ops[r.name].v_in
                                            - _boost_threshold(r, rails, peak)).name}
                        if culprits:
                            hiccup |= culprits
                            continue                            # solve again with them off
            _evaluate(sub, rails, children, loads_on, ops, v_star, peak, hiccup)
            break

    for r in order:                                             # verdicts
        op = ops[r.name]
        if r.kind == "supply":
            continue
        vin = op.v_in if op.v_in is not None else 0.0
        if r.kind in ("ldo", "buck"):
            ok = vin >= r.v_out + r.dropout_v - 1e-6
            op.regulating, op.reason = ok, "" if ok else "dropout"
        elif r.name in hiccup:
            op.regulating, op.reason = False, "hiccup"
        elif op.over:
            op.regulating, op.reason = False, "overinput"
        elif not op.on:
            op.regulating, op.reason = False, "uvlo"
        else:
            op.regulating, op.reason = True, ""
    return ops, True


def _stopped_output(rail: Rail, op: _Op) -> str:
    if rail.output_disconnect:
        return " Its output is disconnected, so this rail is dead."
    return (f" Its diode still passes ≈ {op.v_out:.2f} V straight through (set point {rail.v_out:g} V), "
            f"so everything on this rail runs under-voltage.")


def _source_of(r: Rail, rails: dict[str, Rail]) -> Rail:
    while r.parent:
        r = rails[r.parent]
    return r


def analyse(project: Project, usable_capacity: float = 0.8,
            step_response_s: float = 100e-6, allowed_droop_v: float = 0.15) -> PowerReport:
    """Run the full power budget. Raises ProjectError for anything it can't model."""
    validate(project)
    number("usable capacity", usable_capacity, gt=0, le=1, error=ProjectError)
    number("step response", step_response_s, gt=0, error=ProjectError)
    number("allowed droop", allowed_droop_v, gt=0, error=ProjectError)
    rails = {r.name: r for r in project.rails}
    children: dict[str, list[Rail]] = {r.name: [] for r in project.rails}
    for r in project.rails:
        if r.parent:
            children[r.parent].append(r)
    loads_on: dict[str, list[Load]] = {r.name: [] for r in project.rails}
    for l in project.loads:
        loads_on[l.rail].append(l)

    order: list[Rail] = []

    def preorder(rail: Rail) -> None:
        order.append(rail)
        for c in children[rail.name]:
            preorder(c)

    for root in [r for r in project.rails if r.kind == "supply"]:
        preorder(root)

    avg_ops, avg_ok = _solve(order, rails, children, loads_on, peak=False)
    pk_ops, pk_ok = _solve(order, rails, children, loads_on, peak=True)

    reports: list[RailReport] = []
    for rail in order:
        a, pk = avg_ops[rail.name], pk_ops[rail.name]
        rep = RailReport(rail)
        own = loads_on[rail.name]
        rep.own_avg_ma = sum(l.avg_ma for l in own)
        rep.own_peak_ma = sum(l.peak_ma for l in own)
        avg, peak = a.i_out, pk.i_out
        rep.avg_ma, rep.peak_ma = avg, peak
        rep.in_avg_ma, rep.in_peak_ma = a.i_in, pk.i_in
        rep.util_avg_pct = avg / rail.max_ma * 100
        rep.util_peak_pct = peak / rail.max_ma * 100
        rep.v_peak_v = pk.v_out
        rep.v_in_peak_v = pk.v_in
        rep.regulating, rep.reason = pk.regulating, pk.reason

        if rail.kind == "ldo":
            vin_a, vin_p = a.v_in or 0.0, pk.v_in or 0.0
            rep.dissipation_avg_w = max(vin_a - a.v_out, 0.0) * avg / 1000 + vin_a * rail.iq_ma / 1000
            rep.dissipation_peak_w = max(vin_p - pk.v_out, 0.0) * peak / 1000 + vin_p * rail.iq_ma / 1000
            if rail.theta_ja:
                rep.temp_rise_avg_c = rep.dissipation_avg_w * rail.theta_ja
                rep.temp_rise_peak_c = rep.dissipation_peak_w * rail.theta_ja
        elif rail.kind in ("buck", "boost"):
            # power in − power out at the solved operating point: switching loss while regulating,
            # the diode drop in pass-through, ~nothing when off
            rep.dissipation_avg_w = max((a.v_in or 0.0) * a.i_in - a.v_out * a.i_out, 0.0) / 1000
            rep.dissipation_peak_w = max((pk.v_in or 0.0) * pk.i_in - pk.v_out * pk.i_out, 0.0) / 1000

        # ---- can this regulator actually hold its output?
        bad = pk if not pk.regulating else a           # over-input is worst at light load
        if rail.kind != "supply" and not bad.regulating:
            if bad is a:
                when = "at average load" if bad.reason == "overinput" else "even at average load"
            else:
                when = "even at average load" if not a.regulating else "at peak load"
            if bad.reason == "overinput":
                v_in = bad.v_in or 0.0
                if rail.output_disconnect:
                    rep.add(ERROR, f"Its input is {v_in:.2f} V {when}, not below the {rail.v_out:g} V it's set to, "
                                   f"and a boost can only step up. With output disconnect, BenchBuddy treats it as "
                                   f"off: 0 V on this rail. (Some synchronous parts pass the input through or switch "
                                   f"to a down-mode instead; check the datasheet, and untick Disconnect for a part "
                                   f"that passes Vin through.)")
                else:
                    sev = ERROR if bad.v_out > rail.v_out * 1.05 else WARN
                    rep.add(sev, f"Its input is {v_in:.2f} V {when}, above the {rail.v_out:g} V it's set to: a "
                                 f"boost can only step up, so it stops switching and its diode passes ≈ "
                                 f"{bad.v_out:.2f} V straight through. Everything on this rail sees that voltage.")
            elif bad.reason == "hiccup":
                src = _source_of(rail, rails)
                p_need = rail.v_out * peak / 1000 / rail.efficiency
                v0 = _nominal(src, True)
                limit = (f" '{src.name}' can deliver at most ≈ {v0 * v0 / (4 * src.r_internal_ohm):.2f} W through its "
                         f"{src.r_internal_ohm:g} Ω source resistance, and this boost needs ≈ {p_need:.2f} W."
                         if src.r_internal_ohm > 0 else "")
                rep.add(ERROR, f"No stable operating point {when}: switching on pulls its own input below "
                               f"{_boost_threshold(rail, rails, True):.2f} V, so it shuts down and restarts (hiccups)."
                               f"{limit}{_stopped_output(rail, bad)}")
            elif bad.reason == "uvlo":
                rep.add(ERROR, f"Its input falls to {bad.v_in:.2f} V {when}, below the "
                               f"{_boost_threshold(rail, rails, True):.2f} V it needs to run: the boost is off."
                               f"{_stopped_output(rail, bad)}")
            else:
                rep.add(ERROR, f"Can't hold {rail.v_out:g} V {when}: its input only reaches {bad.v_in:.2f} V "
                               f"(needs {rail.v_out + rail.dropout_v:.2f} V), so the output sags to ≈ "
                               f"{bad.v_out:.2f} V. Everything on this rail will brown out.")

        # ---- rating checks
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
        if rail.kind == "supply" and rail.r_internal_ohm > 0:
            sag = _nominal(rail, True) - pk.v_out
            sag_pct = sag / rail.v_out * 100
            lvl = ERROR if sag_pct > 10 else WARN if sag_pct > 5 else OK
            rep.add(lvl, f"Voltage at peak load ≈ {pk.v_out:.2f} V "
                         f"(sag {sag:.2f} V = {sag_pct:.1f}% across {rail.r_internal_ohm:g} Ω source/cable "
                         f"resistance).")

        # ---- runtime
        if rail.kind == "supply" and rail.capacity_mah and 0 < avg < 1e-6:
            rep.add(OK, "Average draw is below 1 nA: battery self-discharge, not this circuit, sets the runtime.")
        elif rail.kind == "supply" and rail.capacity_mah and avg > 0:
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
                p_buck = rail.v_out * avg / 1000 * (1 / 0.9 - 1)
                msg += f" A buck converter would waste only ≈ {p_buck * 1000:.0f} mW here."
            rep.add(lvl, msg)
        elif rail.kind in ("buck", "boost") and rep.dissipation_avg_w > 0:
            rep.add(OK, f"Converter loss ≈ {rep.dissipation_avg_w * 1000:.0f} mW at {rail.efficiency * 100:.0f}% "
                        f"efficiency; draws {a.i_in:.0f} mA avg / {pk.i_in:.0f} mA peak from '{rail.parent}'.")

        # ---- bulk capacitance
        step = peak - avg
        if step >= 100:
            uf = bulk_cap_for_transient(step / 1000, step_response_s, allowed_droop_v) * 1e6
            rep.suggested_bulk_uf = _round_up_cap(uf)
            rep.add(OK, f"Load steps of up to {step:.0f} mA: put ≥ {rep.suggested_bulk_uf} µF of low-ESR "
                        f"bulk capacitance close to the load on this rail (plus 100 nF per IC).")

        # ---- children: can they still work at the voltage this rail really delivers?
        v_avail = pk.v_out
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
                if pk_ops[child.name].reason == "hiccup":
                    p_need = child.v_out * pk_ops[child.name].i_out / 1000 / child.efficiency
                    rep.add(ERROR, f"Boost '{child.name}' can't start at peak load: it needs ≈ {p_need:.2f} W and "
                                   f"drawing that pulls this rail below its minimum input, so it hiccups.")
                elif v_avail < child.min_vin:
                    rep.add(ERROR, f"Boost '{child.name}' needs ≥ {child.min_vin:g} V in but this rail falls to "
                                   f"{v_avail:.2f} V at peak load.")
                if rail.v_out >= child.v_out:
                    rep.add(WARN, f"Boost '{child.name}' outputs {child.v_out:g} V, which isn't above its "
                                  f"{rail.v_out:g} V input; a boost converter can't regulate down.")
        if rail.kind == "supply" and not (avg_ok and pk_ok):
            rep.add(ERROR, "The solver couldn't find a stable operating point for this supply's tree; "
                           "treat it as failing.")
        reports.append(rep)
    return PowerReport(reports)


# --------------------------------------------------------------------- wiring

AWG_OHM_PER_KM = {
    30: 338.6, 28: 213.2, 26: 133.8, 24: 84.2, 22: 52.96, 20: 33.31,
    18: 20.95, 16: 13.17, 14: 8.286, 12: 5.211, 10: 3.277,
}


def wire_drop(awg: int, length_m: float, amps: float, round_trip: bool = True) -> tuple[float, float]:
    """(volts dropped, loop resistance in ohms) for a copper wire run."""
    non_negative('length', length_m)
    number('current', amps)
    if awg not in AWG_OHM_PER_KM:
        raise ValueError(f"AWG {awg} not in table")
    r = AWG_OHM_PER_KM[awg] / 1000 * length_m * (2 if round_trip else 1)
    return amps * r, r


def smallest_awg_for_drop(length_m: float, amps: float, max_drop_v: float,
                          round_trip: bool = True) -> int | None:
    """Thinnest standard AWG that keeps the drop under max_drop_v (None if even AWG 10 fails)."""
    non_negative('length', length_m)
    number('current', amps)
    positive('allowed drop', max_drop_v)
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
    [non_negative(f'pin {k + 1} current', x) for k, x in enumerate(pin_currents_ma)]
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
    non_negative('active current', active_ma)
    non_negative('active time', active_s)
    non_negative('sleep current', sleep_ma)
    non_negative('sleep time', sleep_s)
    positive('capacity', capacity_mah, allow_none=True)
    number('usable fraction', usable, gt=0, le=1)
    period = active_s + sleep_s
    if period <= 0:
        raise ValueError("cycle time must be positive")
    avg = (active_ma * active_s + sleep_ma * sleep_s) / period
    hours = capacity_mah * usable / avg if capacity_mah and avg > 0 else None
    return DutyResult(avg, active_s / period, hours, hours / 24 if hours is not None else None)


def ohms_law(v: float | None = None, i: float | None = None, r: float | None = None,
             p: float | None = None) -> dict[str, float]:
    """Solve V, I, R, P from any two known values."""
    [positive(k.upper(), x) for k, x in dict(v=v, i=i, r=r, p=p).items() if x is not None]
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


from .validation import guard_arithmetic as _guard_arithmetic  # noqa: E402

# only the stand-alone calculators; analyse() has its own validation and reporting
_guard_arithmetic(globals(), __name__, only=("wire_drop", "duty_cycle_average", "ohms_law", "smallest_awg_for_drop"))
