"""統計數據：這批照片的拍攝參數分佈（對應網頁版 js/pages/stats.js）。

圖表挑過型別：
  直方圖   焦段、ISO —— 值本身有順序，看的是分佈的形狀
  甜甜圈   光圈、白平衡、創意風格、格式 —— 看的是誰佔多少比例
  橫條     相機、鏡頭、快門 —— 名稱很長，橫著排才讀得完
全部用 QPainter 畫，滑過去會顯示那一格的數字。
"""
from __future__ import annotations


import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QSizePolicy, QWidget

from ..engine.exif import WHITE_BALANCE_STD, fmt_shutter
from ..engine.library import library
from ..ui import theme, tooltip
from ..ui.dialogs import scroll
from ..ui.widgets import EmptyState, button, hbox, label, vbox
from .base import Page

SLICE_KEYS = ["accent", "blue", "green", "orange", "red", "purple", "gray"]


def bucket(values, fmt):
    counts: dict[str, int] = {}
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        k = fmt(v)
        counts[k] = counts.get(k, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


class Chart(QWidget):
    def __init__(self, entries):
        super().__init__()
        self.entries = entries
        self.setMouseTracking(True)
        self._hover = -1

    def _hit(self, pos) -> int:
        return -1

    def mouseMoveEvent(self, e):
        i = self._hit(e.position())
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            lb, n = self._label(i)
            tooltip.show_text(e.globalPosition().toPoint(), f"{lb}：{n:,}", self, (id(self), i))
        else:
            tooltip.hide()

    def _label(self, i):
        return self.entries[i]

    def leaveEvent(self, _):
        self._hover = -1
        self.update()


class Columns(Chart):
    """直方圖：刻度太密就跳著標，免得字疊在一起。"""

    def sizeHint(self):
        return QSize(300, 170)

    def _geo(self):
        n = len(self.entries)
        W, H = self.width(), self.height()
        slot = W / max(1, n)
        return n, W, H - 24, slot

    def _hit(self, pos):
        n, W, base, slot = self._geo()
        if pos.y() > base or not n:
            return -1
        i = int(pos.x() // slot)
        return i if 0 <= i < n else -1

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n, W, base, slot = self._geo()
        mx = max(c for _, c in self.entries)
        gap = slot * 0.22
        step = math.ceil(n / 8)
        p.setPen(QPen(theme.c("separator"), 1))
        p.drawLine(QPointF(0, base + 0.5), QPointF(W, base + 0.5))
        f = theme.font("caption")
        p.setFont(f)
        for i, (lb, cnt) in enumerate(self.entries):
            h = max(2.0, cnt / mx * (base - 8))
            r = QRectF(i * slot + gap / 2, base - h, slot - gap, h)
            path = QPainterPath()
            theme.round_rect(path, r, 3)
            col = theme.c("accent")
            if self._hover >= 0 and i != self._hover:
                col.setAlphaF(0.45)
            p.fillPath(path, col)
            if i % step == 0:
                p.setPen(theme.c("secondary"))
                p.drawText(QRectF(i * slot - 20, base + 4, slot + 40, 18), Qt.AlignmentFlag.AlignHCenter, lb)
        p.end()


class Donut(Chart):
    """甜甜圈：超過 6 種就把尾巴併成「其他」，不然圖會碎掉。"""

    def __init__(self, entries, max_slices=6):
        head = entries[:max_slices]
        rest = sum(c for _, c in entries[max_slices:])
        super().__init__(head + ([("其他", rest)] if rest else []))
        self.total = sum(c for _, c in self.entries)

    def sizeHint(self):
        return QSize(300, max(150, 22 * len(self.entries) + 20))

    def _ring(self):
        size = min(140, self.height() - 10)
        return QRectF(4, (self.height() - size) / 2, size, size)

    def _hit(self, pos):
        r = self._ring()
        c = r.center()
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        d = math.hypot(dx, dy)
        if d > r.width() / 2 or d < r.width() / 2 * 0.62:
            # 圖例那一列也算
            lx = r.right() + 18
            if pos.x() >= lx:
                top = (self.height() - 22 * len(self.entries)) / 2
                i = int((pos.y() - top) // 22)
                return i if 0 <= i < len(self.entries) else -1
            return -1
        ang = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
        at = 0.0
        for i, (_, cnt) in enumerate(self.entries):
            at += cnt / self.total * 360
            if ang <= at:
                return i
        return -1

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._ring()
        at = 90.0
        inner = r.adjusted(r.width() * 0.19, r.width() * 0.19, -r.width() * 0.19, -r.width() * 0.19)
        for i, (lb, cnt) in enumerate(self.entries):
            sweep = cnt / self.total * 360
            path = QPainterPath()
            path.arcMoveTo(r, at)
            path.arcTo(r, at, -sweep)
            path.arcTo(inner, at - sweep, sweep)
            path.closeSubpath()
            col = theme.c(SLICE_KEYS[i % len(SLICE_KEYS)])
            if self._hover >= 0 and i != self._hover:
                col.setAlphaF(0.4)
            p.fillPath(path, col)
            at -= sweep
        p.setPen(theme.c("label"))
        p.setFont(theme.font("title3", 700))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{self.total:,}")

        f = theme.font("callout")
        fm = QFontMetrics(f)
        p.setFont(f)
        lx = r.right() + 18
        top = (self.height() - 22 * len(self.entries)) / 2
        for i, (lb, cnt) in enumerate(self.entries):
            y = top + i * 22
            dot = QRectF(lx, y + 6, 10, 10)
            dp = QPainterPath()
            theme.round_rect(dp, dot, 3)
            p.fillPath(dp, theme.c(SLICE_KEYS[i % len(SLICE_KEYS)]))
            pct = f"{round(cnt / self.total * 100)}%"
            pw = fm.horizontalAdvance(pct)
            p.setPen(theme.c("label"))
            p.drawText(QRectF(lx + 18, y, self.width() - lx - 26 - pw, 22), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(lb, Qt.TextElideMode.ElideRight, int(self.width() - lx - 30 - pw)))
            p.setPen(theme.c("secondary"))
            p.drawText(QRectF(lx, y, self.width() - lx - 4, 22),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, pct)
        p.end()


class Bars(Chart):
    ROW = 26

    def __init__(self, entries, max_bars=10):
        super().__init__(entries[:max_bars])

    def sizeHint(self):
        return QSize(300, self.ROW * len(self.entries) + 4)

    def _hit(self, pos):
        i = int(pos.y() // self.ROW)
        return i if 0 <= i < len(self.entries) else -1

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mx = max(c for _, c in self.entries)
        f = theme.font("callout")
        fm = QFontMetrics(f)
        p.setFont(f)
        label_w = min(self.width() * 0.42, 190)
        count_w = 44
        track_w = self.width() - label_w - count_w - 16
        for i, (lb, cnt) in enumerate(self.entries):
            y = i * self.ROW
            p.setPen(theme.c("label"))
            p.drawText(QRectF(0, y, label_w, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(lb, Qt.TextElideMode.ElideRight, int(label_w - 6)))
            track = QRectF(label_w + 8, y + 9, track_w, 8)
            tp = QPainterPath()
            theme.round_rect(tp, track, 2)
            p.fillPath(tp, theme.c("fill"))
            fill = QRectF(track.x(), track.y(), max(4, track_w * cnt / mx), 8)
            fp = QPainterPath()
            theme.round_rect(fp, fill, 2)
            col = theme.c("accent")
            if self._hover >= 0 and i != self._hover:
                col.setAlphaF(0.45)
            p.fillPath(fp, col)
            p.setPen(theme.c("secondary"))
            p.drawText(QRectF(self.width() - count_w, y, count_w, self.ROW),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{cnt:,}")
        p.end()


# ============================================================ 二維圖表
def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


class Heatmap(Chart):
    """熱力圖：兩個維度交叉的張數。顏色用平方根壓一下，少數很多張的格子才不會把其他格子都壓成空白。"""
    LEFT, BOTTOM, TOP = 58, 22, 4

    def __init__(self, xs, ys, matrix, unit="張", x_every=1):
        super().__init__([])
        self.xs, self.ys, self.m = xs, ys, matrix
        self.unit = unit
        self.x_every = x_every
        self.max = max((v for row in matrix for v in row), default=0) or 1

    def sizeHint(self):
        return QSize(300, self.TOP + len(self.ys) * 22 + self.BOTTOM + 18)

    def _geo(self):
        w = (self.width() - self.LEFT - 4) / max(1, len(self.xs))
        h = (self.height() - self.TOP - self.BOTTOM - 18) / max(1, len(self.ys))
        return w, h

    def _hit(self, pos):
        cw, ch = self._geo()
        c = int((pos.x() - self.LEFT) // cw)
        r = int((pos.y() - self.TOP) // ch)
        if 0 <= r < len(self.ys) and 0 <= c < len(self.xs) and pos.x() >= self.LEFT:
            return r * len(self.xs) + c
        return -1

    def _label(self, i):
        r, c = divmod(i, len(self.xs))
        return f"{self.ys[r]} · {self.xs[c]}", self.m[r][c]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cw, ch = self._geo()
        lo = theme.c("fill")
        hi = theme.c("accent")
        f = theme.font("caption")
        p.setFont(f)
        fm = QFontMetrics(f)
        for r, name in enumerate(self.ys):
            y = self.TOP + r * ch
            p.setPen(theme.c("secondary"))
            p.drawText(QRectF(0, y, self.LEFT - 8, ch), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fm.elidedText(str(name), Qt.TextElideMode.ElideRight, self.LEFT - 10))
            for c, v in enumerate(self.m[r]):
                rect = QRectF(self.LEFT + c * cw + 1, y + 1, max(1.0, cw - 2), max(1.0, ch - 2))
                col = _mix(lo, hi, math.sqrt(v / self.max)) if v else QColor(lo)
                if self._hover >= 0 and self._hover != r * len(self.xs) + c:
                    col.setAlphaF(col.alphaF() * 0.75)
                p.fillPath(theme.round_rect(QPainterPath(), rect, 3), col)
        p.setPen(theme.c("secondary"))
        base = self.TOP + len(self.ys) * ch
        for c, name in enumerate(self.xs):
            if c % self.x_every:
                continue
            p.drawText(QRectF(self.LEFT + c * cw - 20, base + 2, cw + 40, 18), Qt.AlignmentFlag.AlignHCenter,
                       str(name))
        # 圖例：少 → 多
        lw = 120
        lr = QRectF(self.width() - lw - 34, self.height() - 12, lw, 8)
        grad = QLinearGradient(lr.left(), 0, lr.right(), 0)
        grad.setColorAt(0, lo)
        grad.setColorAt(1, hi)
        p.fillPath(theme.round_rect(QPainterPath(), lr, 2), grad)
        p.drawText(QRectF(lr.left() - 30, lr.y() - 5, 26, 18), Qt.AlignmentFlag.AlignRight, "少")
        p.drawText(QRectF(lr.right() + 4, lr.y() - 5, 30, 18), Qt.AlignmentFlag.AlignLeft, f"{self.max:,}")
        p.end()


SHUTTER_TICKS = [1 / 8000, 1 / 4000, 1 / 2000, 1 / 1000, 1 / 500, 1 / 250, 1 / 125, 1 / 60, 1 / 30, 1 / 15,
                 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8, 15, 30]
ISO_TICKS = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200]


class Scatter(Chart):
    """快門 × ISO 的散佈圖（兩軸都是對數）。同一組設定拍很多張，泡泡就越大。"""
    LEFT, BOTTOM, PAD = 50, 24, 10

    def __init__(self, points):
        counts: dict[tuple, int] = {}
        for x, y in points:
            counts[(x, y)] = counts.get((x, y), 0) + 1
        self.pts = sorted(counts.items(), key=lambda kv: -kv[1])
        super().__init__([])
        xs = [k[0] for k, _ in self.pts]
        ys = [k[1] for k, _ in self.pts]
        self.x0, self.x1 = math.log10(min(xs)) - 0.2, math.log10(max(xs)) + 0.2
        self.y0, self.y1 = math.log10(min(ys)) - 0.15, math.log10(max(ys)) + 0.15
        self.max = max(c for _, c in self.pts)

    def sizeHint(self):
        return QSize(300, 230)

    def _xy(self, x, y):
        w = self.width() - self.LEFT - self.PAD
        h = self.height() - self.BOTTOM - self.PAD
        px = self.LEFT + (math.log10(x) - self.x0) / max(1e-6, self.x1 - self.x0) * w
        py = self.PAD + h - (math.log10(y) - self.y0) / max(1e-6, self.y1 - self.y0) * h
        return px, py

    def _r(self, n):
        return 3 + 9 * math.sqrt(n / self.max)

    def _hit(self, pos):
        best, bd = -1, 1e9
        for i, ((x, y), n) in enumerate(self.pts):
            px, py = self._xy(x, y)
            d = math.hypot(pos.x() - px, pos.y() - py)
            if d <= self._r(n) + 3 and d < bd:
                best, bd = i, d
        return best

    def _label(self, i):
        (x, y), n = self.pts[i]
        return f"{fmt_shutter(x)} · ISO {y}", n

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = theme.font("caption")
        p.setFont(f)
        grid_pen = QPen(theme.c("separator"), 1)
        # 格線與刻度
        last = -1e9
        for t in SHUTTER_TICKS:
            if self.x0 <= math.log10(t) <= self.x1:
                px, _ = self._xy(t, 10 ** self.y0)
                if px - last < 48:      # 標籤太擠就跳過這一格
                    continue
                last = px
                p.setPen(grid_pen)
                p.drawLine(QPointF(px, self.PAD), QPointF(px, self.height() - self.BOTTOM))
                p.setPen(theme.c("secondary"))
                p.drawText(QRectF(px - 30, self.height() - self.BOTTOM + 4, 60, 18), Qt.AlignmentFlag.AlignHCenter,
                           fmt_shutter(t).replace(" s", "s"))
        for t in ISO_TICKS:
            if self.y0 <= math.log10(t) <= self.y1:
                _, py = self._xy(10 ** self.x0, t)
                p.setPen(grid_pen)
                p.drawLine(QPointF(self.LEFT, py), QPointF(self.width() - self.PAD, py))
                p.setPen(theme.c("secondary"))
                p.drawText(QRectF(0, py - 9, self.LEFT - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           str(t))
        for i, ((x, y), n) in reversed(list(enumerate(self.pts))):
            px, py = self._xy(x, y)
            r = self._r(n)
            col = theme.c("accent")
            col.setAlphaF(0.95 if i == self._hover else 0.55)
            p.setPen(QPen(theme.c("accent"), 1))
            p.setBrush(col)
            p.drawEllipse(QPointF(px, py), r, r)
        p.end()


def _weekday_hour(photos):
    import datetime as dt
    m = [[0] * 24 for _ in range(7)]
    n = 0
    for ph in photos:
        raw = str((ph.info or {}).get("dateTimeOriginal") or "")
        try:
            d = dt.datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        m[d.weekday()][d.hour] += 1
        n += 1
    return m, n


def card(title, sub, chart):
    frame = QFrame()
    frame.setProperty("card", True)
    head = hbox(label(title, "headline"), label(sub, "caption") if sub else None, None, spacing=6)
    body = chart if chart else label("—", "secondary")
    if chart:
        chart.setMinimumHeight(chart.sizeHint().height())
    frame.setLayout(vbox(head, body, None, spacing=12, margins=(16, 14, 16, 14)))
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    return frame


class StatsPage(Page):
    title = "統計數據"
    uses_source = True

    def __init__(self, window):
        super().__init__(window)
        self.scan_btn = button("分析全部照片的 EXIF", "primary", "chart", on_click=library.scan_all_info)
        self.progress = label("—", "secondary")
        self.toolbar.addWidget(self.scan_btn)
        self.toolbar.addWidget(self.progress)
        self.toolbar.addStretch(1)
        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 4, 0)
        self.grid.setSpacing(14)
        self.area = scroll(self.host)
        self.empty = EmptyState("chart")
        self.empty.set("尚未載入照片", "從上方「開啟資料夾」開始。")
        self.root.addWidget(self.area, 1)
        self.root.addWidget(self.empty, 1)
        self._cols = 3
        self._dirty = True
        library.changed.connect(self._mark)
        library.stats_changed.connect(self._paint_progress)
        library.progress_done.connect(self._mark)

    def _mark(self):
        if self.isVisible():
            self.render()
        else:
            self._dirty = True

    def on_show(self):
        self.render()

    def _paint_progress(self):
        s = library.stats()
        self.progress.setText('已分析 {0:,} / {1:,}'.format(s['analysed'], s['total']) if s["total"] else "—")
        self.scan_btn.setEnabled(bool(s["total"]) and s["analysed"] < s["total"] and not library.scanning)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cols = 1 if self.width() < 760 else 2 if self.width() < 1180 else 3
        if cols != self._cols:
            self._cols = cols
            if not self._dirty:
                self.render()

    def render(self):
        self._dirty = False
        self._paint_progress()
        has = bool(library.photos)
        self.area.setVisible(has)
        self.empty.setVisible(not has)
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().hide()          # deleteLater 要等回到事件迴圈才刪，先藏起來免得疊在新的底下
                it.widget().deleteLater()
        if not has:
            return
        infos = [p.info for p in library.photos if p.info]
        g = lambda k: [i.get(k) for i in infos]  # noqa: E731

        focal = sorted(bucket(g("focalLengthRaw"), lambda v: f"{int(v // 10 * 10)}-{int(v // 10 * 10) + 9}"),
                       key=lambda kv: int(kv[0].split("-")[0]))
        iso = sorted(bucket(g("iso"), str), key=lambda kv: int(kv[0]))
        aperture = bucket([float(f[2:]) if f else None for f in g("fNumber")],
                          lambda v: f"f/{int(v) if v == int(v) else v}")
        shutter = bucket(g("exposureTimeRaw"), fmt_shutter)
        wb = bucket(g("whiteBalanceRaw"), lambda v: WHITE_BALANCE_STD[v] if v in WHITE_BALANCE_STD else f'代碼 {v}')
        cams = bucket(g("model"), str)
        lenses = bucket(g("lensModel"), str)
        styles = bucket(g("creativeStyle"), str)
        formats = bucket([p.ext or None for p in library.photos], str)

        cards = [
            card("焦段", "mm", Columns(focal) if focal else None),
            card("ISO", "", Columns(iso) if iso else None),
            card("光圈", "", Donut(aperture) if aperture else None),
            card("快門", "", Bars(shutter) if shutter else None),
            card("白平衡", "", Donut(wb) if wb else None),
            card("檔案格式", "", Donut(formats) if formats else None),
            card("相機", "", Bars(cams) if cams else None),
            card("鏡頭", "", Bars(lenses) if lenses else None),
            card("創意風格", "Sony", Donut(styles) if styles else None),
        ]
        # ---------------- 二維：兩個參數一起看
        week, n_week = _weekday_hour(library.photos)
        heat_time = Heatmap([f"{h}" for h in range(24)], ["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
                            week, x_every=2) if n_week else None
        fa = [(i.get("focalLengthRaw"), i.get("fNumber")) for i in infos if i.get("focalLengthRaw") and i.get("fNumber")]
        heat_fa = None
        if fa:
            fb = lambda v: int(v // 10 * 10)  # noqa: E731
            xs = sorted({fb(v) for v, _ in fa})
            ys = sorted({a for _, a in fa}, key=lambda a: float(a[2:]))
            m = [[0] * len(xs) for _ in ys]
            for v, a in fa:
                m[ys.index(a)][xs.index(fb(v))] += 1
            heat_fa = Heatmap([f"{x}" for x in xs], ys, m, x_every=max(1, math.ceil(len(xs) / 10)))
        si = [(i["exposureTimeRaw"], i["iso"]) for i in infos if i.get("exposureTimeRaw") and i.get("iso")]
        scatter = Scatter(si) if si else None
        cards += [card("焦段 × 光圈", "mm × f 值", heat_fa), card("快門 × ISO", "泡泡越大 = 越多張", scatter)]
        wide = card("拍攝時段", "星期 × 小時", heat_time)

        # 一格一格往下排；一排排不滿時，最後一張往右延伸把空位補滿。
        # 不加 AlignTop：同一排的卡片會被拉到跟最高的那張一樣高。
        n = self._cols
        rows = [cards[i:i + n] for i in range(0, len(cards), n)]
        for r, items in enumerate(rows):
            for c, w in enumerate(items):
                span = n - c if c == len(items) - 1 else 1
                self.grid.addWidget(w, r, c, 1, span)
        row = len(rows)
        self.grid.addWidget(wide, row, 0, 1, n)
        for c in range(3):
            self.grid.setColumnStretch(c, 1 if c < n else 0)
        for r in range(row + 2):
            self.grid.setRowStretch(r, 0)
        self.grid.setRowStretch(row + 1, 1)
