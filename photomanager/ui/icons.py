"""線條圖示：assets/icons/*.svg 是黑色描邊，畫的時候換成指定顏色。

同一個（名稱, 顏色, 大小, 倍率）只畫一次。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap, QGuiApplication
from PySide6.QtSvg import QSvgRenderer

from .. import config

ICON_DIR = config.ASSETS / "icons"


@lru_cache(maxsize=None)
def _svg(name: str) -> str:
    try:
        return (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    except OSError:
        return (ICON_DIR / "info.svg").read_text(encoding="utf-8")


def _tinted(name: str, color: str) -> bytes:
    return _svg(name).replace("#000", color).encode("utf-8")


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 16, dpr: float | None = None) -> QPixmap:
    if dpr is None:
        screen = QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_tinted(name, color)))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str | None = None, size: int = 16, disabled: str | None = None) -> QIcon:
    from . import theme
    ic = QIcon()
    ic.addPixmap(pixmap(name, color or theme.T["label"], size), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, disabled or theme.T["tertiary"], size), QIcon.Mode.Disabled)
    return ic


def file_url(name: str, color: str) -> str:
    """QSS 的 url() 只吃檔案路徑，所以把上好色的 SVG 寫進快取資料夾。"""
    out = config.CACHE_DIR / "icons"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}-{color.strip('#')}.svg"
    if not path.exists():
        path.write_bytes(_tinted(name, color))
    return path.as_posix()


def app_icon() -> QIcon:
    ic = QIcon()
    renderer = QSvgRenderer(str(config.ASSETS / "app-icon.svg"))
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p, QRectF(0, 0, s, s))
        p.end()
        ic.addPixmap(pm)
    return ic
