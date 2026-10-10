import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from benchbuddy.core.partsdb import PartsDB  # noqa: E402
from benchbuddy.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(app, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(PartsDB(":memory:"), settings)
    w.show()
    yield w
    w.close()


def test_all_tabs_build(win):
    assert win.tabs.count() == 9
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        win.tabs.currentWidget().grab()


def test_power_tab_example_and_edits(win):
    t = win.power_tab
    assert t.result_table.rowCount() == 2
    assert "warn" in t.banner.text().lower() or "check" in t.banner.text().lower() or "problem" in t.banner.text().lower()
    # edit a load: 10 servos must overload the 3V3 rail
    t.load_rail_combo.setCurrentText("3V3")
    t.load_combo.setCurrentText("SG90 micro servo")
    t.qty_spin.setValue(10)
    t.add_load()
    assert t.project.rails[1].name == "3V3"
    assert "Problems" in t.banner.text()
    # remove it again
    t.load_table.selectRow(len(t.project.loads) - 1)
    t.remove_load()
    assert "Problems" not in t.banner.text()


def test_power_tab_rename_cascade(win):
    t = win.power_tab
    item = t.rail_table.item(1, 0)
    item.setText("RAIL_X")
    assert all(l.rail == "RAIL_X" for l in t.project.loads)
    assert t.project.rails[1].name == "RAIL_X"


def test_parts_search_and_add(win):
    p = win.parts_tab
    p.search.setText("J3Y")
    assert p.results and p.results[0].part_number == "S8050"
    p.search.setText("104")
    assert not p.decode.isHidden()
    p.search.setText("")
    p.bump(1)
    assert p.results[0].qty == 1 or any(r.qty for r in p.db.all())


def test_sim_tab_runs_and_imports(win):
    s = win.sim_tab
    s.run()
    assert s.result is not None and not s.result.brownout
    # break it: tiny output cap -> brown-out, then let the tool fix it
    s.f_cout.setText("1u")
    s.f_cin.setText("0")
    s.run()
    assert s.result.brownout
    s.suggest()
    assert not s.result.brownout
    # import the example power-budget rail
    s.refresh_rails()
    s.rail_combo.setCurrentText("3V3")
    s.import_rail()
    assert s.params.reg_kind == "ldo" and s.params.v_set == 3.3
    s.plot.grab()


def test_sim_tab_bad_input_does_not_crash(win):
    s = win.sim_tab
    s.f_period.setText("1u")      # shorter than the burst
    s.run()
    s.f_cout.setText("abc")
    s.run()
    assert "number" in s.verdict.toPlainText().lower() or "fill" in s.verdict.toPlainText().lower()


def test_calcs_tab_updates(win):
    from benchbuddy.gui.calcs_tab import CalcsTab
    c = [w for w in (win.tabs.widget(i) for i in range(win.tabs.count())) if isinstance(w, CalcsTab)][0]
    assert "×" in c.adc_out.toPlainText()
    c.i2_cb.setText("5000")
    assert "too high" in c.i2_out.toPlainText().lower()


def _chip_image(tmp_path, text="BC547", size=60):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (size * len(text) + 80, 180), (30, 30, 34))
    ImageDraw.Draw(im).text((40, 40), text, fill=(235, 235, 235), font=ImageFont.load_default(size=size))
    f = tmp_path / f"chip_{text}.png"
    im.save(f)
    return str(f)


def test_parts_tab_csv_and_photo_roundtrip(win, tmp_path):
    p = win.parts_tab
    p.search.setText("")
    p.table.selectRow(0)
    part = p.selected_part()
    img = _chip_image(tmp_path, "NE555P")
    p.do_attach(part.id, img)
    assert p.db.get(part.id).photo_path
    assert "<img" in p.detail.toHtml() or "img" in p.detail.toHtml().lower()
    assert "datasheet" in p.detail.toPlainText().lower()
    out = tmp_path / "parts.csv"
    n = p.do_export(str(out), only_owned=False)
    assert n == len(p.db.all()) > 200
    res = p.do_import(str(out))
    assert res.updated == n and res.added == 0
    p.remove_photo()
    assert p.db.get(part.id).photo_path == ""


@pytest.mark.skipif(not __import__("benchbuddy.core.ocr", fromlist=["x"]).ocr_available()[0],
                    reason="Tesseract not installed")
def test_photo_identify_dialog_reads_and_matches(win, tmp_path, app):
    from benchbuddy.gui.parts_tab import PhotoIdentifyDialog
    dlg = PhotoIdentifyDialog(win.parts_tab.db, _chip_image(tmp_path, "AMS1117-3.3"), win)
    dlg.worker.wait(60000)
    for _ in range(50):
        app.processEvents()
        if dlg.matches:
            break
    assert "AMS1117-3.3" in dlg.tokens
    assert dlg.matches[0].part_number == "AMS1117-3.3"
    assert dlg.use_btn.isEnabled() and dlg.new_btn.isEnabled()
    dlg.match_list.setCurrentRow(0)
    dlg._use_match()
    win.parts_tab.handle_photo_choice(dlg, dlg.image_path)
    assert win.parts_tab.results[0].part_number == "AMS1117-3.3"
    dlg.close()


def test_photo_dialog_without_ocr_shows_hint(win, tmp_path, monkeypatch):
    from benchbuddy.core import ocr as ocr_mod
    from benchbuddy.gui import parts_tab as pt
    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: (False, "The Tesseract program was not found on your PATH."))
    dlg = pt.PhotoIdentifyDialog(win.parts_tab.db, _chip_image(tmp_path), win)
    assert dlg.worker is None
    assert "tesseract" in dlg.status.text().lower() and "install" in dlg.status.text().lower()
    dlg.close()


def test_power_tree_diagram_tracks_project(win):
    t = win.power_tab
    keys = t.tree_view.node_keys()
    assert "rail:USB 5V" in keys and "rail:3V3" in keys
    assert sum(k.startswith("load:") for k in keys) == len(t.project.loads)
    t.tree_view.grab()
    # clicking a rail selects it in the rail table
    t.tree_view.railClicked.emit("3V3")
    assert t.rail_table.item(t.rail_table.currentRow(), 0).text() == "3V3"
    # pop-out window stays in sync
    t.open_tree_window()
    pop = t._tree_views()[1]
    assert pop.node_keys() == keys
    t.on_new()
    assert pop.node_keys() == [] and t.tree_view.node_keys() == []


def test_pinout_tab_filters_and_details(win):
    p = win.pinout_tab
    p.board_combo.setCurrentText("ESP32 DevKit (WROOM-32)")
    p.req_boxes["adc_wifi"].setChecked(True)
    lit = {p.board.pins[i].gpio for i in p.grid.lit}
    assert lit == {32, 33, 34, 35, 36, 39}
    p.req_boxes["out"].setChecked(True)
    assert {p.board.pins[i].gpio for i in p.grid.lit} == {32, 33}
    p.select_label("GPIO12")
    html = p.detail.toPlainText()
    assert "Strapping" in html and "1.8 V" in html
    # boards without the capability hide that checkbox
    p.board_combo.setCurrentText("Arduino Uno / Nano (ATmega328P)")
    assert p.req_boxes["adc_wifi"].isHidden() and not p.req_boxes["adc_wifi"].isChecked()
    p.grab()


def test_window_state_persists(app, tmp_path):
    ini = str(tmp_path / "s.ini")
    w = MainWindow(PartsDB(":memory:"), QSettings(ini, QSettings.Format.IniFormat))
    w.tabs.setCurrentIndex(6)
    w.pinout_tab.board_combo.setCurrentText("ESP32-C3 DevKit / Super Mini")
    w.close()
    w2 = MainWindow(PartsDB(":memory:"), QSettings(ini, QSettings.Format.IniFormat))
    assert w2.tabs.currentIndex() == 6
    assert w2.pinout_tab.board_combo.currentText() == "ESP32-C3 DevKit / Super Mini"
    w2.close()
