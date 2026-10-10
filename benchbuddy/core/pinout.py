"""Board pin reference: what each pin can do and what will bite you.

Each pin carries
  * caps   - capabilities used by the "find me a pin" filter
  * flags  - gotchas, each with a severity (caution / avoid)
  * funcs  - default peripheral roles worth knowing (Arduino-core defaults)

Data is from the Espressif / Microchip datasheets, ESP-IDF GPIO docs and the Arduino core pin
maps (the D1 R32 header follows arduino-esp32's variants/d1_uno32/pins_arduino.h).
Dev-board details (onboard LEDs, PSRAM variants) vary between clones: check yours.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ------------------------------------------------------------------- vocabulary
# capabilities
IN, OUT, PWM, ADC, ADC_WIFI, TOUCH, DAC, WAKE, INT = (
    "in", "out", "pwm", "adc", "adc_wifi", "touch", "dac", "wake", "int")

# flags -> (severity, short label, explanation)
FLAGS: dict[str, tuple[str, str, str]] = {
    "flash": ("avoid", "SPI flash",
              "Wired to the module's SPI flash/PSRAM. Using it crashes or bricks the boot."),
    "usb": ("avoid", "USB",
            "Native USB D-/D+. Using it kills USB serial/flashing on boards that use native USB."),
    "strap": ("caution", "Strapping",
              "Sampled at reset to pick boot mode / flash voltage. An external pull or load "
              "here can stop the board booting or flashing."),
    "input_only": ("caution", "Input only",
                   "No output driver and no internal pull-up/down. Add an external resistor for buttons."),
    "adc2": ("caution", "ADC2",
             "ADC2 channel: unusable while Wi-Fi is running. Use an ADC1 pin for analog + Wi-Fi."),
    "boot_glitch": ("caution", "Boot glitch",
                    "Toggles or outputs a signal during boot. Don't drive relays/motors from it."),
    "serial": ("caution", "USB serial",
               "Shared with the USB-UART used for uploading and Serial. Avoid unless you give up Serial."),
    "jtag": ("caution", "JTAG",
             "JTAG debug pin by default. Fine as GPIO if you don't use a hardware debugger."),
    "psram": ("caution", "PSRAM",
              "Used by PSRAM / octal flash on some module variants (e.g. WROVER, S3 N8R8). Free otherwise."),
    "analog_only": ("caution", "Analog only",
                    "Analog input only: no digital read/write, no pull-ups."),
    "led": ("caution", "Onboard LED",
            "Drives the board's LED on most dev boards; it loads the pin and lights up with your signal."),
    "timer": ("caution", "Timer clash",
              "Its PWM timer is also used by a common library (noted on the pin)."),
}

SEVERITY_ORDER = {"ok": 0, "caution": 1, "avoid": 2}

# filter name -> (label, predicate on Pin)
REQUIREMENTS: dict[str, tuple[str, object]] = {
    "out": ("Digital output", lambda p: OUT in p.caps),
    "pwm": ("PWM", lambda p: PWM in p.caps),
    "adc": ("Analog in (ADC)", lambda p: ADC in p.caps),
    "adc_wifi": ("Analog in with Wi-Fi on", lambda p: ADC_WIFI in p.caps),
    "int": ("Interrupt", lambda p: INT in p.caps),
    "touch": ("Capacitive touch", lambda p: TOUCH in p.caps),
    "dac": ("True analog out (DAC)", lambda p: DAC in p.caps),
    "wake": ("Deep-sleep wake", lambda p: WAKE in p.caps),
    "boot_safe": ("Safe at boot", lambda p: not ({"strap", "boot_glitch", "flash", "usb", "serial"} & p.flags)),
}


@dataclass
class Pin:
    label: str                    # what's printed on the board / used in code
    gpio: int | None = None       # chip GPIO number (None for AVR / power pins)
    funcs: list[str] = field(default_factory=list)
    caps: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    note: str = ""

    @property
    def severity(self) -> str:
        worst = "ok"
        for f in self.flags:
            sev = FLAGS[f][0]
            if SEVERITY_ORDER[sev] > SEVERITY_ORDER[worst]:
                worst = sev
        return worst

    def matches(self, requirements: list[str]) -> bool:
        return all(REQUIREMENTS[r][1](self) for r in requirements)

    @property
    def code_name(self) -> str:
        return str(self.gpio) if self.gpio is not None else self.label


@dataclass
class Board:
    name: str
    chip: str
    pins: list[Pin]
    notes: list[str] = field(default_factory=list)
    max_ma: str = ""

    def find(self, label: str) -> Pin:
        for p in self.pins:
            if p.label.lower() == label.lower() or (p.gpio is not None and str(p.gpio) == label):
                return p
        raise KeyError(label)


def _gpio(n: int, funcs=(), caps=(), flags=(), note: str = "", label: str | None = None) -> Pin:
    return Pin(label or f"GPIO{n}", n, list(funcs), set(caps), set(flags), note)


# ======================================================================== ESP32
_IO = {IN, OUT, PWM, INT}          # every ESP32-family output-capable GPIO has LEDC PWM + interrupts


def _esp32_pins() -> list[Pin]:
    io = _IO
    rtc = {WAKE}
    P = _gpio
    return [
        P(0, ["ADC2_CH1", "TOUCH1", "BOOT button"], io | {ADC, TOUCH} | rtc, {"strap", "adc2", "boot_glitch"},
          "Must be HIGH at reset to run your sketch (LOW = download mode). The BOOT button pulls it LOW."),
        P(1, ["U0TXD (Serial TX)"], io, {"serial", "boot_glitch"}, "Prints the boot log at reset."),
        P(2, ["ADC2_CH2", "TOUCH2"], io | {ADC, TOUCH} | rtc, {"strap", "adc2", "led"},
          "Must be LOW or floating to enter download mode. Many DevKits have the blue LED here."),
        P(3, ["U0RXD (Serial RX)"], io, {"serial"}, "HIGH at boot."),
        P(4, ["ADC2_CH0", "TOUCH0"], io | {ADC, TOUCH} | rtc, {"adc2"}),
        P(5, ["VSPI SS"], io, {"strap", "boot_glitch"}, "Strapping pin for SDIO timing; outputs PWM briefly at boot."),
        P(6, ["SPI flash CLK"], set(), {"flash"}),
        P(7, ["SPI flash D0"], set(), {"flash"}),
        P(8, ["SPI flash D1"], set(), {"flash"}),
        P(9, ["SPI flash D2"], set(), {"flash"}),
        P(10, ["SPI flash D3"], set(), {"flash"}),
        P(11, ["SPI flash CMD"], set(), {"flash"}),
        P(12, ["ADC2_CH5", "TOUCH5", "HSPI MISO", "MTDI"], io | {ADC, TOUCH} | rtc, {"strap", "adc2"},
          "Sets flash voltage at reset: HIGH selects 1.8 V and a 3.3 V-flash module won't boot. "
          "Keep it LOW at reset (or burn the VDD_SDIO eFuse)."),
        P(13, ["ADC2_CH4", "TOUCH4", "HSPI MOSI"], io | {ADC, TOUCH} | rtc, {"adc2"}),
        P(14, ["ADC2_CH6", "TOUCH6", "HSPI CLK"], io | {ADC, TOUCH} | rtc, {"adc2", "boot_glitch"},
          "Outputs PWM briefly at boot."),
        P(15, ["ADC2_CH3", "TOUCH3", "HSPI SS", "MTDO"], io | {ADC, TOUCH} | rtc, {"strap", "adc2", "boot_glitch"},
          "LOW at reset silences the boot log. Outputs PWM briefly at boot."),
        P(16, ["U2RXD (Serial2 RX)"], io, {"psram"}, "Not available on WROVER modules (PSRAM)."),
        P(17, ["U2TXD (Serial2 TX)"], io, {"psram"}, "Not available on WROVER modules (PSRAM)."),
        P(18, ["VSPI SCK"], io),
        P(19, ["VSPI MISO"], io),
        P(21, ["I2C SDA (default)"], io),
        P(22, ["I2C SCL (default)"], io),
        P(23, ["VSPI MOSI"], io),
        P(25, ["DAC1", "ADC2_CH8"], io | {ADC, DAC} | rtc, {"adc2"}),
        P(26, ["DAC2", "ADC2_CH9"], io | {ADC, DAC} | rtc, {"adc2"}),
        P(27, ["ADC2_CH7", "TOUCH7"], io | {ADC, TOUCH} | rtc, {"adc2"}),
        P(32, ["ADC1_CH4", "TOUCH9", "32k XTAL"], io | {ADC, ADC_WIFI, TOUCH} | rtc),
        P(33, ["ADC1_CH5", "TOUCH8", "32k XTAL"], io | {ADC, ADC_WIFI, TOUCH} | rtc),
        P(34, ["ADC1_CH6"], {IN, INT, ADC, ADC_WIFI} | rtc, {"input_only"}),
        P(35, ["ADC1_CH7"], {IN, INT, ADC, ADC_WIFI} | rtc, {"input_only"}),
        P(36, ["ADC1_CH0", "SENSOR_VP"], {IN, INT, ADC, ADC_WIFI} | rtc, {"input_only"}),
        P(39, ["ADC1_CH3", "SENSOR_VN"], {IN, INT, ADC, ADC_WIFI} | rtc, {"input_only"}),
    ]


ESP32_NOTES = [
    "3.3 V logic only. GPIOs are not 5 V tolerant.",
    "ADC is non-linear near 0 V and above ~3.1 V (11 dB attenuation); calibrate or stay mid-range.",
    "All output-capable pins do PWM (LEDC) and interrupts; any pin can be routed to UART/SPI/I2C via the matrix.",
    "Deep-sleep wake (ext0/ext1, touch) works only on RTC GPIOs.",
]


def esp32_devkit() -> Board:
    return Board("ESP32 DevKit (WROOM-32)", "ESP32", _esp32_pins(), ESP32_NOTES,
                 "≈ 20 mA per pin recommended, 40 mA absolute max")


def wemos_d1_r32() -> Board:
    """Uno-shaped ESP32 board: header labels mapped onto ESP32 GPIOs."""
    base = {p.gpio: p for p in _esp32_pins()}
    header = [("D0", 3), ("D1", 1), ("D2", 26), ("D3", 25), ("D4", 17), ("D5", 16), ("D6", 27), ("D7", 14),
              ("D8", 12), ("D9", 13), ("D10", 5), ("D11", 23), ("D12", 19), ("D13", 18),
              ("A0", 2), ("A1", 4), ("A2", 35), ("A3", 34), ("A4", 36), ("A5", 39),
              ("SDA", 21), ("SCL", 22)]
    pins = []
    for label, g in header:
        src = base[g]
        pins.append(Pin(f"{label} · IO{g}", g, list(src.funcs), set(src.caps), set(src.flags), src.note))
    notes = [
        "Header labels follow the Uno, but the pins are ESP32 GPIOs: use the IO number in code.",
        "3.3 V logic: 5 V Uno shields that drive the I/O lines at 5 V can damage it.",
        "A4/A5 are NOT I2C here (they're input-only ADC pins). I2C is on the SDA/SCL pins (IO21/IO22).",
        "D8 is IO12 (flash-voltage strap): a shield pulling it HIGH stops the board booting.",
        "A0 (IO2) is the onboard LED (LED_BUILTIN = 2).",
        "Some batches have silkscreen misprints on the analog row (e.g. A2 printed IO36 but is IO35, "
        "A4 printed IO38 but is IO36). This map follows the Arduino-ESP32 'd1_uno32' variant.",
    ] + ESP32_NOTES[1:]
    return Board("Wemos D1 R32 (ESP32, Uno form)", "ESP32", pins, notes,
                 "≈ 20 mA per pin recommended, 40 mA absolute max")


# ===================================================================== ESP32-S3
def esp32_s3() -> Board:
    io = _IO
    P = _gpio
    pins: list[Pin] = []
    for n in range(0, 22):
        caps = set(io)
        funcs: list[str] = []
        flags: set[str] = set()
        note = ""
        caps.add(WAKE)                                  # GPIO0-21 are RTC GPIOs
        if 1 <= n <= 10:
            funcs.append(f"ADC1_CH{n - 1}")
            caps |= {ADC, ADC_WIFI}
        elif 11 <= n <= 20:
            funcs.append(f"ADC2_CH{n - 11}")
            caps.add(ADC)
            flags.add("adc2")
        if 1 <= n <= 14:
            funcs.append(f"TOUCH{n}")
            caps.add(TOUCH)
        if n == 0:
            funcs.append("BOOT button")
            flags.add("strap")
            note = "LOW at reset = download mode. The BOOT button pulls it LOW."
        if n == 3:
            flags.add("strap")
            note = "JTAG source select strap (only matters if eFuses are set); keep it floating at reset."
        if n == 19:
            funcs.append("USB D-")
            flags.add("usb")
        if n == 20:
            funcs.append("USB D+")
            flags.add("usb")
        defaults = {8: "I2C SDA (default)", 9: "I2C SCL (default)", 10: "SPI SS", 11: "SPI MOSI",
                    12: "SPI SCK", 13: "SPI MISO"}
        if n in defaults:
            funcs.append(defaults[n])
        pins.append(P(n, funcs, caps, flags, note))
    for n in range(26, 33):
        pins.append(P(n, ["SPI flash/PSRAM"], set(), {"flash"}))
    for n in range(33, 38):
        pins.append(P(n, ["Octal flash/PSRAM"], set(io), {"psram"},
                      "Used by octal PSRAM/flash on R8 / N8R8-style modules; free on quad-SPI modules."))
    for n in (38, 39, 40, 41, 42):
        flags = {"jtag"} if n >= 39 else set()
        funcs = {39: ["MTCK"], 40: ["MTDO"], 41: ["MTDI"], 42: ["MTMS"]}.get(n, [])
        note = "RGB LED on DevKitC-1 v1.1." if n == 38 else ""
        if n == 38:
            flags.add("led")
        pins.append(P(n, funcs, set(io), flags, note))
    pins.append(P(43, ["U0TXD (Serial TX)"], set(io), {"serial"}))
    pins.append(P(44, ["U0RXD (Serial RX)"], set(io), {"serial"}))
    pins.append(P(45, ["VDD_SPI strap"], set(io), {"strap"},
                  "Selects flash voltage at reset. Leave it at its default (LOW) on 3.3 V-flash modules."))
    pins.append(P(46, ["ROM log strap"], set(io), {"strap"},
                  "Boot-mode / ROM-log strap. Must be LOW to enter download mode with GPIO0."))
    pins.append(P(47, [], set(io)))
    pins.append(P(48, [], set(io), {"led"}, "RGB LED on DevKitC-1 v1.0."))
    notes = [
        "3.3 V logic only.",
        "ADC1 = GPIO1-10, ADC2 = GPIO11-20. ADC2 conflicts with Wi-Fi.",
        "Boards using native USB for Serial/flashing lose GPIO19/20.",
        "GPIO26-32 are always taken by flash; GPIO33-37 only on octal-PSRAM/flash modules.",
        "Onboard RGB LED is GPIO48 (DevKitC-1 v1.0) or GPIO38 (v1.1).",
    ]
    return Board("ESP32-S3 DevKit", "ESP32-S3", pins, notes, "≈ 20 mA per pin recommended, 40 mA absolute max")


# ===================================================================== ESP32-C3
def esp32_c3() -> Board:
    io = _IO
    P = _gpio
    pins: list[Pin] = []
    for n in range(0, 22):
        caps = set(io)
        funcs: list[str] = []
        flags: set[str] = set()
        note = ""
        if n <= 5:
            caps.add(WAKE)
        if n <= 4:
            funcs.append(f"ADC1_CH{n}")
            caps |= {ADC, ADC_WIFI}
        if n == 5:
            funcs.append("ADC2_CH0")
            caps.add(ADC)
            flags.add("adc2")
            note = "ADC2 is unreliable on the C3 (Espressif errata) and unusable with Wi-Fi."
        if n in (0, 1):
            funcs.append("32k XTAL")
        if n in (4, 5, 6, 7):
            flags.add("jtag")
            funcs.append({4: "MTMS", 5: "MTDI", 6: "MTCK", 7: "MTDO"}[n])
        if n == 2:
            flags.add("strap")
            note = "Strapping pin: keep HIGH (or floating with its weak pull-up) at reset."
        if n == 8:
            flags |= {"strap", "led"}
            funcs.append("I2C SDA (default)")
            note = ("Must be HIGH at reset for download mode. Many C3 boards put the LED / WS2812 here, "
                    "and it's also the Arduino default SDA.")
        if n == 9:
            flags.add("strap")
            funcs += ["BOOT button", "I2C SCL (default)"]
            note = "LOW at reset = download mode (BOOT button). Also the Arduino default SCL: pull-ups keep it HIGH, fine."
        if 12 <= n <= 17:
            funcs = ["SPI flash"]
            caps = set()
            flags = {"flash"}
            note = "Used by the SPI flash on most modules/boards."
        if n == 18:
            funcs.append("USB D-")
            flags.add("usb")
        if n == 19:
            funcs.append("USB D+")
            flags.add("usb")
        if n == 20:
            funcs.append("U0RXD (Serial RX)")
            flags.add("serial")
        if n == 21:
            funcs.append("U0TXD (Serial TX)")
            flags.add("serial")
        pins.append(P(n, funcs, caps, flags, note))
    notes = [
        "3.3 V logic only. Single-core RISC-V, so fewer pins than the classic ESP32.",
        "Only GPIO0-4 are usable ADC with Wi-Fi (ADC1).",
        "Deep-sleep wake on GPIO0-5 only.",
        "GPIO11 is VDD_SPI on some packages; check before using it.",
        "Super-mini boards bring out only a subset; LED pin varies (often GPIO8).",
    ]
    return Board("ESP32-C3 DevKit / Super Mini", "ESP32-C3", pins, notes, "≈ 20 mA per pin recommended, 40 mA absolute max")


# =================================================================== ATmega328P
def arduino_uno_nano() -> Board:
    pins: list[Pin] = []
    io = {IN, OUT}
    spec = {
        0: (["RX (Serial)"], {"serial"}, "Shared with the USB serial chip."),
        1: (["TX (Serial)"], {"serial"}, "Shared with the USB serial chip."),
        2: (["INT0"], set(), ""),
        3: (["INT1", "PWM 490 Hz (Timer2)"], {"timer"}, "Timer2 is used by tone(); PWM here stops while a tone plays."),
        4: ([], set(), ""),
        5: (["PWM 980 Hz (Timer0)"], set(), "Timer0 also runs millis(); don't change its prescaler."),
        6: (["PWM 980 Hz (Timer0)"], set(), "Timer0 also runs millis(); don't change its prescaler."),
        7: ([], set(), ""),
        8: ([], set(), ""),
        9: (["PWM 490 Hz (Timer1)"], {"timer"}, "Servo library takes Timer1: analogWrite on D9/D10 stops working."),
        10: (["PWM 490 Hz (Timer1)", "SPI SS"], {"timer"},
             "Servo library takes Timer1. Must stay an OUTPUT in SPI master mode."),
        11: (["PWM 490 Hz (Timer2)", "SPI MOSI"], {"timer"}, "Timer2 is used by tone()."),
        12: (["SPI MISO"], set(), ""),
        13: (["SPI SCK"], {"led"}, "Onboard 'L' LED is on D13."),
    }
    for d, (funcs, flags, note) in spec.items():
        caps = set(io) | ({PWM} if d in (3, 5, 6, 9, 10, 11) else set()) | ({INT} if d in (2, 3) else set())
        pins.append(Pin(f"D{d}", None, funcs, caps, set(flags), note))
    for a in range(6):
        funcs = [f"ADC{a}", f"D{14 + a}"]
        if a == 4:
            funcs.append("I2C SDA")
        if a == 5:
            funcs.append("I2C SCL")
        pins.append(Pin(f"A{a}", None, funcs, io | {ADC}, set(),
                        "Also usable as a digital pin." if a < 4 else "Shared with I2C: used up if you use Wire."))
    for a in (6, 7):
        pins.append(Pin(f"A{a}", None, [f"ADC{a}"], {IN, ADC}, {"analog_only"}, "Nano / Pro Mini only, not on the Uno."))
    notes = [
        "5 V logic (3.3 V on 8 MHz Pro Minis). Inputs read HIGH above ~3 V at 5 V supply.",
        "Only D2/D3 have dedicated external interrupts; pin-change interrupts work on every pin via a library.",
        "Total current through VCC/GND is limited to ~200 mA for the whole chip.",
        "ADC is 10-bit; the reference is AVcc by default (so it follows your USB voltage).",
    ]
    return Board("Arduino Uno / Nano (ATmega328P)", "ATmega328P", pins, notes,
                 "20 mA per pin recommended, 40 mA absolute max, ~200 mA total")


BOARDS = {b.name: b for b in (esp32_devkit(), wemos_d1_r32(), esp32_s3(), esp32_c3(), arduino_uno_nano())}


def suggest(board: Board, requirements: list[str], include_caution: bool = True) -> list[Pin]:
    """Pins that meet every requirement, best (fewest gotchas) first."""
    hits = [p for p in board.pins if p.matches(requirements) and p.severity != "avoid"]
    if not include_caution:
        hits = [p for p in hits if p.severity == "ok"]
    return sorted(hits, key=lambda p: (SEVERITY_ORDER[p.severity], len(p.flags)))
