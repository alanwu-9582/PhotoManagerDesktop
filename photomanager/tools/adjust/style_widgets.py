"""攝影風格分頁用的兩個元件：風格縮圖格子、iPhone 那種方形控制板。"""
from __future__ import annotations


from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from ...ui import theme


class StyleTile(QAbstractButton):
    """一個風格：用目前這張照片套上去的小預覽 + 名稱。選取時外面一圈主色。"""
    W, H = 96, 92
    THUMB_H = 64

    def __init__(self, key, name, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(name)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.W, self.H)
        self.setToolTip(name)
        self.pix: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def set_pixmap(self, pix):
        self.pix = pix
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        box = QRectF(3, 3, self.W - 6, self.THUMB_H)
        path = theme.round_rect(QPainterPath(), box, 7)
        p.fillPath(path, theme.c("fill"))
        if self.pix is not None and not self.pix.isNull():
            # 裁成格子的比例（置中）
            pw, ph = self.pix.width(), self.pix.height()
            k = max(box.width() / pw, box.height() / ph)
            sw, sh = box.width() / k, box.height() / k
            src = QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(box, self.pix, src)
            p.restore()
        if self.isChecked():
            p.setPen(QPen(theme.c("accent"), 2.5))
            p.drawPath(theme.round_rect(QPainterPath(), box.adjusted(-1.5, -1.5, 1.5, 1.5), 8))
        elif self.underMouse():
            p.setPen(QPen(theme.c("tertiary"), 1.2))
            p.drawPath(path)
        f = theme.font("caption", 600 if self.isChecked() else 400)
        p.setFont(f)
        p.setPen(theme.c("label" if self.isChecked() else "secondary"))
        fm = QFontMetrics(f)
        p.drawText(QRectF(0, self.THUMB_H + 6, self.W, self.H - self.THUMB_H - 6),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.W - 4))
        p.end()


class ControlPad(QWidget):
    """方形控制板：上下是「色調」、左右是「色彩」，各 -100..100。拖曳圓點，雙擊回到中間。"""
    changed = Signal(float, float)       # tone, color
    released = Signal()
    SIZE = 188
    DOTS = 11

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tone = 0.0
        self.color = 0.0
        self._drag = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("上下：色調（往上陰影變亮、往下陰影變深）\n左右：色彩（往左淡、往右濃）\n雙擊回到中間")

    def sizeHint(self):
        return QSize(self.SIZE, self.SIZE)

    def set_values(self, tone, color):
        self.tone, self.color = tone, color
        self.update()

    def _area(self) -> QRectF:
        return QRectF(14, 14, self.SIZE - 28, self.SIZE - 28)

    def _point(self) -> QPointF:
        a = self._area()
        return QPointF(a.center().x() + self.color / 100 * a.width() / 2,
                       a.center().y() - self.tone / 100 * a.height() / 2)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = theme.round_rect(QPainterPath(), r, 14)
        p.fillPath(bg, theme.c("content"))
        # 左淡右濃、上亮下深：淡淡的兩層漸層提示方向
        g1 = QLinearGradient(r.left(), 0, r.right(), 0)
        g1.setColorAt(0, QColor(140, 140, 140, 40))
        g1.setColorAt(1, QColor(255, 120, 60, 55))
        p.fillPath(bg, g1)
        g2 = QLinearGradient(0, r.top(), 0, r.bottom())
        g2.setColorAt(0, QColor(255, 255, 255, 38))
        g2.setColorAt(1, QColor(0, 0, 0, 60))
        p.fillPath(bg, g2)
        p.setPen(theme.c("separator_hex"))
        p.drawPath(bg)
        a = self._area()
        # 點陣（跟 iPhone 的控制板一樣）
        p.setPen(Qt.PenStyle.NoPen)
        dot = theme.c("tertiary")
        dot.setAlphaF(0.55)
        p.setBrush(dot)
        n = self.DOTS
        for i in range(n):
            for j in range(n):
                x = a.left() + a.width() * i / (n - 1)
                y = a.top() + a.height() * j / (n - 1)
                rad = 2.0 if (i == n // 2 or j == n // 2) else 1.3
                p.drawEllipse(QPointF(x, y), rad, rad)
        # 邊上的字
        p.setFont(theme.font(11, 600))
        p.setPen(theme.c("secondary"))
        p.drawText(QRectF(0, 1, self.SIZE, 12), Qt.AlignmentFlag.AlignHCenter, "亮")
        p.drawText(QRectF(0, self.SIZE - 13, self.SIZE, 12), Qt.AlignmentFlag.AlignHCenter, "深")
        p.drawText(QRectF(1, 0, 12, self.SIZE), Qt.AlignmentFlag.AlignCenter, "淡")
        p.drawText(QRectF(self.SIZE - 13, 0, 12, self.SIZE), Qt.AlignmentFlag.AlignCenter, "濃")
        # 圓點
        c = self._point()
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(QColor("white"))
        p.drawEllipse(c, 9, 9)
        p.setBrush(theme.c("accent"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c, 4, 4)
        p.end()

    def _set_from(self, pos):
        a = self._area()
        col = (pos.x() - a.center().x()) / (a.width() / 2) * 100
        tone = -(pos.y() - a.center().y()) / (a.height() / 2) * 100
        col = round(max(-100.0, min(100.0, col)))
        tone = round(max(-100.0, min(100.0, tone)))
        if (tone, col) != (self.tone, self.color):
            self.tone, self.color = tone, col
            self.update()
            self.changed.emit(self.tone, self.color)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._set_from(e.position())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.released.emit()

    def mouseDoubleClickEvent(self, e):
        self._drag = False
        self.tone = self.color = 0.0
        self.update()
        self.changed.emit(0.0, 0.0)
        self.released.emit()
