"""AI 風格提示詞庫。兩個地方，讀的時候合在一起：

  內建  assets/prompts/            跟著程式一起打包（安裝後在程式資料夾裡，可能沒有寫入權限）
  上傳  <設定資料夾>/prompts/       在程式裡「上傳」的提示詞與參考圖都放這裡

每個地方的結構一樣：
  prompts.json                 全部提示詞的清單（id、資料夾、檔名、標題、說明、標籤、語言）
  <資料夾>/prompt.txt           提示詞本文（原樣保留，複製時一字不改）
  <資料夾>/*.jpg / *.png …      參考結果圖；json 的 thumbnails 留空時，資料夾裡的圖片都會自動列出來

幫內建的提示詞上傳參考圖時，圖片放在 <設定資料夾>/prompts/<id>/，顯示時跟內建的圖一起列。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

PROMPT_DIR = config.ASSETS / "prompts"
INDEX = PROMPT_DIR / "prompts.json"
USER_DIR = config.CONFIG_DIR / "prompts"
USER_INDEX = USER_DIR / "prompts.json"
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
    user: bool = False                                # 在程式裡上傳的（可以刪除）

    def text(self) -> str:
        try:
            return self.text_path.read_text(encoding="utf-8-sig")
        except OSError:
            return ""

    @property
    def image_dir(self) -> Path:
        """上傳參考圖要放的地方（一定寫得進去）。"""
        return self.folder if self.user else USER_DIR / self.id


def _images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)


def _read_index(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")).get("prompts", [])
    except (OSError, ValueError):
        return []


def _entries(base: Path, index: Path, user: bool) -> list[Prompt]:
    out = []
    for e in _read_index(index):
        folder = base / e.get("folder", e.get("id", ""))
        thumbs = [folder / t for t in e.get("thumbnails") or []] or _images(folder)
        out.append(Prompt(id=e.get("id", folder.name), folder=folder, text_path=folder / e.get("file", "prompt.txt"),
                          title=str(e.get("title", "")), description=str(e.get("description", "")),
                          tags=list(e.get("tags", [])), language=e.get("language", ""),
                          thumbnails=[p for p in thumbs if p.exists()], user=user))
    return out


def load() -> list[Prompt]:
    builtin = _entries(PROMPT_DIR, INDEX, False)
    for pr in builtin:                       # 內建提示詞另外上傳的參考圖
        pr.thumbnails += [p for p in _images(USER_DIR / pr.id) if p not in pr.thumbnails]
    return builtin + _entries(USER_DIR, USER_INDEX, True)


def _copy_images(paths, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in paths:
        src = Path(src)
        if src.suffix.lower() not in IMAGE_EXT or not src.is_file():
            continue
        target = dest / src.name
        k = 2
        while target.exists():                # 同名就加上編號，不蓋掉原本的圖
            target = dest / f"{src.stem} ({k}){src.suffix}"
            k += 1
        shutil.copy2(src, target)
        n += 1
    return n


def _write_user_index(entries: list[dict]):
    USER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USER_INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "prompts": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, USER_INDEX)


def add_prompt(title: str, text: str, tags=(), images=()) -> str:
    """新增一個上傳的提示詞，回傳 id。"""
    pid = f"user-{time.strftime('%Y%m%d-%H%M%S')}"
    while (USER_DIR / pid).exists():
        pid += "-1"
    folder = USER_DIR / pid
    folder.mkdir(parents=True)
    (folder / "prompt.txt").write_text(text, encoding="utf-8")
    _copy_images(images, folder)
    entries = _read_index(USER_INDEX)
    entries.append({"id": pid, "folder": pid, "file": "prompt.txt", "title": title.strip() or "未命名提示詞",
                    "description": "", "tags": [t for t in tags if t], "language": "", "thumbnails": []})
    _write_user_index(entries)
    return pid


def add_images(prompt: Prompt, paths) -> int:
    return _copy_images(paths, prompt.image_dir)


def delete_prompt(prompt: Prompt):
    """只能刪上傳的；內建的跟著程式，不在這裡刪。"""
    if not prompt.user:
        return
    _write_user_index([e for e in _read_index(USER_INDEX) if e.get("id") != prompt.id])
    shutil.rmtree(prompt.folder, ignore_errors=True)


def open_folder(path: Path):
    """用檔案總管打開。"""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(path)))
