"""照片參數：疊在編輯照片左下角的專業圖表（跟整理分類的「照片資訊」同一種膠囊樣式）。

  直方圖  R / G / B 三色版疊在一起 + 亮度線，兩邊的三角形亮起來代表有死白 / 死黑
  波形    每一直條是照片的那一欄，越上面越亮（影視調色常用，看得出哪個區域過曝）
  顏色    向量示波器：離中心越遠越飽和、角度是色相；斜線是膚色線。下面是代表色
  曝光    平均 / 中間調亮度、動態範圍、溢出比例、色偏，加上拍攝參數

圖表都在長邊 360 的縮圖上算，換一次預覽只要幾毫秒。
"""
from __future__ import annotations


import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import config
from . import icons, theme

TABS = [("hist", "直方圖"), ("wave", "波形"), ("color", "顏色"), ("expo", "曝光")]
EDGE = 360
W_OPEN, CHART_H = 316, 150


def _rgb_small(img) -> np.ndarray:
    """QImage 或 H×W×3 uint8 → 長邊 EDGE 的 uint8 RGB。"""
    if isinstance(img, QImage):
        k = min(1.0, EDGE / max(1, img.width(), img.height()))
        q = img.scaled(max(1, round(img.width() * k)), max(1, round(img.height() * k)),
                       Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        q = q.convertToFormat(QImage.Format.Format_RGB888)
        a = np.frombuffer(q.constBits(), np.uint8, count=q.sizeInBytes()).reshape(q.height(), q.bytesPerLine())
        return a[:, :q.width() * 3].reshape(q.height(), q.width(), 3).copy()
    h, w = img.shape[:2]
    step = max(1, math.ceil(max(h, w) / EDGE))
    return np.ascontiguousarray(img[::step, ::step, :3])


def _gray_image(counts: np.ndarray, tint=(255, 255, 255)) -> QImage:
    """把計數（h×w）變成半透明的白點圖：log 壓一下，少的地方也看得到。"""
    v = np.log1p(counts.astype(np.float32))
    v = v / max(1e-6, float(v.max()))
    a = np.clip(v ** 0.7 * 255, 0, 255).astype(np.uint8)
    h, w = a.shape
    out = np.zeros((h, w, 4), np.uint8)
    for i, c in enumerate(tint[::-1]):                 # BGRA（ARGB32 在小端序機器上）
        out[..., i] = (a.astype(np.uint16) * c // 255).astype(np.uint8)
    out[..., 3] = a
    return QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def analyse(img) -> dict:
    a = _rgb_small(img)
    f = a.astype(np.float32) / 255
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    L = 0.2126 * r + 0.7152 * g + 0.0722 * b
    out = {}
    hists = []
    for ch in (a[..., 0], a[..., 1], a[..., 2]):
        hists.append(np.bincount(ch.reshape(-1), minlength=256).astype(np.float32))
    hl = np.bincount(np.clip(L * 255 + 0.5, 0, 255).astype(np.uint8).reshape(-1), minlength=256).astype(np.float32)
    # 兩端的 0 和 255 常常爆量，把高度上限設在「其他地方的最高點」，圖才不會被壓扁
    top = max(1.0, max(float(hh[2:254].max()) for hh in hists + [hl]))
    out["hist"] = [np.clip(hh / top, 0, 1) for hh in hists] + [np.clip(hl / top, 0, 1)]
    n = L.size
    out["clip_hi"] = float((a.max(axis=2) >= 254).sum()) / n
    out["clip_lo"] = float((a.max(axis=2) <= 2).sum()) / n
    # 波形：x = 欄、y = 亮度
    cols = min(a.shape[1], 200)
    xi = (np.arange(a.shape[1]) * cols // a.shape[1])[None, :].repeat(a.shape[0], 0)
    yi = np.clip((1 - L) * 79, 0, 79).astype(np.int32)
    wave = np.zeros((80, cols), np.float32)
    np.add.at(wave, (yi.reshape(-1), xi.reshape(-1)), 1)
    out["wave"] = _gray_image(wave, (220, 255, 220))
    # 向量示波器（BT.601 的 Cb / Cr）
    cb = -0.1687 * r - 0.3313 * g + 0.5 * b
    cr = 0.5 * r - 0.4187 * g - 0.0813 * b
    N = 121
    ui = np.clip((cb / 0.8 + 0.5) * (N - 1), 0, N - 1).astype(np.int32)
    vi = np.clip((0.5 - cr / 0.8) * (N - 1), 0, N - 1).astype(np.int32)
    vec = np.zeros((N, N), np.float32)
    np.add.at(vec, (vi.reshape(-1), ui.reshape(-1)), 1)
    out["vector"] = _gray_image(vec)
    # 代表色：粗略量化成 4096 色，挑最多的、而且彼此差得夠遠的五個
    q = (a // 16).astype(np.int32)
    keys = (q[..., 0] * 256 + q[..., 1] * 16 + q[..., 2]).reshape(-1)
    counts = np.bincount(keys, minlength=4096).astype(np.float32)
    # 只看數量的話，暗部和灰色會把位子全佔掉；鮮豔一點的顏色加權，才挑得出「這張照片的顏色」
    qr, qg, qb = np.arange(4096) // 256, np.arange(4096) // 16 % 16, np.arange(4096) % 16
    qmax = np.maximum(np.maximum(qr, qg), qb)
    chroma = (qmax - np.minimum(np.minimum(qr, qg), qb)) / 15
    score = counts * (0.25 + chroma * 1.5) * (0.4 + 0.6 * (qmax / 15))
    picks = []
    for k in np.argsort(score)[::-1][:300]:
        if counts[k] == 0:
            break
        c = np.array([(k // 256) * 16 + 8, (k // 16 % 16) * 16 + 8, (k % 16) * 16 + 8], np.float32)
        if all(np.abs(c - p).sum() > 90 for p in picks):
            picks.append(c)
        if len(picks) == 5:
            break
    out["swatches"] = [QColor(int(c[0]), int(c[1]), int(c[2])) for c in picks]
    # 曝光數字
    lin = np.power(np.clip(L, 0, 1), 2.2)
    lo, hi = np.percentile(lin, [1, 99])
    out["mean"] = float(L.mean())
    out["median"] = float(np.median(L))
    out["dr"] = float(math.log2(max(hi, 1e-4) / max(lo, 1e-4)))
    rb = float((r - b).mean())
    gm = float((g - (r + b) / 2).mean())
    cast = []
    if abs(rb) > 0.03:
        cast.append("偏暖" if rb > 0 else "偏冷")
    if abs(gm) > 0.025:
        cast.append("偏綠" if gm > 0 else "偏洋紅")
    out["cast"] = "、".join(cast) or "中性"
    return out


class ScopesOverlay(QWidget):
    """收起來是一顆膠囊；展開後上面是分頁，下面是圖表。開關與分頁會記住。"""
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        saved = config.settings.get("scopes") or {}
        self.open = bool(saved.get("open", False))
        self.tab = saved.get("tab", "hist") if saved.get("tab") in dict(TABS) else "hist"
        self.data: dict | None = None
        self.exif: list[tuple[str, str]] = []
        self.label_text = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resize()

    def set_image(self, img, exif_lines=None, label_text=""):
        self.data = analyse(img) if img is not None else None
        if exif_lines is not None:
            self.exif = exif_lines
        self.label_text = label_text
        self.update()

    def _save(self):
        config.settings.set("scopes", {"open": self.open, "tab": self.tab})

    def _resize(self):
        if not self.open:
            self.setFixedSize(104, 26)
        else:
            self.setFixedSize(W_OPEN, 26 + 28 + CHART_H + 12)
        if self.parent():
            self.parent().place_overlays()

    def _tab_rects(self):
        f = theme.font("caption", 600)
        fm = QFontMetrics(f)
        x = 10.0
        out = []
        for key, lb in TABS:
            w = fm.horizontalAdvance(lb) + 16
            out.append((key, QRectF(x, 28, w, 22)))
            x += w + 4
        return out

    def mousePressEvent(self, e):
        pos = e.position()
        if self.open and pos.y() > 26:
            for key, r in self._tab_rects():
                if r.contains(pos):
                    self.tab = key
                    self._save()
                    self.update()
                    return
            return
        self.open = not self.open
        self._save()
        self._resize()
        self.toggled.emit(self.open)
        self.update()

    # ---------------------------------------------------------------- 畫
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = theme.round_rect(QPainterPath(), r, 10 if self.open else 7)
        p.fillPath(path, QColor(28, 28, 30, 225))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)
        p.setPen(QColor(255, 255, 255, 230))
        p.setFont(theme.font("caption", 600))
        chev = icons.pixmap("chevron-down" if self.open else "chevron-right", "#ffffff", 11)
        p.drawPixmap(QRectF(10, 7.5, 11, 11), chev, QRectF(chev.rect()))
        p.drawText(QRectF(26, 0, 90, 26), Qt.AlignmentFlag.AlignVCenter, "照片參數")
        if not self.open:
            return
        if self.label_text:
            p.setPen(QColor(255, 214, 10))
            p.drawText(QRectF(0, 0, self.width() - 12, 26), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       self.label_text)
        for key, tr_ in self._tab_rects():
            sel = key == self.tab
            if sel:
                p.fillPath(theme.round_rect(QPainterPath(), tr_, 6), QColor(255, 255, 255, 46))
            p.setPen(QColor(255, 255, 255, 235 if sel else 150))
            p.drawText(tr_, Qt.AlignmentFlag.AlignCenter, dict(TABS)[key])
        chart = QRectF(10, 56, self.width() - 20, CHART_H)
        if not self.data:
            p.setPen(QColor(255, 255, 255, 120))
            p.drawText(chart, Qt.AlignmentFlag.AlignCenter, "—")
            return
        getattr(self, f"_paint_{self.tab}")(p, chart)

    def _frame(self, p, r):
        p.fillPath(theme.round_rect(QPainterPath(), r, 5), QColor(0, 0, 0, 90))

    def _paint_hist(self, p, r):
        self._frame(p, r)
        d = self.data
        plot = r.adjusted(4, 6, -4, -16)
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for hh, col in zip(d["hist"][:3], (QColor(230, 60, 60, 105), QColor(60, 200, 80, 105), QColor(70, 120, 240, 105))):
            path = QPainterPath(QPointF(plot.left(), plot.bottom()))
            for i in range(256):
                path.lineTo(plot.left() + i / 255 * plot.width(), plot.bottom() - float(hh[i]) * plot.height())
            path.lineTo(plot.right(), plot.bottom())
            path.closeSubpath()
            p.fillPath(path, col)
        p.restore()
        line = QPainterPath(QPointF(plot.left(), plot.bottom() - float(d["hist"][3][0]) * plot.height()))
        for i in range(1, 256):
            line.lineTo(plot.left() + i / 255 * plot.width(), plot.bottom() - float(d["hist"][3][i]) * plot.height())
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(line)
        # 四等分的參考線
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        for k in (1, 2, 3):
            x = plot.left() + plot.width() * k / 4
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        # 死黑 / 死白的三角形
        for x, frac, left in ((plot.left() + 2, d["clip_lo"], True), (plot.right() - 2, d["clip_hi"], False)):
            on = frac > 0.002
            tri = QPainterPath()
            y = plot.top() + 2
            if left:
                tri.moveTo(x, y)
                tri.lineTo(x + 9, y)
                tri.lineTo(x, y + 9)
            else:
                tri.moveTo(x, y)
                tri.lineTo(x - 9, y)
                tri.lineTo(x, y + 9)
            tri.closeSubpath()
            p.fillPath(tri, QColor(255, 255, 255, 230) if on else QColor(255, 255, 255, 50))
        p.setFont(theme.font("caption"))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(r.left() + 6, r.bottom() - 15, r.width() - 12, 14), Qt.AlignmentFlag.AlignLeft,
                   "死黑 {0:.1f}%".format(d["clip_lo"] * 100))
        p.drawText(QRectF(r.left() + 6, r.bottom() - 15, r.width() - 12, 14), Qt.AlignmentFlag.AlignRight,
                   "死白 {0:.1f}%".format(d["clip_hi"] * 100))

    def _paint_wave(self, p, r):
        self._frame(p, r)
        plot = r.adjusted(26, 6, -6, -6)
        p.setFont(theme.font("caption"))
        for v in (0, 25, 50, 75, 100):
            y = plot.bottom() - v / 100 * plot.height()
            p.setPen(QPen(QColor(255, 255, 255, 40 if v not in (0, 100) else 70), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(255, 255, 255, 120))
            p.drawText(QRectF(r.left() + 2, y - 7, 22, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(v))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(plot, self.data["wave"])

    def _paint_color(self, p, r):
        self._frame(p, r)
        size = r.height() - 12
        sq = QRectF(r.left() + 8, r.top() + 6, size, size)
        c = sq.center()
        rad = size / 2
        # 色相環上的六個標準色位置（R Mg B Cy G Yl）
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, rad, rad)
        p.drawEllipse(c, rad * 0.5, rad * 0.5)
        for (rr, gg, bb), name in (((1, 0, 0), "R"), ((1, 0, 1), "Mg"), ((0, 0, 1), "B"), ((0, 1, 1), "Cy"),
                                    ((0, 1, 0), "G"), ((1, 1, 0), "Yl")):
            # 75% 飽和度的標準色（跟影視用的向量示波器一樣）；色度 ±0.4 對到圓的半徑
            cb = (-0.1687 * rr - 0.3313 * gg + 0.5 * bb) * 0.75
            cr = (0.5 * rr - 0.4187 * gg - 0.0813 * bb) * 0.75
            pt = QPointF(c.x() + cb / 0.4 * rad, c.y() - cr / 0.4 * rad)
            p.setPen(QPen(QColor(int(rr * 255), int(gg * 255), int(bb * 255), 200), 1.2))
            p.drawRect(QRectF(pt.x() - 3, pt.y() - 3, 6, 6))
        # 膚色線（約 123°）
        ang = math.radians(123)
        p.setPen(QPen(QColor(255, 200, 150, 110), 1, Qt.PenStyle.DashLine))
        p.drawLine(c, QPointF(c.x() + math.cos(ang) * rad, c.y() - math.sin(ang) * rad))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), self.data["vector"])
        # 代表色 + 色偏
        x0 = sq.right() + 14
        p.setFont(theme.font("caption"))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(x0, r.top() + 8, r.right() - x0 - 6, 16), Qt.AlignmentFlag.AlignLeft, "代表色")
        sw = self.data["swatches"]
        y = r.top() + 28
        for col in sw:
            box = QRectF(x0, y, 18, 18)
            p.fillPath(theme.round_rect(QPainterPath(), box, 4), col)
            p.setPen(QColor(255, 255, 255, 190))
            p.setFont(theme.font("caption", mono=True))
            p.drawText(QRectF(x0 + 24, y, 80, 18), Qt.AlignmentFlag.AlignVCenter, col.name().upper())
            y += 22
        p.setFont(theme.font("caption"))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(x0, r.bottom() - 18, r.right() - x0 - 6, 16), Qt.AlignmentFlag.AlignLeft,
                   "色偏：{0}".format(self.data["cast"]))

    def _paint_expo(self, p, r):
        self._frame(p, r)
        d = self.data
        rows = [("平均亮度", f"{d['mean'] * 100:.0f}%"), ("中間調", f"{d['median'] * 100:.0f}%"),
                ("動態範圍", f"{d['dr']:.1f} EV"),
                ("死白 / 死黑", f"{d['clip_hi'] * 100:.1f}% / {d['clip_lo'] * 100:.1f}%")]
        rows += self.exif
        f = theme.font("caption")
        fb = theme.font("caption", 600)
        fm = QFontMetrics(f)
        line = fm.height()
        col_w = (r.width() - 16) / 2
        step = line * 2 + 5
        per_col = max(1, int((r.height() - 8) // step))
        for i, (k, v) in enumerate(rows[:per_col * 2]):
            x = r.left() + 8 + (i % 2) * col_w
            y = r.top() + 6 + (i // 2) * step
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 130))
            p.drawText(QRectF(x, y, col_w - 6, line), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(k, Qt.TextElideMode.ElideRight, int(col_w - 8)))
            p.setFont(fb)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(QRectF(x, y + line, col_w - 6, line), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(fb).elidedText(str(v), Qt.TextElideMode.ElideRight, int(col_w - 8)))
