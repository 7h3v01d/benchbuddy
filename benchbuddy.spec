# PyInstaller spec for BenchBuddy: one-folder, windowed build.
#   pyinstaller benchbuddy.spec --noconfirm
# Output: dist/BenchBuddy/BenchBuddy(.exe)

import re
from pathlib import Path

ROOT = Path(SPECPATH)
GUI = ROOT / "benchbuddy" / "gui"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "benchbuddy" / "__init__.py").read_text()).group(1)


def _version_resource() -> str:
    """Windows 'Details' tab metadata for BenchBuddy.exe (ignored on other platforms)."""
    nums = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Leon Priest'),
      StringStruct('FileDescription', 'BenchBuddy electronics bench companion'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'BenchBuddy'),
      StringStruct('LegalCopyright', 'Copyright 2026 Leon Priest. Apache License 2.0'),
      StringStruct('OriginalFilename', 'BenchBuddy.exe'),
      StringStruct('ProductName', 'BenchBuddy'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    out = Path(workpath) / "version_info.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return str(out)

datas = [(str(GUI / "fonts"), "benchbuddy/gui/fonts"),
         (str(GUI / "icons"), "benchbuddy/gui/icons")]

a = Analysis(
    [str(ROOT / "benchbuddy" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["PyQt6.QtSvg", "serial.tools.list_ports",     # SVG icons; serial port scan
                   "pytesseract", "PIL.Image"],                 # photo OCR (Tesseract itself stays external)
    excludes=["tkinter", "pytest", "hypothesis",
              # optional imports of pytesseract / Pillow that BenchBuddy never uses (~150 MB):
              "numpy", "scipy", "pandas", "matplotlib", "IPython", "jedi", "sympy", "cv2",
              "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
              "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtMultimedia", "PyQt6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="BenchBuddy",
    icon=str(GUI / "icons" / "app.ico"),
    version=_version_resource(),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="BenchBuddy", upx=False)
