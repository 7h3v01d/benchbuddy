"""Capacitor helpers: marking decode, RC maths, reactance, decoupling advice."""

from __future__ import annotations

from .validation import DomainError, non_negative, number, positive

import math
from dataclasses import dataclass

# ------------------------------------------------------------------ markings

# EIA tolerance letters used on ceramics/films
TOLERANCE_LETTERS = {
    "B": "±0.1 pF", "C": "±0.25 pF", "D": "±0.5 pF", "F": "±1 %", "G": "±2 %",
    "H": "±3 %", "J": "±5 %", "K": "±10 %", "M": "±20 %", "Z": "+80/-20 %",
}

# EIA 3-char dielectric codes for MLCCs (temperature range / variation)
DIELECTRICS = {
    "C0G": "Class 1, ±30 ppm/°C, very stable (preferred for timing/filters)",
    "NP0": "Same as C0G, very stable",
    "X7R": "Class 2, ±15 % from -55 to 125 °C, decent general purpose",
    "X5R": "Class 2, ±15 % from -55 to 85 °C",
    "X6S": "Class 2, ±22 % from -55 to 105 °C",
    "Y5V": "Class 2, +22/-82 %, poor — loses most capacitance with bias/temp",
    "Z5U": "Class 2, +22/-56 %, poor",
}

# Voltage letter codes (single letter, often on small SMD parts)
VOLTAGE_LETTERS = {"e": 2.5, "G": 4, "J": 6.3, "A": 10, "C": 16, "D": 20,
                   "E": 25, "V": 35, "H": 50}


@dataclass
class CapDecode:
    farads: float
    tolerance: str | None = None
    note: str = ""


def decode_cap_code(code: str) -> CapDecode:
    """Decode '104', '104J', '22', '4n7', '0.1', 'p33' style ceramic/film codes.

    3-digit codes are in picofarads (first two digits x 10^third).
    """
    raw = code.strip()
    if not raw:
        raise ValueError("empty code")
    tol = None
    c = raw.upper()
    # trailing tolerance letter?
    if len(c) >= 3 and c[-1] in TOLERANCE_LETTERS and c[:-1].replace(".", "").isdigit():
        tol = TOLERANCE_LETTERS[c[-1]]
        c = c[:-1]
    # RKM style  4n7, 2u2, p33
    low = raw.lower()
    for letter, mult in (("p", 1e-12), ("n", 1e-9), ("u", 1e-6)):
        if letter in low and low.replace(letter, "", 1).replace(".", "").isdigit():
            if low.startswith(letter):
                return CapDecode(float("0." + low[1:]) * mult, tol, "RKM notation")
            whole, frac = low.split(letter)
            val = float(f"{whole}.{frac or 0}") * mult
            return CapDecode(val, tol, "RKM notation")
    if c.isdigit():
        if len(c) == 3:
            base, exp = int(c[:2]), int(c[2])
            # EIA: a third digit of 8 or 9 means x0.01 / x0.1 (109 = 1.0 pF, 479 = 4.7 pF)
            factor = {8: 0.01, 9: 0.1}.get(exp, 10 ** exp)
            return CapDecode(float(f"{base * factor * 1e-12:.6g}"), tol, "3-digit code, picofarads")
        if len(c) <= 2:
            return CapDecode(int(c) * 1e-12, tol, "1-2 digits are picofarads")
        if len(c) == 4:
            base, exp = int(c[:3]), int(c[3])
            return CapDecode(base * 10 ** exp * 1e-12, tol, "4-digit code, picofarads")
    try:
        # plain decimal like 0.1 -> microfarads
        return CapDecode(float(c) * 1e-6, tol, "decimal value assumed in microfarads")
    except ValueError:
        raise ValueError(f"can't decode capacitor code {code!r}") from None


# ------------------------------------------------------------------- RC / reactance

def rc_time_constant(r_ohms: float, c_farads: float) -> float:
    positive('R', r_ohms)
    positive('C', c_farads)
    return r_ohms * c_farads


def rc_cutoff_hz(r_ohms: float, c_farads: float) -> float:
    positive('R', r_ohms)
    positive('C', c_farads)
    return 1.0 / (2 * math.pi * r_ohms * c_farads)


def rc_charge_time(r_ohms: float, c_farads: float, vstart_frac: float = 0.0,
                   vend_frac: float = 0.632) -> float:
    """Time for an RC to go from vstart_frac to vend_frac of the supply."""
    positive('R', r_ohms)
    positive('C', c_farads)
    number('start fraction', vstart_frac)
    number('end fraction', vend_frac)
    if not 0 <= vstart_frac < vend_frac < 1:
        raise ValueError("need 0 <= start < end < 1")
    tau = r_ohms * c_farads
    return tau * math.log((1 - vstart_frac) / (1 - vend_frac))


def capacitor_reactance(c_farads: float, freq_hz: float) -> float:
    positive('C', c_farads)
    positive('frequency', freq_hz)
    return 1.0 / (2 * math.pi * freq_hz * c_farads)


def capacitor_energy(c_farads: float, volts: float) -> float:
    non_negative('C', c_farads)
    number('voltage', volts)
    return 0.5 * c_farads * volts ** 2


def series_capacitance(values: list[float]) -> float:
    _nonempty(values)
    [positive('C', v) for v in values]
    return 1.0 / sum(1.0 / v for v in values)


def parallel_capacitance(values: list[float]) -> float:
    _nonempty(values)
    [non_negative('C', v) for v in values]
    return sum(values)


def self_resonant_hz(c_farads: float, esl_henries: float) -> float:
    positive('C', c_farads)
    positive('ESL', esl_henries)
    return 1.0 / (2 * math.pi * math.sqrt(c_farads * esl_henries))


# ----------------------------------------------------------------- bulk / holdup

@dataclass
class HoldupResult:
    farads_needed: float
    note: str


def holdup_capacitance(load_amps: float, hold_time_s: float, v_start: float, v_min: float) -> HoldupResult:
    """Capacitance needed to ride through an interruption: C = I*t / (Vstart - Vmin)."""
    non_negative('load current', load_amps)
    positive('hold time', hold_time_s)
    number('start voltage', v_start)
    non_negative('minimum voltage', v_min)
    if v_start <= v_min:
        raise ValueError("start voltage must exceed minimum voltage")
    c = load_amps * hold_time_s / (v_start - v_min)
    return HoldupResult(c, "Ignores ESR and regulator dropout; add a safety margin of 1.5-2x.")


def bulk_cap_for_transient(delta_amps: float, response_time_s: float, allowed_droop_v: float) -> float:
    """C needed so a load step of delta_amps lasting response_time_s droops < allowed_droop_v."""
    non_negative('current step', delta_amps)
    positive('response time', response_time_s)
    number('allowed droop', allowed_droop_v)
    if allowed_droop_v <= 0:
        raise ValueError("allowed droop must be positive")
    return delta_amps * response_time_s / allowed_droop_v


def esr_droop(delta_amps: float, esr_ohms: float) -> float:
    """Instantaneous voltage step caused by capacitor ESR on a current step."""
    non_negative('current step', delta_amps)
    non_negative('ESR', esr_ohms)
    return delta_amps * esr_ohms


# ---------------------------------------------------------------- ESP32 advice

def esp32_decoupling_advice() -> list[str]:
    return [
        "Put 10 µF (X5R/X7R, ≥10 V) + 100 nF right at the 3V3 pins of the module/regulator.",
        "Wi-Fi TX bursts can pull 300-500 mA for a few hundred µs; add 100-470 µF bulk near the 3V3 rail if you see brownouts.",
        "Use low-ESR capacitors (ceramic or polymer) for the bulk cap; cheap electrolytics can add several hundred mV of droop.",
        "Keep decoupling caps close (<10 mm) with short, fat traces/wires back to ground.",
        "Class 2 ceramics lose capacitance under DC bias — a 10 µF 6.3 V X5R on 5 V may only give ~3 µF.",
        "If you power from USB and see resets at Wi-Fi start-up, try a 470 µF electrolytic in parallel with a 10 µF ceramic across 5 V.",
    ]


def _nonempty(values) -> None:
    if not values:
        raise DomainError("enter at least one value")
