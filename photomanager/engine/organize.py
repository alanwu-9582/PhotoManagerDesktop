"""直接在本機資料夾裡搬移／複製／改名照片。

每張照片依它的分類，移動或複製到來源資料夾底下對應的子資料夾（分類的 folder 欄位）。
同一個磁碟上的移動與改名是 os.replace 一次完成，不會真的搬資料；跨磁碟才退回複製 + 刪除。
目的地已經有同名檔案時一律補 _1 / _2 …，不會覆蓋任何東西。
"""
from __future__ import annotations

from ..i18n import tr

import os
import shutil

from .categories import categories


def unique_name(directory: str, name: str) -> str:
    if not os.path.exists(os.path.join(directory, name)):
        return name
    base, ext = os.path.splitext(name)
    for i in range(1, 10000):
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(os.path.join(directory, cand)):
            return cand
    raise OSError(tr('找不到可用的檔名: {0}').format(name))


def _dest_root(lib, photo) -> str:
    """資料夾模式放在來源資料夾底下；檔案模式放在照片自己的資料夾底下。"""
    if lib.mode == "folder" and lib.root:
        return lib.root
    return os.path.dirname(photo.path)


def plan(lib):
    """整理前先讓使用者看清楚會發生什麼事。"""
    by_folder: dict[str, dict] = {}
    items = []
    for p in lib.photos:
        if not p.cat_id or p.organized:
            continue
        cat = categories.by_id(p.cat_id)
        if not cat or cat.action == "keep":
            continue
        folder = cat.folder or cat.name
        root = _dest_root(lib, p)
        # 已經在目標資料夾裡就不用動
        if os.path.normcase(os.path.dirname(p.path)) == os.path.normcase(os.path.join(root, folder)):
            continue
        items.append((p, cat, folder, root))
        b = by_folder.setdefault(folder, {"folder": folder, "action": cat.action, "count": 0})
        b["count"] += 1
    return {"items": items, "byFolder": sorted(by_folder.values(), key=lambda b: -b["count"]), "total": len(items)}


def run(lib, items, on_progress=None):
    """執行整理（可在背景執行緒呼叫）。"""
    result = {"moved": 0, "copied": 0, "failed": [], "done": []}
    total = len(items)
    for i, (p, cat, folder, root) in enumerate(items):
        if on_progress:
            on_progress(i, total)
        try:
            dest_dir = os.path.join(root, folder)
            os.makedirs(dest_dir, exist_ok=True)
            target = unique_name(dest_dir, p.name)
            dest = os.path.join(dest_dir, target)
            if cat.action == "copy":
                shutil.copy2(p.path, dest)
                p.organized = {"folder": folder, "action": "copy"}
                result["copied"] += 1
                result["done"].append((p.name, tr('複製到 {0}/{1}').format(folder, target)))
            else:
                try:
                    os.replace(p.path, dest)
                except OSError:
                    shutil.move(p.path, dest)
                old = p.name
                rel = f"{folder}/{target}" if lib.mode == "folder" else target
                lib.relocated(p, dest, rel)
                p.organized = {"folder": folder, "action": "move"}
                result["moved"] += 1
                result["done"].append((old, tr('移動到 {0}/{1}').format(folder, target)))
        except OSError as e:
            result["failed"].append({"name": p.name, "message": e.strerror or str(e)})
    if on_progress:
        on_progress(total, total)
    return result


def rename(lib, items, on_progress=None):
    """整批就地改名。items: [(photo, new_name)]。"""
    result = {"renamed": 0, "failed": [], "done": []}
    total = len(items)
    for i, (p, new_name) in enumerate(items):
        try:
            if new_name and new_name != p.name:
                directory = os.path.dirname(p.path)
                # 只差大小寫時直接改（Windows 上 exists() 會說它已經在了）
                same = new_name.lower() == p.name.lower()
                target = new_name if same else unique_name(directory, new_name)
                dest = os.path.join(directory, target)
                os.rename(p.path, dest)
                result["done"].append((p.name, f"→ {target}"))
                slash = p.rel_path.rfind("/")
                lib.relocated(p, dest, (p.rel_path[:slash + 1] if slash >= 0 else "") + target)
                result["renamed"] += 1
        except OSError as e:
            result["failed"].append({"name": p.name, "message": e.strerror or str(e)})
        if on_progress:
            on_progress(i + 1, total)
    return result
