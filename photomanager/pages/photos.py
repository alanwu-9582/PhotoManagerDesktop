"""照片檢視：縮圖牆 + 每張卡片下面的 EXIF 欄位（對應網頁版 js/pages/photos.js）。

點照片本身是「完整資訊」；滑過時右下角那顆工具鈕才是送進編輯工具。
右鍵還有一份選單：完整資訊、編輯工具、標記分類、在檔案總管中顯示。
篩選與 EXIF 欄位都開在對話框裡，對話框的高度固定，增減條件不會忽大忽小。
"""
from __future__ import annotations

from ..i18n import tr, tr_value

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QComboBox, QHBoxLayout, QMenu, QSlider, QVBoxLayout, QWidget

from .. import config
from ..engine.categories import categories
from ..engine.library import library
from ..filters import FILTER_FIELDS, active_count, apply_filters, options_for
from ..state import FIELD_DEFS, state
from ..ui import icons, theme
from ..ui.dialogs import Sheet, scroll
from ..ui.inspector import open_inspector
from ..ui.photogrid import GroupHeader, PhotoGrid
from ..grouping import GROUPS, NEEDS_EXIF, group
from ..engine.history import history
from ..ui.widgets import (Stepper, BadgeButton, Chip, EmptyState, FlowLayout, Segmented, button, hbox, icon_button,
                          label, notify, vline)
from .base import Page


def reveal_in_explorer(path):
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError as e:
        notify(tr('開不了檔案總管: {0}').format(e), "danger")


class FieldDialog(Sheet):
    def __init__(self, parent, on_change):
        super().__init__(parent, tr("EXIF 欄位"), width=520)
        holder = QWidget()
        self.flow = FlowLayout(holder, spacing=8)
        self.chips = {}
        for key, lb, _ in FIELD_DEFS:
            chip = Chip(lb, state.fields.get(key, False))
            chip.toggled.connect(lambda on, k=key: (state.set_field(k, on), on_change()))
            self.flow.addWidget(chip)
            self.chips[key] = chip
        self.body.addWidget(holder)
        self.body.addStretch(1)
        off = button(tr("全部關閉"), on_click=self.all_off)
        done = button(tr("完成"), "primary", on_click=self.accept)
        done.setDefault(True)
        self.footer.addWidget(off)
        self.add_footer(done)
        self.resize(520, 260)

    def all_off(self):
        for chip in self.chips.values():
            chip.setChecked(False)


class FilterDialog(Sheet):
    """條件都是即時生效的：每改一下，頁尾的張數就跟著變，關窗時才重畫縮圖牆。"""

    def __init__(self, parent, on_change):
        super().__init__(parent, tr("篩選照片"), width=660, height=460)
        self.on_change = on_change
        self.rows_host = QWidget()
        self.rows = QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(8)
        self.rows.addStretch(1)
        self.body.addWidget(scroll(self.rows_host), 1)
        add = button(tr("新增條件"), "plain", "plus", on_click=self.add_row)
        self.body.addLayout(hbox(add, None))
        self.summary = label("", "secondary")
        clear = button(tr("清除全部"), on_click=self.clear_all)
        done = button(tr("完成"), "primary", on_click=self.accept)
        done.setDefault(True)
        self.footer.addWidget(self.summary)
        self.add_footer(clear, done)
        for f in state.filters:
            self._build_row(f)
        if not state.filters:
            self.add_row()
        self.refresh()

    def refresh(self):
        shown = len(apply_filters(library.photos, state.filters))
        self.summary.setText(tr('{0:,} / {1:,} 張').format(shown, len(library.photos)))
        self.on_change(False)

    def add_row(self):
        f = {"field": "", "value": "", "mode": "include"}
        state.filters.append(f)
        self._build_row(f)
        self.refresh()

    def clear_all(self):
        state.filters.clear()
        while self.rows.count() > 1:
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.refresh()

    def _build_row(self, f):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        field_box = QComboBox()
        field_box.addItem(tr("選擇欄位"), "")
        for ff in FILTER_FIELDS:
            field_box.addItem(ff["label"], ff["key"])
        field_box.setCurrentIndex(max(0, field_box.findData(f["field"])))
        value_box = QComboBox()
        value_box.setMinimumWidth(220)
        mode = Segmented([("include", tr("包含")), ("exclude", tr("不包含"))], f.get("mode", "include"), compact=True)
        remove = icon_button("x", tr("移除這個條件"), size=14)

        def fill_values():
            opts = options_for(f["field"], library.photos) if f["field"] else []
            value_box.blockSignals(True)
            value_box.clear()
            value_box.addItem(tr("選擇值") if opts else tr("沒有可選的值"), "")
            for v, n in opts:
                value_box.addItem(f"{tr_value(v)}（{n}）", v)
            value_box.setCurrentIndex(max(0, value_box.findData(f["value"])))
            value_box.setEnabled(bool(opts))
            value_box.blockSignals(False)

        def on_field():
            f["field"] = field_box.currentData()
            f["value"] = ""
            fill_values()
            self.refresh()

        def on_value():
            f["value"] = value_box.currentData() or ""
            self.refresh()

        def on_remove():
            if f in state.filters:
                state.filters.remove(f)
            row.deleteLater()
            self.refresh()

        fill_values()
        field_box.currentIndexChanged.connect(lambda *_: on_field())
        value_box.currentIndexChanged.connect(lambda *_: on_value())
        mode.changed.connect(lambda v: (f.__setitem__("mode", v), self.refresh()))
        remove.clicked.connect(on_remove)
        lay.addWidget(field_box, 1)
        lay.addWidget(value_box, 2)
        lay.addWidget(mode)
        lay.addWidget(remove)
        self.rows.insertWidget(self.rows.count() - 1, row)


class ToolRow(QAbstractButton):
    """工具清單的一列：圖示、名稱、右邊的箭頭。整列都是按鈕。"""

    def __init__(self, icon_name, text, sub=""):
        super().__init__()
        self.icon_name, self.text_, self.sub = icon_name, text, sub
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(56 if sub else 46)
        self.setMinimumWidth(340)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = theme.round_rect(QPainterPath(), r, 9)
        p.fillPath(path, theme.c("fill_strong" if self.underMouse() else "fill"))
        box = QRectF(10, (r.height() - 30) / 2, 30, 30)
        p.fillPath(theme.round_rect(QPainterPath(), box, 7), theme.c("accent_soft"))
        ic = icons.pixmap(self.icon_name, theme.T["accent"], 17)
        p.drawPixmap(QRectF(box.center().x() - 8.5, box.center().y() - 8.5, 17, 17), ic, QRectF(ic.rect()))
        p.setPen(theme.c("label"))
        p.setFont(theme.font("body", 600))
        if self.sub:
            p.drawText(QRectF(52, 9, r.width() - 90, 20), Qt.AlignmentFlag.AlignVCenter, self.text_)
            p.setFont(theme.font("caption"))
            p.setPen(theme.c("secondary"))
            from PySide6.QtGui import QFontMetrics
            sub = QFontMetrics(p.font()).elidedText(self.sub, Qt.TextElideMode.ElideMiddle, int(r.width() - 90))
            p.drawText(QRectF(52, 29, r.width() - 90, 18), Qt.AlignmentFlag.AlignVCenter, sub)
        else:
            p.drawText(QRectF(52, 0, r.width() - 90, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text_)
        arrow = icons.pixmap("chevron-right", theme.T["tertiary"], 14)
        p.drawPixmap(QRectF(r.width() - 26, r.center().y() - 7, 14, 14), arrow, QRectF(arrow.rect()))
        p.end()


class ToolSheet(Sheet):
    """照片卡片上的快速動作：一個對話框、一列一個工具。工具再多也只是清單變長。"""

    def __init__(self, win, photo):
        super().__init__(win, photo.name, width=420)
        self._title.setToolTip(photo.rel_path)
        for key, r in win.edit_routes():
            row = ToolRow(r["icon"], r["label"])
            row.clicked.connect(lambda _=False, k=key: (self.accept(), win.open_in_tool(k, photo)))
            self.body.addWidget(row)
        info = ToolRow("info", tr("完整資訊"))
        info.clicked.connect(lambda: (self.accept(), open_inspector(win, photo)))
        self.body.addWidget(info)
        reveal = ToolRow("folder", tr("在檔案總管中顯示"), os.path.dirname(photo.path))
        reveal.clicked.connect(lambda: (self.accept(), reveal_in_explorer(photo.path)))
        self.body.addWidget(reveal)
        self.add_footer(button(tr("取消"), on_click=self.reject))


class PhotosPage(Page):
    title = tr("照片檢視")
    uses_source = True

    def __init__(self, window):
        super().__init__(window)
        self.col_slider = Stepper([(n, str(n)) for n in range(1, 9)], state.columns, tip=tr("每排幾張"))
        self.col_slider.changed.connect(self.set_columns)
        self.filter_btn = BadgeButton(tr("篩選"), "filter")
        self.filter_btn.clicked.connect(self.open_filters)
        self.field_btn = BadgeButton(tr("EXIF 欄位"), "info")
        self.field_btn.clicked.connect(self.open_fields)
        self.count = label("—", "secondary")
        self.display = Segmented([("card", tr("卡片")), ("list", tr("列表"))], config.settings.get("photoDisplay") or "card",
                                 compact=True)
        self.display.changed.connect(self.set_display)
        self.group_box = QComboBox()
        for v, lb in GROUPS:
            self.group_box.addItem(lb, v)
        self.group_box.setCurrentIndex(max(0, self.group_box.findData(config.settings.get("photoGroup") or "none")))
        self.group_box.setToolTip(tr("群組方式"))
        self.group_box.currentIndexChanged.connect(lambda *_: self.set_group(self.group_box.currentData()))
        self.expand_btn = button(tr("全部展開"), None, "chevron-down", on_click=lambda: self.collapse_all(False))
        self.collapse_btn = button(tr("全部收合"), None, "chevron-right", on_click=lambda: self.collapse_all(True))
        self.cols_label = label(tr("每排"), "secondary")
        self.collapsed: set[str] = set()
        for w in (self.display, 8, self.cols_label, self.col_slider, 6, vline(), 6,
                  label(tr("群組"), "secondary"), self.group_box, self.expand_btn, self.collapse_btn, 6, vline(), 6,
                  self.filter_btn, self.field_btn, None, self.count):
            if w is None:
                self.toolbar.addStretch(1)
            elif isinstance(w, int):
                self.toolbar.addSpacing(w)
            else:
                self.toolbar.addWidget(w)

        self.grid = PhotoGrid("card", state.columns)
        self.grid.photo_clicked.connect(lambda p: open_inspector(self.win, p))
        self.grid.tool_clicked.connect(self.tool_menu)
        self.grid.context_requested.connect(self.context_menu)
        self.grid.header_clicked.connect(self.toggle_group)
        self.grid.display = self.display.value()
        self.empty = EmptyState("image")
        self.empty.set(tr("尚未載入照片"), tr("從上方「開啟資料夾」開始。"), button(tr("開啟資料夾…"), "primary", "folder",
                                                                   on_click=lambda: self.win.source.open_folder()))
        self.root.addWidget(self.grid, 1)
        self.root.addWidget(self.empty, 1)

        self._dirty = True
        library.changed.connect(self.mark_dirty)
        library.stats_changed.connect(self._paint_counts)
        state.fields_changed.connect(self.grid.refresh)
        categories.changed.connect(self._on_categories)
        self._sync_controls()
        self._paint_counts()

    # ---------------------------------------------------------------- 狀態
    def mark_dirty(self):
        self._dirty = True
        if self.isVisible():
            self.render()

    def on_show(self):
        if self._dirty:
            self.render()
        else:
            self.grid.viewport().update()

    def visible_photos(self):
        return apply_filters(library.photos, state.filters)

    def _sync_controls(self):
        grouped = self.group_box.currentData() != "none"
        self.expand_btn.setVisible(grouped)
        self.collapse_btn.setVisible(grouped)
        cards = self.display.value() == "card"
        for w in (self.cols_label, self.col_slider):
            w.setVisible(cards)

    def _on_categories(self):
        if self.group_box.currentData() == "category":
            self.mark_dirty()
        else:
            self.grid.viewport().update()

    def set_display(self, mode):
        config.settings.set("photoDisplay", mode)
        self._sync_controls()
        self.grid.set_display(mode)

    def set_group(self, how):
        config.settings.set("photoGroup", how)
        self.collapsed.clear()
        self._sync_controls()
        if how in NEEDS_EXIF:
            library.scan_all_info()          # 日期、相機、鏡頭都在 EXIF 裡
        if how == "similar":
            library.scan_hashes(on_done=self.render)
        self.render()

    def toggle_group(self, header):
        if header.key in self.collapsed:
            self.collapsed.discard(header.key)
        else:
            self.collapsed.add(header.key)
        self.render()

    def collapse_all(self, on):
        how = self.group_box.currentData()
        self.collapsed = {k for k, _, _ in group(self.visible_photos(), how)} if on else set()
        self.render()

    def build_rows(self, photos):
        how = self.group_box.currentData()
        if how == "none" or not photos:
            return photos
        rows = []
        for key, title, items in group(photos, how):
            h = GroupHeader(key, title, len(items), key in self.collapsed)
            rows.append(h)
            if not h.collapsed:
                rows += items
        return rows

    def render(self):
        self._dirty = False
        photos = self.visible_photos()
        total = len(library.photos)
        self.grid.set_rows(self.build_rows(photos))
        has = bool(photos)
        self.grid.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            if total:
                self.empty.set(tr("沒有符合條件的照片"), tr("調整或清除篩選條件。"),
                               button(tr("清除篩選"), on_click=self.clear_filters))
            else:
                self.empty.set(tr("尚未載入照片"), tr("從上方「開啟資料夾」開始，或選擇幾張照片。"),
                               button(tr("開啟資料夾…"), "primary", "folder", on_click=lambda: self.win.source.open_folder()))
        self._paint_counts(photos)

    def _paint_counts(self, photos=None):
        total = len(library.photos)
        shown = len(photos) if photos is not None else len(self.visible_photos())
        self.count.setText("—" if not total else tr('{0:,} 張').format(total) if shown == total else tr('{0:,} / {1:,} 張').format(shown, total))
        on = sum(1 for k, _, _ in FIELD_DEFS if state.fields.get(k))
        self.field_btn.setBadge(f"{on}/{len(FIELD_DEFS)}", quiet=True)
        n = active_count(state.filters)
        self.filter_btn.setBadge(str(n) if n else "")

    def set_columns(self, n):
        state.columns = n
        config.settings.set("columns", n)
        self.grid.set_columns(n)

    def clear_filters(self):
        state.filters.clear()
        self.render()

    # ---------------------------------------------------------------- 對話框
    def open_fields(self):
        FieldDialog(self.win, self._paint_counts).exec()

    def open_filters(self):
        # 值的清單是從已讀到的 EXIF 長出來的，所以還沒分析完就先在背景補掃。
        library.scan_all_info()
        dirty = {"v": False}

        def changed(_):
            dirty["v"] = True
            self._paint_counts(self.visible_photos())

        FilterDialog(self.win, changed).exec()
        state.filters[:] = [f for f in state.filters if f.get("field")]
        if dirty["v"]:
            self.render()

    # ---------------------------------------------------------------- 選單
    def tool_menu(self, photo, pos=None):
        ToolSheet(self.win, photo).exec()

    def context_menu(self, photo, pos):
        menu = QMenu(self)
        menu.addAction(icons.icon("info", theme.T["label"]), tr("完整資訊"), lambda: open_inspector(self.win, photo))
        sub = menu.addMenu(icons.icon("tool", theme.T["label"]), tr("用編輯工具開啟"))
        for key, r in self.win.edit_routes():
            sub.addAction(icons.icon(r["icon"], theme.T["label"]), r["label"],
                          lambda k=key: self.win.open_in_tool(k, photo))
        tag = menu.addMenu(icons.icon("tag", theme.T["label"]), tr("標記分類"))
        for cat in categories.all():
            act = tag.addAction(f"{cat.key.upper()}   {cat.name}")
            act.setCheckable(True)
            act.setChecked(photo.cat_id == cat.id)
            act.setEnabled(not photo.organized)
            act.triggered.connect(lambda _=False, c=cat: self._mark(photo, c.id))
        tag.addSeparator()
        tag.addAction(tr("清除標記"), lambda: self._mark(photo, None)).setEnabled(bool(photo.cat_id) and not photo.organized)
        menu.addSeparator()
        menu.addAction(icons.icon("folder", theme.T["label"]), tr("在檔案總管中顯示"), lambda: reveal_in_explorer(photo.path))
        menu.exec(pos)

    def _mark(self, photo, cid):
        photo.cat_id = None if cid is None or photo.cat_id == cid else cid
        library.save_marks()
        cat = categories.by_id(photo.cat_id)
        history.mark(photo.name, cat.name if cat else None)
        if self.group_box.currentData() == "category":
            self.render()
        else:
            self.grid.model_.touch(photo)
