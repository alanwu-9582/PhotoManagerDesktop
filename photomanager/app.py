"""主視窗與路由（對應網頁版 js/core/router.js + js/core/main.js）。

功能列是從 ROUTES 長出來的：新增頁面就是加一筆。頁面第一次打開時才建立，
之後一直留著（切換頁面是瞬間的，捲動位置、輸入到一半的東西都不會掉）。
"""
from __future__ import annotations

from .i18n import tr

import sys
import threading

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow, QMenu, QStackedWidget, QVBoxLayout,
                               QWidget)

from . import config
from .engine.categories import categories
from .engine.library import library
from .state import state
from .ui import icons, theme
from .ui.shell import Sidebar, SourceBar, SourceController, StatusBar
from .ui.widgets import init_toast, label, notify, refresh_icons


def _routes():
    from .pages.photos import PhotosPage
    from .pages.stats import StatsPage
    from .pages.organize import OrganizePage
    from .pages.rename import RenamePage
    from .pages.settings import SettingsPage
    from .tools.photo_edit.page import PhotoEditPage
    from .tools.adjust.page import AdjustPage
    from .tools.frames import FramesPage
    from .tools.depth_blur.page import DepthBlurPage
    from .pages.prompts import PromptsPage
    return {
        "photos": {"label": tr("照片檢視"), "icon": "image", "group": tr("照片管理"), "page": PhotosPage},
        "stats": {"label": tr("統計數據"), "icon": "chart", "group": tr("照片管理"), "page": StatsPage},
        "organize": {"label": tr("整理分類"), "icon": "layers", "group": tr("照片管理"), "page": OrganizePage},
        "rename": {"label": tr("批次改名"), "icon": "copy", "group": tr("照片管理"), "page": RenamePage},
        # 照片編輯：以後的新功能依種類放進既有的頁面（分頁 / ToolGroup），或在這裡加一頁
        "edit": {"label": tr("裁切旋轉"), "icon": "crop", "group": tr("照片編輯"), "page": PhotoEditPage},
        "adjust": {"label": tr("調整"), "icon": "adjust", "group": tr("照片編輯"), "page": AdjustPage},
        "blur": {"label": tr("景深模糊"), "icon": "aperture", "group": tr("照片編輯"), "page": DepthBlurPage},
        "frame": {"label": tr("相框與色卡"), "icon": "frame", "group": tr("照片編輯"), "page": FramesPage},
        # 不是編輯照片的工具（不吃照片），所以不會出現在「用編輯工具開啟」的選單裡
        "prompts": {"label": tr("AI 風格提示詞"), "icon": "sparkle", "group": tr("照片編輯"), "page": PromptsPage,
                    "photo_tool": False},
        "settings": {"label": tr("設定"), "icon": "sliders", "group": tr("其他"), "page": SettingsPage},
    }


class _TaskRelay(QObject):
    progress = Signal(int, int, str)
    done = Signal(object, object, object)   # callback, result, error


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.setWindowTitle("Photo Manager")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(980, 640)
        self.routes = _routes()
        self.pages: dict[str, QWidget] = {}
        self.current = None

        self.source = SourceController(self)
        self.sidebar = Sidebar(self.routes)
        self.sidebar.navigate.connect(self.go)
        self.sidebar.theme_btn.clicked.connect(self.theme_menu)

        self.title = label("", "large")
        self.source_bar = SourceBar(self.source)
        header = QHBoxLayout()
        header.setContentsMargins(24, 16, 24, 10)
        header.setSpacing(12)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.source_bar)

        self.stack = QStackedWidget()
        self.status = StatusBar()
        content = QWidget()
        content.setObjectName("Content")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addLayout(header)
        col.addWidget(self.stack, 1)
        col.addWidget(self.status)

        root = QWidget()
        root.setObjectName("Root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.sidebar)
        lay.addWidget(content, 1)
        self.setCentralWidget(root)
        init_toast(self)

        self._task = _TaskRelay()
        self._task.progress.connect(lambda d, t, s: library.progress.emit(d, t, s))
        self._task.done.connect(self._task_done)

        categories.changed.connect(self._on_categories)
        self._shortcuts()
        geo = config.settings.get("geometry")
        if geo:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        else:
            self.resize(1360, 860)
        self.go("photos")

    # ---------------------------------------------------------------- 路由
    def go(self, key):
        if key not in self.routes:
            return
        if self.current == key:
            return
        old = self.pages.get(self.current)
        if old is not None:
            old.on_hide()
        page = self.pages.get(key)
        if page is None:
            page = self.routes[key]["page"](self)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.current = key
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active(key)
        self.title.setText(self.routes[key]["label"])
        self.source_bar.setVisible(page.uses_source)
        self.setWindowTitle(f"{self.routes[key]['label']} · Photo Manager")
        page.on_show()

    def page(self):
        return self.pages.get(self.current)

    def edit_routes(self):
        return [(k, r) for k, r in self.routes.items() if r["group"] == tr("照片編輯") and r.get("photo_tool", True)]

    def open_in_tool(self, key, photo):
        """把這張設成「目前編輯中的照片」，再換到那個工具。"""
        state.selected_id = photo.id
        state.set_editor_photo(photo.path, photo.id)
        self.go(key)

    # ---------------------------------------------------------------- 背景工作
    def run_task(self, text, job, callback):
        """job(cb) 在背景執行緒跑，cb(done, total) 回報進度；完成後 callback(result, error) 回到 GUI 執行緒。"""
        def run():
            try:
                res = job(lambda d, t: self._task.progress.emit(d, t, text))
                self._task.done.emit(callback, res, None)
            except Exception as e:  # noqa: BLE001
                self._task.done.emit(callback, None, e)

        library.progress.emit(0, 0, text + "…")
        threading.Thread(target=run, daemon=True).start()

    def _task_done(self, callback, result, error):
        library.progress_done.emit()
        callback(result, error)

    # ---------------------------------------------------------------- 快捷鍵
    def _shortcuts(self):
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(fn)

        def open_():
            p = self.page()
            if p is not None and hasattr(p, "choose_file"):
                p.choose_file()
            else:
                self.source.open_folder()

        def save():
            p = self.page()
            if p is not None and hasattr(p, "save"):
                p.save()

        def stash():
            p = self.page()
            if p is not None and hasattr(p, "stash"):
                p.stash()

        sc("Ctrl+O", open_)
        sc("Ctrl+Shift+O", self.source.pick_files)
        sc("F5", self.source.rescan)
        sc("Ctrl+R", self.source.rescan)
        sc("Ctrl+S", save)
        sc("Ctrl+Shift+S", stash)
        sc("Ctrl+\\", self.sidebar.toggle_collapsed)
        for i, key in enumerate(self.routes, start=1):
            sc(f"Ctrl+{i}", lambda k=key: self.go(k))

    # ---------------------------------------------------------------- 外觀
    def theme_menu(self):
        menu = QMenu(self)
        current = config.settings.get("theme")
        for value, text in (("system", tr("跟隨系統")), ("light", tr("淺色")), ("dark", tr("深色"))):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(current == value)
            act.triggered.connect(lambda _=False, v=value: self.set_theme(v))
        btn = self.sidebar.theme_btn
        menu.exec(btn.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 4)))

    def set_theme(self, value):
        config.settings.set("theme", value)
        self.apply_theme()

    def apply_theme(self):
        mode = config.settings.get("theme")
        dark = theme.system_is_dark(self.app) if mode == "system" else mode == "dark"
        theme.apply(self.app, dark)
        icons.pixmap.cache_clear()
        refresh_icons(self)
        self.status.restyle()
        for page in self.pages.values():
            page.restyle()
            for w in page.findChildren(QWidget):
                if hasattr(w, "restyle") and w is not page:
                    try:
                        w.restyle()
                    except TypeError:
                        pass
        self.update()

    # ---------------------------------------------------------------- 其他
    def _on_categories(self):
        # 匯入或重設之後，指向已不存在分類的標記要清掉，否則會變成數得到卻整理不到的幽靈標記。
        library.drop_missing_categories()
        library.stats_changed.emit()

    def restart(self):
        """重新啟動（換語言用）：先把標記與設定都存好，再開一個新的自己、關掉這一個。"""
        from PySide6.QtCore import QProcess
        import os
        self.close()
        args = list(sys.argv)
        if getattr(sys, "frozen", False):          # 打包成 exe 的時候
            QProcess.startDetached(sys.executable, args[1:])
        else:
            QProcess.startDetached(sys.executable, [os.path.abspath(args[0])] + args[1:])
        self.app.quit()

    def closeEvent(self, e):
        config.settings.set("geometry", bytes(self.saveGeometry().toBase64()).decode())
        library.shutdown()
        super().closeEvent(e)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PhotoManager.Desktop")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Manager")
    app.setOrganizationName("PhotoManager")
    from . import __version__
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    mode = config.settings.get("theme")
    theme.apply(app, theme.system_is_dark(app) if mode == "system" else mode == "dark")
    from .ui import tooltip
    tooltip.install(app)
    win = MainWindow(app)
    try:
        app.styleHints().colorSchemeChanged.connect(
            lambda *_: config.settings.get("theme") == "system" and win.apply_theme())
    except AttributeError:
        pass
    win.show()

    # 上一次開過的資料夾：還在就直接接回來 —— 使用者回來就看到上次那批照片。
    last = config.settings.get("lastFolder")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        import os
        if os.path.isdir(args[0]):
            QTimer.singleShot(0, lambda: win.source.scan(args[0], bool(config.settings.get("recursive"))))
        else:
            QTimer.singleShot(0, lambda: win.source.add_files(args))
    elif last:
        import os
        if os.path.isdir(last):
            QTimer.singleShot(0, lambda: win.source.scan(last, bool(config.settings.get("recursive"))))
    return app.exec()
