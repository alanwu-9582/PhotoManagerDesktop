"""EXIF 相框（對應網頁版 js/tools/exif-frame/index.js）。

讀出相機、鏡頭與曝光參數，排成相框再存回去。相框可以只有下面一條、只有上面、
上下、四邊全框，或是拍立得那種下面特別寬的。
"""
from __future__ import annotations

from ...i18n import tr

import re

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QPushButton

from ...engine import exif as exif_mod
from ...engine import image as imgmod
from ...ui.widgets import (Flag, ColorButton, Segmented, SliderField, button, field, hbox, notify, row, section, wrap)
from ..common import IMAGE_FILTER, Stage, ToolPage, safe_name
from .frame import DEFAULTS, MODE_PAD, MODES, render_frame

PREVIEW_WIDTH = 1400


def luminance(hexv):
    m = re.match(r"^#?([0-9a-f]{6})$", str(hexv).strip(), re.I)
    if not m:
        return 1
    n = int(m[1], 16)
    ch = []
    for v in ((n >> 16) & 255, (n >> 8) & 255, n & 255):
        s = v / 255
        ch.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def format_date(raw):
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2})", str(raw or "").strip())
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else str(raw or "").strip()


def fields_from_exif(info):
    if not info:
        return {"model": "", "date": "", "brand": "", "params": ""}
    make = (info.get("make") or "").strip()
    model = (info.get("model") or "").strip()
    # 有些機身把廠牌也寫進型號（"NIKON D750"），兩邊都顯示就重複了。
    if make and model.upper().startswith(make.upper()):
        model = model[len(make):].strip() or model
    params = " ".join(v for v in (
        re.sub(r"\s+", "", info["focalLength"]) if info.get("focalLength") else None,
        info.get("fNumber"),
        re.sub(r"\s+", "", info["exposureTime"]) if info.get("exposureTime") else None,
        f"ISO{info['iso']}" if info.get("iso") is not None else None,
    ) if v)
    return {"model": model, "date": format_date(info.get("dateTimeOriginal") or info.get("dateTime")),
            "brand": make, "params": params}


class FrameStage(Stage):
    def __init__(self):
        super().__init__()
        self.pix: QPixmap | None = None

    def paint_content(self, p: QPainter):
        if not self.pix:
            return
        pad = 18
        aw, ah = self.width() - pad * 2, self.height() - pad * 2
        k = min(aw / self.pix.width(), ah / self.pix.height())
        w, h = self.pix.width() * k, self.pix.height() * k
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.pix, QRectF(self.pix.rect()))


class ExifFramePage(ToolPage):
    title = tr("EXIF 相框")
    stage_class = FrameStage

    def __init__(self, window):
        super().__init__(window)
        self.o = dict(DEFAULTS)
        self.img: QImage | None = None
        self.small: QImage | None = None
        self.logo: QImage | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._paint)
        self.add_output_buttons(tr("輸出加上相框的照片（Ctrl+S）"))
        self._build()

    def _build(self):
        F = self.form
        o = self.o
        F.addWidget(section(tr("相框")))
        self.mode = Segmented(MODES, o["mode"])
        self.mode.changed.connect(self._set_mode)
        F.addWidget(field(tr("樣式"), self.mode))
        align = Segmented([("split", tr("左右")), ("center", tr("置中"))], o["align"])
        align.changed.connect(lambda v: self._set("align", v))
        F.addWidget(field(tr("文字"), align))
        self.pad = SliderField(tr("邊框寬度"), 0, 12, 0.5, o["pad"] * 100, lambda v: f"{v:g}%")
        self.pad.changed.connect(lambda v: self._set("pad", v / 100))
        bar = SliderField(tr("資訊列高度"), 0.5, 2.2, 0.05, o["barScale"], lambda v: f"{v:.2f}×")
        bar.changed.connect(lambda v: self._set("barScale", v))
        F.addWidget(row(self.pad, bar))
        fs = SliderField(tr("字級"), 0.6, 1.6, 0.05, o["fontScale"], lambda v: f"{v:.2f}×")
        fs.changed.connect(lambda v: self._set("fontScale", v))
        rad = SliderField(tr("外框圓角"), 0, 6, 0.5, 0, lambda v: f"{v:g}%")
        rad.changed.connect(lambda v: self._set("radius", v / 100))
        prad = SliderField(tr("照片圓角"), 0, 6, 0.5, 0, lambda v: f"{v:g}%")
        prad.changed.connect(lambda v: self._set("photoRadius", v / 100))
        F.addWidget(row(fs, rad, prad))
        toggles = []
        for key, lb in (("showBar", tr("資訊列")), ("separator", tr("分隔線")), ("shadow", tr("陰影"))):
            cb = Flag(lb)
            cb.setChecked(o[key])
            cb.toggled.connect(lambda on, k=key: self._set(k, on))
            toggles.append(cb)
        F.addWidget(field(tr("開關"), wrap(hbox(*toggles, None, spacing=6))))

        F.addWidget(section(tr("顏色")))
        self.bg = ColorButton(o["bg"])
        self.bg.changed.connect(self._set_bg)
        swatches = []
        for hexv in ("#ffffff", "#f5f1e8", "#121212", "#1c1f26"):
            b = ColorButton(hexv, size=(30, 30), pick=False)
            b.clicked.connect(lambda _=False, h=hexv: (self.bg.set(h), self._set_bg(h)))
            swatches.append(b)
        self.auto_color = Flag(tr("自動"))
        self.auto_color.setChecked(True)
        self.auto_color.toggled.connect(lambda on: on and (self._auto_colors(), self.schedule()))
        F.addWidget(row(field(tr("底色"), self.bg), field(tr("常用"), wrap(hbox(*swatches, None, spacing=6))),
                        field(tr("自動配色"), self.auto_color)))
        self.text_color = ColorButton(o["textColor"])
        self.sub_color = ColorButton(o["subColor"])
        self.accent = ColorButton(o["accent"])
        for btn, key in ((self.text_color, "textColor"), (self.sub_color, "subColor"), (self.accent, "accent")):
            btn.changed.connect(lambda v, k=key: self._set(k, v))
        F.addWidget(row(field(tr("主要文字"), self.text_color), field(tr("次要文字"), self.sub_color), field(tr("品牌"), self.accent)))
        font = QComboBox()
        font.addItem(tr("無襯線"), "sans")
        font.addItem(tr("等寬"), "mono")
        font.currentIndexChanged.connect(lambda *_: self._set("font", font.currentData()))
        self.logo_btn = button(tr("上傳"), None, "upload", on_click=self.pick_logo)
        self.logo_clear = button(tr("移除"), on_click=self.clear_logo)
        self.logo_clear.hide()
        F.addWidget(row(field(tr("字體"), font), field(tr("品牌圖"), wrap(hbox(self.logo_btn, self.logo_clear, None, spacing=6)))))

        F.addWidget(section(tr("文字")))
        self.inputs = {}
        for key, lb, ph in (("model", tr("型號"), "X-T4"), ("date", tr("時間"), "2024-05-16 12:33"),
                            ("brand", tr("品牌"), "FUJIFILM"), ("params", tr("參數"), "53mm f/3.2 1/5800s ISO640"),
                            ("title", tr("標題"), tr("上下框樣式才會用到"))):
            e = QLineEdit()
            e.setPlaceholderText(ph)
            e.textEdited.connect(lambda v, k=key: self._set(k, v))
            self.inputs[key] = e
        F.addWidget(row(field(tr("型號"), self.inputs["model"]), field(tr("時間"), self.inputs["date"])))
        F.addWidget(row(field(tr("品牌"), self.inputs["brand"]), field(tr("參數"), self.inputs["params"])))
        F.addWidget(field(tr("標題"), self.inputs["title"]))

        F.addWidget(section(tr("匯出")))
        self.fmt = QComboBox()
        self.fmt.addItem("JPEG", "jpg")
        self.fmt.addItem("PNG", "png")
        self.scale = QComboBox()
        for v, lb in (("1", tr("原尺寸")), ("0.75", "75%"), ("0.5", "50%"), ("0.25", "25%")):
            self.scale.addItem(lb, v)
        F.addWidget(row(field(tr("格式"), self.fmt), field(tr("尺寸"), self.scale)))
        self.quality = SliderField(tr("JPEG 品質"), 60, 100, 1, 92, lambda v: f"{v:.0f}%")
        F.addWidget(self.quality)
        F.addStretch(1)

    # ---------------------------------------------------------------- 設定
    def _set(self, key, value):
        self.o[key] = value
        self.schedule()

    def _set_mode(self, v):
        self.o["mode"] = v
        self.o["pad"] = MODE_PAD.get(v, 0)
        self.pad.set(round(self.o["pad"] * 1000) / 10)
        self.schedule()

    def _set_bg(self, v):
        self.o["bg"] = v
        if self.auto_color.isChecked():
            self._auto_colors()
        self.schedule()

    def _auto_colors(self):
        """底色換成深色時，文字要跟著翻白，不然什麼都看不到。"""
        dark = luminance(self.o["bg"]) < 0.45
        self.o["textColor"] = "#ffffff" if dark else "#121212"
        self.o["subColor"] = "#b4b4b4" if dark else "#8a8a8a"
        self.o["accent"] = self.o["textColor"]
        self.text_color.set(self.o["textColor"])
        self.sub_color.set(self.o["subColor"])
        self.accent.set(self.o["accent"])

    def pick_logo(self):
        path, _ = QFileDialog.getOpenFileName(self.win, tr("品牌圖"), "", IMAGE_FILTER + ";;SVG (*.svg)")
        if not path:
            return
        try:
            self.logo = imgmod.decode(path, 1200)
            self.logo_btn.setText(tr("更換"))
            self.logo_clear.show()
            self.schedule()
        except Exception as e:  # noqa: BLE001
            notify(tr('讀取失敗: {0}').format(e), "danger")

    def clear_logo(self):
        self.logo = None
        self.logo_btn.setText(tr("上傳"))
        self.logo_clear.hide()
        self.schedule()

    # ---------------------------------------------------------------- 讀圖
    def on_image(self, path, img):
        self.img = img
        self.small = img if img.width() <= PREVIEW_WIDTH else img.scaledToWidth(
            PREVIEW_WIDTH, Qt.TransformationMode.SmoothTransformation)
        try:
            # 別的工具存下來的結果沒有 EXIF，參數要從原始照片讀。
            info, _ = exif_mod.extract_exif(self.origin or path)
        except Exception:  # noqa: BLE001
            info = None
        vals = fields_from_exif(info)
        for k, v in vals.items():
            self.o[k] = v
            self.inputs[k].setText(v)
            self.inputs[k].setCursorPosition(0)   # 長的參數從頭顯示，不要只看到尾巴
        self.set_status(f"{img.width()}×{img.height()}" + ("" if info else tr(" · 無 EXIF")))
        self._paint()

    def schedule(self):
        if self.small is not None:
            self._timer.start()

    def _paint(self):
        if self.small is None:
            return
        out = render_frame(self.small, self.o, self.small.width(), self.logo)
        self.stage.pix = QPixmap.fromImage(out)
        self.stage.update()

    def output_name(self):
        return f"{safe_name(self.path)}_frame.{self.fmt.currentData()}"

    def output_job(self):
        if self.img is None:
            return None
        src, o, logo = self.img, dict(self.o), self.logo
        width = round(self.img.width() * float(self.scale.currentData()))

        def job(progress):
            progress(0.2, tr("排版與繪製相框"))
            return render_frame(src, o, width, logo)

        return job
