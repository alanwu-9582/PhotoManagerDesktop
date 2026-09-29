"""從一張照片挑出幾個代表色（對應網頁版 js/tools/palette-card/palette.js）。

中位切分（median cut）：把所有像素當成 RGB 空間裡的一團點，反覆挑「最胖」的那一盒
沿最長邊切一半，每盒取平均色。比 k-means 穩定（同一張圖永遠同一組色）。

兩個後處理：
  對齊   每個通道取 16 的倍數，色碼永遠長成 #B06040 這種整齊的樣子（可選）
  去重   用 redmean 距離把太像的併掉，再從後面補一個進來
這裡用 numpy 算，幾萬個取樣點也只要幾毫秒。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

MAX_SAMPLES = 24000
MERGE_DISTANCE = 48
SNAP_STEP = 16


def to_hex(rgb):
    return "#" + "".join(f"{int(round(v)):02X}" for v in rgb)


def snap_rgb(rgb):
    return [min(15, round(v / SNAP_STEP)) * SNAP_STEP for v in rgb]


def distance(a, b):
    """redmean 色差：便宜、而且比直接算 RGB 歐氏距離貼近人眼。"""
    rmean = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return ((2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db) ** 0.5


def _dist_many(pixels: np.ndarray, c) -> np.ndarray:
    rmean = (pixels[:, 0] + c[0]) / 2
    d = pixels - np.asarray(c, dtype=np.float32)
    return (2 + rmean / 256) * d[:, 0] ** 2 + 4 * d[:, 1] ** 2 + (2 + (255 - rmean) / 256) * d[:, 2] ** 2


def luminance(rgb):
    def lin(v):
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def shade(rgb, amount):
    target = 255 if amount >= 0 else 0
    k = abs(amount)
    return [round(v + (target - v) * k) for v in rgb]


def sample_pixels(img: QImage) -> np.ndarray:
    """縮圖的像素（不做平滑 —— 邊界內插會生出原圖裡根本沒有的顏色）。"""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())
    arr = arr[:, : w * 4].reshape(h * w, 4)
    total = w * h
    stride = max(1, total // MAX_SAMPLES)
    step = stride + 1 if stride % 2 == 0 else stride   # 奇數步長，避免踩在圖案的週期上
    px = arr[::step]
    px = px[px[:, 3] >= 128]
    return px[:, :3].astype(np.float32)


def _median_cut(pixels: np.ndarray, wanted: int):
    boxes = [pixels]

    def info(b):
        if len(b) == 0:
            return 0, 0
        span = b.max(axis=0) - b.min(axis=0)
        axis = int(np.argmax(span))
        return axis, float(span[axis])

    meta = [info(boxes[0])]
    while len(boxes) < wanted:
        best, best_score = -1, 0.0
        for i, b in enumerate(boxes):
            axis, span = meta[i]
            if len(b) < 2 or span == 0:
                continue
            score = span * len(b)   # 又大又雜的地方才值得切
            if score > best_score:
                best, best_score = i, score
        if best < 0:
            break
        b = boxes[best]
        axis = meta[best][0]
        order = np.argsort(b[:, axis], kind="stable")
        s = b[order]
        half = len(s) >> 1
        parts = [s[:half], s[half:]]
        boxes[best:best + 1] = parts
        meta[best:best + 1] = [info(p) for p in parts]
    return boxes


def extract_palette(img: QImage, count=6, snap=True):
    pixels = sample_pixels(img)
    if not len(pixels):
        return []
    candidates = sorted(
        ({"rgb": [int(round(v)) for v in b.mean(axis=0)], "size": len(b)} for b in _median_cut(pixels, count * 4) if len(b)),
        key=lambda c: -c["size"])

    def weigh(picked):
        """把所有取樣像素各自歸給最接近的選色再數一次，得到的才是真的占比。"""
        d = np.stack([_dist_many(pixels, c["rgb"]) for c in picked], axis=1)
        counts = np.bincount(np.argmin(d, axis=1), minlength=len(picked))
        out = [{**c, "weight": counts[i] / len(pixels)} for i, c in enumerate(picked)]
        return sorted(out, key=lambda c: -c["weight"])

    # 門檻逐步放寬：先求每個都夠不一樣，真的湊不滿再退讓。
    for threshold in (MERGE_DISTANCE, MERGE_DISTANCE / 2, MERGE_DISTANCE / 4, 0):
        picked = []
        for c in candidates:
            rgb = snap_rgb(c["rgb"]) if snap else c["rgb"]
            if any(distance(p["rgb"], rgb) <= threshold for p in picked):
                continue
            picked.append({"rgb": rgb, "hex": to_hex(rgb)})
            if len(picked) == count:
                return weigh(picked)
        if threshold == 0:
            return weigh(picked) if picked else []
    return []
