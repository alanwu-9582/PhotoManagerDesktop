"""把明信片畫出來（對應網頁版 js/tools/palette-card/card.js）。

預覽跟輸出是同一個函式、同一張圖：畫面上看到的就是存下來的那張，只是縮小顯示。
所有尺寸都以畫布寬度為單位寫成比例；面板位置與照片平移都存成相對值，
換尺寸、換版型之後使用者拖出來的構圖才會跟著等比例走。
"""
from __future__ import annotations


import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen)

from ...engine.image import pil_to_qimage, qimage_to_pil
from ...ui import theme
from .palette import contrast, luminance, shade

SIZES = [("1080x1350", "4:5 直式", 1080, 1350), ("1080x1440", "3:4 明信片", 1080, 1440),
         ("1080x1080", "1:1 方形", 1080, 1080), ("1080x1920", "9:16 限時動態", 1080, 1920)]
LAYOUTS = [("glass", "玻璃色卡"), ("caption", "標題留白"), ("strip", "底部色條")]


def clamp(v, lo, hi):
    return min(hi, max(lo, v))


def rounded(rect: QRectF, r) -> QPainterPath:
    path = QPainterPath()
    rr = max(0.0, min(r, rect.width() / 2, rect.height() / 2))
    path.addRoundedRect(rect, rr, rr)
    return path


def qc(rgb, a=1.0):
    c = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    c.setAlphaF(a)
    return c


def _font(size, mono=False, title=False, weight=700):
    f = QFont()
    fams = theme.MONO_FAMILIES + theme.FONT_FAMILIES if (mono or title) else theme.FONT_FAMILIES
    f.setFamilies(fams)
    f.setPixelSize(max(1, round(size)))
    f.setWeight(QFont.Weight(weight))
    return f


def slack_of(img, box: QRectF, zoom=1.0):
    if img is None or not img.width():
        return 0, 0, 0, 0
    scale = max(box.width() / img.width(), box.height() / img.height()) * max(1.0, zoom or 1)
    dw, dh = img.width() * scale, img.height() * scale
    return (dw - box.width()) / 2, (dh - box.height()) / 2, dw, dh


def draw_cover(p, img, box: QRectF, pan, zoom):
    sx, sy, dw, dh = slack_of(img, box, zoom)
    if not dw:
        return
    p.drawImage(QRectF(box.x() - sx + clamp(pan["x"], -1, 1) * sx, box.y() - sy + clamp(pan["y"], -1, 1) * sy, dw, dh), img)


def photo_in(p, img, box, pan, zoom):
    if img is None:
        return
    p.save()
    p.setClipRect(box, Qt.ClipOperation.IntersectClip)
    draw_cover(p, img, box, pan, zoom)
    p.restore()


def tracked(p: QPainter, text, cx, y, tracking):
    """逐字畫，好控制字距；置中不會偏。"""
    chars = list(str(text))
    if not chars:
        return 0
    fm = QFontMetricsF(p.font())
    widths = [fm.horizontalAdvance(ch) for ch in chars]
    total = sum(widths) + tracking * (len(chars) - 1)
    x = cx - total / 2
    for ch, w in zip(chars, widths):
        p.drawText(QPointF(x, y), ch)
        x += w + tracking
    return total


def text_with_shadow(p, draw, color, shadow, blur):
    """canvas 的 shadowBlur 在 QPainter 沒有；用幾層偏移的半透明字近似。"""
    if shadow is not None:
        base = QColor(shadow)
        n = 6
        for i in range(n):
            ang = i / n * 6.283
            dx, dy = math.cos(ang) * blur * 0.35, math.sin(ang) * blur * 0.35 + blur * 0.12
            c = QColor(base)
            c.setAlphaF(base.alphaF() / n * 1.6)
            p.setPen(c)
            draw(dx, dy)
    p.setPen(color)
    draw(0, 0)


def frost(p: QPainter, canvas: QImage, rect: QRectF, radius, sigma, alpha=0.18, rim=0.5):
    """毛玻璃：取一塊已經畫好的畫面、縮小、模糊、再貼回圓角範圍。往外多取一圈，邊緣才連續。"""
    from PIL import ImageFilter
    grab = int(sigma * 1.5 + 0.99)
    x0 = max(0, int(rect.x()) - grab)
    y0 = max(0, int(rect.y()) - grab)
    x1 = min(canvas.width(), int(rect.right() + 0.99) + grab)
    y1 = min(canvas.height(), int(rect.bottom() + 0.99) + grab)
    w, h = x1 - x0, y1 - y0
    if w < 2 or h < 2:
        return
    shrink = clamp(round(sigma / 6), 1, 8)
    sw, sh = max(2, round(w / shrink)), max(2, round(h / shrink))
    part = canvas.copy(x0, y0, w, h).scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
    blurred = pil_to_qimage(qimage_to_pil(part).filter(ImageFilter.GaussianBlur(sigma / shrink)))
    p.save()
    p.setClipPath(rounded(rect, radius))
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(QRectF(x0, y0, w, h), blurred)
    sheen = QLinearGradient(0, rect.y(), 0, rect.bottom())
    sheen.setColorAt(0, QColor(255, 255, 255, int(255 * alpha * 1.6)))
    sheen.setColorAt(0.45, QColor(255, 255, 255, int(255 * alpha * 0.75)))
    sheen.setColorAt(1, QColor(255, 255, 255, int(255 * alpha * 1.1)))
    p.fillRect(rect, sheen)
    p.restore()
    p.setPen(QPen(QColor(255, 255, 255, int(255 * rim)), max(1.0, canvas.width() * 0.0016)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(rounded(rect, radius))


def background_color(colors, mode):
    if not colors:
        return [224, 224, 224]
    if mode == "light":
        return max(colors, key=lambda c: luminance(c["rgb"]))["rgb"]
    if mode == "dark":
        return min(colors, key=lambda c: luminance(c["rgb"]))["rgb"]
    return colors[0]["rgb"]


def readable_on(colors, base):
    best, ratio = None, 0
    for c in colors:
        r = contrast(c["rgb"], base)
        if r > ratio:
            best, ratio = c["rgb"], r
    if ratio >= 4:
        return best
    return [32, 32, 32] if luminance(base) > 0.4 else [240, 240, 240]


# ============================================================ 版面
def frame_of(st):
    _, _, W, H = st["size"]
    bleed = st["background"] == "none"
    pad = 0 if bleed else W * 0.042
    return {"W": W, "H": H, "bleed": bleed, "card": QRectF(pad, pad, W - pad * 2, H - pad * 2),
            "radius": 0 if bleed else W * 0.032}


def columns_for(n):
    if n <= 3:
        return max(1, n)
    if n == 4:
        return 2
    if n <= 6:
        return 3
    return 4


def panel_metrics(card: QRectF, count):
    w = card.width() * 0.888
    pad_x = w * 0.025
    cols = columns_for(count)
    rows = (count + cols - 1) // cols
    cell = (w - pad_x * 2) / cols
    circle = cell * 0.5
    lab = circle * 0.21
    label_gap = circle * 0.31
    row_gap = circle * 0.2
    pad_y = circle * 0.26
    row_h = circle + label_gap + lab
    return {"w": w, "h": pad_y * 2 + rows * row_h + (rows - 1) * row_gap, "padX": pad_x, "padY": pad_y,
            "cols": cols, "rows": rows, "cell": cell, "circle": circle, "label": lab, "labelGap": label_gap,
            "rowGap": row_gap, "rowH": row_h, "x": 0.0, "y": 0.0}


def panel_margin(card):
    return card.width() * 0.045


def place_panel(panel, card: QRectF, wanted):
    m = panel_margin(card)
    cx = card.x() + wanted["x"] * card.width() if wanted else card.center().x()
    cy = card.y() + wanted["y"] * card.height() if wanted else card.bottom() - m - panel["h"] / 2
    panel["x"] = clamp(cx - panel["w"] / 2, card.x(), card.right() - panel["w"])
    panel["y"] = clamp(cy - panel["h"] / 2, card.y(), card.bottom() - panel["h"])
    return panel


def panel_rect(panel):
    return QRectF(panel["x"], panel["y"], panel["w"], panel["h"])


def photo_box(st, card: QRectF):
    if st["layout"] == "caption":
        split = card.y() + card.height() * 0.45
        return QRectF(card.x(), split, card.width(), card.bottom() - split)
    if st["layout"] == "strip":
        return QRectF(card.x(), card.y(), card.width(), card.height() * 0.875)
    return QRectF(card)


def geometry(st):
    fr = frame_of(st)
    panel = None
    if st["layout"] == "glass":
        panel = place_panel(panel_metrics(fr["card"], len(st["colors"] or [])), fr["card"], st["panel"])
    return {**fr, "panel": panel, "photo": photo_box(st, fr["card"])}


def photo_slack(st):
    g = geometry(st)
    sx, sy, _, _ = slack_of(st["image"], g["photo"], st["zoom"])
    return sx, sy


def snap_panel(st, center: QPointF):
    """吸附點：水平置中，以及貼上緣、正中、貼下緣。回傳吸好的相對中心與命中的參考線。"""
    g = geometry(st)
    card, panel = g["card"], g["panel"]
    if not panel:
        return st["panel"], []
    margin = panel_margin(card)
    tol = g["W"] * 0.018
    guides = []

    def stick(v, targets, axis):
        best, gap = v, tol
        for t in targets:
            if abs(v - t) < gap:
                gap, best = abs(v - t), t
        if best != v:
            guides.append((axis, best))
        return best

    cx = stick(center.x(), [card.center().x()], "x")
    cy = stick(center.y(), [card.y() + margin + panel["h"] / 2, card.center().y(),
                            card.bottom() - margin - panel["h"] / 2], "y")
    return {"x": clamp((cx - card.x()) / card.width(), 0, 1), "y": clamp((cy - card.y()) / card.height(), 0, 1)}, guides


# ============================================================ 繪製
def draw_swatch_panel(p, canvas, colors, panel, W):
    frost(p, canvas, panel_rect(panel), W * 0.028, W * 0.026, alpha=0.18)
    left = panel["x"] + panel["padX"]
    top = panel["y"] + panel["padY"]
    p.setFont(_font(panel["label"], mono=True))
    for i, c in enumerate(colors):
        col, rw = i % panel["cols"], i // panel["cols"]
        cx = left + panel["cell"] * (col + 0.5)
        row_top = top + rw * (panel["rowH"] + panel["rowGap"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(c["rgb"]))
        r = panel["circle"] / 2
        p.drawEllipse(QPointF(cx, row_top + r), r, r)
        y = row_top + panel["circle"] + panel["labelGap"] + panel["label"] * 0.8
        text_with_shadow(p, lambda dx, dy, t=c["hex"], x=cx, yy=y: tracked(p, t, x + dx, yy + dy, 0),
                         QColor("white"), QColor(0, 0, 0, 100), panel["label"] * 0.4)


def average_of(canvas: QImage, box: QRectF):
    x, y = max(0, int(box.x())), max(0, int(box.y()))
    w = min(canvas.width() - x, int(box.width() + 0.99))
    h = min(canvas.height() - y, int(box.height() + 0.99))
    if w < 1 or h < 1:
        return [128, 128, 128]
    small = canvas.copy(x, y, w, h).scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
    r = g = b = 0
    for yy in range(8):
        for xx in range(8):
            c = small.pixelColor(xx, yy)
            r, g, b = r + c.red(), g + c.green(), b + c.blue()
    return [r / 64, g / 64, b / 64]


def draw_headline(p, canvas, st, box: QRectF, colors, W, base=None, on_photo=True):
    title = str(st["title"] or "").strip()
    subtitle = str(st["subtitle"] or "").strip()
    if (not title and not subtitle) or box.height() < W * 0.08:
        return
    backdrop = average_of(canvas, box) if on_photo else base
    light = luminance(backdrop) > 0.42
    color = (readable_on(colors, backdrop) if light else [255, 255, 255]) if on_photo else readable_on(colors, base)
    shadow = (QColor(255, 255, 255, 140) if light else QColor(0, 0, 0, 115)) if on_photo else None
    ts, ss = W * 0.034, W * 0.024
    gap = ts * 1.6
    block = (ts if title else 0) + (gap if title and subtitle else 0) + (ss if subtitle else 0)
    cx = box.center().x()
    y = box.y() + (box.height() - block) / 2 + ts * 0.8
    if title:
        p.setFont(_font(ts, title=True))
        text_with_shadow(p, lambda dx, dy, yy=y: tracked(p, title, cx + dx, yy + dy, ts * 0.06), qc(color), shadow, ts * 0.7)
        y += gap
    if subtitle:
        p.setFont(_font(ss, title=True))
        yy = y if title else y - ts * 0.8 + ss * 0.8
        text_with_shadow(p, lambda dx, dy: tracked(p, subtitle, cx + dx, yy + dy, ss * 0.14), qc(color), shadow, ss * 0.7)


def layout_glass(p, canvas, st, g):
    card, radius, W, panel = g["card"], g["radius"], g["W"], g["panel"]
    p.save()
    p.setClipPath(rounded(card, radius))
    draw_cover(p, st["image"], card, st["pan"], st["zoom"])
    p.restore()
    p.end()   # frost 要讀到已經畫好的像素
    p.begin(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_swatch_panel(p, canvas, st["colors"], panel, W)
    above = QRectF(card.x(), card.y(), card.width(), panel["y"] - card.y())
    below_y = panel["y"] + panel["h"]
    below = QRectF(card.x(), below_y, card.width(), card.bottom() - below_y)
    p.end()
    p.begin(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_headline(p, canvas, st, above if above.height() >= below.height() else below, st["colors"], W)


def layout_caption(p, canvas, st, g):
    card, radius, W, base, photo = g["card"], g["radius"], g["W"], g["base"], g["photo"]
    p.save()
    p.setClipPath(rounded(card, radius))
    p.fillRect(card, qc(base))
    photo_in(p, st["image"], photo, st["pan"], st["zoom"])
    p.restore()
    draw_headline(p, canvas, st, QRectF(card.x(), card.y(), card.width(), photo.y() - card.y()),
                  st["colors"], W, base=base, on_photo=False)


def layout_strip(p, canvas, st, g):
    card, radius, W, photo = g["card"], g["radius"], g["W"], g["photo"]
    colors = st["colors"]
    p.save()
    p.setClipPath(rounded(card, radius))
    photo_in(p, st["image"], photo, st["pan"], st["zoom"])
    band_y = photo.bottom()
    band_h = card.bottom() - band_y
    cell = card.width() / max(1, len(colors))
    p.setFont(_font(W * 0.018, mono=True))
    for i, c in enumerate(colors):
        x = card.x() + cell * i
        p.fillRect(QRectF(x, band_y, cell + 1, band_h), qc(c["rgb"]))
        p.setPen(QColor(0, 0, 0, 184) if luminance(c["rgb"]) > 0.45 else QColor(255, 255, 255, 235))
        tracked(p, c["hex"], x + cell / 2, band_y + band_h / 2 + W * 0.007, 0)
    p.restore()
    p.end()
    p.begin(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_headline(p, canvas, st, QRectF(card.x(), card.y(), card.width(), photo.height()), colors, W)


LAYOUT_FN = {"glass": layout_glass, "caption": layout_caption, "strip": layout_strip}


def render_card(st) -> QImage:
    g = geometry(st)
    W, H, card, radius = g["W"], g["H"], g["card"], g["radius"]
    canvas = QImage(W, H, QImage.Format.Format_RGB32)
    colors = st["colors"] or []
    base = background_color(colors, st["background"])
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if g["bleed"]:
        p.fillRect(0, 0, W, H, qc(base))
    else:
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0, qc(shade(base, 0.07)))
        grad.setColorAt(1, qc(shade(base, -0.1)))
        p.fillRect(0, 0, W, H, grad)
        # 卡片陰影：幾層往外擴、越來越淡
        sh = shade(base, -0.65)
        for i in range(8, 0, -1):
            grow = W * 0.03 * i / 8
            p.fillPath(rounded(card.adjusted(-grow, -grow + W * 0.008, grow, grow + W * 0.008), radius + grow),
                       qc(sh, 0.3 / 8 * (1 - i / 9) * 1.8))
        p.fillPath(rounded(card, radius), qc(shade(base, -0.06 if luminance(base) > 0.5 else 0.08)))
    LAYOUT_FN.get(st["layout"], layout_glass)(p, canvas, {**st, "colors": colors}, {**g, "base": base})
    if p.isActive():
        p.end()
    return canvas
