"""選用的 AI 模型清單（設定頁的「AI 模型」用）。

每個模型的模組都提供同一組介面：MODEL_PATH、MODEL_SIZE_MB、MODEL_SOURCE、installed()、download()、reset()，
以及 runtime_available()（有沒有 onnxruntime）。以後加新的模型，在 MODELS 加一筆就會出現在設定頁。
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import config

MODELS_DIR = config.CACHE_DIR / "models"


@dataclass
class ModelInfo:
    key: str
    name: str
    purpose: str          # 用在哪裡（一句話）
    module: object

    @property
    def path(self):
        return self.module.MODEL_PATH

    def installed(self) -> bool:
        return self.module.installed()

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size if self.installed() else 0
        except OSError:
            return 0

    def delete(self):
        self.module.reset()          # 先放掉已經載入的模型，Windows 才刪得掉檔案
        self.path.unlink(missing_ok=True)
        self.path.with_suffix(".part").unlink(missing_ok=True)


def all_models() -> list[ModelInfo]:
    from ..tools.adjust import subject
    from ..tools.depth_blur import model as depth
    return [
        ModelInfo("depth", "Depth Anything V2 Small", "景深模糊：判斷畫面的遠近", depth),
        ModelInfo("subject", "U²-Net-p", "調整 → 遮罩：辨識主體", subject),
    ]


def runtime_available() -> bool:
    from ..tools.depth_blur import model as depth
    return depth.runtime_available()
