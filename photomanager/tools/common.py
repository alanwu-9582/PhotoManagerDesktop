"""編輯類工具共用的外框與「挑一張照片」（對應網頁版 js/tools/kit.js + js/app/photo-picker.js）。

版面一律是「左邊固定大小的照片區、右邊設定」：照片不動，只有右邊那一欄捲動。
三種來源、同一個回呼：拖進照片區、從檔案選、或是從已經載入的那批照片裡挑 ——
剛整理完的那批照片就在手邊，不必再去檔案總管找一次。

每個工具都有「暫存」與「儲存」：暫存只把原尺寸的結果留在記憶體，切到別的編輯工具就接著編輯它；
儲存才寫成檔案。右邊的設定可以分成幾個分頁（add_tabs）；性質相近的工具可以用 ToolGroup
合成一頁、在右上角切換 —— 以後加新工具，就依種類放進既有的群組，或是自己開一頁。
"""
from __future__ import annotations


import os
import re
import threading

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QFileDialog, QFrame, QLineEdit, QPlainTextEdit,
                               QProgressBar, QScrollArea, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from .. import config
from ..engine import exif as exif_mod
from ..engine import image as imgmod
from ..engine.history import history
from ..engine.library import library
from ..state import state
from ..ui import theme
from ..ui.dialogs import Sheet
from ..ui.photogrid import PhotoGrid
from ..ui.scopes import ScopesOverlay
from ..ui.widgets import Segmented, button, hbox, label, notify, vbox
from ..pages.base import Page

IMAGE_FILTER = "圖片 (*.jpg *.jpeg *.png *.tif *.tiff *.heic *.heif *.webp *.bmp *.avif)"


def safe_name(name: str) -> str:
    base = os.path.splitext(os.path.basename(name or "photo"))[0]
    return re.sub(r'[\\/:*?"<>|]+', "-", base).strip() or "photo"


class LibraryPicker(Sheet):
    """已載入的照片。網頁版一次最多列 240 張；這裡是虛擬捲動，全部都能挑。"""

    def __init__(self, parent):
        super().__init__(parent, f'已載入的照片（{len(library.photos):,}）', width=820, height=600)
        self.choice = None
        self.grid = PhotoGrid("thumb", 6)
        self.grid.set_photos(library.photos)
        self.grid.photo_clicked.connect(self._pick)
        if state.selected_id:
            self.grid.delegate.selected_id = state.selected_id
            self.grid.scroll_to_photo(state.selected_id, center=True)
        self.body.addWidget(self.grid, 1)
        self.add_footer(button("取消", on_click=self.reject))

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
        self.original: QPixmap | None = None   # 按住空白鍵時顯示的原圖（有設才有對照）
        self.comparing = False
        self.scopes: ScopesOverlay | None = None
        self.setMinimumSize(320, 260)

    def shown_pix(self, pix):
        """按住空白鍵比對時換成原圖。"""
        return self.original if self.comparing and self.original is not None else pix

    def place_overlays(self):
        if self.scopes is not None:
            self.scopes.move(12, self.height() - self.scopes.height() - 12)
            self.scopes.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.place_overlays()

    def paint_placeholder(self, p: QPainter):
        p.setPen(QColor(255, 255, 255, 150))
        p.setFont(theme.font("title3", 600))
        r = QRectF(self.rect())
        p.drawText(r.adjusted(0, -12, 0, -12), Qt.AlignmentFlag.AlignCenter, "選擇照片")
        p.setFont(theme.font("callout"))
        p.setPen(QColor(255, 255, 255, 100))
        p.drawText(r.adjusted(0, 26, 0, 26), Qt.AlignmentFlag.AlignCenter, "點這裡，或把照片拖進來")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("viewer_bg"))
        p.drawRoundedRect(QRectF(self.rect()), 12, 12)
        if self.empty:
            self.paint_placeholder(p)
        self.paint_content(p)
        if self.comparing and self.original is not None:
            tag = QRectF((self.width() - 64) / 2, 12, 64, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.fillPath(theme.round_rect(QPainterPath(), tag, 6), QColor(0, 0, 0, 170))
            p.setPen(QColor("white"))
            p.setFont(theme.font("caption", 600))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, "原圖")
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
        notify("不是圖片檔", "warning")


class _Loader(QObject):
    done = Signal(object, object, object, object)   # token, path, QImage | None, error
    export_progress = Signal(object, float, str)    # token, 0..1, 說明
    export_done = Signal(object, object, object, object)   # token, path, QImage | None, error


class _SpaceCompare(QObject):
    """按住空白鍵看原圖、放開回到成品。打字的時候不攔。"""

    def __init__(self, page):
        super().__init__(page)
        self.page = page

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and e.key() == Qt.Key.Key_Space:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
                return False
            if not self.page.isVisible() or QApplication.activeModalWidget() is not None:
                return False
            if not e.isAutoRepeat():
                self.page.set_comparing(t == QEvent.Type.KeyPress)
            return True          # 吃掉，不然焦點上的按鈕會被空白鍵按下去
        if t == QEvent.Type.ApplicationDeactivate:
            self.page.set_comparing(False)
        return False


class ToolPage(Page):
    """工具頁：左邊照片區 + 選照片 + 狀態 + 動作，右邊可捲動的設定。"""
    stage_class = Stage
    scopes = False        # 照片區左下角的「照片參數」圖表
    compare = False       # 按住空白鍵看原圖
    png_only = False

    def __init__(self, window):
        super().__init__(window)
        self.stage = self.make_stage()
        if self.scopes:
            self.stage.scopes = ScopesOverlay(self.stage)
            self.stage.place_overlays()
        self._scope_result = self._scope_orig = None
        self._exif_lines = []
        self._space = _SpaceCompare(self) if self.compare else None
        self.stage.dropped.connect(lambda path: self.take(path, None))
        self.stage.clicked_empty.connect(self.choose_file)
        self.status = label("", "caption")
        self.meta = QWidget()
        self.meta_lay = hbox(self.status, None, spacing=8)
        self.meta.setLayout(self.meta_lay)
        pick = hbox(button("選擇照片", None, "upload", "從檔案選一張（Ctrl+O）", self.choose_file),
                    button("已載入的照片", None, "image", "從目前這批照片裡挑一張", self.choose_library),
                    None, spacing=8)
        # 目前這張是別的工具剛存下來的結果時，照片區上面會出現這一條，可以一鍵換回原圖。
        self.result_note = label("", "caption")
        self.revert_btn = button("改回原圖", "plain", "reset", "不要用上一個工具的結果，回到原本的照片", self.revert)
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
        # 捲軸的位置一直留著：切換分頁時內容長短不同，有沒有捲軸都不會讓寬度跳動。
        # 不需要捲動時把手藏起來（idle），看起來就只是一條空白。
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        sb = area.verticalScrollBar()

        def idle(lo, hi):
            sb.setProperty("idle", hi <= lo)
            sb.style().unpolish(sb)
            sb.style().polish(sb)

        sb.rangeChanged.connect(idle)
        idle(0, 0)
        self.scroll_area = area
        # 右欄最上面的分頁列（不跟著捲動）：ToolGroup 的工具切換、add_tabs 的設定分頁都放這裡
        right = QWidget()
        self.tab_slot = QVBoxLayout()
        self.tab_slot.setContentsMargins(4, 0, 12 + sb.sizeHint().width(), 0)   # 右緣跟下面的設定對齊
        self.tab_slot.setSpacing(8)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addLayout(self.tab_slot)
        rl.addWidget(area, 1)

        self.split = split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
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
        self._export_path = None
        self._export_buttons = []
        self.from_result = False
        self._loaded_version = -1
        self._token = None
        self._loader = _Loader()
        self._loader.done.connect(self._on_loaded)
        self._loader.export_progress.connect(self._on_export_progress)
        self._loader.export_done.connect(self._on_export_done)

    def make_stage(self):
        return self.stage_class()

    # ---------------------------------------------------------------- 版面小工具
    def add_tabs(self, options, value=None):
        """右欄分頁：回傳 {key: 那一頁的 QVBoxLayout}。分頁列固定在上面，內容在下面捲動。"""
        self.tabs = Segmented(options, value or options[0][0])
        self.tab_slot.addWidget(self.tabs)
        self.tab_pages = {}
        out = {}
        for key, _ in options:
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            self.form.addWidget(w)
            self.tab_pages[key] = w
            out[key] = lay
        self.form.addStretch(1)
        self.tabs.changed.connect(self._show_tab)
        self._show_tab(self.tabs.value())
        return out

    def _show_tab(self, key):
        for k, w in self.tab_pages.items():
            w.setVisible(k == key)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.on_tab(key)

    def on_tab(self, key):
        pass

    def add_output_buttons(self, save_tip):
        """「暫存」與「儲存…」兩顆按鈕。"""
        self.stash_btn = button("暫存", None, "layers",
                                "把目前的結果留著（不存檔），切到其他編輯工具會接著編輯（Ctrl+Shift+S）", self.stash)
        self.save_btn = button("儲存…", "primary", "download", save_tip, self.save)
        self.actions.addWidget(self.stash_btn)
        self.actions.addWidget(self.save_btn)

    def panel_min_width(self) -> int:
        """設定欄至少要多寬才放得下：每個分頁都算（藏起來的也算），寬度才不會因為切分頁而改變。"""
        m = self.form.contentsMargins()
        need = self.controls.minimumSizeHint().width()
        for w in getattr(self, "tab_pages", {}).values():
            need = max(need, w.minimumSizeHint().width() + m.left() + m.right())
        return need + self.scroll_area.verticalScrollBar().sizeHint().width() + 2

    def fit_panel(self, width=None):
        self.scroll_area.setMinimumWidth(max(340, width or self.panel_min_width()))

    # ---------------------------------------------------------------- 空白鍵比對、照片參數
    def on_show(self):
        self.fit_panel()
        if self._space is not None:
            QApplication.instance().installEventFilter(self._space)
        self._on_show_photo()

    def on_hide(self):
        if self._space is not None:
            QApplication.instance().removeEventFilter(self._space)
            self.set_comparing(False)

    def set_comparing(self, on):
        on = bool(on) and self.stage.original is not None
        if self.stage.comparing == on:
            return
        self.stage.comparing = on
        self.stage.update()
        self._feed_scopes()

    def update_scopes(self, result, original=None):
        """result / original：QImage 或 H×W×3 uint8。"""
        self._scope_result = result
        if original is not None:
            self._scope_orig = original
        self._feed_scopes()

    def _feed_scopes(self):
        sc = self.stage.scopes
        if sc is None:
            return
        cmp_ = self.stage.comparing and self._scope_orig is not None
        sc.set_image(self._scope_orig if cmp_ else self._scope_result, self._exif_lines,
                     "原圖" if cmp_ else "")

    def _read_exif_lines(self):
        try:
            info, _ = exif_mod.extract_exif(self.origin or self.path)
        except Exception:  # noqa: BLE001
            info = None
        i = info or {}
        lines = [(lb, i.get(k)) for k, lb in (("fNumber", "光圈"), ("exposureTime", "快門"), ("iso", "ISO"),
                                               ("focalLength", "焦段"), ("exposureBias", "曝光補償"),
                                               ("model", "相機"))]
        self._exif_lines = [(k, str(v)) for k, v in lines if v not in (None, "")]

    # ---------------------------------------------------------------- 來源
    def choose_file(self):
        start = os.path.dirname(self.path) if self.path else (library.root or config.settings.get("lastFolder") or "")
        path, _ = QFileDialog.getOpenFileName(self.win, "選擇照片", start, IMAGE_FILTER)
        if path:
            self.take(path, None)

    def choose_library(self):
        if not library.photos:
            notify("尚未載入照片")
            return
        dlg = LibraryPicker(self.win)
        if dlg.exec() and dlg.choice:
            self.take(dlg.choice.path, dlg.choice.id)

    def take(self, path, photo_id):
        if not imgmod.is_image_file(path):
            notify("不是圖片檔", "warning")
            return
        # 編輯工具的選圖只在編輯工具之間同步；整理分類的選取維持單向來源。
        state.set_editor_photo(path, photo_id)
        self._loaded_version = state.editor_version
        self.load(path, photo_id)

    def _on_show_photo(self):
        """從整理分類／其他編輯工具延續目前的照片（包括其他工具剛存下來或暫存的結果）。"""
        if state.editor_photo:
            if self._loaded_version != state.editor_version:
                self._loaded_version = state.editor_version
                ep = state.editor_photo
                if ep.get("image") is not None or ep["path"] != self.path:
                    self.load(ep["path"], ep["photo_id"], ep.get("image"), ep.get("origin"), ep.get("source"),
                              ep.get("stashed", False))
            return
        photo = library.by_id(state.selected_id) if state.selected_id else None
        if photo and photo.path != self.path:
            self.load(photo.path, photo.id)

    def load(self, path, photo_id, image: QImage | None = None, origin=None, source=None, stashed=False):
        """解碼在背景做；兩千萬畫素的原圖解一次要好幾百毫秒，不能卡住介面。
        image 有給就是別的工具留下的結果（在記憶體裡，品質沒有被 JPEG 再壓一次），直接用。"""
        self.set_status("讀取中…")
        token = self._token = object()
        self.path = path
        self.origin = origin or path
        self.photo_id = photo_id
        self.from_result = bool(source)
        note = '正在編輯「{0}」暫存的結果' if stashed else '正在編輯「{0}」的結果'
        self.result_note.setText(note.format(source) if source else "")
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
            self.set_status(f'讀取失敗: {error}', "error")
            return
        if img.format() not in (QImage.Format.Format_ARGB32_Premultiplied, QImage.Format.Format_RGB32):
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied if img.hasAlphaChannel()
                                      else QImage.Format.Format_RGB32)
        self.stage.empty = False
        if self.scopes:
            self._read_exif_lines()
        self.on_image(path, img)

    def on_image(self, path: str, img: QImage):
        raise NotImplementedError

    # ---------------------------------------------------------------- 輸出
    def set_status(self, text, tone="ok"):
        self.status.setText(text)
        self.status.setProperty("role", "danger" if tone == "error" else "caption")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    # 子類別實作 output_job()：回傳 job(progress) → QImage（還沒有照片就回傳 None）
    def output_job(self):
        return None

    def output_name(self) -> str:
        return f"{safe_name(self.path)}_edit.jpg"

    def output_quality(self) -> int:
        q = getattr(self, "quality", None)
        return int(q.value()) if q is not None else 92

    def save(self):
        job = self.output_job()
        if job is None:
            notify("還沒有照片", "warning")
            return
        path = self.ask_save(self.output_name(), png_only=self.png_only)
        if path:
            self.export(path, job, self.output_quality(), self._out_buttons())

    def stash(self):
        """原尺寸算一次、留在記憶體裡，不寫檔。"""
        job = self.output_job()
        if job is None:
            notify("還沒有照片", "warning")
            return
        self.export(None, job, buttons=self._out_buttons())

    def _out_buttons(self):
        return [getattr(self, "save_btn", None), getattr(self, "stash_btn", None)]

    def export(self, path, job, quality=92, buttons=()):
        """在背景輸出：job(progress) 回傳 QImage，progress(0..1, 說明) 回報進度；最後寫檔（path 是 None 就只暫存）。

        做好之後，這張結果會變成「目前的照片」—— 換到別的編輯工具時就接著編輯它。
        """
        if self._export_token is not None:
            notify("還在輸出上一張", "warning")
            return
        token = self._export_token = object()
        self._export_path = path
        self._export_buttons = [b for b in buttons if b is not None]
        for b in self._export_buttons:
            b.setEnabled(False)
        self._on_export_progress(token, 0.0, "準備中")
        loader = self._loader

        def emit(*a):
            # 輸出途中就把程式關了：頁面已經不在，訊號送不出去也沒關係（檔案照樣寫完）
            try:
                loader.export_progress.emit(*a)
            except RuntimeError:
                pass

        def run():
            try:
                img = job(lambda f, text: emit(token, float(f), text))
                if path:
                    emit(token, 0.9, "寫入檔案")
                    imgmod.save_image(img, path, quality)
                emit(token, 1.0, "完成")
                result = (img, None)
            except Exception as e:  # noqa: BLE001
                result = (None, e)
            try:
                loader.export_done.emit(token, path, *result)
            except RuntimeError:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _on_export_progress(self, token, frac, text):
        if token is not self._export_token:
            return
        pct = round(frac * 100)
        self.progress.setValue(round(frac * 1000))
        self.progress.show()
        self.progress_label.setText(('暫存中：{0}… {1}%' if self._export_path is None else
                                     '輸出中：{0}… {1}%').format(text, pct))
        self.progress_label.show()
        library.progress.emit(pct, 100, f'{self.title}輸出')

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
            self.set_status(f'輸出失敗: {error}', "error")
            return
        if path is None:
            self.set_status(f'已暫存 {img.width()}×{img.height()}')
            state.set_editor_photo(self.path, self.photo_id, image=img, origin=self.origin, source=self.title,
                                   stashed=True)
            self._loaded_version = state.editor_version
            notify("已暫存；切到其他編輯工具會接著用這張", "success")
            return
        self.set_status(f'已儲存 {img.width()}×{img.height()} · {os.path.basename(path)}')
        # 暫存結果：其他編輯工具會拿它接著編輯。自己這一頁不重新載入，設定照樣留著可以再調。
        state.set_editor_photo(path, self.photo_id, image=img, origin=self.origin, source=self.title)
        history.add("export", f"{self.title}：{os.path.basename(path)}",
                    [(os.path.basename(self.origin or path), f"→ {path}（{img.width()}×{img.height()}）")])
        self._loaded_version = state.editor_version
        notify("已儲存；切到其他編輯工具會接著用這張", "success")

    def ask_save(self, default_name: str, png_only=False):
        start = os.path.dirname(self.path) if self.path else ""
        filt = "PNG (*.png)" if png_only else "JPEG (*.jpg);;PNG (*.png)"
        if not png_only and default_name.lower().endswith(".png"):
            filt = "PNG (*.png);;JPEG (*.jpg)"
        path, _ = QFileDialog.getSaveFileName(self.win, "儲存", os.path.join(start, default_name), filt)
        return path or None


# ============================================================ 幾個工具合成一頁
class ToolGroup(Page):
    """性質相近的工具合在同一頁：右欄最上面一排切換，照片與暫存結果在它們之間共用。

    members = [(key, 名稱, ToolPage 子類別), ...]
    """
    members: list = []

    def __init__(self, window):
        super().__init__(window)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.root.addWidget(self.stack, 1)
        self.pages = {}
        self.switches = []
        options = [(k, lb) for k, lb, _ in self.members]
        for key, _, cls in self.members:
            page = cls(window)
            sw = Segmented(options, key)
            sw.changed.connect(self.switch)
            page.tab_slot.insertWidget(0, sw)
            self.switches.append(sw)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.current = self.members[0][0]

    def page(self):
        return self.pages[self.current]

    def switch(self, key):
        if key == self.current:
            return
        old = self.page()
        old.on_hide()
        self.current = key
        for sw in self.switches:
            sw.setValue(key)
        # 照片區與設定欄的分界跟著上一個工具走，切換時右欄寬度不會跳
        self.page().split.setSizes(old.split.sizes())
        self.stack.setCurrentWidget(self.page())
        self.page().on_show()
        self._fit()

    def on_show(self):
        self.page().on_show()
        self._fit()

    def _fit(self):
        """群組裡的工具用同一個設定欄寬度（取最寬的那個），切換時不會跳。"""
        w = max(p.panel_min_width() for p in self.pages.values())
        for p in self.pages.values():
            p.fit_panel(w)

    def on_hide(self):
        self.page().on_hide()

    def save(self):
        self.page().save()

    def stash(self):
        self.page().stash()

    def choose_file(self):
        self.page().choose_file()

    def restyle(self):
        for p in self.pages.values():
            p.restyle()
        self.update()
