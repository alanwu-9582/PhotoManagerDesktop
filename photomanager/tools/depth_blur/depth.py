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

畫模糊（模擬鏡頭）：在線性光裡用光圈形狀的核心（圓形或多邊形）把照片糊成幾層，
每個像素依它的模糊圈（跟對焦面的深度差）在相鄰兩層之間內插。接近死白的亮點先推亮，
糊開才會變成一顆顆明亮、邊緣清楚的散景。模糊時用正規化卷積把「清楚的主體」排除在外，
背景糊開的時候才不會把主體的顏色暈出去。大半徑的那幾層先縮小再用 FFT 卷積，速度跟半徑幾乎無關。
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

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


# ---------------------------------------------------------------- 鏡頭的模糊
# 跟高斯模糊不一樣的地方：
#   1. 在線性光裡混光（真實的光是相加的），不是在 sRGB 的數值上平均。
#   2. 核心是光圈的形狀（圓形或多邊形，邊緣清楚），所以一個亮點會散成一顆邊緣清楚的圓盤 / 多邊形。
#   3. 亮點的能量比畫面上記錄到的還大（早就過曝了），先把接近死白的地方推亮，糊開之後才會是
#      一顆顆明亮的散景，而不是灰灰的一片。
LEVEL_KERNEL = 14       # 每一層縮小到光圈半徑大約這麼多像素再算：預覽和原尺寸的散景邊緣一樣利


def aperture_kernel(r: float, blades: int = 0, rotation: float = 0.3) -> np.ndarray:
    """半徑 r 的光圈形狀（加總 = 1）。blades = 0 是圓形，6、9… 是幾片葉片圍出的多邊形，
    -1 是圓環（反射式鏡頭中間有一面鏡子擋住，散景會是甜甜圈形狀）。"""
    n = max(1, int(np.ceil(r)))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1].astype(np.float32)
    dist = np.hypot(xx, yy)
    if blades >= 3:
        seg = 2 * np.pi / blades
        th = (np.arctan2(yy, xx) - rotation) % seg - seg / 2
        dist = dist * np.cos(th) / np.cos(seg / 2)        # 多邊形的「距離」：邊上 = r
    k = np.clip(r + 0.5 - dist, 0, 1)                     # 邊緣抗鋸齒一個像素
    if blades == -1:
        k = k - np.clip(r * 0.55 + 0.5 - dist, 0, 1)      # 挖掉中間 55%
    # 真的鏡頭散景邊緣稍微亮一點（球面像差），加一點點就好
    k = k * (1 + 0.18 * np.clip((dist / max(r, 1)) ** 4, 0, 1))
    return (k / k.sum()).astype(np.float32)


def _good(n):
    """FFT 好算的長度（只有 2、3、5 的因數）。"""
    while True:
        m = n
        for f in (2, 3, 5):
            while m % f == 0:
                m //= f
        if m == 1:
            return n
        n += 1


def _conv_fft(chs, K):
    """幾張同大小的 2D 圖一起跟 K 做卷積（邊緣複製）。"""
    n = K.shape[0] // 2
    h, w = chs[0].shape
    FH, FW = _good(h + 4 * n), _good(w + 4 * n)
    Kp = np.zeros((FH, FW), np.float32)
    Kp[:K.shape[0], :K.shape[1]] = K
    Kf = np.fft.rfft2(Kp)
    out = []
    for a in chs:
        ap = np.pad(a, n, mode="edge")
        r = np.fft.irfft2(np.fft.rfft2(ap, s=(FH, FW)) * Kf, s=(FH, FW))
        out.append(r[2 * n:2 * n + h, 2 * n:2 * n + w].astype(np.float32))
    return out


def _resize_f(a, w, h):
    return np.asarray(Image.fromarray(np.ascontiguousarray(a, dtype=np.float32), "F").resize(
        (w, h), Image.Resampling.BILINEAR), dtype=np.float32)


def render(rgb: np.ndarray, amount_map: np.ndarray, max_radius: float, levels=8, cancel=None,
           on_progress=None, blades=0, bokeh=0.4) -> np.ndarray:
    """rgb: HxWx3 uint8；amount_map: HxW 0..1（每個像素的模糊圈 / 最大模糊圈）；回傳 uint8。

    把照片在線性光裡糊成幾層（光圈半徑 1/levels、2/levels … 1 倍），
    每個像素依自己的模糊圈在相鄰兩層之間內插。
    """
    if max_radius < 0.5 or float(amount_map.max()) < 1e-3:
        return rgb
    H, W = rgb.shape[:2]
    lin = np.power(rgb.astype(np.float32) / 255, 2.2, dtype=np.float32)
    w = (0.04 + 0.96 * amount_map).astype(np.float32)        # 清楚的主體幾乎不參與模糊，顏色才不會暈出去
    peak = lin.max(axis=2)
    # 接近死白的「點光源」真正的亮度可能是記錄值的好幾十倍，推亮它們，糊開才會是一顆顆散景。
    # 只推比周圍亮很多的小亮點；天空那種大片的亮區不推，不然會整片暈開蓋住旁邊的東西。
    sh, sw = max(4, round(H / 8)), max(4, round(W / 8))
    local = _resize_f(gauss(_resize_f(peak, sw, sh), max(sw, sh) / 40), W, H)
    spec = np.clip((peak - local) / 0.3, 0, 1)
    boost = (1 + bokeh * 30 * (np.clip((peak - 0.7) / 0.3, 0, 1) ** 3) * spec).astype(np.float32)
    del local, spec
    src = [lin[..., c] * boost * w for c in range(3)]
    del boost, peak
    t = amount_map * levels
    lin *= np.clip(1 - t, 0, 1)[..., None]
    out = lin
    lock = threading.Lock()
    done = [0]

    def level(k):
        """第 k 層：縮小、跟光圈形狀做卷積、放大回來，加到 out（每層互相獨立，可以同時算）。"""
        if cancel and cancel():
            return
        hat = np.clip(1 - np.abs(t - k), 0, 1)
        if hat.any():
            r = max_radius * k / levels
            f = max(1.0, r / LEVEL_KERNEL)
            sw, sh = max(4, round(W / f)), max(4, round(H / f))
            small = [_resize_f(a, sw, sh) for a in src] + [_resize_f(w, sw, sh)]
            conv = _conv_fft(small, aperture_kernel(max(0.8, r / f), blades))
            scale = hat / np.maximum(_resize_f(conv[3], W, H), 1e-4)
            layers = [_resize_f(conv[c], W, H) * scale for c in range(3)]
            with lock:
                for c in range(3):
                    out[..., c] += layers[c]
        with lock:
            done[0] += 1
            n = done[0]
        if on_progress:
            on_progress(n, levels)

    # FFT 和縮放都會放掉 GIL，幾層一起算；同時最多幾層，原尺寸輸出時記憶體才不會爆
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(level, range(1, levels + 1)))
    if cancel and cancel():
        return rgb
    # 推亮過的散景超過 1 的部分直接夾掉：亮點會是一顆實心、邊緣清楚的圓
    return np.clip(np.power(np.clip(out, 0, 1), 1 / 2.2) * 255 + 0.5, 0, 255).astype(np.uint8)


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
