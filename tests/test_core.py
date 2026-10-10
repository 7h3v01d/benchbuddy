import math
from pathlib import Path

import pytest

from benchbuddy.core import capacitors as cap
from benchbuddy.core import power, resistors as res
from benchbuddy.core.identify import identify
from benchbuddy.core.partsdb import PartsDB
from benchbuddy.core.presets import (LOAD_PRESETS, REGULATOR_PRESETS, SOURCE_PRESETS,
                                     make_load, make_rail)
from benchbuddy.core.units import format_rkm, format_value, parse_value


# ------------------------------------------------------------------ units
@pytest.mark.parametrize("text,expected", [
    ("4k7", 4700), ("10k", 10000), ("2.2M", 2.2e6), ("100n", 100e-9), ("10uF", 10e-6),
    ("4R7", 4.7), ("0.5", 0.5), ("220", 220), ("1M5", 1.5e6), ("4u7", 4.7e-6),
    ("100 nF", 100e-9), ("5V", 5), ("33pF", 33e-12), ("22Ω", 22), ("3.3k", 3300),
])
def test_parse_value(text, expected):
    assert parse_value(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "k", "1.2.3"])
def test_parse_value_bad(bad):
    with pytest.raises(ValueError):
        parse_value(bad)


def test_format():
    assert format_value(4700, "Ω") == "4.7 kΩ"
    assert format_value(100e-9, "F") == "100 nF"
    assert format_value(2.2e6, "Ω") == "2.2 MΩ"
    assert format_rkm(4700) == "4k7"
    assert format_rkm(220) == "220R"
    assert format_rkm(4.7) == "4R7"


# -------------------------------------------------------------- resistors
def test_color_decode():
    d = res.decode_color_bands(["yellow", "violet", "red", "gold"])
    assert d.ohms == 4700 and d.tolerance_pct == 5
    d5 = res.decode_color_bands(["brown", "black", "black", "red", "brown"])
    assert d5.ohms == 10000 and d5.tolerance_pct == 1
    d6 = res.decode_color_bands(["brown", "black", "black", "red", "brown", "red"])
    assert d6.tempco_ppm == 50


def test_color_encode_roundtrip():
    for ohms in (1, 4.7, 10, 220, 4700, 10000, 100000, 2.2e6, 0.47):
        bands = res.encode_color_bands(ohms, 4)
        assert res.decode_color_bands(bands).ohms == pytest.approx(ohms, rel=1e-6)
    for ohms in (100, 1500, 47000, 196000):
        bands = res.encode_color_bands(ohms, 5, 1.0)
        assert res.decode_color_bands(bands).ohms == pytest.approx(ohms, rel=1e-6)


def test_color_bad():
    with pytest.raises(ValueError):
        res.decode_color_bands(["pink", "red", "red", "gold"])
    with pytest.raises(ValueError):
        res.decode_color_bands(["red", "red"])


def test_smd_codes():
    assert res.decode_smd_code("472") == 4700
    assert res.decode_smd_code("1002") == 10000
    assert res.decode_smd_code("4R7") == pytest.approx(4.7)
    assert res.decode_smd_code("000") == 0
    assert res.decode_smd_code("01C") == pytest.approx(10000)    # EIA-96: 100 x 100
    assert res.decode_smd_code("68X") == pytest.approx(49.9, rel=1e-3)      # EIA-96: code 68 = 499
    assert res.decode_smd_code("01A") == pytest.approx(100)
    assert res.decode_smd_code("49B") == pytest.approx(3160, rel=1e-3)      # EIA-96: code 49 = 316


def test_nearest_standard():
    assert res.nearest_standard(4800, "E24") == 4700
    assert res.nearest_standard(4800, "E12") == 4700
    assert res.nearest_standard(100, "E12") == 100
    lo, hi = res.standard_neighbours(4800, "E12")
    assert lo == 4700 and hi == 5600


def test_series_parallel():
    assert res.series_resistance([100, 220]) == 320
    assert res.parallel_resistance([100, 100]) == pytest.approx(50)


def test_led_resistor():
    r = res.led_resistor(5, 2.0, 0.015)
    assert r.exact_ohms == pytest.approx(200)
    assert r.chosen_ohms >= 200          # always rounds up so current is safe
    assert r.actual_current_a <= 0.015 + 1e-9
    with pytest.raises(ValueError):
        res.led_resistor(2.0, 3.0, 0.01)


def test_divider():
    d = res.divider_vout(5, 10000, 10000)
    assert d.vout == pytest.approx(2.5)
    loaded = res.divider_vout(5, 10000, 10000, load_ohms=10000)
    assert loaded.vout < 2.5
    best = res.divider_find(5, 3.3, "E24")[0]
    assert abs(best[3]) < 1.0


# ------------------------------------------------------------- capacitors
def test_cap_codes():
    assert cap.decode_cap_code("104").farads == pytest.approx(100e-9)
    assert cap.decode_cap_code("104J").tolerance == "±5 %"
    assert cap.decode_cap_code("22").farads == pytest.approx(22e-12)
    assert cap.decode_cap_code("4n7").farads == pytest.approx(4.7e-9)
    assert cap.decode_cap_code("p33").farads == pytest.approx(0.33e-12)
    assert cap.decode_cap_code("0.1").farads == pytest.approx(0.1e-6)
    assert cap.decode_cap_code("473K").farads == pytest.approx(47e-9)


def test_rc():
    assert cap.rc_time_constant(10e3, 10e-6) == pytest.approx(0.1)
    assert cap.rc_cutoff_hz(1591.5, 100e-9) == pytest.approx(1000, rel=1e-3)
    assert cap.rc_charge_time(1000, 1e-6, 0, 0.632) == pytest.approx(1e-3, rel=1e-2)
    assert cap.capacitor_reactance(1e-6, 1000) == pytest.approx(159.15, rel=1e-3)
    assert cap.holdup_capacitance(0.1, 0.01, 5, 4).farads_needed == pytest.approx(1e-3)


# -------------------------------------------------------------- power budget
def _usb_esp32(extra=None):
    usb = make_rail(SOURCE_PRESETS["USB 2.0 port (5 V, 500 mA)"], "USB")
    ldo = make_rail(REGULATOR_PRESETS["AMS1117-3.3 (LDO, 800 mA)"], "3V3", parent="USB")
    loads = [make_load("ESP32 DevKit (WROOM-32)", "3V3")] + (extra or [])
    return power.Project([usb, ldo], loads)


def test_basic_esp32_on_usb():
    rep = power.analyse(_usb_esp32())
    r33 = rep.by_name("3V3")
    assert r33.avg_ma == pytest.approx(80)
    assert r33.peak_ma == pytest.approx(500)
    usb = rep.by_name("USB")
    assert usb.avg_ma == pytest.approx(80 + 5)            # + AMS1117 Iq (5 mA)
    # LDO dissipation: (5-3.3)*80mA + 5*5mA
    assert r33.dissipation_avg_w == pytest.approx(1.7 * 0.08 + 5 * 0.005)
    assert rep.ok


def test_overload_detected():
    big = [make_load("SG90 micro servo", "3V3", qty=4)]
    rep = power.analyse(_usb_esp32(big))
    assert rep.by_name("3V3").status == power.ERROR
    assert not rep.ok


def test_sag_and_dropout_with_9v_battery_style_source():
    # 4xAA nearly flat feeding an LDO that needs 4.4 V in for 3.3 V
    batt = make_rail(SOURCE_PRESETS["4x AA alkaline (6 V)"], "BATT")
    batt.v_min = 4.4
    ldo = make_rail(REGULATOR_PRESETS["AMS1117-3.3 (LDO, 800 mA)"], "3V3", parent="BATT")
    p = power.Project([batt, ldo], [make_load("ESP32 DevKit (WROOM-32)", "3V3")])
    rep = power.analyse(p)
    batt_rep = rep.by_name("BATT")
    assert batt_rep.v_peak_v < 4.4                     # sags below cut-off at peak
    assert any(l == power.ERROR and "dropout" in t for l, t in batt_rep.messages)


def test_runtime_and_duty_cycle():
    batt = make_rail(SOURCE_PRESETS["18650 Li-ion 2600 mAh"], "BATT")
    ldo = make_rail(REGULATOR_PRESETS["HT7333 (LDO, 250 mA, low Iq)"], "3V3", parent="BATT")
    # 80 mA for 2 s every 10 min, 0.01 mA otherwise
    duty = 2 / 600
    esp = power.Load("ESP", "3V3", 1, 80, 250, 0.01, duty)
    rep = power.analyse(power.Project([batt, ldo], [esp]))
    expected_avg = 80 * duty + 0.01 * (1 - duty)
    assert rep.by_name("3V3").avg_ma == pytest.approx(expected_avg)
    assert rep.by_name("BATT").runtime_h == pytest.approx(2600 * 0.8 / (expected_avg + 0.004), rel=1e-6)


def test_buck_efficiency_math():
    src = make_rail(SOURCE_PRESETS["Wall adapter 12 V 2 A"], "12V")
    buck = make_rail(REGULATOR_PRESETS["Buck 5 V 3 A (LM2596 / generic)"], "5V", parent="12V")
    buck.iq_ma = 0
    p = power.Project([src, buck], [power.Load("x", "5V", 1, 1000, 1000)])
    rep = power.analyse(p)
    # 5 V * 1 A / 0.85 / 12 V
    assert rep.by_name("12V").avg_ma == pytest.approx(5 * 1000 / 0.85 / 12)


def test_validation_errors():
    with pytest.raises(power.ProjectError):
        power.analyse(power.Project([power.Rail("a", 5, "ldo", parent="zzz")], []))
    with pytest.raises(power.ProjectError):
        power.analyse(power.Project([power.Rail("a", 5)], [power.Load("x", "nope")]))
    with pytest.raises(power.ProjectError):
        power.analyse(power.Project([power.Rail("a", 5), power.Rail("a", 3)], []))


def test_project_save_load(tmp_path):
    p = _usb_esp32()
    f = tmp_path / "proj.json"
    p.save(f)
    q = power.Project.load(f)
    assert q.to_dict() == p.to_dict()


def test_all_presets_construct_and_analyse():
    for name in LOAD_PRESETS:
        make_load(name, "USB")
    for name, r in {**SOURCE_PRESETS, **REGULATOR_PRESETS}.items():
        assert r.max_ma > 0


def test_wire_drop():
    v, r = power.wire_drop(22, 1.0, 1.0)             # 1 m out + 1 m back of 22 AWG
    assert r == pytest.approx(0.10592)
    assert v == pytest.approx(r)
    assert power.smallest_awg_for_drop(1.0, 2.0, 0.1) in power.AWG_OHM_PER_KM


def test_gpio_check():
    out = power.gpio_check("Arduino Uno / Nano (ATmega328P, 5 V)", [10, 25, 50])
    levels = [l for l, _ in out]
    assert levels[0] == power.OK and levels[1] == power.WARN and levels[2] == power.ERROR


def test_duty_and_ohm():
    d = power.duty_cycle_average(80, 2, 0.01, 598, capacity_mah=2000)
    assert d.avg_ma == pytest.approx((160 + 5.98) / 600)
    assert d.runtime_h > 1000
    o = power.ohms_law(v=5, r=250)
    assert o["i"] == pytest.approx(0.02) and o["p"] == pytest.approx(0.1)
    with pytest.raises(ValueError):
        power.ohms_law(v=5)


# ------------------------------------------------------------------- parts db
@pytest.fixture()
def db():
    d = PartsDB(":memory:")
    yield d
    d.close()


def test_seed_and_search(db):
    assert len(db.all()) > 50
    assert db.search("2n3904")[0].part_number == "2N3904"
    assert db.search("J3Y")[0].part_number == "S8050"          # SMD marking lookup
    assert db.search("1117")[0].part_number == "AMS1117-3.3"
    assert any(p.part_number == "IRLZ44N" for p in db.search("logic level mosfet"))
    assert db.search("zzzzzz") == []


def test_crud_and_inventory(db):
    pid = db.add("XYZ123", "IC", "Test chip", qty=5, location="Drawer B2")
    p = db.get(pid)
    assert p.user_added and p.qty == 5 and p.location == "Drawer B2"
    db.update(pid, qty=2)
    assert db.get(pid).qty == 2
    assert db.search("drawer b2")[0].id == pid
    assert db.search("xyz", in_stock_only=True)[0].id == pid
    db.delete(pid)
    assert db.get(pid) is None
    with pytest.raises(ValueError):
        db.update(pid, bogus=1)
    with pytest.raises(ValueError):
        db.add("  ")


def test_identify(db):
    m = identify("104", db)
    kinds = {x.kind for x in m}
    assert {"resistor", "capacitor"} <= kinds
    assert any("100 nF" in x.title for x in m if x.kind == "capacitor")
    assert any("100 kΩ" in x.title for x in m if x.kind == "resistor")
    assert any(x.kind == "part" for x in identify("J3Y", db))


# ------------------------------------------------------------- extra calculators
from benchbuddy.core import calcs  # noqa: E402


def test_adc_divider_never_exceeds_adc_range():
    for c in calcs.adc_divider(4.2, 3.3):
        assert c["vout_at_max"] <= 3.3
        assert abs(c["error_pct"]) < 5
    best = calcs.adc_divider(4.2, 3.3)[0]
    assert best["drain_ua"] < 100
    with pytest.raises(ValueError):
        calcs.adc_divider(3.0, 3.3)


def test_trace_width_ipc2221():
    r = calcs.trace_width(1.0, 10, 1.0, True, length_mm=100)
    assert r["width_mm"] == pytest.approx(0.30, abs=0.02)
    assert r["resistance_mohm"] > 0 and r["drop_mv"] == pytest.approx(r["resistance_mohm"])   # 1 A
    wide = calcs.trace_width(3.0, 10, 1.0, True)["width_mm"]
    internal = calcs.trace_width(1.0, 10, 1.0, False)["width_mm"]
    assert wide > r["width_mm"] and internal > r["width_mm"]


def test_buck_boost_inductors():
    b = calcs.buck_inductor(12, 5, 1.0, 500e3)
    assert b["inductance_h"] == pytest.approx(19.4e-6, rel=0.01)
    assert b["peak_a"] == pytest.approx(1.15)
    bo = calcs.boost_inductor(3.7, 5, 1.0, 1e6, efficiency=0.85)
    assert bo["input_a"] == pytest.approx(5 / (3.7 * 0.85), rel=1e-6)
    assert bo["peak_a"] > bo["input_a"]
    with pytest.raises(ValueError):
        calcs.buck_inductor(3.3, 5, 1, 1e5)
    with pytest.raises(ValueError):
        calcs.boost_inductor(5, 3.3, 1, 1e5)


def test_i2c_pullup():
    r = calcs.i2c_pullup(3.3, 100)
    assert r["r_min"] == pytest.approx(966.7, rel=1e-3)
    assert r["r_min"] <= r["suggested"] <= r["r_max"]
    with pytest.raises(ValueError):
        calcs.i2c_pullup(3.3, 1000, "Fast-mode Plus (1 MHz)")      # far too much capacitance


def test_base_resistor():
    r = calcs.base_resistor(0.1, 3.3)           # 100 mA relay from a 3.3 V pin
    assert r["ib_a"] == pytest.approx(0.01)
    assert r["chosen_ohms"] <= r["exact_ohms"]
    assert r["gpio_ok"]
    assert calcs.base_resistor(1.0, 3.3)["actual_ib_ma"] > 20          # needs a Darlington/MOSFET instead
    assert not calcs.base_resistor(1.0, 3.3)["gpio_ok"]


# ------------------------------------------------------------ brown-out simulator
from dataclasses import replace as _replace  # noqa: E402

from benchbuddy.core import brownout as bo  # noqa: E402


def test_sim_steady_state_matches_hand_calc():
    r = bo.simulate(bo.SimParams())
    assert r.v_out_nominal == pytest.approx(3.3 - 0.15 * 0.08, abs=0.002)
    assert not r.brownout


def test_sim_capacitance_matters():
    base = bo.SimParams()
    tiny = bo.simulate(_replace(base, c_out_f=1e-6, c_in_f=0))
    big = bo.simulate(_replace(base, c_out_f=470e-6))
    assert tiny.brownout and not big.brownout
    assert tiny.v_out_min < big.v_out_min


def test_sim_esr_causes_instant_dip():
    low = bo.simulate(bo.SimParams(esr_out=0.05))
    high = bo.simulate(bo.SimParams(esr_out=1.0))
    step = 0.5 - 0.08
    # the load step appears instantly across the ESR: nominal - dI*ESR (regulator recovers a little)
    assert high.v_out_min == pytest.approx(high.v_out_nominal - step * 1.0, abs=0.1)
    assert high.v_out_min < low.v_out_min - 0.2
    assert high.brownout and not low.brownout


def test_sim_dropout_when_source_is_flat():
    r = bo.simulate(bo.SimParams(v_src=4.3))
    assert r.v_in_min < 4.3
    assert any("dropout" in t for _, t in r.notes)
    assert r.v_out_min < r.v_out_nominal - 0.1


def test_sim_source_resistance_sags_input():
    low = bo.simulate(bo.SimParams(r_src=0.1, c_in_f=0))
    high = bo.simulate(bo.SimParams(r_src=1.5, c_in_f=0))
    assert high.v_in_min < low.v_in_min - 0.5


def test_sim_switcher_and_none_modes_run():
    sw = bo.simulate(bo.SimParams(reg_kind="switcher", v_set=3.3, vin_min=3.0, v_src=5.0))
    assert sw.v_out_nominal > 3.0
    none = bo.simulate(bo.SimParams(reg_kind="none", v_set=5.0, c_out_f=10e-6, v_threshold=4.5))
    assert none.v_out_nominal == pytest.approx(5.0, abs=0.1)


def test_sim_validation():
    for bad in (dict(period_s=1e-4, burst_s=1e-3), dict(i_peak_a=0.01), dict(c_out_f=0),
                dict(reg_kind="magic"), dict(n_bursts=0), dict(r_src=0)):
        with pytest.raises(ValueError):
            bo.simulate(_replace(bo.SimParams(), **bad))


def test_suggest_output_cap_fixes_brownout():
    p = _replace(bo.SimParams(), c_out_f=1e-6, c_in_f=0)
    assert bo.simulate(p).brownout
    uf, res = bo.suggest_output_cap(p)
    assert uf is not None and uf >= 10
    assert not res.brownout


def test_from_power_rail():
    from benchbuddy.gui.power_tab import example_project
    proj = example_project()
    p = bo.from_power_rail(proj, "3V3")
    assert p.reg_kind == "ldo" and p.v_set == 3.3 and p.v_src == 5.0
    assert p.i_peak_a >= p.i_base_a
    bo.simulate(p)
    s = bo.from_power_rail(proj, "USB 5V")
    assert s.reg_kind == "none"
    bo.simulate(s)


def test_sim_no_regulator_is_smooth_and_matches_hand_calc():
    # SIM800L-style 2 A bursts straight from a LiPo through 0.35 ohm of battery + wiring
    p = bo.SimParams(v_src=3.9, r_src=0.35, c_in_f=0, reg_kind="none", v_set=3.9, c_out_f=100e-6,
                     esr_out=0.1, i_base_a=0.02, i_peak_a=2.0, burst_s=577e-6, period_s=4.615e-3,
                     v_threshold=3.4)
    r = bo.simulate(p)
    # steady burst level is Vsrc - Rsrc*I = 3.9 - 0.7 = 3.2 V (cap is drained within ~35 us)
    assert r.v_out_min == pytest.approx(3.2, abs=0.03)
    assert r.brownout
    # no numerical ringing: source current never overshoots the load current, no sample-to-sample chaos
    assert max(r.i_in) <= 2.0 * 1.05
    assert max(abs(a - b) for a, b in zip(r.v_out, r.v_out[1:])) < 0.7
    # a weak source can only be rescued by a LOT of capacitance (tau = Rsrc*C must outlast the burst)
    uf, fixed = bo.suggest_output_cap(p)
    assert uf is not None and uf >= 1000          # matches the usual "SIM800L needs >= 1000 uF" advice
    assert not fixed.brownout
    # with an input-side limit too severe even for that, nothing in the list works
    harsh = bo.simulate(_replace(p, r_src=1.5, burst_s=3e-3, period_s=10e-3))
    assert harsh.brownout and bo.suggest_output_cap(_replace(p, r_src=1.5, burst_s=3e-3, period_s=10e-3))[0] is None


def test_sim_no_regulator_cap_rides_through_short_burst():
    short = bo.SimParams(v_src=3.9, r_src=0.35, c_in_f=0, reg_kind="none", v_set=3.9, c_out_f=1000e-6,
                         esr_out=0.02, i_base_a=0.02, i_peak_a=2.0, burst_s=100e-6, period_s=4.615e-3,
                         v_threshold=3.4)
    r = bo.simulate(short)
    # 2 A for 100 us from 1000 uF only droops about I*t/C = 0.2 V plus ESR; stays above 3.4 V
    assert not r.brownout
    assert r.v_out_min > 3.4


def test_sim_lag_mode_stable_with_high_source_resistance():
    r = bo.simulate(bo.SimParams(r_src=2.0, v_src=6.0, r_out=0.05, c_in_f=0))
    assert max(abs(a - b) for a, b in zip(r.v_out, r.v_out[1:])) < 0.3
    assert max(r.i_in) < 0.8


# ------------------------------------------------------------ parts library v2
import csv as _csv  # noqa: E402
import sqlite3 as _sqlite3  # noqa: E402

from benchbuddy.core import partsdb as _pdb  # noqa: E402

_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def test_seed_data_integrity():
    pns = [_pdb._norm(p["pn"]) for p in _pdb.ALL_SEED_PARTS]
    assert len(pns) == len(set(pns)), "duplicate part numbers in the seed library"
    assert len(pns) >= 200
    for p in _pdb.ALL_SEED_PARTS:
        assert p["pn"].strip() and p["cat"].strip() and p["desc"].strip(), p
        assert all(m.strip() for m in p.get("marks", [])), p
    # a marking may only map to several parts if they are variants of the same family
    seen = {}
    for p in _pdb.ALL_SEED_PARTS:
        for m in p.get("marks", []):
            seen.setdefault(_pdb._norm(m), set()).add(p["pn"])
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_library_v2_lookups(db):
    assert db.search("6B")[0].part_number == "BC817"
    assert db.search("702")[0].part_number == "2N7002"
    assert db.search("G1")[0].part_number == "2N5551"
    assert db.search("L43")[0].part_number == "BAT54"
    assert db.search("hmc5883l")[0].part_number == "HMC5883L"
    assert any(p.part_number == "ADS1115" for p in db.search("16 bit adc i2c"))
    assert db.get(db.search("J3Y")[0].id).markings == "J3Y"


def test_migration_from_v1_keeps_user_data(tmp_path):
    path = tmp_path / "old.db"
    con = _sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE parts (id INTEGER PRIMARY KEY AUTOINCREMENT, part_number TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Other', description TEXT NOT NULL DEFAULT '',
            package TEXT NOT NULL DEFAULT '', specs TEXT NOT NULL DEFAULT '', markings TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL DEFAULT '',
            user_added INTEGER NOT NULL DEFAULT 0);
        INSERT INTO parts (part_number, category, description, markings, qty, location, user_added)
            VALUES ('S8050','Transistor (BJT)','NPN','J3Y,J3',7,'Drawer 3',0);
        INSERT INTO parts (part_number, category, description, notes, qty, location, user_added)
            VALUES ('2N3904','Transistor (BJT)','NPN','my own note',12,'Box A',0);
        INSERT INTO parts (part_number, category, description, qty, location, user_added)
            VALUES ('MY-WIDGET','Other','custom thing',3,'Shelf',1);
    """)
    con.commit()
    con.close()
    d = _pdb.PartsDB(path)
    assert len(d.all()) > 200                                     # new starter parts merged in
    s8050 = d.find_by_part_number("S8050")
    assert s8050.markings == "J3Y" and s8050.qty == 7 and s8050.location == "Drawer 3"   # fixed + data kept
    t = d.find_by_part_number("2N3904")
    assert t.notes == "my own note" and t.qty == 12               # edits untouched
    w = d.find_by_part_number("my-widget")
    assert w.user_added and w.qty == 3
    assert d.find_by_part_number("BAT54") is not None
    n = len(d.all())
    d.close()
    d2 = _pdb.PartsDB(path)                                       # opening again must not duplicate anything
    assert len(d2.all()) == n
    d2.close()
    # an edited marking must NOT be overwritten by the fix
    con = _sqlite3.connect(path)
    con.execute("UPDATE parts SET markings='J3Y,XYZ' WHERE part_number='S8050'")
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    d3 = _pdb.PartsDB(path)
    assert d3.find_by_part_number("S8050").markings == "J3Y,XYZ"
    d3.close()


def test_csv_roundtrip_and_import_rules(db, tmp_path):
    pid = db.add("ZZ-1", "IC", "Test chip", qty=4, location="Bin 9", markings="ZZ1")
    db.update(db.find_by_part_number("2N3904").id, qty=25, location="Drawer 1")
    out = tmp_path / "inv.csv"
    n = db.export_csv(out)
    assert n == len(db.all())
    assert db.export_csv(tmp_path / "owned.csv", only_owned=True) == 2

    fresh = _pdb.PartsDB(":memory:")
    res = fresh.import_csv(tmp_path / "owned.csv")
    assert res.updated == 1 and res.added == 1 and not res.errors
    assert fresh.find_by_part_number("2N3904").qty == 25
    assert fresh.find_by_part_number("ZZ-1").location == "Bin 9"

    # alias headers, semicolon delimiter, blanks never erase, bad quantity reported not fatal
    messy = tmp_path / "messy.csv"
    messy.write_text("Part;Quantity;Bin;Type\n2N3904;;Shelf 2;\nNEWPART;abc;Tray;Sensor\n;5;x;y\n", encoding="utf-8")
    r2 = fresh.import_csv(messy)
    assert r2.updated == 1 and r2.added == 1 and r2.skipped == 1 and len(r2.errors) == 1
    p = fresh.find_by_part_number("2N3904")
    assert p.qty == 25 and p.location == "Shelf 2" and p.category == "Transistor (BJT)"
    assert fresh.find_by_part_number("NEWPART").category == "Sensor"

    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        fresh.import_csv(bad)
    assert db.get(pid) is not None


def test_datasheet_links(db):
    p = db.find_by_part_number("ESP32-WROOM-32")
    assert db.datasheet_link(p).startswith("https://www.google.com/search?q=ESP32-WROOM-32+datasheet")
    db.update(p.id, datasheet_url="https://example.com/ds.pdf")
    assert db.datasheet_link(db.get(p.id)) == "https://example.com/ds.pdf"
    q = db.find_by_part_number("nRF24L01+")
    assert "nRF24L01%2B" in db.datasheet_link(q)                   # '+' is URL-encoded


def test_photo_attach_replace_delete(db, tmp_path):
    pid = db.find_by_part_number("BC547").id
    img = tmp_path / "shot.png"
    img.write_bytes(_PNG)
    first = db.attach_photo(pid, img)
    assert Path(first).is_file() and db.get(pid).photo_path == first
    second = db.attach_photo(pid, img)                             # replacing removes the old copy
    assert second != first and not Path(first).exists() and Path(second).is_file()
    db.remove_photo(pid)
    assert not Path(second).exists() and db.get(pid).photo_path == ""
    third = db.attach_photo(pid, img)
    db.delete(pid)
    assert not Path(third).exists()
    assert img.exists()                                            # the user's original is never touched
    with pytest.raises(ValueError):
        db.attach_photo(db.find_by_part_number("BC548").id, tmp_path / "missing.png")
    txt = tmp_path / "notes.txt"
    txt.write_text("x")
    with pytest.raises(ValueError):
        db.attach_photo(db.find_by_part_number("BC548").id, txt)


# ------------------------------------------------------------------ OCR + photo matching
from benchbuddy.core import identify as _idf  # noqa: E402
from benchbuddy.core import ocr as _ocr  # noqa: E402


def test_clean_tokens():
    assert _ocr.clean_tokens("bc547\n  ~~ 2N 3904 !!") == ["BC547", "2N", "3904"]
    assert _ocr.clean_tokens("-AMS1117-3.3-") == ["AMS1117-3.3"]
    assert _ocr.clean_tokens("a  x  ...  ---") == []                       # too short / punctuation only


def test_lookalike_variants():
    v = _idf.lookalike_variants("NESSOP", max_subs=3)
    assert v[0] == "NESSOP" and "NE555P" in v and "NESS0P" in v
    assert "NE555P" not in _idf.lookalike_variants("NESSOP", max_subs=2)
    assert len(v) == len(set(v)) <= 400
    assert _idf.lookalike_variants("ZZ", max_subs=1) == ["ZZ", "2Z", "Z2"]


def test_match_candidates_fixes_ocr_mistakes(db):
    m = _idf.match_candidates(["NESSOP"], db)
    assert m and m[0].part_number == "NE555" and "look-alike" in m[0].note
    partial = _idf.match_candidates(["BC54"], db)
    assert {"BC546", "BC547", "BC548"} <= {x.part_number for x in partial}
    exact = _idf.match_candidates(["J3Y"], db)
    assert exact[0].part_number == "S8050" and exact[0].note == "read exactly as printed"
    assert _idf.match_candidates(["QQQQQQ"], db) == []
    # a strongly matching earlier token outranks a weak later one
    both = _idf.match_candidates(["2N3904", "LM35"], db)
    assert both[0].part_number == "2N3904"


@pytest.mark.skipif(not _ocr.ocr_available()[0], reason="Tesseract not installed")
def test_ocr_reads_clean_synthetic_markings(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    hits = 0
    for text in ("2N3904", "AMS1117-3.3", "LM358N", "J3Y"):
        font = ImageFont.load_default(size=60)
        im = Image.new("L", (60 * len(text) + 60, 140), 30)
        ImageDraw.Draw(im).text((30, 25), text, fill=235, font=font)
        f = tmp_path / "chip.png"
        im.save(f)
        hits += text in _ocr.read_markings(f)
    assert hits == 4


def test_ocr_error_paths(tmp_path):
    with pytest.raises((ValueError, RuntimeError)):
        _ocr.read_markings(tmp_path / "nothing.png")


# ------------------------------------------------------------------ pinout
from benchbuddy.core import pinout  # noqa: E402


def test_pinout_boards_load_and_flags_known():
    assert len(pinout.BOARDS) == 5
    for b in pinout.BOARDS.values():
        assert b.pins, b.name
        for p in b.pins:
            assert p.flags <= set(pinout.FLAGS), (b.name, p.label)
            assert p.severity in pinout.SEVERITY_ORDER


def test_esp32_classic_gotchas():
    b = pinout.esp32_devkit()
    assert b.find("12").severity == "caution" and "strap" in b.find("12").flags
    for g in (6, 7, 8, 9, 10, 11):
        assert b.find(str(g)).severity == "avoid"
    for g in (34, 35, 36, 39):
        p = b.find(str(g))
        assert "input_only" in p.flags and pinout.OUT not in p.caps
    # only ADC1 pins survive "analog + Wi-Fi"
    wifi_adc = {p.gpio for p in b.pins if p.matches(["adc_wifi"])}
    assert wifi_adc == {32, 33, 34, 35, 36, 39}
    assert {p.gpio for p in b.pins if p.matches(["dac"])} == {25, 26}


def test_d1_r32_header_matches_arduino_variant():
    b = pinout.wemos_d1_r32()
    expect = {"D0": 3, "D1": 1, "D2": 26, "D3": 25, "D4": 17, "D5": 16, "D6": 27, "D7": 14, "D8": 12,
              "D9": 13, "D10": 5, "D11": 23, "D12": 19, "D13": 18,
              "A0": 2, "A1": 4, "A2": 35, "A3": 34, "A4": 36, "A5": 39, "SDA": 21, "SCL": 22}
    got = {p.label.split(" · ")[0]: p.gpio for p in b.pins}
    assert got == expect


def test_s3_c3_and_avr_details():
    s3 = pinout.esp32_s3()
    assert {p.gpio for p in s3.pins if "strap" in p.flags} == {0, 3, 45, 46}
    assert {p.gpio for p in s3.pins if p.matches(["adc_wifi"])} == set(range(1, 11))
    c3 = pinout.esp32_c3()
    assert {p.gpio for p in c3.pins if "strap" in p.flags} == {2, 8, 9}
    assert {p.gpio for p in c3.pins if "flash" in p.flags} == set(range(12, 18))
    uno = pinout.arduino_uno_nano()
    assert {p.label for p in uno.pins if p.matches(["pwm"])} == {"D3", "D5", "D6", "D9", "D10", "D11"}
    assert not any(p.matches(["adc_wifi"]) for p in uno.pins)


def test_pin_suggest_orders_clean_pins_first():
    b = pinout.esp32_devkit()
    best = pinout.suggest(b, ["out", "boot_safe"])
    assert best and best[0].severity == "ok"
    assert all(p.severity != "avoid" for p in best)
    assert all(p.severity == "ok" for p in pinout.suggest(b, ["out"], include_caution=False))
