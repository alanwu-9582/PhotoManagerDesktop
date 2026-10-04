"""主體辨識與遮罩。

  detect(rgb)              → 主體的柔和遮罩（0 = 背景、1 = 主體），跟輸入同大小
  effective(subject, …)    → 套用「遮罩主體 / 遮罩非主體」、範圍與羽化之後，真正要保護（不調整）的區域

辨識有兩種：
  AI 主體模型（選用）：U²-Net-p 顯著物體偵測（約 4.6 MB 的 ONNX），使用者同意後才下載到快取資料夾。
  快速估計（內建）：顏色顯著度 + 跟畫面邊緣的差異 + 遠近（有 AI 深度模型就用它）+ 置中，
                     用 Otsu 門檻切開，再用引導濾波把邊界貼齊照片裡的輪廓。主體清楚、背景單純時堪用。
"""
from __future__ import annotations

import os
import urllib.request

import numpy as np
from PIL import Image

from ... import config
from ..depth_blur import depth as DD
from ..depth_blur import model as depth_model

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
MODEL_SOURCE = "github.com/danielgatis/rembg（U²-Net-p）"
MODEL_SIZE_MB = 4.6
MODEL_PATH = config.CACHE_DIR / "models" / "u2netp.onnx"
INPUT = 320
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
WORK_EDGE = 360            # 快速估計用的長邊

_session = None


# ============================================================ AI 模型
def runtime_available() -> bool:
    return depth_model.runtime_available()


def installed() -> bool:
    return MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 1 << 20


def download(on_progress=None, cancel=None):
    """下載到 .part 再換名，中斷不會留下壞檔。"""
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".part")
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "PhotoManager"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel and cancel():
                raise RuntimeError("已取消")
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    if tmp.stat().st_size < 1 << 20:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("下載的檔案不完整")
    os.replace(tmp, MODEL_PATH)
    reset()


def reset():
    global _session
    _session = None


def _predict_ai(rgb: np.ndarray) -> np.ndarray:
    global _session
    if _session is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) - 1)
        _session = ort.InferenceSession(str(MODEL_PATH), opts, providers=["CPUExecutionProvider"])
    H, W = rgb.shape[:2]
    x = np.asarray(Image.fromarray(rgb).resize((INPUT, INPUT), Image.Resampling.LANCZOS), np.float32)
    x = x / max(1.0, float(x.max()))
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    out = _session.run(None, {_session.get_inputs()[0].name: x})[0]
    m = np.asarray(out, np.float32).reshape(out.shape[-2], out.shape[-1])
    lo, hi = float(m.min()), float(m.max())
    m = (m - lo) / max(hi - lo, 1e-6)
    return DD.resize_f(m, W, H)


# ============================================================ 快速估計
def _otsu(a: np.ndarray) -> float:
    hist, edges = np.histogram(a, bins=64, range=(0, 1))
    p = hist.astype(np.float64) / max(1, hist.sum())
    w = np.cumsum(p)
    mu = np.cumsum(p * (edges[:-1] + edges[1:]) / 2)
    between = (mu[-1] * w - mu) ** 2 / np.maximum(w * (1 - w), 1e-9)
    return float((edges[:-1] + edges[1:])[np.argmax(between)] / 2)


def _estimate(rgb: np.ndarray) -> np.ndarray:
    H, W = rgb.shape[:2]
    k = min(1.0, WORK_EDGE / max(H, W))
    sw, sh = max(16, round(W * k)), max(16, round(H * k))
    small = np.asarray(Image.fromarray(rgb).resize((sw, sh), Image.Resampling.BILINEAR)).astype(np.float32) / 255
    # 對立色空間（近似 Lab）：亮度 + 紅綠 + 黃藍
    opp = np.stack([small @ np.array([0.299, 0.587, 0.114], np.float32),
                    small[..., 0] - small[..., 1],
                    0.5 * (small[..., 0] + small[..., 1]) - small[..., 2]], -1) * np.array([1.0, 1.6, 1.6], np.float32)
    blur = np.stack([DD.gauss(opp[..., c], 1.5) for c in range(3)], -1)
    # 1. 顯著度（Achanta 2009）：跟整張的平均色差多少
    ft = np.linalg.norm(blur - blur.reshape(-1, 3).mean(0), axis=2)
    # 2. 跟畫面邊緣的差異：背景通常會碰到邊，主體通常不會
    b = max(2, round(min(sw, sh) * 0.06))
    border = np.concatenate([blur[:b].reshape(-1, 3), blur[-b:].reshape(-1, 3),
                             blur[:, :b].reshape(-1, 3), blur[:, -b:].reshape(-1, 3)])
    # 邊緣的顏色可能有好幾種（天空、地面…）：取到最近的那幾個代表色的距離
    pick = border[np.linspace(0, len(border) - 1, min(len(border), 48)).astype(int)]
    dist = np.min(np.linalg.norm(blur[:, :, None, :] - pick[None, None, :, :], axis=3), axis=2)
    # 3. 遠近：近的比較可能是主體
    if depth_model.runtime_available() and depth_model.installed():
        near, wn = 1 - depth_model.predict((small * 255).astype(np.uint8)), 0.45
    else:
        near, wn = 1 - DD.estimate_depth((small * 255).astype(np.uint8)), 0.2
    # 4. 置中
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    center = np.exp(-(((xx / sw - 0.5) / 0.32) ** 2 + ((yy / sh - 0.52) / 0.36) ** 2))
    score = (0.30 * DD.normalize(ft) + 0.40 * DD.normalize(dist)) * (1 - wn) / 0.70 + wn * DD.normalize(near)
    score = DD.normalize(score * (0.55 + 0.45 * center))
    t = _otsu(score)
    hard = (score > t).astype(np.float32)
    # 小碎塊抹掉：模糊後再切一次
    hard = (DD.gauss(hard, max(1.0, min(sw, sh) / 160)) > 0.5).astype(np.float32)
    gray = opp[..., 0] / max(1e-6, float(opp[..., 0].max()))
    soft = np.clip(DD.guided(gray, hard, max(2, round(min(sw, sh) / 90)), 2e-4), 0, 1)
    return DD.resize_f(soft.astype(np.float32), W, H)


# ============================================================ 對外
def method_name() -> str:
    return "AI 主體模型" if runtime_available() and installed() else "快速估計"


def detect(rgb: np.ndarray):
    """回傳 (主體遮罩 0..1, 用了哪種方法)。遮罩邊界再用照片本身貼齊一次。"""
    how = "快速估計"
    if runtime_available() and installed():
        try:
            m, how = _predict_ai(rgb), "AI 主體模型"
        except Exception:  # noqa: BLE001
            m = _estimate(rgb)
    else:
        m = _estimate(rgb)
    H, W = rgb.shape[:2]
    gray = rgb.astype(np.float32).mean(axis=2) / 255
    m = np.clip(DD.guided(gray, m.astype(np.float32), max(2, round(max(H, W) / 300)), 2e-4), 0, 1)
    return m.astype(np.float32), how


def effective(subject: np.ndarray, mode: str, feather: float, shift: float) -> np.ndarray | None:
    """要保護（不調整）的區域，0..1。

    mode：none / subject（遮罩主體 → 只調整背景）/ background（遮罩非主體 → 只調整主體）
    shift：-100..100，正的讓主體範圍往外長；feather：0..100，邊緣柔和的程度。
    """
    if subject is None or mode == "none":
        return None
    h, w = subject.shape
    c = 0.5 - shift / 100 * 0.35
    half = 0.03 + feather / 100 * 0.25
    m = np.clip((subject - (c - half)) / (2 * half), 0, 1)
    m = m * m * (3 - 2 * m)
    if feather > 0:
        m = DD.gauss(m.astype(np.float32), max(0.5, feather / 100 * max(h, w) / 70))
    m = np.clip(m, 0, 1).astype(np.float32)
    return m if mode == "subject" else (1 - m).astype(np.float32)


def overlay_rgba(mask: np.ndarray) -> np.ndarray:
    """遮罩的顯示：被遮住（不會調整）的地方蓋一層半透明的紅。回傳 premultiplied BGRA。"""
    a = np.clip(mask * 0.5 * 255, 0, 255).astype(np.uint8)
    h, w = a.shape
    out = np.zeros((h, w, 4), np.uint8)
    out[..., 2] = (a.astype(np.uint16) * 255 // 255).astype(np.uint8)    # R（premultiplied）
    out[..., 0] = (a.astype(np.uint16) * 40 // 255).astype(np.uint8)     # 帶一點點藍，比較像玫瑰紅
    out[..., 1] = (a.astype(np.uint16) * 40 // 255).astype(np.uint8)
    out[..., 3] = a
    return out

