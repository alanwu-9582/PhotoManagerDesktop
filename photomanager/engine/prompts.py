"""AI 風格提示詞庫：assets/prompts/ 底下。

  prompts.json                 全部提示詞的清單（標題、說明、標籤、語言、檔案位置）
  <資料夾>/prompt.txt           提示詞本文（原樣保留，複製時一字不改）
  <資料夾>/*.jpg / *.png …      參考結果圖（縮圖）；json 的 thumbnails 留空時，資料夾裡的圖片都會自動列出來

新增一個提示詞：開一個資料夾放 prompt.txt 和參考圖，再在 prompts.json 加一筆。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

PROMPT_DIR = config.ASSETS / "prompts"
INDEX = PROMPT_DIR / "prompts.json"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".bmp", ".gif")


@dataclass
class Prompt:
    id: str
    folder: Path
    text_path: Path
    title: str
    description: str
    tags: list = field(default_factory=list)
    language: str = ""
    thumbnails: list = field(default_factory=list)   # 圖片的完整路徑

    def text(self) -> str:
        try:
            return self.text_path.read_text(encoding="utf-8-sig")
        except OSError:
            return ""


def load() -> list[Prompt]:
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    out = []
    for e in data.get("prompts", []):
        folder = PROMPT_DIR / e.get("folder", e.get("id", ""))
        text_path = folder / e.get("file", "prompt.txt")
        thumbs = [folder / t for t in e.get("thumbnails") or []]
        if not thumbs and folder.is_dir():
            thumbs = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)
        out.append(Prompt(id=e.get("id", folder.name), folder=folder, text_path=text_path,
                          title=str(e.get("title", "")), description=str(e.get("description", "")),
                          tags=list(e.get("tags", [])), language=e.get("language", ""),
                          thumbnails=[p for p in thumbs if p.exists()]))
    return out


def open_folder(path: Path):
    """用檔案總管打開。"""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(path)))
