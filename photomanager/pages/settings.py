"""設定：分類設定、操作紀錄、外觀與快取。之後的設定也都放這裡，一個分頁一組。

分類設定（對應網頁版 js/pages/settings.js）：每個輸入框都是即時儲存。快捷鍵與目標資料夾是
由順序與名稱自動決定的（唯讀），廢片固定是 q、_廢片，不能改名也不能刪。上面的「設定檔」
可以存好幾套不同用途的分類（旅遊、活動攝影、作品整理…），一鍵整套切換。
"""
from __future__ import annotations


from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
import os
import shutil
import threading
import time

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
                               QHeaderView, QAbstractItemView)

from .. import config
from ..engine.categories import DEFAULT_PROFILE, TEMPLATES
from ..engine.history import history

from ..engine.categories import ACTIONS, action_label, categories
from ..engine.library import library
from ..engine import models
from ..ui import dialogs, theme
from ..ui.dialogs import scroll
from ..ui.photogrid import text_on
from ..ui.widgets import ColorButton, EmptyState, Segmented, button, hbox, icon_button, label, notify, section, vbox
from .base import Page

WEB_URL = "https://wayne-1211.github.io/photoManager/"


class CategoryPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)

        # ---------------- 設定檔
        self.profile_box = QComboBox()
        self.profile_box.setToolTip("每個設定檔是一整套分類、顏色與快捷鍵")
        self.profile_box.setMinimumWidth(200)
        self.profile_box.setToolTip("切換整套分類設定")
        self.profile_box.activated.connect(lambda *_: self.switch(self.profile_box.currentData()))
        new_btn = button("新增設定檔", None, "plus")
        menu = QMenu(new_btn)
        menu.addAction("空白（只有廢片）", lambda: self.create(None))
        menu.addAction("複製目前的設定檔", lambda: self.create(None, copy_current=True))
        menu.addSeparator()
        for name in [DEFAULT_PROFILE, *TEMPLATES]:
            menu.addAction(f'範本：{name}', lambda n=name: self.create(n))
        new_btn.setMenu(menu)
        self.rename_btn = button("重新命名…", None, None, on_click=self.rename_profile)
        self.delete_btn = icon_button("trash", "刪除這個設定檔", self.delete_profile, size=15)
        bar = QFrame()
        bar.setProperty("group", True)
        bar.setLayout(hbox(label("設定檔", "headline"), self.profile_box, new_btn, self.rename_btn, self.delete_btn,
                           None,
                           spacing=8, margins=(14, 10, 14, 10)))
        self.root.addWidget(bar)

        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(8)
        self.root.addLayout(self.toolbar)
        self.toolbar.addStretch(1)
        self.toolbar.addWidget(button("新增分類", "primary", "plus", "快捷鍵依順序自動分配；目標資料夾跟分類名稱相同", self.add))
        self.toolbar.addWidget(button("匯出 JSON", None, "download", on_click=self.export))
        self.toolbar.addWidget(button("匯入 JSON", None, "upload", on_click=self.import_))
        self.toolbar.addWidget(button("重設", "danger-plain", None, "回到預設的分類", self.reset))

        self.table_host = QWidget()
        self.grid = QGridLayout(self.table_host)
        self.grid.setContentsMargins(14, 10, 14, 10)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(8)
        frame = QFrame()
        frame.setProperty("group", True)
        frame.setLayout(vbox(self.table_host, None, margins=(0, 0, 0, 0)))
        self.root.addWidget(scroll(frame), 1)
        self._editing = False
        categories.changed.connect(self._external_change)
        self.render()

    def _external_change(self):
        # 自己打字造成的變更不重畫（不然正在打字的輸入框會被換掉而失去焦點）
        self._fill_profiles()
        if not self._editing:
            self.render()

    # ---------------------------------------------------------------- 設定檔
    def _fill_profiles(self):
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for name in categories.profile_names():
            self.profile_box.addItem(name, name)
        self.profile_box.setCurrentIndex(max(0, self.profile_box.findData(categories.active)))
        self.profile_box.blockSignals(False)
        self.delete_btn.setEnabled(len(categories.profiles) > 1)

    def _confirm_drop_marks(self, target):
        """換一套分類之後，舊分類的標記就對不上了 —— 有標記的話先講清楚。"""
        marked = sum(1 for p in library.photos if p.cat_id and not p.organized)
        if not marked:
            return True
        return dialogs.confirm(self.win, f'切換到「{target}」？',
                               f'目前有 {marked} 張照片已標記。換成另一套分類之後，這些標記會被清除（檔案不會動）。',
                               tone="danger", confirm_text="切換")

    def switch(self, name):
        if not name or name == categories.active:
            return
        if not self._confirm_drop_marks(name):
            self._fill_profiles()
            return
        categories.switch(name)
        history.add("settings", f'切換分類設定檔：{name}', [(c.key.upper() or "·", c.name) for c in categories.all()])
        notify(f'已切換到「{name}」', "success")

    def create(self, template, copy_current=False):
        default = template or (f'{categories.active} 副本' if copy_current else "新的設定檔")
        name, ok = QInputDialog.getText(self.win, "新增分類設定檔", "設定檔名稱：", text=default)
        if not ok or not name.strip():
            return
        if not copy_current and not self._confirm_drop_marks(name.strip()):
            return
        created = categories.create_profile(name, template, copy_current)
        history.add("settings", f'新增分類設定檔：{created}', [(c.key.upper() or "·", c.name) for c in categories.all()])
        notify(f'已建立並切換到「{created}」', "success")

    def rename_profile(self):
        old = categories.active
        name, ok = QInputDialog.getText(self.win, "重新命名設定檔", "設定檔名稱：", text=old)
        if ok:
            categories.rename_profile(old, name)

    def delete_profile(self):
        name = categories.active
        if len(categories.profiles) <= 1:
            return
        if not dialogs.confirm(self.win, f'刪除設定檔「{name}」？', "這一套分類會被刪掉，並切換到下一個設定檔。",
                               tone="danger", confirm_text="刪除"):
            return
        nxt = next(n for n in categories.profile_names() if n != name)
        if not self._confirm_drop_marks(nxt):
            return
        categories.delete_profile(name)
        history.add("settings", f'刪除分類設定檔：{name}')

    def render(self):
        self._fill_profiles()
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().hide()          # deleteLater 要等回到事件迴圈才刪，先藏起來免得疊在新的底下
                it.widget().deleteLater()
        heads = ["快捷鍵", "分類名稱", "目標資料夾", "顏色", "動作", ""]
        for c, h in enumerate(heads):
            self.grid.addWidget(label(h, "section"), 0, c)
        cats = categories.all()
        for r, cat in enumerate(cats, start=1):
            key = QLabel((cat.key or "·").upper())
            key.setAlignment(Qt.AlignmentFlag.AlignCenter)
            key.setFixedSize(30, 30)
            col = QColor(cat.color)
            key.setStyleSheet(f"background:{cat.color}; color:{text_on(col).name()}; border-radius: 7px; font-weight: 700;")
            key.setToolTip("廢片快捷鍵固定為 q" if cat.isTrash else "快捷鍵會依分類順序自動調整")

            name = QLineEdit(cat.name)
            name.setReadOnly(cat.isTrash)
            folder = QLineEdit(cat.folder + "/")
            folder.setReadOnly(True)
            folder.setEnabled(False)
            folder.setToolTip("廢片資料夾固定為 _廢片" if cat.isTrash else "目標資料夾會自動與分類名稱相同")
            timer = QTimer(name)
            timer.setSingleShot(True)
            timer.setInterval(300)

            def commit(c=cat, n=name, f=folder):
                self._editing = True
                updated = categories.update(c.id, name=n.text())
                self._editing = False
                if updated:
                    f.setText(updated.folder + "/")

            timer.timeout.connect(commit)
            name.textEdited.connect(lambda *_, t=timer: t.start())

            color = ColorButton(cat.color)

            def set_color(v, c=cat, k=key):
                self._editing = True
                categories.update(c.id, color=v)
                self._editing = False
                k.setStyleSheet(f"background:{v}; color:{text_on(QColor(v)).name()}; border-radius: 7px; font-weight: 700;")

            color.changed.connect(set_color)

            action = QComboBox()
            for a in ACTIONS:
                action.addItem(action_label(a), a)
            action.setCurrentIndex(ACTIONS.index(cat.action))
            action.setEnabled(not cat.isTrash)

            def set_action(_=None, c=cat, box=action):
                self._editing = True
                categories.update(c.id, action=box.currentData())
                self._editing = False

            action.currentIndexChanged.connect(set_action)

            up = icon_button("chevron-up", "往上移", lambda c=cat: categories.move(c.id, -1), size=13)
            down = icon_button("chevron-down", "往下移", lambda c=cat: categories.move(c.id, 1), size=13)
            delete = icon_button("trash", "刪除", lambda c=cat: self.delete(c), size=15)
            up.setEnabled(not cat.isTrash and r > 1)
            down.setEnabled(not cat.isTrash and r < len(cats) - 1)
            delete.setEnabled(not cat.isTrash)
            tools = QWidget()
            tools.setLayout(hbox(up, down, delete, spacing=2))

            for c, w in enumerate((key, name, folder, color, action, tools)):
                self.grid.addWidget(w, r, c)
        self.grid.setColumnStretch(1, 2)
        self.grid.setColumnStretch(2, 2)

    def add(self):
        categories.add()

    def delete(self, cat):
        used = sum(1 for p in library.photos if p.cat_id == cat.id)
        if used and not dialogs.confirm(self.win, f'刪除「{cat.name}」？', f'{used} 張照片會一併清除標記。',
                                        tone="danger", confirm_text="刪除分類"):
            return
        for p in library.photos:
            if p.cat_id == cat.id:
                p.cat_id = None
        categories.remove(cat.id)
        library.save_marks()
        library.changed.emit()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self.win, "匯出分類", "categories.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(categories.to_json())
            notify("已匯出分類", "success")
        except OSError as e:
            dialogs.alert(self.win, "匯出失敗", str(e), tone="danger")

    def import_(self):
        path, _ = QFileDialog.getOpenFileName(self.win, "匯入分類", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                categories.import_json(f.read())
            notify("已匯入", "success")
        except (OSError, ValueError) as e:
            dialogs.alert(self.win, "匯入失敗", str(e), tone="danger")

    def reset(self):
        if dialogs.confirm(self.win, "重設所有分類？", "自訂分類會消失，回到預設的內容。",
                           tone="danger", confirm_text="重設分類"):
            categories.reset()



# ============================================================ 操作紀錄
class HistoryPanel(QWidget):
    """這次工作階段做過的事：最新的在最上面，展開看每一張照片。"""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["時間", "動作", "內容"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        hd = self.tree.header()
        hd.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hd.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hd.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self._open)
        self.count = label("", "secondary")
        self.expand = button("全部展開", None, "chevron-down", on_click=self.tree.expandAll)
        self.collapse = button("全部收合", None, "chevron-right", on_click=self.tree.collapseAll)
        self.copy_btn = button("複製紀錄", None, "copy", "把整份紀錄複製成文字", self.copy)
        self.clear_btn = button("清除紀錄", "danger-plain", None, on_click=self.clear)
        frame = QFrame()
        frame.setProperty("group", True)
        frame.setLayout(vbox(self.tree, margins=(6, 6, 6, 6)))
        self.frame = frame
        self.empty = EmptyState("book")
        self.empty.set("還沒有任何操作", "這次開啟之後的操作會記在這裡。")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(hbox(self.count, None, self.expand, self.collapse, self.copy_btn, self.clear_btn, spacing=8))
        lay.addWidget(frame, 1)
        lay.addWidget(self.empty, 1)
        history.changed.connect(self.render)
        self.render()

    def render(self):
        expanded = {self.tree.topLevelItem(i).data(0, 256) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        self.tree.clear()
        entries = list(reversed(history.entries))
        mono = theme.font("callout", mono=True)
        for e in entries:
            top = QTreeWidgetItem([time.strftime("%H:%M:%S", time.localtime(e.at)), e.label, e.title])
            top.setData(0, 256, id(e))
            top.setFont(0, mono)
            for a, b in e.items:
                child = QTreeWidgetItem(["", "", f"{a}    {b}"])
                child.setToolTip(2, f"{a}\n{b}")
                top.addChild(child)
            for a, b in e.failed:
                child = QTreeWidgetItem(["", "失敗", f"{a}    {b}"])
                child.setForeground(1, theme.c("red"))
                child.setForeground(2, theme.c("red"))
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            if id(e) in expanded:
                top.setExpanded(True)
        n = len(entries)
        self.count.setText(f'這次工作階段 {n} 筆操作' if n else "")
        self.frame.setVisible(bool(n))
        self.empty.setVisible(not n)
        for w in (self.expand, self.collapse, self.copy_btn, self.clear_btn):
            w.setEnabled(bool(n))

    def _open(self, item, _col):
        """雙擊路徑那一列就在檔案總管裡找出來。"""
        text = item.text(2)
        for part in text.split("→ ")[1:]:
            path = part.split("（")[0].strip()
            if os.path.exists(path):
                from .photos import reveal_in_explorer
                reveal_in_explorer(path)
                return

    def copy(self):
        QGuiApplication.clipboard().setText(history.as_text())
        notify("已複製操作紀錄", "success")

    def clear(self):
        if dialogs.confirm(self.win, "清除操作紀錄？", "只清除這份清單，不會復原任何操作。", confirm_text="清除"):
            history.clear()


# ============================================================ 外觀與快取
def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class GeneralPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.win = window
        self.theme = Segmented([("system", "跟隨系統"), ("light", "淺色"), ("dark", "深色")],
                               config.settings.get("theme") or "system")
        self.theme.changed.connect(self.win.set_theme)
        self.cache = label("", "secondary")
        clear = button("清除縮圖快取", None, "trash", "縮圖會在需要時重新產生", self.clear_cache)
        self.cache.setToolTip(str(config.CACHE_DIR))
        box1 = QFrame()
        box1.setProperty("group", True)
        box1.setLayout(vbox(label("外觀", "headline"), self.theme, spacing=10, margins=(16, 14, 16, 14)))
        box2 = QFrame()
        box2.setProperty("group", True)
        box2.setLayout(vbox(label("快取", "headline"), hbox(self.cache, None, clear),
                            spacing=10, margins=(16, 14, 16, 14)))
        web = button("開啟網頁版", None, "external", WEB_URL, self.open_web)
        box3 = QFrame()
        box3.setProperty("group", True)
        box3.setLayout(vbox(label("網頁版", "headline"), hbox(label(WEB_URL, "secondary", selectable=True), None, web),
                            spacing=10, margins=(16, 14, 16, 14)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(box1)
        lay.addWidget(box2)
        lay.addWidget(box3)
        lay.addStretch(1)

    def refresh(self):
        from ..state import fmt_bytes
        self.theme.setValue(config.settings.get("theme") or "system")
        self.cache.setText(f'縮圖快取 {fmt_bytes(_dir_size(config.THUMB_DIR))}')

    def open_web(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(WEB_URL))

    def clear_cache(self):
        if not dialogs.confirm(self.win, "清除縮圖快取？", "只刪快取，照片本身不會動。下次捲到時會重新產生縮圖。",
                               confirm_text="清除"):
            return
        shutil.rmtree(config.THUMB_DIR, ignore_errors=True)
        self.refresh()
        notify("已清除縮圖快取", "success")


# ============================================================ 設定頁
class _ModelRelay(QObject):
    progress = Signal(str, int, int)        # key, 已下載, 總共
    done = Signal(str, object)              # key, error | None


class ModelsPanel(QWidget):
    """已下載的 AI 模型：每個模型一塊，看狀態、下載或刪除。"""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self.relay = _ModelRelay()
        self.relay.progress.connect(self._on_progress)
        self.relay.done.connect(self._on_done)
        self.rows = {}
        self.busy = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        for m in models.all_models():
            status = label("", "secondary")
            action = button("", None, None)
            action.clicked.connect(lambda _=False, mm=m: self.act(mm))
            box = QFrame()
            box.setProperty("group", True)
            box.setLayout(vbox(hbox(label(m.name, "headline"), label(m.purpose, "secondary"), None, spacing=10),
                               hbox(status, None, action, spacing=8), spacing=10, margins=(16, 14, 16, 14)))
            box.setToolTip(f"來源：{m.module.MODEL_SOURCE}\n位置：{m.path}")
            self.rows[m.key] = (m, status, action)
            lay.addWidget(box)
        self.total = label("", "secondary")
        folder = button("開啟模型資料夾", None, "folder", str(models.MODELS_DIR),
                        lambda: self._open_folder())
        foot = QFrame()
        foot.setProperty("group", True)
        foot.setLayout(vbox(hbox(self.total, None, folder, spacing=8), spacing=10, margins=(16, 14, 16, 14)))
        lay.addWidget(foot)
        lay.addStretch(1)

    def _open_folder(self):
        from ..engine.prompts import open_folder
        open_folder(models.MODELS_DIR)

    def refresh(self):
        from ..state import fmt_bytes
        rt = models.runtime_available()
        total = 0
        for key, (m, status, action) in self.rows.items():
            if key in self.busy:
                continue
            size = m.size_bytes()
            total += size
            if m.installed():
                status.setText(f"已下載 · {fmt_bytes(size)}")
                action.setText("刪除")
                action.setProperty("kind", "danger-plain")
                action.setToolTip("刪除模型檔；需要時可以再下載")
                action.setEnabled(True)
            else:
                status.setText(f"未下載（約 {m.module.MODEL_SIZE_MB:g} MB）")
                action.setText("下載")
                action.setProperty("kind", None)
                action.setEnabled(rt)
                action.setToolTip("" if rt else "沒有安裝 onnxruntime，無法使用 AI 模型")
            action.style().unpolish(action)
            action.style().polish(action)
        self.total.setText(f"共使用 {fmt_bytes(total)}" if total else "還沒有下載任何模型")

    def act(self, m):
        if m.key in self.busy:
            return
        if m.installed():
            if not dialogs.confirm(self.win, f"刪除「{m.name}」？", "刪除後會改用內建的快速估計；需要時可以再下載。",
                                   tone="danger", confirm_text="刪除"):
                return
            try:
                m.delete()
            except OSError as e:
                dialogs.alert(self.win, "刪除失敗", str(e), tone="danger")
                return
            notify(f"已刪除「{m.name}」", "success")
            self.refresh()
            self.win.models_changed()
            return
        if not dialogs.confirm(self.win, f"下載「{m.name}」？",
                               f"{m.purpose}\n來源：{m.module.MODEL_SOURCE}\n大小：約 {m.module.MODEL_SIZE_MB:g} MB\n\n"
                               "只需要下載一次，之後離線也能用。照片不會被上傳。", confirm_text="下載"):
            return
        self.busy.add(m.key)
        _, status, action = self.rows[m.key]
        action.setEnabled(False)
        status.setText("下載中…")

        def run():
            try:
                m.module.download(lambda d, t: self.relay.progress.emit(m.key, d, t))
                self.relay.done.emit(m.key, None)
            except Exception as e:  # noqa: BLE001
                self.relay.done.emit(m.key, e)

        threading.Thread(target=run, daemon=True).start()

    def _on_progress(self, key, done, total):
        _, status, _ = self.rows[key]
        status.setText(f"下載中… {done / 1e6:.1f} / {total / 1e6:.1f} MB" if total else f"下載中… {done / 1e6:.1f} MB")

    def _on_done(self, key, error):
        self.busy.discard(key)
        m = self.rows[key][0]
        m.module.reset()
        if error:
            dialogs.alert(self.win, "下載失敗", str(error), tone="danger")
        else:
            notify(f"「{m.name}」已就緒", "success")
        self.refresh()
        self.win.models_changed()


class SettingsPage(Page):
    title = "設定"
    TABS = [("categories", "分類設定"), ("history", "操作紀錄"), ("general", "外觀與快取"), ("models", "AI 模型")]

    def __init__(self, window):
        super().__init__(window)
        self.tabs = Segmented(self.TABS, "categories")
        self.tabs.changed.connect(self.show_tab)
        self.toolbar.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.panels = {"categories": CategoryPanel(window), "history": HistoryPanel(window),
                       "general": GeneralPanel(window), "models": ModelsPanel(window)}
        for w in self.panels.values():
            self.stack.addWidget(w)
        self.root.addWidget(self.stack, 1)

    def show_tab(self, key):
        self.tabs.setValue(key)
        self.stack.setCurrentWidget(self.panels[key])
        if key in ("general", "models"):
            self.panels[key].refresh()

    def on_show(self):
        self.show_tab(self.tabs.value())
