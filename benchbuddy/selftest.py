"""Built-in smoke test: `BenchBuddy.exe --self-test report.txt` (or `python -m benchbuddy --self-test`).

Checks the things that break in a frozen build but not in a source checkout - bundled font,
SVG image plugin, PDF writer, pyserial, the OCR Python side - and opens every tab once.
It uses a throwaway parts database and settings file, so your own data is never touched.
Exit code 0 = all checks passed; the report lists each check.
"""

from __future__ import annotations

import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run(report_path: str | None = None) -> int:
    from PyQt6.QtCore import QSettings, QT_VERSION_STR
    from PyQt6.QtGui import QFontDatabase, QIcon
    from PyQt6.QtWidgets import QApplication

    from . import __version__
    from .gui.theme import ICON_DIR, apply_theme

    lines: list[str] = []
    failures = 0
    started = time.monotonic()

    def log(text: str) -> None:
        lines.append(text)

    def check(name: str, fn) -> None:
        nonlocal failures
        try:
            detail = fn()
            log(f"PASS  {name}" + (f"  ({detail})" if detail else ""))
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none escapes
            failures += 1
            log(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            log("      " + traceback.format_exc().strip().replace("\n", "\n      "))

    app = QApplication.instance() or QApplication(sys.argv[:1])
    apply_theme(app)
    tmp = Path(tempfile.mkdtemp(prefix="benchbuddy-selftest-"))
    log(f"BenchBuddy {__version__} self-test")
    log(f"Python {platform.python_version()} · Qt {QT_VERSION_STR} · {platform.platform()} · "
        f"frozen={bool(getattr(sys, 'frozen', False))}")

    def font():
        fams = QFontDatabase.families()
        assert "JetBrains Mono NL" in fams, "bundled JetBrains Mono NL not registered"
        return app.font().family()

    def icons():
        for name in ("app.svg", "arrow-down-muted.svg", "check.svg"):
            path = ICON_DIR / name
            assert path.is_file(), f"missing {path}"
            pm = QIcon(str(path)).pixmap(16, 16)
            assert not pm.isNull(), f"{name} didn't render: the SVG image plugin is missing"
        return "SVG plugin OK"

    def engines():
        from .core import brownout, power
        from .core.examples import example_project
        rep = power.analyse(example_project())
        assert rep.status in ("ok", "warn", "error") and rep.rails
        res = brownout.simulate(brownout.SimParams())
        assert res.v_out_min > 0
        return f"budget {rep.status}, sim min {res.v_out_min:.2f} V"

    def meter():
        from .core import meter as mt
        sim = mt.SimulatedMeter()
        parser = mt.FrameParser()
        sim.write(b"START\n")
        sim.advance(0.2)
        cap = mt.Capture()
        for f in parser.feed(sim.read(1 << 20)):
            if f.ftype == mt.T_SAMPLES:
                cap.add_frame(*mt.decode_samples(f.payload))
        s = mt.capture_stats(cap)
        assert s.n > 100
        return f"{s.n} demo samples"

    def serial_ports():
        from serial.tools import list_ports
        return f"{len(list(list_ports.comports()))} port(s) visible"

    def ocr_side():
        import importlib
        for mod in ("pytesseract", "PIL.Image"):          # bundled? (hidden imports in the spec)
            importlib.import_module(mod)

        from .core import ocr
        ok, why = ocr.ocr_available()
        return "Tesseract found" if ok else f"Python side OK; {why}"

    win_holder = {}

    def gui():
        from .core.partsdb import PartsDB
        from .gui.main_window import MainWindow
        settings = QSettings(str(tmp / "settings.ini"), QSettings.Format.IniFormat)
        win = MainWindow(PartsDB(str(tmp / "parts.db")), settings)
        win_holder["win"] = win
        names = []
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            app.processEvents()
            img = win.tabs.currentWidget().grab()
            assert not img.isNull()
            names.append(win.tabs.tabText(i).replace("&&", "&"))
        assert len(win.parts_tab.db.all()) > 200, "parts library didn't seed"
        return f"{len(names)} tabs"

    def reports():
        win = win_holder.get("win")
        assert win is not None, "GUI check didn't run"
        html, pdf = tmp / "r.html", tmp / "r.pdf"
        win.power_tab.export_report(str(html))
        win.power_tab.export_report(str(pdf))
        assert "POWER BUDGET REPORT" in html.read_text(encoding="utf-8")
        assert pdf.read_bytes()[:5] == b"%PDF-"
        return f"PDF {pdf.stat().st_size // 1024} KB"

    for name, fn in (("bundled font", font), ("icons / SVG plugin", icons), ("engines", engines),
                     ("meter protocol", meter), ("serial port listing", serial_ports),
                     ("photo OCR (Python side)", ocr_side), ("all tabs open", gui),
                     ("HTML + PDF report export", reports)):
        check(name, fn)

    win = win_holder.get("win")
    if win is not None:
        win.measure_tab.shutdown()
        win.db.close()
        win.deleteLater()
    log(f"{'ALL PASSED' if not failures else f'{failures} FAILED'} in {time.monotonic() - started:.1f} s")
    text = "\n".join(lines) + "\n"
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    if sys.stdout is not None:            # a windowed (frozen) build has no console
        try:
            sys.stdout.write(text)
        except (OSError, ValueError):
            pass
    return 0 if not failures else 1
