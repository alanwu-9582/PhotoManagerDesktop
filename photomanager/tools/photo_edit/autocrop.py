"""自動拉直與自動裁切。

detect_tilt：畫面裡夠強的直線（地平線、建築的水平 / 垂直線），統計它們偏離水平、垂直多少度，
             取最多票的那個角度。線不夠多、意見太分散就回傳 0（不亂轉）。
compose：    在「轉正後不含留白」的範圍裡，依指定比例挑一個裁切框：
             框住主體（留一點邊）、主體的重心放在最近的三分線交點（或正中間），盡量保留畫面。
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from ..depth_blur.depth import gauss

TILT_EDGE = 800
MAX_TILT = 12.0


def detect_tilt(rgb: np.ndarray, max_deg=MAX_TILT) -> float:
    """回傳線條偏離水平的角度（度，畫面座標，正的 = 往右下斜）；要拉直就旋轉 -角度。"""
    H, W = rgb.shape[:2]
    k = min(1.0, TILT_EDGE / max(H, W))
    g = np.asarray(Image.fromarray(rgb).convert("L").resize((max(16, round(W * k)), max(16, round(H * k)))),
                   np.float32) / 255
    g = gauss(g, 1.0)
    gy, gx = np.gradient(g)
    mag = np.hypot(gx, gy)
    b = 6                                         # 邊框附近的梯度不可靠（縮圖的邊緣）
    mag[:b], mag[-b:], mag[:, :b], mag[:, -b:] = 0, 0, 0, 0
    thr = np.percentile(mag, 92)
    sel = mag > max(thr, 0.02)
    if sel.sum() < 200:
        return 0.0
    # 線的方向 = 梯度方向轉 90°；換算成偏離水平 / 垂直的角度
    line = (np.degrees(np.arctan2(gy[sel], gx[sel])) + 90.0) % 180.0          # 0..180
    dev_h = (line + 90.0) % 180.0 - 90.0                                       # 偏離水平
    dev_v = (line % 180.0) - 90.0                                              # 偏離垂直
    w = mag[sel]
    devs = np.concatenate([dev_h[np.abs(dev_h) <= max_deg], dev_v[np.abs(dev_v) <= max_deg]])
    ws = np.concatenate([w[np.abs(dev_h) <= max_deg], w[np.abs(dev_v) <= max_deg]])
    if devs.size < 100:
        return 0.0
    bins = np.arange(-max_deg, max_deg + 0.1, 0.1)
    hist, _ = np.histogram(devs, bins=bins, weights=ws)
    kernel = np.exp(-0.5 * (np.arange(-12, 13) / 4.0) ** 2)
    smooth = np.convolve(hist, kernel / kernel.sum(), mode="same")
    i = int(np.argmax(smooth))
    peak = smooth[i]
    # 峰要明顯比平均高才算數，不然是一張沒有明確線條的照片
    if peak < smooth.mean() * 3.0:
        return 0.0
    angle = (bins[i] + bins[i + 1]) / 2
    # 精修：把邊緣點沿各個候選角度投影，真正的角度會讓同一條線上的點疊在一起（直方圖最尖）
    ys_, xs_ = np.nonzero(sel)
    wv = mag[sel]
    is_h = np.abs(dev_h - angle) <= 2.0
    is_v = np.abs(dev_v - angle) <= 2.0

    def score(a):
        r = math.radians(a)
        c, s_ = math.cos(r), math.sin(r)
        total = 0.0
        if is_h.any():
            proj = (ys_[is_h] * c - xs_[is_h] * s_)
            h_ = np.bincount((proj - proj.min()).astype(np.int64), weights=wv[is_h])
            total += float((h_ * h_).sum())
        if is_v.any():
            proj = (xs_[is_v] * c + ys_[is_v] * s_)
            h_ = np.bincount((proj - proj.min()).astype(np.int64), weights=wv[is_v])
            total += float((h_ * h_).sum())
        return total

    cands = np.arange(angle - 2.0, angle + 2.01, 0.25)
    best = max(cands, key=score)
    fine = np.arange(best - 0.3, best + 0.31, 0.05)
    angle = float(max(fine, key=score))
    if abs(angle) > max_deg:
        return 0.0
    return float(round(angle, 1))


def compose(subject: np.ndarray | None, inner: dict, aspect: float, W: int, H: int) -> dict:
    """subject：工作畫布大小的主體遮罩（0..1）或 None；inner：不含留白的範圍（0..1）；aspect：寬 / 高。
    回傳裁切框（0..1，跟 PhotoEditPage.crop 同格式）。"""
    ix, iy, iw, ih = inner["x"] * W, inner["y"] * H, inner["w"] * W, inner["h"] * H
    # 最大的框
    mw, mh = (ih * aspect, ih) if iw / ih > aspect else (iw, iw / aspect)
    if subject is None or float((subject > 0.5).mean()) < 0.01:
        cx, cy = ix + iw / 2, iy + ih / 2
        return _norm(cx - mw / 2, cy - mh / 2, mw, mh, W, H)
    m = subject[int(iy):int(iy + ih), int(ix):int(ix + iw)]
    ys, xs = np.nonzero(m > 0.5)
    wts = m[ys, xs] ** 2
    gx = ix + float((xs * wts).sum() / wts.sum())
    gy = iy + float((ys * wts).sum() / wts.sum())
    # 主體的外框（去掉最外圍 2% 的雜點），四周留 12%
    x0, x1 = ix + np.percentile(xs, 2), ix + np.percentile(xs, 98)
    y0, y1 = iy + np.percentile(ys, 2), iy + np.percentile(ys, 98)
    need_w, need_h = (x1 - x0) * 1.24, (y1 - y0) * 1.24
    scale = max(need_w / mw, need_h / mh, 0.72)       # 至少保留最大框的 72%，不要裁得太緊
    scale = min(scale, 1.0)
    cw, ch = mw * scale, mh * scale
    # 重心放到最近的三分點（或正中間）
    best = None
    for fx in (1 / 3, 1 / 2, 2 / 3):
        for fy in (1 / 3, 1 / 2, 2 / 3):
            if (fx == 1 / 2) != (fy == 1 / 2):
                continue                         # 只用四個三分點和正中間
            x = gx - fx * cw
            y = gy - fy * ch
            x = min(max(x, ix), ix + iw - cw)
            y = min(max(y, iy), iy + ih - ch)
            # 主體不能被切到
            x = min(max(x, x1 + (need_w - (x1 - x0)) / 2 - cw), x0 - (need_w - (x1 - x0)) / 2) if cw >= need_w else x
            y = min(max(y, y1 + (need_h - (y1 - y0)) / 2 - ch), y0 - (need_h - (y1 - y0)) / 2) if ch >= need_h else y
            x = min(max(x, ix), ix + iw - cw)
            y = min(max(y, iy), iy + ih - ch)
            err = math.hypot(x + fx * cw - gx, y + fy * ch - gy)
            if best is None or err < best[0] - 1e-6:
                best = (err, x, y)
    _, x, y = best
    return _norm(x, y, cw, ch, W, H)


def _norm(x, y, w, h, W, H):
    return {"x": x / W, "y": y / H, "w": w / W, "h": h / H}
