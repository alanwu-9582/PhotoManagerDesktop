"""照片檢視的篩選條件（對應網頁版 js/app/photo-filter.js）。

一條條件就是「欄位 + 值 + 包含／不包含」，組合規則：
  同一個欄位的多個「包含」  → 任一符合就算（或）
  不同欄位之間              → 都要符合（且）
  「不包含」                → 一律排除
可以挑的值直接從目前這批照片長出來，所以不會出現選了卻一張都沒有的選項。
"""
from __future__ import annotations

from .i18n import tr

import re

from .engine.categories import categories
from .engine.library import nat_key

_DATE_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})")


def _date(p):
    raw = (p.info or {}).get("dateTimeOriginal") or (p.info or {}).get("dateTime")
    m = _DATE_RE.match(str(raw or ""))
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


def _cat(p):
    if not p.cat_id:
        return tr("未分類")
    c = categories.by_id(p.cat_id)
    return c.name if c else None


FILTER_FIELDS = [
    {"key": "model", "label": tr("相機型號")},
    {"key": "lensModel", "label": tr("鏡頭")},
    {"key": "focalLength", "label": tr("焦段")},
    {"key": "fNumber", "label": tr("光圈")},
    {"key": "exposureTime", "label": tr("快門")},
    {"key": "iso", "label": "ISO", "format": lambda v: f"ISO {v}"},
    {"key": "whiteBalance", "label": tr("白平衡")},
    {"key": "exposureProgram", "label": tr("曝光模式")},
    {"key": "creativeStyle", "label": tr("創意風格")},
    {"key": "make", "label": tr("製造商")},
    {"key": "__date", "label": tr("拍攝日期"), "value": _date},
    {"key": "__ext", "label": tr("檔案格式"), "value": lambda p: p.ext or None},
    {"key": "__cat", "label": tr("分類"), "value": _cat},
    {"key": "__dir", "label": tr("資料夾"), "value": lambda p: p.folder or tr("（最上層）")},
]
_BY_KEY = {f["key"]: f for f in FILTER_FIELDS}


def field_by_key(key):
    return _BY_KEY.get(key)


def value_of(photo, field):
    if not field:
        return None
    if "value" in field:
        return field["value"](photo)
    raw = (photo.info or {}).get(field["key"])
    if raw is None or raw == "":
        return None
    return field["format"](raw) if "format" in field else str(raw)


def options_for(key, photos):
    field = field_by_key(key)
    if not field:
        return []
    counts: dict[str, int] = {}
    for p in photos:
        v = value_of(p, field)
        if v is not None:
            counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], nat_key(str(kv[0]))))


def matches(photo, filters):
    includes: dict[str, list] = {}
    for f in filters:
        if not f.get("field") or not f.get("value"):
            continue
        if f.get("mode") == "exclude":
            if value_of(photo, field_by_key(f["field"])) == f["value"]:
                return False
        else:
            includes.setdefault(f["field"], []).append(f["value"])
    for key, wanted in includes.items():
        if value_of(photo, field_by_key(key)) not in wanted:
            return False
    return True


def apply_filters(photos, filters):
    if not active_count(filters):
        return photos
    return [p for p in photos if matches(p, filters)]


def active_count(filters):
    return sum(1 for f in filters or [] if f.get("field") and f.get("value"))
