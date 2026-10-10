# BenchBuddy current meter — firmware

`benchbuddy_meter/benchbuddy_meter.ino` turns an ESP32 (written for the **Wemos D1 R32**) plus an
**INA226** or **INA219** breakout into a USB current logger for BenchBuddy's **Measure** tab.

## Flash it

Arduino IDE → board **WEMOS D1 R32** (`esp32:esp32:d1_uno32`; any ESP32 board works if you keep
SDA/SCL on IO21/IO22) → open `benchbuddy_meter.ino` → Upload. No extra libraries needed.

Compile-checked on arduino-esp32 core 2.0.9 (276 KB flash). Close the Serial Monitor before
connecting from BenchBuddy: only one program can own the port.

## Wire it

```
 Bench supply / battery (+) ──► VIN+  [INA]  VIN− ──► (+) of the board you're measuring (DUT)
 Bench supply / battery (−) ──────────────────────────► (−) of the DUT  ──┐
                                                                           │ common ground
 D1 R32  3V3 ──► INA VCC        D1 R32  IO21 (SDA) ──► INA SDA             │
 D1 R32  GND ──► INA GND ◄───────────────────────────────────────────────┘
                                D1 R32  IO22 (SCL) ──► INA SCL
```

* The shunt goes in the **high side** (in series with the DUT's positive supply).
* The D1 R32, the INA and the DUT must share **ground**.
* Power the INA from **3V3** so its I2C lines stay at 3.3 V logic.
* On the D1 R32, SDA/SCL are the two pins next to AREF (IO21/IO22), not A4/A5.
* Most breakouts already have I2C pull-ups; address 0x40–0x4F is found automatically.

## Know your range

The measurable current is the shunt chip's full-scale voltage divided by the shunt resistance
(most boards use **0.1 Ω**, marked `R100`):

| Chip   | Full scale        | With 0.1 Ω | LSB with 0.1 Ω | Bus voltage max |
|--------|-------------------|------------|----------------|-----------------|
| INA226 | ±81.92 mV         | **±819 mA** | 25 µA         | 36 V            |
| INA219 | ±320 mV (PGA /8)  | ±3.2 A     | 100 µA         | 26 V            |

Above full scale the reading clips; BenchBuddy flags it as **OVER RANGE**. For servos/motors on an
INA226, swap to a 0.01 Ω (`R010`) shunt and set that value in BenchBuddy.

Deep-sleep currents (µA) are below what a 0.1 Ω shunt resolves well; use a bigger shunt
(e.g. 1–10 Ω, bypassed while the board is awake) or a dedicated tool such as a Nordic PPK2.

## How the sensor is identified (read-only)

* **INA226**: TI manufacturer ID `0x5449` **and** die ID `0x226x`. Other TI monitors share the
  manufacturer ID (an INA260 at 0x40 reports die `0x2270`); those are ignored with a message
  naming the die ID, because their registers mean different things.
* **INA219**: it has no ID register, so it's recognised only by its power-on config (`0x399F`) or
  one of the two configs this firmware writes. An INA219 left in some other configuration shows
  up as "no sensor"; send `FORCE INA219` (the **Force INA219…** button in the Measure tab) to use it
  anyway. That writes its config register, so only do it when an INA219 is really what's wired.
* Nothing is ever written to a device that hasn't been identified, so a PCA9685 servo driver
  sharing address 0x40 is left alone.

## Modes

| Mode   | What it samples                         | INA226     | INA219     |
|--------|-----------------------------------------|------------|------------|
| NORMAL | current + bus voltage every sample      | ~1.5 kHz   | ~940 Hz (12-bit) |
| FAST   | current only (bus read once on entry)   | ~6.7 kHz   | ~6.3 kHz (10-bit) |

FAST is the one for catching Wi-Fi TX bursts (hundreds of µs). Both modes still **under-read the
very top of short spikes** because each conversion averages over its 140–532 µs window: good for
burst timing, average current and charge, not for nanosecond-accurate peaks.

## Protocol

Framed binary at 921600 baud, documented in `benchbuddy/core/meter.py`. Commands are text lines:
`HELLO`, `START`, `STOP`, `MODE NORMAL`, `MODE FAST`, `FORCE INA219`.

## Testing without hardware

`host_test/` builds the real sketch on a PC against a fake I2C bus (INA226, INA219, a reconfigured INA219,
an INA260 decoy, nothing, or a PCA9685 decoy at 0x40) and checks the output with BenchBuddy's parser: `pytest tests/test_firmware_host.py`.
