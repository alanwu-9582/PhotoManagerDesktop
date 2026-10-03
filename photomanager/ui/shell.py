"""外殼：側邊欄、照片來源列、狀態列（對應網頁版 index.html + js/app/source.js）。

來源是跨頁共用的狀態，所以來源列住在外殼上，只在需要它的頁面出現；
換頁不會掉、也不會重來。
"""
from __future__ import annotations


import os
import threading

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QLabel, QMenu, QProgressBar, QToolButton, QVBoxLayout,
                               QWidget)

from .. import config
from ..engine.categories import categories
from ..engine.library import library
from ..state import fmt_bytes, state
from . import dialogs, icons, theme
from .widgets import Flag, button, hbox, icon_button, label, notify, vbox, vline


# ============================================================ 側邊欄
class NavItem(QAbstractButton):
    def __init__(self, key, text, icon_name, parent=None):
        super().__init__(parent)
        self.key = key
        self.text_ = text
        self.icon_name = icon_name
        self.active = False
        self.collapsed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(32)
        self.setToolTip(text)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self):
        return QSize(200, 32)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 1, -8, -1)
        path = QPainterPath()
        theme.round_rect(path, r, 7)
        if self.active:
            p.fillPath(path, theme.c("accent_soft"))
        elif self.underMouse():
            p.fillPath(path, theme.c("fill"))
        color = theme.T["accent"] if self.active else theme.T["secondary"]
        pm = icons.pixmap(self.icon_name, color, 17)
        ix = r.x() + (r.width() - 17) / 2 if self.collapsed else r.x() + 10
        p.drawPixmap(QRectF(ix, r.center().y() - 8.5, 17, 17), pm, QRectF(0, 0, pm.width(), pm.height()))
        if not self.collapsed:
            p.setFont(theme.font("body", 600 if self.active else 500))
            p.setPen(theme.c("label"))
            p.drawText(r.adjusted(38, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text_)
        p.end()


class Sidebar(QWidget):
    navigate = Signal(str)
    WIDE, NARROW = 212, 60

    def __init__(self, routes, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.items: dict[str, NavItem] = {}
        self.sections: list[QLabel] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 14, 0, 12)
        lay.setSpacing(1)

        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(icons.app_icon().pixmap(28, 28))
        self.brand_name = label("Photo Manager", "headline")
        self.brand_name.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.brand_sub = label("Manage · Edit", "caption")
        brand_text = vbox(self.brand_name, self.brand_sub, spacing=0)
        self.brand = hbox(self.brand_icon, brand_text, None, spacing=10, margins=(16, 0, 10, 14))
        lay.addLayout(self.brand)

        group = None
        for key, r in routes.items():
            if not r.get("nav", True):
                continue
            if r["group"] != group:
                group = r["group"]
                sec = label(group, "section")
                sec.setContentsMargins(18, 12 if self.sections else 2, 0, 4)
                self.sections.append(sec)
                lay.addWidget(sec)
            item = NavItem(key, r["label"], r["icon"])
            item.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            self.items[key] = item
            lay.addWidget(item)
        lay.addStretch(1)

        self.toggle = icon_button("sidebar", "收合側邊欄", self.toggle_collapsed, size=17)
        self.theme_btn = icon_button("palette", "外觀", None, size=17)
        foot = hbox(self.toggle, None, self.theme_btn, margins=(12, 0, 12, 0))
        lay.addLayout(foot)
        self.collapsed = False
        self.set_collapsed(bool(config.settings.get("sidebarCollapsed")))

    def set_active(self, key):
        for k, it in self.items.items():
            it.active = k == key
            it.update()

    def toggle_collapsed(self):
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, on):
        self.collapsed = on
        self.setFixedWidth(self.NARROW if on else self.WIDE)
        for it in self.items.values():
            it.collapsed = on
            it.update()
        for w in (self.brand_name, self.brand_sub, self.theme_btn, *self.sections):
            w.setVisible(not on)
        self.brand.setContentsMargins(16, 0, 10, 14)
        self.toggle.setToolTip("展開側邊欄" if on else "收合側邊欄")
        config.settings.set("sidebarCollapsed", on)


# ============================================================ 背景掃描
class _ScanRelay(QObject):
    done = Signal(object, object, bool, object)   # root, photos, recursive, error
    progress = Signal(int)


class SourceController(QObject):
    """開資料夾、選檔案、重新掃描、清除。實際的讀取在背景執行緒。"""

    def __init__(self, window):
        super().__init__()
        self.win = window
        self._relay = _ScanRelay()
        self._relay.done.connect(self._on_scanned)
        self._relay.progress.connect(lambda n: library.progress.emit(0, 0, f'掃描中… {n} 張'))
        self._busy = False

    def skip_folders(self, recursive):
        return {c.folder for c in categories.all()} if recursive else set()

    def open_folder(self, path=None):
        if not path:
            start = library.root or config.settings.get("lastFolder") or os.path.expanduser("~/Pictures")
            path = QFileDialog.getExistingDirectory(self.win, "開啟照片資料夾", start)
            if not path:
                return
        self.scan(path, bool(config.settings.get("recursive")))

    def rescan(self):
        if library.mode == "folder" and library.root:
            self.scan(library.root, bool(config.settings.get("recursive")))

    def scan(self, path, recursive):
        if self._busy:
            return
        if not os.path.isdir(path):
            dialogs.alert(self.win, "無法開啟資料夾", f'找不到「{path}」。', tone="danger")
            return
        self._busy = True
        library.progress.emit(0, 0, "掃描中…")
        skip = self.skip_folders(recursive)

        def run():
            try:
                photos = library.scan_folder(path, recursive, skip, self._relay.progress.emit)
                self._relay.done.emit(path, photos, recursive, None)
            except Exception as e:  # noqa: BLE001
                self._relay.done.emit(path, None, recursive, e)

        threading.Thread(target=run, daemon=True).start()

    def _on_scanned(self, path, photos, recursive, error):
        self._busy = False
        library.progress_done.emit()
        if error is not None:
            dialogs.alert(self.win, "無法開啟資料夾", str(error), tone="danger")
            return
        state.selected_id = photos[0].id if photos else None
        state.set_editor_photo(None)
        restored = library.set_folder(path, photos, recursive)
        self.win.source_bar.refresh_recent()
        if restored:
            notify(f'已還原 {restored} 張標記')
        elif not photos:
            notify("這個資料夾裡沒有 JPEG / TIFF / HEIC 照片", "warning")

    def pick_files(self):
        start = library.root or config.settings.get("lastFolder") or os.path.expanduser("~/Pictures")
        paths, _ = QFileDialog.getOpenFileNames(self.win, "選擇照片", start,
                                                "照片 (*.jpg *.jpeg *.tif *.tiff *.heic *.heif)")
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        added = library.add_files(paths)
        if added and not state.selected_id:
            state.selected_id = library.photos[0].id
            state.set_editor_photo(None)

    def clear(self):
        if not library.photos:
            return
        if not dialogs.confirm(self.win, f'清除 {len(library.photos)} 張照片？', "只清畫面與標記，不會刪檔案。",
                               tone="danger", confirm_text="清除畫面"):
            return
        library.clear()
        state.selected_id = None
        state.set_editor_photo(None)
        library.changed.emit()
        library.stats_changed.emit()


# ============================================================ 來源列
class SourceBar(QWidget):
    def __init__(self, ctrl: SourceController, parent=None):
        super().__init__(parent)
        self.setObjectName("SourceBar")
        self.ctrl = ctrl
        self.open_btn = button("開啟資料夾", "primary", "folder", "開啟照片資料夾（Ctrl+O）", ctrl.open_folder)
        self.recent_btn = QToolButton()
        self.recent_btn.setProperty("kind", "icon")
        self.recent_btn.setIcon(icons.icon("chevron-down", theme.T["label"], 14))
        self.recent_btn._icon_name = "chevron-down"
        self.recent_btn.setToolTip("最近開過的資料夾")
        self.recent_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(self)
        self.recent_btn.setMenu(self.recent_menu)
        self.recursive = Flag("含子資料夾")
        self.recursive.setToolTip("連同子資料夾裡的照片一起讀入")
        self.recursive.setChecked(bool(config.settings.get("recursive")))
        self.recursive.toggled.connect(lambda on: config.settings.set("recursive", on))
        self.files_btn = button("選擇檔案", None, "files", "選擇幾張照片（Ctrl+Shift+O）", ctrl.pick_files)
        self.rescan_btn = icon_button("refresh", "重新掃描（F5）", ctrl.rescan)
        self.clear_btn = icon_button("x", "清除畫面（不會刪檔案）", ctrl.clear)
        self.setLayout(hbox(self.open_btn, self.recent_btn, 4, self.recursive, 8, vline(), 8, self.files_btn,
                            6, self.rescan_btn, self.clear_btn, spacing=4))
        self.refresh_recent()
        library.stats_changed.connect(self.sync)
        self.sync()

    def sync(self):
        self.rescan_btn.setVisible(library.mode == "folder")
        self.clear_btn.setVisible(bool(library.mode))

    def refresh_recent(self):
        self.recent_menu.clear()
        recent = [p for p in config.settings.get("recentFolders") or [] if os.path.isdir(p)]
        if not recent:
            act = self.recent_menu.addAction("沒有最近的資料夾")
            act.setEnabled(False)
        for path in recent:
            name = os.path.basename(path.rstrip("\\/")) or path
            act = self.recent_menu.addAction(icons.icon("folder", theme.T["secondary"]), name)
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self.ctrl.scan(p, self.recursive.isChecked()))
        if recent:
            self.recent_menu.addSeparator()
            clear = self.recent_menu.addAction("清除清單")
            clear.triggered.connect(self._clear_recent)

    def _clear_recent(self):
        config.settings.set("recentFolders", [])
        self.refresh_recent()


# ============================================================ 狀態列
class StatusBar(QWidget):
    """原本首頁的那幾格數字：來源、張數、EXIF、已標記、已整理、容量。進度也在這裡。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(30)
        self.text = label("", "caption")
        self.bar = QProgressBar()
        self.bar.setFixedWidth(160)
        self.bar.setFixedHeight(5)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.prog = label("", "caption")
        self.cancel = QToolButton()
        self.cancel.setProperty("kind", "icon")
        self.cancel.setIcon(icons.icon("x", theme.T["secondary"], 12))
        self.cancel._icon_name = "x"
        self.cancel.setToolTip("停止")
        self.cancel.clicked.connect(library.cancel_scan)
        self.cancel.hide()
        self.setLayout(hbox(self.text, None, self.prog, self.bar, self.cancel, spacing=8, margins=(16, 0, 12, 0)))
        self._style_bar()
        library.stats_changed.connect(self.sync)
        library.changed.connect(self.sync)
        library.progress.connect(self.on_progress)
        library.progress_done.connect(self.on_done)
        self.sync()

    def _style_bar(self):
        t = theme.T
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {t['fill_strong']}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{ background: {t['accent']}; border-radius: 1px; }}")

    def restyle(self):
        self._style_bar()

    def sync(self):
        if not library.mode:
            self.text.setText("尚未選擇照片來源")
            return
        s = library.stats()
        where = library.root_name if library.mode == "folder" else "選擇的檔案"
        parts = [where, '{0:,} 張'.format(s['total']), f"EXIF {s['analysed']:,}/{s['total']:,}",
                 '已標記 {0:,}'.format(s['marked']), '已整理 {0:,}'.format(s['organized'])]
        if s["bytes"]:
            parts.append(fmt_bytes(s["bytes"]))
        self.text.setText("   ·   ".join(parts))
        self.text.setToolTip(library.root or "")

    def on_progress(self, done, total, text):
        if total:
            pct = round(done / total * 100)
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            self.prog.setText(f"{text}… {done:,} / {total:,}（{pct}%）")
        else:
            self.bar.setRange(0, 0)   # 不確定的進度：跑馬燈
            self.prog.setText(text)
        self.bar.show()
        self.cancel.setVisible(library.scanning)

    def on_done(self):
        self.bar.hide()
        self.cancel.hide()
        self.prog.setText("")
