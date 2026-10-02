"""共用的小元件（對應網頁版 js/tools/kit.js 與 css/components.css）。

照 HIG 的控制項長相：分段控制（segmented control）、膠囊標籤、實心主要按鈕、
灰底次要按鈕、HUD 樣式的通知。顏色一律讀 theme.T，不寫死。
"""
from __future__ import annotations

from ..i18n import tr

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QTimer, Signal, Property)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics
from PySide6.QtWidgets import (QAbstractButton, QColorDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLayout, QPushButton, QSizePolicy, QSlider, QToolButton, QVBoxLayout,
                               QWidget, QWidgetItem, QStyle, QStyleOptionSlider, QGraphicsOpacityEffect)

from . import icons, theme


# ============================================================ 基本
def label(text="", role: str | None = None, wrap=False, selectable=False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def button(text="", kind: str | None = None, icon: str | None = None, tip: str | None = None,
           on_click=None, icon_color: str | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if kind:
        b.setProperty("kind", kind)
    if icon:
        color = icon_color or (theme.T["on_accent"] if kind in ("primary", "destructive")
                               else theme.T["accent"] if kind == "plain" else theme.T["label"])
        b.setIcon(icons.icon(icon, color, 16))
        b.setIconSize(QSize(15, 15))
        b._icon_name = icon  # noqa: SLF001 - 換主題時重新上色
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    return b


def icon_button(name: str, tip: str, on_click=None, size=16, checkable=False) -> QToolButton:
    b = QToolButton()
    b.setProperty("kind", "icon")
    b.setIcon(icons.icon(name, theme.T["label"], size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setAutoRaise(True)
    b._icon_name = name  # noqa: SLF001
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    return b


def hline() -> QFrame:
    f = QFrame()
    f.setProperty("hairline", True)
    return f


def vline() -> QFrame:
    f = QFrame()
    f.setProperty("vline", True)
    f.setFixedHeight(18)
    return f


def hbox(*items, spacing=8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def vbox(*items, spacing=8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        _add(lay, it)
    return lay


def _add(lay, it):
    if it is None:
        lay.addStretch(1)
    elif isinstance(it, int):
        lay.addSpacing(it)
    elif isinstance(it, QLayout):
        lay.addLayout(it)
    else:
        lay.addWidget(it)


def wrap(layout: QLayout, obj_name: str | None = None) -> QWidget:
    w = QWidget()
    if obj_name:
        w.setObjectName(obj_name)
    w.setLayout(layout)
    return w


def refresh_icons(root: QWidget):
    """換主題之後把按鈕上的圖示重新上色。"""
    from PySide6.QtWidgets import QAbstractButton
    for b in root.findChildren(QAbstractButton):
        name = getattr(b, "_icon_name", None)
        if not name:
            continue
        kind = b.property("kind")
        color = getattr(b, "_icon_color", None) or (
            theme.T["on_accent"] if kind in ("primary", "destructive")
            else theme.T["accent"] if kind == "plain" else theme.T["label"])
        b.setIcon(icons.icon(name, color, b.iconSize().width()))


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# ============================================================ 分段控制
class Segmented(QWidget):
    """HIG 的分段控制：灰色軌道，選中的那一段是一塊浮起來的白底。"""
    changed = Signal(str)

    def __init__(self, options, value=None, parent=None, compact=False):
        super().__init__(parent)
        self._options = [(o[0], o[1]) for o in options]
        self._value = value if value is not None else (self._options[0][0] if self._options else None)
        self._hover = -1
        self._compact = compact
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(theme.CONTROL_H)

    def value(self):
        return self._value

    def setValue(self, v, emit=False):
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.changed.emit(v)

    def setEnabled(self, on):  # noqa: N802
        super().setEnabled(on)
        self.update()

    def _widths(self):
        fm = QFontMetrics(self.font())
        pad = 20 if self._compact else 26
        return [fm.horizontalAdvance(lb) + pad for _, lb in self._options]

    def sizeHint(self):
        return QSize(sum(self._widths()) + 4, self.height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def _rects(self):
        ws = self._widths()
        total = sum(ws)
        avail = self.width() - 4
        k = avail / total if total else 1
        x = 2.0
        out = []
        for w in ws:
            out.append(QRectF(x, 2, w * k, self.height() - 4))
            x += w * k
        return out

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        theme.round_rect(path, r, 7)
        p.fillPath(path, theme.c("fill"))
        rects = self._rects()
        sel = next((i for i, (v, _) in enumerate(self._options) if v == self._value), -1)
        for i, rr in enumerate(rects):
            if i == sel:
                seg = QPainterPath()
                theme.round_rect(seg, rr.adjusted(0.5, 0.5, -0.5, -0.5), 5.5)
                p.fillPath(seg.translated(0, 0.6), QColor(0, 0, 0, 30))
                p.fillPath(seg, QColor("#636366") if theme.IS_DARK else QColor("#ffffff"))
            elif i == self._hover and self.isEnabled():
                seg = QPainterPath()
                theme.round_rect(seg, rr.adjusted(1, 1, -1, -1), 5)
                p.fillPath(seg, theme.c("fill"))
            # 段與段之間的細分隔線（選中的那段兩側不畫）
            if 0 < i and sel not in (i, i - 1):
                p.setPen(theme.c("separator"))
                p.drawLine(int(rr.left()), int(rr.top() + 5), int(rr.left()), int(rr.bottom() - 5))
        for i, rr in enumerate(rects):
            f = self.font()
            f.setWeight(theme.QFont.Weight.DemiBold if i == sel else theme.QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(theme.c("label") if self.isEnabled() else theme.c("tertiary"))
            p.drawText(rr, Qt.AlignmentFlag.AlignCenter, self._options[i][1])
        p.end()

    def _hit(self, pos):
        for i, rr in enumerate(self._rects()):
            if rr.contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, _):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if i >= 0 and self.isEnabled():
            self.setValue(self._options[i][0], emit=True)

    def keyPressEvent(self, e):
        idx = next((i for i, (v, _) in enumerate(self._options) if v == self._value), 0)
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            idx = max(0, min(len(self._options) - 1, idx + (1 if e.key() == Qt.Key.Key_Right else -1)))
            self.setValue(self._options[idx][0], emit=True)
        else:
            super().keyPressEvent(e)


# ============================================================ 滑桿 + 數值
class JumpSlider(QSlider):
    """點軌道的任何地方就直接跳到那裡（Qt 預設是往那邊跳一個 pageStep），而且可以接著拖。"""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                                 QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(e.position().toPoint()):
                groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                                     QStyle.SubControl.SC_SliderGroove, self)
                span = max(1, groove.width() - handle.width())
                x = int(e.position().x() - groove.x() - handle.width() / 2)
                self.jumping = True          # 跳過去的這一下還在按著，不算「放開」
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span,
                                                             opt.upsideDown))
                self.jumping = False
        super().mousePressEvent(e)     # 把手現在就在游標下面，接下來的移動就是拖曳


class SliderField(QWidget):
    """標題 + 目前的值 + 滑桿。值可以是小數（內部用整數刻度換算）。

    changed：值一變就發（拖曳中也會）；committed：放開滑桿、或用點的 / 鍵盤改值時才發 ——
    算得很慢的效果接 committed，拖曳時就不會一直重算。
    """
    changed = Signal(float)
    committed = Signal(float)

    def __init__(self, title, lo, hi, step=1.0, value=0.0, fmt=None, parent=None):
        super().__init__(parent)
        self._step = step
        self._lo = lo
        self._fmt = fmt or (lambda v: f"{v:g}")
        self.title = label(title, "secondary")
        self.out = label("", "mono")
        self.out.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = JumpSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, round((hi - lo) / step))
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.valueChanged.connect(self._on)
        self.slider.sliderReleased.connect(lambda: self.committed.emit(self.value()))
        self.setLayout(vbox(hbox(self.title, None, self.out), self.slider, spacing=4))
        self.set(value)

    def value(self) -> float:
        return round(self._lo + self.slider.value() * self._step, 6)

    def set(self, v, emit=False):
        self.slider.blockSignals(not emit)
        self.slider.setValue(round((v - self._lo) / self._step))
        self.slider.blockSignals(False)
        self.out.setText(self._fmt(self.value()))

    def _on(self, _):
        self.out.setText(self._fmt(self.value()))
        self.changed.emit(self.value())
        if not self.slider.isSliderDown() and not getattr(self.slider, "jumping", False):
            self.committed.emit(self.value())

    def mouseDoubleClickEvent(self, e):
        super().mouseDoubleClickEvent(e)


# ============================================================ 有級的數值
class Stepper(QWidget):
    """一格一格的值（光圈 f 值、每排幾張…）：[‹]  值  [›]。點中間的值會列出全部選項直接挑。

    options：[(值, 顯示文字), ...]；鍵盤左右鍵也可以換。
    """
    changed = Signal(object)
    BTN = 30

    def __init__(self, options, value=None, parent=None, tip=None, expand=False):
        super().__init__(parent)
        self._options = list(options)
        self._expand = expand          # True：跟同一欄的下拉選單一樣撐滿欄寬
        vals = [v for v, _ in self._options]
        self._i = vals.index(value) if value in vals else 0
        self._hover = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(theme.CONTROL_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Fixed)
        if tip:
            self.setToolTip(tip)

    def value(self):
        return self._options[self._i][0]

    def setValue(self, v, emit=False):  # noqa: N802
        vals = [x for x, _ in self._options]
        if v in vals and vals.index(v) != self._i:
            self._i = vals.index(v)
            self.update()
            if emit:
                self.changed.emit(self.value())

    def step(self, d):
        i = max(0, min(len(self._options) - 1, self._i + d))
        if i != self._i:
            self._i = i
            self.update()
            self.changed.emit(self.value())

    def _text_w(self):
        fm = QFontMetrics(theme.font("callout", 600))
        return max(fm.horizontalAdvance(t) for _, t in self._options) + 16

    def sizeHint(self):
        return QSize(self.BTN * 2 + self._text_w(), theme.CONTROL_H)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _part(self, x):
        if x < self.BTN:
            return "dec"
        if x > self.width() - self.BTN:
            return "inc"
        return "menu"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.fillPath(theme.round_rect(QPainterPath(), r, 7), theme.c("fill"))
        on = self.isEnabled()
        for part, rr, glyph, ok in (("dec", QRectF(2, 2, self.BTN - 4, r.height() - 4), "chevron-left", self._i > 0),
                                    ("inc", QRectF(r.width() - self.BTN + 2, 2, self.BTN - 4, r.height() - 4),
                                     "chevron-right", self._i < len(self._options) - 1)):
            if self._hover == part and ok and on:
                p.fillPath(theme.round_rect(QPainterPath(), rr, 5), theme.c("fill_strong"))
            col = theme.T["label"] if ok and on else theme.T["tertiary"]
            pm = icons.pixmap(glyph, col, 13)
            c = rr.center()
            p.drawPixmap(QRectF(c.x() - 6.5, c.y() - 6.5, 13, 13), pm, QRectF(pm.rect()))
        mid = QRectF(self.BTN, 2, r.width() - self.BTN * 2, r.height() - 4)
        seg = theme.round_rect(QPainterPath(), mid, 5.5)
        p.fillPath(seg, QColor("#636366") if theme.IS_DARK else QColor("#ffffff"))
        if self._hover == "menu" and on:
            p.fillPath(seg, theme.c("fill"))
        p.setFont(theme.font("callout", 600))
        p.setPen(theme.c("label") if on else theme.c("tertiary"))
        p.drawText(mid, Qt.AlignmentFlag.AlignCenter, self._options[self._i][1])
        if self.hasFocus():
            pen = QPen(theme.c("accent"), 1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(theme.round_rect(QPainterPath(), r.adjusted(0.75, 0.75, -0.75, -0.75), 7))
        p.end()

    def mouseMoveEvent(self, e):
        h = self._part(e.position().x())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        part = self._part(e.position().x())
        if part == "dec":
            self.step(-1)
        elif part == "inc":
            self.step(1)
        else:
            from PySide6.QtWidgets import QMenu
            menu = QMenu(self)
            for i, (_, text) in enumerate(self._options):
                act = menu.addAction(text)
                act.setCheckable(True)
                act.setChecked(i == self._i)
                act.triggered.connect(lambda _=False, k=i: self.step(k - self._i))
            menu.exec(self.mapToGlobal(QPoint(self.BTN, self.height() + 2)))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.step(-1)
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.step(1)
        else:
            super().keyPressEvent(e)


# ============================================================ 欄位（標題在上）
def field(title: str, widget: QWidget | QLayout) -> QWidget:
    w = QWidget()
    lay = vbox(label(title, "secondary"), widget, spacing=5)
    w.setLayout(lay)
    return w


def row(*widgets, spacing=14) -> QWidget:
    w = QWidget()
    lay = hbox(spacing=spacing)
    for x in widgets:
        lay.addWidget(x, 1)
    w.setLayout(lay)
    return w


def section(title: str) -> QLabel:
    lb = label(title.upper() if title.isascii() else title, "section")
    lb.setContentsMargins(2, 10, 0, 0)
    return lb


# ============================================================ 顏色鈕
class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, value="#000000", parent=None, size=(46, 30), pick=True):
        super().__init__(parent)
        self._value = value
        self.setFixedSize(*size)
        self.setToolTip(value)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if pick:
            self.clicked.connect(self._pick)

    def value(self):
        return self._value

    def set(self, v):
        self._value = v
        self.update()

    def _pick(self):
        col = QColorDialog.getColor(QColor(self._value), self.window(), tr("選擇顏色"))
        if col.isValid():
            self._value = col.name()
            self.update()
            self.changed.emit(self._value)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        theme.round_rect(path, r, 7)
        p.fillPath(path, QColor(self._value))
        p.setPen(theme.c("separator"))
        p.drawPath(path)
        p.end()


# ============================================================ 可切換的膠囊
class Chip(QPushButton):
    def __init__(self, text, checked=False, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda *_: self._paint())
        self._paint()

    def _paint(self):
        t = theme.T
        if self.isChecked():
            css = f"background:{t['accent']}; color:{t['on_accent']}; border-radius: 6px; padding: 5px 12px; font-weight:600;"
        else:
            css = f"background:{t['fill']}; color:{t['label']}; border-radius: 6px; padding: 5px 12px;"
        self.setStyleSheet(f"QPushButton {{{css}}}")


# ============================================================ 帶數字的按鈕
class BadgeButton(QPushButton):
    def __init__(self, text, icon_name=None, parent=None):
        super().__init__(text, parent)
        self._badge = ""
        self._quiet = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self.setIcon(icons.icon(icon_name, theme.T["label"], 15))
            self.setIconSize(QSize(15, 15))
            self._icon_name = icon_name

    def setBadge(self, text, quiet=False):  # noqa: N802
        self._badge = str(text or "")
        self._quiet = quiet
        self.setStyleSheet(f"QPushButton {{ padding-right: {self._badge_w() + 16}px; }}" if self._badge else "")
        self.updateGeometry()
        self.update()

    def _badge_w(self):
        if not self._badge:
            return 0
        f = theme.font("caption", 600)
        return max(18, QFontMetrics(f).horizontalAdvance(self._badge) + 10)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._badge:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self._badge_w()
        r = QRectF(self.width() - w - 8, (self.height() - 18) / 2, w, 18)
        path = QPainterPath()
        theme.round_rect(path, r, 5)
        p.fillPath(path, theme.c("fill_strong") if self._quiet else theme.c("accent"))
        p.setFont(theme.font("caption", 600))
        p.setPen(theme.c("secondary") if self._quiet else QColor("white"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._badge)
        p.end()


# ============================================================ 自動換行排版
class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):  # noqa: N802
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def clear(self):
        while self._items:
            it = self._items.pop()
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()

    def _do(self, r, test):
        x, y, line = r.x(), r.y(), 0
        for it in self._items:
            if it.widget() and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > r.right() and line > 0:
                x = r.x()
                y += line + self._sp
                nx = x + hint.width() + self._sp
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line = max(line, hint.height())
        return y + line - r.y()


# ============================================================ HUD 通知
class Toast(QWidget):
    """HIG 的 HUD：深色半透明膠囊，出現在視窗底部，幾秒後淡出。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text = ""
        self._tone = "info"
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(180)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.hide()

    def show_message(self, text, tone="info", ms=2600):
        self._text = text
        self._tone = tone
        f = theme.font("body", 500)
        w = QFontMetrics(f).horizontalAdvance(text) + 58
        par = self.parentWidget()
        self.setGeometry((par.width() - w) // 2, par.height() - 96, w, 40)
        self.show()
        self.raise_()
        self.update()
        self._anim.stop()
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self):
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        theme.round_rect(path, r, 10)
        p.fillPath(path, QColor(30, 30, 32, 235))
        dot = {"success": theme.T["green"], "warning": theme.T["orange"], "danger": theme.T["red"]}.get(
            self._tone, theme.T["accent"])
        p.setBrush(QColor(dot))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(18, r.center().y() - 4, 8, 8), 2, 2)
        p.setPen(QColor("white"))
        p.setFont(theme.font("body", 500))
        p.drawText(r.adjusted(34, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
        p.end()


_toast: Toast | None = None


def init_toast(window: QWidget):
    global _toast
    _toast = Toast(window)


def notify(text, tone="info"):
    if _toast:
        _toast.show_message(text, tone)


# ============================================================ 空狀態
class EmptyState(QWidget):
    def __init__(self, glyph="image", title=tr("尚未載入照片"), parent=None):
        super().__init__(parent)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setPixmap(icons.pixmap(glyph, theme.T["tertiary"], 44))
        self._glyph = glyph
        self.title = label(title, "title3")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub = label("", "secondary")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setWordWrap(True)
        self.action = None
        self._lay = vbox(None, self.icon, 6, self.title, self.sub, None, spacing=6)
        self.setLayout(self._lay)

    def set(self, title, sub="", action: QWidget | None = None):
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        if action is not self.action:
            if self.action:
                self.action.setParent(None)
            self.action = action
            if action:
                holder = hbox(None, action, None)
                self._lay.insertLayout(5, holder)

    def restyle(self):
        self.icon.setPixmap(icons.pixmap(self._glyph, theme.T["tertiary"], 44))


def shadow(widget: QWidget, blur=24, dy=4, alpha=60):
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)
    return eff


# ============================================================ 開關（照原本 photoManager 的 .tool-flag）
class Flag(QAbstractButton):
    """勾選開關：外面一圈細框，選取時裡面那塊填滿主色，和外框之間留一圈間距。

    跟 QCheckBox 同一套介面（setChecked / isChecked / toggled），所以可以直接替換。
    給 icon_name 就是只有圖示的方形版本（例如翻轉），文字放在提示裡。
    """
    H = 30
    INSET = 3

    def __init__(self, text="", checked=False, icon_name: str | None = None, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.icon_name = icon_name
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if icon_name and text:
            self.setToolTip(text)
        self.toggled.connect(lambda *_: self.update())

    def _font(self):
        return theme.font("callout", 600)

    def sizeHint(self):
        if self.icon_name:
            return QSize(self.H, self.H)
        return QSize(QFontMetrics(self._font()).horizontalAdvance(self.text()) + 2 * (self.INSET + 12), self.H)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on, hover, enabled = self.isChecked(), self.underMouse(), self.isEnabled()
        path = theme.round_rect(QPainterPath(), outer, 6)
        p.fillPath(path, theme.c("content"))
        border = theme.c("tertiary" if hover and enabled else "separator_hex")
        p.setPen(border)
        p.drawPath(path)
        inner = outer.adjusted(self.INSET, self.INSET, -self.INSET, -self.INSET)
        if on:
            fill = theme.c("accent")
            if not enabled:
                fill.setAlphaF(0.4)
            p.fillPath(theme.round_rect(QPainterPath(), inner, 4), fill)
        if on:
            fg = theme.T["on_accent"]
        elif not enabled:
            fg = theme.T["tertiary"]
        else:
            fg = theme.T["label"] if hover else theme.T["secondary"]
        if self.icon_name:
            pm = icons.pixmap(self.icon_name, fg, 17)
            c = inner.center()
            p.drawPixmap(QRectF(c.x() - 8.5, c.y() - 8.5, 17, 17), pm, QRectF(pm.rect()))
        else:
            p.setFont(self._font())
            p.setPen(QColor(fg))
            p.drawText(inner, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            pen = p.pen()
            pen.setColor(theme.c("accent"))
            pen.setWidthF(2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(theme.round_rect(QPainterPath(), outer.adjusted(-1, -1, 1, 1), 7))
        p.end()


# ============================================================ 調整用的一列滑桿（Lightroom 那種）
class ParamSlider(QWidget):
    """一列：名稱、滑桿、數值。雙擊名稱或滑桿回到預設值；有中點的（-100..100）從中間往兩邊填色。

    gradient 給一串顏色就把軌道畫成漸層（色溫、色調、色相那種）。拖曳時發 changed，放開時發 released。
    """
    changed = Signal(float)
    released = Signal()
    H = 30
    LABEL_W = 86
    VALUE_W = 46

    def __init__(self, title, lo, hi, value=0.0, step=1.0, default=None, fmt=None, gradient=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.lo, self.hi, self.step = lo, hi, step
        self.default = value if default is None else default
        self._v = value
        self.fmt = fmt or (lambda v: f"{v:+.0f}" if lo < 0 < hi else f"{v:.0f}")
        self.gradient = gradient
        self._drag = False
        self._hover = False
        self.setFixedHeight(self.H)
        self.setMinimumWidth(220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def value(self):
        return self._v

    def set(self, v, emit=False):
        v = min(self.hi, max(self.lo, round(v / self.step) * self.step))
        if abs(v - self._v) < 1e-9:
            return
        self._v = v
        self.update()
        if emit:
            self.changed.emit(v)

    def set_gradient(self, colors):
        self.gradient = colors
        self.update()

    def _track(self) -> QRectF:
        return QRectF(self.LABEL_W + 8, self.H / 2 - 2, self.width() - self.LABEL_W - self.VALUE_W - 16, 4)

    def _x(self, v):
        t = self._track()
        return t.x() + (v - self.lo) / (self.hi - self.lo) * t.width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        changed = abs(self._v - self.default) > 1e-9
        p.setFont(theme.font("callout", 600 if changed else 400))
        p.setPen(theme.c("label" if changed and enabled else "secondary"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(0, 0, self.LABEL_W, self.H), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.title, Qt.TextElideMode.ElideRight, self.LABEL_W))
        t = self._track()
        track = theme.round_rect(QPainterPath(), t, 2)
        if self.gradient:
            from PySide6.QtGui import QLinearGradient
            g = QLinearGradient(t.left(), 0, t.right(), 0)
            n = len(self.gradient)
            for i, c in enumerate(self.gradient):
                g.setColorAt(i / max(1, n - 1), QColor(c))
            p.fillPath(track, g)
        else:
            p.fillPath(track, theme.c("fill_strong"))
            # 從中點（或最左邊）填到目前的值
            origin = 0 if self.lo < 0 < self.hi else self.lo
            a, b = sorted((self._x(origin), self._x(self._v)))
            if b - a > 0.5:
                fill = theme.c("accent")
                if not enabled:
                    fill.setAlphaF(0.4)
                p.fillPath(theme.round_rect(QPainterPath(), QRectF(a, t.y(), b - a, t.height()), 2), fill)
        if self.lo < 0 < self.hi:
            p.setPen(theme.c("tertiary"))
            x0 = self._x(0)
            p.drawLine(QPointF(x0, t.y() - 4), QPointF(x0, t.y() - 1))
        # 把手
        x = self._x(self._v)
        r = 7 if (self._drag or self._hover) else 6
        p.setPen(QColor(0, 0, 0, 50))
        p.setBrush(theme.c("slider_thumb" if not self._drag else "slider_thumb_hover") if enabled else theme.c("tertiary"))
        p.drawEllipse(QPointF(x, t.center().y()), r, r)
        p.setFont(theme.font("callout", mono=True))
        p.setPen(theme.c("label" if changed else "secondary"))
        p.drawText(QRectF(self.width() - self.VALUE_W, 0, self.VALUE_W, self.H),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.fmt(self._v))
        p.end()

    def _from_x(self, x):
        t = self._track()
        f = min(1.0, max(0.0, (x - t.x()) / max(1.0, t.width())))
        return self.lo + f * (self.hi - self.lo)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        if e.position().x() < self.LABEL_W:
            return
        x = e.position().x()
        on_thumb = abs(x - self._x(self._v)) <= 8
        # 點在軌道上：直接跳到那個位置；按在把手上：保持原值，從這裡開始拖（不會先跳一下）
        self._grab = (x - self._x(self._v)) if on_thumb else 0.0
        self._drag = True
        if not on_thumb:
            self.set(self._from_x(x), emit=True)
        self.update()

    def mouseMoveEvent(self, e):
        over = abs(e.position().x() - self._x(self._v)) <= 10
        if over != self._hover:
            self._hover = over
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if e.position().x() >= self.LABEL_W else Qt.CursorShape.ArrowCursor)
        if self._drag:
            self.set(self._from_x(e.position().x() - getattr(self, "_grab", 0.0)), emit=True)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.update()
            self.released.emit()

    def mouseDoubleClickEvent(self, e):
        self._drag = False
        self.set(self.default, emit=True)
        self.released.emit()

    def leaveEvent(self, _):
        self._hover = False
        self.update()

    def keyPressEvent(self, e):
        big = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.set(self._v - self.step * big, emit=True)
            self.released.emit()
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.set(self._v + self.step * big, emit=True)
            self.released.emit()
        else:
            super().keyPressEvent(e)
