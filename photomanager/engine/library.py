"""照片來源與延遲載入。

兩種來源：
  folder 模式  開一個資料夾（可含子資料夾），照片留在原地，之後可以直接搬移。
  files  模式  挑幾個檔案。桌面版有完整的檔案系統權限，所以這個模式一樣可以就地整理。

效能策略（網頁版的延遲載入再往前推一步）：
  - 背景工作執行緒 + 三條佇列：原圖 > 縮圖 > EXIF 掃描。
    縮圖佇列是「後進先出」：捲得很快的時候，畫面上現在看得到的那幾張先做，
    早就捲過去的請求自然沉到底、被丟掉。
  - 縮圖快取在磁碟上（依路徑 + 大小 + 修改時間），第二次開同一個資料夾幾乎是瞬間。
  - EXIF 也快取在磁碟上（SQLite），統計、篩選、改名不必再把幾千個檔頭讀一遍。
  - 記憶體裡的縮圖與原圖都有上限（LRU），正在畫面上的原圖會被釘住不淘汰。
"""
from __future__ import annotations

from ..i18n import tr

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QImage, QPixmap

from .. import config
from . import exif as exif_mod
from . import image as imgmod
from .categories import categories

THUMB_EDGE = 384        # 縮圖長邊（實際像素）
MAX_THUMBS = 700        # 記憶體裡同時保留的縮圖數
FULL_BUDGET = 640 << 20 # 原圖快取的記憶體上限（位元組）
MAX_DEPTH = 6           # 掃描子資料夾的最大深度


def nat_key(s: str):
    """檔名自然排序（DSC_9 排在 DSC_10 前面）。"""
    import re
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def cache_key(path: str, size: int, mtime: float) -> str:
    raw = f"{os.path.normcase(os.path.abspath(path))}|{size}|{int(mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def dhash(img: QImage) -> int:
    """差異雜湊：縮成 9×8 灰階，每一列比較左右相鄰像素。兩張照片的雜湊差幾個位元就是有多不像。"""
    small = img.scaled(9, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_Grayscale8)
    bits = 0
    for y in range(8):
        line = small.constScanLine(y)
        row = bytes(line)[:9]
        for x in range(8):
            bits = (bits << 1) | (row[x] > row[x + 1])
    return bits


# ============================================================ 資料
@dataclass(eq=False)
class Photo:
    id: int
    path: str
    name: str
    rel_path: str
    size: int = 0
    mtime: float = 0.0
    info: dict | None = None
    info_state: str = "idle"     # idle | done | error
    thumb_state: str = "idle"    # idle | loading | done | error
    cat_id: str | None = None
    organized: dict | None = None  # {"folder", "action"} 已實際搬移／複製過
    thumb_error: str = ""
    dhash: int | None = None       # 64 位元的差異雜湊，「相似照片」分組用

    @property
    def ext(self) -> str:
        dot = self.name.rfind(".")
        return self.name[dot + 1:].upper() if dot > 0 else ""

    @property
    def folder(self) -> str:
        slash = self.rel_path.rfind("/")
        return self.rel_path[:slash] if slash > 0 else ""


# ============================================================ 磁碟快取
class InfoCache:
    """EXIF 的磁碟快取。一個 SQLite 檔，鎖住就能跨執行緒共用。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str]] = []
        try:
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(config.CACHE_DIR / "exif.sqlite"), check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("CREATE TABLE IF NOT EXISTS exif (k TEXT PRIMARY KEY, v TEXT)")
        except sqlite3.Error:
            self._db = None

    def get(self, key: str):
        if not self._db:
            return None
        with self._lock:
            try:
                row = self._db.execute("SELECT v FROM exif WHERE k=?", (key,)).fetchone()
            except sqlite3.Error:
                return None
        if not row:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    def get_many(self, keys: list[str]) -> dict:
        out = {}
        if not self._db or not keys:
            return out
        with self._lock:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = "SELECT k, v FROM exif WHERE k IN (%s)" % ",".join("?" * len(chunk))
                try:
                    for k, v in self._db.execute(q, chunk):
                        try:
                            out[k] = json.loads(v)
                        except ValueError:
                            pass
                except sqlite3.Error:
                    break
        return out

    def put(self, key: str, info):
        if not self._db:
            return
        with self._lock:
            self._pending.append((key, json.dumps(info, ensure_ascii=False)))
            if len(self._pending) >= 64:
                self._flush_locked()

    def flush(self):
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self._pending or not self._db:
            return
        try:
            self._db.executemany("INSERT OR REPLACE INTO exif VALUES (?, ?)", self._pending)
            self._db.commit()
        except sqlite3.Error:
            pass
        self._pending.clear()


def thumb_cache_path(key: str):
    return config.THUMB_DIR / key[:2] / f"{key}.jpg"


# ============================================================ 背景工作
class _Relay(QObject):
    """背景執行緒丟結果回 GUI 執行緒用。信號跨執行緒時 Qt 會自動排進事件佇列。"""
    thumb = Signal(object, object)      # photo, QImage | None
    info = Signal(object)               # photo
    full = Signal(object, int, object)  # photo, max_edge, QImage | None
    hashes = Signal(object)             # (token, done, total, callback)


class Scheduler:
    """三條佇列、幾條工作執行緒。優先序：原圖 > 縮圖（後進先出）> EXIF 掃描。"""

    def __init__(self, workers: int):
        self._cv = threading.Condition()
        self.full: deque = deque()
        self.thumb: deque = deque()
        self.scan: deque = deque()
        self._stop = False
        self._threads = [threading.Thread(target=self._run, daemon=True, name=f"pm-worker-{i}")
                         for i in range(workers)]
        for t in self._threads:
            t.start()

    def push(self, queue: str, job, front=False):
        with self._cv:
            q = getattr(self, queue)
            (q.appendleft if front else q.append)(job)
            self._cv.notify()

    def clear(self, *queues):
        with self._cv:
            for name in queues or ("full", "thumb", "scan"):
                getattr(self, name).clear()

    def _take(self):
        if self.full:
            return self.full.popleft()
        if self.thumb:
            return self.thumb.pop()   # 最新的請求先做
        if self.scan:
            return self.scan.popleft()
        return None

    def _run(self):
        while True:
            with self._cv:
                job = self._take()
                while job is None and not self._stop:
                    self._cv.wait()
                    job = self._take()
                if self._stop:
                    return
            try:
                job()
            except Exception as e:  # noqa: BLE001 - 單一張壞掉不能讓工作執行緒死掉
                print("worker error:", e)


# ============================================================ 照片庫
class Library(QObject):
    changed = Signal()                 # 來源換了、標記變了：頁面重畫
    photo_updated = Signal(object)     # 某一張的縮圖或 EXIF 到了
    stats_changed = Signal()
    progress = Signal(int, int, str)   # done, total, label；total = 0 表示純文字
    progress_done = Signal()
    full_ready = Signal(object, int)   # photo, max_edge

    def __init__(self):
        super().__init__()
        self.mode: str | None = None   # folder | files | None
        self.root: str | None = None
        self.root_name = ""
        self.recursive = True
        self.photos: list[Photo] = []
        self._by_id: dict[int, Photo] = {}
        self._seq = 0
        self._gen = 0                  # 換來源就 +1，舊的背景結果直接丟掉

        self.info_cache = InfoCache()
        self._thumbs: OrderedDict[int, QPixmap] = OrderedDict()
        self._thumb_wanted: set[int] = set()
        self._full: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._full_bytes = 0
        self._full_wanted: set[tuple[int, int]] = set()
        self._pinned: set[int] = set()
        self._scan_token = None

        self._relay = _Relay()
        self._relay.thumb.connect(self._on_thumb, Qt.ConnectionType.QueuedConnection)
        self._relay.info.connect(self._on_info, Qt.ConnectionType.QueuedConnection)
        self._relay.full.connect(self._on_full, Qt.ConnectionType.QueuedConnection)
        self._relay.hashes.connect(self._on_hashes, Qt.ConnectionType.QueuedConnection)
        n = max(2, min(6, (os.cpu_count() or 4) - 1))
        self.sched = Scheduler(n)
        self._marks_timer = None

    # ---------------------------------------------------------------- 查詢
    def by_id(self, pid) -> Photo | None:
        return self._by_id.get(pid)

    def index_of(self, photo: Photo) -> int:
        try:
            return self.photos.index(photo)
        except ValueError:
            return -1

    def stats(self):
        total = len(self.photos)
        analysed = marked = organized = size = 0
        for p in self.photos:
            if p.info_state != "idle":
                analysed += 1
            if p.cat_id:
                marked += 1
            if p.organized:
                organized += 1
            size += p.size or 0
        return {"total": total, "analysed": analysed, "marked": marked, "organized": organized, "bytes": size}

    # ---------------------------------------------------------------- 來源
    def _make(self, path: str, rel: str, st=None) -> Photo:
        self._seq += 1
        p = Photo(self._seq, path, os.path.basename(path), rel.replace("\\", "/"))
        if st is not None:
            p.size, p.mtime = st.st_size, st.st_mtime
        return p

    def clear(self):
        self.save_marks_now()
        self._gen += 1
        self.cancel_scan()
        self.sched.clear()
        self.photos = []
        self._by_id = {}
        self._thumbs.clear()
        self._thumb_wanted.clear()
        self._full.clear()
        self._full_bytes = 0
        self._full_wanted.clear()
        self._pinned.clear()
        self.mode = None
        self.root = None
        self.root_name = ""

    def scan_folder(self, root: str, recursive: bool, skip: set[str], on_progress=None) -> list[Photo]:
        """在背景執行緒呼叫。只列檔案、讀 stat，不碰內容。"""
        out: list[Photo] = []
        root = os.path.abspath(root)

        def walk(d: str, prefix: str, depth: int):
            try:
                it = os.scandir(d)
            except OSError:
                return
            with it:
                entries = sorted(it, key=lambda e: nat_key(e.name))
            for e in entries:
                try:
                    if e.is_file():
                        if not imgmod.is_library_file(e.name):
                            continue
                        out.append(self._make(e.path, prefix + e.name, e.stat()))
                        if on_progress and len(out) % 250 == 0:
                            on_progress(len(out))
                    elif e.is_dir() and depth > 0:
                        if e.name.startswith(".") or e.name in skip:
                            continue
                        walk(e.path, prefix + e.name + "/", depth - 1)
                except OSError:
                    continue

        walk(root, "", MAX_DEPTH if recursive else 0)
        out.sort(key=lambda p: nat_key(p.rel_path))
        return out

    def set_folder(self, root: str, photos: list[Photo], recursive: bool):
        self.clear()
        self.mode = "folder"
        self.root = os.path.abspath(root)
        self.root_name = os.path.basename(self.root.rstrip("\\/")) or self.root
        self.recursive = recursive
        self.photos = photos
        self._by_id = {p.id: p for p in photos}
        self._hydrate_info(photos)
        restored = self.restore_marks()
        config.settings.remember_folder(self.root)
        self.changed.emit()
        self.stats_changed.emit()
        return restored

    def add_files(self, paths: list[str]) -> list[Photo]:
        accepted = [os.path.abspath(p) for p in paths if imgmod.is_library_file(p) and os.path.isfile(p)]
        if not accepted:
            return []
        if self.mode != "files":
            self.clear()
            self.mode = "files"
            self.root_name = tr("已選擇的檔案")
        known = {os.path.normcase(p.path) for p in self.photos}
        added = []
        for path in accepted:
            if os.path.normcase(path) in known:
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            added.append(self._make(path, os.path.basename(path), st))
        self.photos = sorted(self.photos + added, key=lambda p: nat_key(p.rel_path))
        self._by_id.update({p.id: p for p in added})
        self._hydrate_info(added)
        self.changed.emit()
        self.stats_changed.emit()
        return added

    def _hydrate_info(self, photos: list[Photo]):
        """從磁碟快取把讀過的 EXIF 直接補上。幾千張也只是一次查詢。"""
        keys = {cache_key(p.path, p.size, p.mtime): p for p in photos}
        for k, info in self.info_cache.get_many(list(keys)).items():
            p = keys[k]
            p.info = info
            p.info_state = "done" if info else "error"

    # ---------------------------------------------------------------- 標記
    def restore_marks(self) -> int:
        if self.mode != "folder" or not self.root:
            return 0
        data = config.read_json(config.marks_path(self.root), {}) or {}
        n = 0
        for p in self.photos:
            cid = data.get(p.rel_path)
            if cid and categories.by_id(cid):
                p.cat_id = cid
                n += 1
        return n

    def save_marks_now(self):
        if self.mode != "folder" or not self.root:
            return
        data = {p.rel_path: p.cat_id for p in self.photos if p.cat_id and not p.organized}
        path = config.marks_path(self.root)
        if data:
            config.write_json(path, data)
        else:
            try:
                path.unlink()
            except OSError:
                pass

    def save_marks(self):
        """標記改了。連續按鍵時合併成一次寫入。"""
        from PySide6.QtCore import QTimer
        if self._marks_timer is None:
            self._marks_timer = QTimer(self)
            self._marks_timer.setSingleShot(True)
            self._marks_timer.setInterval(300)
            self._marks_timer.timeout.connect(self.save_marks_now)
        self._marks_timer.start()
        self.stats_changed.emit()

    def drop_missing_categories(self):
        dropped = 0
        for p in self.photos:
            if p.cat_id and not categories.by_id(p.cat_id):
                p.cat_id = None
                dropped += 1
        if dropped:
            self.save_marks()
        return dropped

    # ---------------------------------------------------------------- EXIF
    def _read_info(self, p: Photo, gen: int):
        try:
            st = os.stat(p.path)
            p.size, p.mtime = st.st_size, st.st_mtime
            info, _ = exif_mod.extract_exif(p.path)
        except Exception:  # noqa: BLE001
            info = None
        if gen != self._gen:
            return
        p.info = info
        p.info_state = "done" if info else "error"
        self.info_cache.put(cache_key(p.path, p.size, p.mtime), info)

    def read_info_now(self, p: Photo, want_raw=False):
        """同步讀一次（完整資訊視窗用，含 Raw EXIF）。"""
        try:
            info, _ = exif_mod.extract_exif(p.path, want_raw=want_raw)
        except Exception:  # noqa: BLE001
            info = None
        if info and p.info_state != "done":
            p.info = {k: v for k, v in info.items() if k != "raw"}
            p.info_state = "done"
        return info

    def scan_all_info(self):
        """把還沒讀過 EXIF 的整批讀完，用 progress 信號回報。"""
        todo = [p for p in self.photos if p.info_state == "idle"]
        if not todo or self._scan_token:
            return False
        token = self._scan_token = object()
        gen = self._gen
        total = len(todo)
        counter = {"n": 0}
        lock = threading.Lock()

        def job(p):
            def run():
                if self._scan_token is not token:
                    return
                if p.info_state == "idle":
                    self._read_info(p, gen)
                with lock:
                    counter["n"] += 1
                    n = counter["n"]
                if n % 20 == 0 or n == total:
                    self._relay.info.emit((token, n, total))
            return run

        for p in todo:
            self.sched.push("scan", job(p))
        self.progress.emit(0, total, tr("分析 EXIF"))
        return True

    def scan_hashes(self, on_done=None):
        """替還沒有相似度雜湊的照片補算（有縮圖快取就讀快取，沒有就用 DCT 縮放解一張 64px 的小圖）。"""
        todo = [p for p in self.photos if p.dhash is None]
        if not todo:
            if on_done:
                on_done()
            return False
        gen = self._gen
        total = len(todo)
        counter = {"n": 0}
        lock = threading.Lock()
        token = object()
        self._hash_token = token

        def job(p):
            def run():
                if gen != self._gen or self._hash_token is not token:
                    return
                if p.dhash is None:
                    img = None
                    try:
                        st = os.stat(p.path)
                        cached = thumb_cache_path(cache_key(p.path, st.st_size, st.st_mtime))
                        if cached.exists():
                            img = QImage(str(cached))
                        if img is None or img.isNull():
                            img = imgmod.decode(p.path, 64)
                        p.dhash = dhash(img)
                    except Exception:  # noqa: BLE001
                        p.dhash = 0
                with lock:
                    counter["n"] += 1
                    n = counter["n"]
                if n % 25 == 0 or n == total:
                    self._relay.hashes.emit((token, n, total, on_done))
            return run

        for p in todo:
            self.sched.push("scan", job(p))
        self.progress.emit(0, total, tr("比對相似照片"))
        return True

    def _on_hashes(self, payload):
        token, n, total, on_done = payload
        if token is not getattr(self, "_hash_token", None):
            return
        self.progress.emit(n, total, tr("比對相似照片"))
        if n >= total:
            self._hash_token = None
            self.progress_done.emit()
            if on_done:
                on_done()

    def cancel_scan(self):
        if self._scan_token:
            self._scan_token = None
            self.sched.clear("scan")
            self.info_cache.flush()
            self.progress_done.emit()
            self.stats_changed.emit()

    @property
    def scanning(self):
        return self._scan_token is not None

    def _on_info(self, payload):
        if isinstance(payload, tuple):
            token, n, total = payload
            if token is not self._scan_token:
                return
            self.progress.emit(n, total, tr("分析 EXIF"))
            self.stats_changed.emit()
            if n >= total:
                self._scan_token = None
                self.info_cache.flush()
                self.progress_done.emit()
                self.changed.emit()
            return
        self.photo_updated.emit(payload)

    # ---------------------------------------------------------------- 縮圖
    def thumb(self, p: Photo) -> QPixmap | None:
        pm = self._thumbs.get(p.id)
        if pm is not None:
            self._thumbs.move_to_end(p.id)
        return pm

    def request_thumb(self, p: Photo):
        """畫面上需要這張的縮圖。已經有就直接回；在排隊就不重排。"""
        if p.id in self._thumbs or p.id in self._thumb_wanted or p.thumb_state == "error":
            return
        self._thumb_wanted.add(p.id)
        p.thumb_state = "loading"
        gen = self._gen
        self.sched.push("thumb", lambda: self._make_thumb(p, gen))

    def _make_thumb(self, p: Photo, gen: int):
        if gen != self._gen:
            return
        img = None
        try:
            st = os.stat(p.path)
            p.size, p.mtime = st.st_size, st.st_mtime
        except OSError as e:
            p.thumb_error = str(e)
            self._relay.thumb.emit(p, None)
            return
        key = cache_key(p.path, p.size, p.mtime)
        if p.info_state == "idle":
            self._read_info(p, gen)
        cached = thumb_cache_path(key)
        if cached.exists():
            img = QImage(str(cached))
            if img.isNull():
                img = None
        if img is None:
            try:
                img = imgmod.decode(p.path, THUMB_EDGE)
            except Exception as e:  # noqa: BLE001
                p.thumb_error = str(e)
                img = self._exif_thumb(p)
            if img is not None and not img.isNull():
                try:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    img.save(str(cached), "JPG", 82)
                except OSError:
                    pass
        if img is not None and not img.isNull() and p.dhash is None:
            p.dhash = dhash(img)
        if gen == self._gen:
            self._relay.thumb.emit(p, img)

    @staticmethod
    def _exif_thumb(p: Photo):
        """原圖解不開（例如沒有 HEIC 解碼器）時，退回相機內嵌的小縮圖。"""
        try:
            _, data = exif_mod.extract_exif(p.path, want_thumb=True)
        except Exception:  # noqa: BLE001
            return None
        if not data:
            return None
        img = QImage.fromData(data)
        if img.isNull():
            return None
        o = (p.info or {}).get("orientation")
        from PySide6.QtGui import QTransform
        rot = {3: 180, 6: 90, 8: 270}.get(o)
        return img.transformed(QTransform().rotate(rot)) if rot else img

    def _on_thumb(self, p: Photo, img):
        self._thumb_wanted.discard(p.id)
        if self._by_id.get(p.id) is not p:
            return
        if img is None or img.isNull():
            p.thumb_state = "error"
        else:
            p.thumb_state = "done"
            self._thumbs[p.id] = QPixmap.fromImage(img)
            while len(self._thumbs) > MAX_THUMBS:
                old, _ = self._thumbs.popitem(last=False)
                q = self._by_id.get(old)
                if q:
                    q.thumb_state = "idle"
        self.photo_updated.emit(p)
        self.stats_changed.emit()

    def drop_thumb_queue(self):
        """換頁／大幅捲動時，把還沒開始做的縮圖請求整批丟掉。"""
        self.sched.clear("thumb")
        for pid in list(self._thumb_wanted):
            q = self._by_id.get(pid)
            if q and q.thumb_state == "loading":
                q.thumb_state = "idle"
        self._thumb_wanted.clear()

    # ---------------------------------------------------------------- 原圖
    def full(self, p: Photo, max_edge: int) -> QImage | None:
        """已經解好的原圖（或更大的版本）。沒有就回 None，呼叫端再 request_full。"""
        best = None
        for (pid, edge), img in self._full.items():
            if pid == p.id and (edge == 0 or edge >= max_edge) and (best is None or edge < best[0]):
                best = (edge, (pid, edge))
        if best:
            self._full.move_to_end(best[1])
            return self._full[best[1]]
        return None

    def full_any(self, p: Photo) -> QImage | None:
        """手上有的最大那一張，不管夠不夠大（先頂著用）。"""
        best = None
        for (pid, edge), img in self._full.items():
            if pid == p.id:
                score = edge or 1 << 30
                if best is None or score > best[0]:
                    best = (score, img)
        return best[1] if best else None

    def request_full(self, p: Photo, max_edge: int, front=True):
        key = (p.id, max_edge)
        if key in self._full_wanted or self.full(p, max_edge) is not None:
            return
        self._full_wanted.add(key)
        gen = self._gen

        def run():
            if gen != self._gen:
                return
            try:
                img = imgmod.decode(p.path, max_edge or None)
            except Exception:  # noqa: BLE001
                img = None
            if gen == self._gen:
                self._relay.full.emit(p, max_edge, img)

        self.sched.push("full", run, front=front)

    def _on_full(self, p: Photo, max_edge: int, img):
        self._full_wanted.discard((p.id, max_edge))
        if self._by_id.get(p.id) is not p or img is None or img.isNull():
            self.full_ready.emit(p, -1)
            return
        key = (p.id, max_edge)
        self._full[key] = img
        self._full_bytes += img.sizeInBytes()
        self._evict_full()
        self.full_ready.emit(p, max_edge)

    def pin_full(self, ids):
        self._pinned = {i for i in ids if i}
        self._evict_full()

    def _evict_full(self):
        while self._full_bytes > FULL_BUDGET:
            victim = next((k for k in self._full if k[0] not in self._pinned), None)
            if victim is None:
                break
            self._full_bytes -= self._full.pop(victim).sizeInBytes()

    def preload_around(self, index: int, ahead: int, behind: int, max_edge: int, pool=None):
        pool = pool if pool is not None else self.photos
        for d in [*range(1, ahead + 1), *(-i for i in range(1, behind + 1))]:
            i = index + d
            if 0 <= i < len(pool):
                self.request_full(pool[i], max_edge, front=False)

    # ---------------------------------------------------------------- 更名後
    def relocated(self, p: Photo, new_path: str, new_rel: str):
        """檔案被搬走或改名了：換路徑，縮圖留著（內容沒變）。"""
        p.path = new_path
        p.name = os.path.basename(new_path)
        p.rel_path = new_rel

    def shutdown(self):
        self.save_marks_now()
        self.info_cache.flush()


# QObject 不需要 QApplication 就能建立；QPixmap 只會在 GUI 執行緒的槽裡才產生。
library = Library()
