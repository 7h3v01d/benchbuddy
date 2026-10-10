"""Adversarial / property tests for the brown-out simulator (reviewer H2, H3)."""

import math
import os
from dataclasses import replace

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from benchbuddy.core import brownout as bo

SIM = settings(max_examples=int(os.environ.get("BB_SIM_EXAMPLES", "40")))   # real time-domain runs: quick by default

BASE = bo.SimParams(n_bursts=1, period_s=3e-3, t_first_s=0.5e-3)


@st.composite
def params(draw):
    kind = draw(st.sampled_from(["ldo", "switcher", "none"]))
    v_set = draw(st.floats(1.0, 12.0))
    return replace(
        BASE, reg_kind=kind, v_set=v_set,
        v_src=draw(st.floats(0.5, 24.0)), r_src=draw(st.floats(0.005, 5.0)),
        c_in_f=draw(st.sampled_from([0.0, 1e-6, 10e-6, 100e-6])), esr_in=draw(st.floats(0, 0.5)),
        dropout_v=draw(st.floats(0, 2)), i_rated_a=draw(st.floats(0.05, 3)), r_out=draw(st.floats(0.01, 1)),
        t_response_s=draw(st.floats(1e-6, 1e-4)), i_limit_a=draw(st.floats(0.05, 5)),
        iq_a=draw(st.floats(0, 0.01)), efficiency=draw(st.floats(0.3, 1.0)), vin_min=draw(st.floats(0, 12)),
        c_out_f=draw(st.sampled_from([1e-6, 10e-6, 100e-6, 1000e-6])), esr_out=draw(st.floats(0, 0.5)),
        i_base_a=draw(st.floats(0, 0.5)), i_peak_a=draw(st.floats(0.5, 20)),
        burst_s=draw(st.floats(50e-6, 1e-3)), v_threshold=draw(st.floats(0, v_set)))


# ------------------------------------------------------ H2: no impossible polarity
@pytest.mark.parametrize("peak", [0.5, 1.0, 2.0, 5.0, 10.0, 100.0])
def test_reviewer_peak_sweep_never_goes_negative(peak):
    res = bo.simulate(bo.SimParams(i_peak_a=peak))
    assert min(res.v_out) >= 0 and res.v_out_min >= 0 and min(res.v_in) >= 0


@pytest.mark.parametrize("change", [
    dict(i_limit_a=0.01, i_peak_a=2.0),                 # hard current-limit collapse
    dict(i_peak_a=500.0),                               # huge transient
    dict(v_src=0.1),                                    # source can't do anything
    dict(r_src=1e5),                                    # source with no capability
    dict(reg_kind="switcher", vin_min=20.0),            # regulator shut down
    dict(c_out_f=1e-12, esr_out=0),                     # basically no capacitor
])
def test_collapse_scenarios_stay_non_negative(change):
    res = bo.simulate(replace(BASE, **change))
    assert min(res.v_out) >= 0 and min(res.v_in) >= 0
    assert all(math.isfinite(x) for x in res.v_out + res.v_in + res.i_in)


@SIM
@given(params())
def test_random_valid_params_give_finite_non_negative_traces(p):
    res = bo.simulate(p)
    assert all(math.isfinite(x) for x in res.v_out + res.v_in + res.i_in + res.i_load)
    assert min(res.v_out) >= 0 and min(res.v_in) >= 0
    assert res.v_out_min >= 0 and 0 <= res.time_below_s <= p.duration_s + 1e-9


# ----------------------------------------------------- H3: validation is exhaustive
BAD = [("reg_kind", "magic"), ("v_src", 0), ("v_src", -5), ("v_src", math.nan), ("r_src", 0), ("r_src", math.inf),
       ("c_in_f", -1e-6), ("esr_in", -0.1), ("v_set", 0), ("dropout_v", -1), ("i_rated_a", 0), ("r_out", 0),
       ("t_response_s", 0), ("t_response_s", -1), ("i_limit_a", 0), ("iq_a", -0.001), ("efficiency", 0),
       ("efficiency", 1.2), ("vin_min", -1), ("c_out_f", 0), ("esr_out", -1), ("i_base_a", -0.1),
       ("i_peak_a", math.nan), ("burst_s", 0), ("period_s", 0), ("t_first_s", -1), ("n_bursts", 0),
       ("n_bursts", 2.5), ("n_bursts", 10**9), ("v_threshold", -1), ("v_src", "5"), ("efficiency", True),
       ("period_s", 1e3)]


@pytest.mark.parametrize("field,value", BAD)
def test_every_bad_parameter_is_a_simulation_error(field, value):
    with pytest.raises(bo.SimulationError):
        bo.simulate(replace(bo.SimParams(), **{field: value}))


def test_reviewer_efficiency_zero_is_no_longer_a_zero_division():
    with pytest.raises(bo.SimulationError, match="efficiency"):
        bo.simulate(bo.SimParams(reg_kind="switcher", efficiency=0))


@pytest.mark.parametrize("t,i", [([0.0, math.nan], [0.1, 0.1]), ([0.0, 1e-3], [0.1, math.inf]),
                                 ([0.0, 1e-3], [0.1, -0.5]), ([0.0, 0.0], [0.1, 0.1]), ([0.0, 500.0], [0.1, 0.1])])
def test_bad_measured_profiles_are_rejected(t, i):
    with pytest.raises(bo.SimulationError):
        bo.simulate(bo.with_profile(bo.SimParams(), t, i))


# ------------------------------------------------------------ monotonic physics
@SIM
@given(params(), st.floats(1.5, 10))
def test_more_source_resistance_never_helps(p, factor):
    a = bo.simulate(p)
    b = bo.simulate(replace(p, r_src=p.r_src * factor))
    # Tolerance = integrator accuracy. The step size depends on R (loop gain), so two runs use
    # different steps; at a forced identical fine step the ordering is always right, but at the
    # default step the discretisation error is ~0.5 % of the dip (checked case: +1.0 mV on a
    # 200 mV dip, −0.45 mV once both runs share a 0.1 µs step).
    assert b.v_out_min <= a.v_out_min + 1e-3 + 0.01 * a.droop_v


@SIM
@given(params(), st.floats(1.2, 5))
def test_bigger_load_step_never_helps(p, factor):
    a = bo.simulate(p)
    b = bo.simulate(replace(p, i_peak_a=p.i_peak_a * factor))
    assert b.v_out_min <= a.v_out_min + 1e-3 + 0.01 * a.droop_v          # see the tolerance note above


@SIM
@given(params(), st.floats(2, 10))
def test_more_output_capacitance_never_makes_the_dip_worse(p, factor):
    assume(p.c_out_f * factor <= 10)
    a = bo.simulate(p)
    b = bo.simulate(replace(p, c_out_f=p.c_out_f * factor))
    # tolerance: the first-order regulator lag can trade a few mV between the two
    assert b.v_out_min >= a.v_out_min - max(0.02, 0.01 * p.v_set)


def test_only_the_requested_bursts_are_simulated():
    p = bo.SimParams(n_bursts=2)
    end = p.t_first_s + p.n_bursts * p.period_s
    assert bo._load_current(p, end) == p.i_base_a                     # burst 3 would start here
    assert bo._load_current(p, p.t_first_s + p.period_s) == p.i_peak_a  # burst 2 does
    res = bo.simulate(p, max_points=10**7)
    assert res.i_load[-1] == p.i_base_a
