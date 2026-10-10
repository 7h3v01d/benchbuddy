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


# ------------------------------------------------------------------------ 555
CAP_E6 = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]
LN2 = math.log(2)


def ne555_astable(r1: float, r2: float, c: float, diode: bool = False) -> dict:
    """Classic 555 astable. With a diode across R2, C charges through R1 only (duty can go < 50 %)."""
    if min(r1, r2, c) <= 0:
        raise ValueError("R1, R2 and C must be positive")
    t_high = LN2 * (r1 if diode else r1 + r2) * c
    t_low = LN2 * r2 * c
    period = t_high + t_low
    return dict(freq_hz=1 / period, t_high_s=t_high, t_low_s=t_low, period_s=period, duty=t_high / period)


def ne555_astable_design(freq_hz: float, duty: float = 0.5, r_min: float = 1e3, r_max: float = 1e6,
                         series: str = "E24") -> list[dict]:
    """Standard-value R1/R2/C combos for a target frequency and duty, best first.

    Duty below 50 % (or exactly 50 %) needs the diode-across-R2 variant; the result says which.
    R1 is kept >= 1 kΩ so the discharge transistor isn't shorted to Vcc.
    """
    if freq_hz <= 0 or not 0 < duty < 1:
        raise ValueError("frequency must be positive and duty between 0 and 1")
    diode = duty <= 0.5
    table = SERIES[series]
    res = sorted({float(f"{b * 10 ** e:.6g}") for e in range(2, 7) for b in table
                  if r_min <= b * 10 ** e <= r_max})
    caps = [float(f"{b * 10 ** e:.6g}") for e in range(-12, -3) for b in CAP_E6]
    out = []
    period = 1 / freq_hz
    for c in caps:
        # ideal R2 from the low time, then R1 from the high time
        r2_ideal = (1 - duty) * period / (LN2 * c)
        if not r_min <= r2_ideal <= r_max:
            continue
        r2 = nearest_standard(r2_ideal, series)
        t_high = duty * period
        r1_ideal = t_high / (LN2 * c) - (0 if diode else r2)
        if not r_min <= r1_ideal <= r_max:
            continue
        r1 = nearest_standard(r1_ideal, series)
        got = ne555_astable(r1, r2, c, diode)
        f_err = (got["freq_hz"] - freq_hz) / freq_hz * 100
        out.append(dict(r1=r1, r2=r2, c=c, diode=diode, freq_hz=got["freq_hz"], duty=got["duty"],
                        freq_err_pct=f_err, duty_err=got["duty"] - duty))
    out.sort(key=lambda d: (abs(d["freq_err_pct"]) + abs(d["duty_err"]) * 100, -d["c"]))
    return out[:6]


def ne555_monostable(r: float, c: float) -> float:
    """Pulse width of a 555 one-shot: 1.1·R·C (= ln 3 · RC)."""
    if r <= 0 or c <= 0:
        raise ValueError("R and C must be positive")
    return math.log(3) * r * c


# -------------------------------------------------------------------- op-amp
def opamp_gain(rf: float, rg: float, inverting: bool = False) -> dict:
    """Closed-loop gain and noise gain for the two classic resistor-feedback stages.

    Non-inverting: G = 1 + Rf/Rg.  Inverting: G = -Rf/Rin (Rg is the input resistor).
    Noise gain (what sets bandwidth) is 1 + Rf/Rg in both cases.
    """
    if rf <= 0 or rg <= 0:
        raise ValueError("resistors must be positive")
    noise_gain = 1 + rf / rg
    return dict(gain=-rf / rg if inverting else noise_gain, noise_gain=noise_gain)


def opamp_design(gain: float, inverting: bool = False, series: str = "E24",
                 rg_min: float = 1e3, rg_max: float = 100e3) -> list[dict]:
    """Rf/Rg pairs from an E-series that hit a target gain, best first."""
    if inverting:
        g = abs(gain)
        if g <= 0:
            raise ValueError("gain magnitude must be positive")
        ratio = g
    else:
        if gain < 1:
            raise ValueError("a non-inverting stage can't have a gain below 1 (use a divider or an inverting stage)")
        if gain == 1:
            return [dict(rf=0.0, rg=float("inf"), gain=1.0, err_pct=0.0, note="Voltage follower: wire the output straight to IN−.")]
        ratio = gain - 1
    table = SERIES[series]
    vals = sorted({float(f"{b * 10 ** e:.6g}") for e in range(1, 8) for b in table})
    out = []
    for rg in (v for v in vals if rg_min <= v <= rg_max):
        rf = nearest_standard(rg * ratio, series)
        got = opamp_gain(rf, rg, inverting)["gain"]
        target = -abs(gain) if inverting else gain
        out.append(dict(rf=rf, rg=rg, gain=got, err_pct=(got - target) / abs(target) * 100))
    out.sort(key=lambda d: (round(abs(d["err_pct"]), 3), abs(math.log10(d["rg"] / 10e3))))
    return out[:6]


def opamp_check(gain: float, noise_gain: float, vin_pk: float, v_neg: float, v_pos: float,
                headroom_v: float = 1.5, gbw_hz: float | None = None, slew_v_per_us: float | None = None,
                freq_hz: float | None = None) -> list[tuple[str, str]]:
    """Output swing, bandwidth and slew checks for a gain stage."""
    msgs: list[tuple[str, str]] = []
    v_out = gain * vin_pk
    lo, hi = v_neg + headroom_v, v_pos - headroom_v
    # dual supply: treat the input as an AC peak, so the output swings ±Vout;
    # single supply: Vout is the (signed) level the output has to reach
    points = [v_out, -v_out] if v_neg < 0 else [v_out]
    need_lo, need_hi = min(points), max(points)
    span = f"{need_lo:.2f} V" if need_lo == need_hi else f"{need_lo:.2f} V … {need_hi:.2f} V"
    if need_hi > hi or need_lo < lo:
        msgs.append(("error", f"Output needs to reach {span} but can only swing "
                              f"{lo:.2f} V … {hi:.2f} V ({headroom_v:g} V from each rail). It will clip: lower the gain, "
                              f"change the rails or use a rail-to-rail op-amp (headroom ≈ 0.1 V)."))
    else:
        msgs.append(("ok", f"Output {span} fits inside the {lo:.2f} V … {hi:.2f} V swing."))
    if v_neg >= 0 and gain < 0:
        msgs.append(("warn", "Inverting stage on a single supply: bias the non-inverting input to mid-rail, "
                             "or the output can only go towards the negative rail."))
    if gbw_hz:
        bw = gbw_hz / noise_gain
        lvl = "ok"
        text = f"Small-signal bandwidth ≈ {bw:,.0f} Hz (GBW {gbw_hz:,.0f} Hz ÷ noise gain {noise_gain:.3g})."
        if freq_hz and freq_hz > bw / 10:
            lvl = "warn" if freq_hz <= bw else "error"
            text += f" Your {freq_hz:,.0f} Hz signal is {'above' if freq_hz > bw else 'within 10× of'} it: expect gain error."
        msgs.append((lvl, text))
    if slew_v_per_us and freq_hz:
        need = 2 * math.pi * freq_hz * abs(v_out) / 1e6
        lvl = "ok" if need <= slew_v_per_us else "error"
        msgs.append((lvl, f"A full-swing {freq_hz:,.0f} Hz sine needs {need:.3g} V/µs of slew rate "
                          f"(op-amp: {slew_v_per_us:g} V/µs)."))
    return msgs


# --------------------------------------------------------------- LED matrices
LED_TYPES = {
    "WS2812B (classic, ~20 mA/channel)": dict(ma_per_ch=20.0, idle_ma=1.0, v=5.0),
    "WS2812B-V5 / SK6812 RGB (~12 mA/channel)": dict(ma_per_ch=12.0, idle_ma=1.0, v=5.0),
    "SK6812 RGBW (~12 mA/channel, 4 ch)": dict(ma_per_ch=12.0, idle_ma=1.0, v=5.0, channels=4),
    "APA102 / SK9822 (~20 mA/channel)": dict(ma_per_ch=20.0, idle_ma=0.8, v=5.0),
    "WS2815 12 V (~15 mA per pixel per channel)": dict(ma_per_ch=15.0, idle_ma=1.5, v=12.0),
}
LED_MIXES = {
    "Full white (worst case)": 1.0,
    "Typical animations (~1/3 of full)": 1 / 3,
    "Single colour, e.g. all red": None,     # one channel of three
}


def led_budget(count: int, ma_per_ch: float, channels: int = 3, brightness: float = 1.0,
               mix: float | None = 1.0, idle_ma: float = 1.0, volts: float = 5.0,
               psu_margin: float = 1.25) -> dict:
    """Current and power for an addressable-LED strip/matrix.

    mix: fraction of full-white drive (1 = all channels at 100 %); None = one channel only.
    """
    if count <= 0 or ma_per_ch <= 0 or channels <= 0:
        raise ValueError("count, channel current and channels must be positive")
    if not 0 <= brightness <= 1:
        raise ValueError("brightness must be between 0 and 100 %")
    per_led_full = ma_per_ch * channels
    drive = (ma_per_ch if mix is None else per_led_full * mix) * brightness
    total_ma = count * (drive + idle_ma)
    worst_ma = count * (per_led_full * brightness + idle_ma)
    return dict(total_ma=total_ma, worst_ma=worst_ma, idle_ma=count * idle_ma,
                power_w=total_ma / 1000 * volts, psu_a=worst_ma / 1000 * psu_margin,
                per_led_full_ma=per_led_full)


def led_max_brightness(count: int, ma_per_ch: float, psu_ma: float, channels: int = 3,
                       idle_ma: float = 1.0, mix: float | None = 1.0) -> float:
    """Highest global brightness (0..1) that keeps the drawn current within psu_ma."""
    if count <= 0 or psu_ma <= 0:
        raise ValueError("count and supply current must be positive")
    usable = psu_ma - count * idle_ma
    if usable <= 0:
        return 0.0
    per_led = ma_per_ch if mix is None else ma_per_ch * channels * mix
    return min(usable / (count * per_led), 1.0)
