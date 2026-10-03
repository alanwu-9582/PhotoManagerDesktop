"""這次工作階段的操作紀錄。

搬移、複製、標記、改名、輸出、切換分類設定 —— 做過什麼都記一筆，方便確認剛才對哪些照片
做了什麼。只記在記憶體裡（「這次」），關掉程式就沒了。

連續的標記會併成同一筆（一路按 1 2 3 下去不會洗版），兩分鐘沒標記才開新的一筆。
"""
from __future__ import annotations


import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

KIND_LABEL = {"organize": "整理", "mark": "標記", "rename": "改名", "export": "輸出",
              "zip": "打包", "settings": "設定"}
MERGE_SECONDS = 120


@dataclass
class Entry:
    kind: str
    title: str
    items: list = field(default_factory=list)      # [(照片, 說明)]
    failed: list = field(default_factory=list)     # [(照片, 錯誤)]
    at: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    @property
    def label(self):
        return KIND_LABEL.get(self.kind, self.kind)


class History(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.entries: list[Entry] = []

    def add(self, kind, title, items=(), failed=()):
        e = Entry(kind, title, list(items), list(failed))
        self.entries.append(e)
        self.changed.emit()
        return e

    def mark(self, name: str, category: str | None):
        """一張照片被標記（category=None 是清除）。連續的標記併成一筆。"""
        now = time.time()
        text = f"→ {category}" if category else "清除標記"
        last = self.entries[-1] if self.entries else None
        if last and last.kind == "mark" and now - last.updated < MERGE_SECONDS:
            # 同一張改來改去只留最後一次
            last.items = [it for it in last.items if it[0] != name] + [(name, text)]
            last.updated = now
            last.title = f'標記 {len(last.items)} 張'
        else:
            self.entries.append(Entry("mark", "標記 1 張", [(name, text)]))
        self.changed.emit()

    def clear(self):
        self.entries.clear()
        self.changed.emit()

    def as_text(self) -> str:
        lines = []
        for e in self.entries:
            lines.append(f"{time.strftime('%H:%M:%S', time.localtime(e.at))}  [{e.label}]  {e.title}")
            lines += [f"    {a}  {b}" for a, b in e.items]
            lines += [f"    ✕ {a}  {b}" for a, b in e.failed]
        return "\n".join(lines)


history = History()
