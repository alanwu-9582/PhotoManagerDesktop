"""批次改名（對應網頁版 js/pages/rename.js）。

一條命名樣式 + 幾個 token，下面即時列出「原本 → 之後」。按下去才真的動到硬碟。
改名是不可逆的（檔案系統沒有復原），所以每一個新檔名都要先在預覽裡看得到，撞名也要先標出來。
預覽清單是虛擬化的，幾千列也不必分頁。
"""
from __future__ import annotations


import datetime as dt
import re

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QComboBox, QFrame, QLineEdit, QListView, QSpinBox,
                               QStyledItemDelegate, QToolButton, QWidget)

from ..engine import organize as org
from ..engine.history import history
from ..engine.library import library
from ..ui import dialogs, icons, theme
from .. import config
from ..ui.widgets import icon_button, Flag, EmptyState, FlowLayout, button, field, hbox, label, notify, vbox
from .base import Page

TOKENS = [("date", "20260923"), ("time", "174218"), ("camera", "ILCE-6400"), ("lens", "E 18-135mm"),
          ("focal", "35mm"), ("iso", "ISO100"), ("fnumber", "f5.6"), ("shutter", "1-250s"),
          ("folder", "DCIM"), ("original", "DSC01234"), ("index", "001")]

# 第一次開啟時的預設命名規則；之後使用者存的會放在設定檔裡。
BUILTIN_PRESETS = [
    {"name": "日期_相機_流水號", "pattern": "{date}_{camera}_{index}"},
    {"name": "日期_時間", "pattern": "{date}_{time}"},
    {"name": "資料夾_流水號", "pattern": "{folder}_{index}"},
    {"name": "日期_原檔名", "pattern": "{date}_{original}"},
    {"name": "相機_焦段_光圈_快門", "pattern": "{camera}_{focal}_{fnumber}_{shutter}_{index}"},
]
PRESET_KEYS = ("pattern", "start", "pad", "case", "find", "replace")


def load_presets():
    saved = config.settings.get("renamePresets")
    if isinstance(saved, list) and all(isinstance(x, dict) and x.get("name") for x in saved):
        return saved
    return [dict(x) for x in BUILTIN_PRESETS]


_DT = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def base_name(name):
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def ext_name(name):
    dot = name.rfind(".")
    return name[dot + 1:] if dot > 0 else ""


def sanitize(v):
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]', "_", str(v or ""))).strip()


def shot_date(p):
    m = _DT.match(str((p.info or {}).get("dateTimeOriginal") or ""))
    if m:
        return m[1] + m[2] + m[3], m[4] + m[5] + m[6]
    if p.mtime:
        d = dt.datetime.fromtimestamp(p.mtime)
        return d.strftime("%Y%m%d"), d.strftime("%H%M%S")
    return "", ""


def token_values(p, index, pad):
    i = p.info or {}
    date, time = shot_date(p)
    folder = p.folder.split("/")[-1] if p.folder else (library.root_name if library.mode == "folder" else "")
    return {
        "date": date, "time": time,
        "camera": sanitize(i.get("model")), "lens": sanitize(i.get("lensModel")),
        "focal": re.sub(r"\s+", "", str(i.get("focalLength") or "")),
        "iso": f"ISO{i['iso']}" if i.get("iso") else "",
        "fnumber": str(i.get("fNumber") or "").replace("/", ""),
        # 檔名裡不能有斜線，1/250 s 只好寫成 1-250s。
        "shutter": re.sub(r"\s+", "", str(i.get("exposureTime") or "").replace("/", "-")),
        "folder": sanitize(folder), "original": base_name(p.name),
        "index": str(index).zfill(pad),
    }


def parse_regex(text):
    """"/DSC_(\\d+)/i" 或直接一段樣式。False = 寫錯了，None = 沒填。"""
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.match(r"^/(.*)/([gimsuy]*)$", raw)
    try:
        if m:
            flags = re.I if "i" in m[2] else 0
            flags |= re.M if "m" in m[2] else 0
            flags |= re.S if "s" in m[2] else 0
            return re.compile(m[1], flags)
        return re.compile(raw)
    except re.error:
        return False


def js_replacement(rep: str) -> str:
    """把 JavaScript 風格的 $1 / $& 換成 Python 的 \\1 / \\g<0>。"""
    rep = rep.replace("\\", "\\\\")
    rep = re.sub(r"\$(\d+)", r"\\g<\1>", rep)
    return rep.replace("$&", r"\g<0>").replace("$$", "$")


def build_name(p, index, o):
    values = token_values(p, index, o["pad"])
    name = re.sub(r"\{(\w+)\}", lambda m: values.get(m[1], m[0]), o["pattern"] or "")
    if o["regex"]:
        try:
            name = o["regex"].sub(js_replacement(o["replace"]), name)
        except (re.error, IndexError):
            pass
    # 連續或落單的分隔符號多半是某個 token 沒有值造成的，收一收。
    name = re.sub(r"^[_\-.]+|[_\-.]+$", "", re.sub(r"[_\-.]{2,}", "_", sanitize(name)))
    if not name:
        name = base_name(p.name)
    ext = ext_name(p.name)
    if o["case"] == "lower":
        ext = ext.lower()
    elif o["case"] == "upper":
        ext = ext.upper()
    return f"{name}.{ext}" if ext else name


def build_plan(photos, o):
    seen: dict[str, int] = {}
    out = []
    for i, p in enumerate(photos):
        name = build_name(p, o["start"] + i, o)
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        out.append({"photo": p, "name": name, "duplicate": seen[key] > 1, "changed": name != p.name})
    return out


class PlanModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.UserRole:
            return self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            r = self.rows[index.row()]
            return f"{r['photo'].rel_path}\n→ {r['name']}"
        return None


class PlanDelegate(QStyledItemDelegate):
    def paint(self, p: QPainter, opt, index):
        r = index.data(Qt.ItemDataRole.UserRole)
        rect = QRectF(opt.rect)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if index.row() % 2:
            p.fillRect(rect, theme.c("fill"))
        if r["duplicate"]:
            p.fillRect(rect, QColor(255, 59, 48, 36))
        f = theme.font("callout", mono=True)
        p.setFont(f)
        fm = QFontMetrics(f)
        half = (rect.width() - 50) / 2
        old = QRectF(rect.x() + 12, rect.y(), half - 12, rect.height())
        arrow = QRectF(old.right(), rect.y(), 38, rect.height())
        new = QRectF(arrow.right(), rect.y(), half - 12, rect.height())
        p.setPen(theme.c("secondary"))
        p.drawText(old, Qt.AlignmentFlag.AlignVCenter, fm.elidedText(r["photo"].name, Qt.TextElideMode.ElideMiddle, int(old.width())))
        p.setPen(theme.c("tertiary"))
        p.drawText(arrow, Qt.AlignmentFlag.AlignCenter, "→")
        p.setPen(theme.c("red") if r["duplicate"] else theme.c("label") if r["changed"] else theme.c("tertiary"))
        p.drawText(new, Qt.AlignmentFlag.AlignVCenter, fm.elidedText(r["name"], Qt.TextElideMode.ElideMiddle, int(new.width())))

    def sizeHint(self, opt, index):  # noqa: N802
        return QSize(200, 28)


class RenamePage(Page):
    title = "批次改名"
    uses_source = True

    def __init__(self, window):
        super().__init__(window)
        self.pattern = QLineEdit("{date}_{camera}_{index}")
        self.pattern.setProperty("mono", True)
        self.pattern.setMinimumWidth(320)
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.pad = QSpinBox()
        self.pad.setRange(1, 8)
        self.pad.setValue(3)
        self.case = QComboBox()
        for v, lb in (("keep", "原樣"), ("lower", "小寫"), ("upper", "大寫")):
            self.case.addItem(lb, v)
        self.case.setCurrentIndex(1)
        self.marked_only = Flag("只改已標記")
        # 命名預設：常用的規則存起來，下次一選就好，不必重新組 token。
        self.presets = load_presets()
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(220)
        self.preset_box.setToolTip("套用存好的命名規則")
        self.preset_box.activated.connect(self.apply_preset)
        self.save_preset_btn = button("儲存為預設…", None, "plus", "把目前的命名規則存成預設", self.save_preset)
        self.del_preset_btn = icon_button("trash", "刪除這個預設", self.delete_preset, size=15)
        self._fill_presets()
        self.root.addLayout(hbox(label("命名預設", "secondary"), self.preset_box, self.save_preset_btn,
                                 self.del_preset_btn, None, spacing=8))
        top = hbox(field("命名", self.pattern), field("起號", self.start), field("位數", self.pad),
                   field("副檔名", self.case), spacing=12)
        top.setStretch(0, 1)
        mo = vbox(None, self.marked_only, spacing=0)
        top.addLayout(mo)
        self.root.addLayout(top)

        tokens_host = QWidget()
        flow = FlowLayout(tokens_host, spacing=6)
        for key, hint in TOKENS:
            b = QToolButton()
            b.setText(f"{{{key}}}  {hint}")
            b.setToolTip(f'插入 {{{key}}}（例如 {hint}）')
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QToolButton {{ border-radius: 6px; padding: 5px 10px; font-family: {theme.MONO_FAMILIES[0]}; }}")
            b.clicked.connect(lambda _=False, k=key: self.insert_token(k))
            flow.addWidget(b)
        self.root.addWidget(tokens_host)

        # 正則取代（收合）
        self.regex_toggle = button("正則取代", "plain", "chevron-right", on_click=self.toggle_regex)
        self.find = QLineEdit()
        self.find.setPlaceholderText(r"/DSC_(\d+)/i")
        self.find.setProperty("mono", True)
        self.replace = QLineEdit()
        self.replace.setPlaceholderText("IMG_$1")
        self.replace.setProperty("mono", True)
        self.regex_note = label("", "danger")
        self.regex_row = QWidget()
        self.regex_row.setLayout(hbox(self.find, self.replace, self.regex_note, spacing=8))
        self.regex_row.hide()
        self.root.addLayout(hbox(self.regex_toggle, None))
        self.root.addWidget(self.regex_row)

        self.apply_btn = button("套用改名", "primary", None, "直接在硬碟上改檔名", self.apply)
        self.summary = label("—", "secondary")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.result = label("", None, wrap=True)
        self.result.setObjectName("Banner")
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.hide()
        self.root.addLayout(hbox(self.apply_btn, 6, self.summary, None))
        self.root.addWidget(self.result)

        self.model = PlanModel()
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(PlanDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        frame = QFrame()
        frame.setProperty("group", True)
        frame.setLayout(vbox(self.list, margins=(0, 4, 0, 4)))
        self.frame = frame
        self.empty = EmptyState("copy")
        self.root.addWidget(frame, 1)
        self.root.addWidget(self.empty, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.render)
        for w in (self.pattern, self.find, self.replace):
            w.textChanged.connect(lambda *_: self._timer.start())
        for w in (self.start, self.pad):
            w.valueChanged.connect(lambda *_: self.render())
        self.case.currentIndexChanged.connect(lambda *_: self.render())
        self.marked_only.toggled.connect(lambda *_: self.render())
        library.changed.connect(self._maybe_render)
        self._dirty = True

    # ---------------------------------------------------------------- 命名預設
    def _fill_presets(self, select=None):
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItem("選擇預設…", None)
        for i, pr in enumerate(self.presets):
            self.preset_box.addItem(f"{pr['name']}    {pr.get('pattern', '')}", i)
        self.preset_box.setCurrentIndex(0 if select is None else select + 1)
        self.preset_box.blockSignals(False)
        self.del_preset_btn.setEnabled(select is not None)

    def _current_settings(self):
        return {"pattern": self.pattern.text(), "start": self.start.value(), "pad": self.pad.value(),
                "case": self.case.currentData(), "find": self.find.text(), "replace": self.replace.text()}

    def apply_preset(self, *_):
        i = self.preset_box.currentData()
        self.del_preset_btn.setEnabled(i is not None)
        if i is None:
            return
        pr = self.presets[i]
        self.pattern.setText(pr.get("pattern", ""))
        self.start.setValue(int(pr.get("start", 1)))
        self.pad.setValue(int(pr.get("pad", 3)))
        self.case.setCurrentIndex(max(0, self.case.findData(pr.get("case", "lower"))))
        self.find.setText(pr.get("find", ""))
        self.replace.setText(pr.get("replace", ""))
        if pr.get("find") and not self.regex_row.isVisible():
            self.toggle_regex()
        self.render()

    def _store(self):
        config.settings.set("renamePresets", [dict(x) for x in self.presets])

    def save_preset(self):
        from PySide6.QtWidgets import QInputDialog
        cur = self.preset_box.currentData()
        default = self.presets[cur]["name"] if cur is not None else self.pattern.text()
        name, ok = QInputDialog.getText(self.win, "儲存命名預設", "預設名稱：", text=default)
        name = (name or "").strip()
        if not ok or not name:
            return
        data = {"name": name[:40], **self._current_settings()}
        idx = next((i for i, pr in enumerate(self.presets) if pr["name"] == data["name"]), None)
        if idx is None:
            self.presets.append(data)
            idx = len(self.presets) - 1
        else:
            self.presets[idx] = data
        self._store()
        self._fill_presets(idx)
        notify('已儲存命名預設「{0}」'.format(data['name']), "success")

    def delete_preset(self):
        i = self.preset_box.currentData()
        if i is None:
            return
        name = self.presets[i]["name"]
        if not dialogs.confirm(self.win, f'刪除命名預設「{name}」？', "只會刪掉這個預設，不會動到任何檔案。",
                               confirm_text="刪除"):
            return
        del self.presets[i]
        self._store()
        self._fill_presets()

    def _maybe_render(self):
        if self.isVisible():
            self.render()
        else:
            self._dirty = True

    def on_show(self):
        # 樣式裡的 {camera} / {date} 都來自 EXIF，沒掃過就全是空的 —— 一進來就先補掃。
        library.scan_all_info()
        self.render()

    def toggle_regex(self):
        on = not self.regex_row.isVisible()
        self.regex_row.setVisible(on)
        name = "chevron-down" if on else "chevron-right"
        self.regex_toggle.setIcon(icons.icon(name, theme.T["accent"]))
        self.regex_toggle._icon_name = name

    def insert_token(self, key):
        self.pattern.insert(f"{{{key}}}")
        self.pattern.setFocus()
        self.render()

    def opts(self):
        rx = parse_regex(self.find.text())
        return {"pattern": self.pattern.text(), "start": self.start.value(), "pad": self.pad.value(),
                "case": self.case.currentData(), "regex": rx or None, "regex_bad": rx is False,
                "replace": self.replace.text()}

    def targets(self):
        return [p for p in library.photos if p.cat_id] if self.marked_only.isChecked() else library.photos

    def render(self):
        self._dirty = False
        o = self.opts()
        self.regex_note.setText("這段正則寫錯了" if o["regex_bad"] else "")
        self.find.setProperty("invalid", o["regex_bad"])
        self.find.style().unpolish(self.find)
        self.find.style().polish(self.find)
        photos = self.targets()
        plan = build_plan(photos, o)
        changed = sum(1 for r in plan if r["changed"])
        dup = sum(1 for r in plan if r["duplicate"])
        if photos:
            s = f'<b>{changed:,}</b> / {len(photos):,} 張會改名'
            if dup:
                s += f" · <span style='color:{theme.T['red']}'>{dup} 個撞名</span>"
            self.summary.setText(s)
        else:
            self.summary.setText("—")
        self.apply_btn.setEnabled(bool(changed) and bool(library.mode))
        self.model.set_rows(plan)
        self.frame.setVisible(bool(photos))
        self.empty.setVisible(not photos)
        if not photos:
            self.empty.set("沒有符合的照片" if library.photos else "尚未載入照片",
                           "取消「只改已標記」看全部。" if library.photos else "開啟資料夾後，這裡會列出每一張的新檔名。")

    def apply(self):
        o = self.opts()
        plan = [r for r in build_plan(self.targets(), o) if r["changed"]]
        if not plan:
            return
        sample = "\n".join(f"  {r['photo'].name}  →  {r['name']}" for r in plan[:5])
        more = f'\n  …還有 {len(plan) - 5} 個' if len(plan) > 5 else ""
        if not dialogs.confirm(self.win, f'改名 {len(plan)} 個檔案？',
                               f'{sample}{more}\n\n檔案會直接在硬碟上改名（不會經過資源回收筒）。',
                               tone="danger", confirm_text="開始改名"):
            return
        self.apply_btn.setEnabled(False)
        items = [(r["photo"], r["name"]) for r in plan]
        self.win.run_task("改名中", lambda cb: org.rename(library, items, cb), self._done)

    def _done(self, result, error):
        if error:
            dialogs.alert(self.win, "改名失敗", str(error), tone="danger")
        else:
            library.save_marks_now()
            failed = result["failed"]
            history.add("rename", '改名 {0} 個檔案'.format(result['renamed']), result["done"],
                        [(f["name"], f["message"]) for f in failed])
            html = '已改名 <b>{0}</b>'.format(result['renamed'])
            if failed:
                html += f'<br>失敗 <b>{len(failed)}</b>：<br>' + "<br>".join(
                    f"{f['name']} — {f['message']}" for f in failed[:8])
            self.result.setText(html)
            self.result.setProperty("tone", "error" if failed else "ok")
            self.result.style().unpolish(self.result)
            self.result.style().polish(self.result)
            self.result.show()
            notify('已改名 {0} 個檔案'.format(result['renamed']), "danger" if failed else "success")
        library.changed.emit()
        library.stats_changed.emit()
