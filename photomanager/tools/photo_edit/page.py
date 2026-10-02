"""裁切與旋轉（對應網頁版 js/tools/photo-edit/index.js）。

拉框裁切、拉桿轉正，上面可以疊九宮格、黃金比例、黃金螺旋等構圖格線。
格線只畫在畫面上，不會進到輸出的檔案裡。預覽畫在長邊 1400px 的工作畫布上，
匯出時才用原尺寸重畫一次。
"""
from __future__ import annotations

from ...i18n import tr

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QComboBox

from ...engine import image as imgmod
from ...ui import theme
from ...ui.widgets import (Flag, ColorButton, Segmented, SliderField, button, field, hbox, icon_button, label, notify,
                           row, section, wrap)
from ..common import Stage, ToolPage, safe_name
from .guides import DIRECTIONAL, GUIDES, guide_paths
from .transform import ASPECTS, crop_image, fit_crop, largest_inner_rect, render_work, rotated_size

PREVIEW_EDGE = 1400
MIN_PX = 28
HANDLE = 9


def clamp(v, lo, hi):
    return min(hi, max(lo, v))


class CropStage(Stage):
    def __init__(self, tool):
        super().__init__()
        self.tool = tool
        self.pix: QPixmap | None = None
        self._drag = None
        self._lum = self._lum_key = None
        self._overlay = self._overlay_key = None
        self.setMouseTracking(True)

    def frame_rect(self) -> QRectF:
        """照片整張縮進照片區之後的位置。"""
        if not self.pix:
            return QRectF()
        pad = 18
        aw, ah = self.width() - pad * 2, self.height() - pad * 2
        k = min(aw / self.pix.width(), ah / self.pix.height())
        w, h = self.pix.width() * k, self.pix.height() * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def crop_rect(self) -> QRectF:
        f = self.frame_rect()
        c = self.tool.crop
        return QRectF(f.x() + c["x"] * f.width(), f.y() + c["y"] * f.height(), c["w"] * f.width(), c["h"] * f.height())

    def handles(self):
        r = self.crop_rect()
        pts = {"nw": r.topLeft(), "ne": r.topRight(), "se": r.bottomRight(), "sw": r.bottomLeft()}
        if not self.tool.aspect_value():   # 鎖了比例就只留四個角
            pts.update({"n": QPointF(r.center().x(), r.top()), "s": QPointF(r.center().x(), r.bottom()),
                        "w": QPointF(r.left(), r.center().y()), "e": QPointF(r.right(), r.center().y())})
        return pts

    def paint_content(self, p: QPainter):
        if not self.pix:
            return
        f = self.frame_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(f, self.pix, QRectF(self.pix.rect()))
        c = self.crop_rect()
        # 框外壓暗
        outside = QPainterPath()
        outside.addRect(f)
        inner = QPainterPath()
        inner.addRect(c)
        p.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 130))
        # 格線：先換掉畫舞台底色時留下的筆刷 —— 螺旋是開放路徑，帶著筆刷畫會被填成一塊黑。
        p.setBrush(Qt.BrushStyle.NoBrush)
        overlay = self._guide_overlay(f, c)
        if overlay is not None:
            p.drawImage(f, overlay)
        # 裁切框與把手：白線 + 深色外緣，任何底色上都看得到
        p.setPen(QPen(QColor(0, 0, 0, 120), 3))
        p.drawRect(c)
        p.setPen(QPen(QColor(255, 255, 255, 240), 1.5))
        p.drawRect(c)
        p.setPen(QPen(QColor(0, 0, 0, 110), 1))
        p.setBrush(QColor("white"))
        s = HANDLE + 1
        for pt in self.handles().values():
            p.drawRoundedRect(QRectF(pt.x() - s / 2, pt.y() - s / 2, s, s), 2, 2)

    # ---------------------------------------------------------------- 自適應格線
    def _lum_map(self, w: int, h: int):
        """照片在畫面上每個像素的亮度（先模糊過，顏色才不會沿著細節一路閃）。"""
        key = (self.pix.cacheKey(), w, h)
        if self._lum_key == key:
            return self._lum
        img = self.pix.toImage()
        small = img.scaled(max(1, w // 10), max(1, h // 10), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        gray = small.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
            w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        arr = np.frombuffer(gray.constBits(), dtype=np.uint8, count=gray.sizeInBytes())
        self._lum = arr.reshape(h, gray.bytesPerLine())[:, :w].copy()
        self._lum_key = key
        return self._lum

    def _guide_overlay(self, f: QRectF, c: QRectF):
        """格線逐像素挑顏色：底下暗就用白線、亮就用深色線，再墊一圈相反色的淡邊。"""
        o = self.tool.o
        paths = guide_paths(o["guide"], c.width(), c.height(), o["variant"])
        if not paths:
            return None
        dpr = self.devicePixelRatioF()
        w, h = max(1, round(f.width() * dpr)), max(1, round(f.height() * dpr))
        key = (self.pix.cacheKey(), w, h, round(c.x(), 1), round(c.y(), 1), round(c.width(), 1),
               round(c.height(), 1), o["guide"], o["variant"])
        if self._overlay_key == key:
            return self._overlay

        def mask(width, faint_alpha):
            m = QImage(w, h, QImage.Format.Format_Alpha8)
            m.fill(0)
            q = QPainter(m)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.scale(dpr, dpr)
            q.translate(c.x() - f.x(), c.y() - f.y())
            q.setBrush(Qt.BrushStyle.NoBrush)
            for path, faint in paths:
                pen = QPen(QColor(0, 0, 0, faint_alpha if faint else 255), width)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                q.setPen(pen)
                q.drawPath(path)
            q.end()
            a = np.frombuffer(m.constBits(), dtype=np.uint8, count=m.sizeInBytes())
            return a.reshape(h, m.bytesPerLine())[:, :w].astype(np.float32) / 255

        line = mask(1.3, 150)
        halo = mask(3.6, 150) * 0.45
        dark = self._lum_map(w, h) < 128
        c_line = np.where(dark, 250.0, 18.0)
        c_halo = 255.0 - np.where(dark, 255.0, 0.0)
        alpha = line + halo * (1 - line)
        prem = c_line * line + c_halo * halo * (1 - line)
        out = np.empty((h, w, 4), dtype=np.uint8)
        v = np.clip(prem, 0, 255).astype(np.uint8)
        out[..., 0] = v
        out[..., 1] = v
        out[..., 2] = v
        out[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
        img = QImage(out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        img.setDevicePixelRatio(dpr)
        self._overlay, self._overlay_key = img, key
        return img

    def _hit(self, pos):
        for d, pt in self.handles().items():
            if (pos - pt).manhattanLength() <= HANDLE + 4:
                return d
        if self.crop_rect().contains(pos):
            return "move"
        return None

    def mousePressEvent(self, e):
        if self.empty or not self.pix:
            return
        hit = self._hit(e.position())
        if hit:
            self._drag = {"dir": hit, "start": e.position(), "base": dict(self.tool.crop),
                          "frame": self.frame_rect(), "aspect": self.tool.aspect_value()}

    def mouseMoveEvent(self, e):
        if not self._drag:
            hit = self._hit(e.position()) if self.pix else None
            cursors = {"move": Qt.CursorShape.SizeAllCursor, "n": Qt.CursorShape.SizeVerCursor,
                       "s": Qt.CursorShape.SizeVerCursor, "e": Qt.CursorShape.SizeHorCursor,
                       "w": Qt.CursorShape.SizeHorCursor, "nw": Qt.CursorShape.SizeFDiagCursor,
                       "se": Qt.CursorShape.SizeFDiagCursor, "ne": Qt.CursorShape.SizeBDiagCursor,
                       "sw": Qt.CursorShape.SizeBDiagCursor}
            self.setCursor(cursors.get(hit, Qt.CursorShape.PointingHandCursor if self.empty else Qt.CursorShape.ArrowCursor))
            return
        d = self._drag
        f = d["frame"]
        dx = (e.position().x() - d["start"].x()) / f.width()
        dy = (e.position().y() - d["start"].y()) / f.height()
        b = d["base"]
        if d["dir"] == "move":
            self.tool.crop = {"x": clamp(b["x"] + dx, 0, 1 - b["w"]), "y": clamp(b["y"] + dy, 0, 1 - b["h"]),
                              "w": b["w"], "h": b["h"]}
        else:
            self.tool.crop = self._resize(d["dir"], dx, dy, b, d["aspect"], f.width(), f.height())
        self.tool.paint_size()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None
            return
        super().mouseReleaseEvent(e)

    @staticmethod
    def _resize(dir_, dxn, dyn, b, aspect, pw, ph):
        """比例鎖上時對角固定不動，另一邊跟著比例走，撞到邊界就整塊縮小而不是變形。"""
        west, east, north, south = "w" in dir_, "e" in dir_, "n" in dir_, "s" in dir_
        L, T = b["x"] * pw, b["y"] * ph
        R, B = (b["x"] + b["w"]) * pw, (b["y"] + b["h"]) * ph
        dx, dy = dxn * pw, dyn * ph
        if aspect:
            ax, ay = (R if west else L), (B if north else T)
            px = clamp((L if west else R) + dx, 0, pw)
            w = abs(px - ax)
            max_w = ax if west else pw - ax
            max_h = ay if north else ph - ay
            w = clamp(w, MIN_PX, min(max_w, max_h * aspect))
            h = w / aspect
            L, R = (ax - w, ax) if west else (ax, ax + w)
            T, B = (ay - h, ay) if north else (ay, ay + h)
        else:
            if west:
                L = clamp(L + dx, 0, R - MIN_PX)
            if east:
                R = clamp(R + dx, L + MIN_PX, pw)
            if north:
                T = clamp(T + dy, 0, B - MIN_PX)
            if south:
                B = clamp(B + dy, T + MIN_PX, ph)
        return {"x": L / pw, "y": T / ph, "w": (R - L) / pw, "h": (B - T) / ph}


class PhotoEditPage(ToolPage):
    title = tr("裁切旋轉")

    def __init__(self, window):
        super().__init__(window)
        self.o = {"rotate": 0.0, "turn": 0, "fine": 0.0, "flipH": False, "flipV": False, "bg": "#000000",
                  "transparent": False, "aspect": "free", "portrait": False, "guide": "thirds", "variant": 0,
                  "autoInner": True}
        self.crop = {"x": 0, "y": 0, "w": 1, "h": 1}
        self.full: QImage | None = None
        self.small: QImage | None = None
        self.work: QImage | None = None
        self.size_out = label("", "caption")
        self.meta_lay.addWidget(self.size_out)
        self.add_output_buttons(tr("輸出裁切後的照片（Ctrl+S）"))
        self._rebuild = QTimer(self)
        self._rebuild.setSingleShot(True)
        self._rebuild.setInterval(16)
        self._rebuild.timeout.connect(self._do_rebuild)
        self._build_controls()

    def make_stage(self):
        return CropStage(self)

    # ---------------------------------------------------------------- 控制項
    def _build_controls(self):
        F = self.form
        F.addWidget(section(tr("旋轉")))
        self.rotate = SliderField(tr("旋轉微調"), -45, 45, 0.1, 0, lambda v: f"{v:.1f}°")
        self.rotate.changed.connect(self._on_fine)
        F.addWidget(self.rotate)
        turns = hbox(icon_button("rotate-left", tr("向左 90°"), lambda: self.turn(-90)),
                     icon_button("rotate-right", tr("向右 90°"), lambda: self.turn(90)),
                     button(tr("重設"), None, None, tr("角度歸零"), self.reset_angle), None, spacing=4)
        self.flip_h = Flag(tr("水平翻轉"), icon_name="flip-h")
        self.flip_v = Flag(tr("垂直翻轉"), icon_name="flip-v")
        self.flip_h.toggled.connect(lambda on: self._set("flipH", on))
        self.flip_v.toggled.connect(lambda on: self._set("flipV", on))
        F.addWidget(row(field(tr("整圈"), wrap(turns)), field(tr("翻轉"), wrap(hbox(self.flip_h, self.flip_v, None, spacing=6)))))

        F.addWidget(section(tr("裁切")))
        self.aspect = QComboBox()
        for v, lb in ASPECTS:
            self.aspect.addItem(lb, v)
        self.aspect.currentIndexChanged.connect(lambda *_: self._set_aspect())
        self.orient = Segmented([("landscape", tr("橫")), ("portrait", tr("直"))], "landscape")
        self.orient.changed.connect(lambda v: (self.o.__setitem__("portrait", v == "portrait"), self.reset_crop()))
        F.addWidget(row(field(tr("比例"), self.aspect), field(tr("方向"), self.orient)))
        self.auto_inner = Flag(tr("自動避開留白"))
        self.auto_inner.setChecked(True)
        self.auto_inner.toggled.connect(self._set_auto_inner)
        self.bg = ColorButton("#000000")
        self.bg.changed.connect(lambda v: self._set("bg", v))
        crop_btns = hbox(button(tr("填滿"), None, None, tr("裁切框撐滿整張（含留白）"), self.fill_crop),
                         button(tr("貼齊照片"), None, None, tr("把裁切框收進不含留白的範圍"), self.snap_inside), None, spacing=6)
        F.addWidget(field(tr("裁切框"), wrap(crop_btns)))
        F.addWidget(row(field(tr("留白"), self.auto_inner), field(tr("底色"), self.bg)))

        F.addWidget(section(tr("格線")))
        self.guide = QComboBox()
        for v, lb in GUIDES:
            self.guide.addItem(lb, v)
        self.guide.setCurrentIndex(1)
        self.guide.currentIndexChanged.connect(self._set_guide)
        self.variant_btn = button(tr("換方向"), None, "rotate", on_click=self._next_variant)
        self.variant_btn.setEnabled(False)
        F.addWidget(row(field(tr("格線"), self.guide), field(tr("方向"), self.variant_btn)))

        F.addWidget(section(tr("匯出")))
        self.fmt = QComboBox()
        self.fmt.addItem("JPEG", "jpg")
        self.fmt.addItem("PNG", "png")
        self.fmt.currentIndexChanged.connect(self._on_format)
        self.scale = QComboBox()
        for v, lb in (("1", tr("原尺寸")), ("0.75", "75%"), ("0.5", "50%"), ("0.25", "25%")):
            self.scale.addItem(lb, v)
        self.transparent = Flag(tr("留白透明"))
        self.transparent.setEnabled(False)
        self.transparent.toggled.connect(lambda on: self._set("transparent", on))
        F.addWidget(row(field(tr("格式"), self.fmt), field(tr("尺寸"), self.scale), field(tr("透明"), self.transparent)))
        self.quality = SliderField(tr("JPEG 品質"), 60, 100, 1, 92, lambda v: f"{v:.0f}%")
        F.addWidget(self.quality)
        F.addStretch(1)

    # ---------------------------------------------------------------- 讀圖
    def on_image(self, path, img):
        self.full = img
        edge = max(img.width(), img.height())
        self.small = img if edge <= PREVIEW_EDGE else img.scaled(
            img.size() * (PREVIEW_EDGE / edge), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.o.update(rotate=0.0, turn=0, fine=0.0)
        self.rotate.set(0)
        self._do_rebuild()
        self.reset_crop()
        self.set_status(f"{img.width()}×{img.height()}")

    # ---------------------------------------------------------------- 幾何
    def aspect_value(self):
        a = self.o["aspect"]
        if a == "free":
            return None
        if a == "source":
            if not self.full:
                return None
            r = self.full.width() / self.full.height()
        else:
            r = float(a)
        return 1 / r if self.o["portrait"] else r

    def inner_box(self):
        if not self.full or not self.work:
            return {"x": 0, "y": 0, "w": 1, "h": 1}
        ow, oh = rotated_size(self.full.width(), self.full.height(), self.o["rotate"])
        iw, ih = largest_inner_rect(self.full.width(), self.full.height(), self.o["rotate"])
        w, h = clamp(iw / ow, 0.05, 1), clamp(ih / oh, 0.05, 1)
        return {"x": (1 - w) / 2, "y": (1 - h) / 2, "w": w, "h": h}

    def reset_crop(self):
        if not self.work:
            return
        self.crop = fit_crop(self.work.width(), self.work.height(), self.aspect_value(),
                             self.inner_box() if self.o["autoInner"] else None)
        self.paint_size()
        self.stage.update()

    def snap_inside(self):
        if self.work:
            self.crop = fit_crop(self.work.width(), self.work.height(), self.aspect_value(), self.inner_box())
            self.paint_size()
            self.stage.update()

    def fill_crop(self):
        self.o["autoInner"] = False
        self.auto_inner.blockSignals(True)
        self.auto_inner.setChecked(False)
        self.auto_inner.blockSignals(False)
        self.reset_crop()

    def refit_crop(self):
        """畫布長寬比變了（轉了一點角度就會）之後，把鎖住的比例接回去。"""
        if not self.work:
            return
        if self.o["autoInner"]:
            self.snap_inside()
            return
        aspect = self.aspect_value()
        if not aspect:
            return
        c = self.crop
        cx, cy = c["x"] + c["w"] / 2, c["y"] + c["h"] / 2
        wpx = c["w"] * self.work.width()
        hpx = wpx / aspect
        if hpx > self.work.height():
            hpx = self.work.height()
            wpx = hpx * aspect
        if wpx > self.work.width():
            wpx = self.work.width()
            hpx = wpx / aspect
        w, h = wpx / self.work.width(), hpx / self.work.height()
        self.crop = {"w": w, "h": h, "x": clamp(cx - w / 2, 0, 1 - w), "y": clamp(cy - h / 2, 0, 1 - h)}

    def paint_size(self):
        if not self.full:
            self.size_out.setText("")
            return
        ow, oh = rotated_size(self.full.width(), self.full.height(), self.o["rotate"])
        w, h = round(self.crop["w"] * ow), round(self.crop["h"] * oh)
        self.size_out.setText(tr('輸出 {0} × {1} px · 比例 {2:.3f}').format(w, h, w / h) if h else "")

    # ---------------------------------------------------------------- 重畫
    def rebuild(self):
        if self.small is not None:
            self._rebuild.start()

    def _do_rebuild(self):
        if self.small is None:
            return
        self.work = render_work(self.small, self.o, max(self.small.width(), self.small.height()))
        self.stage.pix = QPixmap.fromImage(self.work)
        self.refit_crop()
        self.paint_size()
        self.stage.update()

    def _set(self, key, value):
        self.o[key] = value
        self.rebuild()

    def _on_fine(self, v):
        self.o["fine"] = v
        self.o["rotate"] = self.o["turn"] + v
        self.rebuild()

    def turn(self, delta):
        """90 度整轉與微調分開記，轉完 90 度滑桿還是停在原本的微調值。"""
        self.o["turn"] = (self.o["turn"] + delta) % 360
        self.o["rotate"] = self.o["turn"] + self.o["fine"]
        self._do_rebuild()
        self.reset_crop()

    def reset_angle(self):
        self.o.update(turn=0, fine=0.0, rotate=0.0)
        self.rotate.set(0)
        self._do_rebuild()
        self.reset_crop()

    def _set_aspect(self):
        self.o["aspect"] = self.aspect.currentData()
        self.reset_crop()

    def _set_auto_inner(self, on):
        self.o["autoInner"] = on
        if on:
            self.reset_crop()

    def _set_guide(self):
        self.o["guide"] = self.guide.currentData()
        self.variant_btn.setEnabled(self.o["guide"] in DIRECTIONAL)
        self.stage.update()

    def _next_variant(self):
        self.o["variant"] = (self.o["variant"] + 1) % 4
        self.stage.update()

    def _on_format(self):
        png = self.fmt.currentData() == "png"
        self.transparent.setEnabled(png)
        self.quality.setEnabled(not png)
        if not png and self.transparent.isChecked():
            self.transparent.setChecked(False)

    # ---------------------------------------------------------------- 輸出
    def output_name(self):
        return f"{safe_name(self.path)}_crop.{self.fmt.currentData()}"

    def output_job(self):
        if not self.full:
            return None
        src, o, crop = self.full, dict(self.o), dict(self.crop)
        scale = float(self.scale.currentData())

        def job(progress):
            progress(0.1, tr("旋轉與翻轉"))
            ow, oh = rotated_size(src.width(), src.height(), o["rotate"])
            full = render_work(src, o, round(max(ow, oh) * scale))
            progress(0.7, tr("裁切"))
            return crop_image(full, crop)

        return job
