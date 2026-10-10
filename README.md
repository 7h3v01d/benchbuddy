# BenchBuddy

A desktop bench companion for Arduino / ESP32 / general electronics projects.
Built with Python + PyQt6. All the maths lives in `benchbuddy/core/` with no GUI
dependency, so a web UI (FastAPI / Streamlit) can reuse it later.

## Run it

```bash
pip install -r requirements.txt
python -m benchbuddy
```

Or install it as a package (adds a `benchbuddy` launcher):

```bash
pip install .
```

Run the tests (install `requirements-dev.txt` first; the GUI tests run headless):

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
```

The core tests need no Qt at all: on a machine without PyQt6 the GUI tests skip and the rest
(engines, meter, firmware host tests, adversarial suites) still run. The property-based suites use
[hypothesis](https://hypothesis.readthedocs.io); for a much deeper search:

```bash
python -m pytest tests --hypothesis-profile=deep          # 3,000 cases per property
BB_SIM_EXAMPLES=400 python -m pytest tests/test_adversarial_brownout.py
```

### Current meter (optional hardware)

Flash `firmware/benchbuddy_meter` to a Wemos D1 R32 (any ESP32), wire an INA226 or INA219 breakout in
series with the board you want to measure, then pick its COM port in the **Measure** tab.
Wiring, ranges and limits: [`firmware/README.md`](firmware/README.md).

### Windows build (standalone .exe)

Run `setup.bat` once, then `build.bat`. It installs the exact versions in
`constraints-release.txt` (checked to resolve for Windows x64 / Python 3.11), runs the tests, renders
the icon (`tools/make_icon.py`) and runs PyInstaller with `benchbuddy.spec`. The result is
`dist\BenchBuddy\BenchBuddy.exe`: one folder, no Python needed on the target PC, fonts, icons and the
photo-OCR Python side bundled. Photo OCR still needs the free Tesseract program installed (the
UB-Mannheim installer's default folder is found automatically, or set `BENCHBUDDY_TESSERACT`).

## What's inside

| Tab | What it does |
|---|---|
| **Power budget** | Build a power tree (USB / battery / adapter → LDO / buck / boost → loads). Shows average and peak current per rail, voltage sag from cable/battery resistance, regulator dropout failures, LDO heat, bulk-capacitor suggestions and battery runtime. Presets for ESP32 variants, Arduino, servos, NeoPixels, SIM800L, etc. A live **power-tree diagram** draws supply → regulator → load with status colours, peak-vs-rating bars and current-weighted lines; click a rail to select it, or **⤢ Expand** it into its own window. **Export report…** writes a self-contained dark HTML page (prints light) or an A4 PDF with the diagram, rails, loads and findings. Save/open as JSON. |
| **Brown-out sim** | Time-domain simulation of a load burst (e.g. Wi-Fi TX) through source resistance, input cap, regulator and output cap. Shows the voltage dip against your brown-out threshold, can import a rail from the Power budget, and finds the smallest output capacitor that survives. |
| **Measure** | Live current from an **INA226 / INA219** on an ESP32 (see `firmware/`). Min/max trace that keeps µs spikes visible at any zoom, drag-to-select analysis (average, 99th-pct peak, charge, energy, active/idle split, burst count and width), zero offset, over-range and resolution warnings, CSV save/open. **Use as a load** writes the measured active/peak/sleep/duty into the Power budget; **Replay in the Brown-out sim** drives the simulator with your real waveform instead of square bursts. A built-in demo device works without hardware. |
| **Resistors** | Colour code ⇄ value (3-6 bands), SMD codes (3/4-digit, R, EIA-96), E12/E24/E96 nearest values, series/parallel, voltage divider, LED resistor. |
| **Capacitors** | Marking decoder (104, 473K, 4n7 ...), RC time/cutoff, reactance, series/parallel, hold-up and bulk-cap sizing, ESP32 decoupling rules. |
| **Design calcs** | Battery ADC divider, IPC-2221 trace width, buck/boost inductor sizing, I2C pull-up range, BJT base resistor, **555 timer** (design from frequency + duty with standard parts, analyse, one-shot), **op-amp gain** (E-series Rf/Rg picks plus output-swing, bandwidth and slew checks) and **LED strip power** (current, PSU size, max brightness for your supply, FastLED power-limit line). |
| **Wiring & GPIO** | Ohm's law, wire voltage drop by AWG, GPIO current limits per chip. |
| **Pinout** | Pin maps for ESP32 DevKit, Wemos D1 R32, ESP32-S3, ESP32-C3 and Uno/Nano. Each pin shows its functions and gotchas (strapping, flash, input-only, ADC2-vs-Wi-Fi, boot glitches, timer clashes). **Find a pin** filters by what you need (PWM, ADC with Wi-Fi on, touch, DAC, deep-sleep wake, safe at boot) and ranks the cleanest pins first. |
| **Battery & sleep** | Wake/sleep duty-cycle average current and runtime. |
| **Parts lookup** | 213 built-in parts searchable by part number, SMD marking (e.g. `J3Y`) or keywords. Add your own, track quantity and location, attach photos, link to datasheet searches, import/export CSV. **Identify from photo** reads markings with OCR and matches them against the library, tolerating look-alike characters (5/S/O, 0/D, 1/I/L, 8/B). |

Your parts library is stored at `~/.benchbuddy/parts.db` (override with the
`BENCHBUDDY_DB` environment variable). Updates add new built-in parts without overwriting your edits.

### Optional: photo identification

```bash
pip install pytesseract pillow
```
plus the Tesseract program (Windows: UB-Mannheim installer; macOS: `brew install tesseract`; Linux: `sudo apt install tesseract-ocr`).
OCR is best-effort: clean, close-up, well-lit text works; tiny SMD codes and shiny packages often fail.
Results are candidates to confirm, not answers. SMD markings also vary by manufacturer.

### Simulator limits

The brown-out sim is a simplified model (regulator = first-order lag, optional current limit). It does not model
real control-loop stability or ringing. Use it to compare options and size capacitors, then verify with a scope.
Its integrator is implicit (backward Euler / exact exponential updates), so it stays stable for any capacitor
or ESR, and rail voltages never go below 0 V. Expect ~0.5 % discretisation error on the size of a dip.

## How the power budget thinks

- **Average** current drives battery runtime and regulator heat. **Peak** current drives brown-out checks.
- Peaks of all loads are assumed to happen **at the same time**. This is deliberately conservative.
- Loads are modelled as **constant-current**. Only supplies have series resistance (`R int`), so for
  each supply the engine solves `V = Vnom − R·I(V)` exactly (bracketed bisection, always converges),
  where `I(V)` is the real draw of everything below it at that voltage:
  - **LDO**: output `min(Vset, Vin − dropout)`; passes its output current plus Iq.
  - **Buck**: same voltage rule; draws `Vset·Iout / (η·Vin)` while regulating, rising to `Iout`
    (pass-through at 100 % duty) as it falls into dropout.
  - **Boost**: draws `Vout·Iout / (η·Vin)` above its minimum input and nothing below it. If switching on
    pulls its own input under that minimum, there is no steady state and it's reported as
    **hiccuping**, with the source's maximum deliverable power (`V²/4R`) for comparison.
- Every rail is judged on the voltage it *actually* receives, so a regulator in dropout drags
  everything downstream with it and each affected rail reports its own error.
- Warnings use thresholds: 80 % of rating, 5 % / 10 % sag, 50 / 90 °C LDO rise.
- All preset currents are **typical** values. Clones and dev boards vary; confirm with a datasheet or meter.

## What it refuses

Engines check their own inputs and raise one error family (`DomainError`, a `ValueError`) instead of
computing nonsense: non-finite numbers, negative currents/quantities, zero or negative voltages,
efficiencies outside (0, 1], impossible battery cut-offs, NaN or unknown fields in saved files, NaN
in capture CSVs, reversed-shunt measured profiles. A saved project with *impossible* values still
opens (so you can fix it) but won't be analysed until it's valid. Every calculator page shows these
as a message, never a crash. Imported text (parts CSV) is always shown as text, links only open if
they're `http(s)`, exported CSV cells can't start a spreadsheet formula, and photos are checked by
content, size (25 MB) and pixel count (40 MP) before anything decodes them.

## Look & feel

The header strip shows the power-budget verdict on every tab (click it to jump there). Ctrl+1…0 jump between tabs (also under **View**). The window size, last tab and pinout board are remembered between launches.


Dark industrial theme (obsidian / teal / phosphor, flat zero-radius controls, JetBrains Mono).
All colours, the stylesheet and font loading live in `benchbuddy/gui/theme.py`; change a token there
and it applies everywhere, including the brown-out plot and the status banner.
JetBrains Mono NL is bundled in `benchbuddy/gui/fonts/` under the SIL Open Font License (`OFL.txt`),
so it works without installing the font. Arrow/check icons are small SVGs in `benchbuddy/gui/icons/`.

## Licence

Apache License 2.0, © 2026 Leon Priest: see `LICENSE` and `NOTICE`. The bundled JetBrains Mono NL font
is under the SIL Open Font License (`benchbuddy/gui/fonts/OFL.txt`).

## Layout

```
benchbuddy/core/   validation, units, resistors, capacitors, power, brownout, calcs, presets, examples, pinout, report, meter, images, partsdb, seed_parts, seed_extra, identify, ocr
benchbuddy/gui/    one module per tab + main_window, theme (+ fonts/, icons/)
tests/             core maths, headless GUI smoke tests, adversarial / property suites (test_adversarial_*)
tools/            make_icon.py (SVG → .ico for the Windows build)
firmware/         ESP32 meter sketch + wiring guide; host_test/ runs the sketch on a PC for tests
```

## Ideas for next steps

- Web UI on the same core (FastAPI + a small front end)
- Wi-Fi/BLE burst profiles for the simulator, multi-rail simulation
- Community-shareable parts library
- More calculators (active filters, Zener / shunt regulators)
- Ask-about-a-part via a local Ollama model
