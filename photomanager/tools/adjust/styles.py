"""攝影風格（參考 iPhone 16 之後的相機「攝影風格」）。

iPhone 的攝影風格分兩類：
  膚色基調（undertone）：標準、琥珀色、金色、玫瑰金色、中性、冷玫瑰色 —— 主要改變膚色與中間調的色溫色調
  氛圍（mood）：鮮明、自然、明亮、戲劇效果、寧靜、溫馨、空靈、柔和黑白、強烈黑白 —— 改變整體的明暗與飽和
每個風格都可以再用一塊方形的「控制板」微調：上下是「色調」（往上陰影變亮、比較柔；往下陰影變深、對比強），
左右是「色彩」（往左淡、往右濃）；下面的「色盤」滑桿控制這個風格的顏色有多強。

這裡是依照 Apple 公開的效果描述與實拍比對做的近似，不是 Apple 的原始演算法。
全部在 sRGB（感知亮度）上逐像素計算，所以預覽和原尺寸輸出一致。
"""
from __future__ import annotations

from ...i18n import tr

import numpy as np

# cast：加在中間調的顏色（R, G, B）；sat：飽和度倍率；contrast：對比；bright：亮度；
# fade：黑色抬起來多少（褪色感）；hi：亮部的色調；bw：黑白
STYLES = {
    "standard": {},
    "amber": {"cast": (0.050, 0.014, -0.050), "sat": 1.04, "contrast": 0.05},
    "gold": {"cast": (0.040, 0.030, -0.070), "sat": 1.08, "bright": 0.04, "hi": (1.0, 0.86, 0.48)},
    "rose_gold": {"cast": (0.052, -0.012, -0.012), "sat": 1.0, "hi": (1.0, 0.76, 0.72)},
    "neutral": {"cast": (-0.026, 0.0, 0.024), "sat": 0.94},
    "cool_rose": {"cast": (0.018, -0.024, 0.042), "sat": 1.0},
    "vibrant": {"sat": 1.32, "contrast": 0.12},
    "natural": {"sat": 1.06, "contrast": -0.06, "bright": 0.02},
    "luminous": {"bright": 0.12, "contrast": -0.10, "sat": 1.10, "fade": 0.015},
    "dramatic": {"contrast": 0.36, "bright": -0.10, "sat": 0.88, "cast": (-0.010, 0.0, 0.020)},
    "quiet": {"contrast": -0.28, "fade": 0.07, "sat": 0.72, "cast": (0.016, 0.008, -0.010), "bright": 0.03},
    "cozy": {"cast": (0.056, 0.020, -0.046), "bright": -0.07, "contrast": 0.10, "sat": 1.04},
    "ethereal": {"bright": 0.10, "contrast": -0.30, "fade": 0.05, "sat": 0.78, "cast": (0.020, -0.010, 0.036),
                 "hi": (1.0, 0.86, 0.96)},
    "muted_bw": {"bw": True, "contrast": -0.18, "fade": 0.04},
    "stark_bw": {"bw": True, "contrast": 0.46, "bright": -0.04},
}

UNDERTONES = [("standard", tr("標準")), ("amber", tr("琥珀色")), ("gold", tr("金色")),
              ("rose_gold", tr("玫瑰金色")), ("neutral", tr("中性")), ("cool_rose", tr("冷玫瑰色"))]
MOODS = [("vibrant", tr("鮮明")), ("natural", tr("自然")), ("luminous", tr("明亮")), ("dramatic", tr("戲劇效果")),
         ("quiet", tr("寧靜")), ("cozy", tr("溫馨")), ("ethereal", tr("空靈")), ("muted_bw", tr("柔和黑白")),
         ("stark_bw", tr("強烈黑白"))]

DEFAULT = {"name": "standard", "tone": 0.0, "color": 0.0, "palette": 100.0}
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def is_identity(st) -> bool:
    return (st.get("name", "standard") == "standard" and abs(st.get("tone", 0)) < 1e-9
            and abs(st.get("color", 0)) < 1e-9)


def _luma(g):
    return g[..., 0] * 0.2126 + g[..., 1] * 0.7152 + g[..., 2] * 0.0722


def _contrast(L, c):
    """以 0.46 為支點的 S 曲線；c > 0 加對比、c < 0 變柔。"""
    if abs(c) < 1e-6:
        return L
    k = max(-0.8, c * 1.1)
    piv = 0.46
    x = np.clip(L, 0, 1)
    lo = piv * np.power(x / piv, 1 + k)
    hi = 1 - (1 - piv) * np.power(np.clip((1 - x) / (1 - piv), 0, None), 1 + k)
    return np.where(x < piv, lo, hi) + (L - x)


def apply(g: np.ndarray, st: dict) -> np.ndarray:
    """g: h×w×3 的 sRGB（0..1，可能略超過 1）→ 套用攝影風格。"""
    if is_identity(st):
        return g
    S = STYLES.get(st.get("name", "standard"), {})
    pal = st.get("palette", 100.0) / 100          # 色盤：風格的顏色有多強
    tone = st.get("tone", 0.0) / 100              # 上：陰影變亮、柔；下：陰影變深、對比強
    col = st.get("color", 0.0) / 100              # 左：淡；右：濃
    # 1. 明暗：亮度、對比（含「色調」軸）、陰影
    L = _luma(g)
    L2 = L * (1 + S.get("bright", 0.0))
    L2 = _contrast(L2, S.get("contrast", 0.0) - 0.32 * tone)
    if tone:
        sh = np.clip(1 - np.clip(L2, 0, 1), 0, 1) ** 3
        L2 = L2 + (0.09 if tone > 0 else 0.06) * tone * sh
    g = g * ((np.maximum(L2, 0) + 0.02) / (np.maximum(L, 0) + 0.02))[..., None]
    fade = S.get("fade", 0.0)
    if fade:
        g = fade + g * (1 - fade)
    # 2. 顏色
    gray = _luma(g)[..., None]
    if S.get("bw"):
        # 黑白：色盤調低會留一點原本的顏色
        g = gray + (g - gray) * (1 - pal) * 0.6
        return g
    sat = 1 + (S.get("sat", 1.0) - 1) * pal
    sat *= 1 + (0.65 * col if col > 0 else 0.85 * col)
    g = gray + (g - gray) * max(sat, 0.0)
    cast = S.get("cast")
    if cast:
        Lc = np.clip(_luma(g), 0, 1)
        w = np.clip(4 * Lc * (1 - Lc) * 1.25, 0, 1)[..., None]       # 膚色多半在中間調，主要改中間調
        amt = pal * (1 + 0.6 * col)
        g = g + np.array(cast, np.float32) * amt * w
    hi = S.get("hi")
    if hi:
        Lc = np.clip(_luma(g), 0, 1)
        whi = np.clip((Lc - 0.55) / 0.45, 0, 1)[..., None] ** 2
        t = np.array(hi, np.float32)
        t = t - float(t @ _LUMA)
        g = g + whi * t * 0.10 * pal
    return g
