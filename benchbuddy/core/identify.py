"""'What is this thing?' - interpret a marking against resistor, capacitor and parts DB."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, product

from .capacitors import decode_cap_code
from .partsdb import PartsDB
from .resistors import decode_smd_code
from .units import format_value


@dataclass
class Match:
    kind: str          # "resistor" | "capacitor" | "part"
    title: str
    detail: str
    part_id: int | None = None


def identify(text: str, db: PartsDB | None = None) -> list[Match]:
    """Return every sensible interpretation of a marking, most specific first."""
    q = text.strip()
    out: list[Match] = []
    if not q:
        return out

    if db is not None:
        for p in db.search(q)[:8]:
            out.append(Match("part", f"{p.part_number}: {p.description}",
                             f"{p.category} · {p.package} · {p.specs}".strip(" ·"), p.id))

    # numeric-looking codes could be a resistor, a capacitor, or both
    try:
        ohms = decode_smd_code(q)
        if ohms > 0:
            out.append(Match("resistor", f"If SMD resistor: {format_value(ohms, 'Ω')}",
                             f"Code '{q}' read as a resistor marking (3/4-digit: value × 10^n, R = decimal point; "
                             f"2 digits + letter = EIA-96)."))
    except ValueError:
        pass
    try:
        cap = decode_cap_code(q)
        out.append(Match("capacitor", f"If capacitor: {format_value(cap.farads, 'F')}",
                         "Code '{}' read as a capacitor marking ({}){}.".format(
                             q, cap.note, f", tolerance {cap.tolerance}" if cap.tolerance else "")))
    except ValueError:
        pass
    return out


# ---------------------------------------------------------------- photo / OCR candidates

# characters that OCR (and tired eyes) mix up on printed markings
LOOKALIKES = {"5": "SO", "S": "5O", "0": "OD", "O": "05D", "D": "0O", "1": "IL", "I": "1L", "L": "1I",
              "8": "B", "B": "8", "2": "Z", "Z": "2", "6": "G", "G": "6"}


def lookalike_variants(token: str, max_subs: int = 2, limit: int = 400) -> list[str]:
    """The token itself, then versions with up to `max_subs` look-alike characters swapped."""
    t = token.upper()
    out = [t]
    seen = {t}
    positions = [i for i, ch in enumerate(t) if ch in LOOKALIKES]
    for k in range(1, max_subs + 1):
        for idxs in combinations(positions, k):
            for repl in product(*(LOOKALIKES[t[i]] for i in idxs)):
                chars = list(t)
                for i, r in zip(idxs, repl):
                    chars[i] = r
                v = "".join(chars)
                if v not in seen:
                    seen.add(v)
                    out.append(v)
                    if len(out) >= limit:
                        return out
    return out


@dataclass
class PhotoMatch:
    token: str            # what the OCR read
    variant: str          # what was actually looked up (differs when look-alikes were swapped)
    part_id: int
    part_number: str
    description: str
    score: int
    note: str


def match_candidates(tokens: list[str], db: PartsDB, limit: int = 10) -> list[PhotoMatch]:
    """Match OCR candidates against the library, tolerating look-alike character mistakes.

    The token exactly as read may match loosely (partial part numbers and keywords); swapped
    variants only count when they match a marking or part number strongly, to avoid noise.
    """
    best: dict[int, PhotoMatch] = {}
    parts = db.all()                                  # load the library once, not once per variant
    for rank, token in enumerate(tokens):
        for variant in lookalike_variants(token, max_subs=3):
            exact_read = variant == token.upper()
            for score, part in db.search_scored(variant, limit=20, parts=parts):
                if score < (60 if exact_read else 55):
                    continue
                adj = score - (0 if exact_read else 10) - min(rank, 10)
                if part.id in best and best[part.id].score >= adj:
                    continue
                note = ("read exactly as printed" if exact_read else
                        f"read as {token.upper()}, matched as {variant} (look-alike characters swapped)")
                if score < 95 and exact_read:
                    note = f"partial match for {token.upper()}"
                best[part.id] = PhotoMatch(token.upper(), variant, part.id, part.part_number,
                                           part.description, adj, note)
    return sorted(best.values(), key=lambda m: (-m.score, m.part_number))[:limit]
