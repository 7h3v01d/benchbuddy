"""Builds the real firmware sketch against PC stubs (fake INA on a fake I2C bus) and
decodes its serial output with the host parser. Skipped when no C++ compiler is around."""

import shutil
import subprocess
from pathlib import Path

import pytest

from benchbuddy.core import meter as m

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"
CXX = shutil.which("g++") or shutil.which("clang++")

pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler")


@pytest.fixture(scope="module")
def host_meter(tmp_path_factory):
    exe = tmp_path_factory.mktemp("fw") / "host_meter"
    cmd = [CXX, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", f"-I{FW / 'host_test'}",
           "-x", "c++", str(FW / "benchbuddy_meter" / "benchbuddy_meter.ino"),
           "-x", "none", str(FW / "host_test" / "main.cpp"), "-o", str(exe)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return exe


def _run(exe, chip):
    res = subprocess.run([str(exe), str(chip)], check=True, capture_output=True)
    p = m.FrameParser()
    frames = p.feed(res.stdout)
    assert p.bad_frames == 0 and p.skipped_bytes == 0
    return frames, res.stderr.decode()


@pytest.mark.parametrize("chip,name,normal_hz,fast_hz", [(226, "INA226", 1506, 6667), (219, "INA219", 940, 6250)])
def test_firmware_streams_correct_samples(host_meter, chip, name, normal_hz, fast_hz):
    frames, _ = _run(host_meter, chip)
    hellos = [m.parse_hello(f.payload) for f in frames if f.ftype == m.T_HELLO]
    assert hellos[0]["chip"] == name and hellos[0]["mode"] == "NORMAL"
    assert hellos[1]["mode"] == "FAST"
    cap = m.Capture(chip=name, r_shunt=0.1)
    switch = None
    for f in frames:
        if f.ftype == m.T_HELLO and switch is None and m.parse_hello(f.payload)["mode"] == "FAST":
            switch = len(cap)
        elif f.ftype == m.T_SAMPLES:
            cap.add_frame(*m.decode_samples(f.payload))
    for lo, hi, hz in ((0, switch, normal_hz), (switch, len(cap), fast_hz)):
        s = m.capture_stats(cap, lo, hi)
        assert s.rate_hz == pytest.approx(hz, rel=0.05)
        # fake load: 50 mA idle, 400 mA for 1 ms every 10 ms -> 85 mA average, 10 % duty
        assert s.avg_a == pytest.approx(0.085, abs=0.006)
        assert s.max_a == pytest.approx(0.400, abs=0.002)
        assert s.duty == pytest.approx(0.10, abs=0.012)
        assert s.burst_avg_s == pytest.approx(0.001, rel=0.1)
        assert s.avg_v == pytest.approx(3.287, abs=0.01)
    logs = [f.payload for f in frames if f.ftype == m.T_LOG]
    assert logs == [b"unknown command"]


@pytest.mark.parametrize("chip", [0, 9685])
def test_firmware_without_sensor_explains_and_never_writes_strangers(host_meter, chip):
    frames, err = _run(host_meter, chip)
    hello = m.parse_hello(next(f for f in frames if f.ftype == m.T_HELLO).payload)
    assert hello["chip"] == "NONE"
    assert any(b"Check wiring" in f.payload for f in frames if f.ftype == m.T_LOG)
    assert not any(f.ftype == m.T_SAMPLES for f in frames)
    assert "decoy_writes=0" in err          # a PCA9685 at 0x40 is left alone


def test_ina260_is_not_mistaken_for_an_ina226(host_meter):
    frames, err = _run(host_meter, 260)
    hello = m.parse_hello(next(f for f in frames if f.ftype == m.T_HELLO).payload)
    assert hello["chip"] == "NONE"
    assert any(b"not an INA226" in f.payload and b"0x2270" in f.payload for f in frames if f.ftype == m.T_LOG)
    assert not any(f.ftype == m.T_SAMPLES for f in frames)


def test_reconfigured_ina219_needs_and_accepts_force(host_meter):
    frames, _ = _run(host_meter, 2191)
    hellos = [m.parse_hello(f.payload) for f in frames if f.ftype == m.T_HELLO]
    assert hellos[0]["chip"] == "NONE"                       # not guessed at boot
    assert hellos[1]["chip"] == "INA219"                     # explicit FORCE INA219
    assert any(b"Forced INA219" in f.payload for f in frames if f.ftype == m.T_LOG)
    cap = m.Capture(chip="INA219", r_shunt=0.1)
    for f in frames:
        if f.ftype == m.T_SAMPLES:
            cap.add_frame(*m.decode_samples(f.payload))
    assert m.capture_stats(cap).avg_a == pytest.approx(0.085, abs=0.006)
