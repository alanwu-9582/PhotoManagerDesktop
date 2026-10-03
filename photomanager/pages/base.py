"""頁面的共同外形：大標題 + 右邊的工具列 + 內容。"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget



class Page(QWidget):
    title = ""
    uses_source = False

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 0, 24, 16)
        self.root.setSpacing(12)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(8)
        self.root.addLayout(self.toolbar)

    # 生命週期：頁面只建一次，切過來 / 切走時通知
    def on_show(self):
        pass

    def on_hide(self):
        pass

    def restyle(self):
        """換主題之後需要自己重畫的東西。"""
        self.update()
