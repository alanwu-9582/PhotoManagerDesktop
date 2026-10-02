"""照片色卡明信片（對應網頁版 js/tools/palette-card/index.js）。

取出照片的代表色，排成一張可以存下來的明信片。卡片是直接操作的：
拖色卡面板換位置（會吸附到置中、上緣、正中、下緣，並畫出參考線）、
拖照片調構圖、滾輪縮放照片。參考線只畫在畫面上，不會進到存下來的圖裡。
也可以直接 Ctrl+V 貼上剪貼簿裡的圖片。
"""
from __future__ import annotations

from ...i18n import tr

import os
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton, QWidget

from ...engine import image as imgmod
from ...ui import theme
from ...ui.widgets import FlowLayout, Segmented, button, field, notify, row, section
from ..common import Stage, ToolPage, safe_name
from .card import LAYOUTS, SIZES, geometry, panel_rect, photo_slack, render_card, snap_panel
from .palette import extract_palette

SAMPLE_EDGE = 160
PHOTO_EDGE = 2400
COUNTS = [4, 5, 6, 8]


def clamp(v, lo, hi):
    return min(hi, max(lo, v))


class CardStage(Stage):
    def __init__(self, tool):
        super().__init__()
        self.tool = tool
        self.pix: QPixmap | None = None
        self.guides = []
        self._drag = None
        self.setMouseTracking(True)

    def card_rect(self) -> QRectF:
        if not self.pix:
            return QRectF()
        pad = 18
        aw, ah = self.width() - pad * 2, self.height() - pad * 2
        k = min(aw / self.pix.width(), ah / self.pix.height())
        w, h = self.pix.width() * k, self.pix.height() * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def to_canvas(self, pos) -> QPointF:
        r = self.card_rect()
        if not r.width():
            return QPointF()
        st = self.tool.st
        return QPointF((pos.x() - r.x()) * st["size"][2] / r.width(), (pos.y() - r.y()) * st["size"][3] / r.height())

    def paint_content(self, p: QPainter):
        if not self.pix:
            return
        r = self.card_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(r, self.pix, QRectF(self.pix.rect()))
        if not self.guides:
            return
        st = self.tool.st
        k = r.width() / st["size"][2]
        card = geometry(st)["card"]
        pen = QPen(QColor(255, 255, 255, 240), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        for axis, at in self.guides:
            if axis == "x":
                x = r.x() + at * k
                p.drawLine(QPointF(x, r.y() + card.y() * k), QPointF(x, r.y() + card.bottom() * k))
            else:
                y = r.y() + at * k
                p.drawLine(QPointF(r.x() + card.x() * k, y), QPointF(r.x() + card.right() * k, y))

    def mousePressEvent(self, e):
        st = self.tool.st
        if self.empty or st["image"] is None or e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self.to_canvas(e.position())
        g = geometry(st)
        if g["panel"] and panel_rect(g["panel"]).contains(pt):
            pr = panel_rect(g["panel"])
            card = g["card"]
            self._drag = {"kind": "panel", "from": pt, "card": card,
                          "center": QPointF((pr.center().x() - card.x()) / card.width(),
                                            (pr.center().y() - card.y()) / card.height())}
        else:
            sx, sy = photo_slack(st)
            if not sx and not sy:
                return   # 照片剛好貼齊，沒有可以拖的餘裕
            self._drag = {"kind": "photo", "from": pt, "pan": dict(st["pan"]), "slack": (sx, sy)}
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        st = self.tool.st
        if not self._drag:
            if st["image"] is not None and not self.empty:
                g = geometry(st)
                over = g["panel"] and panel_rect(g["panel"]).contains(self.to_canvas(e.position()))
                self.setCursor(Qt.CursorShape.SizeAllCursor if over else Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        pt = self.to_canvas(e.position())
        d = self._drag
        if d["kind"] == "panel":
            card = d["card"]
            wanted = QPointF(card.x() + d["center"].x() * card.width() + pt.x() - d["from"].x(),
                             card.y() + d["center"].y() * card.height() + pt.y() - d["from"].y())
            center, self.guides = snap_panel(st, wanted)
            st["panel"] = center
        else:
            sx, sy = d["slack"]
            st["pan"] = {"x": clamp(d["pan"]["x"] + (pt.x() - d["from"].x()) / sx, -1, 1) if sx else 0,
                         "y": clamp(d["pan"]["y"] + (pt.y() - d["from"].y()) / sy, -1, 1) if sy else 0}
        self.tool.paint_now(fast=True)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None
            self.guides = []
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.tool.paint_now()
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        st = self.tool.st
        if st["image"] is None:
            return
        step = 1.12 if e.angleDelta().y() > 0 else 1 / 1.12
        nz = clamp(st["zoom"] * step, 1, 4)
        if nz != st["zoom"]:
            st["zoom"] = nz
            self.tool.paint_now(fast=True)
            self.tool._settle.start()


class PaletteCardPage(ToolPage):
    title = tr("照片色卡")
    png_only = True

    def __init__(self, window):
        super().__init__(window)
        self.st = {"image": None, "sample": None, "colors": [], "layout": "glass", "size": SIZES[0], "count": 6,
                   "snap": False, "background": "auto", "title": "", "subtitle": "", "panel": None,
                   "pan": {"x": 0, "y": 0}, "zoom": 1.0}
        self.chips_host = QWidget()
        self.chips = FlowLayout(self.chips_host, spacing=6)
        self.meta.layout().insertWidget(0, self.chips_host, 1)
        self.actions.addWidget(button(tr("複製色碼"), None, "copy", tr("把全部色碼複製到剪貼簿"), self.copy_all))
        self.actions.addWidget(button(tr("重設位置"), None, "reset", tr("面板與照片回到預設位置"), self.reset_placement))
        self.add_output_buttons(tr("輸出明信片（Ctrl+S）"))
        self.save_btn.setText(tr("儲存 PNG…"))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.paint_now)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(150)
        self._settle.timeout.connect(self.paint_now)
        paste = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste.activated.connect(self.paste)
        self._build()

    def make_stage(self):
        return CardStage(self)

    def _build(self):
        F = self.form
        st = self.st
        F.addWidget(section(tr("版型")))
        lay = Segmented(LAYOUTS, st["layout"])
        lay.changed.connect(lambda v: self._set("layout", v))
        F.addWidget(lay)
        size = QComboBox()
        for s in SIZES:
            size.addItem(s[1], s[0])
        size.currentIndexChanged.connect(lambda i: self._set("size", SIZES[i]))
        count = QComboBox()
        for n in COUNTS:
            count.addItem(tr('{0} 色').format(n), n)
        count.setCurrentIndex(COUNTS.index(6))
        count.currentIndexChanged.connect(lambda i: (st.__setitem__("count", COUNTS[i]), self.recolor()))
        F.addWidget(row(field(tr("尺寸"), size), field(tr("顏色數量"), count)))
        snap = Segmented([("raw", tr("原色")), ("snap", tr("整齊色碼"))], "raw")
        snap.changed.connect(lambda v: (st.__setitem__("snap", v == "snap"), self.recolor()))
        bg = Segmented([("auto", tr("自動")), ("light", tr("淺")), ("dark", tr("深")), ("none", tr("無"))], "auto")
        bg.changed.connect(lambda v: self._set("background", v))
        F.addWidget(row(field(tr("色碼"), snap), field(tr("底色"), bg)))
        title = QLineEdit()
        title.setPlaceholderText("Title")
        title.textEdited.connect(lambda v: self._set("title", v))
        sub = QLineEdit()
        sub.setPlaceholderText("Subtitle")
        sub.textEdited.connect(lambda v: self._set("subtitle", v))
        F.addWidget(row(field(tr("標題"), title), field(tr("副標"), sub)))
        F.addStretch(1)

    def _set(self, key, value):
        self.st[key] = value
        self.schedule()

    # ---------------------------------------------------------------- 讀圖
    def on_image(self, path, img):
        edge = max(img.width(), img.height())
        if edge > PHOTO_EDGE:
            img = img.scaled(img.size() * (PHOTO_EDGE / edge), Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.st["image"] = img
        # 取色只需要一張縮圖；關掉平滑，縮圖就是直接抽樣原圖的像素。
        k = min(1.0, SAMPLE_EDGE / max(img.width(), img.height()))
        self.st["sample"] = img.scaled(max(1, round(img.width() * k)), max(1, round(img.height() * k)),
                                       Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        self.st["pan"] = {"x": 0, "y": 0}
        self.st["zoom"] = 1.0
        self.recolor()

    def recolor(self):
        if self.st["sample"] is None:
            return
        self.st["colors"] = extract_palette(self.st["sample"], self.st["count"], self.st["snap"])
        self.chips.clear()
        for c in self.st["colors"]:
            b = QPushButton(c["hex"])
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tr('複製 {0}').format(c['hex']))
            fg = "#111" if (0.2126 * c["rgb"][0] + 0.7152 * c["rgb"][1] + 0.0722 * c["rgb"][2]) > 150 else "#fff"
            b.setStyleSheet(f"QPushButton {{ background: {c['hex']}; color: {fg}; border-radius: 6px; padding: 5px 10px;"
                            f" font-family: {theme.MONO_FAMILIES[0]}; font-weight: 600; }}")
            b.clicked.connect(lambda _=False, h=c["hex"]: self.copy_one(h))
            self.chips.addWidget(b)
        n = len(self.st["colors"])
        self.set_status(tr('只挑得出 {0} 色').format(n) if n < self.st["count"] else "")
        self.schedule()

    # ---------------------------------------------------------------- 畫
    def schedule(self):
        if self.st["image"] is not None:
            self._timer.start()

    def paint_now(self, fast=False):
        self._timer.stop()
        if self.st["image"] is None:
            return
        try:
            img = render_card(self.st)
        except Exception as e:  # noqa: BLE001
            self.set_status(tr('畫不出來: {0}').format(e), "error")
            return
        self.stage.pix = QPixmap.fromImage(img)
        self.stage.update()

    # ---------------------------------------------------------------- 動作
    def reset_placement(self):
        self.st.update(panel=None, pan={"x": 0, "y": 0}, zoom=1.0)
        self.paint_now()

    def copy_one(self, hexv):
        QGuiApplication.clipboard().setText(hexv)
        notify(tr('已複製 {0}').format(hexv), "success")

    def copy_all(self):
        if not self.st["colors"]:
            notify(tr("還沒有照片"), "warning")
            return
        QGuiApplication.clipboard().setText("\n".join(c["hex"] for c in self.st["colors"]))
        notify(tr("已複製色碼"), "success")

    def paste(self):
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            return
        tmp = os.path.join(tempfile.gettempdir(), "photomanager-paste.png")
        img.save(tmp, "PNG")
        self.take(tmp, None)

    def output_name(self):
        return f"{safe_name(self.path)}-{self.st['layout']}.png"

    def output_quality(self):
        return 100

    def output_job(self):
        if self.st["image"] is None:
            return None
        st = dict(self.st)

        def job(progress):
            progress(0.2, tr("繪製明信片"))
            return render_card(st)

        return job
