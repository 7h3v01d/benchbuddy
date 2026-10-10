"""Resistor helpers: colour codes, SMD codes, E-series, dividers, LED resistors."""

from __future__ import annotations

from .validation import DomainError, non_negative, number, positive, reciprocal

import math
from dataclasses import dataclass

# ---------------------------------------------------------------- colour code

DIGIT_COLORS = ["black", "brown", "red", "orange", "yellow",
                "green", "blue", "violet", "grey", "white"]
MULTIPLIER_COLORS = {
    "black": 1, "brown": 10, "red": 100, "orange": 1e3, "yellow": 1e4,
    "green": 1e5, "blue": 1e6, "violet": 1e7, "grey": 1e8, "white": 1e9,
    "gold": 0.1, "silver": 0.01,
}
TOLERANCE_COLORS = {
    "brown": 1.0, "red": 2.0, "green": 0.5, "blue": 0.25, "violet": 0.1,
    "grey": 0.05, "gold": 5.0, "silver": 10.0, "none": 20.0,
}
TEMPCO_COLORS = {
    "black": 250, "brown": 100, "red": 50, "orange": 15, "yellow": 25,
    "green": 20, "blue": 10, "violet": 5, "grey": 1,
}

# approximate display colours for the GUI
COLOR_HEX = {
    "black": "#111111", "brown": "#7b4a1e", "red": "#d32f2f", "orange": "#f57c00",
    "yellow": "#fbc02d", "green": "#388e3c", "blue": "#1976d2", "violet": "#7b1fa2",
    "grey": "#9e9e9e", "white": "#fafafa", "gold": "#c9a227", "silver": "#c0c0c0",
    "none": "#d9c9a3",
}


@dataclass
class ColorDecode:
    ohms: float
    tolerance_pct: float
    tempco_ppm: int | None = None

    @property
    def min_ohms(self) -> float:
        return self.ohms * (1 - self.tolerance_pct / 100)

    @property
    def max_ohms(self) -> float:
        return self.ohms * (1 + self.tolerance_pct / 100)


def decode_color_bands(bands: list[str]) -> ColorDecode:
    """Decode 3, 4, 5 or 6 band resistors (bands given left to right)."""
    b = [x.strip().lower() for x in bands]
    n = len(b)
    if n not in (3, 4, 5, 6):
        raise ValueError("resistors have 3, 4, 5 or 6 bands")

    def digit(c: str) -> int:
        if c not in DIGIT_COLORS:
            raise ValueError(f"'{c}' is not a valid digit colour")
        return DIGIT_COLORS.index(c)

    def mult(c: str) -> float:
        if c not in MULTIPLIER_COLORS:
            raise ValueError(f"'{c}' is not a valid multiplier colour")
        return MULTIPLIER_COLORS[c]

    def tol(c: str) -> float:
        if c not in TOLERANCE_COLORS:
            raise ValueError(f"'{c}' is not a valid tolerance colour")
        return TOLERANCE_COLORS[c]

    if n == 3:      # d d mult  (20%)
        ohms = (digit(b[0]) * 10 + digit(b[1])) * mult(b[2])
        return ColorDecode(ohms, 20.0)
    if n == 4:      # d d mult tol
        ohms = (digit(b[0]) * 10 + digit(b[1])) * mult(b[2])
        return ColorDecode(ohms, tol(b[3]))
    ohms = (digit(b[0]) * 100 + digit(b[1]) * 10 + digit(b[2])) * mult(b[3])
    if n == 5:
        return ColorDecode(ohms, tol(b[4]))
    if b[5] not in TEMPCO_COLORS:
        raise ValueError(f"'{b[5]}' is not a valid temperature-coefficient colour")
    return ColorDecode(ohms, tol(b[4]), TEMPCO_COLORS[b[5]])


def encode_color_bands(ohms: float, bands: int = 4, tolerance_pct: float = 5.0) -> list[str]:
    """Return band colours for a resistance value."""
    number('resistance', ohms)
    number('tolerance', tolerance_pct)
    if ohms <= 0:
        raise ValueError("resistance must be positive")
    if bands not in (4, 5):
        raise ValueError("only 4 or 5 band encoding supported")
    sig_digits = 2 if bands == 4 else 3
    exp = math.floor(math.log10(ohms))
    mantissa = ohms / 10 ** exp                      # 1.000 .. 9.999
    digits = round(mantissa * 10 ** (sig_digits - 1))
    if digits >= 10 ** sig_digits:                   # rounding overflow
        digits //= 10
        exp += 1
    multiplier_exp = exp - (sig_digits - 1)
    mult_color = None
    for name, val in MULTIPLIER_COLORS.items():
        if math.isclose(val, 10 ** multiplier_exp, rel_tol=1e-9):
            mult_color = name
            break
    if mult_color is None:
        raise ValueError("value out of colour-code range")
    tol_color = None
    for name, val in TOLERANCE_COLORS.items():
        if math.isclose(val, tolerance_pct):
            tol_color = name
            break
    if tol_color is None:
        raise ValueError(f"no colour for {tolerance_pct}% tolerance")
    digit_str = str(digits).zfill(sig_digits)
    return [DIGIT_COLORS[int(d)] for d in digit_str] + [mult_color, tol_color]


# ------------------------------------------------------------------- SMD codes

def decode_smd_code(code: str) -> float:
    """Decode 3/4-digit SMD codes (472 -> 4.7k, 1002 -> 10k, 4R7 -> 4.7, 01C EIA-96)."""
    c = code.strip().upper()
    if not c:
        raise ValueError("empty code")
    if "R" in c:
        return float(c.replace("R", "."))
    if c.isdigit() and len(c) in (3, 4):
        base, exp = int(c[:-1]), int(c[-1])
        return base * 10 ** exp
    # EIA-96: two digits + letter multiplier
    if len(c) == 3 and c[:2].isdigit() and c[2].isalpha():
        idx = int(c[:2])
        if not 1 <= idx <= 96:
            raise ValueError("EIA-96 index must be 01-96")
        mult = {"Z": 0.001, "Y": 0.01, "R": 0.01, "X": 0.1, "S": 0.1,
                "A": 1, "B": 10, "H": 10, "C": 100, "D": 1e3, "E": 1e4, "F": 1e5}
        if c[2] not in mult:
            raise ValueError("unknown EIA-96 multiplier letter")
        return float(f"{E96[idx - 1] * 100 * mult[c[2]]:.6g}")
    raise ValueError(f"can't decode SMD code {code!r}")


# ----------------------------------------------------------------------- series

E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
E96 = [100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137, 140,
       143, 147, 150, 154, 158, 162, 165, 169, 174, 178, 182, 187, 191, 196, 200,
       205, 210, 215, 221, 226, 232, 237, 243, 249, 255, 261, 267, 274, 280, 287,
       294, 301, 309, 316, 324, 332, 340, 348, 357, 365, 374, 383, 392, 402, 412,
       422, 432, 442, 453, 464, 475, 487, 499, 511, 523, 536, 549, 562, 576, 590,
       604, 619, 634, 649, 665, 681, 698, 715, 732, 750, 768, 787, 806, 825, 845,
       866, 887, 909, 931, 953, 976]
E96 = [v / 100 for v in E96]

SERIES = {"E12": E12, "E24": E24, "E96": E96}


def nearest_standard(ohms: float, series: str = "E24") -> float:
    """Closest standard value (by ratio) in the given E-series."""
    positive('resistance', ohms)
    _series(series)
    if ohms <= 0:
        raise ValueError("resistance must be positive")
    table = SERIES[series]
    exp = math.floor(math.log10(ohms))
    best = None
    for e in (exp - 1, exp, exp + 1):
        for base in table:
            cand = base * 10 ** e
            if best is None or abs(math.log(cand / ohms)) < abs(math.log(best / ohms)):
                best = cand
    return float(f"{best:.6g}")


def standard_neighbours(ohms: float, series: str = "E24") -> tuple[float, float]:
    """(next lower, next higher) standard values around `ohms`."""
    positive('resistance', ohms)
    _series(series)
    table = SERIES[series]
    exp = math.floor(math.log10(ohms))
    cands = sorted(float(f"{b * 10 ** e:.6g}") for e in (exp - 1, exp, exp + 1) for b in table)
    lower = max((c for c in cands if c <= ohms * 1.0000001), default=cands[0])
    higher = min((c for c in cands if c >= ohms * 0.9999999), default=cands[-1])
    return lower, higher


# ------------------------------------------------------- series / parallel / etc

def series_resistance(values: list[float]) -> float:
    _nonempty(values)
    [non_negative('R', v) for v in values]
    return sum(values)


def parallel_resistance(values: list[float]) -> float:
    _nonempty(values)
    [positive('R', v) for v in values]
    if any(v <= 0 for v in values):
        raise ValueError("resistances must be positive")
    return reciprocal("parallel resistance", sum(reciprocal("parallel resistance", v) for v in values))


# ----------------------------------------------------------------- resistor use

@dataclass
class LedResult:
    exact_ohms: float
    chosen_ohms: float
    actual_current_a: float
    resistor_power_w: float
    recommended_power_rating_w: float
    led_power_w: float


def led_resistor(vsupply: float, vf: float, current_a: float, series: str = "E24") -> LedResult:
    """Current-limiting resistor for an LED (rounded UP to a standard value)."""
    number('supply voltage', vsupply)
    non_negative('LED forward voltage', vf)
    number('current', current_a)
    _series(series)
    if vsupply <= vf:
        raise ValueError("supply voltage must be higher than the LED forward voltage")
    if current_a <= 0:
        raise ValueError("current must be positive")
    exact = (vsupply - vf) / current_a
    _, higher = standard_neighbours(exact, series)
    actual = (vsupply - vf) / higher
    p = actual ** 2 * higher
    return LedResult(exact, higher, actual, p, p * 2, actual * vf)


@dataclass
class DividerResult:
    vout: float
    current_a: float
    power_total_w: float


def divider_vout(vin: float, r1: float, r2: float, load_ohms: float | None = None) -> DividerResult:
    """Output of a resistive divider (R1 top, R2 bottom), optionally loaded."""
    number('input voltage', vin)
    positive('R1', r1)
    positive('R2', r2)
    positive('load', load_ohms, allow_none=True)
    if r1 <= 0 or r2 <= 0:
        raise ValueError("resistors must be positive")
    r2_eff = r2 if not load_ohms else parallel_resistance([r2, load_ohms])
    vout = vin * r2_eff / (r1 + r2_eff)
    i = vin / (r1 + r2_eff)
    return DividerResult(vout, i, vin * i)


def divider_find(vin: float, vout: float, series: str = "E24",
                 r_min: float = 1e3, r_max: float = 1e6) -> list[tuple[float, float, float, float]]:
    """Best (r1, r2, actual_vout, error_pct) combos for a target output, best first."""
    number('input voltage', vin)
    number('output voltage', vout)
    _series(series)
    positive('R min', r_min)
    positive('R max', r_max)
    if not 0 < vout < vin:
        raise ValueError("need 0 < vout < vin")
    table = SERIES[series]
    values = []
    e = math.floor(math.log10(r_min))
    while 10 ** e <= r_max:
        for b in table:
            v = b * 10 ** e
            if r_min <= v <= r_max:
                values.append(float(f"{v:.6g}"))
        e += 1
    results = []
    for r1 in values:
        for r2 in values:
            vo = vin * r2 / (r1 + r2)
            err = (vo - vout) / vout * 100
            results.append((r1, r2, vo, err))
    results.sort(key=lambda t: (abs(t[3]), t[0] + t[1]))
    return results[:8]


def power_rating_ok(ohms: float, volts: float | None = None, amps: float | None = None) -> float:
    """Dissipated power in a resistor given V or I."""
    positive('resistance', ohms)
    number('voltage', volts, allow_none=True)
    number('current', amps, allow_none=True)
    if volts is not None:
        return volts ** 2 / ohms
    if amps is not None:
        return amps ** 2 * ohms
    raise ValueError("provide volts or amps")


def _nonempty(values) -> None:
    if not values:
        raise DomainError("enter at least one value")


def _series(name) -> None:
    if name not in SERIES:
        raise DomainError(f"unknown E-series {name!r}")


from .validation import guard_arithmetic as _guard_arithmetic  # noqa: E402

_guard_arithmetic(globals(), __name__)
