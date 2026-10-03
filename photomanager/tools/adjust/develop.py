"""調整（參考 Lightroom 的「基本」「色彩混合」「效果」面板）的計算，只用 numpy。

順序跟 Lightroom 差不多：
  線性光：白平衡 → 曝光 → 聚光燈（區域曝光 + 周圍壓暗）
  感知亮度：去朦朧 → 攝影風格（styles.py）→ 亮部 / 陰影 / 白色 / 黑色 → 對比 → 清晰度 / 紋理
  顏色：自然飽和度 / 飽和度 → 色彩混合（HSL）→ 顏色分級
  最後：暈影 → 顆粒

會看「附近」的調整（亮部 / 陰影的區域亮度、清晰度、去朦朧）先在長邊 MAP_EDGE 的縮圖上
算成一張平滑的圖，再內插到原尺寸；所以預覽（長邊一千多）和輸出（原尺寸）看起來一樣，
原尺寸也可以一段一段（STRIP 列）處理，兩千萬畫素也不會吃掉好幾 GB 記憶體。
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageFilter

from ..depth_blur.depth import gauss, guided
from . import styles as ST

MAP_EDGE = 640
STRIP = 384

# 色彩混合的八個色相（度）
BANDS = [("red", 0), ("orange", 30), ("yellow", 60), ("green", 120), ("aqua", 180), ("blue", 240),
         ("purple", 270), ("magenta", 300)]

DEFAULTS = {
    "temp": 0.0, "tint": 0.0,
    "exposure": 0.0, "contrast": 0.0, "highlights": 0.0, "shadows": 0.0, "whites": 0.0, "blacks": 0.0,
    "texture": 0.0, "clarity": 0.0, "dehaze": 0.0, "vibrance": 0.0, "saturation": 0.0,
    "hsl": {b: [0.0, 0.0, 0.0] for b, _ in BANDS},           # 色相、飽和度、明亮度（-100..100）
    "grade": {"sh_hue": 220.0, "sh_sat": 0.0, "hi_hue": 40.0, "hi_sat": 0.0, "balance": 0.0},
    "vignette": 0.0, "vig_mid": 50.0, "vig_feather": 50.0,
    "grain": 0.0, "grain_size": 25.0,
    "spots": [],          # [{shape, cx, cy, rx, ry, angle, ev, feather, warmth}]，cx/cy 是寬高的比例，rx/ry 是長邊的比例
    "spot_dim": 35.0,     # 聚光燈以外壓暗多少
    "style": dict(ST.DEFAULT),   # 攝影風格（iPhone 那種）：名稱、色調、色彩、色盤
}


def defaults():
    import copy
    return copy.deepcopy(DEFAULTS)


def is_identity(p) -> bool:
    d = DEFAULTS
    for k, v in d.items():
        if k in ("hsl", "grade", "spots", "vig_mid", "vig_feather", "grain_size", "spot_dim", "style"):
            continue
        if abs(p[k] - v) > 1e-9:
            return False
    if p["spots"] or not ST.is_identity(p.get("style", ST.DEFAULT)):
        return False
    if any(abs(x) > 1e-9 for vals in p["hsl"].values() for x in vals):
        return False
    return p["grade"]["sh_sat"] == 0 and p["grade"]["hi_sat"] == 0


# ============================================================ 小工具
def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def to_linear(g):
    return np.power(np.maximum(g, 0), 2.2, dtype=np.float32)


def to_gamma(lin):
    return np.power(np.maximum(lin, 0), 1 / 2.2, dtype=np.float32)


def luma(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def soft_clip(x, knee=0.88):
    """超過 knee 的部分用 tanh 壓回 1 以內：提亮的時候亮部是慢慢飽和，不會整片死白。"""
    over = x > knee
    if not over.any():
        return x
    k = 1 - knee
    return np.where(over, knee + k * np.tanh((x - knee) / k), x)


def hue_rgb(hue_deg):
    """色相 → 純色（0..1）。"""
    h = (hue_deg % 360) / 60
    x = 1 - abs(h % 2 - 1)
    return [(1, x, 0), (x, 1, 0), (0, 1, x), (0, x, 1), (x, 0, 1), (1, 0, x)][int(h) % 6]


def resize(a, w, h):
    return np.asarray(Image.fromarray(np.ascontiguousarray(a, dtype=np.float32)).resize((w, h), Image.Resampling.BILINEAR))


def blur8(a, sigma):
    """用 PIL 的 8-bit 高斯模糊（快很多）；量化誤差不到 1/500，紋理這種細節看不出來。"""
    lo, hi = float(a.min()), float(a.max())
    span = max(hi - lo, 1e-6)
    im = Image.fromarray(np.clip((a - lo) / span * 255 + 0.5, 0, 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32) / 255 * span + lo


class Sampler:
    """把縮圖大小的平滑圖內插到原尺寸的某幾列（雙線性）。"""

    def __init__(self, mh, mw, H, W):
        self.mh, self.mw, self.H, self.W = mh, mw, H, W
        x = (np.arange(W, dtype=np.float32) + 0.5) * mw / W - 0.5
        x = np.clip(x, 0, mw - 1)
        self.x0 = np.floor(x).astype(np.int32)
        self.x1 = np.minimum(self.x0 + 1, mw - 1)
        self.wx = (x - self.x0).astype(np.float32)

    def rows(self, m, y0, y1):
        y = (np.arange(y0, y1, dtype=np.float32) + 0.5) * self.mh / self.H - 0.5
        y = np.clip(y, 0, self.mh - 1)
        yi0 = np.floor(y).astype(np.int32)
        yi1 = np.minimum(yi0 + 1, self.mh - 1)
        wy = (y - yi0)[:, None].astype(np.float32)
        a, b = m[yi0], m[yi1]
        top = a[:, self.x0] * (1 - self.wx) + a[:, self.x1] * self.wx
        bot = b[:, self.x0] * (1 - self.wx) + b[:, self.x1] * self.wx
        return top * (1 - wy) + bot * wy


# ============================================================ 線性光的部分（逐像素）
def wb_gains(p):
    t, m = p["temp"] / 100, p["tint"] / 100
    g = np.array([2 ** (0.55 * t), 2 ** (-0.45 * m), 2 ** (-0.55 * t)], np.float32)
    return g / float(g @ np.array([0.2126, 0.7152, 0.0722], np.float32))   # 亮度不變，只換顏色


def spot_distance(shape, u, v):
    """聚光燈形狀的「距離」：邊緣 = 1，裡面 < 1。
    ellipse 橢圓；rect 圓角矩形（超橢圓）；beam 光帶 —— 沿著角度方向無限長的一整條，只看離中線多遠。"""
    if shape == "beam":
        return np.abs(v)
    if shape == "rect":
        return np.power(np.abs(u) ** 6 + np.abs(v) ** 6, 1 / 6)
    return np.sqrt(u * u + v * v)


def spot_masks(p, xs, ys, W, H):
    """每一個聚光燈的遮罩（0..1）。xs: (W,) 像素中心的 x，ys: (h,) 這幾列的 y。"""
    out = []
    edge = max(W, H)
    for s in p["spots"]:
        cx, cy = s["cx"] * W, s["cy"] * H
        rx, ry = max(1.0, s["rx"] * edge), max(1.0, s["ry"] * edge)
        a = math.radians(s.get("angle", 0.0))
        ca, sa = math.cos(a), math.sin(a)
        dx = xs[None, :] - cx
        dy = ys[:, None] - cy
        u = (dx * ca + dy * sa) / rx
        v = (-dx * sa + dy * ca) / ry
        d = spot_distance(s.get("shape", "ellipse"), u, v)
        f = max(0.02, s.get("feather", 50) / 100)
        out.append((s, 1 - smoothstep(1 - f, 1 + f * 0.25, d)))
    return out


def stage_linear(g, p, xs, ys, W, H):
    """g: 0..1 的 sRGB（h×W×3）→ 線性光，做完白平衡、曝光、聚光燈。"""
    lin = to_linear(g)
    if p["temp"] or p["tint"]:
        lin *= wb_gains(p)
    if p["exposure"]:
        lin *= np.float32(2 ** p["exposure"])
    if p["spots"]:
        union = np.zeros(lin.shape[:2], np.float32)
        for s, m in spot_masks(p, xs, ys, W, H):
            if s.get("ev"):
                lin *= np.power(2.0, s["ev"] * m, dtype=np.float32)[..., None]
            if s.get("warmth"):
                w = s["warmth"] / 100 * m
                lin[..., 0] *= np.power(2.0, 0.5 * w, dtype=np.float32)
                lin[..., 2] *= np.power(2.0, -0.5 * w, dtype=np.float32)
            union = np.maximum(union, m)
        if p["spot_dim"]:
            lin *= np.power(2.0, -2.6 * p["spot_dim"] / 100 * (1 - union), dtype=np.float32)[..., None]
    return lin


# ============================================================ 縮圖上算的平滑圖
def _min_filter(a, r):
    """方形最小值濾波（侵蝕），r 很小所以直接位移比大小。"""
    out = a.copy()
    p = np.pad(a, r, mode="edge")
    h, w = a.shape
    for dy in range(-r, r + 1):
        out = np.minimum(out, p[r + dy:r + dy + h, r:r + w])
    p = np.pad(out, r, mode="edge")
    res = out.copy()
    for dx in range(-r, r + 1):
        res = np.minimum(res, p[r:r + h, r + dx:r + dx + w])
    return res


def prepare(rgb_u8: np.ndarray, p) -> dict:
    """在長邊 MAP_EDGE 的縮圖上算「附近」類的調整要用的平滑圖。"""
    H, W = rgb_u8.shape[:2]
    k = min(1.0, MAP_EDGE / max(H, W))
    mw, mh = max(8, round(W * k)), max(8, round(H * k))
    small = np.asarray(Image.fromarray(rgb_u8).resize((mw, mh), Image.Resampling.BILINEAR)).astype(np.float32) / 255
    xs = (np.arange(mw, dtype=np.float32) + 0.5) * W / mw
    ys = (np.arange(mh, dtype=np.float32) + 0.5) * H / mh
    g = to_gamma(stage_linear(small, p, xs, ys, W, H))
    maps = {"shape": (mh, mw)}
    if p["dehaze"]:
        # 暗通道先驗（He et al. 2009）：霧越濃，三個色版裡最暗的那個越亮。
        dark = _min_filter(g.min(axis=2), max(1, round(max(mw, mh) / 70)))
        flat = dark.reshape(-1)
        n = max(1, flat.size // 1000)
        idx = np.argpartition(flat, -n)[-n:]
        A = np.clip(g.reshape(-1, 3)[idx].mean(axis=0), 0.35, 1.0).astype(np.float32)
        dn = _min_filter((g / A).min(axis=2), max(1, round(max(mw, mh) / 70)))
        gray = luma(g)
        t = guided(gray, (1 - dn).astype(np.float32), max(2, round(max(mw, mh) / 40)), 1e-3)
        maps["A"] = A
        maps["t"] = np.clip(t, 0.05, 1).astype(np.float32)
        g = _dehaze(g, p["dehaze"] / 100, A, maps["t"])
    g = ST.apply(g, p.get("style", ST.DEFAULT))
    L = np.clip(luma(g), 0, 1).astype(np.float32)
    edge = max(mw, mh)
    # 亮部 / 陰影看的是「這一帶」的亮度，用保邊的引導濾波，邊界才不會長出光暈。
    maps["base"] = np.clip(guided(L, L, max(2, round(edge / 30)), 0.02), 0, 1).astype(np.float32)
    if p["clarity"]:
        maps["band"] = (gauss(L, edge / 400) - gauss(L, edge / 70)).astype(np.float32)
    return maps


def _dehaze(g, amt, A, t):
    if amt > 0:
        tt = 1 - amt * 0.95 * (1 - t)
        out = (g - A) / np.maximum(tt, 0.1)[..., None] + A
        # 去霧會把整張拉暗一點，補回一半的平均亮度
        return np.clip(out, 0, 1.2)
    a = -amt * 0.7
    return g + (A - g) * (a * (0.35 + 0.65 * (1 - t)))[..., None]


# ============================================================ 一段（幾列）的處理
def _tone(L, base, p):
    if p["shadows"]:
        w = 1 - smoothstep(0.0, 0.55, base)
        L = L * np.power(2.0, p["shadows"] / 100 * 1.25 * w, dtype=np.float32)
    if p["highlights"]:
        w = smoothstep(0.4, 1.0, base)
        L = L * np.power(2.0, p["highlights"] / 100 * 0.85 * w, dtype=np.float32)
    if p["whites"]:
        L = L + p["whites"] / 100 * 0.35 * np.clip(L, 0, 1.2) ** 3
    if p["blacks"]:
        L = L + p["blacks"] / 100 * 0.18 * np.clip(1 - L, 0, 1) ** 3
    if p["contrast"]:
        k = max(-0.8, p["contrast"] / 100 * 1.1)
        piv = 0.46
        x = np.clip(L, 0, 1)
        lo = piv * np.power(x / piv, 1 + k)
        hi = 1 - (1 - piv) * np.power(np.clip((1 - x) / (1 - piv), 0, None), 1 + k)
        L = np.where(x < piv, lo, hi) + (L - x)
    return L


def _hue(rgb):
    """色相（度）：用對立色空間的角度，跟 HSV 的色相幾乎一樣，但只要一次 arctan2。"""
    a = rgb[..., 0] - 0.5 * (rgb[..., 1] + rgb[..., 2])
    b = 0.8660254 * (rgb[..., 1] - rgb[..., 2])
    return np.degrees(np.arctan2(b, a)) % 360


_HUE_X = np.array([c for _, c in BANDS] + [360], np.float32)


def _band_interp(h, vals):
    """八個色相各自的設定值，依像素色相在相鄰兩個之間線性內插。"""
    return np.interp(h, _HUE_X, np.array(list(vals) + [vals[0]], np.float32)).astype(np.float32)


def _rotate_hue(g, L, deg):
    """繞著灰軸轉色相（Rodrigues），亮度不變。deg 可以是每個像素不同。"""
    th = np.radians(deg)
    c, s = np.cos(th), np.sin(th)
    v = g - L[..., None]
    k = 0.57735027
    kxv = np.stack([k * (v[..., 2] - v[..., 1]), k * (v[..., 0] - v[..., 2]), k * (v[..., 1] - v[..., 0])], -1)
    kdv = (k * v.sum(axis=2))[..., None]
    out = v * c[..., None] + kxv * s[..., None] + k * kdv * (1 - c)[..., None]
    return out + L[..., None]


def _color(g, L, p):
    if p["vibrance"] or p["saturation"]:
        sat = g.max(axis=2) - g.min(axis=2)
        f = 1 + p["saturation"] / 100
        if p["vibrance"]:
            v = p["vibrance"] / 100
            f = f + (v * np.power(1 - np.clip(sat, 0, 1), 2.0) if v > 0 else v)
        f = np.maximum(f, 0)
        g = L[..., None] + (g - L[..., None]) * (f[..., None] if np.ndim(f) else f)
    hsl = p["hsl"]
    if any(abs(x) > 1e-9 for vals in hsl.values() for x in vals):
        names = [n for n, _ in BANDS]
        h = _hue(g)
        mx, mn = g.max(axis=2), g.min(axis=2)
        chroma = np.clip((mx - mn) / np.maximum(mx, 1e-3), 0, 1)      # 越灰的像素受影響越少
        dh = [hsl[n][0] / 100 * 30 for n in names]
        ds = [hsl[n][1] / 100 for n in names]
        dl = [hsl[n][2] / 100 for n in names]
        if any(dh):
            g = _rotate_hue(g, luma(g), _band_interp(h, dh) * chroma)
        if any(ds):
            Lg = luma(g)[..., None]
            g = Lg + (g - Lg) * np.maximum(1 + _band_interp(h, ds), 0)[..., None]
        if any(dl):
            g = g * np.power(2.0, _band_interp(h, dl) * 1.1 * chroma, dtype=np.float32)[..., None]
    gr = p["grade"]
    if gr["sh_sat"] or gr["hi_sat"]:
        bal = gr["balance"] / 100
        Lc = np.clip(L, 0, 1)
        wsh = (1 - smoothstep(0.0, 0.6 + 0.3 * bal, Lc))[..., None]
        whi = smoothstep(0.35 + 0.3 * bal, 1.0, Lc)[..., None]
        for w, hue, sat in ((wsh, gr["sh_hue"], gr["sh_sat"]), (whi, gr["hi_hue"], gr["hi_sat"])):
            if sat:
                tint = np.array(hue_rgb(hue), np.float32)
                tint = tint - float(tint @ np.array([0.2126, 0.7152, 0.0722], np.float32))  # 只加顏色、不加亮度
                g = g + w * tint * (sat / 100 * 0.25)
    return g


def _grain_field(p, W, H):
    """顆粒：大小跟著照片長邊走，所以預覽和原尺寸的顆粒感一樣。固定亂數種子，每次都一樣。"""
    cells = max(64, round(max(W, H) / (1.2 + p["grain_size"] / 100 * 4.0) / 1.0))
    gw = max(8, round(cells * W / max(W, H)))
    gh = max(8, round(cells * H / max(W, H)))
    rng = np.random.default_rng(20240517)
    return rng.standard_normal((gh, gw)).astype(np.float32)


def apply_rows(g, p, maps, sampler, y0, W, H, grain=None, gsampler=None):
    """g: 這幾列的 sRGB 0..1（h×W×3），回傳處理好的 0..1。"""
    h = g.shape[0]
    xs = np.arange(W, dtype=np.float32) + 0.5
    ys = np.arange(y0, y0 + h, dtype=np.float32) + 0.5
    lin = stage_linear(g, p, xs, ys, W, H)
    out = to_gamma(lin)
    if p["dehaze"] and "t" in maps:
        out = _dehaze(out, p["dehaze"] / 100, maps["A"], sampler.rows(maps["t"], y0, y0 + h))
    # 攝影風格當作「底片」先套，後面的滑桿在它上面微調（跟 iPhone 拍完再修圖一樣）
    out = ST.apply(out, p.get("style", ST.DEFAULT))
    L = luma(out)
    base = sampler.rows(maps["base"], y0, y0 + h)
    L2 = _tone(L, base, p)
    if p["clarity"] and "band" in maps:
        mid = np.clip(4 * np.clip(L2, 0, 1) * (1 - np.clip(L2, 0, 1)), 0, 1) ** 0.6
        L2 = L2 + p["clarity"] / 100 * 1.6 * sampler.rows(maps["band"], y0, y0 + h) * mid
    if p["texture"]:
        detail = L - blur8(L, max(W, H) / 900)
        L2 = L2 + p["texture"] / 100 * 1.4 * detail
    ratio = (np.maximum(L2, 0) + 0.02) / (np.maximum(L, 0) + 0.02)
    out = out * ratio[..., None]
    out = _color(out, L2, p)
    if p["vignette"]:
        xn = (xs / W - 0.5) * 2
        yn = (ys / H - 0.5) * 2
        r = np.sqrt(xn[None, :] ** 2 + yn[:, None] ** 2) / math.sqrt(2)
        mid = 0.15 + 0.7 * p["vig_mid"] / 100
        fe = 0.05 + 0.8 * p["vig_feather"] / 100
        m = smoothstep(mid - fe / 2, mid + fe / 2, r)[..., None]
        v = p["vignette"] / 100
        out = out * np.power(2.0, 1.8 * v * m, dtype=np.float32) if v < 0 else out + (1 - out) * (v * 0.85 * m)
    if p["grain"] and grain is not None:
        n = gsampler.rows(grain, y0, y0 + h)
        Lc = np.clip(luma(out), 0, 1)
        amp = p["grain"] / 100 * 0.07 * (0.35 + 0.65 * 4 * Lc * (1 - Lc))
        out = out + (n * amp)[..., None]
    return soft_clip(out)


# ============================================================ 整張
def render(rgb_u8: np.ndarray, p, progress=None, cancel=None) -> np.ndarray:
    """rgb_u8: H×W×3 uint8 → 調整好的 uint8。原尺寸一段一段處理。"""
    if is_identity(p):
        return rgb_u8
    H, W = rgb_u8.shape[:2]
    maps = prepare(rgb_u8, p)
    sampler = Sampler(*maps["shape"], H, W)
    grain = gsampler = None
    if p["grain"]:
        grain = _grain_field(p, W, H)
        gsampler = Sampler(grain.shape[0], grain.shape[1], H, W)
    out = np.empty_like(rgb_u8)
    pad = int(math.ceil(max(W, H) / 900 * 3)) + 2 if p["texture"] else 0
    # numpy 運算時會放掉 GIL，所以分段丟給幾條執行緒一起算，多核心就快好幾倍。
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    step = min(STRIP, max(32, math.ceil(H / workers)))
    done = [0]

    def one(y0):
        if cancel and cancel():
            return
        y1 = min(H, y0 + step)
        a0, a1 = max(0, y0 - pad), min(H, y1 + pad)
        g = rgb_u8[a0:a1].astype(np.float32) / 255
        res = apply_rows(g, p, maps, sampler, a0, W, H, grain, gsampler)
        out[y0:y1] = np.clip(res[y0 - a0:y0 - a0 + (y1 - y0)] * 255 + 0.5, 0, 255).astype(np.uint8)
        done[0] += y1 - y0
        if progress:
            progress(done[0] / H)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, range(0, H, step)))
    if cancel and cancel():
        return None
    return out


def auto_tone(rgb_u8: np.ndarray, p) -> dict:
    """自動色調：不猜公式，直接在小圖上套用、量結果、再修正。

    原則是「補足」而不是「壓平」：中間調只往 0.42 拉一部分（本來就偏亮 / 偏暗的照片保留氣氛），
    最暗與最亮的 0.5% 用黑色 / 白色拉到接近純黑 / 純白，對比只會加、不會減 ——
    減對比加上壓亮部、提陰影，照片就會變得灰白霧霧的。
    """
    import copy
    H, W = rgb_u8.shape[:2]
    k = min(1.0, 400 / max(H, W))
    small = np.asarray(Image.fromarray(rgb_u8).resize((max(8, round(W * k)), max(8, round(H * k))),
                                                       Image.Resampling.BILINEAR))
    q = copy.deepcopy(p)
    q.update(exposure=0.0, contrast=0.0, highlights=0.0, shadows=0.0, whites=0.0, blacks=0.0, dehaze=0.0,
             texture=0.0, clarity=0.0, vignette=0.0, grain=0.0, spots=[])

    def measure():
        out = render(small, q).astype(np.float32) / 255
        L = luma(out)
        lo, p5, p10, med, p95, hi = np.percentile(L, [0.5, 5, 10, 50, 95, 99.5])
        return dict(lo=lo, p5=p5, p10=p10, med=med, p95=p95, hi=hi, clip=float((out.max(axis=2) >= 0.995).mean()),
                    sat=float((out.max(axis=2) - out.min(axis=2)).mean()))

    m = measure()
    # 1. 曝光：只修正一部分的差距，而且有上下限
    ev = 0.6 * 2.2 * math.log2(0.42 / max(m["med"], 0.02))
    q["exposure"] = float(np.clip(ev, -1.5, 1.5)) if abs(ev) > 0.12 else 0.0
    m = measure()
    while q["exposure"] > 0 and m["clip"] > 0.02:          # 提亮不能把亮部燒掉
        q["exposure"] = max(0.0, q["exposure"] - 0.25)
        m = measure()
    # 2. 黑色被抬起來（霧、逆光）：先去朦朧
    if m["lo"] > 0.12:
        q["dehaze"] = float(np.clip((m["lo"] - 0.05) * 220, 0, 60))
        m = measure()
    # 3. 亮部已經溢出才壓
    # 已經死白的地方救不回來，壓太多只會變成一片灰；所以最多壓到 -35，讓白色還是白的
    if m["clip"] > 0.01:
        q["highlights"] = -float(np.clip(m["clip"] * 500, 12, 35))
        m = measure()
    # 4. 黑點、白點：反覆量幾次，拉到接近純黑 / 純白
    for _ in range(3):
        if m["lo"] > 0.035 or m["lo"] < 0.008:
            q["blacks"] = float(np.clip(q["blacks"] + (0.02 - m["lo"]) / 0.0018 * 0.8, -70, 25))
        if m["hi"] < 0.9 or m["hi"] > 0.985:
            q["whites"] = float(np.clip(q["whites"] + (0.95 - m["hi"]) / max(0.0035 * m["hi"] ** 3, 0.001) * 0.8,
                                        -40, 45))
        m = measure()
    # 5. 對比只加不減：中間 90% 的範圍太窄（平淡）才加
    spread = m["p95"] - m["p5"]
    if spread < 0.6:
        q["contrast"] = float(np.clip((0.68 - spread) * 90, 0, 30))
        m = measure()
    # 6. 暗部整片看不見才稍微提一點陰影
    if m["p10"] < 0.05 and m["med"] < 0.38:
        q["shadows"] = float(np.clip((0.05 - m["p10"]) * 500, 0, 30))
    # 7. 顏色太淡補一點細節飽和度
    vib = float(np.clip((0.13 - m["sat"]) * 200, 0, 20)) if m["sat"] < 0.13 else 0.0
    out = {k: round(q[k], 2 if k == "exposure" else 0) for k in
           ("exposure", "contrast", "highlights", "shadows", "whites", "blacks", "dehaze")}
    out["vibrance"] = round(vib)
    return out
