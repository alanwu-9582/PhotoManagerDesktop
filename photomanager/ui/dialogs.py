"""對話框（對應網頁版 js/ui/modal.js 與 js/app/dialog.js）。

HIG 的警示框：粗體標題、一段說明、右下角按鈕 —— 取消在左、主要動作在右；
會動到硬碟檔案的動作用紅色的「破壞性」按鈕，而且預設焦點停在取消上。
"""
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget, QScrollArea

from . import icons, theme
from .widgets import button, label, hbox, vbox


class Sheet(QDialog):
    """共用外框：標題列、內容、底部按鈕列。"""

    def __init__(self, parent, title: str, width=520, height=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        if height:
            self.resize(width, height)
        self._title = label(title, "title2")
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(8)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)
        root.addWidget(self._title)
        root.addLayout(self.body, 1)
        root.addLayout(self.footer)

    def add_footer(self, *widgets, stretch_first=True):
        if stretch_first:
            self.footer.addStretch(1)
        for w in widgets:
            if w is None:
                self.footer.addStretch(1)
            else:
                self.footer.addWidget(w)


class _Alert(QDialog):
    def __init__(self, parent, title, message, tone, confirm_text, cancel_text, show_cancel):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        glyph = QLabel()
        color = theme.T["red"] if tone == "danger" else theme.T["orange"] if tone == "warning" else theme.T["accent"]
        glyph.setPixmap(icons.pixmap("alert" if tone in ("danger", "warning") else "info", color, 30))
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop)
        head = label(title, "headline", wrap=True)
        head.setStyleSheet(f"font-size: {theme.SIZE['title3']}px;")
        msg = label(message, "secondary", wrap=True, selectable=True)
        text_col = vbox(head, msg, spacing=6)
        top = hbox(glyph, text_col, spacing=14)
        top.setAlignment(glyph, Qt.AlignmentFlag.AlignTop)

        ok = button(confirm_text, "destructive" if tone == "danger" and show_cancel else "primary")
        ok.clicked.connect(self.accept)
        btns = hbox(None)
        if show_cancel:
            cancel = button(cancel_text)
            cancel.clicked.connect(self.reject)
            btns.addWidget(cancel)
            btns.addWidget(ok)
            # 會動到檔案的時候，預設停在「取消」—— 按 Enter 不該直接開始搬檔案。
            (cancel if tone == "danger" else ok).setDefault(True)
            (cancel if tone == "danger" else ok).setFocus()
        else:
            btns.addWidget(ok)
            ok.setDefault(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(18)
        lay.addLayout(top)
        lay.addLayout(btns)


def confirm(parent: QWidget, title: str, message: str = "", tone="info",
            confirm_text="確定", cancel_text="取消") -> bool:
    return _Alert(parent, title, message, tone, confirm_text, cancel_text, True).exec() == QDialog.DialogCode.Accepted


def alert(parent: QWidget, title: str, message: str = "", tone="info", confirm_text="好"):
    _Alert(parent, title, message, tone, confirm_text, "", False).exec()


def scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    return area
