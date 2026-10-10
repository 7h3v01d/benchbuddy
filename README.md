# BenchBuddy

A desktop bench companion for Arduino / ESP32 / general electronics projects.
Built with Python + PyQt6. All the maths lives in `benchbuddy/core/` with no GUI
dependency, so a web UI (FastAPI / Streamlit) can reuse it later.

## Run it

```bash
pip install -r requirements.txt
python -m benchbuddy
```

Run the tests (the GUI tests run headless):

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
```

## What's inside

| Tab | What it does |
|---|---|
| **Power budget** | Build a power tree (USB / battery / adapter → LDO / buck / boost → loads). Shows average and peak current per rail, voltage sag from cable/battery resistance, regulator dropout failures, LDO heat, bulk-capacitor suggestions and battery runtime. Presets for ESP32 variants, Arduino, servos, NeoPixels, SIM800L, etc. A live **power-tree diagram** draws supply → regulator → load with status colours, peak-vs-rating bars and current-weighted lines; click a rail to select it, or **⤢ Expand** it into its own window. Save/open as JSON. |
| **Brown-out sim** | Time-domain simulation of a load burst (e.g. Wi-Fi TX) through source resistance, input cap, regulator and output cap. Shows the voltage dip against your brown-out threshold, can import a rail from the Power budget, and finds the smallest output capacitor that survives. |
| **Resistors** | Colour code ⇄ value (3-6 bands), SMD codes (3/4-digit, R, EIA-96), E12/E24/E96 nearest values, series/parallel, voltage divider, LED resistor. |
| **Capacitors** | Marking decoder (104, 473K, 4n7 ...), RC time/cutoff, reactance, series/parallel, hold-up and bulk-cap sizing, ESP32 decoupling rules. |
| **Design calcs** | Battery ADC divider, IPC-2221 trace width, buck/boost inductor sizing, I2C pull-up range, BJT base resistor. |
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

## How the power budget thinks

- **Average** current drives battery runtime and LDO heat. **Peak** current drives brown-out checks.
- Peaks of all loads are assumed to happen **at the same time**. This is deliberately conservative.
- A rail passes if its average is within its rating, peaks only briefly exceed it, and the
  voltage at the source (after sag from `R int`) still clears every regulator's dropout.
- Warnings use thresholds: 80 % of rating, 5 % / 10 % sag, 50 / 90 °C LDO rise.
- All preset currents are **typical** values. Clones and dev boards vary; confirm with a datasheet or meter.

## Look & feel

Ctrl+1…9 jump between tabs (also under **View**). The window size, last tab and pinout board are remembered between launches.


Dark industrial theme (obsidian / teal / phosphor, flat zero-radius controls, JetBrains Mono).
All colours, the stylesheet and font loading live in `benchbuddy/gui/theme.py`; change a token there
and it applies everywhere, including the brown-out plot and the status banner.
JetBrains Mono NL is bundled in `benchbuddy/gui/fonts/` under the SIL Open Font License (`OFL.txt`),
so it works without installing the font. Arrow/check icons are small SVGs in `benchbuddy/gui/icons/`.

## Layout

```
benchbuddy/core/   units, resistors, capacitors, power, brownout, calcs, presets, pinout, partsdb, seed_parts, seed_extra, identify, ocr
benchbuddy/gui/    one module per tab + main_window, theme (+ fonts/, icons/)
tests/             core maths + headless GUI smoke tests
```

## Ideas for next steps

- Web UI on the same core (FastAPI + a small front end)
- Wi-Fi/BLE burst profiles for the simulator, multi-rail simulation
- Community-shareable parts library
- More calculators (filters, op-amp gain, LED matrix budgets)
