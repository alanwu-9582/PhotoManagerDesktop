"""照片分類設定。

來源優先序：
  1. 使用者資料夾裡的 categories.json（使用者自訂，隨時可改）
  2. 程式附帶的 assets/categories.json（專案預設）
  3. 內建 FALLBACK（保證一定有東西可用）

每個分類有一個穩定的 id，照片記的是 id，所以之後改快捷鍵或名稱都不會弄丟已標記的照片。
"""
from __future__ import annotations

from ..i18n import tr

import json
import random
import re
import string
from dataclasses import dataclass, asdict

from PySide6.QtCore import QObject, Signal

from .. import config

ACTIONS = ["move", "copy", "keep"]
ACTION_LABEL = {"move": tr("移動"), "copy": tr("複製"), "keep": tr("只標記")}
TRASH = {"key": "q", "name": tr("廢片"), "folder": "_廢片", "color": "#ff8080", "action": "move", "isTrash": True}
FALLBACK = [
    {"key": "1", "name": tr("機器人特寫"), "color": "#87d1ff", "action": "move"},
    {"key": "2", "name": tr("賽場動態"), "color": "#7dff95", "action": "move"},
    {"key": "3", "name": tr("團隊合照"), "color": "#ffbc5e", "action": "move"},
    TRASH,
]
PALETTE = ["#87d1ff", "#7dff95", "#ffbc5e", "#ff8080", "#c9a3ff", "#5eead4", "#f0abfc", "#fde047", "#94a3b8"]
# q 保留給廢片；其餘快捷鍵完全由分類順序決定。
KEY_POOL = "1234567890wertyuiopasdfghjklzxcvbnm"
USER_PATH = config.CONFIG_DIR / "categories.json"
PROFILES_PATH = config.CONFIG_DIR / "category_profiles.json"
DEFAULT_PROFILE = tr("預設")

# 新增設定檔時可以選的範本：每一組都是一整套分類（廢片 q 會自動補上）。
TEMPLATES = {
    tr("旅遊"): [(tr("風景"), "#87d1ff"), (tr("人像"), "#f0abfc"), (tr("美食"), "#ffbc5e"), (tr("街拍"), "#7dff95"), (tr("建築"), "#c9a3ff")],
    tr("活動攝影"): [(tr("舞台"), "#87d1ff"), (tr("觀眾"), "#7dff95"), (tr("合照"), "#ffbc5e"), (tr("花絮"), "#c9a3ff"), (tr("精選"), "#fde047")],
    tr("作品整理"): [(tr("精選"), "#fde047"), (tr("候選"), "#87d1ff"), (tr("待修圖"), "#ffbc5e"), (tr("已發佈"), "#7dff95")],
}
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def new_id() -> str:
    return "cat_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def sanitize_folder(name) -> str:
    """Windows / macOS 都不能出現的字元，另外去掉結尾的點與空白。"""
    s = re.sub(r'[\\/:*?"<>|]', "_", str(name or ""))
    s = re.sub(r"[\x00-\x1f]", "", s).strip()
    s = re.sub(r"[. ]+$", "", s)
    return (s or tr("未命名"))[:80]


def action_label(action: str) -> str:
    return ACTION_LABEL.get(action, action)


@dataclass
class Category:
    id: str
    key: str
    name: str
    folder: str
    color: str
    action: str
    isTrash: bool = False


def _normalize_one(raw, index) -> Category:
    src = raw if isinstance(raw, dict) else {}
    name = str(src.get("name") or "").strip() or tr('分類 {0}').format(index + 1)
    is_trash = src.get("isTrash") is True or src.get("name") == TRASH["name"] or src.get("folder") == TRASH["folder"]
    action = src.get("action") if src.get("action") in ACTIONS else "move"
    color = src.get("color") if HEX_RE.match(str(src.get("color") or "")) else PALETTE[index % len(PALETTE)]
    cid = src.get("id") if isinstance(src.get("id"), str) and src.get("id") else new_id()
    if is_trash:
        return Category(cid, TRASH["key"], TRASH["name"], TRASH["folder"], color, TRASH["action"], True)
    return Category(cid, str(src.get("key") or "")[:1], name, sanitize_folder(name), color, action)


def _apply_fixed_rules(items: list[Category]) -> list[Category]:
    trash = next((c for c in items if c.isTrash), None)
    normal = [c for c in items if not c.isTrash]
    for i, c in enumerate(normal):
        c.key = KEY_POOL[i] if i < len(KEY_POOL) else ""
        c.folder = sanitize_folder(c.name)
    if trash:
        trash.key, trash.name, trash.folder, trash.action = TRASH["key"], TRASH["name"], TRASH["folder"], TRASH["action"]
        normal.append(trash)
    return normal


def _normalize_list(arr) -> list[Category]:
    if not isinstance(arr, list):
        return []
    used, normal, trash = set(), [], None
    for i, raw in enumerate(arr[:40]):
        c = _normalize_one(raw, i)
        while c.id in used:
            c.id = new_id()
        used.add(c.id)
        if c.isTrash:
            trash = trash or c
        else:
            normal.append(c)
    normal.append(trash or _normalize_one(TRASH, len(normal)))
    return _apply_fixed_rules(normal)


class Categories(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._defaults = None
        saved = config.read_json(USER_PATH)
        items = _normalize_list(saved.get("categories")) if isinstance(saved, dict) else []
        self.items: list[Category] = items or [Category(**asdict(c)) for c in self.defaults()]
        data = config.read_json(PROFILES_PATH)
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict) and data["profiles"]:
            self.profiles: dict[str, list] = data["profiles"]
            self.active = data.get("active") if data.get("active") in self.profiles else next(iter(self.profiles))
        else:
            self.profiles = {DEFAULT_PROFILE: [asdict(c) for c in self.items]}
            self.active = DEFAULT_PROFILE

    def defaults(self) -> list[Category]:
        if self._defaults is None:
            data = config.read_json(config.ASSETS / "categories.json")
            parsed = _normalize_list(data.get("categories")) if isinstance(data, dict) else []
            self._defaults = parsed or _normalize_list(FALLBACK)
        return self._defaults

    def save(self):
        """存目前這一套，同時寫回目前的設定檔 —— 在設定頁改的東西就是改這個設定檔。"""
        config.write_json(USER_PATH, {"version": 2, "categories": [asdict(c) for c in self.items]})
        self.profiles[self.active] = [asdict(c) for c in self.items]
        self._save_profiles()
        self.changed.emit()

    def _save_profiles(self):
        config.write_json(PROFILES_PATH, {"version": 1, "active": self.active, "profiles": self.profiles})

    # ---------------- 設定檔（Profile）
    def profile_names(self) -> list[str]:
        return list(self.profiles)

    def switch(self, name: str):
        if name not in self.profiles or name == self.active:
            return
        self.active = name
        self.items = _normalize_list(self.profiles[name]) or [Category(**asdict(c)) for c in self.defaults()]
        self.save()

    def _unique(self, name: str) -> str:
        name = (name or "").strip()[:40] or tr("新的設定檔")
        base, n = name, 2
        while name in self.profiles:
            name = f"{base} {n}"
            n += 1
        return name

    def create_profile(self, name: str, template: str | None = None, copy_current=False) -> str:
        """template：TEMPLATES 的名稱；copy_current：複製目前這一套；都沒有就是只有廢片的空白設定。"""
        name = self._unique(name)
        if copy_current:
            raw = [{**asdict(c), "id": new_id()} for c in self.items]
        elif template in TEMPLATES:
            raw = [{"name": n, "color": col, "action": "move"} for n, col in TEMPLATES[template]]
        elif template == DEFAULT_PROFILE:
            raw = [{**asdict(c), "id": new_id()} for c in self.defaults()]
        else:
            raw = []
        self.profiles[name] = [asdict(c) for c in _normalize_list(raw)]
        self._save_profiles()
        self.switch(name)
        return name

    def rename_profile(self, old: str, new: str) -> str:
        if old not in self.profiles:
            return old
        new = (new or "").strip()[:40]
        if not new or new == old:
            return old
        new = self._unique(new)
        self.profiles = {(new if k == old else k): v for k, v in self.profiles.items()}
        if self.active == old:
            self.active = new
        self._save_profiles()
        self.changed.emit()
        return new

    def delete_profile(self, name: str):
        if name not in self.profiles or len(self.profiles) <= 1:
            return
        del self.profiles[name]
        if self.active == name:
            self.active = None
            self.switch(next(iter(self.profiles)))
        else:
            self._save_profiles()
            self.changed.emit()

    # ---------------- 讀取
    def all(self) -> list[Category]:
        return self.items

    def by_id(self, cid) -> Category | None:
        if not cid:
            return None
        return next((c for c in self.items if c.id == cid), None)

    def by_key(self, key: str) -> Category | None:
        if not key or len(key) != 1:
            return None
        k = key.lower()
        return next((c for c in self.items if c.key.lower() == k), None)

    # ---------------- 編輯
    def add(self) -> Category:
        cat = _normalize_one({"name": tr("新分類"), "color": PALETTE[len(self.items) % len(PALETTE)]}, len(self.items))
        self.items.insert(max(0, len(self.items) - 1), cat)
        self.items = _apply_fixed_rules(self.items)
        self.save()
        return cat

    def update(self, cid, **patch) -> Category | None:
        cat = self.by_id(cid)
        if not cat:
            return None
        if not cat.isTrash and "name" in patch:
            cat.name = str(patch["name"])[:60]
            cat.folder = sanitize_folder(cat.name)
        if "color" in patch and HEX_RE.match(str(patch["color"])):
            cat.color = patch["color"]
        if not cat.isTrash and patch.get("action") in ACTIONS:
            cat.action = patch["action"]
        self.items = _apply_fixed_rules(self.items)
        self.save()
        return cat

    def move(self, cid, delta: int):
        i = next((n for n, c in enumerate(self.items) if c.id == cid), -1)
        j = i + delta
        if i < 0 or self.items[i].isTrash or j < 0 or j >= len(self.items) or self.items[j].isTrash:
            return
        self.items.insert(j, self.items.pop(i))
        self.items = _apply_fixed_rules(self.items)
        self.save()

    def remove(self, cid):
        i = next((n for n, c in enumerate(self.items) if c.id == cid), -1)
        if i < 0 or self.items[i].isTrash:
            return None
        removed = self.items.pop(i)
        self.items = _apply_fixed_rules(self.items)
        self.save()
        return removed

    def reset(self):
        self.items = [Category(**{**asdict(c), "id": new_id()}) for c in self.defaults()]
        self.save()

    # ---------------- JSON 匯入 / 匯出
    def to_json(self) -> str:
        out = []
        for c in self.items:
            d = {"key": c.key, "name": c.name, "folder": c.folder, "color": c.color, "action": c.action}
            if c.isTrash:
                d["isTrash"] = True
            out.append(d)
        return json.dumps({"version": 2, "categories": out}, ensure_ascii=False, indent=2)

    def import_json(self, text: str):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(tr("不是合法的 JSON 檔案")) from e
        arr = data if isinstance(data, list) else (data.get("categories") if isinstance(data, dict) else None)
        parsed = _normalize_list(arr)
        if not parsed:
            raise ValueError(tr("JSON 裡找不到任何分類（需要 categories 陣列）"))
        self.items = parsed
        self.save()


categories = Categories()
