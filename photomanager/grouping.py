"""照片檢視的群組方式。

每一種都是「照片 → 群組名稱」；群組依照片原本的順序第一次出現的位置排列
（資料夾、日期這種有自然順序的，另外照名稱排）。

「相似照片」比較特別：先依拍攝時間排好，相鄰兩張長得夠像（差異雜湊相差很少），
或是同一台相機一秒內連拍的，就歸在同一組 —— 挑連拍、挑同一個構圖時最好用。
沒有找到相似的照片收在最後的「單張」裡。
"""
from __future__ import annotations

from .i18n import tr

import datetime as dt
import re

from .engine.categories import categories
from .engine.library import nat_key

GROUPS = [
    ("none", tr("不分組")),
    ("folder", tr("資料夾")),
    ("date", tr("拍攝日期")),
    ("camera", tr("相機型號")),
    ("lens", tr("鏡頭")),
    ("category", tr("分類")),
    ("format", tr("檔案格式")),
    ("similar", tr("相似照片")),
]
NEEDS_EXIF = {"date", "camera", "lens", "similar"}

_DT = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
SIMILAR_BITS = 12      # 64 位元裡最多差幾個位元算「像」
BURST_SECONDS = 1.5


def shot_time(p) -> float:
    m = _DT.match(str((p.info or {}).get("dateTimeOriginal") or ""))
    if m:
        try:
            return dt.datetime(*map(int, m.groups())).timestamp()
        except ValueError:
            pass
    return p.mtime or 0.0


def _key(p, how):
    info = p.info or {}
    if how == "folder":
        return p.folder or tr("（最上層）")
    if how == "date":
        m = _DT.match(str(info.get("dateTimeOriginal") or ""))
        if m:
            return f"{m[1]}-{m[2]}-{m[3]}"
        return dt.datetime.fromtimestamp(p.mtime).strftime("%Y-%m-%d") if p.mtime else tr("（不明）")
    if how == "camera":
        return info.get("model") or tr("（沒有 EXIF）")
    if how == "lens":
        return info.get("lensModel") or tr("（沒有鏡頭資訊）")
    if how == "category":
        if p.organized:
            return tr('已整理 · {0}').format(p.organized['folder'])
        c = categories.by_id(p.cat_id) if p.cat_id else None
        return c.name if c else tr("未分類")
    if how == "format":
        return p.ext or tr("（其他）")
    return ""


def group(photos, how):
    """回傳 [(key, 標題, [照片…])]。"""
    if how == "similar":
        return _similar(photos)
    buckets: dict[str, list] = {}
    for p in photos:
        buckets.setdefault(_key(p, how), []).append(p)
    keys = list(buckets)
    if how in ("folder", "date", "format"):
        keys.sort(key=lambda k: (k.startswith(("（", "(")), nat_key(k)))
        if how == "date":
            keys.sort(key=lambda k: (k.startswith(("（", "(")), k), reverse=False)
    elif how == "category":
        order = {c.name: i for i, c in enumerate(categories.all())}
        keys.sort(key=lambda k: (k.startswith(tr("已整理")), k == tr("未分類"), order.get(k, 99), k))
    else:
        keys.sort(key=lambda k: (-len(buckets[k]), nat_key(k)))
    return [(f"{how}:{k}", k, buckets[k]) for k in keys]


def _hamming(a, b):
    return bin(a ^ b).count("1")


def _similar(photos):
    ordered = sorted(photos, key=lambda p: (shot_time(p), nat_key(p.rel_path)))
    runs, cur = [], []
    prev = None
    for p in ordered:
        if prev is not None:
            close = (p.dhash and prev.dhash and _hamming(p.dhash, prev.dhash) <= SIMILAR_BITS)
            burst = (abs(shot_time(p) - shot_time(prev)) <= BURST_SECONDS
                     and (p.info or {}).get("model") == (prev.info or {}).get("model"))
            if not (close or burst):
                runs.append(cur)
                cur = []
        cur.append(p)
        prev = p
    if cur:
        runs.append(cur)
    out, singles = [], []
    for run in runs:
        if len(run) < 2:
            singles += run
            continue
        t = shot_time(run[0])
        when = dt.datetime.fromtimestamp(t).strftime("%m/%d %H:%M:%S") if t else run[0].name
        out.append((f"similar:{run[0].id}", f"{when} · {run[0].name}", run))
    if singles:
        out.append(("similar:__single", tr("單張（沒有相似的）"), singles))
    return out
