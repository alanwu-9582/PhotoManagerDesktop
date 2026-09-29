"""提示框（tooltip）。

Qt 原生的 QToolTip 是一個獨立的視窗，QSS 的 border-radius 只畫得出圓角的底色，
視窗本身還是方的（四個角會露出底色）。這裡換成一個透明背景、自己畫圓角的小視窗，
字也小一號。攔的是整個程式的 ToolTip 事件，所以既有的 setToolTip() 全部照用。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QApplication, QWidget

from . import theme

PAD_X, PAD_Y, MAX_W = 8, 4, 320


class TipPopup(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text = ""
        self.owner = None
        self.key = None
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)

    def _font(self):
        return theme.font("caption", 500)

    def show_text(self, pos: QPoint, text: str, owner=None, key=None):
        if not text:
            self.hide()
            return
        self.text, self.owner, self.key = text, owner, key
        fm = QFontMetrics(self._font())
        br = fm.boundingRect(QRect(0, 0, MAX_W, 10000), Qt.TextFlag.TextWordWrap, text)
        size = QSize(br.width() + PAD_X * 2 + 2, br.height() + PAD_Y * 2 + 2)
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x, y = pos.x() + 12, pos.y() + 20
        if x + size.width() > area.right():
            x = area.right() - size.width()
        if y + size.height() > area.bottom():
            y = pos.y() - size.height() - 8
        self.setGeometry(x, y, size.width(), size.height())
        self.update()
        self.show()
        self.raise_()
        self._hide.start(max(4000, len(text) * 90))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = theme.round_rect(QPainterPath(), r, 6)
        p.fillPath(path, theme.c("elevated"))
        p.setPen(theme.c("separator_hex"))
        p.drawPath(path)
        p.setFont(self._font())
        p.setPen(theme.c("label"))
        p.drawText(r.adjusted(PAD_X, PAD_Y, -PAD_X, -PAD_Y), Qt.TextFlag.TextWordWrap, self.text)
        p.end()


class _TipFilter(QObject):
    def __init__(self, popup: TipPopup):
        super().__init__()
        self.popup = popup

    @staticmethod
    def _item_tip(obj, pos):
        view = obj.parent()
        if isinstance(view, QAbstractItemView) and obj is view.viewport():
            idx = view.indexAt(pos)
            if idx.isValid():
                return str(idx.data(Qt.ItemDataRole.ToolTipRole) or ""), (id(view), idx.row(), idx.column())
            return "", None
        return None

    def eventFilter(self, obj, e):  # noqa: N802
        try:
            return self._filter(obj, e)
        except RuntimeError:   # 程式關閉中，物件已經被刪掉
            return False

    def _filter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.ToolTip and isinstance(obj, QWidget):
            item = self._item_tip(obj, e.pos())
            if item is not None:
                text, key = item
            else:
                text, key = obj.toolTip(), id(obj)
            if text:
                self.popup.show_text(e.globalPos(), text, obj, key)
            else:
                self.popup.hide()
            return True
        if self.popup.isVisible():
            if t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.KeyPress,
                     QEvent.Type.WindowDeactivate, QEvent.Type.Hide) and obj is self.popup.owner:
                self.popup.hide()
            elif t == QEvent.Type.MouseMove and obj is self.popup.owner:
                item = self._item_tip(obj, e.position().toPoint())
                if item is not None and item[1] != self.popup.key:
                    self.popup.hide()
        return False


_popup: TipPopup | None = None
_filter: _TipFilter | None = None


def install(app: QApplication):
    global _popup, _filter
    _popup = TipPopup()
    _filter = _TipFilter(_popup)
    app.installEventFilter(_filter)
    app.aboutToQuit.connect(lambda: app.removeEventFilter(_filter))


def show_text(pos: QPoint, text: str, owner=None, key=None):
    if _popup is not None:
        _popup.show_text(pos, text, owner, key)


def hide():
    if _popup is not None:
        _popup.hide()
