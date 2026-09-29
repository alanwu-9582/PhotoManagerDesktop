"""設定與本機記憶。

網頁版把這些放在 localStorage / IndexedDB，桌面版改成使用者資料夾裡的幾個 JSON 檔：

    %APPDATA%/PhotoManager/settings.json     介面偏好、上次的資料夾、最近清單
    %APPDATA%/PhotoManager/categories.json   自訂分類
    %APPDATA%/PhotoManager/marks/<hash>.json 每個資料夾的分類標記（依完整路徑分開存）
    %LOCALAPPDATA%/PhotoManager/thumbs/      縮圖快取（可以整個刪掉，會自己重建）

寫入一律先寫暫存檔再換名，當機或斷電不會留下半個 JSON。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

APP_NAME = "PhotoManager"


def _base(env: str, fallback: str) -> Path:
    root = os.environ.get(env)
    if root:
        return Path(root) / APP_NAME
    return Path.home() / fallback / APP_NAME


if sys.platform == "win32":
    CONFIG_DIR = _base("APPDATA", "AppData/Roaming")
    CACHE_DIR = _base("LOCALAPPDATA", "AppData/Local")
elif sys.platform == "darwin":
    CONFIG_DIR = Path.home() / "Library/Application Support" / APP_NAME
    CACHE_DIR = Path.home() / "Library/Caches" / APP_NAME
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_NAME

THUMB_DIR = CACHE_DIR / "thumbs"
MARKS_DIR = CONFIG_DIR / "marks"
ASSETS = Path(__file__).resolve().parent / "assets"


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


class Settings:
    """介面偏好。小、而且讀寫頻繁，整份留在記憶體裡，改了才寫回去。"""

    PATH = CONFIG_DIR / "settings.json"
    DEFAULTS = {
        "recursive": True,
        "lastFolder": None,
        "recentFolders": [],
        "fields": None,          # 卡片上要顯示的 EXIF 欄位
        "columns": 4,            # 照片檢視每排幾張
        "manageColumns": 3,      # 整理分類縮圖欄每排幾張
        "sidebarCollapsed": False,
        "theme": "system",       # system | light | dark
        "geometry": None,
        "photoDisplay": "card",  # card | list
        "photoGroup": "none",    # 照片檢視的群組方式
        "renamePresets": None,   # [{name, pattern, start, pad, case, find, replace}]
        "language": "zh-Hant",   # 顯示語言：zh-Hant | en
    }

    def __init__(self):
        data = read_json(self.PATH, {}) or {}
        self.data = {**self.DEFAULTS, **{k: v for k, v in data.items() if k in self.DEFAULTS}}

    def get(self, key):
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        if self.data.get(key) == value:
            return
        self.data[key] = value
        write_json(self.PATH, self.data)

    def remember_folder(self, path: str):
        recent = [p for p in self.get("recentFolders") or [] if p != path]
        recent.insert(0, path)
        self.data["recentFolders"] = recent[:8]
        self.data["lastFolder"] = path
        write_json(self.PATH, self.data)


settings = Settings()


def marks_path(root: str) -> Path:
    key = hashlib.sha1(os.path.normcase(os.path.abspath(root)).encode("utf-8")).hexdigest()[:20]
    return MARKS_DIR / f"{key}.json"
