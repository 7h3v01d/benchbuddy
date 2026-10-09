"""Parsing and formatting of engineering-notation values (4k7, 10uF, 2.2M ...)."""

from __future__ import annotations

import re

_PREFIX = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
    "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
}
_UNIT_WORDS = ("ohms", "ohm", "Ω", "farads", "farad", "F", "f", "A", "a", "V", "v",
               "W", "w", "H", "h", "Hz", "hz", "s")

_SI_STEPS = [
    (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
]


def parse_value(text: str) -> float:
    """Parse '4k7', '10 uF', '2.2M', '100n', '0.5', '4R7' into a float.

    Raises ValueError for anything it can't understand.
    """
    if text is None:
        raise ValueError("empty value")
    s = str(text).strip().replace(" ", "").replace(",", "")
    if not s:
        raise ValueError("empty value")

    # strip trailing unit words (longest first), but keep a lone prefix letter
    for word in sorted(_UNIT_WORDS, key=len, reverse=True):
        if s.endswith(word) and len(s) > len(word):
            candidate = s[: -len(word)]
            # don't strip 'F'/'f' etc. if that leaves something unparsable later
            s = candidate
            break

    # RKM style: 4k7, 2R2, 1M5, 4u7
    m = re.fullmatch(r"(\d+)([RrpnuµμmkKMG])(\d+)", s)
    if m:
        whole, letter, frac = m.groups()
        mult = 1.0 if letter in "Rr" else _PREFIX[letter]
        return float(f"{whole}.{frac}") * mult

    m = re.fullmatch(r"([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)([pnuµμmkKMG]?)", s)
    if not m:
        raise ValueError(f"can't parse value: {text!r}")
    number, prefix = m.groups()
    return float(number) * _PREFIX[prefix]


def format_value(value: float, unit: str = "", digits: int = 3) -> str:
    """Format 4700 -> '4.7 kΩ' style strings (unit appended as given)."""
    if value == 0:
        return f"0 {unit}".strip()
    sign = "-" if value < 0 else ""
    v = abs(value)
    for factor, prefix in _SI_STEPS:
        if v >= factor * 0.9999999:
            scaled = v / factor
            text = f"{scaled:.{digits}g}"
            return f"{sign}{text} {prefix}{unit}".strip()
    scaled = v / 1e-12
    return f"{sign}{scaled:.{digits}g} p{unit}".strip()


def format_rkm(value: float) -> str:
    """Resistor RKM notation: 4700 -> '4k7', 220 -> '220R', 0.47 -> 'R47'."""
    if value >= 1e6:
        base, letter = value / 1e6, "M"
    elif value >= 1e3:
        base, letter = value / 1e3, "k"
    else:
        base, letter = value, "R"
    text = f"{base:.3g}"
    if "." in text:
        whole, frac = text.split(".")
        return f"{whole if whole != '0' or letter != 'R' else ''}{letter}{frac}"
    return f"{text}{letter}"
