"""AI 風格提示詞：把照片轉成各種風格的提示詞，挑一個、一鍵複製，貼到 AI 生圖工具裡用。

內建的提示詞在 assets/prompts/，在這裡上傳的放在設定資料夾（見 engine/prompts.py）。
左邊是卡片（顯示第一組範例的成果圖），右邊是選到的那一個：範例（原圖 → 成果）、全文、複製。
「上傳提示詞」新增一個、「新增範例」幫選到的那個加一組原圖與成果圖；上傳的圖片都會先壓縮。
"""
from __future__ import annotations


from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFileDialog, QFrame, QLineEdit, QPlainTextEdit, QSplitter,
                               QVBoxLayout, QWidget)

from ..engine import prompts as P
from ..ui import icons, theme
from ..ui import dialogs
from ..ui.dialogs import Sheet, scroll
from ..ui.widgets import EmptyState, FlowLayout, button, field, hbox, icon_button, label, notify, wrap
from .base import Page

TAG_NAMES = {"poster": "海報", "collage": "拼貼", "geometric": "幾何", "paper": "紙張",
             "watercolor": "水彩", "grid": "方格"}
LANG_NAMES = {"en": "英文", "zh-Hant": "中文"}
IMAGE_FILTER = "圖片 (*.jpg *.jpeg *.png *.webp *.heic *.heif *.avif *.bmp *.gif)"


def tag_line(pr, sep):
    parts = [LANG_NAMES.get(pr.language, pr.language)] + [TAG_NAMES.get(t, t) for t in pr.tags]
    return sep.join(x for x in parts if x)


class ImageSlot(QAbstractButton):
    """選一張圖片的格子：空的時候是虛線框，選了就顯示預覽；點一下（再）選。"""
    changed = Signal()
    W, H = 220, 150

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.path: str | None = None
        self.pix: QPixmap | None = None
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setToolTip(f"選擇{title}（會壓縮成長邊 {P.UPLOAD_EDGE}、JPEG）")
        self.clicked.connect(self.pick)

    def pick(self):
        path, _ = QFileDialog.getOpenFileName(self, f"選擇{self.title}", "", IMAGE_FILTER)
        if path:
            self.path = path
            self.pix = load_thumb(path, self.W)
            self.update()
            self.changed.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.pix is not None and not self.pix.isNull():
            paint_cover(p, r, self.pix, 8)
        else:
            path = theme.round_rect(QPainterPath(), r, 8)
            p.fillPath(path, theme.c("fill") if self.underMouse() else theme.c("content"))
            pen = QPen(theme.c("tertiary"), 1.2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(path)
            glyph = icons.pixmap("upload", theme.T["secondary"], 20)
            c = r.center()
            p.drawPixmap(QRectF(c.x() - 10, c.y() - 20, 20, 20), glyph, QRectF(glyph.rect()))
            p.setFont(theme.font("callout", 600))
            p.setPen(theme.c("secondary"))
            p.drawText(QRectF(r.x(), c.y() + 6, r.width(), 20), Qt.AlignmentFlag.AlignHCenter, f"選擇{self.title}")
        p.end()


def slot_pair(before: ImageSlot, after: ImageSlot) -> QWidget:
    """原圖 → 成果圖 兩格並排。"""
    arrow = QWidget()
    arrow.setFixedSize(26, ImageSlot.H)

    def paint(_e, w=arrow):
        p = QPainter(w)
        pm = icons.pixmap("arrow-right", theme.T["tertiary"], 16)
        p.drawPixmap(QRectF(5, (w.height() - 16) / 2, 16, 16), pm, QRectF(pm.rect()))
        p.end()
    arrow.paintEvent = paint
    return wrap(hbox(field("原圖", before), arrow, field("成果圖", after), None, spacing=6))


class ExampleDialog(Sheet):
    """幫提示詞新增一組範例：原圖與成果圖都要有。"""

    def __init__(self, parent):
        super().__init__(parent, "新增範例", width=520)
        self.before = ImageSlot("原圖")
        self.after = ImageSlot("成果圖")
        self.body.addWidget(slot_pair(self.before, self.after))
        self.ok = button("新增", "primary", on_click=self.accept)
        self.ok.setDefault(True)
        self.add_footer(button("取消", on_click=self.reject), self.ok)
        for s in (self.before, self.after):
            s.changed.connect(self._sync)
        self._sync()

    def _sync(self):
        self.ok.setEnabled(bool(self.before.path and self.after.path))

    def values(self):
        return self.before.path, self.after.path


class UploadDialog(Sheet):
    """上傳一個新的提示詞：標題、標籤、本文（貼上或讀 .txt）、一組範例（原圖 → 成果圖，選填但要成對）。"""

    def __init__(self, parent):
        super().__init__(parent, "上傳提示詞", width=560, height=660)
        self.title = QLineEdit()
        self.title.setPlaceholderText("標題")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("標籤，用逗號分開")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("貼上提示詞，或從 .txt 讀取")
        load_txt = button("從 .txt 讀取…", None, "upload", on_click=self.load_txt)
        self.before = ImageSlot("原圖")
        self.after = ImageSlot("成果圖")
        self.body.addWidget(field("標題", self.title))
        self.body.addWidget(field("標籤", self.tags))
        self.body.addWidget(field("提示詞", self.text), 1)
        self.body.addWidget(wrap(hbox(load_txt, None)))
        self.body.addWidget(slot_pair(self.before, self.after))
        for s in (self.before, self.after):
            s.changed.connect(self._sync)
        self.ok = button("上傳", "primary", on_click=self.accept)
        self.ok.setDefault(True)
        self.add_footer(button("取消", on_click=self.reject), self.ok)
        self.text.textChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        # 範例可以不附，但附了就要原圖、成果圖都有
        paired = bool(self.before.path) == bool(self.after.path)
        self.ok.setEnabled(bool(self.text.toPlainText().strip()) and paired)

    def load_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇提示詞檔", "", "文字檔 (*.txt *.md);;所有檔案 (*)")
        if not path:
            return
        try:
            raw = open(path, "rb").read()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp950", errors="replace")      # 舊的 Big5 文字檔
        except OSError as e:
            dialogs.alert(self, "讀取失敗", str(e), tone="danger")
            return
        self.text.setPlainText(text)
        if not self.title.text().strip():
            import os
            self.title.setText(os.path.splitext(os.path.basename(path))[0])

    def values(self):
        tags = [t.strip() for t in self.tags.text().replace("，", ",").split(",")]
        return self.title.text(), self.text.toPlainText(), tags, self.before.path, self.after.path


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
    p.drawText(QRectF(box.x(), c.y() + 8, box.width(), 18), Qt.AlignmentFlag.AlignHCenter, "尚無範例")


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
        self.pix = load_thumb(prompt.cover, self.W) if prompt.cover else None

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
        tags = tag_line(self.prompt, " · ")
        p.drawText(QRectF(x, y + 24, w, 16), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(tags, Qt.TextElideMode.ElideRight, int(w)))
        p.end()


class ExampleView(QWidget):
    """一組範例：原圖 → 成果圖。點圖片用系統的看圖程式打開；上傳的可以刪。"""
    TW, TH = 128, 96
    removed = Signal(object)

    def __init__(self, ex: P.Example, parent=None):
        super().__init__(parent)
        self.ex = ex
        self.pix = {k: (load_thumb(getattr(ex, k), self.TW) if getattr(ex, k) else None) for k in ("before", "after")}
        self.setFixedSize(self.TW * 2 + 26, self.TH + 22)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if ex.removable:
            self.del_btn = icon_button("x", "刪除這組範例", lambda: self.removed.emit(self.ex), size=12)
            self.del_btn.setParent(self)
            self.del_btn.setStyleSheet("QToolButton { background: rgba(0,0,0,0.55); border-radius: 6px; }")
            self.del_btn.move(self.width() - 28, 4)
            self.del_btn.hide()

    def _rects(self):
        return {"before": QRectF(0, 0, self.TW, self.TH), "after": QRectF(self.TW + 26, 0, self.TW, self.TH)}

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for k, r in self._rects().items():
            if getattr(self.ex, k):
                paint_cover(p, r.adjusted(0.5, 0.5, -0.5, -0.5), self.pix[k])
            else:
                path = theme.round_rect(QPainterPath(), r.adjusted(0.5, 0.5, -0.5, -0.5), 8)
                p.setPen(QPen(theme.c("tertiary"), 1, Qt.PenStyle.DashLine))
                p.drawPath(path)
            p.setFont(theme.font("caption"))
            p.setPen(theme.c("secondary"))
            p.drawText(QRectF(r.x(), self.TH + 4, r.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                       "原圖" if k == "before" else "成果")
        pm = icons.pixmap("arrow-right", theme.T["tertiary"], 14)
        p.drawPixmap(QRectF(self.TW + 6, (self.TH - 14) / 2, 14, 14), pm, QRectF(pm.rect()))
        p.end()

    def enterEvent(self, _):
        if self.ex.removable:
            self.del_btn.show()
            self.del_btn.raise_()

    def leaveEvent(self, _):
        if self.ex.removable:
            self.del_btn.hide()

    def mouseReleaseEvent(self, e):
        for k, r in self._rects().items():
            if r.contains(QPointF(e.position())) and getattr(self.ex, k):
                P.open_folder(getattr(self.ex, k))
                return


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
                  button("上傳提示詞…", "primary", "upload", "新增一個提示詞（可以附一組原圖與成果圖）", self.upload),
                  button("重新整理", None, "refresh", "重新讀取提示詞資料夾（在外面加了圖片之後按這裡）", self.reload),
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
        self.folder_btn = button("開啟這個資料夾", None, "folder", "放範例圖片的地方", self.open_current)
        self.add_img_btn = button("新增範例…", None, "image", "幫這個提示詞加一組原圖與成果圖（會先壓縮）",
                                  self.add_example)
        self.delete_btn = icon_button("trash", "刪除這個提示詞", self.delete_current, size=15)
        self.chars = label("", "caption")
        for w in (self.d_title, self.d_meta, self.d_desc, self.thumbs_host):
            d.addWidget(w)
        d.addWidget(self.text, 1)
        d.addWidget(wrap(hbox(self.chars, None, self.delete_btn, self.folder_btn, self.add_img_btn, self.copy_btn,
                              spacing=8)))

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
        self.d_meta.setText(tag_line(pr, "　·　") + ("　·　已上傳" if pr.user else ""))
        self.d_meta.setVisible(bool(self.d_meta.text()))
        self.d_desc.setText(pr.description)
        self.d_desc.setVisible(bool(pr.description))
        self.delete_btn.setVisible(pr.user)
        self.thumbs.clear()
        self.thumbs_host.setVisible(bool(pr.examples))
        for ex in pr.examples[:8]:
            v = ExampleView(ex)
            v.removed.connect(self.remove_example)
            self.thumbs.addWidget(v)
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
            # 內建的提示詞：有上傳過參考圖就開放那些圖的資料夾，不然開內建的資料夾
            target = self.current.image_dir if self.current.image_dir.exists() and not self.current.user else self.current.folder
            P.open_folder(target)

    def upload(self):
        dlg = UploadDialog(self.win)
        if not dlg.exec():
            return
        title, text, tags, before, after = dlg.values()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            pid = P.add_prompt(title, text, tags, before, after)
        except OSError as e:
            QApplication.restoreOverrideCursor()
            dialogs.alert(self.win, "上傳失敗", str(e), tone="danger")
            return
        QApplication.restoreOverrideCursor()
        self.current = None
        self.reload()
        card = next((c for c in self.cards if c.prompt.id == pid), None)
        if card:
            self.select(card.prompt)
        notify(f"已上傳「{card.prompt.title if card else title}」", "success")

    def add_example(self):
        if not self.current:
            return
        dlg = ExampleDialog(self.win)
        if not dlg.exec():
            return
        before, after = dlg.values()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            P.add_example(self.current.image_dir, before, after)
        except OSError as e:
            QApplication.restoreOverrideCursor()
            dialogs.alert(self.win, "上傳失敗", str(e), tone="danger")
            return
        QApplication.restoreOverrideCursor()
        self.reload()
        notify("已新增範例", "success")

    def remove_example(self, ex):
        if not dialogs.confirm(self.win, "刪除這組範例？", "原圖與成果圖都會刪除。", tone="danger", confirm_text="刪除"):
            return
        P.delete_example(ex)
        self.reload()

    def delete_current(self):
        pr = self.current
        if not pr or not pr.user:
            return
        if not dialogs.confirm(self.win, f"刪除「{pr.title}」？", "提示詞與它的範例都會刪除。", tone="danger",
                               confirm_text="刪除"):
            return
        P.delete_prompt(pr)
        self.current = None
        self.reload()
        notify("已刪除", "success")
