"""相框的版面計算與繪製（對應網頁版 js/tools/exif-frame/frame.js）。

版面只認「照片要畫多寬」這一個尺寸，其他（邊框、資訊列、字級）全部按比例算出來，
所以同一份設定用在預覽（1400px）與匯出（原尺寸）會長得一模一樣。
"""
from __future__ import annotations


from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from ...ui import theme

MODES = [("bottom", "下框"), ("top", "上框"), ("both", "上下框"), ("full", "全框"), ("polaroid", "拍立得")]
MODE_PAD = {"bottom": 0, "top": 0, "both": 0, "full": 0.03, "polaroid": 0.025}

DEFAULTS = {
    "mode": "bottom", "pad": 0.0, "barScale": 1.0, "showBar": True, "align": "split",
    "bg": "#ffffff", "textColor": "#121212", "subColor": "#8a8a8a", "accent": "#121212",
    "fontScale": 1.0, "font": "sans", "radius": 0.0, "photoRadius": 0.0, "shadow": False,
    "separator": True, "title": "", "model": "", "date": "", "brand": "", "params": "",
}


def clamp(v, lo, hi):
    return min(hi, max(lo, v))


def layout(w, h, o):
    base = min(w, h)
    pad = round(base * clamp(o["pad"], 0, 0.15))
    bar = round(base * 0.13 * clamp(o["barScale"], 0.4, 2.4)) if o["showBar"] else 0
    top = bottom = pad
    side = pad
    mode = o["mode"]
    if mode == "top":
        top = pad + bar
    elif mode == "both":
        top = pad + round(bar * 0.62)
        bottom = pad + bar
    elif mode == "polaroid":
        bottom = pad + round(bar * 1.9)
    else:
        bottom = pad + bar
    W, H = w + side * 2, h + top + bottom
    info = QRectF(0, 0, W, top) if mode == "top" else QRectF(0, top + h, W, bottom)
    title = QRectF(0, 0, W, top) if mode == "both" else None
    return {"W": W, "H": H, "photo": QRectF(side, top, w, h), "info": info, "title": title,
            "pad": pad, "bar": bar, "unit": bar or round(base * 0.1)}


def _font(o, size, weight=400) -> QFont:
    f = QFont()
    f.setFamilies(theme.MONO_FAMILIES if o["font"] == "mono" else theme.FONT_FAMILIES)
    f.setPixelSize(max(1, round(size)))
    f.setWeight(QFont.Weight(weight))
    return f


def _text(p: QPainter, text, x, y, align="left", baseline="top"):
    """跟 canvas 一樣以 textAlign / textBaseline 對齊。"""
    fm = QFontMetricsF(p.font())
    w = fm.horizontalAdvance(text)
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    if baseline == "top":
        y += fm.ascent()
    elif baseline == "middle":
        y += (fm.ascent() - fm.descent()) / 2
    p.drawText(QPointF(x, y), text)
    return w


def _left_block(p, o, x, cy, unit):
    big = unit * 0.30 * o["fontScale"]
    small = unit * 0.205 * o["fontScale"]
    gap = big * 0.34
    has_sub = bool(o["date"])
    total = big + gap + small if has_sub else big
    y = cy - total / 2
    if o["model"]:
        p.setFont(_font(o, big, 700))
        p.setPen(QColor(o["textColor"]))
        _text(p, o["model"], x, y)
    y += big + gap
    if has_sub:
        p.setFont(_font(o, small, 400))
        p.setPen(QColor(o["subColor"]))
        _text(p, o["date"], x, y)


def _right_block(p, o, right, cy, unit, logo):
    brand_size = unit * 0.30 * o["fontScale"]
    param_size = unit * 0.26 * o["fontScale"]
    gap = unit * 0.16
    x = right
    if o["params"]:
        p.setFont(_font(o, param_size, 700))
        p.setPen(QColor(o["textColor"]))
        w = _text(p, o["params"], x, cy, "right", "middle")
        x -= w + gap
    has_brand = logo is not None or o["brand"]
    if has_brand and o["separator"] and o["params"]:
        half = unit * 0.26
        col = QColor(o["subColor"])
        col.setAlphaF(0.55)
        p.setPen(QPen(col, max(1.0, unit * 0.015)))
        p.drawLine(QPointF(x, cy - half), QPointF(x, cy + half))
        x -= gap
    if logo is not None:
        lh = unit * 0.42 * o["fontScale"]
        lw = lh * logo.width() / logo.height()
        p.drawImage(QRectF(x - lw, cy - lh / 2, lw, lh), logo)
    elif o["brand"]:
        p.setFont(_font(o, brand_size, 700))
        p.setPen(QColor(o["accent"]))
        _text(p, o["brand"], x, cy, "right", "middle")


def _centered(p, o, rect: QRectF, unit, logo):
    big = unit * 0.28 * o["fontScale"]
    small = unit * 0.21 * o["fontScale"]
    cx = rect.x() + rect.width() / 2
    line1 = " ".join(v for v in (o["brand"], o["model"]) if v)
    line2 = "　".join(v for v in (o["params"], o["date"]) if v)
    gap = big * 0.42
    total = (big if line1 else 0) + (gap if line1 and line2 else 0) + (small if line2 else 0)
    y = rect.y() + rect.height() / 2 - total / 2
    if logo is not None and line1:
        lh = big * 1.25
        lw = lh * logo.width() / logo.height()
        p.drawImage(QRectF(cx - lw / 2, y - lh * 0.1, lw, lh), logo)
        y += lh + gap
    elif line1:
        p.setFont(_font(o, big, 700))
        p.setPen(QColor(o["textColor"]))
        _text(p, line1, cx, y, "center")
        y += big + gap
    if line2:
        p.setFont(_font(o, small, 400))
        p.setPen(QColor(o["subColor"]))
        _text(p, line2, cx, y, "center")


def rounded(rect: QRectF, r):
    path = QPainterPath()
    if r <= 0:
        path.addRect(rect)
    else:
        rr = min(r, rect.width() / 2, rect.height() / 2)
        path.addRoundedRect(rect, rr, rr)
    return path


def render_frame(img: QImage, opts: dict, target_width, logo: QImage | None = None) -> QImage:
    o = {**DEFAULTS, **opts}
    w = max(1, round(target_width))
    h = max(1, round(img.height() / img.width() * w))
    L = layout(w, h, o)
    out = QImage(int(L["W"]), int(L["H"]), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    outer = rounded(QRectF(0, 0, L["W"], L["H"]), round(min(L["W"], L["H"]) * clamp(o["radius"], 0, 0.08)))
    p.fillPath(outer, QColor(o["bg"]))

    ph = L["photo"]
    photo_path = rounded(ph, round(min(ph.width(), ph.height()) * clamp(o["photoRadius"], 0, 0.08)))
    if o["shadow"] and L["pad"] > 0:
        # 柔和陰影：往下偏一點、疊幾層半透明的外擴。
        blur = L["pad"] * 0.7
        dy = L["pad"] * 0.16
        steps = 10
        for i in range(steps, 0, -1):
            grow = blur * i / steps
            a = 0.28 / steps * 1.6 * (1 - i / (steps + 1))
            sp = rounded(ph.adjusted(-grow, -grow + dy, grow, grow + dy),
                         min(ph.width(), ph.height()) * clamp(o["photoRadius"], 0, 0.08) + grow)
            p.fillPath(sp, QColor(0, 0, 0, int(255 * a)))
    p.save()
    p.setClipPath(photo_path)
    p.drawImage(ph, img)
    p.restore()

    if o["showBar"]:
        unit = L["unit"]
        inset = max(L["pad"], unit * 0.42)
        info = L["info"]
        if o["align"] == "center" or o["mode"] == "polaroid":
            _centered(p, o, info, unit, logo)
        else:
            cy = info.y() + info.height() / 2 + (0 if o["mode"] == "top" else L["pad"] * 0.1)
            _left_block(p, o, info.x() + inset, cy, unit)
            _right_block(p, o, info.x() + info.width() - inset, cy, unit, logo)
        if L["title"] is not None and o["title"]:
            t = L["title"]
            p.setFont(_font(o, unit * 0.26 * o["fontScale"], 600))
            p.setPen(QColor(o["textColor"]))
            _text(p, o["title"], t.x() + t.width() / 2, t.y() + t.height() / 2 + L["pad"] * 0.2, "center", "middle")
    p.end()
    return out
