"""調整（參考 Lightroom 的「基本」「色彩混合」「顏色分級」「效果」，再加上聚光燈）。

右邊六個分頁：基本、風格、色彩、效果、聚光燈、遮罩。「遮罩」辨識主體（subject.py），
可以遮罩主體或遮罩非主體：被遮住的地方維持原樣，所有調整只套在沒有遮住的地方。
自動辨識之外可以用筆刷「加入 / 移除主體」：筆刷會依顏色判斷刷到的地方是不是同一個東西，再貼齊邊緣（半自動）。「風格」是 iPhone 相機那種攝影風格（styles.py）。拖滑桿時先用小圖快速算一張草稿，
停下來再用預覽大小算一次；儲存或暫存時才用原尺寸（develop.render 會一段一段算）。
按住空白鍵看原圖，左下角的「照片參數」有直方圖、波形、向量示波器與曝光數字。
聚光燈：切到「聚光燈」分頁後在照片上拖曳畫一個區域，裡面提亮、外面壓暗；
拖中間移動、拖邊上的點改大小，Delete 刪掉選取的那個。
"""
from __future__ import annotations


import math
import threading

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import QComboBox, QGridLayout, QWidget

from ...ui import dialogs, theme
from ...ui.widgets import (Flag, ParamSlider, Segmented, SliderField, button, field, hbox, label, notify, row, section,
                           vbox, wrap)
from ..common import Stage, ToolPage, safe_name
from ..depth_blur.page import qimage_to_rgb, rgb_to_qimage
from . import develop as D
from . import styles as ST
from . import subject as SB
from .style_widgets import ControlPad, StyleTile

PREVIEW_EDGE = 1000
DRAFT_EDGE = 520
HANDLE = 8
ROT_GAP = 26          # 旋轉把手離形狀邊緣多遠（像素）
SHAPES = [("ellipse", "橢圓"), ("rect", "矩形"), ("beam", "光帶")]

HUE_NAMES = {"red": "紅色", "orange": "橙色", "yellow": "黃色", "green": "綠色", "aqua": "水藍色",
             "blue": "藍色", "purple": "紫色", "magenta": "洋紅色"}


def hue_hex(h, s=1.0, v=1.0):
    return QColor.fromHsvF((h % 360) / 360, s, v).name()


def ev_fmt(v):
    return f"{v:+.2f}"


class _Relay(QObject):
    done = Signal(object, object, object)     # gen, kind, rgb array
    detail = Signal(object, object, object, object, object)   # token, gen, 成品, 原圖, 區域
    thumb = Signal(object, str, object)       # token, 風格名稱, rgb array
    subject = Signal(object, object, str)     # token, 主體遮罩 | None, 方法 / 錯誤
    stroke = Signal(object, object)           # token, (y0, x0, 選取程度, 加入 / 移除)
    download = Signal(int, int)
    download_done = Signal(object)


# ============================================================ 照片區（含聚光燈的編輯）
class AdjustStage(Stage):
    def __init__(self, tool):
        super().__init__()
        self.tool = tool
        self.pix: QPixmap | None = None
        self._drag = None
        self.setMouseTracking(True)

    def paint_content(self, p: QPainter):
        if not self.pix:
            return
        self.paint_photo(p, self.pix)
        ov = self.tool.mask_overlay()
        if ov is not None and not self.comparing:
            p.drawImage(self.image_rect(), ov)       # 被遮住（不會調整）的地方蓋一層紅
        if self.tool.brushing() and not self.comparing:
            self._paint_brush(p)
        if self.editing_spots() and not self.comparing:
            self._paint_spots(p, self.image_rect())

    # ---------------------------------------------------------------- 遮罩筆刷
    def _paint_brush(self, p):
        rad = self.tool.brush_screen_radius()
        col = QColor(255, 80, 80) if self.tool.brush_mode.value() == "add" else QColor(80, 220, 140)
        stroke = getattr(self, "_stroke", None)
        if stroke:
            trail = QColor(col)
            trail.setAlphaF(0.35)
            pen = QPen(trail, rad * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(stroke[0])
            for pt in stroke[1:]:
                path.lineTo(pt)
            if len(stroke) == 1:
                path.lineTo(stroke[0] + QPointF(0.01, 0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        hover = getattr(self, "_hover", None)
        if hover is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 140), 3))
            p.drawEllipse(hover, rad, rad)
            p.setPen(QPen(QColor(255, 255, 255, 230), 1.3))
            p.drawEllipse(hover, rad, rad)

    def leaveEvent(self, e):
        self._hover = None
        self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.tool.brushing():
            return          # 刷遮罩時雙擊不切換縮放
        if self.editing_spots():
            return          # 編輯聚光燈時雙擊不切換縮放（第一下已經在畫聚光燈了）
        super().mouseDoubleClickEvent(e)

    def editing_spots(self):
        """在聚光燈分頁、而且框線沒有藏起來：滑鼠是在畫 / 改聚光燈；否則滑鼠拿來拖曳檢視。"""
        return self.tool.spot_mode() and self.overlay_visible

    # ---------------------------------------------------------------- 聚光燈的幾何
    def _geo(self, s, r=None):
        r = r or self.image_rect()
        edge = max(r.width(), r.height())
        c = QPointF(r.x() + s["cx"] * r.width(), r.y() + s["cy"] * r.height())
        return c, s["rx"] * edge, s["ry"] * edge, s.get("angle", 0.0)

    def _handles(self, s):
        """改大小的把手 + 一個旋轉把手（形狀上方那顆圓的）。光帶沒有長度，只有寬度的把手。"""
        c, rx, ry, ang = self._geo(s)
        t = QTransform().translate(c.x(), c.y()).rotate(ang)
        out = {"s": t.map(QPointF(0, ry)), "n": t.map(QPointF(0, -ry))}
        if s.get("shape", "ellipse") != "beam":
            out.update(e=t.map(QPointF(rx, 0)), w=t.map(QPointF(-rx, 0)))
        out["rot"] = t.map(QPointF(0, -ry - ROT_GAP))
        return out

    def _outline(self, shape, rx, ry, long_side):
        """形狀的外框路徑（在轉好角度的座標裡，中心是原點）。"""
        path = QPainterPath()
        if shape == "beam":
            for y in (-ry, ry):
                path.moveTo(-long_side, y)
                path.lineTo(long_side, y)
        elif shape == "rect":
            theme.round_rect(path, QRectF(-rx, -ry, rx * 2, ry * 2), min(rx, ry) * 0.3)
        else:
            path.addEllipse(QPointF(0, 0), rx, ry)
        return path

    def _paint_spots(self, p, r):
        p.setBrush(Qt.BrushStyle.NoBrush)
        long_side = math.hypot(r.width(), r.height())
        for i, s in enumerate(self.tool.p["spots"]):
            c, rx, ry, ang = self._geo(s, r)
            shape = s.get("shape", "ellipse")
            sel = i == self.tool.sel
            p.save()
            if shape == "beam":
                p.setClipRect(r)          # 光帶是無限長的，只畫在照片範圍裡
            p.translate(c)
            p.rotate(ang)
            outline = self._outline(shape, rx, ry, long_side)
            # 黑邊 + 白線：任何底色上都看得到
            p.setPen(QPen(QColor(0, 0, 0, 120), 3))
            p.drawPath(outline)
            pen = QPen(QColor(255, 255, 255, 235 if sel else 150), 1.4)
            if not sel:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(outline)
            f = max(0.02, s.get("feather", 50) / 100)
            if sel:
                p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DotLine))
                p.drawPath(self._outline(shape, rx * (1 - f), ry * (1 - f), long_side))
                if shape == "beam":
                    p.drawLine(QPointF(-long_side, 0), QPointF(long_side, 0))
                p.setClipping(False)
                p.setPen(QPen(QColor(255, 255, 255, 170), 1))
                p.drawLine(QPointF(0, -ry), QPointF(0, -ry - ROT_GAP))
            p.restore()
            p.setPen(QPen(QColor(0, 0, 0, 120), 1))
            p.setBrush(QColor(255, 214, 10) if sel else QColor(255, 255, 255, 200))
            p.drawEllipse(c, 4, 4)
            if sel:
                p.setBrush(QColor("white"))
                for k, pt in self._handles(s).items():
                    if k == "rot":
                        p.drawEllipse(pt, HANDLE / 2 + 1, HANDLE / 2 + 1)
                    else:
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
            shape = spots[i].get("shape", "ellipse")
            inside = abs(v) <= 1 if shape == "beam" else (
                (abs(u) ** 6 + abs(v) ** 6) <= 1 if shape == "rect" else u * u + v * v <= 1)
            if inside and (shape != "beam" or self.image_rect().contains(pos)):
                return i, "move"
        return -1, None

    def _norm(self, pos):
        r = self.image_rect()
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def mousePressEvent(self, e):
        if self.empty or not self.pix:
            return
        if e.button() == Qt.MouseButton.LeftButton and self.tool.brushing() and not self.comparing:
            self._stroke = [e.position()]
            self.update()
            return
        # 中鍵隨時可以拖曳檢視；左鍵在沒有編輯聚光燈、沒有在刷遮罩的時候拖曳檢視
        if self.zoomed() and (e.button() == Qt.MouseButton.MiddleButton or
                              (e.button() == Qt.MouseButton.LeftButton and not self.editing_spots())):
            self.start_pan(e.position())
            return
        if e.button() != Qt.MouseButton.LeftButton or not self.editing_spots():
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
        if self._pan is not None:
            self.move_pan(pos)
            return
        if self.tool.brushing():
            self._hover = pos
            if getattr(self, "_stroke", None):
                self._stroke.append(pos)
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.update()
            return
        self._hover = None
        if not self._drag:
            if self.empty:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif self.editing_spots():
                _, kind = self._hit(pos)
                self.setCursor(Qt.CursorShape.SizeAllCursor if kind == "move" else
                               Qt.CursorShape.CrossCursor if kind is None else
                               Qt.CursorShape.PointingHandCursor if kind == "rot" else Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor if self.zoomed() else Qt.CursorShape.ArrowCursor)
            return
        d = self._drag
        s = self.tool.p["spots"][self.tool.sel]
        r = self.image_rect()
        edge = max(r.width(), r.height())
        b = d["base"]
        if d["kind"] == "move":
            s["cx"] = min(1.2, max(-0.2, b["cx"] + (pos.x() - d["start"].x()) / r.width()))
            s["cy"] = min(1.2, max(-0.2, b["cy"] + (pos.y() - d["start"].y()) / r.height()))
        elif d["kind"] == "rot":
            c, _, _, _ = self._geo(b)
            ang = math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x())) + 90     # 把手在形狀的正上方
            ang = (ang + 180) % 360 - 180
            if ang > 90:
                ang -= 180
            elif ang < -90:
                ang += 180
            s["angle"] = round(ang)
        elif d["kind"] == "new" and s.get("shape") == "beam":
            # 光帶：從按下的地方往哪個方向拖，光帶就沿著那個方向；寬度用預設值，之後拖邊上的點改
            dx, dy = pos.x() - d["start"].x(), pos.y() - d["start"].y()
            if math.hypot(dx, dy) > 6:
                ang = math.degrees(math.atan2(dy, dx))
                if ang > 90:
                    ang -= 180
                elif ang < -90:
                    ang += 180
                s["angle"] = round(ang)
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
        if self._pan is not None:
            self.end_pan()
            return
        stroke = getattr(self, "_stroke", None)
        if stroke:
            self._stroke = None
            r = self.image_rect()
            pts = [((q.x() - r.x()) / r.width(), (q.y() - r.y()) / r.height()) for q in stroke]
            self.tool.apply_stroke(pts)
            self.update()
            return
        if self._drag:
            d = self._drag
            self._drag = None
            s = self.tool.p["spots"][self.tool.sel]
            if d["kind"] == "new" and s.get("shape") == "beam":
                s["rx"], s["ry"] = 0.5, 0.07
            elif d["kind"] == "new" and s["rx"] < 0.03 and s["ry"] < 0.03:
                # 只點一下沒有拖：給一個預設大小
                s["rx"], s["ry"] = 0.16, 0.2
            self.tool.spots_changed(final=True)
            return
        super().mouseReleaseEvent(e)


# ============================================================ 頁面
class AdjustPage(ToolPage):
    title = "調整"
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
        self.add_output_buttons("用原尺寸輸出調整後的照片（Ctrl+S）")
        self.stage.enable_zoom("隱藏聚光燈框線")
        self.stage.hud.overlay.toggled.connect(lambda *_: self.stage.update())
        # 放大到預覽圖不夠清楚時，停下來 0.25 秒就用原圖重算看得到的那一塊
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(250)
        self._detail_timer.timeout.connect(self._render_detail)
        self._detail_token = None
        self.stage.view_changed.connect(self._detail_timer.start)
        self._relay.detail.connect(self._on_detail)
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
        tabs = self.add_tabs([("basic", "基本"), ("style", "風格"), ("color", "色彩"),
                              ("effects", "效果"), ("spot", "聚光燈"), ("mask", "遮罩")])
        self._build_style(tabs["style"])
        self._build_mask(tabs["mask"])
        B = tabs["basic"]
        B.addWidget(section("白平衡"))
        self._slider(B, "temp", "色溫", gradient=["#3b78d8", "#d8d8d8", "#e2b23c"])
        self._slider(B, "tint", "色調", gradient=["#3fae49", "#d8d8d8", "#c74bc5"])
        head = hbox(section("色調"), None,
                    button("自動", None, None, "依直方圖自動調整曝光、亮部、陰影、白色、黑色", self.auto),
                    button("全部重設", None, "reset", "所有調整回到預設值", self.reset_all), spacing=6)
        B.addWidget(wrap(head))
        self._slider(B, "exposure", "曝光", -5, 5, 0.05, ev_fmt)
        self._slider(B, "contrast", "對比")
        self._slider(B, "highlights", "亮部")
        self._slider(B, "shadows", "陰影")
        self._slider(B, "whites", "白色")
        self._slider(B, "blacks", "黑色")
        B.addWidget(section("外觀"))
        self._slider(B, "texture", "紋理")
        self._slider(B, "clarity", "清晰度")
        self._slider(B, "dehaze", "去朦朧")
        self._slider(B, "vibrance", "細節飽和度")
        self._slider(B, "saturation", "飽和度")

        C = tabs["color"]
        C.addWidget(section("色彩混合"))
        self.hsl_mode = Segmented([("0", "色相"), ("1", "飽和度"), ("2", "明亮度")], "0")
        self.hsl_mode.changed.connect(lambda *_: self._sync_hsl())
        C.addWidget(self.hsl_mode)
        self.hsl_sliders = {}
        for name, hue in D.BANDS:
            s = ParamSlider(HUE_NAMES[name], -100, 100, 0, 1, 0)
            s.changed.connect(lambda v, n=name: self._set_hsl(n, v))
            s.released.connect(self.settle)
            self.hsl_sliders[name] = s
            C.addWidget(s)
        C.addWidget(section("顏色分級"))
        spectrum = [hue_hex(h, 0.75, 0.95) for h in range(0, 361, 30)]
        for key, title in (("sh_hue", "陰影色相"), ("sh_sat", "陰影飽和"), ("hi_hue", "亮部色相"),
                           ("hi_sat", "亮部飽和"), ("balance", "平衡")):
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
        E.addWidget(section("暈影"))
        self._slider(E, "vignette", "總量")
        self._slider(E, "vig_mid", "中點", 0, 100)
        self._slider(E, "vig_feather", "羽化", 0, 100)
        E.addWidget(section("顆粒"))
        self._slider(E, "grain", "總量", 0, 100)
        self._slider(E, "grain_size", "大小", 0, 100)

        S = tabs["spot"]
        self.shape = Segmented(SHAPES, "ellipse")
        self.shape.setToolTip("在照片上拖曳畫出聚光燈（光帶沿著拖曳的方向）\n拖中間移動、拖邊上的點改大小、拖上方的圓點旋轉，Delete 刪除")
        self.shape.changed.connect(self._set_shape)
        S.addWidget(field("形狀", self.shape))
        self.spot_info = label("", "secondary")
        self.del_btn = button("刪除", None, "trash", "刪除選取的聚光燈（Delete）", self.remove_spot)
        S.addWidget(wrap(hbox(self.spot_info, None,
                              button("新增", None, "plus", "在畫面中間加一個聚光燈",
                                     lambda: (self.add_spot(0.5, 0.5, 0.18, 0.22), self.spots_changed(final=True))),
                              self.del_btn, spacing=6)))
        S.addWidget(section("選取的聚光燈"))
        self.spot_sliders = {}
        for key, title, lo, hi, step, fmt, dflt in (
                ("ev", "亮度", -2, 3, 0.05, ev_fmt, 0.8), ("feather", "羽化", 0, 100, 1, None, 60),
                ("warmth", "色溫", -100, 100, 1, None, 0), ("angle", "角度", -90, 90, 1, lambda v: f"{v:+.0f}°", 0)):
            s = ParamSlider(title, lo, hi, dflt, step, dflt, fmt,
                            ["#3b78d8", "#d8d8d8", "#e2b23c"] if key == "warmth" else None)
            s.changed.connect(lambda v, k=key: self._set_spot(k, v))
            s.released.connect(self.settle)
            self.spot_sliders[key] = s
            S.addWidget(s)
        S.addWidget(section("周圍"))
        self._slider(S, "spot_dim", "周圍壓暗", 0, 100)
        self._sync_spot()

        F = self.form
        F.addWidget(section("匯出"))
        self.fmt = QComboBox()
        self.fmt.addItem("JPEG", "jpg")
        self.fmt.addItem("PNG", "png")
        self.quality = SliderField("JPEG 品質", 60, 100, 1, 92, lambda v: f"{v:.0f}%")
        F.addWidget(row(field("格式", self.fmt), self.quality))

    def _build_style(self, Y):
        self.style_tiles = {}
        for title, group in (("膚色基調", ST.UNDERTONES), ("氛圍", ST.MOODS)):
            Y.addWidget(section(title))
            host = QWidget()
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            for i, (key, name) in enumerate(group):
                t = StyleTile(key, name)
                t.clicked.connect(lambda _=False, k=key: self.set_style(k))
                grid.addWidget(t, i // 3, i % 3)
                self.style_tiles[key] = t
            grid.setColumnStretch(3, 1)
            Y.addWidget(host)
        Y.addWidget(section("微調"))
        self.pad = ControlPad()
        self.pad.changed.connect(self._set_pad)
        self.pad.released.connect(self.settle)
        self.pad_info = label("", "secondary", wrap=True)
        side = vbox(self.pad_info, None, spacing=8)
        Y.addWidget(wrap(hbox(self.pad, side, spacing=14)))
        self.palette_slider = ParamSlider("色盤", 0, 100, 100, 1, 100, lambda v: f"{v:.0f}")
        self.palette_slider.changed.connect(lambda v: self._set_style_key("palette", v))
        self.palette_slider.released.connect(self.settle)
        Y.addWidget(self.palette_slider)
        self.palette_slider.setToolTip("風格的顏色有多強")
        self._relay.thumb.connect(self._on_thumb)
        self._thumb_token = None
        self._sync_style()

    def set_style(self, key):
        self.p["style"]["name"] = key
        self._sync_style()
        self.changed(final=True)

    def _set_pad(self, tone, color):
        self.p["style"]["tone"], self.p["style"]["color"] = tone, color
        self._paint_pad_info()
        self.changed()

    def _set_style_key(self, key, v):
        self.p["style"][key] = v
        self.changed()

    def _paint_pad_info(self):
        st = self.p["style"]
        name = dict(ST.UNDERTONES + ST.MOODS).get(st["name"], "")
        self.pad_info.setText(f'{name}\n色調 {st["tone"]:+.0f}\n色彩 {st["color"]:+.0f}')

    def _sync_style(self):
        st = self.p["style"]
        for k, t in self.style_tiles.items():
            t.setChecked(k == st["name"])
        self.pad.set_values(st["tone"], st["color"])
        self.palette_slider.set(st["palette"])
        self._paint_pad_info()

    def _render_thumbs(self):
        """每個風格套在這張照片的小圖上，當作格子裡的預覽（背景算，算好一個顯示一個）。"""
        if self.small is None:
            return
        token = self._thumb_token = object()
        h, w = self.small.shape[:2]
        k = min(1.0, 180 / max(h, w))
        from PIL import Image
        tiny = np.asarray(Image.fromarray(self.small).resize((max(8, round(w * k)), max(8, round(h * k)))))

        def run():
            for key, _ in ST.UNDERTONES + ST.MOODS:
                if token is not self._thumb_token:
                    return
                q = D.defaults()
                q["style"]["name"] = key
                self._relay.thumb.emit(token, key, D.render(tiny, q))

        threading.Thread(target=run, daemon=True).start()

    def _on_thumb(self, token, key, arr):
        if token is self._thumb_token and key in self.style_tiles:
            self.style_tiles[key].set_pixmap(QPixmap.fromImage(rgb_to_qimage(arr)))

    # ---------------------------------------------------------------- 遮罩
    def _build_mask(self, M):
        self.subject = self.mask_eff = None
        self._overlay = None
        self._subject_token = None
        M.addWidget(section("主體"))
        self.subject_label = label("", "secondary")
        self.redetect_btn = button("重新辨識", None, "refresh", "再辨識一次主體", self.detect_subject)
        self.sb_dl_btn = button(f"下載 AI 主體模型（約 {SB.MODEL_SIZE_MB:g} MB）", "plain", "download",
                                "辨識得更準，只需要下載一次", self.ask_subject_download)
        M.addWidget(wrap(hbox(self.subject_label, None, self.redetect_btn, spacing=6)))
        M.addWidget(wrap(hbox(self.sb_dl_btn, None)))
        M.addWidget(section("遮罩"))
        self.mask_mode = Segmented([("none", "不遮罩"), ("subject", "遮罩主體"), ("background", "遮罩非主體")], "none")
        self.mask_mode.setToolTip("被遮住的地方維持原樣，調整只套在沒有遮住的地方")
        self.mask_mode.changed.connect(self._set_mask_mode)
        M.addWidget(self.mask_mode)
        self.mask_sliders = {}
        for key, title, lo, hi, tip in (("shift", "範圍", -100, 100, "主體的範圍往外擴（+）或往內縮（−）"),
                                        ("feather", "羽化", 0, 100, "遮罩邊緣柔和的程度")):
            s = ParamSlider(title, lo, hi, D.DEFAULTS["mask"][key], 1, D.DEFAULTS["mask"][key])
            s.setToolTip(tip)
            s.changed.connect(lambda v, k=key: self._set_mask_key(k, v))
            s.released.connect(self.settle)
            self.mask_sliders[key] = s
            M.addWidget(s)
        self.show_mask = Flag("顯示遮罩範圍")
        self.show_mask.setChecked(True)
        self.show_mask.setToolTip("用紅色標出被遮住、不會調整的地方")
        self.show_mask.toggled.connect(lambda *_: self.stage.update())
        M.addWidget(self.show_mask)
        M.addWidget(section("手動修改"))
        self.brush_mode = Segmented([("off", "瀏覽"), ("add", "加入主體"), ("sub", "移除主體")], "off")
        self.brush_mode.setToolTip("在照片上刷：加入或移除主體的範圍（中鍵拖曳移動檢視）")
        self.brush_mode.changed.connect(lambda *_: self.stage.update())
        M.addWidget(self.brush_mode)
        self.brush_size = ParamSlider("筆刷大小", 2, 40, 10, 1, 10)
        self.brush_size.setToolTip("筆刷半徑（照片長邊的百分比 × 4）")
        M.addWidget(self.brush_size)
        self.brush_auto = Flag("自動辨識邊緣")
        self.brush_auto.setChecked(True)
        self.brush_auto.setToolTip("依顏色判斷刷到的地方是不是同一個東西，並貼齊邊緣；關掉就照筆刷的範圍")
        self.undo_btn = button("復原", None, "reset", "復原上一筆（Ctrl+Z）", self.undo_stroke)
        self.clear_btn = button("清除修改", "danger-plain", None, "清掉所有手動修改，回到自動辨識的結果", self.clear_strokes)
        M.addWidget(wrap(hbox(self.brush_auto, None, self.undo_btn, self.clear_btn, spacing=6)))
        self.edit_add = self.edit_sub = None
        self._edit_undo = []
        self._feats = None
        self._stroke_token = None
        self._relay.stroke.connect(self._on_stroke)
        undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo.activated.connect(lambda: self.tabs.value() == "mask" and self.undo_stroke())
        self._sync_edit_buttons()
        self._relay.subject.connect(self._on_subject)
        self._relay.download.connect(self._on_sb_download)
        self._relay.download_done.connect(self._on_sb_downloaded)
        self._paint_subject_method()

    def on_models_changed(self):
        self._paint_subject_method()
        if self.subject is not None:
            self.subject = None
            self.detect_subject()     # 換了辨識方法，重新辨識

    def _paint_subject_method(self, busy=False):
        if busy:
            self.subject_label.setText("辨識中…")
        else:
            self.subject_label.setText(SB.method_name())
        self.subject_label.setToolTip("U²-Net-p 顯著物體偵測" if SB.method_name().startswith("AI") else
                                      "依顏色、遠近與位置估計，主體清楚時堪用；下載 AI 模型會準確很多")
        self.sb_dl_btn.setVisible(SB.runtime_available() and not SB.installed())

    def detect_subject(self):
        if self.small is None:
            return
        token = self._subject_token = object()
        rgb = self.small
        self._paint_subject_method(busy=True)

        def run():
            try:
                m, how = SB.detect(rgb)
                self._relay.subject.emit(token, m, how)
            except Exception as e:  # noqa: BLE001
                self._relay.subject.emit(token, None, str(e))

        threading.Thread(target=run, daemon=True).start()

    def _on_subject(self, token, m, how):
        if token is not self._subject_token:
            return
        if m is None:
            self.subject_label.setText("辨識失敗")
            self.subject_label.setToolTip(how)
            return
        self.subject = m
        self._paint_subject_method()
        self._update_mask()

    def _set_mask_mode(self, mode):
        self.p["mask"]["mode"] = mode
        if mode != "none" and SB.runtime_available() and not SB.installed() and not getattr(self, "_sb_offered", False):
            # 快速估計在人多、背景複雜的照片常常不準；第一次用遮罩時問一次要不要下載 AI 模型
            self._sb_offered = True
            QTimer.singleShot(0, self.ask_subject_download)
        if mode != "none" and self.subject is None:
            self.detect_subject()         # 第一次用到才辨識；算好之後 _on_subject 會套用
        self._update_mask()

    def _set_mask_key(self, key, v):
        self.p["mask"][key] = v
        self._update_mask(final=False)

    # ---------------------------------------------------------------- 遮罩：筆刷
    def brushing(self):
        return self.tabs.value() == "mask" and self.brush_mode.value() != "off" and self.small is not None

    def brush_radius_px(self):
        """筆刷半徑，以預覽圖（遮罩的解析度）的像素計。"""
        h, w = self.small.shape[:2]
        return self.brush_size.value() / 400 * max(h, w)

    def brush_screen_radius(self):
        r = self.stage.image_rect()
        if self.small is None or r.isEmpty():
            return 10.0
        return self.brush_radius_px() * r.width() / self.small.shape[1]

    def _combined_subject(self):
        """自動辨識的主體加上手動修改。"""
        base = self.subject
        if self.edit_add is None:
            return base
        if base is None:
            base = np.zeros(self.edit_add.shape, np.float32)
        return np.clip(np.maximum(base, self.edit_add) * (1 - self.edit_sub), 0, 1).astype(np.float32)

    def apply_stroke(self, norm_pts):
        if self.small is None or not norm_pts:
            return
        if self.p["mask"]["mode"] == "none":
            self.mask_mode.setValue("subject", emit=True)       # 開始刷了，先開遮罩才看得到效果
        h, w = self.small.shape[:2]
        pts = [(x * w, y * h) for x, y in norm_pts]
        if self._feats is None:
            self._feats = SB.stroke_features(self.small)
        token = self._stroke_token = object()
        feats, radius, auto, mode = self._feats, self.brush_radius_px(), self.brush_auto.isChecked(), self.brush_mode.value()

        def run():
            y0, x0, patch = SB.smart_stroke(feats, pts, radius, auto)
            self._relay.stroke.emit(token, (y0, x0, patch, mode))

        threading.Thread(target=run, daemon=True).start()

    def _on_stroke(self, token, res):
        y0, x0, patch, mode = res
        if patch.size == 0:
            return
        h, w = self.small.shape[:2]
        if self.edit_add is None:
            self.edit_add = np.zeros((h, w), np.float32)
            self.edit_sub = np.zeros((h, w), np.float32)
        self._edit_undo.append((self.edit_add.copy(), self.edit_sub.copy()))
        del self._edit_undo[:-20]
        ph, pw = patch.shape
        sl = (slice(y0, y0 + ph), slice(x0, x0 + pw))
        if mode == "add":
            self.edit_add[sl] = np.maximum(self.edit_add[sl], patch)
            self.edit_sub[sl] *= 1 - patch
        else:
            self.edit_sub[sl] = np.maximum(self.edit_sub[sl], patch)
            self.edit_add[sl] *= 1 - patch
        self._sync_edit_buttons()
        self._update_mask()

    def undo_stroke(self):
        if not self._edit_undo:
            return
        self.edit_add, self.edit_sub = self._edit_undo.pop()
        self._sync_edit_buttons()
        self._update_mask()

    def clear_strokes(self):
        if self.edit_add is None:
            return
        self._edit_undo.append((self.edit_add.copy(), self.edit_sub.copy()))
        self.edit_add = self.edit_sub = None
        self._sync_edit_buttons()
        self._update_mask()

    def _sync_edit_buttons(self):
        self.undo_btn.setEnabled(bool(self._edit_undo))
        self.clear_btn.setEnabled(self.edit_add is not None)

    def _update_mask(self, final=True):
        mk = self.p["mask"]
        self.mask_eff = SB.effective(self._combined_subject(), mk["mode"], mk["feather"], mk["shift"])
        self._overlay = None
        if self.mask_eff is not None:
            rgba = SB.overlay_rgba(self.mask_eff)
            h, w = rgba.shape[:2]
            self._overlay = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        for s in self.mask_sliders.values():
            s.setEnabled(mk["mode"] != "none")
        self.stage.update()
        self.changed(final=final)

    def mask_overlay(self):
        if self.tabs.value() == "mask" and self.show_mask.isChecked():
            return self._overlay
        return None

    def _sync_mask(self):
        mk = self.p["mask"]
        self.mask_mode.setValue(mk["mode"])
        for k, s in self.mask_sliders.items():
            s.set(mk[k])
            s.setEnabled(mk["mode"] != "none")

    def ask_subject_download(self):
        if not dialogs.confirm(
                self.win, "下載 AI 主體模型？",
                f"快速估計在人多、背景複雜的照片常常不準。\n\n檔案：U²-Net-p 顯著物體偵測（ONNX）\n"
                f"來源：{SB.MODEL_SOURCE}\n大小：約 {SB.MODEL_SIZE_MB:g} MB\n\n"
                "只需要下載一次，之後離線也能用。照片不會被上傳。", confirm_text="下載"):
            return
        self.sb_dl_btn.setEnabled(False)

        def run():
            try:
                SB.download(lambda d, t: self._relay.download.emit(d, t))
                self._relay.download_done.emit(None)
            except Exception as e:  # noqa: BLE001
                self._relay.download_done.emit(e)

        threading.Thread(target=run, daemon=True).start()

    def _on_sb_download(self, done, total):
        self.sb_dl_btn.setText(f"下載中… {done / 1e6:.1f} / {total / 1e6:.1f} MB" if total else f"下載中… {done / 1e6:.1f} MB")

    def _on_sb_downloaded(self, error):
        self.sb_dl_btn.setEnabled(True)
        self.sb_dl_btn.setText(f"下載 AI 主體模型（約 {SB.MODEL_SIZE_MB:g} MB）")
        if error:
            dialogs.alert(self.win, "下載失敗", str(error), tone="danger")
            return
        notify("AI 主體模型已就緒", "success")
        self._paint_subject_method()
        if self.small is not None:
            self.detect_subject()

    def spot_mode(self):
        return self.tabs.value() == "spot"

    def on_tab(self, key):
        if key == "mask" and self.subject is None and self.small is not None:
            self.detect_subject()          # 切到遮罩分頁就先辨識好，選遮罩方式時不用等
        self.stage.hud.overlay.setVisible(key == "spot")
        self.stage.place_overlays()
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
        self._sync_style()
        self._sync_mask()

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
        self._update_mask(final=False)
        self.changed(final=True)
        self.stage.update()

    # ---------------------------------------------------------------- 聚光燈
    def add_spot(self, cx, cy, rx, ry):
        shape = self.shape.value()
        if shape == "beam":
            rx, ry = 0.5, max(ry, 0.07)
        self.p["spots"].append({"shape": shape, "cx": cx, "cy": cy, "rx": rx, "ry": ry, "angle": 0.0, "ev": 0.8,
                                "feather": 60.0, "warmth": 0.0})
        self.sel = len(self.p["spots"]) - 1
        self._sync_spot()

    def _set_shape(self, shape):
        """形狀：之後畫的聚光燈用這個；有選取的話，選取的那個也換。"""
        if 0 <= self.sel < len(self.p["spots"]):
            s = self.p["spots"][self.sel]
            if s.get("shape", "ellipse") != shape:
                if shape == "beam":
                    s["rx"] = 0.5
                    s["ry"] = min(s["ry"], 0.15)
                s["shape"] = shape
                self.spots_changed(final=True)

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
        self.spot_info.setText(f'聚光燈 {self.sel + 1} / {len(spots)}' if on else
                               "還沒有聚光燈" if not spots else f'共 {len(spots)} 個')
        self.del_btn.setEnabled(on)
        for key, s in self.spot_sliders.items():
            s.setEnabled(on)
            if on:
                s.set(spots[self.sel].get(key, s.default))
        if on:
            self.shape.setValue(spots[self.sel].get("shape", "ellipse"))
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
        self.subject = self.mask_eff = self._overlay = None
        self._subject_token = None
        self.edit_add = self.edit_sub = self._feats = None
        self._edit_undo = []
        self._sync_edit_buttons()
        self.brush_mode.setValue("off")
        self._sync_all()
        self._paint_subject_method()
        if self.tabs.value() == "mask":
            self.detect_subject()
        self.stage.pix = QPixmap.fromImage(small)
        self.stage.original = self.stage.pix
        self.stage.set_detail(None, None)
        self.stage.reset_view()
        self.update_scopes(self.small, self.small)
        self._render_thumbs()
        self.set_status(f"{img.width()}×{img.height()}")

    # ---------------------------------------------------------------- 預覽
    def changed(self, final=False):
        if self.small is None:
            return
        self.gen += 1
        self.stage.set_detail(None, None)      # 舊的清晰區塊是舊參數算的
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
        p, mask = _copy(self.p), self.mask_eff

        def run():
            out = D.render(arr, p, cancel=lambda: gen != self.gen, mask=mask)
            if out is not None:
                self._relay.done.emit(gen, kind, out)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, gen, kind, out):
        if gen != self.gen or (kind == "draft" and self._full_shown == gen):
            return
        if kind == "full":
            self._full_shown = gen
            self.update_scopes(out)
            self._detail_timer.start()
        self.stage.pix = QPixmap.fromImage(rgb_to_qimage(out))
        self.stage.update()

    # ---------------------------------------------------------------- 放大時的清晰區塊
    def _render_detail(self):
        req = self.detail_crop(self.full)
        if req is None:
            self.stage.set_detail(None, None)
            return
        token = self._detail_token = object()
        gen, p, small, mask = self.gen, _copy(self.p), self.small, self.mask_eff

        def run():
            rgb = qimage_to_rgb(req["img"])
            out = D.render_region(rgb, p, D.prepare(small, p), req["W"], req["H"], req["x0"], req["y0"], mask=mask)
            self._relay.detail.emit(token, gen, out, rgb, req["region"])

        threading.Thread(target=run, daemon=True).start()

    def _on_detail(self, token, gen, out, rgb, region):
        if token is not self._detail_token or gen != self.gen:
            return
        self.stage.set_detail(QPixmap.fromImage(rgb_to_qimage(out)), region, QPixmap.fromImage(rgb_to_qimage(rgb)))

    # ---------------------------------------------------------------- 輸出
    def output_name(self):
        return f"{safe_name(self.path)}_adjust.{self.fmt.currentData()}"

    def output_job(self):
        if self.full is None:
            return None
        full, p, mask = self.full, _copy(self.p), self.mask_eff

        def job(progress):
            progress(0.03, "準備原尺寸")
            rgb = qimage_to_rgb(full)
            out = D.render(rgb, p, progress=lambda f: progress(0.05 + 0.8 * f, "套用調整"), mask=mask)
            return rgb_to_qimage(out)

        return job


def _copy(p):
    import copy
    return copy.deepcopy(p)
