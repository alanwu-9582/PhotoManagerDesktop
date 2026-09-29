"""AI 深度模型（選用）：Depth Anything V2 Small，int8 量化的 ONNX 版，約 27 MB。

不隨程式附帶，第一次要用時由使用者確認後才下載到快取資料夾，之後離線也能用。
沒有 onnxruntime 或沒下載模型時，景深工具會退回 depth.py 的快速估計。
"""
from __future__ import annotations

from ...i18n import tr

import os
import urllib.request

import numpy as np
from PIL import Image

from ... import config

MODEL_URL = ("https://huggingface.co/onnx-community/depth-anything-v2-small/"
             "resolve/main/onnx/model_quantized.onnx")
MODEL_SOURCE = "huggingface.co/onnx-community/depth-anything-v2-small"
MODEL_SIZE_MB = 27
MODEL_PATH = config.CACHE_DIR / "models" / "depth-anything-v2-small-q8.onnx"
INPUT = 518   # 14 的倍數，模型訓練時用的大小
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

_session = None


def runtime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


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
                raise RuntimeError(tr("已取消"))
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    if tmp.stat().st_size < 1 << 20:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(tr("下載的檔案不完整"))
    os.replace(tmp, MODEL_PATH)


def _load(path=None):
    global _session
    if _session is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) - 1)
        _session = ort.InferenceSession(str(path or MODEL_PATH), opts, providers=["CPUExecutionProvider"])
    return _session


def reset():
    global _session
    _session = None


def predict(rgb: np.ndarray, path=None) -> np.ndarray:
    """rgb: HxWx3 uint8 → 同大小的深度 0（近）..1（遠）。"""
    H, W = rgb.shape[:2]
    sess = _load(path)
    x = np.asarray(Image.fromarray(rgb).resize((INPUT, INPUT), Image.Resampling.BICUBIC), np.float32) / 255
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    name = sess.get_inputs()[0].name
    out = sess.run(None, {name: x})[0]
    disp = np.asarray(out, np.float32).reshape(out.shape[-2], out.shape[-1])
    # 模型輸出的是相對視差：越大越近。轉成 0 近 … 1 遠。
    lo, hi = np.percentile(disp, [1, 99])
    near = np.clip((disp - lo) / max(hi - lo, 1e-6), 0, 1)
    depth = (1 - near).astype(np.float32)
    return np.asarray(Image.fromarray(depth).resize((W, H), Image.Resampling.BILINEAR))
