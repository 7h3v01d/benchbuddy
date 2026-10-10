"""Optional OCR for reading the markings printed on a component from a photo.

Needs two extras that are NOT required for the rest of BenchBuddy:
    pip install pytesseract pillow
    plus the Tesseract program itself (see INSTALL_HINT).

Honest expectations: clean, close-up, well-lit photos of through-hole parts and
bigger SMD markings work reasonably. Tiny laser-etched SMD codes, shiny packages and
blurry shots often don't. Results are *candidates*: pick the right one, then the
parts library tells you what it might be.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

from .images import check_image

_SOURCE_HINT = (
    "To read markings from photos, install the optional extras:\n"
    "  pip install pytesseract pillow\n"
    "and Tesseract itself:\n"
    "  Windows: installer from github.com/UB-Mannheim/tesseract/wiki\n"
    "  macOS:   brew install tesseract\n"
    "  Linux:   sudo apt install tesseract-ocr"
)
_FROZEN_HINT = (
    "Photo reading is built into this app; it only needs the free Tesseract program:\n"
    "  install it from github.com/UB-Mannheim/tesseract/wiki (default folder is fine),\n"
    "  or point the BENCHBUDDY_TESSERACT environment variable at tesseract.exe."
)
INSTALL_HINT = _FROZEN_HINT if getattr(sys, "frozen", False) else _SOURCE_HINT

_WINDOWS_DEFAULTS = (                    # (environment variable, path below it)
    ("ProgramFiles", ("Tesseract-OCR", "tesseract.exe")),
    ("ProgramFiles(x86)", ("Tesseract-OCR", "tesseract.exe")),
    ("LOCALAPPDATA", ("Programs", "Tesseract-OCR", "tesseract.exe")),
)


def find_tesseract() -> str | None:
    """BENCHBUDDY_TESSERACT, then PATH, then the Windows installer's default folders."""
    override = os.environ.get("BENCHBUDDY_TESSERACT", "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        for var, parts in _WINDOWS_DEFAULTS:
            base = os.environ.get(var)
            if base and Path(base, *parts).is_file():
                return str(Path(base, *parts))
    return None


WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-+./"
_ALLOWED = re.compile(r"[^A-Z0-9\-+./]")


def ocr_available() -> tuple[bool, str]:
    """(ok, reason). reason explains what is missing when ok is False."""
    try:
        import importlib
        importlib.import_module("PIL")
    except ImportError:
        return False, "The 'pillow' package is not installed."
    try:
        import pytesseract
    except ImportError:
        return False, "The 'pytesseract' package is not installed."
    exe = find_tesseract()
    if not exe:
        return False, "The Tesseract program wasn't found (not on PATH or in its default install folder)."
    pytesseract.pytesseract.tesseract_cmd = exe
    return True, ""


def clean_tokens(text: str) -> list[str]:
    """Turn raw OCR output into plausible marking candidates (upper-case, 2-24 chars)."""
    out: list[str] = []
    for raw in re.split(r"\s+", text.upper()):
        tok = _ALLOWED.sub("", raw).strip("-+./")
        if 2 <= len(tok) <= 24 and re.search(r"[A-Z0-9]", tok):
            out.append(tok)
    return out


def _pair_candidates(lines: list[list[str]]) -> list[str]:
    """Join adjacent short tokens on a line ('2N' '3904' -> '2N3904')."""
    joined = []
    for toks in lines:
        for a, b in zip(toks, toks[1:]):
            if len(a) <= 6 and len(b) <= 8:
                joined.append(a + b)
    return joined


def _variants(img):
    """Pre-processed copies of the photo: chips are usually light print on a dark body."""
    from PIL import ImageOps
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=2)
    short = min(gray.size)
    if short < 700:                                   # tesseract likes text that is tall enough
        scale = 700 / short
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
    elif max(gray.size) > 3000:
        scale = 3000 / max(gray.size)
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
    inv = ImageOps.invert(gray)
    return [gray, inv,
            gray.point(lambda v: 255 if v > 140 else 0),
            inv.point(lambda v: 255 if v > 140 else 0)]


def read_markings(image_path: str | Path, max_candidates: int = 12) -> list[str]:
    """Return candidate marking strings found in a photo, most-agreed-upon first."""
    ok, why = ocr_available()
    if not ok:
        raise RuntimeError(f"{why}\n\n{INSTALL_HINT}")
    import pytesseract
    from PIL import Image

    path = Path(image_path)
    check_image(path)                   # real image, size and pixel limits before decoding
    img = Image.open(path)
    img.load()

    votes: Counter[str] = Counter()
    for variant in _variants(img):
        for psm in (6, 11):
            cfg = f"--psm {psm} -c tessedit_char_whitelist={WHITELIST}"
            try:
                text = pytesseract.image_to_string(variant, config=cfg, timeout=30)
            except RuntimeError:                      # tesseract timed out on this variant
                continue
            lines = [clean_tokens(line) for line in text.splitlines()]
            for toks in lines:
                votes.update(toks)
            votes.update(_pair_candidates(lines))
    ranked = sorted(votes, key=lambda t: (-votes[t], -len(t), t))
    return ranked[:max_candidates]
