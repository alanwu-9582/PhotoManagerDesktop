"""旋轉、翻轉與裁切的幾何（對應網頁版 js/tools/photo-edit/transform.js）。

流程只有兩步：先把照片轉正成一張「工作畫布」（旋轉後的外接矩形，四角會留白），
再從工作畫布上切一塊出來。預覽與匯出用同一套算式，差別只有工作畫布畫多大，
所以畫面上看到的框框，匯出後就是那一塊。
"""
from __future__ import annotations

from ...i18n import tr

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

ASPECTS = [
    ("free", tr("自由")), ("source", tr("原圖比例")), ("1", "1:1"), ("1.25", "5:4"), ("1.3333333", "4:3"),
    ("1.5", "3:2"), ("1.6180339", tr("黃金比例 1.618:1")), ("1.7777778", "16:9"), ("2", "2:1"),
]


def rotated_size(w, h, deg):
    c, s = abs(math.cos(math.radians(deg))), abs(math.sin(math.radians(deg)))
    return w * c + h * s, w * s + h * c


def largest_inner_rect(w, h, deg):
    """旋轉後仍然完全落在照片內、面積最大的那塊矩形（跟照片同方向，不含留白）。"""
    angle = abs(math.radians(deg)) % math.pi
    a = math.pi - angle if angle > math.pi / 2 else angle
    sin, cos = math.sin(a), math.cos(a)
    wide = w >= h
    long_side, short_side = (w, h) if wide else (h, w)
    if short_side <= 2 * sin * cos * long_side or abs(sin - cos) < 1e-10:
        x = 0.5 * short_side
        return (x / sin, x / cos) if wide else (x / cos, x / sin)
    cos2a = cos * cos - sin * sin
    return (w * cos - h * sin) / cos2a, (h * cos - w * sin) / cos2a


def render_work(img: QImage, o: dict, long_edge: float) -> QImage:
    ow, oh = rotated_size(img.width(), img.height(), o["rotate"])
    scale = long_edge / max(ow, oh)
    W, H = max(1, round(ow * scale)), max(1, round(oh * scale))
    out = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent if o["transparent"] else QColor(o["bg"]))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(W / 2, H / 2)
    p.rotate(o["rotate"])
    p.scale(-1 if o["flipH"] else 1, -1 if o["flipV"] else 1)
    dw, dh = img.width() * scale, img.height() * scale
    p.drawImage(QRectF(-dw / 2, -dh / 2, dw, dh), img)
    p.end()
    return out


def crop_image(work: QImage, crop: dict) -> QImage:
    sx, sy = round(crop["x"] * work.width()), round(crop["y"] * work.height())
    sw = max(1, round(crop["w"] * work.width()))
    sh = max(1, round(crop["h"] * work.height()))
    return work.copy(sx, sy, min(sw, work.width() - sx), min(sh, work.height() - sy))


def fit_crop(cw, ch, aspect, limit=None):
    """置中、在畫布內最大、且符合指定比例的裁切框（0~1）。"""
    box = limit or {"x": 0, "y": 0, "w": 1, "h": 1}
    max_w, max_h = box["w"] * cw, box["h"] * ch
    w, h = max_w, max_h
    if aspect:
        if max_w / max_h > aspect:
            w = max_h * aspect
        else:
            h = max_w / aspect
    return {"x": box["x"] + (max_w - w) / 2 / cw, "y": box["y"] + (max_h - h) / 2 / ch, "w": w / cw, "h": h / ch}
