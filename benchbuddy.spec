# PyInstaller spec for BenchBuddy: one-folder, windowed build.
#   pyinstaller benchbuddy.spec --noconfirm
# Output: dist/BenchBuddy/BenchBuddy(.exe)

from pathlib import Path

ROOT = Path(SPECPATH)
GUI = ROOT / "benchbuddy" / "gui"

datas = [(str(GUI / "fonts"), "benchbuddy/gui/fonts"),
         (str(GUI / "icons"), "benchbuddy/gui/icons")]

a = Analysis(
    [str(ROOT / "benchbuddy" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["PyQt6.QtSvg", "serial.tools.list_ports",     # SVG icons; serial port scan
                   "pytesseract", "PIL.Image"],                 # photo OCR (Tesseract itself stays external)
    excludes=["tkinter", "pytest", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
              "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtMultimedia", "PyQt6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BenchBuddy",
    icon=str(GUI / "icons" / "app.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="BenchBuddy", upx=False)
