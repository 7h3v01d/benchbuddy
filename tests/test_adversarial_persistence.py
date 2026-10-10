"""Adversarial tests for imported data: CSV, photos, links, and rendering stored text
(reviewer M3, M4, M5 and property 12)."""

import os
import re
import struct
import zlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from benchbuddy.core import images  # noqa: E402
from benchbuddy.core.partsdb import PartsDB, csv_safe, csv_unsafe, safe_url  # noqa: E402

HOSTILE = "<b>bold</b><a href='file:///C:/Windows/System32/calc.exe'>click</a><img src=x>"


@pytest.fixture()
def db():
    d = PartsDB(":memory:")
    yield d
    d.close()


# ---------------------------------------------------------------- CSV formulas (M4)
@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://evil\",\"x\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"])
def test_formula_cells_are_neutralised_and_restored(value):
    assert csv_safe(value).startswith("'")
    assert csv_unsafe(csv_safe(value)) == value


@pytest.mark.parametrize("value", ["BC547", "4k7", "'quoted already", "", "10µF"])
def test_ordinary_cells_are_untouched(value):
    assert csv_unsafe(csv_safe(value)) == value
    assert csv_safe(value) == value


def test_export_import_round_trip_with_hostile_values(db, tmp_path):
    pid = db.add(part_number="=EVIL()", category="Test", description="-5V rail " + HOSTILE,
                 notes="@cmd", location="+shelf")
    f = tmp_path / "parts.csv"
    db.export_csv(f)
    raw = f.read_text(encoding="utf-8-sig")
    assert "'=EVIL()" in raw and ",'@cmd" in raw and ",'+shelf" in raw    # inert in a spreadsheet
    db2 = PartsDB(":memory:")
    db2.import_csv(f)
    back = next(p for p in db2.all() if p.part_number == "=EVIL()")
    orig = db.get(pid)
    assert (back.description, back.notes, back.location) == (orig.description, orig.notes, orig.location)
    db2.close()


# ------------------------------------------------------------------ links (M3)
@pytest.mark.parametrize("url,ok", [
    ("https://www.ti.com/lit/ds/symlink/ina226.pdf", True), ("http://example.com/x.pdf", True),
    ("file:///C:/Windows/System32/calc.exe", False), ("javascript:alert(1)", False),
    ("ms-settings:privacy", False), ("steam://run/123", False), ("//no-scheme.example", False),
    ("https:///nohost", False), ("", False), ("   ", False),
])
def test_only_web_links_are_kept(url, ok):
    assert safe_url(url) == (url.strip() if ok else "")


def test_datasheet_link_falls_back_to_search_for_bad_schemes(db):
    pid = db.add(part_number="X1", category="Test", datasheet_url="file:///etc/passwd")
    link = PartsDB.datasheet_link(db.get(pid))
    assert link.startswith("https://") and "passwd" not in link


# ----------------------------------------------------------------- photos (M5)
def _png(w: int, h: int) -> bytes:
    """Minimal PNG whose header claims w×h (pixel data is a 1×1 stub)."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + \
        chunk(b"IEND", b"")


def test_real_photo_is_accepted_and_stored_with_its_true_type(db, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    src = tmp_path / "chip.jpg"                                   # PNG content, misleading name
    Image.new("RGB", (64, 48), "white").save(src, format="PNG")
    pid = db.add(part_number="P1", category="Test")
    stored = db.attach_photo(pid, src)
    assert stored.endswith(".png")


@pytest.mark.parametrize("name,content,why", [
    ("fake.jpg", b"MZ\x90\x00 this is an exe, not a photo", "isn't a JPEG"),
    ("empty.png", b"", "empty"),
    ("notes.png", b"hello world", "isn't a JPEG"),
])
def test_non_images_are_refused(db, tmp_path, name, content, why):
    f = tmp_path / name
    f.write_bytes(content)
    pid = db.add(part_number="P2", category="Test")
    with pytest.raises(ValueError, match=why):
        db.attach_photo(pid, f)
    assert db.get(pid).photo_path == ""


def test_oversized_file_is_refused_before_reading(db, tmp_path):
    f = tmp_path / "huge.jpg"
    with open(f, "wb") as fh:
        fh.write(b"\xff\xd8\xff")
        fh.truncate(images.MAX_FILE_BYTES + 1)                     # sparse: cheap to create
    pid = db.add(part_number="P3", category="Test")
    with pytest.raises(ValueError, match="MB"):
        db.attach_photo(pid, f)


def test_decompression_bomb_header_is_refused(db, tmp_path):
    pytest.importorskip("PIL")
    f = tmp_path / "bomb.png"
    f.write_bytes(_png(50_000, 50_000))                           # claims 2.5 gigapixels
    pid = db.add(part_number="P4", category="Test")
    with pytest.raises(ValueError, match="MP"):
        db.attach_photo(pid, f)


def test_truncated_image_is_refused(db, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    good = tmp_path / "ok.png"
    Image.new("RGB", (64, 64), "red").save(good)
    bad = tmp_path / "cut.png"
    bad.write_bytes(good.read_bytes()[:40])
    pid = db.add(part_number="P5", category="Test")
    with pytest.raises(ValueError, match="damaged"):
        db.attach_photo(pid, bad)


# --------------------------------------------- stored text renders as text (property 12)
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_parts_detail_shows_markup_as_text(qapp, db):
    from benchbuddy.gui.parts_tab import PartsTab
    db.add(part_number="ZZ" + HOSTILE, category="Test", description=HOSTILE, notes=HOSTILE,
           specs=HOSTILE, datasheet_url="file:///C:/Windows/System32/calc.exe")
    tab = PartsTab(db)
    tab.search.setText("ZZ")
    qapp.processEvents()
    tab.table.selectRow(0)
    tab.show_detail()
    text = tab.detail.toPlainText()
    assert "<b>bold</b>" in text and "<img src=x>" in text           # literally visible
    hrefs = re.findall(r'href="([^"]*)"', tab.detail.toHtml())
    assert hrefs and all(h.startswith("https://") for h in hrefs), hrefs   # the only live link is the web search


def test_result_view_refuses_non_web_links(qapp, monkeypatch):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    from benchbuddy.gui import widgets
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    for bad in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ms-settings:privacy", "steam://run/1"):
        assert widgets.open_web_link(QUrl(bad)) is False
    assert widgets.open_web_link(QUrl("https://www.ti.com/")) is True
    assert opened == ["https://www.ti.com/"]
    view = widgets.ResultView()
    assert not view.openExternalLinks() and not view.openLinks()


def test_power_names_render_as_text(qapp):
    from benchbuddy.core import power
    from benchbuddy.gui.power_tab import PowerTab
    tab = PowerTab()
    tab.project = power.Project([power.Rail("<b>USB</b>", 5.0, "supply", max_ma=500)],
                                [power.Load("<i>x</i>", "<b>USB</b>", 1, 600, 600)])
    tab.refresh_all()
    assert "<b>USB</b>" in tab.details.toPlainText()
