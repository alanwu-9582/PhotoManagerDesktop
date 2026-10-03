"""整理分類頁左邊那張大圖的檢視器（對應網頁版 js/pages/organize-viewer.js）。

三件事：
  拖曳平移 + 滾輪縮放   縮到底就是「剛好放進框裡」，不會比原本更小；連點兩下放大／還原
  左右對照              把右邊的縮圖拖進來就分成兩格，連動開關打開後一起動
  偷看下一張            按住 space（或那顆按鈕）換成下一張，放開就回來 —— 視角完全不變

解析度分兩層：平常用「螢幕大小」的版本（解碼快、預載得起），放大到看得出差別時
才替這一張去解原尺寸。互動中用快速縮放，手停下來 120ms 再用平滑縮放重畫一次。
"""
from __future__ import annotations


import math

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from ..engine.library import library, Photo
from . import icons, theme
from .photogrid import MIME

MIN_ZOOM, MAX_ZOOM = 1.0, 8.0


def display_edge() -> int:
    screen = QGuiApplication.primaryScreen()
    if not screen:
        return 2560
    s = screen.size() * screen.devicePixelRatio()
    return int(min(4096, max(1600, max(s.width(), s.height()))))


class Pane(QWidget):
    transformed = Signal(object)
    close_requested = Signal()

    def __init__(self, show_close=False, parent=None):
        super().__init__(parent)
        self.photo: Photo | None = None
        self.shown: Photo | None = None      # 偷看時跟 photo 不同
        self.pix: QPixmap | None = None
        self._pix_src = None                 # 目前 pix 是哪張圖（id, edge）
        self.x = self.y = 0.0
        self.z = 1.0
        self.show_close = show_close
        self.peeking = False
        self._drag = None
        self._fast = False
        self._smooth = QTimer(self)
        self._smooth.setSingleShot(True)
        self._smooth.setInterval(120)
        self._smooth.timeout.connect(self._settle)
        self.setMouseTracking(True)
        self.setMinimumSize(120, 120)
        library.full_ready.connect(self._on_full)
        library.photo_updated.connect(self._on_thumb)

    # ---------------------------------------------------------------- 照片
    def set_photo(self, photo: Photo | None):
        if photo is self.photo and not self.peeking:
            return
        same = photo is self.photo
        self.photo = photo
        self.peeking = False
        self._show(photo)
        if not same:
            self.reset()

    def peek(self, photo: Photo | None):
        """暫時換成另一張，平移縮放不動。photo=None 就換回來。"""
        if photo is None:
            if self.peeking:
                self.peeking = False
                self._show(self.photo)
            return
        self.peeking = True
        self._show(photo)

    def _show(self, photo):
        self.shown = photo
        self.pix = None
        self._pix_src = None
        if photo is None:
            self.update()
            return
        self._pick_image()
        self.update()

    def _pick_image(self):
        p = self.shown
        if p is None:
            return
        edge = display_edge()
        need_full = self._needs_full()
        img = library.full(p, 0) if need_full else None
        if img is None:
            img = library.full(p, edge)
        if img is None:
            img = library.full_any(p)
        if img is not None:
            key = (p.id, img.cacheKey())
            if key != self._pix_src:
                self.pix = QPixmap.fromImage(img)
                self._pix_src = key
        elif self.pix is None:
            thumb = library.thumb(p)
            if thumb is not None:
                self.pix = thumb
                self._pix_src = (p.id, "thumb")
            else:
                library.request_thumb(p)
        if library.full(p, edge) is None:
            library.request_full(p, edge)
        if need_full and library.full(p, 0) is None:
            library.request_full(p, 0)

    def _needs_full(self):
        """畫面上一個像素要用到顯示版本的一個以上像素，就該換原尺寸了。"""
        if self.shown is None or self.z <= 1.2:
            return False
        info = self.shown.info or {}
        w, h = info.get("width") or 0, info.get("height") or 0
        edge = display_edge()
        if w and h and max(w, h) <= edge:
            return False
        dpr = self.devicePixelRatioF()
        fit = self._fit_size(edge, edge)
        return max(fit.width(), fit.height()) * self.z * dpr > edge * 1.05

    def _on_full(self, photo, edge):
        if self.shown is photo and edge >= 0:
            self._pick_image()
            self.update()

    def _on_thumb(self, photo):
        if self.shown is photo and self.pix is None:
            self._pick_image()
            self.update()

    # ---------------------------------------------------------------- 幾何
    def _fit_size(self, iw=None, ih=None) -> QSize:
        if iw is None:
            if not self.pix:
                return QSize(0, 0)
            dpr = self.pix.devicePixelRatio()
            iw, ih = self.pix.width() / dpr, self.pix.height() / dpr
        if iw <= 0 or ih <= 0:
            return QSize(0, 0)
        k = min(self.width() / iw, self.height() / ih)
        return QSize(max(1, int(iw * k)), max(1, int(ih * k)))

    def _limit(self):
        if self.z <= 1.001:
            self.x = self.y = 0.0
            return
        base = self._fit_size()
        mx = max(0.0, (base.width() * self.z - self.width()) / 2)
        my = max(0.0, (base.height() * self.z - self.height()) / 2)
        self.x = min(max(self.x, -mx), mx)
        self.y = min(max(self.y, -my), my)

    def view(self):
        return {"x": self.x, "y": self.y, "z": self.z}

    def set_view(self, x, y, z):
        self.z = min(max(z, MIN_ZOOM), MAX_ZOOM)
        self.x, self.y = x, y
        self._changed()

    def reset(self):
        self.x = self.y = 0.0
        self.z = 1.0
        self._changed(emit=False)

    def zoom_at(self, factor, cx=None, cy=None):
        before = self.z
        after = min(max(before * factor, MIN_ZOOM), MAX_ZOOM)
        if after == before:
            return
        # 座標原點在框的正中間；游標底下那一點不動：t' = c - (c - t) * z'/z
        ox = (cx if cx is not None else self.width() / 2) - self.width() / 2
        oy = (cy if cy is not None else self.height() / 2) - self.height() / 2
        k = after / before
        self.z = after
        self.x = ox - (ox - self.x) * k
        self.y = oy - (oy - self.y) * k
        self._changed()

    def _changed(self, emit=True):
        self._limit()
        self._fast = True
        self._smooth.start()
        self.update()
        if emit:
            self.transformed.emit(self)

    def _settle(self):
        self._fast = False
        # 放大到需要的時候才去要原尺寸。
        before = self._pix_src
        self._pick_image()
        if before != self._pix_src:
            pass
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._limit()

    # ---------------------------------------------------------------- 畫
    def close_rect(self):
        return QRectF(self.width() - 38, 10, 28, 28)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.c("viewer_bg"))
        if self.pix is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not self._fast)
            base = self._fit_size()
            w, h = base.width() * self.z, base.height() * self.z
            cx, cy = self.width() / 2 + self.x, self.height() / 2 + self.y
            p.drawPixmap(QRectF(cx - w / 2, cy - h / 2, w, h), self.pix, QRectF(self.pix.rect()))
        elif self.shown is not None:
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(theme.font("body"))
            text = "讀取中…" if self.shown.thumb_state != "error" else (self.shown.thumb_error or "無法預覽")
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.shown is not None:
            f = theme.font("caption", 600)
            fm = QFontMetrics(f)
            name = self.shown.name
            tw = fm.horizontalAdvance(name) + 18
            r = QRectF(10, 10, min(tw, self.width() - 60), 22)
            path = QPainterPath()
            theme.round_rect(path, r, 6)
            p.fillPath(path, QColor(255, 159, 10, 230) if self.peeking else QColor(0, 0, 0, 140))
            p.setFont(f)
            p.setPen(QColor("#1c1c1e") if self.peeking else QColor("white"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(name, Qt.TextElideMode.ElideMiddle, int(r.width() - 14)))
        par = self.parentWidget()
        if getattr(par, "_drop_hint", False):
            pen = p.pen()
            pen.setColor(theme.c("accent"))
            pen.setWidthF(3)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
            p.setPen(Qt.PenStyle.NoPen)
            f = theme.font("callout", 600)
            p.setFont(f)
            tip = "放開就左右對照"
            tw = QFontMetrics(f).horizontalAdvance(tip) + 28
            tool_r = QRectF((self.width() - tw) / 2, self.height() / 2 - 17, tw, 34)
            p.fillPath(theme.round_rect(QPainterPath(), tool_r, 8), theme.c("accent"))
            p.setPen(QColor("white"))
            p.drawText(tool_r, Qt.AlignmentFlag.AlignCenter, tip)
        if self.show_close:
            r = self.close_rect()
            path = QPainterPath()
            theme.round_rect(path, r, 7)
            p.fillPath(path, QColor(0, 0, 0, 150))
            ic = icons.pixmap("x", "#ffffff", 13)
            p.drawPixmap(QRectF(r.center().x() - 6.5, r.center().y() - 6.5, 13, 13), ic, QRectF(ic.rect()))
        p.end()

    # ---------------------------------------------------------------- 互動
    def wheelEvent(self, e):
        dy = e.angleDelta().y() or e.pixelDelta().y()
        pos = e.position()
        self.zoom_at(math.exp(dy * 0.0016), pos.x(), pos.y())

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.show_close and self.close_rect().contains(e.position()):
            self.close_requested.emit()
            return
        if self.z > 1.001:
            self._drag = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            d = e.position() - self._drag
            self._drag = e.position()
            self.x += d.x()
            self.y += d.y()
            self._changed()
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.z > 1.001 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.z > 1.001 else Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, e):
        if self.z > 1.001:
            self.reset()
            self.transformed.emit(self)
        else:
            self.zoom_at(2.5, e.position().x(), e.position().y())


class HudButton(QToolButton):
    def __init__(self, icon_name, tip, checkable=False):
        super().__init__()
        self._icon_name = icon_name
        self._icon_color = "#ffffff"
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(icons.icon(icon_name, "#ffffff", 15))
        self.setIconSize(QSize(15, 15))
        self.setFixedSize(30, 30)
        self.setStyleSheet(
            "QToolButton { background: transparent; border-radius: 6px; padding: 0; min-height: 30px; max-height: 30px; }"
            "QToolButton:hover { background: rgba(255,255,255,0.14); }"
            "QToolButton:checked { background: rgba(255,255,255,0.24); }"
            "QToolButton:pressed { background: rgba(255,255,255,0.30); }")


class Hud(QWidget):
    """浮在照片底部的深色工具列（HIG 的 HUD 樣式）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peek = HudButton("play", "按住看下一張（Space）")
        self.link = HudButton("link", "兩張一起縮放平移", checkable=True)
        self.link.setChecked(True)
        self.reset = HudButton("reset", "回到原始大小（0）")
        self.zoom = QToolButton()
        self.zoom.setEnabled(False)
        self.zoom.setStyleSheet("QToolButton { color: white; background: transparent; font-weight: 600; padding: 0 6px; }"
                                "QToolButton:disabled { color: white; background: transparent; }")
        self.zoom.setText("100%")
        self.zoom.setFixedHeight(30)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 8, 4)
        lay.setSpacing(2)
        for w in (self.peek, self.link, self.reset, self.zoom):
            lay.addWidget(w)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        theme.round_rect(path, r, 10)
        p.fillPath(path, QColor(28, 28, 30, 215))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)
        p.end()


class InfoOverlay(QWidget):
    """照片資訊那一塊（疊在照片左下角）。收起來只是一顆膠囊，展開換照片也維持展開。"""
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.open = False
        self.lines: list[tuple[str, str]] = []
        self.badge: tuple[str, str] | None = None   # (text, color)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_content(self, lines, badge):
        self.lines = lines
        self.badge = badge
        self._resize()
        self.update()

    def _resize(self):
        if not self.open:
            self.setFixedSize(92, 26)
            return
        f = theme.font("caption")
        fm = QFontMetrics(f)
        w = 260
        for k, v in self.lines:
            w = max(w, fm.horizontalAdvance(v) + 110)
        h = 34 + (26 if self.badge else 0) + len(self.lines) * 18 + 8
        self.setFixedSize(min(w, 420), h)

    def mousePressEvent(self, e):
        self.open = not self.open
        self._resize()
        self.toggled.emit(self.open)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        theme.round_rect(path, r, 10 if self.open else 7)
        p.fillPath(path, QColor(28, 28, 30, 215))
        p.setPen(QColor(255, 255, 255, 230))
        p.setFont(theme.font("caption", 600))
        chev = icons.pixmap("chevron-down" if self.open else "chevron-right", "#ffffff", 11)
        p.drawPixmap(QRectF(10, 7.5, 11, 11), chev, QRectF(chev.rect()))
        p.drawText(QRectF(26, 0, 80, 26), Qt.AlignmentFlag.AlignVCenter, "照片資訊")
        if not self.open:
            return
        y = 30.0
        if self.badge:
            text, color = self.badge
            f = theme.font("caption", 600)
            fm = QFontMetrics(f)
            br = QRectF(10, y, min(fm.horizontalAdvance(text) + 16, self.width() - 20), 20)
            bp = QPainterPath()
            theme.round_rect(bp, br, 5)
            col = QColor(color)
            p.fillPath(bp, col)
            lum = 0.2126 * col.redF() + 0.7152 * col.greenF() + 0.0722 * col.blueF()
            p.setPen(QColor("#111") if lum > 0.55 else QColor("white"))
            p.setFont(f)
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, int(br.width() - 12)))
            y += 26
        f = theme.font("caption")
        p.setFont(f)
        fm = QFontMetrics(f)
        for k, v in self.lines:
            p.setPen(QColor(255, 255, 255, 140))
            p.drawText(QRectF(12, y, 90, 18), Qt.AlignmentFlag.AlignVCenter, k)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(QRectF(100, y, self.width() - 112, 18), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(v, Qt.TextElideMode.ElideRight, self.width() - 112))
            y += 18


class Viewer(QWidget):
    drop_photo = Signal(int)
    exit_compare = Signal()
    peek_pressed = Signal()
    peek_released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("Stage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.linked = True
        self._syncing = False
        self._drop_hint = False
        self.main = Pane()
        self.other = Pane(show_close=True)
        self.other.hide()
        for pane in (self.main, self.other):
            pane.transformed.connect(self._on_transform)
        self.other.close_requested.connect(self.exit_compare.emit)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.main)
        lay.addWidget(self.other)

        self.hud = Hud(self)
        self.hud.link.toggled.connect(self._set_linked)
        self.hud.reset.clicked.connect(self.reset)
        self.hud.peek.pressed.connect(self.peek_pressed.emit)
        self.hud.peek.released.connect(self.peek_released.emit)
        self.hud.link.hide()
        self.info = InfoOverlay(self)
        self.info.toggled.connect(lambda *_: self._place_overlays())
        self._empty = True

    @property
    def panes(self):
        return [self.main] + ([self.other] if self.other.isVisible() else [])

    def render(self, main: Photo | None, other: Photo | None):
        self._empty = main is None
        self.main.set_photo(main)
        if other is not None:
            was = self.other.isVisible()
            self.other.set_photo(other)
            self.other.show()
            if not was:
                self.main.reset()
        else:
            self.other.hide()
            self.other.set_photo(None)
        self.hud.link.setVisible(other is not None)
        self.hud.setVisible(main is not None)
        self.info.setVisible(main is not None)
        self._paint_zoom()
        self._place_overlays()

    def reset(self):
        for p in self.panes:
            p.reset()
        self._paint_zoom()

    def _set_linked(self, on):
        self.linked = on

    def _on_transform(self, src: Pane):
        self._paint_zoom()
        if not self.linked or self._syncing or len(self.panes) < 2:
            return
        self._syncing = True
        for p in self.panes:
            if p is not src:
                p.set_view(src.x, src.y, src.z)
        self._syncing = False

    def _paint_zoom(self):
        self.hud.zoom.setText(f"{round(self.main.z * 100)}%")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_overlays()

    def _place_overlays(self):
        self.hud.adjustSize()
        self.hud.move((self.width() - self.hud.width()) // 2, self.height() - self.hud.height() - 12)
        self.info.move(12, self.height() - self.info.height() - 12)
        if self.info.geometry().intersects(self.hud.geometry()):
            self.hud.move(self.width() - self.hud.width() - 12, self.height() - self.hud.height() - 12)
        self.hud.raise_()
        self.info.raise_()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_hint:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = p.pen()
            pen.setColor(theme.c("accent"))
            pen.setWidth(3)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 12, 12)

    # ---------------------------------------------------------------- 拖進來就對照
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME) and not self._empty:
            e.acceptProposedAction()
            self._drop_hint = True
            self._repaint_panes()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME) and not self._empty:
            e.acceptProposedAction()

    def _repaint_panes(self):
        self.update()
        for pane in (self.main, self.other):
            pane.update()

    def dragLeaveEvent(self, e):
        self._drop_hint = False
        self._repaint_panes()

    def dropEvent(self, e):
        self._drop_hint = False
        self._repaint_panes()
        try:
            pid = int(bytes(e.mimeData().data(MIME)).decode())
        except ValueError:
            return
        e.acceptProposedAction()
        self.drop_photo.emit(pid)
