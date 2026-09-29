"""編輯類工具共用的外框與「挑一張照片」（對應網頁版 js/tools/kit.js + js/app/photo-picker.js）。

版面一律是「左邊固定大小的照片區、右邊設定」：照片不動，只有右邊那一欄捲動。
三種來源、同一個回呼：拖進照片區、從檔案選、或是從已經載入的那批照片裡挑 ——
剛整理完的那批照片就在手邊，不必再去檔案總管找一次。
"""
from __future__ import annotations

from ..i18n import tr

import os
import re
import threading

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QFileDialog, QFrame, QProgressBar, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from .. import config
from ..engine import image as imgmod
from ..engine.history import history
from ..engine.library import library
from ..state import state
from ..ui import theme
from ..ui.dialogs import Sheet
from ..ui.photogrid import PhotoGrid
from ..ui.widgets import button, hbox, label, notify, vbox
from ..pages.base import Page

IMAGE_FILTER = tr("圖片 (*.jpg *.jpeg *.png *.tif *.tiff *.heic *.heif *.webp *.bmp *.avif)")


def safe_name(name: str) -> str:
    base = os.path.splitext(os.path.basename(name or "photo"))[0]
    return re.sub(r'[\\/:*?"<>|]+', "-", base).strip() or "photo"


class LibraryPicker(Sheet):
    """已載入的照片。網頁版一次最多列 240 張；這裡是虛擬捲動，全部都能挑。"""

    def __init__(self, parent):
        super().__init__(parent, tr('已載入的照片（{0:,}）').format(len(library.photos)), width=820, height=600)
        self.choice = None
        self.grid = PhotoGrid("thumb", 6)
        self.grid.set_photos(library.photos)
        self.grid.photo_clicked.connect(self._pick)
        if state.selected_id:
            self.grid.delegate.selected_id = state.selected_id
            self.grid.scroll_to_photo(state.selected_id, center=True)
        self.body.addWidget(self.grid, 1)
        self.add_footer(button(tr("取消"), on_click=self.reject))

    def _pick(self, photo):
        self.choice = photo
        self.accept()


class Stage(QWidget):
    """照片區：深色底、可以拖照片進來、空的時候點一下就是選照片。"""
    dropped = Signal(str)
    clicked_empty = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Stage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAcceptDrops(True)
        self.empty = True
        self._over = False
        self.setMinimumSize(320, 260)

    def paint_placeholder(self, p: QPainter):
        p.setPen(QColor(255, 255, 255, 150))
        p.setFont(theme.font("title3", 600))
        r = QRectF(self.rect())
        p.drawText(r.adjusted(0, -12, 0, -12), Qt.AlignmentFlag.AlignCenter, tr("選擇照片"))
        p.setFont(theme.font("callout"))
        p.setPen(QColor(255, 255, 255, 100))
        p.drawText(r.adjusted(0, 26, 0, 26), Qt.AlignmentFlag.AlignCenter, tr("點這裡，或把照片拖進來"))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("viewer_bg"))
        p.drawRoundedRect(QRectF(self.rect()), 12, 12)
        if self.empty:
            self.paint_placeholder(p)
        self.paint_content(p)
        if self._over:
            pen = p.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setColor(theme.c("accent"))
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 11, 11)
        p.end()

    def paint_content(self, p: QPainter):
        pass

    def mouseReleaseEvent(self, e):
        if self.empty and e.button() == Qt.MouseButton.LeftButton:
            self.clicked_empty.emit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._over = True
            self.update()

    def dragLeaveEvent(self, _):
        self._over = False
        self.update()

    def dropEvent(self, e):
        self._over = False
        self.update()
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path and imgmod.is_image_file(path):
                e.acceptProposedAction()
                self.dropped.emit(path)
                return
        notify(tr("不是圖片檔"), "warning")


class _Loader(QObject):
    done = Signal(object, object, object, object)   # token, path, QImage | None, error
    export_progress = Signal(object, float, str)    # token, 0..1, 說明
    export_done = Signal(object, object, object, object)   # token, path, QImage | None, error


class ToolPage(Page):
    """工具頁：左邊照片區 + 選照片 + 狀態 + 動作，右邊可捲動的設定。"""
    stage_class = Stage

    def __init__(self, window):
        super().__init__(window)
        self.stage = self.make_stage()
        self.stage.dropped.connect(lambda path: self.take(path, None))
        self.stage.clicked_empty.connect(self.choose_file)
        self.status = label("", "caption")
        self.meta = QWidget()
        self.meta_lay = hbox(self.status, None, spacing=8)
        self.meta.setLayout(self.meta_lay)
        pick = hbox(button(tr("選擇照片"), None, "upload", tr("從檔案選一張（Ctrl+O）"), self.choose_file),
                    button(tr("已載入的照片"), None, "image", tr("從目前這批照片裡挑一張"), self.choose_library),
                    None, spacing=8)
        # 目前這張是別的工具剛存下來的結果時，照片區上面會出現這一條，可以一鍵換回原圖。
        self.result_note = label("", "caption")
        self.revert_btn = button(tr("改回原圖"), "plain", "reset", tr("不要用上一個工具的結果，回到原本的照片"), self.revert)
        self.result_bar = QWidget()
        self.result_bar.setLayout(hbox(self.result_note, None, self.revert_btn, spacing=6))
        self.result_bar.hide()
        # 輸出進度
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.progress_label = label("", "caption")
        self.progress_label.hide()
        self.actions = hbox(self.progress_label, self.progress, None, spacing=8)
        left = QWidget()
        left.setLayout(vbox(self.result_bar, self.stage, pick, self.meta, self.actions, spacing=10))
        left.layout().setStretch(1, 1)

        self.controls = QWidget()
        self.form = QVBoxLayout(self.controls)
        self.form.setContentsMargins(4, 0, 12, 12)
        self.form.setSpacing(12)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.controls)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setMinimumWidth(340)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(area)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([760, 420])
        split.setHandleWidth(16)
        split.setChildrenCollapsible(False)
        self.root.addWidget(split, 1)

        self.path: str | None = None
        self.origin: str | None = None      # 原始照片（讀 EXIF 用；結果檔沒有 EXIF）
        self.photo_id: int | None = None
        self._export_token = None
        self._export_buttons = []
        self._loaded_version = -1
        self._token = None
        self._loader = _Loader()
        self._loader.done.connect(self._on_loaded)
        self._loader.export_progress.connect(self._on_export_progress)
        self._loader.export_done.connect(self._on_export_done)

    def make_stage(self):
        return self.stage_class()

    # ---------------------------------------------------------------- 來源
    def choose_file(self):
        start = os.path.dirname(self.path) if self.path else (library.root or config.settings.get("lastFolder") or "")
        path, _ = QFileDialog.getOpenFileName(self.win, tr("選擇照片"), start, IMAGE_FILTER)
        if path:
            self.take(path, None)

    def choose_library(self):
        if not library.photos:
            notify(tr("尚未載入照片"))
            return
        dlg = LibraryPicker(self.win)
        if dlg.exec() and dlg.choice:
            self.take(dlg.choice.path, dlg.choice.id)

    def take(self, path, photo_id):
        if not imgmod.is_image_file(path):
            notify(tr("不是圖片檔"), "warning")
            return
        # 編輯工具的選圖只在編輯工具之間同步；整理分類的選取維持單向來源。
        state.set_editor_photo(path, photo_id)
        self._loaded_version = state.editor_version
        self.load(path, photo_id)

    def on_show(self):
        """從整理分類／其他編輯工具延續目前的照片（包括其他工具剛存下來的結果）。"""
        if state.editor_photo:
            if self._loaded_version != state.editor_version:
                self._loaded_version = state.editor_version
                ep = state.editor_photo
                if ep.get("image") is not None or ep["path"] != self.path:
                    self.load(ep["path"], ep["photo_id"], ep.get("image"), ep.get("origin"), ep.get("source"))
            return
        photo = library.by_id(state.selected_id) if state.selected_id else None
        if photo and photo.path != self.path:
            self.load(photo.path, photo.id)

    def load(self, path, photo_id, image: QImage | None = None, origin=None, source=None):
        """解碼在背景做；兩千萬畫素的原圖解一次要好幾百毫秒，不能卡住介面。
        image 有給就是別的工具留下的結果（在記憶體裡，品質沒有被 JPEG 再壓一次），直接用。"""
        self.set_status(tr("讀取中…"))
        token = self._token = object()
        self.path = path
        self.origin = origin or path
        self.photo_id = photo_id
        self.result_note.setText(tr('正在編輯「{0}」的結果').format(source) if source else "")
        self.result_bar.setVisible(bool(source))
        if image is not None:
            self._loader.done.emit(token, path, image, None)
            return

        def run():
            try:
                img = imgmod.decode(path)
                self._loader.done.emit(token, path, img, None)
            except Exception as e:  # noqa: BLE001
                self._loader.done.emit(token, path, None, e)

        threading.Thread(target=run, daemon=True).start()

    def revert(self):
        """不用上一個工具的結果，換回原始照片。"""
        if self.origin:
            self.take(self.origin, self.photo_id)

    def _on_loaded(self, token, path, img, error):
        if token is not self._token:
            return
        if error is not None or img is None or img.isNull():
            self.set_status(tr('讀取失敗: {0}').format(error), "error")
            return
        if img.format() not in (QImage.Format.Format_ARGB32_Premultiplied, QImage.Format.Format_RGB32):
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied if img.hasAlphaChannel()
                                      else QImage.Format.Format_RGB32)
        self.stage.empty = False
        self.on_image(path, img)

    def on_image(self, path: str, img: QImage):
        raise NotImplementedError

    # ---------------------------------------------------------------- 輸出
    def set_status(self, text, tone="ok"):
        self.status.setText(text)
        self.status.setProperty("role", "danger" if tone == "error" else "caption")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def export(self, path, job, quality=92, buttons=()):
        """在背景輸出：job(progress) 回傳 QImage，progress(0..1, 說明) 回報進度；最後寫檔。

        存好之後，這張結果會變成「目前的照片」—— 換到別的編輯工具時就接著編輯它。
        """
        if self._export_token is not None:
            notify(tr("還在輸出上一張"), "warning")
            return
        token = self._export_token = object()
        self._export_buttons = [b for b in buttons if b is not None]
        for b in self._export_buttons:
            b.setEnabled(False)
        self._on_export_progress(token, 0.0, tr("準備中"))
        emit = self._loader.export_progress.emit

        def run():
            try:
                img = job(lambda f, text: emit(token, float(f), text))
                emit(token, 0.9, tr("寫入檔案"))
                imgmod.save_image(img, path, quality)
                emit(token, 1.0, tr("完成"))
                self._loader.export_done.emit(token, path, img, None)
            except Exception as e:  # noqa: BLE001
                self._loader.export_done.emit(token, path, None, e)

        threading.Thread(target=run, daemon=True).start()

    def _on_export_progress(self, token, frac, text):
        if token is not self._export_token:
            return
        pct = round(frac * 100)
        self.progress.setValue(round(frac * 1000))
        self.progress.show()
        self.progress_label.setText(tr('輸出中：{0}… {1}%').format(text, pct))
        self.progress_label.show()
        library.progress.emit(pct, 100, tr('{0}輸出').format(self.title))

    def _on_export_done(self, token, path, img, error):
        if token is not self._export_token:
            return
        self._export_token = None
        for b in self._export_buttons:
            b.setEnabled(True)
        self.progress.hide()
        self.progress_label.hide()
        library.progress_done.emit()
        if error is not None or img is None:
            self.set_status(tr('輸出失敗: {0}').format(error), "error")
            return
        self.set_status(tr('已儲存 {0}×{1} · {2}').format(img.width(), img.height(), os.path.basename(path)))
        # 暫存結果：其他編輯工具會拿它接著編輯。自己這一頁不重新載入，設定照樣留著可以再調。
        state.set_editor_photo(path, self.photo_id, image=img, origin=self.origin, source=self.title)
        history.add("export", f"{self.title}：{os.path.basename(path)}",
                    [(os.path.basename(self.origin or path), f"→ {path}（{img.width()}×{img.height()}）")])
        self._loaded_version = state.editor_version
        notify(tr("已儲存；切到其他編輯工具會接著用這張"), "success")

    def ask_save(self, default_name: str, png_only=False):
        start = os.path.dirname(self.path) if self.path else ""
        filt = "PNG (*.png)" if png_only else "JPEG (*.jpg);;PNG (*.png)"
        if not png_only and default_name.lower().endswith(".png"):
            filt = "PNG (*.png);;JPEG (*.jpg)"
        path, _ = QFileDialog.getSaveFileName(self.win, tr("儲存"), os.path.join(start, default_name), filt)
        return path or None
