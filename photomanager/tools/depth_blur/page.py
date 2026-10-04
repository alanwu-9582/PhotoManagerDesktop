"""景深模糊（參考 Lightroom 的「鏡頭模糊」）。

載入照片就自動偵測遠近，右邊出現深度分佈的直方圖：拖兩個把手選「對焦範圍」，
範圍內保持清楚、範圍外依距離逐漸變糊。也可以直接點照片上想對焦的地方。
模糊是照鏡頭的方式算的（depth.render）：用光圈值決定最大的模糊圈，光圈形狀可以是圓形或多邊形，
亮點會散成一顆顆散景。
預覽在長邊 1100px 的縮圖上算、而且在背景執行緒裡算；儲存時才用原尺寸重算一次。
"""
from __future__ import annotations


import threading

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QWidget

from ...ui import dialogs, theme
from ...ui.widgets import Flag, Segmented, SliderField, Stepper, button, field, hbox, label, notify, row, section
from ..common import Stage, ToolPage, safe_name
from . import depth as D
from . import model

PREVIEW_EDGE = 1100
DRAFT_EDGE = 480          # 拖曳 / 點對焦時先顯示的小圖
BINS = 64
F_STOPS = [1.2, 1.4, 1.8, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0]
BLADES = [(0, "圓形"), (9, "9 葉"), (7, "7 葉"), (6, "6 葉"), (5, "5 葉"), (-1, "圓環")]


def max_radius(f_number, edge):
    """光圈越大（f 值越小）模糊圈越大：f/1.2 約是長邊的 5%，跟模糊圈和光圈直徑成正比一樣。"""
    return edge * 0.05 * 1.2 / f_number


def qimage_to_rgb(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())
    return a[:, : w * 3].reshape(h, w, 3).copy()


def rgb_to_qimage(a: np.ndarray) -> QImage:
    a = np.ascontiguousarray(a)
    h, w = a.shape[:2]
    return QImage(a.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


# ============================================================ 深度直方圖
class DepthHistogram(QWidget):
    """深度分佈 + 對焦範圍。把手之間是保持清楚的範圍；拖中間可以整段平移。"""
    changed = Signal(float, float)
    released = Signal()

    PAD = 8
    STRIP = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hist = np.zeros(BINS, np.float32)
        self.lo, self.hi = 0.0, 0.25
        self._drag = None
        self.setMinimumHeight(118)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, hist, lo, hi):
        self.hist = hist
        self.lo, self.hi = lo, hi
        self.update()

    def set_range(self, lo, hi):
        self.lo, self.hi = lo, hi
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(self.PAD, 6, self.width() - self.PAD * 2, self.height() - 6 - self.STRIP - 10)

    def _x(self, v):
        p = self._plot()
        return p.x() + v * p.width()

    def _v(self, x):
        p = self._plot()
        return min(1.0, max(0.0, (x - p.x()) / max(1, p.width())))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        bg = theme.round_rect(QPainterPath(), QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8)
        p.fillPath(bg, theme.c("content"))
        p.setPen(theme.c("separator_hex"))
        p.drawPath(bg)
        # 範圍底色
        sel = QRectF(self._x(self.lo), plot.y(), self._x(self.hi) - self._x(self.lo), plot.height())
        tint = theme.c("accent")
        tint.setAlphaF(0.14)
        p.fillRect(sel, tint)
        # 直方圖
        bw = plot.width() / BINS
        for i, v in enumerate(self.hist):
            h = max(1.0, v * (plot.height() - 4))
            r = QRectF(plot.x() + i * bw + 0.5, plot.bottom() - h, max(1.0, bw - 1), h)
            mid = (i + 0.5) / BINS
            col = theme.c("accent") if self.lo <= mid <= self.hi else theme.c("tertiary")
            p.fillPath(theme.round_rect(QPainterPath(), r, 1.5), col)
        # 近 → 遠的色條
        strip = QRectF(plot.x(), plot.bottom() + 6, plot.width(), self.STRIP)
        grad = QLinearGradient(strip.left(), 0, strip.right(), 0)
        for t, c in ((0, "#ffe082"), (0.25, "#f0785a"), (0.5, "#aa3c8c"), (0.75, "#462882"), (1, "#14143c")):
            grad.setColorAt(t, QColor(c))
        p.fillPath(theme.round_rect(QPainterPath(), strip, 3), grad)
        # 把手
        for v in (self.lo, self.hi):
            x = self._x(v)
            hr = QRectF(x - 3, plot.y() - 2, 6, plot.height() + self.STRIP + 10)
            p.fillPath(theme.round_rect(QPainterPath(), hr, 1.9), theme.c("label"))
        p.setFont(theme.font("caption"))
        p.setPen(theme.c("secondary"))
        p.end()

    def mousePressEvent(self, e):
        x = e.position().x()
        xl, xh = self._x(self.lo), self._x(self.hi)
        if abs(x - xl) <= 8 and abs(x - xl) <= abs(x - xh):
            self._drag = ("lo", x)
        elif abs(x - xh) <= 8:
            self._drag = ("hi", x)
        elif xl < x < xh:
            self._drag = ("move", x, self.lo, self.hi)
        else:
            # 點在範圍外：把比較近的那個把手拉過來
            v = self._v(x)
            if abs(x - xl) < abs(x - xh):
                self.lo = min(v, self.hi - 0.01)
                self._drag = ("lo", x)
            else:
                self.hi = max(v, self.lo + 0.01)
                self._drag = ("hi", x)
            self.update()
            self.changed.emit(self.lo, self.hi)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if not self._drag:
            near = min(abs(x - self._x(self.lo)), abs(x - self._x(self.hi))) <= 8
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.PointingHandCursor)
            return
        kind = self._drag[0]
        v = self._v(x)
        if kind == "lo":
            self.lo = min(v, self.hi - 0.01)
        elif kind == "hi":
            self.hi = max(v, self.lo + 0.01)
        else:
            _, x0, lo0, hi0 = self._drag
            d = self._v(x) - self._v(x0)
            d = min(max(d, -lo0), 1 - hi0)
            self.lo, self.hi = lo0 + d, hi0 + d
        self.update()
        self.changed.emit(self.lo, self.hi)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None
            self.released.emit()


# ============================================================ 照片區
class BlurStage(Stage):
    focus_clicked = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.pix: QPixmap | None = None
        self.marker: QPointF | None = None
        self.busy = False
        self.setMouseTracking(True)

    def paint_content(self, p: QPainter):
        if not self.pix:
            return
        r = self.image_rect()
        self.paint_photo(p, self.pix)
        if self.marker is not None and not self.comparing and self.overlay_visible:
            c = QPointF(r.x() + self.marker.x() * r.width(), r.y() + self.marker.y() * r.height())
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 140), 3.5))
            p.drawRect(QRectF(c.x() - 14, c.y() - 14, 28, 28))
            p.setPen(QPen(QColor(255, 214, 10), 1.6))
            p.drawRect(QRectF(c.x() - 14, c.y() - 14, 28, 28))
        if self.busy:
            p.setPen(Qt.PenStyle.NoPen)
            tag = QRectF(min(r.right(), self.width()) - 92, max(r.y(), 0) + 10, 82, 24)
            p.fillPath(theme.round_rect(QPainterPath(), tag, 6), QColor(0, 0, 0, 160))
            p.setPen(QColor("white"))
            p.setFont(theme.font("caption", 600))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, "計算中…")

    def mousePressEvent(self, e):
        if self.empty or not self.pix or e.button() != Qt.MouseButton.LeftButton:
            return
        r = self.image_rect()
        if r.contains(e.position()):
            x = (e.position().x() - r.x()) / r.width()
            y = (e.position().y() - r.y()) / r.height()
            self.marker = QPointF(x, y)
            self.update()
            self.focus_clicked.emit(x, y)

    def mouseMoveEvent(self, e):
        if self.empty:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor if self.image_rect().contains(e.position())
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self.empty:
            super().mouseReleaseEvent(e)


class _Relay(QObject):
    depth = Signal(object, object, str)       # token, depth array | None, 說明 / 錯誤
    draft = Signal(object, object)            # token, 小圖的成品（先頂著，完整的預覽接著來）
    preview = Signal(object, object)          # token, rgb array
    download = Signal(int, int)
    download_done = Signal(object)            # error | None
    saved = Signal(object, object)            # path, error


# ============================================================ 頁面
class DepthBlurPage(ToolPage):
    title = "景深模糊"
    scopes = True
    compare = True

    def __init__(self, window):
        super().__init__(window)
        self.full: QImage | None = None
        self.small: np.ndarray | None = None       # 預覽用的 RGB
        self.depth: np.ndarray | None = None       # 預覽大小的深度
        self.method = ""
        self._depth_token = self._render_token = None
        self._relay = _Relay()
        self._relay.depth.connect(self._on_depth)
        self._relay.preview.connect(self._on_preview)
        self._relay.download.connect(self._on_download_progress)
        self._relay.download_done.connect(self._on_downloaded)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render)
        self.stage.focus_clicked.connect(self.focus_at)
        self.add_output_buttons("用原尺寸輸出（Ctrl+S）")
        self.stage.enable_zoom("隱藏對焦框", zoom=False)
        self.stage.hud.overlay.toggled.connect(lambda *_: self.stage.update())
        self._relay.draft.connect(self._on_draft)
        self.draft = self.draft_depth = None
        self._build()

    def make_stage(self):
        return BlurStage()

    # ---------------------------------------------------------------- 控制項
    def _build(self):
        F = self.form
        F.addWidget(section("偵測"))
        self.method_label = label("", "secondary", wrap=True)
        self.redetect = button("重新偵測", None, "refresh", on_click=self.detect)
        self.dl_btn = button(f'下載 AI 深度模型（約 {model.MODEL_SIZE_MB} MB）', "plain", "download",
                             "更準確的遠近判斷，只需要下載一次", self.ask_download)
        F.addWidget(self.method_label)
        F.addWidget(self._wrap(hbox(self.redetect, self.dl_btn, None, spacing=6)))
        self.view = Segmented([("result", "成品"), ("depth", "深度圖"), ("original", "原圖")], "result")
        self.view.changed.connect(lambda *_: self._show())
        F.addWidget(field("顯示", self.view))

        F.addWidget(section("對焦範圍"))
        self.hist = DepthHistogram()
        # 拖曳時只更新深度圖的顯示（很快）；成品等放開才重算
        self.hist.changed.connect(lambda *_: self.view.value() == "depth" and self._show())
        self.hist.released.connect(self.schedule)
        self.hist.setToolTip("拖兩邊的把手選對焦範圍，或直接點照片上想對焦的地方")
        F.addWidget(self.hist)
        self.bg_only = Flag("只模糊背景")
        self.bg_only.toggled.connect(lambda *_: self.schedule())
        F.addWidget(self.bg_only)

        F.addWidget(section("鏡頭"))
        self.aperture = Stepper([(f, f"f/{f:g}") for f in F_STOPS], 1.8, tip="光圈值：越小越糊", expand=True)
        self.aperture.changed.connect(lambda *_: self.schedule())
        self.feather = SliderField("過渡", 1, 50, 1, 8, lambda v: f"{v:.0f}%")
        self.feather.committed.connect(lambda *_: self.schedule())
        F.addWidget(row(field("光圈", self.aperture), self.feather))
        # 光圈形狀：圓形、幾片葉片圍出的多邊形，或反射鏡頭那種中間空心的圓環
        self.blades = QComboBox()
        for v, lb in BLADES:
            self.blades.addItem(lb, v)
        self.blades.currentIndexChanged.connect(lambda *_: self.schedule())
        self.bokeh = SliderField("散景亮點", 0, 100, 1, 50, lambda v: f"{v:.0f}")
        self.bokeh.committed.connect(lambda *_: self.schedule())
        F.addWidget(row(field("光圈形狀", self.blades), self.bokeh))

        F.addWidget(section("匯出"))
        self.fmt = QComboBox()
        self.fmt.addItem("JPEG", "jpg")
        self.fmt.addItem("PNG", "png")
        self.quality = SliderField("JPEG 品質", 60, 100, 1, 92, lambda v: f"{v:.0f}%")
        F.addWidget(row(field("格式", self.fmt), self.quality))
        F.addStretch(1)
        self._paint_method()

    @staticmethod
    def _wrap(lay):
        w = QWidget()
        w.setLayout(lay)
        return w

    def on_models_changed(self):
        model.reset()
        self.method = ""
        self._paint_method()
        if self.small is not None:
            self.detect()            # 用新的方法重新偵測

    def _paint_method(self):
        has_rt, has_model = model.runtime_available(), model.installed()
        self.dl_btn.setVisible(has_rt and not has_model)
        # 畫面上只留一句；詳細原因放在提示裡
        if self.method and self.method != "AI":
            text, tip = "快速估計", self.method
        elif has_model and has_rt:
            text, tip = "AI 深度模型", "Depth Anything V2"
        elif not has_rt:
            text, tip = "快速估計", "沒有安裝 onnxruntime，無法使用 AI 模型"
        else:
            text, tip = "快速估計", "只看位置與清晰度，遠近常常不準；下載 AI 模型會準確很多"
        self.method_label.setText(text)
        self.method_label.setToolTip(tip)

    # ---------------------------------------------------------------- 讀圖與偵測
    def on_image(self, path, img):
        self.full = img
        edge = max(img.width(), img.height())
        small = img if edge <= PREVIEW_EDGE else img.scaled(
            img.size() * (PREVIEW_EDGE / edge), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.small = qimage_to_rgb(small)
        self.depth = None
        self.stage.marker = None
        self.stage.pix = QPixmap.fromImage(small)
        self.stage.original = self.stage.pix
        self.stage.reset_view()
        self.update_scopes(self.small, self.small)
        k = min(1.0, DRAFT_EDGE / max(small.width(), small.height()))
        self.draft = qimage_to_rgb(small.scaled(max(1, round(small.width() * k)), max(1, round(small.height() * k)),
                                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
        self.set_status(f"{img.width()}×{img.height()}")
        self.detect()

    def detect(self):
        if self.small is None:
            return
        token = self._depth_token = object()
        rgb = self.small
        self.stage.busy = True
        self.stage.update()
        use_model = model.runtime_available() and model.installed()

        def run():
            try:
                if use_model:
                    d = model.predict(rgb)
                    how = "AI"
                else:
                    d = D.estimate_depth(rgb)
                    how = ""
                d = D.refine_to(d, rgb)
                self._relay.depth.emit(token, d, how)
            except Exception as e:  # noqa: BLE001
                try:
                    d = D.refine_to(D.estimate_depth(rgb), rgb)
                    self._relay.depth.emit(token, d, f'AI 模型無法使用（{e}），改用快速估計。')
                except Exception as e2:  # noqa: BLE001
                    self._relay.depth.emit(token, None, str(e2))

        threading.Thread(target=run, daemon=True).start()

    def _on_depth(self, token, d, how):
        if token is not self._depth_token:
            return
        self.stage.busy = False
        if d is None:
            self.set_status(f'偵測失敗: {how}', "error")
            return
        self.depth = d
        self.draft_depth = D.resize_f(d, self.draft.shape[1], self.draft.shape[0])
        self.method = how
        self._paint_method()
        # 預設對焦：畫面中間偏下那一塊（大部分照片的主體所在），範圍寬 0.22。
        c = D.focus_at(d, 0.5, 0.6)
        self.hist.set_data(D.histogram(d, BINS), max(0.0, c - 0.11), min(1.0, c + 0.11))
        self._render()

    def focus_at(self, x, y):
        if self.depth is None:
            return
        c = D.focus_at(self.depth, x, y)
        half = max(0.03, (self.hist.hi - self.hist.lo) / 2)
        lo, hi = max(0.0, c - half), min(1.0, c + half)
        self.hist.set_range(lo, hi)
        self.schedule()

    # ---------------------------------------------------------------- 預覽
    def params(self):
        return (self.hist.lo, self.hist.hi, self.feather.value() / 100, self.bg_only.isChecked(),
                float(self.aperture.value()), int(self.blades.currentData()), self.bokeh.value() / 100)

    def schedule(self):
        if self.depth is not None:
            self._timer.start()

    def _render(self):
        if self.depth is None or self.small is None:
            return
        if self.view.value() != "result":
            self._show()
            return
        token = self._render_token = object()
        rgb, depth = self.small, self.depth
        lo, hi, feather, bg_only, fnum, blades, bokeh = self.params()
        self._pending_key = (self.params(), id(depth))
        self.stage.busy = True
        self.stage.update()

        draft, ddepth = self.draft, self.draft_depth
        cancel = lambda: token is not self._render_token  # noqa: E731

        def run():
            # 先用小圖算一張（約 0.1 秒）馬上顯示，再算預覽大小的那一張
            if ddepth is not None:
                amap = D.blur_amount_map(ddepth, lo, hi, feather, bg_only)
                out = D.render(draft, amap, max_radius(fnum, max(draft.shape[:2])), blades=blades, bokeh=bokeh,
                               cancel=cancel)
                if not cancel():
                    self._relay.draft.emit(token, out)
            amap = D.blur_amount_map(depth, lo, hi, feather, bg_only)
            out = D.render(rgb, amap, max_radius(fnum, max(rgb.shape[:2])), blades=blades, bokeh=bokeh, cancel=cancel)
            self._relay.preview.emit(token, out)

        threading.Thread(target=run, daemon=True).start()

    def _on_draft(self, token, out):
        if token is self._render_token and self.view.value() == "result":
            self.stage.pix = QPixmap.fromImage(rgb_to_qimage(out))
            self.stage.update()

    def _on_preview(self, token, out):
        if token is not self._render_token:
            return
        self.stage.busy = False
        self._result = out
        self._result_key = self._pending_key
        self.update_scopes(out)
        self._show()

    def _show(self):
        if self.small is None:
            return
        mode = self.view.value()
        if mode == "original" or self.depth is None:
            arr = self.small
        elif mode == "depth":
            arr = D.colorize(self.depth, self.hist.lo, self.hist.hi)
        else:
            arr = getattr(self, "_result", None)
            fresh = getattr(self, "_result_key", None) == (self.params(), id(self.depth))
            if arr is None or arr.shape != self.small.shape or not fresh:
                if arr is not None and arr.shape == self.small.shape:
                    self.stage.pix = QPixmap.fromImage(rgb_to_qimage(arr))   # 先頂著舊的
                    self.stage.update()
                self._render()
                return
        self.stage.pix = QPixmap.fromImage(rgb_to_qimage(arr))
        self.stage.update()

    # ---------------------------------------------------------------- 模型下載
    def ask_download(self):
        if not dialogs.confirm(
                self.win, "下載 AI 深度模型？",
                f'檔案：Depth Anything V2 Small（int8 量化 ONNX）\n來源：{model.MODEL_SOURCE}\n大小：約 {model.MODEL_SIZE_MB} MB\n\n只需要下載一次，之後離線也能用。照片不會被上傳。',
                confirm_text="下載"):
            return
        self.dl_btn.setEnabled(False)

        def run():
            try:
                model.download(lambda d, t: self._relay.download.emit(d, t))
                self._relay.download_done.emit(None)
            except Exception as e:  # noqa: BLE001
                self._relay.download_done.emit(e)

        threading.Thread(target=run, daemon=True).start()

    def _on_download_progress(self, done, total):
        mb = done / 1e6
        self.dl_btn.setText(f'下載中… {mb:.1f} / {total / 1e6:.0f} MB' if total else f'下載中… {mb:.1f} MB')

    def _on_downloaded(self, error):
        self.dl_btn.setEnabled(True)
        self.dl_btn.setText(f'下載 AI 深度模型（約 {model.MODEL_SIZE_MB} MB）')
        if error:
            dialogs.alert(self.win, "下載失敗", str(error), tone="danger")
            return
        model.reset()
        self.method = ""
        self._paint_method()
        notify("AI 深度模型已就緒", "success")
        self.detect()

    # ---------------------------------------------------------------- 儲存
    def output_name(self):
        return f"{safe_name(self.path)}_blur.{self.fmt.currentData()}"

    def output_job(self):
        if self.full is None or self.depth is None:
            return None
        full, depth = self.full, self.depth
        lo, hi, feather, bg_only, fnum, blades, bokeh = self.params()

        def job(progress):
            progress(0.03, "準備原尺寸")
            full_rgb = qimage_to_rgb(full)
            progress(0.1, "對齊深度圖")
            d = D.refine_to(depth, full_rgb)
            amap = D.blur_amount_map(d, lo, hi, feather, bg_only)
            out = D.render(full_rgb, amap, max_radius(fnum, max(full_rgb.shape[:2])), blades=blades, bokeh=bokeh,
                           on_progress=lambda k, n: progress(0.25 + 0.6 * k / n, f'模糊第 {k}/{n} 層'))
            return rgb_to_qimage(out)

        return job
