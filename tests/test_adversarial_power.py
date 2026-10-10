"""Adversarial / property tests for the power engine.

Invalid input must be rejected before analysis, valid input must give finite and
physically ordered results, and failures must propagate down the tree.
"""

import json
import math
from dataclasses import replace

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from benchbuddy.core import power as P

FAST = settings()          # examples / deadline come from the active profile (tests/conftest.py)

finite_pos = st.floats(min_value=0.01, max_value=50, allow_nan=False, allow_infinity=False)
currents = st.floats(min_value=0, max_value=3000, allow_nan=False, allow_infinity=False)


def _base():
    return P.Project([P.Rail("S", 5.0, "supply", max_ma=2000, r_internal_ohm=0.2, v_min=4.75, capacity_mah=2000),
                      P.Rail("R", 3.3, "ldo", parent="S", max_ma=800, dropout_v=0.3, iq_ma=1)],
                     [P.Load("L", "R", 2, 50, 300, 1, 0.5)])


# --------------------------------------------------------------- strategies
@st.composite
def projects(draw):
    """Random but valid trees: 1-2 supplies, up to 4 regulators, up to 6 loads."""
    rails = []
    for k in range(draw(st.integers(1, 2))):
        v = draw(st.floats(1.0, 24.0))
        rails.append(P.Rail(f"S{k}", v, "supply", max_ma=draw(st.floats(10, 5000)),
                            v_min=draw(st.one_of(st.none(), st.floats(0.5, 1.0).map(lambda f: f * v))),
                            r_internal_ohm=draw(st.floats(0, 3)),
                            capacity_mah=draw(st.one_of(st.none(), st.floats(50, 10000)))))
    for k in range(draw(st.integers(0, 4))):
        parent = draw(st.sampled_from(rails)).name
        kind = draw(st.sampled_from(["ldo", "buck", "boost"]))
        rails.append(P.Rail(f"R{k}", draw(st.floats(0.8, 24.0)), kind, parent=parent,
                            max_ma=draw(st.floats(10, 5000)), dropout_v=draw(st.floats(0, 2)),
                            min_vin=draw(st.floats(0.5, 5)), efficiency=draw(st.floats(0.5, 1.0)),
                            iq_ma=draw(st.floats(0, 10)), theta_ja=draw(st.floats(0, 250))))
    loads = []
    for k in range(draw(st.integers(0, 6))):
        loads.append(P.Load(f"L{k}", draw(st.sampled_from(rails)).name, draw(st.integers(1, 20)),
                            draw(currents), draw(currents), draw(currents), draw(st.floats(0, 1))))
    return P.Project(rails, loads)


def _finite_report(rep: P.PowerReport) -> None:
    for rr in rep.rails:
        for f in ("avg_ma", "peak_ma", "in_avg_ma", "in_peak_ma", "util_peak_pct", "util_avg_pct",
                  "dissipation_avg_w", "dissipation_peak_w"):
            v = getattr(rr, f)
            assert math.isfinite(v) and v >= 0, (rr.rail.name, f, v)
        for f in ("v_peak_v", "v_in_peak_v", "temp_rise_avg_c", "runtime_h"):
            v = getattr(rr, f)
            assert v is None or (math.isfinite(v) and v >= 0), (rr.rail.name, f, v)


# ------------------------------------------------- 1, 15: finite or declared error
@FAST
@given(projects())
def test_random_valid_projects_give_finite_reports(proj):
    rep = P.analyse(proj)
    _finite_report(rep)
    assert {rr.rail.name for rr in rep.rails} == {r.name for r in proj.rails}


# ------------------------------------------- 2, 3: impossible input never analysed
BAD_RAIL = [("v_out", -5), ("v_out", 0), ("v_out", math.nan), ("v_out", math.inf), ("max_ma", -1), ("max_ma", 0),
            ("r_internal_ohm", -0.1), ("capacity_mah", 0), ("capacity_mah", -5), ("v_min", -1), ("v_min", 6.0),
            ("v_min", math.nan), ("iq_ma", -1), ("theta_ja", -1), ("efficiency", 0), ("efficiency", 1.5),
            ("efficiency", math.nan), ("dropout_v", -0.1), ("min_vin", -1), ("v_out", "5"), ("max_ma", True),
            ("name", ""), ("name", 7)]
BAD_LOAD = [("qty", 0), ("qty", -2), ("qty", 2.5), ("qty", True), ("i_active_ma", -100), ("i_peak_ma", -50),
            ("i_sleep_ma", -1), ("i_active_ma", math.nan), ("i_peak_ma", math.inf), ("duty", -0.1), ("duty", 1.1),
            ("duty", math.nan), ("name", ""), ("rail", None)]


@pytest.mark.parametrize("field,value", BAD_RAIL)
def test_impossible_rail_values_are_rejected(field, value):
    proj = _base()
    proj.rails[0] = replace(proj.rails[0], **{field: value})
    with pytest.raises(P.ProjectError):
        P.analyse(proj)


@pytest.mark.parametrize("field,value", BAD_LOAD)
def test_impossible_load_values_are_rejected(field, value):
    proj = _base()
    proj.loads[0] = replace(proj.loads[0], **{field: value})
    with pytest.raises(P.ProjectError):
        P.analyse(proj)


def test_reviewer_probes_no_longer_say_ok():
    for mutate in (lambda p: setattr(p.rails[0], "v_out", -5.0), lambda p: setattr(p.rails[0], "v_out", math.nan),
                   lambda p: setattr(p.loads[0], "i_active_ma", -100.0), lambda p: setattr(p.loads[0], "qty", -2)):
        proj = _base()
        mutate(proj)
        with pytest.raises(P.ProjectError):
            P.analyse(proj)


# ------------------------------------------------------- 10: persistence round trip
@FAST
@given(projects())
def test_dict_and_json_round_trip_preserve_results(proj):
    a = P.analyse(proj)
    b = P.analyse(P.Project.from_dict(json.loads(json.dumps(proj.to_dict()))))
    assert [(r.rail.name, r.status, r.avg_ma, r.peak_ma, r.v_peak_v) for r in a.rails] == \
           [(r.rail.name, r.status, r.avg_ma, r.peak_ma, r.v_peak_v) for r in b.rails]


@pytest.mark.parametrize("text", [
    '{"rails":[{"name":"S","v_out":NaN}],"loads":[]}',
    '{"rails":[{"name":"S","v_out":Infinity}],"loads":[]}',
    '{"rails":[{"name":"S","v_out":5,"surprise":1}],"loads":[]}',
    '{"rails":[{"name":"S","v_out":"5"}],"loads":[]}',
    '{"rails":"S","loads":[]}',
    '[1,2,3]',
    '{"rails":[{"name":"S","v_out":5}],"loads":[{"name":"x","rail":"S","qty":1.5}]}',
    'not json at all',
])
def test_malformed_files_are_rejected_even_non_strict(tmp_path, text):
    f = tmp_path / "p.json"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(P.ProjectError):
        P.Project.load(f, strict=False)


def test_non_strict_load_opens_impossible_values_for_fixing(tmp_path):
    f = tmp_path / "p.json"
    f.write_text('{"rails":[{"name":"S","v_out":-5}],"loads":[]}', encoding="utf-8")
    with pytest.raises(P.ProjectError):
        P.Project.load(f)                         # strict: refuses
    proj = P.Project.load(f, strict=False)        # GUI: opens...
    with pytest.raises(P.ProjectError):
        P.analyse(proj)                           # ...but analysis still refuses


# ------------------------------------------------------------ 6, 9: monotonicity
def _states(rep):
    return [r.regulating for r in rep.rails]


@FAST
@given(projects(), st.integers(0, 5), st.floats(1, 500))
def test_more_load_never_lowers_upstream_current(proj, k, extra):
    """Holds while every regulator stays in the same state. (A boost that shuts down under the
    extra load legitimately stops drawing - that case is covered by its own error test.)"""
    assume(proj.loads)
    load = proj.loads[k % len(proj.loads)]
    before = P.analyse(proj)
    load.i_active_ma += extra
    load.i_peak_ma += extra
    after = P.analyse(proj)
    assume(_states(before) == _states(after))
    for a, b in zip(before.rails, after.rails):
        assert b.peak_ma >= a.peak_ma - 1e-6, a.rail.name


@FAST
@given(projects(), st.floats(0.01, 2.0))
def test_more_source_resistance_never_raises_the_rail(proj, extra_r):
    before = P.analyse(proj)
    for r in proj.rails:
        if r.kind == "supply":
            r.r_internal_ohm += extra_r
    after = P.analyse(proj)
    if _states(before) == _states(after):
        for a, b in zip(before.rails, after.rails):
            assert b.v_peak_v <= a.v_peak_v + 1e-6, a.rail.name
    else:                                  # something gave up: it must be a regulator that got worse
        assert any(a.regulating and not b.regulating for a, b in zip(before.rails, after.rails))


@FAST
@given(st.floats(4, 24), st.floats(0, 1.0), st.floats(1, 12), st.floats(10, 2000), st.floats(0.5, 0.95), st.floats(0.01, 0.2))
def test_lower_efficiency_never_reduces_input_current(vs, r, vo, i, eff, d_eff):
    assume(vo < vs - 1)

    def run(e):
        p = P.Project([P.Rail("S", vs, "supply", max_ma=1e6, r_internal_ohm=r),
                       P.Rail("B", vo, "buck", parent="S", max_ma=1e6, efficiency=e, dropout_v=0.5)],
                      [P.Load("x", "B", 1, i, i)])
        return P.analyse(p).by_name("B")

    hi, lo = run(min(eff + d_eff, 1.0)), run(eff)
    if hi.regulating and lo.regulating:
        assert lo.in_peak_ma >= hi.in_peak_ma - 1e-6


# ------------------------------------------------- self-consistency + collapse (H4)
@FAST
@given(st.floats(3, 24), st.floats(0.01, 5), st.floats(1, 12), st.floats(10, 3000), st.floats(0.5, 1.0),
       st.floats(0, 1.5))
def test_buck_on_resistive_source_is_self_consistent(vs, r, vo, i_out, eff, drop):
    """Reviewer H4: the converter's input current and the source's sag must agree exactly."""
    assume(vo < vs)
    p = P.Project([P.Rail("S", vs, "supply", max_ma=1e6, r_internal_ohm=r),
                   P.Rail("B", vo, "buck", parent="S", max_ma=1e6, efficiency=eff, dropout_v=drop)],
                  [P.Load("x", "B", 1, i_out, i_out)])
    rep = P.analyse(p)
    b, s = rep.by_name("B"), rep.by_name("S")
    v = s.v_peak_v
    # the two equations every operating point must satisfy simultaneously
    ratio = min(1.0, vo / (eff * v)) if v > 0 else 1.0
    assert b.in_peak_ma == pytest.approx(ratio * i_out, rel=1e-6)
    assert v == pytest.approx(max(vs - r * b.in_peak_ma / 1000, 0.0), rel=1e-6, abs=1e-6)
    assert b.v_peak_v == pytest.approx(max(min(vo, v - drop), 0.0), rel=1e-6, abs=1e-6)
    assert b.regulating == (v >= vo + drop - 1e-6)
    # and when a regulated solution exists, it's the textbook one
    p_in = vo * i_out / 1000 / eff
    if p_in < vs * vs / (4 * r) * 0.98:
        v_up = (vs + math.sqrt(vs * vs - 4 * r * p_in)) / 2          # stable root of V = Vs − R·P/V
        if v_up >= max(vo + drop, vo / eff) + 1e-3:
            assert b.regulating and v == pytest.approx(v_up, rel=1e-5)


@FAST
@given(st.floats(2, 12), st.floats(0.5, 5), st.floats(5, 24), st.floats(50, 3000), st.floats(0.5, 1.0))
def test_boost_that_cannot_be_powered_hiccups(vs, r, vo, i_out, eff):
    assume(vo > vs)
    p = P.Project([P.Rail("S", vs, "supply", max_ma=1e6, r_internal_ohm=r),
                   P.Rail("B", vo, "boost", parent="S", max_ma=1e6, efficiency=eff, min_vin=0.5 * vs)],
                  [P.Load("x", "B", 1, i_out, i_out)])
    b = P.analyse(p).by_name("B")
    p_in = vo * i_out / 1000 / eff
    p_max = vs * vs / (4 * r)
    if p_in > p_max * 1.02:
        assert not b.regulating and b.status == "error"
        assert any("hiccup" in m or "is off" in m for _, m in b.messages)
    elif p_in < p_max * 0.98:
        i = (vs - math.sqrt(vs * vs - 4 * r * p_in)) / (2 * r)
        if vs - i * r >= 0.5 * vs:                 # stays above its UVLO: must run and be exact
            assert b.regulating
            assert b.in_peak_ma == pytest.approx(i * 1000, rel=1e-4)


# -------------------------------------------------------- H5: failure propagates
def test_upstream_dropout_propagates_down_the_tree():
    p = P.Project([P.Rail("S", 3.0, "supply", max_ma=2000),
                   P.Rail("A", 3.3, "ldo", parent="S", max_ma=800, dropout_v=1.1),
                   P.Rail("B", 1.8, "ldo", parent="A", max_ma=500, dropout_v=0.3),
                   P.Rail("C", 5.0, "boost", parent="B", max_ma=500, min_vin=1.8)],
                  [P.Load("mcu", "C", 1, 50, 50)])
    rep = P.analyse(p)
    a, b, c = rep.by_name("A"), rep.by_name("B"), rep.by_name("C")
    assert not a.regulating and a.v_peak_v == pytest.approx(1.9)
    assert not b.regulating and b.v_peak_v == pytest.approx(1.6)
    assert not c.regulating and c.v_peak_v == 0                         # boost below its UVLO
    assert all(r.status == "error" for r in (a, b, c))
    assert rep.status == "error"


@FAST
@given(projects())
def test_no_rail_reports_ok_when_its_input_cant_support_it(proj):
    rep = P.analyse(proj)
    for rr in rep.rails:
        if rr.rail.kind != "supply" and not rr.regulating:
            assert rr.status == "error", rr.rail.name


@FAST
@given(projects())
def test_regulator_output_never_exceeds_what_physics_allows(proj):
    rep = P.analyse(proj)
    for rr in rep.rails:
        r = rr.rail
        if r.kind in ("ldo", "buck"):
            assert rr.v_peak_v <= r.v_out + 1e-9
            assert rr.v_peak_v <= max(rr.v_in_peak_v - r.dropout_v, 0) + 1e-9
        if r.kind == "supply":
            assert rr.v_peak_v <= (r.v_min or r.v_out) + 1e-9
