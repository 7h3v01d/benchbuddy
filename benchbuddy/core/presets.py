"""Typical-value presets for loads, supplies and regulators.

All figures are *typical* and meant as a sane starting point. Always check the
datasheet or measure with a meter: clones and dev boards vary a lot.
Currents are in mA.
"""

from __future__ import annotations

from .power import Load, Rail

# name: (rail voltage, active, peak, sleep, note)
LOAD_PRESETS: dict[str, dict] = {
    # --- microcontrollers
    "ESP32 DevKit (WROOM-32)": dict(v=3.3, active=80, peak=500, sleep=5,
        note="Wi-Fi TX bursts 240-500 mA. Bare chip deep-sleeps at ~10 µA, but dev boards with USB-UART/LEDs waste 5-10 mA."),
    "ESP32-S3 DevKit": dict(v=3.3, active=90, peak=500, sleep=7,
        note="Wi-Fi TX bursts up to ~500 mA."),
    "ESP32-C3 board": dict(v=3.3, active=60, peak=350, sleep=5,
        note="Lower power than classic ESP32."),
    "ESP32-CAM (AI-Thinker)": dict(v=5.0, active=200, peak=600, sleep=6,
        note="Camera + Wi-Fi + flash LED. Notorious for brown-outs on weak USB supplies; give it a solid 5 V."),
    "ESP8266 NodeMCU / D1 mini": dict(v=3.3, active=70, peak=320, sleep=1,
        note="TX bursts ~300 mA. Deep sleep on the bare chip is ~20 µA."),
    "Arduino Uno R3": dict(v=5.0, active=45, peak=50, sleep=35,
        note="Board draws ~45 mA running; USB chip and regulator keep it high even when the AVR sleeps."),
    "Arduino Nano": dict(v=5.0, active=19, peak=25, sleep=15, note="~19 mA typical."),
    "Arduino Pro Mini 3.3 V / 8 MHz": dict(v=3.3, active=5, peak=8, sleep=0.005,
        note="With regulator + power LED removed, sleep can be a few µA."),
    "Raspberry Pi Pico (RP2040)": dict(v=3.3, active=25, peak=50, sleep=1, note="Pico W peaks ~150 mA with Wi-Fi."),
    # --- displays
    "SSD1306 OLED 128x64 (I2C)": dict(v=3.3, active=10, peak=20, sleep=0.01, note="Depends on how many pixels are lit."),
    "ILI9341 2.8\" TFT (with backlight)": dict(v=3.3, active=80, peak=100, sleep=80, note="Backlight dominates."),
    "HD44780 16x2 LCD (backlight on)": dict(v=5.0, active=25, peak=30, sleep=25, note="~20 mA of this is the backlight."),
    # --- LEDs / sound
    "WS2812B / NeoPixel (per LED)": dict(v=5.0, active=30, peak=60, sleep=1,
        note="60 mA each at full white; ~1 mA even when 'off'. Multiply with qty."),
    "5 mm LED + resistor": dict(v=3.3, active=10, peak=20, sleep=0, note="Set by your resistor."),
    "Active buzzer (5 V)": dict(v=5.0, active=30, peak=30, sleep=0, note=""),
    # --- sensors
    "HC-SR04 ultrasonic": dict(v=5.0, active=15, peak=20, sleep=2, note="ECHO pin is 5 V: level-shift it for 3.3 V boards."),
    "DHT22 / AM2302": dict(v=3.3, active=1.5, peak=1.5, sleep=0.05, note=""),
    "DS18B20 temperature": dict(v=3.3, active=1.5, peak=1.5, sleep=0.001, note=""),
    "BME280 / BMP280": dict(v=3.3, active=0.35, peak=0.7, sleep=0.0001, note=""),
    "MPU6050 IMU": dict(v=3.3, active=3.9, peak=4, sleep=0.005, note=""),
    "NEO-6M GPS": dict(v=3.3, active=45, peak=70, sleep=11, note="Acquisition draws more than tracking."),
    "SD card module (SPI)": dict(v=3.3, active=25, peak=100, sleep=0.2, note="Writes spike to ~100 mA."),
    # --- radio
    "nRF24L01+": dict(v=3.3, active=13.5, peak=15, sleep=0.001,
        note="Very sensitive to supply noise: 10 µF + 100 nF right at the module."),
    "LoRa SX1276 / RFM95 (+20 dBm)": dict(v=3.3, active=12, peak=130, sleep=0.0002, note="TX at +20 dBm ~120 mA."),
    "SIM800L GSM": dict(v=4.0, active=100, peak=2000, sleep=1,
        note="TX bursts up to 2 A! Needs 3.4-4.4 V, ≥1000 µF bulk cap, and a supply that can really deliver 2 A."),
    # --- actuators
    "SG90 micro servo": dict(v=5.0, active=150, peak=650, sleep=10, note="Peak = stall. Several servos starting together add up fast."),
    "MG996R servo": dict(v=5.0, active=500, peak=2500, sleep=10, note="Stall ~2.5 A at 6 V."),
    "Relay module (1 ch, 5 V)": dict(v=5.0, active=75, peak=90, sleep=0, note="Coil + LED while energised."),
    "TT gear motor (3-6 V)": dict(v=5.0, active=250, peak=1200, sleep=0, note="Stall current is much higher than running."),
    "NEMA17 stepper (A4988/DRV8825)": dict(v=12.0, active=1000, peak=1700, sleep=50, note="Set by driver current limit; holding torque keeps drawing."),
    "28BYJ-48 stepper + ULN2003": dict(v=5.0, active=240, peak=300, sleep=0, note="Coils stay energised when holding."),
    "5 V fan (40 mm)": dict(v=5.0, active=100, peak=200, sleep=0, note="Start-up surge."),
    "L298N driver (logic quiescent)": dict(v=5.0, active=36, peak=40, sleep=36, note="Plus ~2 V drop on the motor supply."),
}

# name: Rail
SOURCE_PRESETS: dict[str, Rail] = {
    "USB 2.0 port (5 V, 500 mA)": Rail("USB 5V", 5.0, "supply", max_ma=500, v_min=4.75, r_internal_ohm=0.3),
    "USB 3.0 port (5 V, 900 mA)": Rail("USB 5V", 5.0, "supply", max_ma=900, v_min=4.75, r_internal_ohm=0.3),
    "USB charger (5 V, 2 A)": Rail("USB 5V", 5.0, "supply", max_ma=2000, v_min=4.75, r_internal_ohm=0.35),
    "Bench / wall supply 5 V 3 A": Rail("5V supply", 5.0, "supply", max_ma=3000, r_internal_ohm=0.05),
    "Wall adapter 12 V 2 A": Rail("12V supply", 12.0, "supply", max_ma=2000, v_min=11.4, r_internal_ohm=0.15),
    "LiPo 1S 1000 mAh (1C)": Rail("LiPo", 3.7, "supply", max_ma=1000, v_min=3.0, r_internal_ohm=0.15, capacity_mah=1000),
    "18650 Li-ion 2600 mAh": Rail("18650", 3.7, "supply", max_ma=2600, v_min=3.0, r_internal_ohm=0.1, capacity_mah=2600),
    "2x AA alkaline (3 V)": Rail("2xAA", 3.0, "supply", max_ma=500, v_min=2.0, r_internal_ohm=0.3, capacity_mah=2500),
    "4x AA alkaline (6 V)": Rail("4xAA", 6.0, "supply", max_ma=1000, v_min=4.0, r_internal_ohm=0.6, capacity_mah=2500),
    "9 V PP3 battery": Rail("9V batt", 9.0, "supply", max_ma=150, v_min=6.0, r_internal_ohm=2.0, capacity_mah=500,
                            note="Weak: high internal resistance, tiny capacity. Poor choice for ESP32 or servos."),
}

REGULATOR_PRESETS: dict[str, Rail] = {
    "AMS1117-3.3 (LDO, 800 mA)": Rail("3V3", 3.3, "ldo", max_ma=800, dropout_v=1.1, iq_ma=5, theta_ja=100),
    "AP2112K-3.3 (LDO, 600 mA)": Rail("3V3", 3.3, "ldo", max_ma=600, dropout_v=0.25, iq_ma=0.055, theta_ja=200),
    "HT7333 (LDO, 250 mA, low Iq)": Rail("3V3", 3.3, "ldo", max_ma=250, dropout_v=0.3, iq_ma=0.004, theta_ja=160),
    "MCP1700-3302 (LDO, 250 mA, low Iq)": Rail("3V3", 3.3, "ldo", max_ma=250, dropout_v=0.18, iq_ma=0.0016, theta_ja=230),
    "LM7805 (LDO, 1 A)": Rail("5V", 5.0, "ldo", max_ma=1000, dropout_v=2.0, iq_ma=6, theta_ja=65),
    "Buck 5 V 3 A (LM2596 / generic)": Rail("5V", 5.0, "buck", max_ma=3000, dropout_v=1.5, efficiency=0.85, iq_ma=5),
    "Buck 3.3 V 1.5 A (MP1584 mini)": Rail("3V3", 3.3, "buck", max_ma=1500, dropout_v=1.0, efficiency=0.9, iq_ma=2),
    "Boost 5 V 1 A (MT3608, from LiPo)": Rail("5V boost", 5.0, "boost", max_ma=1000, min_vin=2.0, efficiency=0.85, iq_ma=1),
}


def make_load(preset_name: str, rail: str, qty: int = 1, duty: float = 1.0) -> Load:
    p = LOAD_PRESETS[preset_name]
    return Load(preset_name, rail, qty, p["active"], p["peak"], p["sleep"], duty)


def make_rail(preset: Rail, name: str | None = None, parent: str | None = None) -> Rail:
    from dataclasses import replace
    return replace(preset, name=name or preset.name, parent=parent)
