"""Adversarial tests for the meter protocol and capture handling (reviewer property 11)."""

import math
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from benchbuddy.core import meter as m

frames = st.lists(st.tuples(st.integers(0, 0xFFFFFFFF),
                            st.lists(st.tuples(st.integers(0, 0xFFFF), st.integers(-32768, 32767),
                                               st.integers(0, 0xFFFF)), min_size=1, max_size=40)),
                  min_size=1, max_size=8)


@given(st.binary(max_size=20000), st.integers(1, 97))
def test_garbage_never_raises_and_buffer_stays_bounded(junk, chunk):
    p = m.FrameParser()
    for k in range(0, len(junk), chunk):
        p.feed(junk[k:k + chunk])
        assert len(p.buf) <= 5 + m.MAX_PAYLOAD + 2      # never more than one frame's worth held


@given(frames, st.lists(st.binary(max_size=64), min_size=1, max_size=9), st.integers(1, 61))
def test_valid_frames_survive_any_interleaved_garbage(fs, junks, chunk):
    stream = b""
    for k, (t0, smp) in enumerate(fs):
        stream += junks[k % len(junks)] + m.encode_samples(t0, smp)
    # a live meter keeps streaming: more frames follow, so a junk header that happens to look
    # plausible gets disproved by its checksum and the parser resyncs onto the real frames
    stream += b"".join(m.encode_frame(m.T_LOG, b"keepalive" * 20) for _ in range(8))
    p = m.FrameParser()
    got = []
    for k in range(0, len(stream), chunk):
        got += p.feed(stream[k:k + chunk])
    decoded = [m.decode_samples(f.payload) for f in got if f.ftype == m.T_SAMPLES]
    # garbage can never forge a frame that passes the checksum *and* parses as samples here,
    # so every real frame must come back, in order (extra junk frames would fail decode)
    want = [(t0, [tuple(x) for x in smp]) for t0, smp in fs]
    assert [d for d in decoded if d in want] == want


@given(st.binary(min_size=1, max_size=300))
def test_decode_samples_rejects_rather_than_misreads(payload):
    try:
        t0, smp = m.decode_samples(payload)
    except (ValueError, struct.error):
        return
    assert len(payload) == 5 + len(smp) * m.SAMPLE_SIZE


@pytest.mark.parametrize("csv", [
    "t_s,current_a,bus_v\n0,0.05,3.3\n0.001,nan,3.3\n",
    "t_s,current_a,bus_v\n0,0.05,3.3\n0.001,inf,3.3\n",
    "t_s,current_a,bus_v\n0,0.05,3.3\n-inf,0.05,3.3\n",
    "t_s,current_a,bus_v\n0,0.05,3.3\n0.001,0.05,abc\n",
    "t_s,current_a,bus_v\n0,0.05,3.3\n",
    "t_s,current_a\n0.002,1\n0.001,1\n",
    "x,y\n1,2\n3,4\n",
    "",
])
def test_bad_capture_files_are_rejected_with_a_reason(csv):
    with pytest.raises(ValueError):
        m.Capture.from_csv(csv)


def test_capture_rejects_non_finite_values_and_bad_shunt():
    cap = m.Capture()
    with pytest.raises(ValueError):
        cap.add_values([0.0, 0.001], [0.1, math.nan])
    with pytest.raises(ValueError):
        cap.add_values([0.0, 0.001], [0.1])
    cap.r_shunt = 0
    with pytest.raises(ValueError):
        cap.add_frame(0, [(0, 100, 100)])


@given(st.lists(st.floats(-0.01, 5, allow_nan=False), min_size=2, max_size=400), st.floats(1e-5, 1e-2))
def test_stats_of_any_valid_capture_are_finite(currents, step):
    cap = m.Capture()
    cap.add_values([k * step for k in range(len(currents))], currents, [3.3] * len(currents))
    s = m.capture_stats(cap)
    for f in ("avg_a", "min_a", "max_a", "p99_a", "rms_a", "charge_mah", "energy_mwh", "duty", "threshold_a"):
        assert math.isfinite(getattr(s, f)), f
    assert 0 <= s.duty <= 1 and s.min_a <= s.avg_a + 1e-12 and s.avg_a <= s.max_a + 1e-12


@given(st.lists(st.floats(0, 5, allow_nan=False), min_size=2, max_size=3000), st.integers(4, 400))
def test_profile_reduction_keeps_the_extremes(currents, max_points):
    cap = m.Capture()
    cap.add_values([k * 1e-4 for k in range(len(currents))], currents)
    t, i = m.to_profile(cap, max_points=max_points)
    assert len(t) <= max(max_points, len(currents) if len(currents) <= max_points else max_points)
    assert max(i) == max(currents) and min(i) == min(currents)
    assert all(b >= a for a, b in zip(t, t[1:]))
