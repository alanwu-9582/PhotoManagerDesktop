"""整理分類：左邊大圖、右邊縮圖側欄，鍵盤一路標記下去（對應網頁版 js/pages/organize.js）。

標記只是存在記憶體與本機設定裡的一個分類 id；真正動到硬碟上的檔案只有按下
「套用整理」那一刻。檔案模式（選擇檔案）一樣可以就地整理（放進照片各自的資料夾），
或打包成 .zip。

鍵盤：1 2 3…／q 標記、方向鍵切換、⌫ 清除、按住 space 偷看下一張、0 回到原始大小。
"""
from __future__ import annotations

from ..i18n import tr, tr_value

import os
import threading
import zipfile

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QApplication, QComboBox,
                               QFileDialog, QFrame, QLineEdit, QPlainTextEdit, QScrollArea, QSlider, QSplitter,
                               QVBoxLayout, QWidget)

from .. import config
from ..engine import organize as org
from ..engine.categories import action_label, categories
from ..engine.history import history
from ..engine.library import library
from ..state import fmt_bytes, state
from ..ui import dialogs, icons, theme
from ..ui.photogrid import PhotoGrid, text_on
from ..ui.viewer import Viewer, display_edge
from ..ui.widgets import Stepper, Flag, EmptyState, FlowLayout, button, hbox, label, notify, vbox
from .base import Page

SIDE_FIELDS = [("dateTimeOriginal", tr("拍攝時間")), ("model", tr("相機型號")), ("lensModel", tr("鏡頭")),
               ("focalLength", tr("焦段")), ("fNumber", tr("光圈")), ("exposureTime", tr("快門")), ("iso", "ISO"),
               ("creativeStyle", tr("創意風格"))]


class LegendItem(QAbstractButton):
    """圖例上的一格：快捷鍵、名稱、張數、動作。點下去就是標記目前這張。"""

    def __init__(self, cat=None, parent=None):
        super().__init__(parent)
        self.cat = cat
        self.count = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(30)

    def sizeHint(self):
        fm = QFontMetrics(theme.font("callout", 600))
        if self.cat is None:
            return QSize(fm.horizontalAdvance(tr("清除")) + 48, 30)
        extra = fm.horizontalAdvance(f"{self.cat.name}  {self.count}  {action_label(self.cat.action)}")
        return QSize(extra + 52, 30)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        theme.round_rect(path, r, 7)
        p.fillPath(path, theme.c("fill_strong" if self.underMouse() else "fill"))
        key_r = QRectF(4, 4, 22, 22)
        kp = QPainterPath()
        theme.round_rect(kp, key_r, 5)
        if self.cat is None:
            p.fillPath(kp, theme.c("control"))
            p.setPen(theme.c("label"))
            p.setFont(theme.font("callout", 600))
            ic = icons.pixmap("x", theme.T["label"], 11)
            p.drawPixmap(QRectF(key_r.center().x() - 5.5, key_r.center().y() - 5.5, 11, 11), ic, QRectF(ic.rect()))
            p.drawText(QRectF(32, 0, self.width() - 36, 30), Qt.AlignmentFlag.AlignVCenter, tr("清除"))
            return
        col = QColor(self.cat.color)
        p.fillPath(kp, col)
        p.setPen(text_on(col))
        p.setFont(theme.font("caption", 700))
        p.drawText(key_r, Qt.AlignmentFlag.AlignCenter, (self.cat.key or "·").upper())
        x = 32
        f = theme.font("callout", 600)
        fm = QFontMetrics(f)
        p.setFont(f)
        p.setPen(theme.c("label"))
        p.drawText(QRectF(x, 0, 400, 30), Qt.AlignmentFlag.AlignVCenter, self.cat.name)
        x += fm.horizontalAdvance(self.cat.name) + 7
        p.setPen(theme.c("accent") if self.count else theme.c("secondary"))
        p.drawText(QRectF(x, 0, 100, 30), Qt.AlignmentFlag.AlignVCenter, str(self.count))
        x += fm.horizontalAdvance(str(self.count)) + 7
        p.setFont(theme.font("caption"))
        p.setPen(theme.c("tertiary"))
        p.drawText(QRectF(x, 0, 100, 30), Qt.AlignmentFlag.AlignVCenter, action_label(self.cat.action))


class _KeyFilter(QObject):
    """整頁的鍵盤：焦點在輸入框或開著對話框的時候不攔。"""

    def __init__(self, page):
        super().__init__()
        self.page = page

    def eventFilter(self, obj, e):  # noqa: N802
        t = e.type()
        if t not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            if t == QEvent.Type.ApplicationDeactivate:
                self.page.end_peek()
            return False
        if not self.page.isVisible() or QApplication.activeModalWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)):
            return False
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                            | Qt.KeyboardModifier.MetaModifier):
            return False
        if t == QEvent.Type.ShortcutOverride:
            # 讓 Space 不要去按到目前有焦點的按鈕
            if e.key() == Qt.Key.Key_Space or self.page.handles(e):
                e.accept()
                return False
            return False
        if t == QEvent.Type.KeyRelease:
            if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
                self.page.end_peek()
                return True
            return False
        return self.page.on_key(e)


class OrganizePage(Page):
    title = tr("整理分類")
    uses_source = True

    def __init__(self, window):
        super().__init__(window)
        self.compare_id = None
        self.peeking = False
        self._dirty = True
        self._info_open = False

        # ---------------- 圖例
        legend_host = QWidget()
        self.legend = FlowLayout(legend_host, spacing=6)
        self.root.addWidget(legend_host)
        self.legend_items: list[LegendItem] = []

        # ---------------- 左：大圖
        self.viewer = Viewer()
        self.viewer.drop_photo.connect(self.set_compare)
        self.viewer.exit_compare.connect(lambda: self.exit_compare())
        self.viewer.peek_pressed.connect(self.start_peek)
        self.viewer.peek_released.connect(self.end_peek)
        self.viewer.info.toggled.connect(lambda on: setattr(self, "_info_open", on))
        self.empty = EmptyState("layers")
        self.empty.set(tr("尚未載入照片"), tr("開啟資料夾後，用 1 2 3… 標記、方向鍵切換。"))
        left = QWidget()
        left.setLayout(vbox(self.viewer, self.empty, spacing=0))

        # ---------------- 右：套用 + 縮圖
        self.apply_btn = button(tr("套用整理"), "primary", None, tr("把標記好的照片搬進分類資料夾"), self.apply)
        self.apply_btn.setProperty("kind", "primary")
        self.zip_btn = button(tr("打包 .zip"), None, "download", tr("把標記好的照片依分類打包成一個 .zip"), self.export_zip)
        self.hint = label("", "secondary", wrap=True)
        self.hint.setTextFormat(Qt.TextFormat.RichText)
        self.result = label("", None, wrap=True)
        self.result.setObjectName("Banner")
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.hide()
        # 摘要放在固定高度的捲動區裡：分類多寡、有沒有結果訊息都不會讓這一塊忽高忽低，
        # 下面的縮圖欄也就不會跟著跳。
        details = QWidget()
        details.setLayout(vbox(self.hint, self.result, None, spacing=8))
        self.details = QScrollArea()
        self.details.setWidget(details)
        self.details.setWidgetResizable(True)
        self.details.setFrameShape(QFrame.Shape.NoFrame)
        self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        apply_box = QFrame()
        apply_box.setProperty("group", True)
        apply_box.setLayout(vbox(hbox(self.apply_btn, self.zip_btn, spacing=8), self.details,
                                 spacing=8, margins=(12, 12, 12, 12)))
        apply_box.layout().itemAt(0).layout().setStretch(0, 1)
        apply_box.setFixedHeight(160)

        self.col_slider = Stepper([(n, str(n)) for n in range(2, 7)], state.manage_columns, tip=tr("每排幾張"))
        self.col_slider.changed.connect(self.set_columns)
        self.hide_done = Flag(tr("只看未分類"))
        self.hide_done.setChecked(state.hide_done)
        self.hide_done.toggled.connect(self.set_hide_done)
        self.count = label("—", "caption")
        # 張數自己一行：跟開關擠在同一行的話，側欄一窄就會被切掉。
        bar = hbox(label(tr("每排"), "secondary"), self.col_slider, None, self.hide_done, spacing=6)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.grid = PhotoGrid("thumb", state.manage_columns, draggable=True)
        self.grid.photo_clicked.connect(self.on_thumb_click)
        right = QWidget()
        right.setLayout(vbox(apply_box, bar, self.count, self.grid, spacing=8))
        right.setMinimumWidth(280)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([900, 380])
        self.split.setHandleWidth(14)
        self.split.setChildrenCollapsible(False)
        self.root.addWidget(self.split, 1)

        for w in (self.apply_btn, self.zip_btn, self.hide_done, self.col_slider):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._keys = _KeyFilter(self)
        self._preload = QTimer(self)
        self._preload.setSingleShot(True)
        self._preload.setInterval(180)
        self._preload.timeout.connect(self._do_preload)

        library.changed.connect(self.mark_dirty)
        library.photo_updated.connect(self._on_photo_updated)
        categories.changed.connect(self._on_categories)

    # ================================================================ 生命週期
    def on_show(self):
        # 同步方向以整理分類為準；進入本頁後，下一個編輯工具會採用這裡的目前照片。
        if state.editor_photo:
            state.set_editor_photo(None)
        self.compare_id = None
        QApplication.instance().installEventFilter(self._keys)
        self.render_all()

    def on_hide(self):
        self.end_peek()
        QApplication.instance().removeEventFilter(self._keys)
        library.pin_full([])
        self._preload.stop()

    def mark_dirty(self):
        if self.isVisible():
            self.render_all()
        else:
            self._dirty = True

    def _on_categories(self):
        if self.isVisible():
            self.render_legend()
            self.grid.viewport().update()
            self.render_apply_hint()
            self.render_info()

    def _on_photo_updated(self, photo):
        if self.isVisible() and photo.id == state.selected_id:
            self.render_info()

    # ================================================================ 清單
    def manage_list(self):
        return [p for p in library.photos if not p.cat_id] if state.hide_done else library.photos

    def selected_index(self, lst=None):
        lst = self.manage_list() if lst is None else lst
        for i, p in enumerate(lst):
            if p.id == state.selected_id:
                return i
        return 0

    def current(self):
        lst = self.manage_list()
        return lst[self.selected_index(lst)] if lst else None

    def next_photo(self):
        lst = self.manage_list()
        i = self.selected_index(lst) + 1
        return lst[i] if i < len(lst) else None

    def select(self, pid):
        state.selected_id = pid

    # ================================================================ 畫面
    def render_all(self):
        self._dirty = False
        lst = self.manage_list()
        if lst and not any(p.id == state.selected_id for p in lst):
            self.select(lst[0].id)
        self.grid.set_photos(lst)
        has = bool(lst)
        self.viewer.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            self.empty.set(tr("沒有符合的照片") if library.photos else tr("尚未載入照片"),
                           tr("取消「只看未分類」看全部。") if library.photos else tr("開啟資料夾後，用 1 2 3… 標記、方向鍵切換。"))
        self.render_legend()
        self.update_selection(scroll=True)
        self.render_apply_hint()

    def render_legend(self):
        counts: dict[str, int] = {}
        for p in library.photos:
            if p.cat_id:
                counts[p.cat_id] = counts.get(p.cat_id, 0) + 1
        cats = categories.all()
        if len(self.legend_items) != len(cats) + 1 or any(
                it.cat is not c for it, c in zip(self.legend_items, cats)):
            self.legend.clear()
            self.legend_items = []
            for cat in cats + [None]:
                it = LegendItem(cat)
                it.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                it.clicked.connect(lambda _=False, c=cat: self.mark(c) if c else self.clear_mark())
                self.legend.addWidget(it)
                self.legend_items.append(it)
        for it in self.legend_items:
            if it.cat:
                it.count = counts.get(it.cat.id, 0)
                it.setToolTip(f"{it.cat.name}（{it.cat.key.upper()}）→ {it.cat.folder}/")
            it.updateGeometry()
            it.update()
        self.legend.invalidate()
        s = library.stats()
        self.count.setText(tr('{0:,} · 標記 {1:,} · 整理 {2:,}').format(s['total'], s['marked'], s['organized']) if s["total"] else "—")

    def update_selection(self, scroll=True):
        photo = self.current()
        other = library.by_id(self.compare_id) if self.compare_id else None
        if other is None or photo is None or other is photo:
            self.compare_id = None
            other = None
        self.grid.delegate.selected_id = photo.id if photo else None
        self.grid.delegate.compare_id = self.compare_id
        self.grid.viewport().update()
        if scroll and photo:
            self.grid.scroll_to_photo(photo.id)
        if self.peeking:
            self.end_peek()
        self.viewer.render(photo, other)
        nxt = self.next_photo()
        library.pin_full([photo and photo.id, other and other.id, nxt and nxt.id])
        self.render_info()
        self._preload.start()

    def render_info(self):
        photo = self.current()
        if not photo:
            return
        info = photo.info or {}
        lines = [(tr("檔名"), photo.name), (tr("大小"), fmt_bytes(photo.size or 0))]
        for k, lb in SIDE_FIELDS:
            v = info.get(k)
            if v is not None:
                lines.append((lb, tr_value(v)))
        if photo.info_state == "idle" and not photo.info:
            lines.append(("EXIF", tr("讀取中…")))
        elif not photo.info:
            lines.append(("EXIF", tr("無 EXIF")))
        badge = None
        if photo.organized:
            verb = tr("複製") if photo.organized["action"] == "copy" else tr("移動")
            badge = (tr('✓ 已{0}到 {1}').format(verb, photo.organized['folder']), theme.T["green"])
        elif photo.cat_id:
            cat = categories.by_id(photo.cat_id)
            if cat:
                badge = (f"{cat.name} → {tr('不移動') if cat.action == 'keep' else cat.folder}", cat.color)
        self.viewer.info.open = self._info_open
        self.viewer.info.set_content(lines, badge)
        self.viewer._place_overlays()  # noqa: SLF001

    def _do_preload(self):
        """預先解好前後幾張（偷看下一張才會是即時的）。等手停下來才做。"""
        lst = self.manage_list()
        if lst:
            library.preload_around(self.selected_index(lst), 4, 2, display_edge(), lst)

    # ================================================================ 標記
    def mark(self, cat):
        lst = self.manage_list()
        if not lst:
            return
        i = self.selected_index(lst)
        photo = lst[i]
        if photo.organized:   # 已經搬過的就不再改
            notify(tr("這張已經整理過了"), "warning")
            return
        photo.cat_id = None if photo.cat_id == cat.id else cat.id
        library.save_marks()
        history.mark(photo.name, cat.name if photo.cat_id else None)
        after = self.manage_list()
        nxt = min(i, len(after) - 1) if state.hide_done else min(i + 1, len(after) - 1)
        self.select(after[nxt].id if after and nxt >= 0 else None)
        if state.hide_done:
            self.grid.set_photos(after)
            if not after:
                self.render_all()
                return
        else:
            self.grid.model_.touch(photo)
        self.render_legend()
        self.update_selection()
        self.render_apply_hint()

    def clear_mark(self):
        photo = self.current()
        if not photo or photo.organized:
            return
        had = photo.cat_id
        photo.cat_id = None
        library.save_marks()
        if had:
            history.mark(photo.name, None)
        if state.hide_done:
            self.grid.set_photos(self.manage_list())
        self.grid.model_.touch(photo)
        self.render_legend()
        self.update_selection()
        self.render_apply_hint()

    def move(self, delta):
        lst = self.manage_list()
        if not lst:
            return
        i = max(0, min(len(lst) - 1, self.selected_index(lst) + delta))
        self.select(lst[i].id)
        self.update_selection()

    # ================================================================ 對照 / 偷看
    def on_thumb_click(self, photo):
        # 對照中再點兩張裡的任一張，就收回成單張 —— 點到的那張留下來。
        if self.compare_id and photo.id in (state.selected_id, self.compare_id):
            self.exit_compare(photo.id)
            return
        self.select(photo.id)
        self.update_selection(scroll=False)

    def set_compare(self, pid):
        photo = library.by_id(pid)
        if not photo or photo.id == state.selected_id:
            return
        self.compare_id = pid
        self.update_selection(scroll=False)

    def exit_compare(self, keep=None):
        self.compare_id = None
        if keep:
            self.select(keep)
        self.update_selection(scroll=False)

    def start_peek(self):
        nxt = self.next_photo()
        if self.peeking or not nxt:
            return
        self.peeking = True
        self.viewer.main.peek(nxt)

    def end_peek(self):
        if not self.peeking:
            return
        self.peeking = False
        self.viewer.main.peek(None)

    # ================================================================ 鍵盤
    def handles(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                 Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Space):
            return True
        return bool(e.text()) and categories.by_key(e.text()) is not None

    def on_key(self, e) -> bool:
        if not library.photos:
            return False
        k = e.key()
        if k == Qt.Key.Key_Space:
            if not e.isAutoRepeat():
                self.start_peek()
            return True
        cols = state.manage_columns
        moves = {Qt.Key.Key_Right: 1, Qt.Key.Key_Left: -1, Qt.Key.Key_Down: cols, Qt.Key.Key_Up: -cols}
        if k in moves:
            self.move(moves[k])
            return True
        if k in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear_mark()
            return True
        if k == Qt.Key.Key_0 and not categories.by_key("0"):
            self.viewer.reset()
            return True
        cat = categories.by_key(e.text()) if e.text() else None
        if cat:
            if not e.isAutoRepeat():
                self.mark(cat)
            return True
        return False

    # ================================================================ 設定
    def set_columns(self, n):
        state.manage_columns = n
        config.settings.set("manageColumns", n)
        self.grid.set_columns(n)

    def set_hide_done(self, on):
        state.hide_done = on
        self.render_all()

    # ================================================================ 套用整理
    def render_apply_hint(self):
        self.zip_btn.setVisible(library.mode == "files")
        if not library.mode:
            self.hint.setText(tr("尚未選擇照片來源"))
            self.apply_btn.setEnabled(False)
            return
        plan = org.plan(library)
        self.apply_btn.setEnabled(plan["total"] > 0)
        self.zip_btn.setEnabled(any(p.cat_id and (categories.by_id(p.cat_id) or None) and
                                    categories.by_id(p.cat_id).action != "keep" for p in library.photos))
        if not plan["total"]:
            self.hint.setText(tr("沒有待整理的照片"))
            return
        where = library.root_name if library.mode == "folder" else tr("各照片所在的資料夾")
        code = f"<span style='font-family:{theme.MONO_FAMILIES[0]}'>{{}}</span>"
        rows = "".join(
            tr("<div><b>{0}</b> 張 → {1} <span style='color:{2}'>{3}</span></div>").format(b['count'], code.format(b['folder'] + '/'), theme.T['tertiary'], action_label(b['action']))
            for b in plan["byFolder"])
        self.hint.setText(tr('<div><b>{0}</b> 張 → {1}</div>{2}').format(plan['total'], code.format(where), rows))

    def apply(self):
        plan = org.plan(library)
        if not plan["total"]:
            return
        where = library.root_name if library.mode == "folder" else tr("各照片所在的資料夾")
        summary = "\n".join(tr('  {0}/   {1} 張（{2}）').format(b['folder'], b['count'], action_label(b['action'])) for b in plan["byFolder"])
        if not dialogs.confirm(self.win, tr('整理 {0} 張照片？').format(plan['total']),
                               tr('即將在「{0}」內整理：\n\n{1}\n\n「移動」會真的改變檔案在硬碟上的位置（不會經過資源回收筒）。').format(where, summary),
                               tone="danger", confirm_text=tr("開始整理")):
            return
        self.apply_btn.setEnabled(False)
        self.win.run_task(tr("整理中"), lambda cb: org.run(library, plan["items"], cb), self._applied)

    def _applied(self, result, error):
        if error:
            dialogs.alert(self.win, tr("整理失敗"), str(error), tone="danger")
        else:
            library.save_marks_now()
            failed = result["failed"]
            html = tr('移動 <b>{0}</b> · 複製 <b>{1}</b>').format(result['moved'], result['copied'])
            if failed:
                html += tr('<br>失敗 <b>{0}</b>：<br>').format(len(failed)) + "<br>".join(
                    f"{f['name']} — {f['message']}" for f in failed[:8])
            self.result.setText(html)
            self.result.setProperty("tone", "error" if failed else "ok")
            self.result.style().unpolish(self.result)
            self.result.style().polish(self.result)
            self.result.show()
            notify(tr('已整理 {0} 張').format(result['moved'] + result['copied']), "danger" if failed else "success")
            where = library.root_name if library.mode == "folder" else tr("各照片所在的資料夾")
            history.add("organize", tr('整理 {0} 張（{1}）').format(result['moved'] + result['copied'], where), result["done"],
                        [(f["name"], f["message"]) for f in failed])
        library.stats_changed.emit()
        self.render_all()

    def export_zip(self):
        groups: dict[str, list] = {}
        for p in library.photos:
            cat = categories.by_id(p.cat_id) if p.cat_id else None
            if cat and cat.action != "keep":
                groups.setdefault(cat.folder, []).append(p)
        if not groups:
            dialogs.alert(self.win, tr("沒有可打包的照片"), tr("先標記需要移動或複製的分類。"))
            return
        path, _ = QFileDialog.getSaveFileName(self.win, tr("打包成 .zip"), "Photo_Manager_Export.zip", "ZIP (*.zip)")
        if not path:
            return

        def job(cb):
            total = sum(len(v) for v in groups.values())
            done = 0
            used = set()
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
                for folder, items in groups.items():
                    for p in items:
                        base, ext = os.path.splitext(p.name)
                        name, n = p.name, 1
                        while f"{folder}/{name}".lower() in used:
                            name = f"{base}_{n}{ext}"
                            n += 1
                        used.add(f"{folder}/{name}".lower())
                        z.write(p.path, f"{folder}/{name}")
                        done += 1
                        cb(done, total)
            return total

        def done(n, error):
            if error:
                dialogs.alert(self.win, tr("打包失敗"), str(error), tone="danger")
            else:
                notify(tr('已打包 {0} 張').format(n), "success")
                history.add("zip", tr('打包 {0} 張').format(n), [(f"{folder}/", tr('{0} 張').format(len(v))) for folder, v in groups.items()]
                            + [(tr("檔案"), path)])

        self.win.run_task(tr("打包中"), job, done)
