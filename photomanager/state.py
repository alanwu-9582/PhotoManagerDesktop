"""跨頁共用的畫面狀態（對應網頁版 js/app/state.js）。

頁面切換時不會被重建，但「選了哪些 EXIF 欄位」「每排幾張」「現在選到哪一張」
這些是使用者的選擇，放在這裡讓每一頁都讀得到同一份。
"""
from __future__ import annotations


from PySide6.QtCore import QObject, Signal

from . import config

FIELD_DEFS = [
    ("model", "相機型號", True),
    ("lensModel", "鏡頭", True),
    ("focalLength", "焦段", True),
    ("fNumber", "光圈", True),
    ("exposureTime", "快門", True),
    ("iso", "ISO", True),
    ("exposureBias", "曝光補償", False),
    ("exposureProgram", "曝光模式", False),
    ("meteringMode", "測光模式", False),
    ("whiteBalance", "白平衡", False),
    ("flash", "閃光燈", False),
    ("colorSpace", "色域", False),
    ("focalLength35mm", "35mm等效焦段", False),
    ("sceneCaptureType", "場景類型", False),
    ("dateTimeOriginal", "拍攝時間", False),
    ("make", "製造商", False),
    ("creativeStyle", "創意風格 (Sony)", True),
]


class AppState(QObject):
    fields_changed = Signal()
    filters_changed = Signal()
    editor_changed = Signal()

    def __init__(self):
        super().__init__()
        saved = config.settings.get("fields")
        self.fields = {k: (saved.get(k, on) if isinstance(saved, dict) else on) for k, _, on in FIELD_DEFS}
        self.columns = int(config.settings.get("columns") or 4)
        self.manage_columns = int(config.settings.get("manageColumns") or 3)
        self.hide_done = False
        # 照片檢視的篩選條件: [{"field", "value", "mode": "include" | "exclude"}]
        self.filters: list[dict] = []
        self.selected_id: int | None = None
        # 編輯工具之間共用的目前照片: {"path", "photo_id"}；外部檔案的 photo_id 是 None。
        self.editor_photo: dict | None = None
        self.editor_version = 0

    def set_field(self, key, on):
        self.fields[key] = on
        config.settings.set("fields", dict(self.fields))
        self.fields_changed.emit()

    def enabled_fields(self):
        return [(k, label) for k, label, _ in FIELD_DEFS if self.fields.get(k)]

    def set_editor_photo(self, path: str | None, photo_id: int | None = None, image=None, origin=None, source=None,
                         stashed=False):
        """image / origin / source：某個編輯工具存下來的結果（圖本身、原始照片路徑、哪個工具）。
        stashed：只是暫存（沒有寫成檔案），path 仍是原本那張的路徑。"""
        self.editor_photo = ({"path": path, "photo_id": photo_id, "image": image, "origin": origin or path,
                              "source": source, "stashed": stashed} if path else None)
        self.editor_version += 1
        self.editor_changed.emit()


state = AppState()


def fmt_bytes(n) -> str:
    if not n:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    v = float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:.0f} {units[i]}" if i == 0 else f"{v:.1f} {units[i]}"
