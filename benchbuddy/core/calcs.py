"""Extra design calculators: ADC divider, PCB trace width, buck/boost inductor,
I2C pull-ups and transistor base resistors."""

from __future__ import annotations

import math

from .resistors import SERIES, nearest_standard, standard_neighbours


# ----------------------------------------------------------- battery ADC divider

def adc_divider(vbat_max: float, adc_max: float, series: str = "E24",
                r_min: float = 10e3, r_max: float = 1e6, headroom: float = 0.95,
                top: int = 6) -> list[dict]:
    """Resistor pairs that scale a battery voltage into an ADC range.

    The output at `vbat_max` never exceeds `adc_max`. Aims for `headroom * adc_max`
    and, among similar errors, prefers larger resistors (less battery drain).
    Returns dicts: r1, r2, vout_at_max, error_pct, drain_ua, source_ohms.
    """
    if vbat_max <= 0 or adc_max <= 0:
        raise ValueError("voltages must be positive")
    if vbat_max <= adc_max:
        raise ValueError("battery voltage is already within the ADC range: no divider needed")
    target = adc_max * headroom
    table = SERIES[series]
    values: list[float] = []
    e = math.floor(math.log10(r_min))
    while 10 ** e <= r_max:
        for b in table:
            v = float(f"{b * 10 ** e:.6g}")
            if r_min <= v <= r_max:
                values.append(v)
        e += 1
    out = []
    for r1 in values:
        for r2 in values:
            vo = vbat_max * r2 / (r1 + r2)
            if vo > adc_max:
                continue
            err = (vo - target) / target * 100
            out.append(dict(r1=r1, r2=r2, vout_at_max=vo, error_pct=err,
                            drain_ua=vbat_max / (r1 + r2) * 1e6,
                            source_ohms=r1 * r2 / (r1 + r2)))
    # bucket error to 0.5 % steps, then prefer the lowest drain
    out.sort(key=lambda d: (round(abs(d["error_pct"]) / 0.5), d["drain_ua"]))
    return out[:top]


# ---------------------------------------------------------------- PCB trace width

def trace_width(current_a: float, rise_c: float = 10.0, copper_oz: float = 1.0,
                external: bool = True, length_mm: float | None = None) -> dict:
    """IPC-2221 trace width for a given current and allowed temperature rise."""
    if current_a <= 0 or rise_c <= 0 or copper_oz <= 0:
        raise ValueError("current, temperature rise and copper weight must be positive")
    k = 0.048 if external else 0.024
    area_mil2 = (current_a / (k * rise_c ** 0.44)) ** (1 / 0.725)
    thickness_mil = copper_oz * 1.378
    width_mil = area_mil2 / thickness_mil
    width_mm = width_mil * 0.0254
    result = dict(width_mm=width_mm, width_mil=width_mil, area_mil2=area_mil2)
    if length_mm:
        rho = 1.72e-8 * (1 + 0.00393 * (25 + rise_c - 20))
        thickness_m = copper_oz * 35e-6
        r = rho * (length_mm / 1000) / ((width_mm / 1000) * thickness_m)
        result.update(resistance_mohm=r * 1000, drop_mv=r * current_a * 1000)
    return result


# ------------------------------------------------------------ switching inductors

def buck_inductor(vin: float, vout: float, iout: float, fsw: float,
                  ripple_ratio: float = 0.3, vripple: float = 0.05) -> dict:
    """Inductor and output-cap estimate for a buck converter (continuous mode)."""
    if not 0 < vout < vin:
        raise ValueError("a buck converter needs 0 < Vout < Vin")
    if min(iout, fsw, ripple_ratio) <= 0:
        raise ValueError("current, frequency and ripple ratio must be positive")
    d = vout / vin
    di = ripple_ratio * iout
    l = (vin - vout) * d / (fsw * di)
    ipk = iout + di / 2
    cout = di / (8 * fsw * vripple)
    return dict(duty=d, inductance_h=l, ripple_a=di, peak_a=ipk, isat_a=ipk * 1.25, cout_f=cout)


def boost_inductor(vin: float, vout: float, iout: float, fsw: float, efficiency: float = 0.85,
                   ripple_ratio: float = 0.3, vripple: float = 0.05) -> dict:
    """Inductor and output-cap estimate for a boost converter (continuous mode)."""
    if not 0 < vin < vout:
        raise ValueError("a boost converter needs 0 < Vin < Vout")
    if min(iout, fsw, ripple_ratio) <= 0 or not 0 < efficiency <= 1:
        raise ValueError("current, frequency, ripple ratio and efficiency must be positive")
    d = 1 - vin * efficiency / vout
    iin = iout * vout / (vin * efficiency)
    di = ripple_ratio * iin
    l = vin * d / (fsw * di)
    ipk = iin + di / 2
    cout = iout * d / (fsw * vripple)
    return dict(duty=d, input_a=iin, inductance_h=l, ripple_a=di, peak_a=ipk,
                isat_a=ipk * 1.25, cout_f=cout)


# ------------------------------------------------------------------------- I2C

I2C_MODES = {
    "Standard (100 kHz)": dict(tr=1000e-9, iol=3e-3),
    "Fast (400 kHz)": dict(tr=300e-9, iol=3e-3),
    "Fast-mode Plus (1 MHz)": dict(tr=120e-9, iol=20e-3),
}


def i2c_pullup(vcc: float, bus_cap_pf: float, mode: str = "Standard (100 kHz)") -> dict:
    """Allowed pull-up range per the I2C spec and a sensible E24 pick."""
    if vcc <= 0.4 or bus_cap_pf <= 0:
        raise ValueError("need Vcc > 0.4 V and a positive bus capacitance")
    m = I2C_MODES[mode]
    r_min = (vcc - 0.4) / m["iol"]
    r_max = m["tr"] / (0.8473 * bus_cap_pf * 1e-12)
    if r_min > r_max:
        raise ValueError(
            f"Bus capacitance of {bus_cap_pf:g} pF is too high for {mode}: the allowed range is empty "
            f"({r_min:.0f} Ω min vs {r_max:.0f} Ω max). Shorten wires, use a slower mode or an I2C buffer.")
    geo = math.sqrt(r_min * r_max)
    pick = nearest_standard(geo, "E24")
    if not r_min <= pick <= r_max:
        lo, hi = standard_neighbours(geo, "E24")
        pick = lo if r_min <= lo <= r_max else hi
    return dict(r_min=r_min, r_max=r_max, suggested=pick,
                current_ma_low=(vcc - 0.4) / pick * 1000)


# --------------------------------------------------------- transistor base resistor

def base_resistor(ic_a: float, v_drive: float, vbe: float = 0.7, forced_beta: float = 10.0,
                  gpio_limit_ma: float = 20.0) -> dict:
    """Base resistor to saturate a BJT switch (forced beta, rounded down to E24)."""
    if ic_a <= 0 or forced_beta <= 0:
        raise ValueError("collector current and forced beta must be positive")
    if v_drive <= vbe:
        raise ValueError("drive voltage must be higher than Vbe")
    ib = ic_a / forced_beta
    exact = (v_drive - vbe) / ib
    lower, _ = standard_neighbours(exact, "E24")
    chosen = lower
    actual_ib = (v_drive - vbe) / chosen
    return dict(ib_a=ib, exact_ohms=exact, chosen_ohms=chosen, actual_ib_ma=actual_ib * 1000,
                gpio_ok=actual_ib * 1000 <= gpio_limit_ma,
                resistor_power_w=actual_ib ** 2 * chosen)
