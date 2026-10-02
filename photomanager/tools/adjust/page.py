"""調整（參考 Lightroom 的「基本」「色彩混合」「顏色分級」「效果」，再加上聚光燈）。

右邊四個分頁：基本、色彩、效果、聚光燈。拖滑桿時先用小圖快速算一張草稿，
停下來再用預覽大小算一次；儲存或暫存時才用原尺寸（develop.render 會一段一段算）。
按住空白鍵看原圖，左下角的「照片參數」有直方圖、波形、向量示波器與曝光數字。
聚光燈：切到「聚光燈」分頁後在照片上拖曳畫一個區域，裡面提亮、外面壓暗；
拖中間移動、拖邊上的點改大小，Delete 刪掉選取的那個。
"""
from __future__ import annotations

from ...i18n import tr

import math
import threading

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import QComboBox

from ...ui.widgets import ParamSlider, Segmented, SliderField, button, field, hbox, label, notify, row, section, wrap
from ..common import Stage, ToolPage, safe_name
from ..depth_blur.page import qimage_to_rgb, rgb_to_qimage
from . import develop as D

PREVIEW_EDGE = 1000
DRAFT_EDGE = 520
HANDLE = 8

HUE_NAMES = {"red": tr("紅色"), "orange": tr("橙色"), "yellow": tr("黃色"), "green": tr("綠色"), "aqua": tr("水藍色"),
             "blue": tr("藍色"), "purple": tr("紫色"), "magenta": tr("洋紅色")}


def hue_hex(h, s=1.0, v=1.0):
    return QColor.fromHsvF((h % 360) / 360, s, v).name()


def ev_fmt(v):
    return f"{v:+.2f}"


class _Relay(QObject):
    done = Signal(object, object, object)     # gen, kind, rgb array


# ============================================================ 照片區（含聚光燈的編輯）
class AdjustStage(Stage):
    def __init__(self, tool):
        super().__init__()
        self.tool = tool
        self.pix: QPixmap | None = None
        self._drag = None
        self.setMouseTracking(True)

    def image_rect(self) -> QRectF:
        if not self.pix:
            return QRectF()
        pad = 18
        k = min((self.width() - pad * 2) / self.pix.width(), (self.height() - pad * 2) / self.pix.height())
        w, h = self.pix.width() * k, self.pix.height() * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paint_content(self, p: QPainter):
        if not self.pix:
            return
        r = self.image_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pix = self.shown_pix(self.pix)
        p.drawPixmap(r, pix, QRectF(pix.rect()))
        if self.tool.spot_mode() and not self.comparing:
            self._paint_spots(p, r)

    # ---------------------------------------------------------------- 聚光燈的幾何
    def _geo(self, s, r=None):
        r = r or self.image_rect()
        edge = max(r.width(), r.height())
        c = QPointF(r.x() + s["cx"] * r.width(), r.y() + s["cy"] * r.height())
        return c, s["rx"] * edge, s["ry"] * edge, s.get("angle", 0.0)

    def _handles(self, s):
        c, rx, ry, ang = self._geo(s)
        t = QTransform().translate(c.x(), c.y()).rotate(ang)
        return {"e": t.map(QPointF(rx, 0)), "w": t.map(QPointF(-rx, 0)),
                "s": t.map(QPointF(0, ry)), "n": t.map(QPointF(0, -ry))}

    def _paint_spots(self, p, r):
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i, s in enumerate(self.tool.p["spots"]):
            c, rx, ry, ang = self._geo(s, r)
            sel = i == self.tool.sel
            p.save()
            p.translate(c)
            p.rotate(ang)
            # 黑邊 + 白線：任何底色上都看得到
            p.setPen(QPen(QColor(0, 0, 0, 120), 3))
            p.drawEllipse(QPointF(0, 0), rx, ry)
            pen = QPen(QColor(255, 255, 255, 235 if sel else 150), 1.4)
            if not sel:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawEllipse(QPointF(0, 0), rx, ry)
            f = max(0.02, s.get("feather", 50) / 100)
            if sel:
                p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DotLine))
                p.drawEllipse(QPointF(0, 0), rx * (1 - f), ry * (1 - f))
            p.restore()
            p.setPen(QPen(QColor(0, 0, 0, 120), 1))
            p.setBrush(QColor(255, 214, 10) if sel else QColor(255, 255, 255, 200))
            p.drawEllipse(c, 4, 4)
            if sel:
                p.setBrush(QColor("white"))
                for pt in self._handles(s).values():
                    p.drawRect(QRectF(pt.x() - HANDLE / 2, pt.y() - HANDLE / 2, HANDLE, HANDLE))
            p.setBrush(Qt.BrushStyle.NoBrush)

    def _hit(self, pos):
        spots = self.tool.p["spots"]
        if 0 <= self.tool.sel < len(spots):
            for k, pt in self._handles(spots[self.tool.sel]).items():
                if (pos - pt).manhattanLength() <= HANDLE + 4:
                    return self.tool.sel, k
        for i in range(len(spots) - 1, -1, -1):
            c, rx, ry, ang = self._geo(spots[i])
            a = math.radians(ang)
            dx, dy = pos.x() - c.x(), pos.y() - c.y()
            u = (dx * math.cos(a) + dy * math.sin(a)) / max(rx, 1)
            v = (-dx * math.sin(a) + dy * math.cos(a)) / max(ry, 1)
            if u * u + v * v <= 1:
                return i, "move"
        return -1, None

    def _norm(self, pos):
        r = self.image_rect()
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def mousePressEvent(self, e):
        if self.empty or not self.pix or e.button() != Qt.MouseButton.LeftButton:
            return
        if not self.tool.spot_mode():
            return
        pos = e.position()
        r = self.image_rect()
        i, kind = self._hit(pos)
        if i >= 0:
            self.tool.select_spot(i)
            self._drag = {"kind": kind, "start": pos, "base": dict(self.tool.p["spots"][i])}
            return
        if not r.contains(pos):
            return
        cx, cy = self._norm(pos)
        self.tool.add_spot(cx, cy, 0.02, 0.02)
        self._drag = {"kind": "new", "start": pos, "base": dict(self.tool.p["spots"][self.tool.sel])}

    def mouseMoveEvent(self, e):
        pos = e.position()
        if not self._drag:
            if self.empty:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.tool.spot_mode():
                _, kind = self._hit(pos)
                self.setCursor(Qt.CursorShape.SizeAllCursor if kind == "move" else
                               Qt.CursorShape.CrossCursor if kind is None else Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        d = self._drag
        s = self.tool.p["spots"][self.tool.sel]
        r = self.image_rect()
        edge = max(r.width(), r.height())
        b = d["base"]
        if d["kind"] == "move":
            s["cx"] = min(1.2, max(-0.2, b["cx"] + (pos.x() - d["start"].x()) / r.width()))
            s["cy"] = min(1.2, max(-0.2, b["cy"] + (pos.y() - d["start"].y()) / r.height()))
        else:
            c, _, _, ang = self._geo(b)
            a = math.radians(ang)
            dx, dy = pos.x() - c.x(), pos.y() - c.y()
            u = abs(dx * math.cos(a) + dy * math.sin(a)) / edge
            v = abs(-dx * math.sin(a) + dy * math.cos(a)) / edge
            if d["kind"] == "new":
                s["rx"], s["ry"] = max(0.02, u), max(0.02, v)
            elif d["kind"] in ("e", "w"):
                s["rx"] = max(0.02, u)
            else:
                s["ry"] = max(0.02, v)
        self.tool.spots_changed()
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            d = self._drag
            self._drag = None
            s = self.tool.p["spots"][self.tool.sel]
            if d["kind"] == "new" and s["rx"] < 0.03 and s["ry"] < 0.03:
                # 只點一下沒有拖：給一個預設大小
                s["rx"], s["ry"] = 0.16, 0.2
            self.tool.spots_changed(final=True)
            return
        super().mouseReleaseEvent(e)


# ============================================================ 頁面
class AdjustPage(ToolPage):
    title = tr("調整")
    scopes = True
    compare = True

    def __init__(self, window):
        super().__init__(window)
        self.p = D.defaults()
        self.sel = -1
        self.full: QImage | None = None
        self.small: np.ndarray | None = None
        self.draft: np.ndarray | None = None
        self.gen = 0
        self._full_shown = -1
        self._relay = _Relay()
        self._relay.done.connect(self._on_done)
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(10)
        self._draft_timer.timeout.connect(lambda: self._render("draft"))
        self._full_timer = QTimer(self)
        self._full_timer.setSingleShot(True)
        self._full_timer.setInterval(200)
        self._full_timer.timeout.connect(lambda: self._render("full"))
        self.sliders: dict[str, ParamSlider] = {}
        self.add_output_buttons(tr("用原尺寸輸出調整後的照片（Ctrl+S）"))
        self._build()
        dele = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        dele.activated.connect(self.remove_spot)

    def make_stage(self):
        return AdjustStage(self)

    # ---------------------------------------------------------------- 控制項
    def _slider(self, lay, key, title, lo=-100, hi=100, step=1, fmt=None, gradient=None, default=None):
        s = ParamSlider(title, lo, hi, self.p[key], step, default if default is not None else D.DEFAULTS[key],
                        fmt, gradient)
        s.changed.connect(lambda v, k=key: self._set(k, v))
        s.released.connect(self.settle)
        self.sliders[key] = s
        lay.addWidget(s)
        return s

    def _build(self):
        tabs = self.add_tabs([("basic", tr("基本")), ("color", tr("色彩")), ("effects", tr("效果")),
                              ("spot", tr("聚光燈"))])
        B = tabs["basic"]
        B.addWidget(section(tr("白平衡")))
        self._slider(B, "temp", tr("色溫"), gradient=["#3b78d8", "#d8d8d8", "#e2b23c"])
        self._slider(B, "tint", tr("色調"), gradient=["#3fae49", "#d8d8d8", "#c74bc5"])
        head = hbox(section(tr("色調")), None,
                    button(tr("自動"), None, None, tr("依直方圖自動調整曝光、亮部、陰影、白色、黑色"), self.auto),
                    button(tr("全部重設"), None, "reset", tr("所有調整回到預設值"), self.reset_all), spacing=6)
        B.addWidget(wrap(head))
        self._slider(B, "exposure", tr("曝光"), -5, 5, 0.05, ev_fmt)
        self._slider(B, "contrast", tr("對比"))
        self._slider(B, "highlights", tr("亮部"))
        self._slider(B, "shadows", tr("陰影"))
        self._slider(B, "whites", tr("白色"))
        self._slider(B, "blacks", tr("黑色"))
        B.addWidget(section(tr("外觀")))
        self._slider(B, "texture", tr("紋理"))
        self._slider(B, "clarity", tr("清晰度"))
        self._slider(B, "dehaze", tr("去朦朧"))
        self._slider(B, "vibrance", tr("細節飽和度"))
        self._slider(B, "saturation", tr("飽和度"))

        C = tabs["color"]
        C.addWidget(section(tr("色彩混合")))
        self.hsl_mode = Segmented([("0", tr("色相")), ("1", tr("飽和度")), ("2", tr("明亮度"))], "0")
        self.hsl_mode.changed.connect(lambda *_: self._sync_hsl())
        C.addWidget(self.hsl_mode)
        self.hsl_sliders = {}
        for name, hue in D.BANDS:
            s = ParamSlider(HUE_NAMES[name], -100, 100, 0, 1, 0)
            s.changed.connect(lambda v, n=name: self._set_hsl(n, v))
            s.released.connect(self.settle)
            self.hsl_sliders[name] = s
            C.addWidget(s)
        C.addWidget(section(tr("顏色分級")))
        spectrum = [hue_hex(h, 0.75, 0.95) for h in range(0, 361, 30)]
        for key, title in (("sh_hue", tr("陰影色相")), ("sh_sat", tr("陰影飽和")), ("hi_hue", tr("亮部色相")),
                           ("hi_sat", tr("亮部飽和")), ("balance", tr("平衡"))):
            if key.endswith("hue"):
                s = ParamSlider(title, 0, 360, self.p["grade"][key], 1, D.DEFAULTS["grade"][key],
                                lambda v: f"{v:.0f}°", spectrum)
            elif key == "balance":
                s = ParamSlider(title, -100, 100, 0, 1, 0)
            else:
                s = ParamSlider(title, 0, 100, 0, 1, 0)
            s.changed.connect(lambda v, k=key: self._set_grade(k, v))
            s.released.connect(self.settle)
            self.sliders["grade." + key] = s
            C.addWidget(s)
        self._sync_hsl()

        E = tabs["effects"]
        E.addWidget(section(tr("暈影")))
        self._slider(E, "vignette", tr("總量"))
        self._slider(E, "vig_mid", tr("中點"), 0, 100)
        self._slider(E, "vig_feather", tr("羽化"), 0, 100)
        E.addWidget(section(tr("顆粒")))
        self._slider(E, "grain", tr("總量"), 0, 100)
        self._slider(E, "grain_size", tr("大小"), 0, 100)

        S = tabs["spot"]
        self.spot_info = label("", "secondary")
        self.del_btn = button(tr("刪除"), None, "trash", tr("刪除選取的聚光燈（Delete）"), self.remove_spot)
        S.addWidget(wrap(hbox(self.spot_info, None,
                              button(tr("新增"), None, "plus", tr("在畫面中間加一個聚光燈"),
                                     lambda: (self.add_spot(0.5, 0.5, 0.18, 0.22), self.spots_changed(final=True))),
                              self.del_btn, spacing=6)))
        S.addWidget(section(tr("選取的聚光燈")))
        self.spot_sliders = {}
        for key, title, lo, hi, step, fmt, dflt in (
                ("ev", tr("亮度"), -2, 3, 0.05, ev_fmt, 0.8), ("feather", tr("羽化"), 0, 100, 1, None, 60),
                ("warmth", tr("色溫"), -100, 100, 1, None, 0), ("angle", tr("角度"), -90, 90, 1, lambda v: f"{v:+.0f}°", 0)):
            s = ParamSlider(title, lo, hi, dflt, step, dflt, fmt,
                            ["#3b78d8", "#d8d8d8", "#e2b23c"] if key == "warmth" else None)
            s.changed.connect(lambda v, k=key: self._set_spot(k, v))
            s.released.connect(self.settle)
            self.spot_sliders[key] = s
            S.addWidget(s)
        S.addWidget(section(tr("周圍")))
        self._slider(S, "spot_dim", tr("周圍壓暗"), 0, 100)
        self._sync_spot()

        F = self.form
        F.addWidget(section(tr("匯出")))
        self.fmt = QComboBox()
        self.fmt.addItem("JPEG", "jpg")
        self.fmt.addItem("PNG", "png")
        self.quality = SliderField(tr("JPEG 品質"), 60, 100, 1, 92, lambda v: f"{v:.0f}%")
        F.addWidget(row(field(tr("格式"), self.fmt), self.quality))

    def spot_mode(self):
        return self.tabs.value() == "spot"

    def on_tab(self, key):
        self.stage.update()

    # ---------------------------------------------------------------- 參數
    def _set(self, key, v):
        self.p[key] = v
        self.changed()

    def _set_hsl(self, name, v):
        self.p["hsl"][name][int(self.hsl_mode.value())] = v
        self.changed()

    def _set_grade(self, key, v):
        self.p["grade"][key] = v
        self.changed()

    def _sync_hsl(self):
        mode = int(self.hsl_mode.value())
        for name, hue in D.BANDS:
            s = self.hsl_sliders[name]
            s.set(self.p["hsl"][name][mode])
            if mode == 0:
                s.set_gradient([hue_hex(hue - 30, 0.8, 0.9), hue_hex(hue, 0.8, 0.9), hue_hex(hue + 30, 0.8, 0.9)])
            elif mode == 1:
                s.set_gradient([hue_hex(hue, 0.0, 0.75), hue_hex(hue, 0.95, 0.9)])
            else:
                s.set_gradient([hue_hex(hue, 0.8, 0.25), hue_hex(hue, 0.5, 1.0)])

    def _sync_all(self):
        for key, s in self.sliders.items():
            if key.startswith("grade."):
                s.set(self.p["grade"][key[6:]])
            else:
                s.set(self.p[key])
        self._sync_hsl()
        self._sync_spot()

    def auto(self):
        if self.small is None:
            return
        self.p.update(D.auto_tone(self.small, self.p))
        self._sync_all()
        self.changed(final=True)

    def reset_all(self):
        self.p = D.defaults()
        self.sel = -1
        self._sync_all()
        self.changed(final=True)
        self.stage.update()

    # ---------------------------------------------------------------- 聚光燈
    def add_spot(self, cx, cy, rx, ry):
        self.p["spots"].append({"cx": cx, "cy": cy, "rx": rx, "ry": ry, "angle": 0.0, "ev": 0.8, "feather": 60.0,
                                "warmth": 0.0})
        self.sel = len(self.p["spots"]) - 1
        self._sync_spot()

    def select_spot(self, i):
        self.sel = i
        self._sync_spot()
        self.stage.update()

    def remove_spot(self):
        if not self.spot_mode() or not (0 <= self.sel < len(self.p["spots"])):
            return
        del self.p["spots"][self.sel]
        self.sel = len(self.p["spots"]) - 1
        self._sync_spot()
        self.spots_changed(final=True)

    def _set_spot(self, key, v):
        if 0 <= self.sel < len(self.p["spots"]):
            self.p["spots"][self.sel][key] = v
            self.spots_changed()

    def _sync_spot(self):
        spots = self.p["spots"]
        on = 0 <= self.sel < len(spots)
        self.spot_info.setText(tr('聚光燈 {0} / {1}').format(self.sel + 1, len(spots)) if on else
                               tr("還沒有聚光燈") if not spots else tr('共 {0} 個').format(len(spots)))
        self.del_btn.setEnabled(on)
        for key, s in self.spot_sliders.items():
            s.setEnabled(on)
            if on:
                s.set(spots[self.sel].get(key, s.default))
        self.sliders["spot_dim"].setEnabled(bool(spots))

    def spots_changed(self, final=False):
        self._sync_spot()
        self.changed(final)
        self.stage.update()

    # ---------------------------------------------------------------- 讀圖
    def on_image(self, path, img):
        self.full = img
        edge = max(img.width(), img.height())
        small = img if edge <= PREVIEW_EDGE else img.scaled(
            img.size() * (PREVIEW_EDGE / edge), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.small = qimage_to_rgb(small)
        k = min(1.0, DRAFT_EDGE / max(small.width(), small.height()))
        draft = small.scaled(max(1, round(small.width() * k)), max(1, round(small.height() * k)),
                             Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.draft = qimage_to_rgb(draft)
        # 每張新照片（包括別的工具傳過來的結果）都從零開始，免得同一個調整被套兩次
        self.p = D.defaults()
        self.sel = -1
        self._sync_all()
        self.stage.pix = QPixmap.fromImage(small)
        self.stage.original = self.stage.pix
        self.stage.update()
        self.update_scopes(self.small, self.small)
        self.set_status(f"{img.width()}×{img.height()}")

    # ---------------------------------------------------------------- 預覽
    def changed(self, final=False):
        if self.small is None:
            return
        self.gen += 1
        if final:
            self._full_timer.stop()
            self._render("full")
        else:
            self._draft_timer.start()
            self._full_timer.start()

    def settle(self):
        """放開滑桿：馬上算預覽大小的那一張。"""
        if self.small is not None:
            self._full_timer.stop()
            self._render("full")

    def _render(self, kind):
        arr = self.draft if kind == "draft" else self.small
        if arr is None:
            return
        gen = self.gen
        p = _copy(self.p)

        def run():
            out = D.render(arr, p, cancel=lambda: gen != self.gen)
            if out is not None:
                self._relay.done.emit(gen, kind, out)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, gen, kind, out):
        if gen != self.gen or (kind == "draft" and self._full_shown == gen):
            return
        if kind == "full":
            self._full_shown = gen
            self.update_scopes(out)
        self.stage.pix = QPixmap.fromImage(rgb_to_qimage(out))
        self.stage.update()

    # ---------------------------------------------------------------- 輸出
    def output_name(self):
        return f"{safe_name(self.path)}_adjust.{self.fmt.currentData()}"

    def output_job(self):
        if self.full is None:
            return None
        full, p = self.full, _copy(self.p)

        def job(progress):
            progress(0.03, tr("準備原尺寸"))
            rgb = qimage_to_rgb(full)
            out = D.render(rgb, p, progress=lambda f: progress(0.05 + 0.8 * f, tr("套用調整")))
            return rgb_to_qimage(out)

        return job


def _copy(p):
    import copy
    return copy.deepcopy(p)
