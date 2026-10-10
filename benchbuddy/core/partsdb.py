"""SQLite parts database: search by part number, SMD marking code or keywords.

Library versions
----------------
* v1: first starter library
* v2: ~140 more parts, datasheet link + photo columns. Existing libraries are
      *merged*: missing starter parts are added, anything you edited or added is kept.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from .images import check_image
from .seed_extra import EXTRA_PARTS
from .seed_parts import SEED_PARTS

ALL_SEED_PARTS = SEED_PARTS + EXTRA_PARTS
SEED_VERSION = 2

# v1 shipped a bare "J3" marking on the S8050 that is ambiguous with the S9013 family.
# Fix it only if the row still has the original value (i.e. the user hasn't edited it).
SEED_FIXES = [("S8050", "markings", "J3Y,J3", "J3Y")]

CSV_FIELDS = ["part_number", "category", "description", "package", "specs", "markings",
              "notes", "qty", "location", "datasheet_url"]
CSV_ALIASES = {
    "part": "part_number", "partnumber": "part_number", "part number": "part_number", "mpn": "part_number",
    "name": "part_number", "type": "category", "desc": "description", "footprint": "package",
    "marking": "markings", "markings": "markings", "marks": "markings", "quantity": "qty", "count": "qty",
    "stock": "qty", "owned": "qty", "bin": "location", "storage": "location", "datasheet": "datasheet_url",
    "link": "datasheet_url", "url": "datasheet_url", "comment": "notes", "comments": "notes",
}


def default_db_path() -> Path:
    override = os.environ.get("BENCHBUDDY_DB")
    if override:
        return Path(override)
    return Path.home() / ".benchbuddy" / "parts.db"


@dataclass
class Part:
    id: int
    part_number: str
    category: str
    description: str
    package: str
    specs: str
    markings: str          # comma separated SMD / top markings
    notes: str
    qty: int
    location: str
    user_added: bool
    datasheet_url: str = ""
    photo_path: str = ""


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number   TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'Other',
    description   TEXT NOT NULL DEFAULT '',
    package       TEXT NOT NULL DEFAULT '',
    specs         TEXT NOT NULL DEFAULT '',
    markings      TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    qty           INTEGER NOT NULL DEFAULT 0,
    location      TEXT NOT NULL DEFAULT '',
    user_added    INTEGER NOT NULL DEFAULT 0,
    datasheet_url TEXT NOT NULL DEFAULT '',
    photo_path    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_parts_pn ON parts(part_number);
"""


def _norm(s: str) -> str:
    """Normalise for matching: upper-case, strip spaces/dashes/dots/underscores."""
    return re.sub(r"[\s\-_.]", "", s).upper()


def datasheet_search_url(part_number: str) -> str:
    """A web search for the part's datasheet (always valid, never goes stale)."""
    return "https://www.google.com/search?q=" + quote_plus(f"{part_number} datasheet")


class PartsDB:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        in_memory = str(self.path) == ":memory:"
        if not in_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate_columns()
        self._photos_dir = (Path(tempfile.mkdtemp(prefix="benchbuddy_photos_")) if in_memory
                            else self.path.parent / "photos")
        self._merge_seed()

    # ------------------------------------------------------------ migrations
    def _migrate_columns(self) -> None:
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(parts)")}
        for col in ("datasheet_url", "photo_path"):
            if col not in have:
                self.conn.execute(f"ALTER TABLE parts ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def _merge_seed(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SEED_VERSION:
            return
        existing = {_norm(r[0]) for r in self.conn.execute("SELECT part_number FROM parts")}
        rows = []
        for p in ALL_SEED_PARTS:
            if _norm(p["pn"]) in existing:
                continue
            rows.append((p["pn"], p["cat"], p["desc"], p.get("pkg", ""), p.get("specs", ""),
                         ",".join(p.get("marks", [])), p.get("notes", "")))
            existing.add(_norm(p["pn"]))
        self.conn.executemany(
            "INSERT INTO parts (part_number, category, description, package, specs, markings, notes) "
            "VALUES (?,?,?,?,?,?,?)", rows)
        for pn, column, old, new in SEED_FIXES:
            self.conn.execute(f"UPDATE parts SET {column}=? WHERE part_number=? AND {column}=? AND user_added=0",
                              (new, pn, old))
        self.conn.execute(f"PRAGMA user_version = {SEED_VERSION}")
        self.conn.commit()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _row(r: sqlite3.Row) -> Part:
        return Part(r["id"], r["part_number"], r["category"], r["description"], r["package"],
                    r["specs"], r["markings"], r["notes"], r["qty"], r["location"], bool(r["user_added"]),
                    r["datasheet_url"], r["photo_path"])

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def datasheet_link(part: Part) -> str:
        """A link that is safe to make clickable: the stored URL only if it's plain http(s)."""
        return safe_url(part.datasheet_url) or datasheet_search_url(part.part_number)

    # --------------------------------------------------------------------- CRUD
    def get(self, part_id: int) -> Part | None:
        r = self.conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> list[Part]:
        return [self._row(r) for r in self.conn.execute("SELECT * FROM parts ORDER BY category, part_number")]

    def categories(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT category FROM parts ORDER BY category")]

    def find_by_part_number(self, part_number: str) -> Part | None:
        n = _norm(part_number)
        for p in self.all():
            if _norm(p.part_number) == n:
                return p
        return None

    def add(self, part_number: str, category: str = "Other", description: str = "", package: str = "",
            specs: str = "", markings: str = "", notes: str = "", qty: int = 0, location: str = "",
            datasheet_url: str = "") -> int:
        if not part_number.strip():
            raise ValueError("part number is required")
        cur = self.conn.execute(
            "INSERT INTO parts (part_number, category, description, package, specs, markings, notes, qty, "
            "location, user_added, datasheet_url) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
            (part_number.strip(), category.strip() or "Other", description, package, specs, markings, notes,
             qty, location, datasheet_url.strip()))
        self.conn.commit()
        return int(cur.lastrowid)

    def update(self, part_id: int, **fields) -> None:
        allowed = {"part_number", "category", "description", "package", "specs", "markings", "notes",
                   "qty", "location", "datasheet_url", "photo_path"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown field(s): {', '.join(sorted(bad))}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE parts SET {sets} WHERE id=?", (*fields.values(), part_id))
        self.conn.commit()

    def delete(self, part_id: int) -> None:
        p = self.get(part_id)
        if p and p.photo_path:
            self._delete_photo_file(p.photo_path)
        self.conn.execute("DELETE FROM parts WHERE id=?", (part_id,))
        self.conn.commit()

    # ------------------------------------------------------------------- photos
    @property
    def photos_dir(self) -> Path:
        return self._photos_dir

    def attach_photo(self, part_id: int, source: str | Path) -> str:
        """Copy an image into the library's photo folder and link it to the part."""
        part = self.get(part_id)
        if part is None:
            raise ValueError("no such part")
        src = Path(source)
        ext = check_image(src)          # real type, size and pixel limits (ImageError is a ValueError)
        self._photos_dir.mkdir(parents=True, exist_ok=True)
        dest = self._photos_dir / f"part{part_id}_{uuid.uuid4().hex[:12]}{ext}"
        shutil.copyfile(src, dest)
        if part.photo_path and Path(part.photo_path) != dest:
            self._delete_photo_file(part.photo_path)
        self.update(part_id, photo_path=str(dest))
        return str(dest)

    def remove_photo(self, part_id: int) -> None:
        p = self.get(part_id)
        if p and p.photo_path:
            self._delete_photo_file(p.photo_path)
            self.update(part_id, photo_path="")

    def _delete_photo_file(self, path: str) -> None:
        try:
            f = Path(path)
            if f.is_file() and self._photos_dir in f.parents:      # never delete files outside our folder
                f.unlink()
        except OSError:
            pass

    # ---------------------------------------------------------------------- CSV
    def export_csv(self, path: str | Path, only_owned: bool = False) -> int:
        parts = [p for p in self.all() if p.qty > 0] if only_owned else self.all()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(CSV_FIELDS)
            for p in parts:
                w.writerow([csv_safe(v) for v in (p.part_number, p.category, p.description, p.package, p.specs,
                                                  p.markings, p.notes, p.qty, p.location, p.datasheet_url)])
        return len(parts)

    def import_csv(self, path: str | Path) -> ImportResult:
        """Add new parts and update existing ones (matched by part number).

        Blank cells never erase existing data. Unknown columns are ignored.
        Quantity is *set* to the value in the file when that cell is filled in.
        """
        res = ImportResult()
        with open(path, newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(fh, dialect=dialect)
            if not reader.fieldnames:
                raise ValueError("the file is empty")
            mapping = {}
            for name in reader.fieldnames:
                key = (name or "").strip().lower()
                key = CSV_ALIASES.get(key, key)
                if key in CSV_FIELDS:
                    mapping[name] = key
            if "part_number" not in mapping.values():
                raise ValueError("no part number column found (expected a header like 'part_number' or 'Part')")
            for line_no, row in enumerate(reader, start=2):
                data = {mapping[k]: csv_unsafe((v or "").strip()) for k, v in row.items() if k in mapping}
                pn = data.get("part_number", "")
                if not pn:
                    res.skipped += 1
                    continue
                qty = None
                if data.get("qty"):
                    try:
                        qty = max(int(float(data["qty"])), 0)
                    except ValueError:
                        res.errors.append(f"line {line_no}: quantity '{data['qty']}' is not a number (ignored)")
                existing = self.find_by_part_number(pn)
                if existing:
                    changes = {k: v for k, v in data.items() if k not in ("part_number", "qty") and v}
                    if qty is not None:
                        changes["qty"] = qty
                    if changes:
                        self.update(existing.id, **changes)
                    res.updated += 1
                else:
                    self.add(pn, data.get("category", "") or "Other", data.get("description", ""),
                             data.get("package", ""), data.get("specs", ""), data.get("markings", ""),
                             data.get("notes", ""), qty or 0, data.get("location", ""),
                             data.get("datasheet_url", ""))
                    res.added += 1
        return res

    # ------------------------------------------------------------------- search
    def search(self, query: str, category: str | None = None, in_stock_only: bool = False,
               limit: int = 200) -> list[Part]:
        """Ranked search: exact marking / part number first, then partial and keyword matches."""
        return [p for _, p in self.search_scored(query, category, in_stock_only, limit)]

    def search_scored(self, query: str, category: str | None = None, in_stock_only: bool = False,
                      limit: int = 200, parts: list[Part] | None = None) -> list[tuple[int, Part]]:
        """Like search() but returns (score, part). 100 = exact marking, 95 = exact part number,
        70 = partial marking, 60/55 = partial part number, 30 = keyword match, 0 = no query."""
        q = query.strip()
        parts = list(parts) if parts is not None else self.all()
        if category and category != "All":
            parts = [p for p in parts if p.category == category]
        if in_stock_only:
            parts = [p for p in parts if p.qty > 0]
        if not q:
            return [(0, p) for p in parts[:limit]]

        nq = _norm(q)
        words = [w.lower() for w in re.split(r"\s+", q) if w]
        scored: list[tuple[int, Part]] = []
        for p in parts:
            pn = _norm(p.part_number)
            marks = [_norm(m) for m in p.markings.split(",") if m.strip()]
            haystack = " ".join([p.part_number, p.category, p.description, p.package,
                                 p.specs, p.markings, p.notes, p.location]).lower()
            score = 0
            if nq in marks:
                score = 100
            elif pn == nq:
                score = 95
            elif len(nq) >= 3 and any(nq in m or (len(m) >= 3 and m in nq) for m in marks):
                score = 70
            elif nq and nq in pn:
                score = 60
            elif nq and pn and pn in nq and len(pn) >= 4:
                score = 55
            elif all(w in haystack for w in words):
                score = 30
            if score:
                scored.append((score, p))
        scored.sort(key=lambda t: (-t[0], t[1].category, t[1].part_number))
        return scored[:limit]


# ------------------------------------------------------------------ hardening
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Stop spreadsheet apps treating a cell as a formula (=, +, -, @ ...) by prefixing a quote."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def csv_unsafe(value: str) -> str:
    """Undo csv_safe on import (a quote we added in front of a formula character)."""
    if value.startswith("'") and value[1:2] and value[1] in "=+-@\t\r":
        return value[1:]
    return value


def safe_url(url: str) -> str:
    """The URL if it's an absolute http(s) link, else '' (no file:, javascript:, custom schemes)."""
    u = (url or "").strip()
    try:
        parts = urlparse(u)
    except ValueError:
        return ""
    return u if parts.scheme.lower() in ("http", "https") and parts.netloc else ""
