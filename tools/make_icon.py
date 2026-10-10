"""Render gui/icons/app.svg into a multi-size Windows .ico (needs PyQt6 + Pillow).

    python tools/make_icon.py
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

ICONS = Path(__file__).resolve().parents[1] / "benchbuddy" / "gui" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # QIcon needs an app alive
    icon = QIcon(str(ICONS / "app.svg"))
    assert app is not None
    big = icon.pixmap(256, 256).toImage()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    big.save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data())))
    img.save(ICONS / "app.ico", sizes=[(s, s) for s in SIZES])
    img.save(ICONS / "app.png")
    print(f"wrote {ICONS / 'app.ico'} and app.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
