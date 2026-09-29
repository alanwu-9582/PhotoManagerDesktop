"""虛擬化的縮圖牆。

網頁版是「一頁 50 張、換頁才讀下一批」；桌面版改成 QListView 的虛擬捲動：
不管資料夾裡有幾千張，畫面上只會畫看得到的那幾格，縮圖也只替那幾格去要。
所以分頁鈕不見了，但「翻到哪才讀哪」的精神一樣，而且捲動是連續的。

捲很快的時候，路過的每一格都會去排一個縮圖請求。停下來 120ms 之後把還沒開始的
請求整批丟掉、只替現在看得到的重新要 —— 手停在哪，哪裡就先出來。
"""
from __future__ import annotations

from ..i18n import tr, tr_value

from dataclasses import dataclass

from PySide6.QtCore import (QAbstractListModel, QMimeData, QModelIndex, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QTimer, Signal, QByteArray)
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QDrag, QPixmap, QFontMetrics, QTransform
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListView, QStyledItemDelegate, QStyle

from ..engine.categories import categories
from ..engine.library import library, Photo
from ..state import state
from . import icons, theme

MIME = "application/x-pm-photo"
PhotoRole = Qt.ItemDataRole.UserRole + 1


@dataclass(eq=False)
class GroupHeader:
    """群組標題列。collapsed 時底下的照片不會出現在模型裡（不佔版面、也不會去讀縮圖）。"""
    key: str
    title: str
    count: int
    collapsed: bool = False


class PhotoModel(QAbstractListModel):
    def __init__(self, parent=None, draggable=False):
        super().__init__(parent)
        self.rows: list = []                 # Photo 或 GroupHeader
        self.photos: list[Photo] = []        # 只有照片（依顯示順序）
        self._rows: dict[int, int] = {}
        self._draggable = draggable
        library.photo_updated.connect(self.touch)

    def set_photos(self, photos: list[Photo]):
        self.set_rows(photos)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.photos = [r for r in self.rows if isinstance(r, Photo)]
        self._rows = {r.id: i for i, r in enumerate(self.rows) if isinstance(r, Photo)}
        self.endResetModel()

    @property
    def has_headers(self):
        return len(self.photos) != len(self.rows)

    def row_of(self, photo_id) -> int:
        return self._rows.get(photo_id, -1)

    def touch(self, photo: Photo):
        r = self._rows.get(photo.id)
        if r is not None:
            idx = self.index(r)
            self.dataChanged.emit(idx, idx)

    def touch_all(self):
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1))

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        p = self.rows[index.row()]
        if role == PhotoRole:
            return p
        if isinstance(p, GroupHeader):
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            return p.rel_path
        if role == Qt.ItemDataRole.DisplayRole:
            return p.name
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self._draggable:
            f |= Qt.ItemFlag.ItemIsDragEnabled
        return f

    def mimeTypes(self):  # noqa: N802
        return [MIME]

    def mimeData(self, indexes):  # noqa: N802
        md = QMimeData()
        if indexes and isinstance(self.rows[indexes[0].row()], Photo):
            md.setData(MIME, QByteArray(str(self.rows[indexes[0].row()].id).encode()))
        return md


# ============================================================ 共用繪圖
def rounded(rect: QRectF, r: float) -> QPainterPath:
    return theme.round_rect(QPainterPath(), rect, r)


def draw_cover(p: QPainter, pm: QPixmap, rect: QRectF):
    """等同 object-fit: cover —— 置中裁掉多的那一邊。"""
    dpr = pm.devicePixelRatio()
    pw, ph = pm.width() / dpr, pm.height() / dpr
    if pw <= 0 or ph <= 0:
        return
    k = max(rect.width() / pw, rect.height() / ph)
    sw, sh = rect.width() / k, rect.height() / k
    src = QRectF((pw - sw) / 2 * dpr, (ph - sh) / 2 * dpr, sw * dpr, sh * dpr)
    p.drawPixmap(rect, pm, src)


def draw_contain(p: QPainter, pm: QPixmap, rect: QRectF):
    dpr = pm.devicePixelRatio()
    pw, ph = pm.width() / dpr, pm.height() / dpr
    k = min(rect.width() / pw, rect.height() / ph)
    w, h = pw * k, ph * k
    p.drawPixmap(QRectF(rect.x() + (rect.width() - w) / 2, rect.y() + (rect.height() - h) / 2, w, h),
                 pm, QRectF(0, 0, pm.width(), pm.height()))


def fill_image(p: QPainter, pm: QPixmap, rect: QRectF, path: QPainterPath, cover=True):
    """把照片填進一條路徑。用材質筆刷填而不是裁切：裁切沒有反鋸齒，圓角會是鋸齒狀甚至看不出來。"""
    dpr = pm.devicePixelRatio()
    pw, ph = pm.width() / dpr, pm.height() / dpr
    if pw <= 0 or ph <= 0:
        return
    k = (max if cover else min)(rect.width() / pw, rect.height() / ph)
    w, h = pw * k, ph * k
    x, y = rect.x() + (rect.width() - w) / 2, rect.y() + (rect.height() - h) / 2
    brush = QBrush(pm)
    brush.setTransform(QTransform().translate(x, y).scale(w / pm.width(), h / pm.height()))
    if not cover:
        path = path.intersected(rounded(QRectF(x, y, w, h), 0))
    p.fillPath(path, brush)


def paint_thumb(p: QPainter, photo: Photo, rect: QRectF, radius: float, cover=True, path=None):
    clip = path if path is not None else rounded(rect, radius)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.fillPath(clip, theme.c("photo_bg"))
    pm = library.thumb(photo)
    if pm is not None:
        fill_image(p, pm, rect, clip, cover)
    else:
        if photo.thumb_state != "error":
            library.request_thumb(photo)
            glyph = icons.pixmap("image", theme.T["tertiary"], 22)
        else:
            glyph = icons.pixmap("alert", theme.T["tertiary"], 22)
        p.drawPixmap(QRectF(rect.center().x() - 11, rect.center().y() - 11, 22, 22), glyph,
                     QRectF(0, 0, glyph.width(), glyph.height()))
    p.restore()


def pill(p: QPainter, rect: QRectF, color: QColor, text: str, text_color: QColor, fnt):
    p.fillPath(rounded(rect, 5), color)
    p.setFont(fnt)
    p.setPen(text_color)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def text_on(color: QColor) -> QColor:
    lum = 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
    return QColor("#111111") if lum > 0.55 else QColor("#ffffff")


HEADER_H = 44


def paint_header(p: QPainter, h: GroupHeader, rect: QRectF, hovered: bool):
    r = rect.adjusted(6, 8, -6, -4)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if hovered:
        p.fillPath(rounded(r, 8), theme.c("fill"))
    ic = icons.pixmap("chevron-right" if h.collapsed else "chevron-down", theme.T["secondary"], 12)
    p.drawPixmap(QRectF(r.x() + 8, r.center().y() - 6, 12, 12), ic, QRectF(ic.rect()))
    f = theme.font("headline", 600)
    p.setFont(f)
    p.setPen(theme.c("label"))
    fm = QFontMetrics(f)
    title = fm.elidedText(h.title, Qt.TextElideMode.ElideMiddle, int(r.width() - 120))
    p.drawText(QRectF(r.x() + 28, r.y(), r.width() - 100, r.height()), Qt.AlignmentFlag.AlignVCenter, title)
    x = r.x() + 28 + fm.horizontalAdvance(title) + 10
    cf = theme.font("caption", 600)
    cw = QFontMetrics(cf).horizontalAdvance(str(h.count)) + 14
    pill(p, QRectF(x, r.center().y() - 9, cw, 18), theme.c("fill_strong"), str(h.count), theme.c("secondary"), cf)
    p.setPen(QPen(theme.c("separator"), 1))
    p.drawLine(QPointF(x + cw + 10, r.center().y()), QPointF(r.right() - 4, r.center().y()))


# ============================================================ 照片檢視的卡片
class CardDelegate(QStyledItemDelegate):
    """縮圖 + 檔名 + 選定的 EXIF 欄位。每張卡一樣高（欄位數決定），虛擬捲動才算得快。"""
    PAD = 10
    ROW_H = 18

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.hover_row = -1

    def card_height(self, width):
        n = len(state.enabled_fields())
        return int(width * 0.75) + self.PAD + 20 + max(1, n) * self.ROW_H + self.PAD + 4

    LIST_H = 64

    def tool_rect(self, card: QRect) -> QRect:
        if self.view.display == "list":
            return QRect(card.right() - 40, card.center().y() - 16, 32, 32)
        thumb_h = int(card.width() * 0.75)
        return QRect(card.right() - 40, card.top() + thumb_h - 40, 32, 32)

    def paint(self, p: QPainter, opt, index):
        photo = index.data(PhotoRole)
        if isinstance(photo, GroupHeader):
            paint_header(p, photo, QRectF(opt.rect), index.row() == self.hover_row)
            return
        if self.view.display == "list":
            self.paint_row(p, opt, index, photo)
            return
        r = QRectF(opt.rect).adjusted(6, 6, -6, -6)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        hovered = index.row() == self.hover_row
        card = rounded(r, 12)
        p.fillPath(card, theme.c("content"))
        p.setPen(QPen(theme.c("accent") if hovered else theme.c("separator"), 1))
        p.drawPath(card)

        thumb = QRectF(r.x(), r.y(), r.width(), round(r.width() * 0.75))
        # 縮圖的上緣跟著卡片的圓角，下緣是直的
        top = card.intersected(rounded(thumb, 0))
        paint_thumb(p, photo, thumb, 0, path=top)

        cat = categories.by_id(photo.cat_id) if photo.cat_id else None
        if cat:
            col = QColor(cat.color)
            pill(p, QRectF(thumb.x() + 8, thumb.y() + 8, 22, 22), col, cat.key.upper() or "·",
                 text_on(col), theme.font("caption", 700))

        if hovered:
            tool_r = QRectF(self.tool_rect(opt.rect.adjusted(6, 6, -6, -6)))
            p.fillPath(rounded(tool_r, 8), QColor(20, 20, 22, 200))
            ic = icons.pixmap("tool", "#ffffff", 16)
            p.drawPixmap(QRectF(tool_r.center().x() - 8, tool_r.center().y() - 8, 16, 16), ic,
                         QRectF(0, 0, ic.width(), ic.height()))

        x, w = r.x() + self.PAD, r.width() - self.PAD * 2
        y = thumb.bottom() + self.PAD
        p.setFont(theme.font("callout", 600))
        p.setPen(theme.c("label"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(x, y, w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(photo.rel_path, Qt.TextElideMode.ElideMiddle, int(w)))
        y += 22

        fields = state.enabled_fields()
        info = photo.info or {}
        rows = [(lb, info.get(k)) for k, lb in fields if info.get(k) is not None]
        f = theme.font("caption")
        p.setFont(f)
        fm = QFontMetrics(f)
        if not rows:
            p.setPen(theme.c("tertiary"))
            p.drawText(QRectF(x, y, w, self.ROW_H), Qt.AlignmentFlag.AlignVCenter,
                       tr("讀取中…") if photo.info_state == "idle" else tr("無 EXIF"))
            return
        for lb, val in rows:
            p.setPen(theme.c("secondary"))
            p.drawText(QRectF(x, y, w * 0.42, self.ROW_H), Qt.AlignmentFlag.AlignVCenter, lb)
            p.setPen(theme.c("label"))
            vw = w * 0.58
            p.drawText(QRectF(x + w * 0.42, y, vw, self.ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fm.elidedText(tr_value(val), Qt.TextElideMode.ElideRight, int(vw)))
            y += self.ROW_H

    def paint_row(self, p: QPainter, opt, index, photo):
        """列表：小縮圖、檔名與路徑，右邊把選定的 EXIF 欄位排成一欄一欄。"""
        r = QRectF(opt.rect).adjusted(6, 2, -6, -2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        hovered = index.row() == self.hover_row
        path = rounded(r, 8)
        p.fillPath(path, theme.c("content") if not hovered else theme.c("control_hover"))
        if hovered:
            p.setPen(QPen(theme.c("accent"), 1))
            p.drawPath(path)
        th = r.height() - 12
        thumb = QRectF(r.x() + 6, r.y() + 6, th * 1.33, th)
        paint_thumb(p, photo, thumb, 5)
        cat = categories.by_id(photo.cat_id) if photo.cat_id else None
        if cat:
            col = QColor(cat.color)
            pill(p, QRectF(thumb.x() + 3, thumb.y() + 3, 18, 18), col, cat.key.upper() or "·",
                 text_on(col), theme.font("caption", 700))
        x = thumb.right() + 12
        right = r.right() - (48 if hovered else 12)
        name_w = min(260.0, max(140.0, (right - x) * 0.3))
        p.setFont(theme.font("body", 600))
        p.setPen(theme.c("label"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(x, r.y() + 10, name_w, 20), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(photo.name, Qt.TextElideMode.ElideMiddle, int(name_w)))
        p.setFont(theme.font("caption"))
        p.setPen(theme.c("secondary"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(x, r.y() + 32, name_w, 18), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(photo.folder or tr("（最上層）"), Qt.TextElideMode.ElideMiddle, int(name_w)))
        x += name_w + 16
        fields = state.enabled_fields()
        info = photo.info or {}
        if fields and right > x:
            col_w = (right - x) / len(fields)
            for k, lb in fields:
                if col_w >= 48:
                    p.setFont(theme.font("caption"))
                    p.setPen(theme.c("tertiary"))
                    fm = QFontMetrics(p.font())
                    p.drawText(QRectF(x, r.y() + 10, col_w - 8, 18), Qt.AlignmentFlag.AlignVCenter,
                               fm.elidedText(lb, Qt.TextElideMode.ElideRight, int(col_w - 8)))
                    v = info.get(k)
                    text = tr_value(v) if v is not None else ("…" if photo.info_state == "idle" else "—")
                    p.setFont(theme.font("callout"))
                    p.setPen(theme.c("label") if v is not None else theme.c("tertiary"))
                    fm = QFontMetrics(p.font())
                    p.drawText(QRectF(x, r.y() + 30, col_w - 8, 20), Qt.AlignmentFlag.AlignVCenter,
                               fm.elidedText(text, Qt.TextElideMode.ElideRight, int(col_w - 8)))
                x += col_w
        if hovered:
            tool_r = QRectF(self.tool_rect(opt.rect.adjusted(6, 2, -6, -2)))
            p.fillPath(rounded(tool_r, 8), theme.c("fill_strong"))
            ic = icons.pixmap("tool", theme.T["label"], 16)
            p.drawPixmap(QRectF(tool_r.center().x() - 8, tool_r.center().y() - 8, 16, 16), ic, QRectF(ic.rect()))

    def sizeHint(self, opt, index):  # noqa: N802
        return self.view.item_size(index)


# ============================================================ 整理分類的小縮圖
class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.selected_id = None
        self.compare_id = None
        self.hover_row = -1

    def paint(self, p: QPainter, opt, index):
        photo: Photo = index.data(PhotoRole)
        r = QRectF(opt.rect).adjusted(3, 3, -3, -3)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        paint_thumb(p, photo, r, 8)

        if photo.organized:
            p.fillPath(rounded(r, 8), QColor(0, 0, 0, 120))

        # 檔名（底部漸層上）
        if r.width() >= 70:
            band = QRectF(r.x(), r.bottom() - 22, r.width(), 22)
            p.fillPath(rounded(r, 8).intersected(rounded(band, 0)), QColor(0, 0, 0, 110))
            f = theme.font("caption")
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 235))
            fm = QFontMetrics(f)
            p.drawText(band.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(photo.name, Qt.TextElideMode.ElideMiddle, int(band.width() - 12)))

        f = theme.font("caption", 600)
        fm = QFontMetrics(f)
        if photo.organized:
            text = "✓ " + photo.organized["folder"]
            w = min(r.width() - 12, fm.horizontalAdvance(text) + 14)
            pill(p, QRectF(r.x() + 6, r.y() + 6, w, 20), theme.c("green"),
                 fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w - 10)), QColor("#0b2e14"), f)
        elif photo.cat_id:
            cat = categories.by_id(photo.cat_id)
            if cat:
                col = QColor(cat.color)
                w = min(r.width() - 12, fm.horizontalAdvance(cat.name) + 14)
                pill(p, QRectF(r.x() + 6, r.y() + 6, w, 20), col,
                     fm.elidedText(cat.name, Qt.TextElideMode.ElideRight, int(w - 10)), text_on(col), f)

        ring = None
        if photo.id == self.selected_id:
            ring = theme.c("accent")
        elif photo.id == self.compare_id:
            ring = theme.c("orange")
        if ring is not None:
            p.setPen(QPen(ring, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(rounded(r.adjusted(-1.5, -1.5, 1.5, 1.5), 9.5))

    def sizeHint(self, opt, index):  # noqa: N802
        return self.view.item_size(index)


# ============================================================ 視圖
class PhotoGrid(QListView):
    """columns 決定每排幾張；格子大小跟著寬度走。"""
    photo_clicked = Signal(object)
    tool_clicked = Signal(object, QPoint)
    context_requested = Signal(object, QPoint)
    header_clicked = Signal(object)

    def __init__(self, kind="card", columns=4, draggable=False, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.columns = columns
        self.display = "card"      # card | list（只對照片檢視的卡片有意義）
        self._cell = QSize(100, 100)
        self.model_ = PhotoModel(self, draggable=draggable)
        self.setModel(self.model_)
        self.delegate = CardDelegate(self) if kind == "card" else ThumbDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(400)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(40)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 捲軸一直留著：否則捲軸出現的那一刻可用寬度變窄，每排會少一張。
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setMouseTracking(True)
        self.setSpacing(0)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._draggable = draggable
        self._dragged = False
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(120)
        self._settle.timeout.connect(self._on_settle)
        self.verticalScrollBar().valueChanged.connect(lambda *_: self._settle.start())
        self._press = None

    # ---------------------------------------------------------------- 版面
    def set_columns(self, n):
        self.columns = max(1, n)
        self._relayout()

    def set_display(self, mode):
        self.display = mode
        self._relayout()
        self.viewport().update()

    def _flexible(self):
        """有群組標題或是列表時，每一列大小不一樣，不能用固定格子。"""
        return self.model_.has_headers or (self.kind == "card" and self.display == "list")

    def _relayout(self):
        w = self.viewport().width() - 1
        if w <= 0:
            return
        cell = max(40, w // self.columns)
        if self.kind == "card":
            h = self.delegate.card_height(cell - 12) + 12
        else:
            h = cell
        self._cell = QSize(cell, h)
        if self._flexible():
            self.setUniformItemSizes(False)
            self.setGridSize(QSize())
        else:
            self.setUniformItemSizes(True)
            self.setGridSize(self._cell)
        self.doItemsLayout()

    def item_size(self, index) -> QSize:
        row = index.data(PhotoRole)
        full = self.viewport().width() - 1
        if isinstance(row, GroupHeader):
            return QSize(full, HEADER_H)
        if self.kind == "card" and self.display == "list":
            return QSize(full, self.delegate.LIST_H)
        return self._cell

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def refresh(self):
        """欄位數量變了（卡片高度跟著變）。"""
        self._relayout()
        self.viewport().update()

    def set_photos(self, photos):
        self.set_rows(photos)

    def set_rows(self, rows):
        # 清單沒變（例如背景掃完 EXIF）就只重畫，不重設模型 —— 捲動位置不能跳回頂端。
        old = self.model_.rows
        same = len(old) == len(rows) and all(
            a is b or (isinstance(a, GroupHeader) and isinstance(b, GroupHeader) and a.key == b.key
                       and a.collapsed == b.collapsed and a.count == b.count and a.title == b.title)
            for a, b in zip(old, rows))
        if same:
            self.viewport().update()
            return
        library.drop_thumb_queue()
        self.model_.set_rows(rows)
        self._relayout()

    def _on_settle(self):
        library.drop_thumb_queue()
        self.viewport().update()

    def photo_at(self, pos) -> Photo | None:
        idx = self.indexAt(pos)
        row = idx.data(PhotoRole) if idx.isValid() else None
        return row if isinstance(row, Photo) else None

    def scroll_to_photo(self, photo_id, center=False):
        r = self.model_.row_of(photo_id)
        if r >= 0:
            hint = QAbstractItemView.ScrollHint.PositionAtCenter if center else QAbstractItemView.ScrollHint.EnsureVisible
            self.scrollTo(self.model_.index(r), hint)

    # ---------------------------------------------------------------- 滑鼠
    def mouseMoveEvent(self, e):
        # 自己開始拖曳：QListView 只在「已選取」的項目上才會起拖，而這裡沒有選取模式。
        if (self._draggable and self._press is not None and not self._dragged
                and e.buttons() & Qt.MouseButton.LeftButton
                and (e.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance()):
            idx = self.indexAt(self._press)
            if idx.isValid() and isinstance(idx.data(PhotoRole), Photo):
                self._dragged = True
                self._start_drag(idx)
                self._press = None
                return
        super().mouseMoveEvent(e)
        if self.kind == "card" or self.model_.has_headers:
            idx = self.indexAt(e.position().toPoint())
            row = idx.row() if idx.isValid() else -1
            if row != self.delegate.hover_row:
                self.delegate.hover_row = row
                self.viewport().update()
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if row >= 0 else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if getattr(self.delegate, "hover_row", -1) != -1:
            self.delegate.hover_row = -1
            self.viewport().update()

    def mousePressEvent(self, e):
        self._press = e.position().toPoint() if e.button() == Qt.MouseButton.LeftButton else None
        self._dragged = False
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        pos = e.position().toPoint()
        if (pos - self._press).manhattanLength() > 6:
            return
        idx = self.indexAt(pos)
        if not idx.isValid():
            return
        photo = idx.data(PhotoRole)
        if isinstance(photo, GroupHeader):
            self.header_clicked.emit(photo)
            return
        if self.kind == "card":
            card = self.visualRect(idx).adjusted(6, 2, -6, -2) if self.display == "list" \
                else self.visualRect(idx).adjusted(6, 6, -6, -6)
            if self.delegate.tool_rect(card).contains(pos):
                self.tool_clicked.emit(photo, self.viewport().mapToGlobal(pos))
                return
        self.photo_clicked.emit(photo)

    def contextMenuEvent(self, e):
        photo = self.photo_at(e.pos())
        if photo:
            self.context_requested.emit(photo, e.globalPos())

    def startDrag(self, actions):  # noqa: N802
        pass   # 由 mouseMoveEvent 的 _start_drag 接手

    def _start_drag(self, idx):
        photo = idx.data(PhotoRole)
        drag = QDrag(self)
        drag.setMimeData(self.model_.mimeData([idx]))
        pm = library.thumb(photo)
        if pm:
            small = pm.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            drag.setPixmap(small)
            drag.setHotSpot(QPoint(small.width() // 2, small.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)
