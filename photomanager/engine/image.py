"""圖片解碼共用層。

JPEG / TIFF / PNG 走 Qt 的 QImageReader：
  - setAutoTransform 讓 EXIF 方向直接在解碼時轉正
  - setScaledSize 對 JPEG 會用 libjpeg 的 DCT 縮放（1/2、1/4、1/8），
    兩千萬畫素的原圖縮成縮圖不必先整張解出來，快上好幾倍

HEIC / HEIF 走 pillow-heif（本機解碼，原檔不會被改寫）。Qt 解不開的格式也一律退到 Pillow。
這些函式都可以在背景執行緒呼叫：QImage 本身不綁 GUI 執行緒（QPixmap 才綁）。
"""
from __future__ import annotations


import os
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader

try:
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 400_000_000
except ImportError:  # pragma: no cover
    Image = ImageOps = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAS_HEIF = True
except Exception:  # pragma: no cover - 沒裝 pillow-heif 就只能看 EXIF
    HAS_HEIF = False

LIBRARY_RE = re.compile(r"\.(jpe?g|tiff?|heic|heif)$", re.I)
IMAGE_RE = re.compile(r"\.(jpe?g|png|gif|webp|bmp|tiff?|avif|heic|heif)$", re.I)
HEIC_RE = re.compile(r"\.(heic|heif|avif)$", re.I)

QImageReader.setAllocationLimit(2048)  # MB；預設 256MB 對一億畫素的 TIFF 不夠


def is_library_file(name: str) -> bool:
    return bool(LIBRARY_RE.search(name))


def is_image_file(name: str) -> bool:
    return bool(IMAGE_RE.search(name))


def is_heic(name: str) -> bool:
    return bool(HEIC_RE.search(name))


class DecodeError(Exception):
    pass


def _fit(w: int, h: int, max_edge: int | None) -> tuple[int, int]:
    if not max_edge or max(w, h) <= max_edge:
        return w, h
    k = max_edge / max(w, h)
    return max(1, round(w * k)), max(1, round(h * k))


def pil_to_qimage(im) -> QImage:
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    fmt = QImage.Format.Format_RGBA8888 if im.mode == "RGBA" else QImage.Format.Format_RGB888
    data = im.tobytes()
    bpl = im.width * (4 if im.mode == "RGBA" else 3)
    return QImage(data, im.width, im.height, bpl, fmt).copy()


def qimage_to_pil(img: QImage):
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(ptr), "raw", "RGBA",
                            img.bytesPerLine(), 1)


def _decode_pillow(path: str, max_edge: int | None) -> QImage:
    if Image is None:
        raise DecodeError("缺少 Pillow")
    try:
        with Image.open(path) as im:
            if max_edge and im.format == "JPEG":
                im.draft("RGB", _fit(im.width, im.height, max_edge * 2))
            im = ImageOps.exif_transpose(im)
            if max_edge:
                im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS if max_edge <= 800 else Image.Resampling.BILINEAR)
            return pil_to_qimage(im)
    except Exception as e:  # noqa: BLE001
        if is_heic(path) and not HAS_HEIF:
            raise DecodeError("這張 HEIC 解不開（缺少 pillow-heif），只能看 EXIF") from e
        raise DecodeError(f'解不開這張照片: {e}') from e


def decode(path: str, max_edge: int | None = None) -> QImage:
    """解碼並轉正。max_edge 給了就縮到長邊不超過它。"""
    if not is_heic(path):
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max_edge:
            # 方向轉 90° 時寬高會互換，但長邊不變，所以縮放倍率照樣成立。
            w, h = _fit(size.width(), size.height(), max_edge)
            if (w, h) != (size.width(), size.height()):
                reader.setScaledSize(QSize(w, h))
                reader.setQuality(85)
        img = reader.read()
        if not img.isNull():
            return img
    return _decode_pillow(path, max_edge)


def image_size(path: str) -> tuple[int, int] | None:
    """不解碼就量尺寸（已套用方向）。"""
    if not is_heic(path):
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        s = reader.size()
        if s.isValid():
            w, h = s.width(), s.height()
            t = reader.transformation()
            if int(getattr(t, "value", t)) & 0x4:  # QImageIOHandler.TransformationRotate90
                w, h = h, w
            return w, h
    try:
        with Image.open(path) as im:
            w, h = im.size
            o = im.getexif().get(0x0112, 1)
            return (h, w) if o in (5, 6, 7, 8) else (w, h)
    except Exception:  # noqa: BLE001
        return None


def save_image(img: QImage, path: str, quality: int = 92) -> None:
    ext = os.path.splitext(path)[1].lower()
    fmt = "PNG" if ext == ".png" else "JPG"
    if fmt == "JPG" and img.hasAlphaChannel():
        flat = QImage(img.size(), QImage.Format.Format_RGB32)
        flat.fill(Qt.GlobalColor.white)
        from PySide6.QtGui import QPainter
        p = QPainter(flat)
        p.drawImage(0, 0, img)
        p.end()
        img = flat
    if not img.save(path, fmt, quality if fmt == "JPG" else -1):
        raise DecodeError("輸出失敗")
