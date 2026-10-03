"""一張照片的完整資訊（對應網頁版 js/app/exif-inspector.js）。

平常卡片上只放幾個欄位，這裡放全部：檔案、相機、鏡頭、曝光、色彩、GPS，
最後再附一份沒整理過的 Raw EXIF（連相機的私有欄位都列出來）。
Raw EXIF 是開這個視窗時才重新讀一次檔案的，整批掃描時不留，省記憶體。
"""
from __future__ import annotations


import datetime as dt
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFormLayout, QHeaderView, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QAbstractItemView)

from ..engine import image as imgmod
from ..engine.library import library
from ..state import fmt_bytes
from . import theme
from .dialogs import Sheet, scroll
from .widgets import Segmented, button, label, notify

TABS = [("basic", "Basic"), ("exposure", "Exposure"), ("camera", "Camera"), ("lens", "Lens"),
        ("color", "Color"), ("gps", "GPS"), ("raw", "Raw EXIF")]


def fmt_exif_date(v):
    if not v:
        return None
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(v))
    return f"{m[1]}/{m[2]}/{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else str(v)


def _v(v):
    return None if v is None else str(v)


def groups_for(photo):
    i = photo.info or {}
    gps = i.get("gps")
    w, h = i.get("width"), i.get("height")
    container = {"heif": "HEIF / HEIC", "jpeg": "JPEG", "tiff": "TIFF"}.get(i.get("container"))
    coord = lambda v, ref: None if v is None else f"{abs(v):.4f}° {ref}"  # noqa: E731
    return {
        "basic": [
            ("檔名", photo.name),
            ("路徑", photo.path),
            ("格式", photo.ext or None),
            ("尺寸", f"{w} × {h}" if w and h else None),
            ("像素", f"{w * h / 1e6:.1f} MP" if w and h else None),
            ("檔案大小", fmt_bytes(photo.size) if photo.size else None),
            ("拍攝時間", fmt_exif_date(i.get("dateTimeOriginal"))),
            ("修改時間", dt.datetime.fromtimestamp(photo.mtime).strftime("%Y/%m/%d %H:%M:%S") if photo.mtime else None),
            ("方向", i.get("orientation")),
            ("容器", container),
        ],
        "exposure": [
            ("焦段", i.get("focalLength")), ("35mm 等效", i.get("focalLength35mm")),
            ("光圈", i.get("fNumber")), ("快門", i.get("exposureTime")), ("ISO", i.get("iso")),
            ("曝光補償", i.get("exposureBias")), ("曝光模式", _v(i.get("exposureProgram"))),
            ("測光模式", _v(i.get("meteringMode"))), ("閃光燈", _v(i.get("flash"))),
            ("場景類型", _v(i.get("sceneCaptureType"))),
        ],
        "camera": [("製造商", i.get("make")), ("型號", i.get("model")), ("韌體 / 軟體", i.get("software")),
                   ("相機時間", fmt_exif_date(i.get("dateTime")))],
        "lens": [("鏡頭", i.get("lensModel")), ("焦段", i.get("focalLength")), ("最大光圈", i.get("fNumber"))],
        "color": [("色域", _v(i.get("colorSpace"))), ("白平衡", _v(i.get("whiteBalance"))), ("創意風格", i.get("creativeStyle"))],
        "gps": [
            ("緯度", coord(gps["latitude"], gps["latitudeRef"])),
            ("經度", coord(gps["longitude"], gps["longitudeRef"])),
            ("海拔", None if gps.get("altitude") is None else f"{gps['altitude']:.1f} m"),
        ] if gps else [],
    }


class Inspector(Sheet):
    def __init__(self, parent, photo):
        super().__init__(parent, "照片資訊", width=640, height=560)
        self.photo = photo
        # Raw EXIF 現在才讀（以及還沒掃到的那幾張的一般欄位）。一張只要幾毫秒，直接同步讀。
        info = library.read_info_now(photo, want_raw=True)
        self.raw = (info or {}).get("raw") or {"ifd0": [], "exif": [], "gps": []}
        cur = photo.info
        if cur is not None and (not cur.get("width") or not cur.get("height")):
            # 有些檔案沒寫 PixelXDimension，就不解碼直接量一次檔頭。
            size = imgmod.image_size(photo.path)
            if size:
                cur["width"], cur["height"] = size
        self.groups = groups_for(photo)

        tabs = [t for t in TABS if t[0] != "gps" or self.groups["gps"]]
        self.tabs = Segmented(tabs, "basic")
        self.tabs.changed.connect(self.show_tab)
        self.stack = QStackedWidget()
        self.pages = {}
        for key, _ in tabs:
            page = self._raw_page() if key == "raw" else self._rows_page(self.groups.get(key, []))
            self.pages[key] = self.stack.addWidget(page)
        self.body.addWidget(self.tabs)
        self.body.addWidget(self.stack, 1)

        foot = label(photo.name, "caption")
        self.copy_btn = button("複製", None, "copy", on_click=self.copy)
        done = button("完成", "primary", on_click=self.accept)
        done.setDefault(True)
        self.footer.addWidget(foot)
        self.add_footer(self.copy_btn, done)

    def show_tab(self, key):
        self.stack.setCurrentIndex(self.pages[key])

    def _rows_page(self, rows):
        kept = [(k, v) for k, v in rows if v is not None and v != ""]
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(4, 8, 4, 4)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        if not kept:
            form.addRow(label("—", "secondary"))
        for k, v in kept:
            val = label(str(v), None, wrap=True, selectable=True)
            form.addRow(label(k, "secondary"), val)
        return scroll(w)

    def _raw_page(self):
        sections = [(t, self.raw.get(k) or []) for t, k in (("IFD0", "ifd0"), ("Exif", "exif"), ("GPS", "gps"))]
        sections = [s for s in sections if s[1]]
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)
        if not sections:
            lay.addWidget(label("—", "secondary"))
        for title, rows in sections:
            lay.addWidget(label(title, "section"))
            t = QTableWidget(len(rows), 3)
            t.setHorizontalHeaderLabels(["Tag", "Name", "Value"])
            t.verticalHeader().hide()
            t.setShowGrid(False)
            t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            mono = theme.font("callout", mono=True)
            for r, row in enumerate(rows):
                for c, text in enumerate((row["tag"], row["name"] or "—", row["value"])):
                    it = QTableWidgetItem(text)
                    if c != 1:
                        it.setFont(mono)
                    if c == 2:
                        it.setToolTip(text)
                    t.setItem(r, c, it)
            t.resizeRowsToContents()
            t.setFixedHeight(t.horizontalHeader().height() + sum(t.rowHeight(i) for i in range(len(rows))) + 4)
            lay.addWidget(t)
        lay.addStretch(1)
        return scroll(w)

    def copy(self):
        lines = [f"{k}\t{v}" for rows in self.groups.values() for k, v in rows if v is not None and v != ""]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.copy_btn.setText("已複製")
        notify("已複製照片資訊", "success")


def open_inspector(parent, photo):
    Inspector(parent, photo).exec()
