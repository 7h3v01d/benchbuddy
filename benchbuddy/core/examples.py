"""Ready-made example projects (shared by the GUI and the tests, no Qt needed)."""

from __future__ import annotations

from . import power
from .presets import REGULATOR_PRESETS, SOURCE_PRESETS, make_load, make_rail


def example_project() -> power.Project:
    """An ESP32 DevKit + OLED + BME280 on USB through an AMS1117 (the app's starting project)."""
    usb = make_rail(SOURCE_PRESETS["USB 2.0 port (5 V, 500 mA)"], "USB 5V")
    ldo = make_rail(REGULATOR_PRESETS["AMS1117-3.3 (LDO, 800 mA)"], "3V3", parent="USB 5V")
    loads = [
        make_load("ESP32 DevKit (WROOM-32)", "3V3"),
        make_load("SSD1306 OLED 128x64 (I2C)", "3V3"),
        make_load("BME280 / BMP280", "3V3"),
    ]
    return power.Project([usb, ldo], loads)
