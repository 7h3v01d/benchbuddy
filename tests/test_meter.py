import struct

import pytest

from benchbuddy.core import meter as m


def test_frame_roundtrip_and_resync():
    f1 = m.encode_frame(m.T_HELLO, b"fw=1;chip=INA226;mode=NORMAL")
    f2 = m.encode_samples(1000, [(0, 400, 2640), (664, 401, 2641)])
    junk = b"ets Jun  8 2016 00:22:57\r\nrst:0x1 (POWERON_RESET)\r\n\xb5\xb5"
    corrupt = bytearray(f2)
    corrupt[10] ^= 0xFF                                  # payload damage -> checksum fails
    p = m.FrameParser()
    frames = []
    stream = junk + f1 + bytes(corrupt) + f2
    for k in range(0, len(stream), 7):                    # arbitrary chunking
        frames += p.feed(stream[k:k + 7])
    assert [f.ftype for f in frames] == [m.T_HELLO, m.T_SAMPLES]
    assert m.parse_hello(frames[0].payload)["chip"] == "INA226"
    t0, samples = m.decode_samples(frames[1].payload)
    assert t0 == 1000 and samples == [(0, 400, 2640), (664, 401, 2641)]
    assert p.bad_frames >= 1 and p.skipped_bytes > 0


def test_oversized_length_is_rejected():
    p = m.FrameParser()
    bogus = m.SYNC + struct.pack("<BH", 2, 60000) + b"\x00" * 10
    good = m.encode_frame(m.T_LOG, b"ok")
    assert [f.payload for f in p.feed(bogus + good)] == [b"ok"]


def test_chip_conversions():
    ina226, ina219 = m.CHIPS["INA226"], m.CHIPS["INA219"]
    assert ina226.shunt_volts(4000) == pytest.approx(10e-3)        # 4000 × 2.5 µV
    assert ina226.bus_volts(2640) == pytest.approx(3.3)            # × 1.25 mV
    assert ina219.shunt_volts(1000) == pytest.approx(10e-3)        # × 10 µV
    assert ina219.bus_volts((825 << 3) | 0b10) == pytest.approx(3.3)   # bits 15..3 × 4 mV, CNVR ignored
    assert ina226.full_scale_a(0.1) == pytest.approx(0.8192)
    assert ina219.full_scale_a(0.1) == pytest.approx(3.2)
    assert ina226.overrange(0x7FFF) and not ina226.overrange(1000)


def test_capture_from_frames_handles_micros_wrap():
    cap = m.Capture(chip="INA226", r_shunt=0.1)
    near_wrap = 0xFFFFFFFF - 1000
    cap.add_frame(near_wrap, [(0, 4000, 2640), (664, 4000, 2640)])
    cap.add_frame((near_wrap + 1328) & 0xFFFFFFFF, [(0, 4000, 2640)])
    assert list(cap.t) == pytest.approx([0, 664e-6, 1328e-6])
    assert cap.i[0] == pytest.approx(0.1)                           # 10 mV / 0.1 Ω


def _square(cap, high=0.3, low=0.05, period=0.1, on=0.01, rate=10000, seconds=1.0):
    n = int(seconds * rate)
    t = [k / rate for k in range(n)]
    i = [high if (x % period) < on else low for x in t]
    cap.add_values(t, i, [3.3] * n)


def test_stats_on_known_square_wave():
    cap = m.Capture()
    _square(cap)
    s = m.capture_stats(cap)
    assert s.avg_a == pytest.approx(0.1 * 0.3 + 0.9 * 0.05, rel=0.01)   # 75 mA
    assert s.duty == pytest.approx(0.1, abs=0.005)
    assert s.bursts == 10
    assert s.burst_avg_s == pytest.approx(0.01, rel=0.05)
    assert s.active_avg_a == pytest.approx(0.3, rel=0.02)
    assert s.idle_avg_a == pytest.approx(0.05, rel=0.02)
    assert s.charge_mah == pytest.approx(0.075 * s.duration_s / 3.6, rel=0.01)
    assert s.energy_mwh == pytest.approx(s.charge_mah * 3.3, rel=0.001)
    assert s.max_a == 0.3 and s.rate_hz == pytest.approx(10000, rel=0.001)
    load = s.as_load()
    assert load["i_active_ma"] == pytest.approx(300, rel=0.02) and load["duty"] == pytest.approx(0.1, abs=0.005)


def test_zero_offset():
    cap = m.Capture()
    cap.add_values([k * 1e-3 for k in range(50)], [0.002] * 50)
    off = cap.zero_from()
    assert off == pytest.approx(0.002) and max(abs(x) for x in cap.i) < 1e-12
    with pytest.raises(ValueError):
        cap.zero_from(0, 5)


def test_csv_roundtrip_and_validation():
    cap = m.Capture(chip="INA219", r_shunt=0.05)
    _square(cap, seconds=0.05)
    back = m.Capture.from_csv(cap.to_csv())
    assert back.chip == "INA219" and back.r_shunt == 0.05 and len(back) == len(cap)
    assert list(back.i) == pytest.approx(list(cap.i))
    with pytest.raises(ValueError):
        m.Capture.from_csv("a,b\n1,2\n")
    with pytest.raises(ValueError):
        m.Capture.from_csv("t_s,current_a\n0,1\n-1,2\n0.5,1\n")


def test_profile_reduction_keeps_peaks():
    cap = m.Capture()
    _square(cap, seconds=3.0, on=0.0005)                  # 0.5 ms spikes every 100 ms
    t, i = m.to_profile(cap, max_points=2000)
    assert len(t) <= 2000 and t[0] == 0 and all(b >= a for a, b in zip(t, t[1:]))
    assert max(i) == 0.3 and sum(1 for x in i if x == 0.3) >= 25     # every spike survives
    t2, _ = m.to_profile(cap, 0, 100)
    assert len(t2) == 100


def test_ring_buffer_trims():
    cap = m.Capture(max_samples=1000)
    cap.add_values([k * 1e-3 for k in range(1500)], [0.0] * 1500)
    assert len(cap) == 1000 and cap.dropped == 500 and cap.t[0] == pytest.approx(0.5)


def test_simulated_meter_speaks_the_protocol():
    sim = m.SimulatedMeter("INA226", 0.1)
    p = m.FrameParser()
    hello = p.feed(sim.read(4096))
    assert hello and m.parse_hello(hello[0].payload)["chip"] == "INA226"
    sim.write(b"START\n")
    sim.advance(0.5)
    cap = m.Capture("INA226", 0.1)
    for f in p.feed(sim.read(1 << 20)):
        if f.ftype == m.T_SAMPLES:
            cap.add_frame(*m.decode_samples(f.payload))
    s = m.capture_stats(cap)
    assert 600 < s.rate_hz < 1600                          # NORMAL ≈ 1.5 kHz
    assert 0.04 < s.avg_a < 0.09 and s.max_a > 0.3 and s.bursts >= 4
    sim.write(b"MODE FAST\n")
    fast = [f for f in p.feed(sim.read(4096)) if f.ftype == m.T_HELLO]
    assert m.parse_hello(fast[-1].payload)["mode"] == "FAST"
    sim.write(b"STOP\n")
    sim.advance(0.5)
    assert sim.in_waiting == 0


def test_auto_threshold_splits_clusters():
    vals = [0.05] * 900 + [0.3] * 100
    thr = m.auto_threshold(vals)
    assert 0.05 < thr < 0.3


def test_flat_noise_has_no_bursts():
    import random
    rng = random.Random(3)
    cap = m.Capture()
    cap.add_values([k * 1e-4 for k in range(500)], [0.047 + rng.gauss(0, 0.001) for _ in range(500)])
    s = m.capture_stats(cap)
    assert s.bursts == 0 and s.duty == 0 and s.idle_avg_a == pytest.approx(0.047, abs=0.001)


def test_rare_short_bursts_still_count():
    cap = m.Capture()
    rate = 10000
    t = [k / rate for k in range(rate)]
    cap.add_values(t, [0.3 if (x % 0.2) < 0.001 else 0.05 for x in t])     # 0.5 % duty
    s = m.capture_stats(cap)
    assert s.bursts == 5 and s.duty == pytest.approx(0.005, abs=0.001)
