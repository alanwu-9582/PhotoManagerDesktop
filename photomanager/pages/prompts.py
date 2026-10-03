"""AI 風格提示詞：把照片轉成各種風格的提示詞，挑一個、一鍵複製，貼到 AI 生圖工具裡用。

提示詞放在 assets/prompts/（見 engine/prompts.py）：每個提示詞一個資料夾，裡面是 prompt.txt 和參考結果圖。
左邊是卡片（有參考圖就顯示第一張），右邊是選到的那一個：參考圖、全文、複製。
"""
from __future__ import annotations


from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QFrame, QLineEdit, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from ..engine import prompts as P
from ..ui import icons, theme
from ..ui.dialogs import scroll
from ..ui.widgets import EmptyState, FlowLayout, button, hbox, label, notify, wrap
from .base import Page

TAG_NAMES = {"poster": "海報", "collage": "拼貼", "geometric": "幾何", "paper": "紙張",
             "watercolor": "水彩", "grid": "方格"}
LANG_NAMES = {"en": "英文", "zh-Hant": "中文"}


def load_thumb(path, edge) -> QPixmap:
    r = QImageReader(str(path))
    r.setAutoTransform(True)
    s = r.size()
    if s.isValid() and max(s.width(), s.height()) > edge * 2:
        k = edge * 2 / max(s.width(), s.height())
        r.setScaledSize(QSize(max(1, round(s.width() * k)), max(1, round(s.height() * k))))
    img = r.read()
    return QPixmap.fromImage(img) if not img.isNull() else QPixmap()


def paint_cover(p: QPainter, box: QRectF, pix: QPixmap | None, radius=8):
    """把圖片裁成框的比例畫進去；沒有圖就畫一個「尚無參考圖」的佔位。"""
    path = theme.round_rect(QPainterPath(), box, radius)
    p.fillPath(path, theme.c("fill"))
    if pix is not None and not pix.isNull():
        pw, ph = pix.width(), pix.height()
        k = max(box.width() / pw, box.height() / ph)
        sw, sh = box.width() / k, box.height() / k
        p.save()
        p.setClipPath(path)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(box, pix, QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh))
        p.restore()
        return
    glyph = icons.pixmap("image", theme.T["tertiary"], 26)
    c = box.center()
    p.drawPixmap(QRectF(c.x() - 13, c.y() - 22, 26, 26), glyph, QRectF(glyph.rect()))
    p.setFont(theme.font("caption"))
    p.setPen(theme.c("tertiary"))
    p.drawText(QRectF(box.x(), c.y() + 8, box.width(), 18), Qt.AlignmentFlag.AlignHCenter, "尚無參考圖")


class PromptCard(QAbstractButton):
    W, H = 232, 222
    THUMB_H = 148

    def __init__(self, prompt: P.Prompt, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.W, self.H)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.pix = load_thumb(prompt.thumbnails[0], self.W) if prompt.thumbnails else None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        card = theme.round_rect(QPainterPath(), r, 12)
        p.fillPath(card, theme.c("content"))
        if self.isChecked():
            p.setPen(QPen(theme.c("accent"), 2))
        else:
            p.setPen(theme.c("tertiary") if self.underMouse() else theme.c("separator_hex"))
        p.drawPath(card)
        paint_cover(p, QRectF(r.x() + 8, r.y() + 8, r.width() - 16, self.THUMB_H), self.pix)
        x, w = r.x() + 12, r.width() - 24
        y = r.y() + self.THUMB_H + 16
        f = theme.font("headline", 600)
        p.setFont(f)
        p.setPen(theme.c("label"))
        p.drawText(QRectF(x, y, w, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(self.prompt.title, Qt.TextElideMode.ElideRight, int(w)))
        f = theme.font("caption")
        p.setFont(f)
        p.setPen(theme.c("secondary"))
        tags = " · ".join([LANG_NAMES.get(self.prompt.language, self.prompt.language)] +
                          [TAG_NAMES.get(t, t) for t in self.prompt.tags])
        p.drawText(QRectF(x, y + 24, w, 16), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(tags, Qt.TextElideMode.ElideRight, int(w)))
        p.end()


class ThumbView(QAbstractButton):
    """右邊的參考圖：點一下用系統的看圖程式打開原圖。"""
    W, H = 150, 112

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.pix = load_thumb(path, self.W)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(path.name)
        self.clicked.connect(lambda: P.open_folder(path))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_cover(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.pix)
        p.end()


class PromptsPage(Page):
    title = "AI 風格提示詞"

    def __init__(self, window):
        super().__init__(window)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜尋標題、標籤或內容")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(self._filter)
        self.count = label("", "secondary")
        for w in (self.search, self.count, None,
                  button("重新整理", None, "refresh", "重新讀取提示詞資料夾（加了參考圖之後按這裡）", self.reload),
                  button("開啟提示詞資料夾", None, "folder", str(P.PROMPT_DIR), lambda: P.open_folder(P.PROMPT_DIR))):
            if w is None:
                self.toolbar.addStretch(1)
            else:
                self.toolbar.addWidget(w)

        self.cards_host = QWidget()
        self.flow = FlowLayout(self.cards_host, spacing=14)
        self.cards_area = scroll(self.cards_host)

        self.detail = QFrame()
        self.detail.setProperty("group", True)
        d = QVBoxLayout(self.detail)
        d.setContentsMargins(18, 16, 18, 16)
        d.setSpacing(10)
        self.d_title = label("", "title2", wrap=True)
        self.d_meta = label("", "secondary", wrap=True)
        self.d_desc = label("", None, wrap=True)
        self.thumbs_host = QWidget()
        self.thumbs = FlowLayout(self.thumbs_host, spacing=8)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(theme.font("callout"))
        self.copy_btn = button("複製提示詞", "primary", "copy", "複製全文，貼到 AI 生圖工具裡", self.copy)
        self.folder_btn = button("開啟這個資料夾", None, "folder", "放參考結果圖的地方", self.open_current)
        self.chars = label("", "caption")
        for w in (self.d_title, self.d_meta, self.d_desc, self.thumbs_host):
            d.addWidget(w)
        d.addWidget(self.text, 1)
        d.addWidget(wrap(hbox(self.chars, None, self.folder_btn, self.copy_btn, spacing=8)))

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self.cards_area)
        self.split.addWidget(self.detail)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([620, 520])
        self.split.setHandleWidth(14)
        self.split.setChildrenCollapsible(False)
        self.empty = EmptyState("copy")
        self.empty.set("還沒有提示詞", "提示詞資料夾是空的。")
        self.root.addWidget(self.split, 1)
        self.root.addWidget(self.empty, 1)
        self.cards: list[PromptCard] = []
        self.current: P.Prompt | None = None
        self.reload()

    # ---------------------------------------------------------------- 資料
    def reload(self):
        keep = self.current.id if self.current else None
        self.flow.clear()
        self.cards = []
        for pr in P.load():
            c = PromptCard(pr)
            c.clicked.connect(lambda _=False, card=c: self.select(card.prompt))
            self.flow.addWidget(c)
            self.cards.append(c)
        has = bool(self.cards)
        self.split.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            target = next((c.prompt for c in self.cards if c.prompt.id == keep), self.cards[0].prompt)
            self.select(target)
        self._filter(self.search.text())

    def on_show(self):
        self.reload()          # 參考圖可能是在外面加的

    def _filter(self, q):
        q = q.strip().lower()
        shown = 0
        for c in self.cards:
            pr = c.prompt
            hay = " ".join([pr.title, pr.description, *pr.tags,
                            *[TAG_NAMES.get(t, "") for t in pr.tags], pr.text()]).lower()
            ok = not q or q in hay
            c.setVisible(ok)
            shown += ok
        self.count.setText(f'{shown} 個提示詞' if not q else f'找到 {shown} 個')
        self.cards_host.updateGeometry()

    def select(self, pr: P.Prompt):
        self.current = pr
        for c in self.cards:
            c.setChecked(c.prompt.id == pr.id)
        self.d_title.setText(pr.title)
        tags = "、".join(TAG_NAMES.get(t, t) for t in pr.tags)
        self.d_meta.setText(f'語言：{LANG_NAMES.get(pr.language, pr.language or "—")}　標籤：{tags or "—"}')
        self.d_desc.setText(pr.description)
        self.thumbs.clear()
        self.thumbs_host.setVisible(bool(pr.thumbnails))
        if pr.thumbnails:
            for t in pr.thumbnails[:8]:
                self.thumbs.addWidget(ThumbView(t))
        text = pr.text()
        self.text.setPlainText(text)
        self.chars.setText(f'{len(text):,} 字')

    # ---------------------------------------------------------------- 動作
    def copy(self):
        if not self.current:
            return
        QGuiApplication.clipboard().setText(self.current.text())
        notify(f'已複製「{self.current.title}」', "success")

    def open_current(self):
        if self.current:
            P.open_folder(self.current.folder)
