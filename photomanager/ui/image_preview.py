"""程式內的圖片預覽視窗：看一組（或好幾組）原圖 → 成果，不用開系統的相片程式。

  滾輪縮放、放大後拖曳、雙擊切換符合視窗（跟編輯工具的照片區一樣）
  原圖 / 成果 切換；看成果時按住空白鍵暫時看原圖
  ← → 換上一組 / 下一組，Esc 關閉
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QVBoxLayout

from . import theme
from .widgets import Segmented, hbox, icon_button, label

MAX_EDGE = 2800


def load_full(path) -> QPixmap:
    r = QImageReader(str(path))
    r.setAutoTransform(True)
    s = r.size()
    if s.isValid() and max(s.width(), s.height()) > MAX_EDGE:
        k = MAX_EDGE / max(s.width(), s.height())
        r.setScaledSize(QSize(round(s.width() * k), round(s.height() * k)))
    img = r.read()
    return QPixmap.fromImage(img) if not img.isNull() else QPixmap()


class ImagePreview(QDialog):
    """examples：[(原圖路徑 | None, 成果路徑 | None), ...]；index、kind（"before" / "after"）是一開始看哪一張。"""

    def __init__(self, parent, title, examples, index=0, kind="after"):
        super().__init__(parent)
        from ..tools.common import Stage
        self.setWindowTitle(title)
        self.setModal(True)
        if parent is not None:
            g = parent.window().geometry()
            self.resize(int(g.width() * 0.82), int(g.height() * 0.86))
        self.examples = examples
        self.i = index
        self.kind = kind
        self._cache = {}

        class View(Stage):
            def paint_content(self, p):
                if self.pix is not None and not self.pix.isNull():
                    self.paint_photo(p, self.pix)

        self.view = View()
        self.view.setAcceptDrops(False)
        self.view.empty = False
        self.view.enable_zoom()
        self.title = label(title, "title2")
        self.counter = label("", "secondary")
        self.which = Segmented([("before", "原圖"), ("after", "成果")], kind, compact=True)
        self.which.changed.connect(self._set_kind)
        self.prev_btn = icon_button("chevron-left", "上一組（←）", lambda: self.step(-1), size=15)
        self.next_btn = icon_button("chevron-right", "下一組（→）", lambda: self.step(1), size=15)
        top = hbox(self.title, None, self.which, self.counter, self.prev_btn, self.next_btn, spacing=8)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 18)
        lay.setSpacing(12)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        for key, d in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            QShortcut(QKeySequence(key), self, activated=lambda d=d: self.step(d))
        self.setStyleSheet(f"QDialog {{ background: {theme.T['window']}; }}")
        self._show()

    def _pix(self, path):
        if path is None:
            return None
        key = str(path)
        if key not in self._cache:
            self._cache[key] = load_full(path)
        return self._cache[key]

    def _show(self):
        before, after = self.examples[self.i]
        has_both = before is not None and after is not None
        if self.kind == "after" and after is None:
            self.kind = "before"
        if self.kind == "before" and before is None:
            self.kind = "after"
        self.which.setVisible(has_both)
        self.which.setValue(self.kind)
        n = len(self.examples)
        self.counter.setText(f"{self.i + 1} / {n}" if n > 1 else "")
        for b in (self.prev_btn, self.next_btn):
            b.setVisible(n > 1)
        shown = after if self.kind == "after" else before
        self.view.pix = self._pix(shown)
        # 看成果時按住空白鍵暫時換成原圖
        self.view.original = self._pix(before) if (self.kind == "after" and before is not None) else None
        self.view.reset_view()

    def _set_kind(self, kind):
        self.kind = kind
        self._show()

    def step(self, d):
        n = len(self.examples)
        if n > 1:
            self.i = (self.i + d) % n
            self._show()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            if self.view.original is not None:
                self.view.comparing = True
                self.view.update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self.view.comparing = False
            self.view.update()
            return
        super().keyReleaseEvent(e)
