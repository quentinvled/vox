"""Genere assets/Vox.ico a partir de l'icone dessinee en code.

A relancer si le dessin de l'icone change : `uv run python tools/make_icon.py`
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vox.ui.widgets import make_app_icon  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(icon: QIcon, size: int) -> bytes:
    pixmap = icon.pixmap(size, size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_ico(icon: QIcon, sizes=SIZES) -> bytes:
    """Assemble un .ico contenant des PNG (format accepte depuis Vista)."""
    images = [(size, png_bytes(icon, size)) for size in sizes]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,      # largeur (0 = 256)
            0 if size >= 256 else size,      # hauteur
            0,                               # nombre de couleurs
            0,                               # reserve
            1,                               # plans
            32,                              # bits par pixel
            len(data),
            offset,
        )
        payload += data
        offset += len(data)

    return bytes(header + entries + payload)


def main() -> int:
    app = QApplication(sys.argv)
    icon = make_app_icon()
    out = ROOT / "assets" / "Vox.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_ico(icon))
    print(f"{out} ecrit ({out.stat().st_size} octets, {len(SIZES)} tailles)")

    # relecture de controle
    check = QIcon(str(out))
    print("tailles relues :", sorted({s.width() for s in check.availableSizes()}))
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
