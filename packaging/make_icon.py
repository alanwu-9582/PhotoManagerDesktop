"""把 photomanager/assets/app-icon.svg 轉成 Windows 用的 PhotoManager.ico（多尺寸）。"""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(ROOT / "photomanager/assets/app-icon.svg")).render(p)
    p.end()
    buf = QBuffer(ba := QByteArray())
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    out = ROOT / "packaging/PhotoManager.ico"
    imgs = [render(s) for s in SIZES]
    imgs[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    print("icon:", out)
