"""景深模糊的計算：估深度、畫模糊。只用 numpy + Pillow。

有下載 AI 深度模型時（model.py）深度由模型給；沒有的話用這裡的快速估計 ——
它只是個退路：單張照片的清晰度說不出誰近誰遠，所以結果偏向「下面近、上面遠」的平滑漸層。

估深度（輕量版，沒有神經網路）：
  1. 失焦量：照片裡本來就糊的地方，多半離對焦面比較遠。用「再模糊一次、看邊緣的
     梯度掉多少」估每條邊的失焦半徑（Zhuo & Sim 2011），邊緣掉得少 = 本來就糊。
  2. 擴散：只有邊緣上有估計值，用正規化卷積鋪滿整張，再用引導濾波（guided filter）
     讓深度的邊界貼齊照片裡物體的輪廓。
  3. 先驗：畫面上方通常比較遠、主體通常在中間。整張都很清楚（手機照、風景）時，
     失焦量看不出遠近，就改由先驗決定。
  結果是 0（近／清楚）到 1（遠／模糊）的一張圖。

畫模糊：把照片模糊成幾層（0、1/6、2/6 … 最大半徑），每個像素依它跟對焦範圍的距離
在相鄰兩層之間內插。模糊時用正規化卷積把「清楚的主體」排除在外，背景糊開的時候
才不會把主體的顏色暈出去。大半徑的那幾層先縮小再模糊，速度跟半徑幾乎無關。
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

DEPTH_EDGE = 512   # 估深度用的長邊


# ============================================================ 基本濾波
def box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """沿一軸的平均濾波（邊緣複製），用累加和做，跟半徑無關。"""
    if r < 1:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def box2(a, r):
    return box(box(a, r, 0), r, 1)


def gauss(a, sigma):
    """三次方框模糊 ≈ 高斯。"""
    r = max(1, int(round(sigma * 0.94)))
    for _ in range(3):
        a = box2(a, r)
    return a


def guided(I, p, r, eps):
    """引導濾波：輸出跟著 I 的邊緣走、內容來自 p。"""
    mI, mp = box2(I, r), box2(p, r)
    cov = box2(I * p, r) - mI * mp
    var = box2(I * I, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return box2(a, r) * I + box2(b, r)


def normalize(a, lo_pct=2, hi_pct=98):
    lo, hi = np.percentile(a, [lo_pct, hi_pct])
    if hi - lo < 1e-6:
        return np.zeros_like(a)
    return np.clip((a - lo) / (hi - lo), 0, 1).astype(np.float32)


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.float32) / 255


def resize_f(a: np.ndarray, w: int, h: int) -> np.ndarray:
    return np.asarray(Image.fromarray(a.astype(np.float32)).resize((w, h), Image.Resampling.BILINEAR))


# ============================================================ 估深度
def estimate_depth(rgb: np.ndarray) -> np.ndarray:
    """rgb: HxWx3 uint8（任意大小）→ 同大小的深度 0..1。"""
    H, W = rgb.shape[:2]
    k = min(1.0, DEPTH_EDGE / max(H, W))
    sw, sh = max(8, round(W * k)), max(8, round(H * k))
    small = np.asarray(Image.fromarray(rgb).resize((sw, sh), Image.Resampling.BILINEAR))
    g = gauss(to_gray(small), 0.6)

    # 1. 邊緣上的失焦半徑
    sigma0 = 1.0
    g2 = gauss(g, sigma0)

    def grad(a):
        gx = np.zeros_like(a)
        gy = np.zeros_like(a)
        gx[:, 1:-1] = (a[:, 2:] - a[:, :-2]) * 0.5
        gy[1:-1, :] = (a[2:, :] - a[:-2, :]) * 0.5
        return np.hypot(gx, gy)

    m1, m2 = grad(g), grad(g2)
    thresh = max(0.015, float(np.percentile(m1, 82)))
    edge = (m1 > thresh).astype(np.float32)
    ratio = m1 / (m2 + 1e-4)
    sig = sigma0 / np.sqrt(np.maximum(ratio * ratio - 1, 1e-3))
    sig = np.clip(sig, 0, 6).astype(np.float32)

    # 2. 鋪滿：正規化卷積 + 引導濾波
    long_edge = max(sw, sh)
    s = long_edge / 16
    num, den = gauss(sig * edge, s), gauss(edge, s)
    fallback = float(np.median(sig[edge > 0])) if edge.any() else 0.0
    dense = np.where(den > 1e-3, num / np.maximum(den, 1e-6), fallback).astype(np.float32)
    dense = guided(g, gauss(dense, long_edge / 60), max(2, long_edge // 24), 1e-2)
    defocus = normalize(dense)

    # 3. 先驗：越上面越遠、越邊邊越遠
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    top = 1 - yy / max(1, sh - 1)
    center = np.hypot((xx / max(1, sw - 1) - 0.5) * 1.2, (yy / max(1, sh - 1) - 0.55)) / 0.75
    prior = normalize(0.7 * top + 0.3 * np.clip(center, 0, 1), 0, 100)
    # 失焦量本身有沒有對比：整張都一樣清楚時，它說不出遠近。
    spread = float(np.std(defocus))
    wd = float(np.clip((spread - 0.1) / 0.3, 0.1, 0.5))
    depth = wd * defocus + (1 - wd) * prior
    depth = guided(g, depth.astype(np.float32), max(2, long_edge // 40), 4e-3)
    depth = normalize(gauss(depth, 1.0), 1, 99)
    return resize_f(depth, W, H) if (sw, sh) != (W, H) else depth


def refine_to(depth: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """把低解析度的深度放大到 rgb 的大小，再用照片本身當引導把邊界貼齊。"""
    H, W = rgb.shape[:2]
    d = resize_f(depth, W, H) if depth.shape != (H, W) else depth
    r = max(2, max(H, W) // 300)
    return np.clip(guided(to_gray(rgb), d, r, 1e-3), 0, 1).astype(np.float32)


def histogram(depth: np.ndarray, bins=64) -> np.ndarray:
    h, _ = np.histogram(depth, bins=bins, range=(0, 1))
    h = h.astype(np.float32)
    return h / h.max() if h.max() > 0 else h


def focus_at(depth: np.ndarray, x: float, y: float) -> float:
    """某一點（0..1 座標）附近的深度中位數：點在邊緣上也不會跳。"""
    H, W = depth.shape
    cx, cy = int(x * (W - 1)), int(y * (H - 1))
    r = max(2, max(H, W) // 80)
    patch = depth[max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1]
    return float(np.median(patch)) if patch.size else float(depth[cy, cx])


# ============================================================ 畫模糊
NEAR_GAIN = 1.3     # 對焦面前面的東西糊得比後面快（真的鏡頭也是這樣）


def blur_amount_map(depth, lo, hi, feather, bg_only):
    """每個像素要糊多少（0..1）—— 模擬鏡頭的模糊圈（circle of confusion）。

    對焦範圍內是清楚的；範圍外的模糊量跟「離對焦面多遠」成正比：
    稍微在後面一點只糊一點點，越遠越糊，最遠的地方才到最大模糊量。
    前景離鏡頭近，同樣的深度差糊得更多。feather 決定剛離開對焦範圍時多快開始糊。
    """
    far = np.maximum(depth - hi, 0)
    coc = far / max(1.0 - hi, 0.12)                      # 最遠處 = 1
    if not bg_only:
        near = np.maximum(lo - depth, 0)
        coc = np.maximum(coc, near * NEAR_GAIN / max(lo, 0.12))
        dist = np.maximum(far, near)
    else:
        dist = far
    coc = np.clip(coc, 0, 1)
    # 對焦範圍的邊緣柔一點：剛出範圍的地方不會突然跳一階
    t = np.clip(dist / max(feather, 0.01), 0, 1)
    onset = t * t * (3 - 2 * t)
    return (coc * onset).astype(np.float32)


def _blur_level(img: Image.Image, radius: float) -> Image.Image:
    """半徑大的先縮小再模糊，再放回來 —— 反正都糊了，看不出差別，速度快很多。"""
    if radius < 0.5:
        return img
    f = max(1, int(radius / 5))
    if f > 1:
        small = img.resize((max(1, img.width // f), max(1, img.height // f)), Image.Resampling.BILINEAR)
        return small.filter(ImageFilter.GaussianBlur(radius / f)).resize(img.size, Image.Resampling.BILINEAR)
    return img.filter(ImageFilter.GaussianBlur(radius))


def render(rgb: np.ndarray, amount_map: np.ndarray, max_radius: float, levels=8, cancel=None,
           on_progress=None) -> np.ndarray:
    """rgb: HxWx3 uint8；amount_map: HxW 0..1；回傳 uint8。"""
    if max_radius < 0.5 or float(amount_map.max()) < 1e-3:
        return rgb
    arr = rgb.astype(np.float32)
    # 正規化卷積的權重：清楚的主體幾乎不參與模糊，背景糊開時才不會把主體的顏色帶出去。
    w = (0.04 + 0.96 * amount_map).astype(np.float32)
    pre = Image.fromarray(np.clip(arr * w[..., None], 0, 255).astype(np.uint8))
    wimg = Image.fromarray(np.clip(w * 255, 0, 255).astype(np.uint8))
    t = amount_map * levels
    out = arr * np.clip(1 - t, 0, 1)[..., None]
    for k in range(1, levels + 1):
        if cancel and cancel():
            return rgb
        if on_progress:
            on_progress(k, levels)
        hat = np.clip(1 - np.abs(t - k), 0, 1)
        if not hat.any():
            continue
        r = max_radius * k / levels
        num = np.asarray(_blur_level(pre, r), dtype=np.float32)
        den = np.asarray(_blur_level(wimg, r), dtype=np.float32)[..., None] / 255
        level = num / np.maximum(den, 1e-3)
        out += level * hat[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def colorize(depth: np.ndarray, lo=None, hi=None) -> np.ndarray:
    """深度圖的顯示：近 = 亮黃、遠 = 深藍紫；對焦範圍內的保持原色，範圍外壓暗一點。"""
    stops = np.array([[255, 224, 130], [240, 120, 90], [170, 60, 140], [70, 40, 130], [20, 20, 60]], np.float32)
    x = depth * (len(stops) - 1)
    i = np.clip(x.astype(int), 0, len(stops) - 2)
    f = (x - i)[..., None]
    rgb = stops[i] * (1 - f) + stops[i + 1] * f
    if lo is not None:
        inside = (depth >= lo) & (depth <= hi)
        rgb = np.where(inside[..., None], rgb, rgb * 0.45)
    return rgb.astype(np.uint8)
