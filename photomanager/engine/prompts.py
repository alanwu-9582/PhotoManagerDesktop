"""AI 風格提示詞庫。兩個地方，讀的時候合在一起：

  內建  assets/prompts/            跟著程式一起打包（安裝後在程式資料夾裡，可能沒有寫入權限）
  上傳  <設定資料夾>/prompts/       在程式裡「上傳」的提示詞與範例都放這裡

每個地方的結構一樣：
  prompts.json                 全部提示詞的清單（id、資料夾、檔名、標題、說明、標籤、語言）
  <資料夾>/prompt.txt           提示詞本文（原樣保留，複製時一字不改）
  <資料夾>/<名稱>_原圖.jpg       範例：原圖與成果圖成對，用檔名配對（也接受 _before / _after）
  <資料夾>/<名稱>_成果.jpg       沒有標示是原圖還是成果的圖片，當成只有成果圖的範例

幫內建的提示詞新增範例時，圖片放在 <設定資料夾>/prompts/<id>/，顯示時跟內建的一起列。
在程式裡上傳的圖片一律先壓縮（長邊 1600、JPEG 品質 85），不會把十幾 MB 的原檔整個複製進來。
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
BEFORE_MARKS = ("_原圖", "-原圖", "_before", "-before")
AFTER_MARKS = ("_成果", "-成果", "_after", "-after")
UPLOAD_EDGE, UPLOAD_QUALITY = 1600, 85


@dataclass
class Example:
    before: Path | None
    after: Path | None
    removable: bool = False          # 上傳的才可以刪；內建的跟著程式

    @property
    def cover(self) -> Path | None:
        return self.after or self.before


@dataclass
class Prompt:
    id: str
    folder: Path
    text_path: Path
    title: str
    description: str
    tags: list = field(default_factory=list)
    language: str = ""
    examples: list = field(default_factory=list)      # [Example]
    user: bool = False                                # 在程式裡上傳的（可以刪除）

    def text(self) -> str:
        try:
            return self.text_path.read_text(encoding="utf-8-sig")
        except OSError:
            return ""

    @property
    def cover(self) -> Path | None:
        """卡片上顯示的圖：第一組範例的成果圖。"""
        return next((e.cover for e in self.examples if e.cover), None)

    @property
    def image_dir(self) -> Path:
        """新增範例要放的地方（一定寫得進去）。"""
        return self.folder if self.user else USER_DIR / self.id


def _split_mark(stem: str):
    low = stem.lower()
    for marks, kind in ((BEFORE_MARKS, "before"), (AFTER_MARKS, "after")):
        for m in marks:
            if low.endswith(m.lower()):
                return stem[: -len(m)], kind
    return stem, "after"


def _examples(folder: Path, removable: bool) -> list[Example]:
    """資料夾裡的圖片依檔名配成「原圖 → 成果」。"""
    if not folder.is_dir():
        return []
    pairs: dict[str, dict] = {}
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in IMAGE_EXT:
            continue
        key, kind = _split_mark(p.stem)
        slot = pairs.setdefault(key, {})
        if kind in slot:                     # 同一個名稱有兩張成果圖：分開成兩組
            pairs[f"{key}\0{p.name}"] = {kind: p}
        else:
            slot[kind] = p
    return [Example(v.get("before"), v.get("after"), removable) for v in pairs.values()]


def _read_index(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")).get("prompts", [])
    except (OSError, ValueError):
        return []


def _entries(base: Path, index: Path, user: bool) -> list[Prompt]:
    out = []
    for e in _read_index(index):
        folder = base / e.get("folder", e.get("id", ""))
        out.append(Prompt(id=e.get("id", folder.name), folder=folder, text_path=folder / e.get("file", "prompt.txt"),
                          title=str(e.get("title", "")), description=str(e.get("description", "")),
                          tags=list(e.get("tags", [])), language=e.get("language", ""),
                          examples=_examples(folder, user), user=user))
    return out


def load() -> list[Prompt]:
    builtin = _entries(PROMPT_DIR, INDEX, False)
    for pr in builtin:                       # 內建提示詞另外上傳的範例（可以刪）
        pr.examples += _examples(USER_DIR / pr.id, True)
    return builtin + _entries(USER_DIR, USER_INDEX, True)


# ============================================================ 上傳
def compress(src, dest: Path):
    """讀進來（含 HEIC）、轉正、縮到長邊 UPLOAD_EDGE、存成 JPEG。"""
    from . import image as imgmod
    img = imgmod.decode(str(src), UPLOAD_EDGE)
    if img is None or img.isNull():
        raise OSError(f"讀不了這張圖片：{Path(src).name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    imgmod.save_image(img, str(dest), UPLOAD_QUALITY)


def add_example(prompt_dir: Path, before=None, after=None) -> Example:
    """新增一組範例（原圖、成果圖各自壓縮後存進去）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name, k = stamp, 2
    while any((prompt_dir / f"{name}{m}.jpg").exists() for m in ("_原圖", "_成果")):
        name = f"{stamp}-{k}"
        k += 1
    b = prompt_dir / f"{name}_原圖.jpg" if before else None
    a = prompt_dir / f"{name}_成果.jpg" if after else None
    if before:
        compress(before, b)
    if after:
        compress(after, a)
    return Example(b, a, True)


def delete_example(ex: Example):
    if not ex.removable:
        return
    for p in (ex.before, ex.after):
        if p is not None:
            p.unlink(missing_ok=True)


def _write_user_index(entries: list[dict]):
    USER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USER_INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "prompts": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, USER_INDEX)


def add_prompt(title: str, text: str, tags=(), before=None, after=None) -> str:
    """新增一個上傳的提示詞（可以順便附一組範例），回傳 id。"""
    pid = f"user-{time.strftime('%Y%m%d-%H%M%S')}"
    while (USER_DIR / pid).exists():
        pid += "-1"
    folder = USER_DIR / pid
    folder.mkdir(parents=True)
    (folder / "prompt.txt").write_text(text, encoding="utf-8")
    if before or after:
        add_example(folder, before, after)
    entries = _read_index(USER_INDEX)
    entries.append({"id": pid, "folder": pid, "file": "prompt.txt", "title": title.strip() or "未命名提示詞",
                    "description": "", "tags": [t for t in tags if t], "language": ""})
    _write_user_index(entries)
    return pid


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
