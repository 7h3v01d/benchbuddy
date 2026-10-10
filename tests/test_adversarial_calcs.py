"""Every public calculator rejects non-finite and out-of-domain input with a controlled
ValueError (reviewer properties 13 and 14), and never raises a raw arithmetic error."""

import inspect
import itertools
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from benchbuddy.core import calcs, capacitors, power, resistors
from benchbuddy.core.validation import _all_finite

# (function, valid kwargs) - the valid call proves the harness; each numeric arg is then poisoned
CASES = [
    (capacitors.rc_time_constant, dict(r_ohms=1e3, c_farads=1e-6)),
    (capacitors.rc_cutoff_hz, dict(r_ohms=1e3, c_farads=1e-6)),
    (capacitors.rc_charge_time, dict(r_ohms=1e3, c_farads=1e-6, vstart_frac=0.0, vend_frac=0.5)),
    (capacitors.capacitor_reactance, dict(c_farads=1e-6, freq_hz=1e3)),
    (capacitors.capacitor_energy, dict(c_farads=1e-6, volts=5.0)),
    (capacitors.self_resonant_hz, dict(c_farads=1e-6, esl_henries=1e-9)),
    (capacitors.holdup_capacitance, dict(load_amps=0.1, hold_time_s=0.01, v_start=5.0, v_min=3.0)),
    (capacitors.bulk_cap_for_transient, dict(delta_amps=0.4, response_time_s=1e-4, allowed_droop_v=0.1)),
    (capacitors.esr_droop, dict(delta_amps=0.4, esr_ohms=0.05)),
    (resistors.encode_color_bands, dict(ohms=4700.0, bands=4, tolerance_pct=5.0)),
    (resistors.nearest_standard, dict(ohms=4800.0)),
    (resistors.standard_neighbours, dict(ohms=4800.0)),
    (resistors.led_resistor, dict(vsupply=5.0, vf=2.0, current_a=0.02)),
    (resistors.divider_vout, dict(vin=5.0, r1=10e3, r2=10e3)),
    (resistors.divider_find, dict(vin=5.0, vout=3.3)),
    (resistors.power_rating_ok, dict(ohms=100.0, volts=5.0)),
    (calcs.adc_divider, dict(vbat_max=4.2, adc_max=3.1)),
    (calcs.trace_width, dict(current_a=1.0, rise_c=10.0, copper_oz=1.0)),
    (calcs.buck_inductor, dict(vin=12.0, vout=5.0, iout=1.0, fsw=500e3)),
    (calcs.boost_inductor, dict(vin=3.7, vout=5.0, iout=0.5, fsw=1e6)),
    (calcs.i2c_pullup, dict(vcc=3.3, bus_cap_pf=100.0)),
    (calcs.base_resistor, dict(ic_a=0.1, v_drive=3.3)),
    (calcs.ne555_astable, dict(r1=1e3, r2=10e3, c=100e-9)),
    (calcs.ne555_astable_design, dict(freq_hz=1000.0, duty=0.6)),
    (calcs.ne555_monostable, dict(r=100e3, c=10e-6)),
    (calcs.opamp_gain, dict(rf=10e3, rg=1e3)),
    (calcs.opamp_design, dict(gain=11.0)),
    (calcs.opamp_check, dict(gain=2.0, noise_gain=2.0, vin_pk=0.5, v_neg=-5.0, v_pos=5.0)),
    (calcs.led_budget, dict(count=60, ma_per_ch=20.0)),
    (calcs.led_max_brightness, dict(count=60, ma_per_ch=20.0, psu_ma=2000.0)),
    (power.wire_drop, dict(awg=24, length_m=1.0, amps=1.0)),
    (power.smallest_awg_for_drop, dict(length_m=1.0, amps=1.0, max_drop_v=0.1)),
    (power.duty_cycle_average, dict(active_ma=80.0, active_s=1.0, sleep_ma=0.01, sleep_s=59.0)),
    (power.ohms_law, dict(v=5.0, r=100.0)),
]
POISON = [math.nan, math.inf, -math.inf]


@pytest.mark.parametrize("fn,kwargs", CASES, ids=[c[0].__name__ for c in CASES])
def test_valid_call_works(fn, kwargs):
    fn(**kwargs)


@pytest.mark.parametrize("fn,kwargs,arg,bad",
                         [(fn, kw, a, b) for fn, kw in CASES for a, v in kw.items()
                          if isinstance(v, float) for b in POISON],
                         ids=lambda x: getattr(x, "__name__", None) if callable(x) else None)
def test_non_finite_inputs_are_rejected(fn, kwargs, arg, bad):
    with pytest.raises(ValueError):
        fn(**{**kwargs, arg: bad})


@pytest.mark.parametrize("call", [
    lambda: capacitors.capacitor_reactance(-1e-6, 1e3),          # reviewer: negative C
    lambda: capacitors.capacitor_reactance(0, 1e3),
    lambda: capacitors.series_capacitance([0, 1e-6]),             # was ZeroDivisionError
    lambda: capacitors.series_capacitance([]),
    lambda: capacitors.capacitor_energy(-1e-6, 5),
    lambda: capacitors.rc_cutoff_hz(0, 1e-6),
    lambda: capacitors.self_resonant_hz(1e-6, 0),
    lambda: capacitors.bulk_cap_for_transient(0.4, 1e-4, 0),
    lambda: resistors.parallel_resistance([]),
    lambda: resistors.parallel_resistance([0, 10]),
    lambda: resistors.series_resistance([-1]),
    lambda: resistors.nearest_standard(1000, "E7"),
    lambda: resistors.power_rating_ok(0, volts=5),
    lambda: power.ohms_law(v=5, i=0),                             # V/I with I = 0
    lambda: power.ohms_law(v=0, r=10),
    lambda: power.wire_drop(24, -1, 1),
    lambda: power.duty_cycle_average(80, 0, 0.01, 0),             # zero period
    lambda: power.duty_cycle_average(80, 1, 0.01, 59, usable=0),
    lambda: calcs.adc_divider(4.2, 3.1, r_min=1e6, r_max=1e3),    # empty range: was IndexError in the GUI
    lambda: calcs.opamp_check(2, 2, 0.5, 5, -5),                  # rails swapped
    lambda: calcs.led_budget(0, 20),
    lambda: calcs.led_budget(60, 20, psu_margin=0.5),
    # valid-but-extreme inputs whose product underflows to 0 (found by the fuzzer in 0.5.2)
    lambda: capacitors.self_resonant_hz(5.9e-170, 5.9e-170),
    lambda: capacitors.rc_cutoff_hz(5.9e-170, 5.9e-170),
    lambda: capacitors.capacitor_reactance(5.9e-170, 5.9e-170),
    lambda: capacitors.series_capacitance([5e-324]),
    lambda: resistors.parallel_resistance([5e-324, 1.0]),
])
def test_out_of_domain_inputs_are_rejected(call):
    with pytest.raises(ValueError):
        call()


finite = st.floats(allow_nan=False, allow_infinity=False)            # the whole range a user can type


@given(st.data())
def test_random_inputs_never_escape_as_arithmetic_errors(data):
    """Any finite garbage either computes a finite answer or raises ValueError - never
    ZeroDivision/Overflow, and never inf/NaN handed back as if it were a result."""
    fn, kwargs = data.draw(st.sampled_from(CASES))
    args = {k: (data.draw(finite) if isinstance(v, float) else v) for k, v in kwargs.items()}
    try:
        result = fn(**args)
    except ValueError:
        return
    assert _all_finite(result), (fn.__name__, args, result)


# every magnitude class floats have: subnormal, smallest normal, tiny, ordinary, huge, max, zero, negatives
EXTREMES = (5e-324, 2.2250738585072014e-308, 1e-200, 1e-12, 1.0, 3.0, 1e12, 1e200, 1.7e308,
            0.0, -1e-300, -1.0, -1e300)


@pytest.mark.parametrize("fn,kwargs", CASES, ids=[fn.__name__ for fn, _ in CASES])
def test_extreme_magnitudes_are_computed_or_refused(fn, kwargs):
    """Deterministic version of the fuzz above: every pair of extreme magnitudes across the float
    arguments (the rest at their defaults). Underflow to a zero divisor was found this way in 0.5.2."""
    floats = [k for k, v in kwargs.items() if isinstance(v, float)]
    pairs = itertools.combinations(floats, 2) if len(floats) > 1 else [(f,) for f in floats]
    for names in pairs:
        for combo in itertools.product(EXTREMES, repeat=len(names)):
            args = dict(kwargs)
            args.update(zip(names, combo))
            try:
                result = fn(**args)
            except ValueError:
                continue
            assert _all_finite(result), (fn.__name__, args, result)


def test_every_public_calculator_is_covered():
    covered = {fn for fn, _ in CASES}
    for mod, skip in ((capacitors, {"decode_cap_code", "esp32_decoupling_advice", "series_capacitance",
                                    "parallel_capacitance"}),
                      (resistors, {"decode_color_bands", "decode_smd_code", "series_resistance",
                                   "parallel_resistance"}),
                      (calcs, {"auto_threshold"})):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ != mod.__name__ or name.startswith("_") or name in skip:
                continue
            assert fn in covered, f"{mod.__name__}.{name} has no adversarial case"
