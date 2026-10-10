"""Live current measurement: wire protocol, INA219/INA226 conversion, captures and stats.

Hardware: an ESP32 (e.g. Wemos D1 R32) running firmware/benchbuddy_meter reads an
INA219 or INA226 over I2C and streams framed binary samples over USB serial.

Frame (all little-endian)::

    0xB5 0x42 | type u8 | len u16 | payload[len] | ck_a ck_b

ck_a/ck_b is an 8-bit Fletcher checksum over type, len and payload (as in u-blox UBX).
Types: HELLO (key=value;… text), SAMPLES (t0_us u32, n u8, n × [dt_us u16, shunt i16, bus u16]),
LOG (text). Host → device commands are ASCII lines: HELLO, START, STOP, MODE NORMAL|FAST.

No Qt here: the GUI drives this from a worker thread, and tests drive it directly.
"""

from __future__ import annotations

import bisect
import csv
import io
import math
import random
import struct
from array import array
from dataclasses import dataclass, field

from .validation import DomainError

SYNC = b"\xB5\x42"
T_HELLO, T_SAMPLES, T_LOG = 0x01, 0x02, 0x03
MAX_PAYLOAD = 1024
SAMPLE_FMT = "<HhH"            # dt_us, shunt raw, bus raw
SAMPLE_SIZE = struct.calcsize(SAMPLE_FMT)
DEFAULT_BAUD = 921600


# ------------------------------------------------------------------ framing
def fletcher8(data: bytes) -> tuple[int, int]:
    a = b = 0
    for x in data:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return a, b


def encode_frame(ftype: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    body = struct.pack("<BH", ftype, len(payload)) + payload
    a, b = fletcher8(body)
    return SYNC + body + bytes((a, b))


def encode_samples(t0_us: int, samples: list[tuple[int, int, int]]) -> bytes:
    """samples: (dt_us, shunt_raw, bus_raw); dt of the first is relative to t0."""
    if len(samples) > 255:
        raise ValueError("at most 255 samples per frame")
    payload = struct.pack("<IB", t0_us & 0xFFFFFFFF, len(samples))
    payload += b"".join(struct.pack(SAMPLE_FMT, *s) for s in samples)
    return encode_frame(T_SAMPLES, payload)


@dataclass
class Frame:
    ftype: int
    payload: bytes


def _plausible_header(ftype: int, length: int) -> bool:
    if length > MAX_PAYLOAD:
        return False
    if ftype == T_SAMPLES:
        return length >= 5 and (length - 5) % SAMPLE_SIZE == 0 and (length - 5) // SAMPLE_SIZE <= 255
    return ftype in (T_HELLO, T_LOG) and length <= 512


class FrameParser:
    """Incremental parser: feed it raw serial bytes, get whole frames back.

    Garbage (boot ROM text, half frames after connecting mid-stream) is skipped by
    hunting for the sync bytes; a bad checksum drops one byte and resyncs.
    """

    def __init__(self):
        self.buf = bytearray()
        self.bad_frames = 0
        self.skipped_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buf.extend(data)
        out: list[Frame] = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                keep = 1 if self.buf[-1:] == SYNC[:1] else 0
                self.skipped_bytes += len(self.buf) - keep
                del self.buf[:len(self.buf) - keep]
                return out
            if i:
                self.skipped_bytes += i
                del self.buf[:i]
            if len(self.buf) < 5:
                return out
            ftype, length = struct.unpack_from("<BH", self.buf, 2)
            if not _plausible_header(ftype, length):
                # sync bytes inside junk: don't wait for a frame that can't exist, resync now
                self.bad_frames += 1
                del self.buf[:1]
                continue
            total = 2 + 3 + length + 2
            if len(self.buf) < total:
                return out
            body = bytes(self.buf[2:5 + length])
            if fletcher8(body) != (self.buf[5 + length], self.buf[6 + length]):
                self.bad_frames += 1
                del self.buf[:1]
                continue
            out.append(Frame(ftype, body[3:]))
            del self.buf[:total]


def parse_hello(payload: bytes) -> dict[str, str]:
    text = payload.decode("ascii", "replace")
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def decode_samples(payload: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    t0, n = struct.unpack_from("<IB", payload, 0)
    need = 5 + n * SAMPLE_SIZE
    if len(payload) != need:
        raise ValueError(f"sample frame length {len(payload)} != {need}")
    return t0, [struct.unpack_from(SAMPLE_FMT, payload, 5 + k * SAMPLE_SIZE) for k in range(n)]


# ------------------------------------------------------------- chip maths
@dataclass(frozen=True)
class Chip:
    name: str
    shunt_lsb_v: float
    shunt_fs_v: float             # full-scale shunt voltage (overrange above this)
    max_bus_v: float

    def shunt_volts(self, raw: int) -> float:
        return raw * self.shunt_lsb_v

    def bus_volts(self, raw: int) -> float:
        if self.name == "INA219":
            return (raw >> 3) * 4e-3            # bits 15..3, 4 mV LSB
        return raw * 1.25e-3

    def overrange(self, raw: int) -> bool:
        return abs(raw * self.shunt_lsb_v) >= self.shunt_fs_v * 0.999

    def full_scale_a(self, r_shunt: float) -> float:
        return self.shunt_fs_v / r_shunt

    def lsb_a(self, r_shunt: float) -> float:
        return self.shunt_lsb_v / r_shunt


CHIPS = {
    "INA226": Chip("INA226", 2.5e-6, 81.92e-3, 36.0),
    "INA219": Chip("INA219", 10e-6, 320e-3, 26.0),     # PGA /8 (±320 mV), as the firmware sets it
}


# ---------------------------------------------------------------- captures
@dataclass
class Capture:
    """Time-stamped current/voltage samples. t in seconds from the first sample."""
    chip: str = "INA226"
    r_shunt: float = 0.1
    max_samples: int = 2_000_000
    t: array = field(default_factory=lambda: array("d"))
    i: array = field(default_factory=lambda: array("d"))
    v: array = field(default_factory=lambda: array("d"))
    zero_a: float = 0.0
    overrange_count: int = 0
    dropped: int = 0
    _t_base_us: int | None = None
    _last_us: int = 0
    _wraps: int = 0

    def __len__(self) -> int:
        return len(self.t)

    def clear(self) -> None:
        self.t, self.i, self.v = array("d"), array("d"), array("d")
        self.overrange_count = self.dropped = 0
        self._t_base_us = None
        self._last_us = self._wraps = 0

    def add_frame(self, t0_us: int, samples: list[tuple[int, int, int]]) -> int:
        """Append decoded raw samples; returns how many were added."""
        if not (isinstance(self.r_shunt, (int, float)) and math.isfinite(self.r_shunt) and self.r_shunt > 0):
            raise DomainError("shunt resistance must be a positive number")
        chip = CHIPS[self.chip]
        t_us = t0_us
        for k, (dt, sh, bus) in enumerate(samples):
            t_us = (t_us + dt) & 0xFFFFFFFF if k else t0_us + dt
            t_us &= 0xFFFFFFFF
            if self._t_base_us is None:
                self._t_base_us = t_us
                self._last_us = t_us
            if t_us < self._last_us and self._last_us - t_us > 0x80000000:
                self._wraps += 1                 # micros() wrapped after ~71 minutes
            self._last_us = t_us
            abs_us = t_us + self._wraps * 0x100000000 - self._t_base_us
            if chip.overrange(sh):
                self.overrange_count += 1
            self.t.append(abs_us * 1e-6)
            self.i.append(chip.shunt_volts(sh) / self.r_shunt - self.zero_a)
            self.v.append(chip.bus_volts(bus))
        self._trim()
        return len(samples)

    def add_values(self, t_s: list[float], i_a: list[float], v_v: list[float] | None = None) -> None:
        v_v = v_v if v_v is not None else [0.0] * len(t_s)
        if not len(t_s) == len(i_a) == len(v_v):
            raise DomainError("time, current and voltage lists must be the same length")
        for name, seq in (("time", t_s), ("current", i_a), ("voltage", v_v)):
            if not all(math.isfinite(x) for x in seq):
                raise DomainError(f"{name} contains NaN or infinity")
        if any(b < a for a, b in zip(t_s, t_s[1:])) or (len(self.t) and t_s and t_s[0] < self.t[-1]):
            raise DomainError("timestamps must increase")
        self.t.extend(t_s)
        self.i.extend(i_a)
        self.v.extend(v_v)
        self._trim()

    def _trim(self) -> None:
        extra = len(self.t) - self.max_samples
        if extra > 0:
            del self.t[:extra]
            del self.i[:extra]
            del self.v[:extra]
            self.dropped += extra

    def window(self, t_from: float | None = None, t_to: float | None = None) -> tuple[int, int]:
        """Index range [lo, hi) covering the time window."""
        lo = 0 if t_from is None else bisect.bisect_left(self.t, t_from)
        hi = len(self.t) if t_to is None else bisect.bisect_right(self.t, t_to)
        return lo, hi

    def zero_from(self, lo: int = 0, hi: int | None = None) -> float:
        """Treat the mean of a no-load stretch as the offset and subtract it from now on."""
        hi = len(self.i) if hi is None else hi
        if hi - lo < 10:
            raise ValueError("need at least 10 samples with nothing connected to zero the meter")
        offset = sum(self.i[lo:hi]) / (hi - lo)
        self.zero_a += offset
        for k in range(len(self.i)):
            self.i[k] -= offset
        return offset

    # ----------------------------------------------------------------- csv
    def to_csv(self, lo: int = 0, hi: int | None = None) -> str:
        hi = len(self.t) if hi is None else hi
        buf = io.StringIO()
        buf.write(f"# benchbuddy capture v1; chip={self.chip}; shunt_ohm={self.r_shunt:g}; zero_a={self.zero_a:.9g}\n")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["t_s", "current_a", "bus_v"])
        t0 = self.t[lo] if hi > lo else 0.0
        for k in range(lo, hi):
            w.writerow([f"{self.t[k] - t0:.6f}", f"{self.i[k]:.9g}", f"{self.v[k]:.5g}"])
        return buf.getvalue()

    @classmethod
    def from_csv(cls, text: str) -> "Capture":
        cap = cls()
        lines = text.splitlines()
        if lines and lines[0].startswith("#"):
            meta = parse_hello(lines[0].lstrip("# ").replace("benchbuddy capture v1", "").encode())
            cap.chip = meta.get("chip", cap.chip) if meta.get("chip") in CHIPS else cap.chip
            try:
                r_sh = float(meta.get("shunt_ohm", cap.r_shunt))
                zero = float(meta.get("zero_a", 0.0))
                if math.isfinite(r_sh) and r_sh > 0:
                    cap.r_shunt = r_sh
                if math.isfinite(zero):
                    cap.zero_a = zero
            except ValueError:
                pass
            lines = lines[1:]
        rows = csv.reader(lines)
        header = next(rows, None)
        if not header or [h.strip().lower() for h in header[:2]] != ["t_s", "current_a"]:
            raise ValueError("not a BenchBuddy capture (expected columns t_s,current_a[,bus_v])")
        t, i, v = [], [], []
        for n, row in enumerate(rows, start=2):
            if not row:
                continue
            try:
                vals = [float(row[0]), float(row[1]), float(row[2]) if len(row) > 2 and row[2] else 0.0]
            except ValueError:
                raise DomainError(f"line {n}: not a number") from None
            if not all(math.isfinite(x) for x in vals):
                raise DomainError(f"line {n}: contains NaN or infinity")
            t.append(vals[0])
            i.append(vals[1])
            v.append(vals[2])
            if len(t) > cap.max_samples:
                raise DomainError(f"capture has more than {cap.max_samples:,} samples")
        if len(t) < 2:
            raise ValueError("capture has fewer than 2 samples")
        if any(b < a for a, b in zip(t, t[1:])):
            raise ValueError("timestamps must be increasing")
        cap.add_values(t, i, v)
        return cap


# ------------------------------------------------------------------ stats
@dataclass
class CaptureStats:
    n: int
    duration_s: float
    rate_hz: float
    avg_a: float
    min_a: float
    max_a: float
    p99_a: float
    rms_a: float
    charge_mah: float
    energy_mwh: float
    avg_v: float
    min_v: float
    threshold_a: float
    duty: float               # fraction of time above threshold
    active_avg_a: float
    idle_avg_a: float
    bursts: int
    burst_avg_s: float

    def as_load(self) -> dict:
        """Numbers for a power-budget Load (mA): active, peak, sleep, duty."""
        return dict(i_active_ma=self.active_avg_a * 1000, i_peak_ma=self.p99_a * 1000,
                    i_sleep_ma=self.idle_avg_a * 1000, duty=self.duty)


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def auto_threshold(values: list[float]) -> float:
    """Split 'idle' from 'active' with a two-cluster (Otsu-style) search on a histogram."""
    if not values:
        return 0.0
    lo, hi = min(values), max(values)
    if hi - lo < 1e-6:
        return hi
    bins = 128
    width = (hi - lo) / bins
    hist = [0] * bins
    for x in values:
        hist[min(int((x - lo) / width), bins - 1)] += 1
    total = len(values)
    sum_all = sum((k + 0.5) * h for k, h in enumerate(hist))
    best_k, best_var = 0, -1.0
    w0 = s0 = 0.0
    for k in range(bins - 1):
        w0 += hist[k]
        s0 += (k + 0.5) * hist[k]
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0, m1 = s0 / w0, (sum_all - s0) / w1
        var = w0 * w1 * (m0 - m1) ** 2
        if var > best_var:
            best_var, best_k = var, k
    return lo + (best_k + 1) * width


def _is_flat(srt: list[float]) -> bool:
    """True when the spread is small next to the level itself.

    Uses the second-highest sample, not a percentile, so rare short bursts (beacons at ~1 % duty)
    still count, while a single glitch doesn't.
    """
    lo, hi = _percentile(srt, 0.01), srt[-2] if len(srt) >= 2 else srt[-1]
    level = max(abs(_percentile(srt, 0.5)), 1e-3)
    return hi - lo < 0.25 * level


def capture_stats(cap: Capture, lo: int = 0, hi: int | None = None,
                  threshold_a: float | None = None) -> CaptureStats:
    hi = len(cap) if hi is None else hi
    n = hi - lo
    if n < 2:
        raise ValueError("need at least 2 samples")
    t, i, v = cap.t, cap.i, cap.v
    duration = t[hi - 1] - t[lo]
    if duration <= 0:
        raise ValueError("capture has no time span")
    # trapezoid integrals, so uneven sample spacing is handled correctly
    q = e = active_t = active_q = 0.0
    bursts, in_burst, burst_t = 0, False, 0.0
    vals = list(i[lo:hi])
    srt = sorted(vals)
    if threshold_a is not None:
        thr = threshold_a
    elif _is_flat(srt):
        thr = srt[-1] + 1e-9            # one level plus noise: no active/idle split to find
    else:
        thr = auto_threshold(vals)
    for k in range(lo, hi - 1):
        dt = t[k + 1] - t[k]
        ia = (i[k] + i[k + 1]) / 2
        q += ia * dt
        e += (i[k] * v[k] + i[k + 1] * v[k + 1]) / 2 * dt
        if i[k] > thr:
            active_t += dt
            active_q += ia * dt
            if not in_burst:
                bursts += 1
                in_burst = True
            burst_t += dt
        else:
            in_burst = False
    avg = q / duration
    idle_t = duration - active_t
    return CaptureStats(
        n=n, duration_s=duration, rate_hz=(n - 1) / duration, avg_a=avg,
        min_a=srt[0], max_a=srt[-1], p99_a=_percentile(srt, 0.99),
        rms_a=math.sqrt(sum(x * x for x in vals) / n),
        charge_mah=q / 3.6, energy_mwh=e / 3.6,
        avg_v=sum(v[lo:hi]) / n, min_v=min(v[lo:hi]),
        threshold_a=thr, duty=active_t / duration,
        active_avg_a=active_q / active_t if active_t else avg,
        idle_avg_a=(q - active_q) / idle_t if idle_t > 0 else avg,
        bursts=bursts, burst_avg_s=burst_t / bursts if bursts else 0.0)


def to_profile(cap: Capture, lo: int = 0, hi: int | None = None,
               max_points: int = 20000) -> tuple[list[float], list[float]]:
    """(t, i) for the brown-out simulator, starting at t = 0.

    Long captures are reduced with min/max pairs per bucket, so peaks survive.
    """
    hi = len(cap) if hi is None else hi
    if hi - lo < 2:
        raise ValueError("need at least 2 samples")
    t0 = cap.t[lo]
    if hi - lo <= max_points:
        return [x - t0 for x in cap.t[lo:hi]], list(cap.i[lo:hi])
    buckets = max_points // 2
    step = (hi - lo) / buckets
    ts, is_ = [], []
    for b in range(buckets):
        a, z = lo + int(b * step), lo + int((b + 1) * step)
        if z <= a:
            continue
        seg = range(a, z)
        kmin = min(seg, key=lambda k: cap.i[k])
        kmax = max(seg, key=lambda k: cap.i[k])
        for k in sorted((kmin, kmax)) if kmin != kmax else (kmin,):
            ts.append(cap.t[k] - t0)
            is_.append(cap.i[k])
    return ts, is_


# ------------------------------------------------------------- simulation
class SimulatedMeter:
    """Stands in for a serial port with the firmware on the other end.

    Generates an ESP32-like current trace: ~45 mA idle with modem-sleep wiggle,
    a Wi-Fi beacon/TX burst train every 102.4 ms, and a longer radio-on stretch
    every second. Implements the bits of pyserial the reader uses.
    """

    is_simulated = True

    def __init__(self, chip: str = "INA226", r_shunt: float = 0.1, seed: int = 1, v_bus: float = 3.3):
        self.chip = CHIPS[chip]
        self.r_shunt = r_shunt
        self.rng = random.Random(seed)
        self.v_bus = v_bus
        self.mode = "NORMAL"
        self.streaming = False
        self.t_us = 0
        self._out = bytearray()
        self._in = bytearray()
        self.is_open = True
        self._queue_hello()

    @property
    def period_us(self) -> int:
        if self.chip.name == "INA226":
            return 664 if self.mode == "NORMAL" else 150
        return 1064 if self.mode == "NORMAL" else 160

    def _queue_hello(self) -> None:
        cfg = {"INA226": {"NORMAL": "0x4097", "FAST": "0x4005"},
               "INA219": {"NORMAL": "0x399F", "FAST": "0x398D"}}[self.chip.name][self.mode]
        text = (f"fw=1;chip={self.chip.name};addr=0x40;mode={self.mode};period_us={self.period_us};"
                f"cfg={cfg};sim=1")
        self._out += encode_frame(T_HELLO, text.encode())

    def current_at(self, t_s: float) -> float:
        ms = (t_s * 1000) % 1000
        beacon = (t_s * 1000) % 102.4
        i = 0.045 + 0.004 * math.sin(t_s * 2 * math.pi * 7)
        if beacon < 1.2:
            i = 0.31 + 0.12 * (beacon < 0.25)
        if 400 <= ms < 412:
            i = 0.22 + (0.18 if (ms - 400) % 3 < 1.1 else 0.0)
        return i + self.rng.gauss(0, 0.0008)

    def _generate(self, n: int) -> None:
        while n > 0:
            k = min(n, 32)
            t0 = self.t_us
            samples = []
            for j in range(k):
                dt = 0 if j == 0 else self.period_us + self.rng.randint(-2, 2)
                self.t_us += dt if j else 0
                i = self.current_at(self.t_us * 1e-6)
                raw = int(round(i * self.r_shunt / self.chip.shunt_lsb_v))
                lim = int(self.chip.shunt_fs_v / self.chip.shunt_lsb_v)
                raw = max(-lim, min(lim, raw))
                # like the firmware: FAST mode doesn't convert the bus, it repeats the value read on entry
                v = self.v_bus - (0.045 if self.mode == "FAST" else i) * 0.15
                bus = int(round(v / 1.25e-3)) if self.chip.name == "INA226" else int(round(v / 4e-3)) << 3
                samples.append((dt, raw, bus & 0xFFFF))
            self.t_us += self.period_us
            self._out += encode_samples(t0, samples)
            n -= k

    # pyserial-ish surface
    def write(self, data: bytes) -> int:
        self._in += data
        while b"\n" in self._in:
            line, _, rest = bytes(self._in).partition(b"\n")
            self._in = bytearray(rest)
            cmd = line.decode("ascii", "ignore").strip().upper()
            if cmd == "START":
                self.streaming = True
            elif cmd == "STOP":
                self.streaming = False
            elif cmd == "HELLO":
                self._queue_hello()
            elif cmd.startswith("MODE "):
                m = cmd.split(" ", 1)[1]
                if m in ("NORMAL", "FAST"):
                    self.mode = m
                    self._queue_hello()
        return len(data)

    def advance(self, seconds: float) -> None:
        """Let simulated time pass (the GUI calls this from its read loop)."""
        if self.streaming:
            self._generate(max(1, int(seconds * 1e6 / self.period_us)))

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    @property
    def in_waiting(self) -> int:
        return len(self._out)

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        self.is_open = False
