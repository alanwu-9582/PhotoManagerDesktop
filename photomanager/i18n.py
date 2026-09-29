"""顯示語言。

介面上的字串一律寫成 tr("繁體中文原文")，繁體中文就是原文本身，其他語言用原文當鍵查表
（例如 i18n_en.py）。帶變數的字串寫成 tr("已整理 {0} 張").format(n)，翻譯時可以調換 {0} 的位置。

語言在啟動時決定：很多標籤（功能列、欄位名稱、選項）在模組載入時就組好了，
所以切換語言之後重新啟動程式才會完整套用（設定頁會直接幫忙重新啟動）。

照片的 EXIF 值（例如「光圈優先」）存的是固定的中文，顯示的時候才經過 tr_value() 翻譯，
所以快取在不同語言之間可以共用。
"""
from __future__ import annotations

import re

from . import config

LANGUAGES = [("zh-Hant", "繁體中文"), ("en", "English")]
DEFAULT = "zh-Hant"


def _load(lang):
    if lang == "en":
        from .i18n_en import EN
        return EN
    return {}


LANG = config.settings.get("language") or DEFAULT
if LANG not in dict(LANGUAGES):
    LANG = DEFAULT
TABLE: dict[str, str] = _load(LANG)


def tr(text: str) -> str:
    if not TABLE:
        return text
    return TABLE.get(text, text)


_FLASH = re.compile(r"^(已擊發|未擊發)( \(0x[0-9a-f]+\))$")


def tr_value(v) -> str:
    """EXIF 值的顯示（光圈優先、權衡測光、已擊發 (0x10)…）。數字與型號照原樣。"""
    s = str(v)
    if not TABLE:
        return s
    m = _FLASH.match(s)
    if m:
        return TABLE.get(m[1], m[1]) + m[2]
    return TABLE.get(s, s)
