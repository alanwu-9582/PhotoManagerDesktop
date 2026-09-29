"""全程式唯一的顏色、字體、尺寸來源（對應網頁版的 css/theme.css）。

配色照 Apple Human Interface Guidelines 的系統色：淺色／深色兩套，跟著作業系統切換。
強調色用 systemIndigo —— 跟原本品牌的紫灰同一個色相，但對比度符合 HIG 對按鈕與連結的要求。
自己畫的元件（縮圖牆、圖表、檢視器）在 paintEvent 裡讀 T[...]，所以換主題不必重建。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "window": "#f5f5f7",
    "content": "#ffffff",
    "sidebar": "#ebebef",
    "elevated": "#ffffff",
    "control": "#ffffff",
    "control_hover": "#f2f2f5",
    "fill": "rgba(120,120,128,0.12)",      # systemFill（次要按鈕、分段控制的底）
    "fill_strong": "rgba(120,120,128,0.20)",
    "fill_hex": "#e9e9ee",
    "label": "#1d1d1f",
    "secondary": "#6e6e73",
    "tertiary": "#a1a1a6",
    "separator": "rgba(0,0,0,0.10)",
    "separator_hex": "#e0e0e5",
    "accent": "#5856d6",
    "accent_hover": "#4b49c8",
    "accent_soft": "rgba(88,86,214,0.14)",
    "on_accent": "#ffffff",
    "red": "#ff3b30", "orange": "#ff9500", "yellow": "#ffcc00", "green": "#34c759",
    "teal": "#30b0c7", "blue": "#007aff", "purple": "#af52de", "gray": "#8e8e93",
    "photo_bg": "#e8e8ed",                  # 縮圖還沒到時的底
    "viewer_bg": "#1c1c1e",                 # 看照片的地方一律深色，照片的顏色才不會被襯偏
    "shadow": "rgba(0,0,0,0.12)",
    "selection": "#5856d6",
    "slider_thumb": "#6c6ae0",
    "slider_thumb_hover": "#4b49c8",
}

DARK = {
    "window": "#1c1c1e",
    "content": "#232325",
    "sidebar": "#28282b",
    "elevated": "#2c2c2e",
    "control": "#3a3a3c",
    "control_hover": "#444447",
    "fill": "rgba(120,120,128,0.24)",
    "fill_strong": "rgba(120,120,128,0.36)",
    "fill_hex": "#38383b",
    "label": "#f5f5f7",
    "secondary": "#a1a1a6",
    "tertiary": "#6e6e73",
    "separator": "rgba(255,255,255,0.10)",
    "separator_hex": "#3a3a3d",
    "accent": "#7d7aff",
    "accent_hover": "#8f8cff",
    "accent_soft": "rgba(125,122,255,0.22)",
    "on_accent": "#ffffff",
    "red": "#ff453a", "orange": "#ff9f0a", "yellow": "#ffd60a", "green": "#30d158",
    "teal": "#40c8e0", "blue": "#0a84ff", "purple": "#bf5af2", "gray": "#98989d",
    "photo_bg": "#2c2c2e",
    "viewer_bg": "#111113",
    "shadow": "rgba(0,0,0,0.45)",
    "selection": "#7d7aff",
    "slider_thumb": "#b6b3f5",
    "slider_thumb_hover": "#d4d2fb",
}

T: dict[str, str] = dict(DARK)
IS_DARK = True

if sys.platform == "darwin":
    FONT_FAMILIES = [".AppleSystemUIFont", "PingFang TC", "Helvetica Neue"]
    MONO_FAMILIES = ["SF Mono", "Menlo"]
elif sys.platform == "win32":
    # Segoe UI（拉丁）+ 微軟正黑體 UI（中文）：兩套都是為螢幕做過 hinting 的靜態字型，
    # 小字級也是清楚的；可變字型（Segoe UI Variable、Noto Sans TC VF）在 Qt 裡沒有 hinting，會糊。
    FONT_FAMILIES = ["Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei"]
    MONO_FAMILIES = ["Cascadia Mono", "Consolas"]
else:
    FONT_FAMILIES = ["Inter", "Noto Sans CJK TC", "Noto Sans", "sans-serif"]
    MONO_FAMILIES = ["JetBrains Mono", "DejaVu Sans Mono"]

# HIG 字級（macOS 的階層，放大一點點以配合 Windows 的字體渲染）
CONTROL_H = 30   # 同一列的控制項（按鈕、下拉、輸入框、分段控制、開關…）一律這麼高

SIZE = {"large": 26, "title2": 18, "title3": 16, "headline": 14, "body": 14, "callout": 13, "caption": 12}


def radius(w: float, h: float, r: float) -> float:
    """圓角半徑一律小於自己最短邊的 1/3。"""
    return max(0.0, min(r, min(w, h) / 3 - 0.01))


def round_rect(path, rect, r):
    """addRoundedRect，但半徑自動收在短邊的 1/3 以內。"""
    v = radius(rect.width(), rect.height(), r)
    path.addRoundedRect(rect, v, v)
    return path


def c(key: str) -> QColor:
    """把 token 轉成 QColor（支援 rgba(...) 寫法）。"""
    v = T[key]
    if v.startswith("rgba"):
        r, g, b, a = [x.strip() for x in v[5:-1].split(",")]
        col = QColor(int(r), int(g), int(b))
        col.setAlphaF(float(a))
        return col
    return QColor(v)


def font(size: str | int = "body", weight: int = 400, mono=False) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES if mono else FONT_FAMILIES)
    f.setPixelSize(SIZE[size] if isinstance(size, str) else size)
    f.setWeight(QFont.Weight(weight))
    # 完整 hinting：字形貼齊像素格，Windows 上才會是銳利的（不設的話 DirectWrite 會糊成一片灰）。
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def _families_css(fams):
    return ", ".join(f'"{f}"' for f in fams)


def stylesheet() -> str:
    t = T
    fam = _families_css(FONT_FAMILIES)
    mono = _families_css(MONO_FAMILIES)
    return f"""
* {{ font-family: {fam}; font-size: {SIZE['body']}px; color: {t['label']}; }}
QMainWindow, #Root {{ background: {t['window']}; }}
QProgressBar {{ background: {t['fill_strong']}; border: none; border-radius: 1px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 1px; }}
QToolTip {{ background: {t['elevated']}; color: {t['label']}; border: 1px solid {t['separator_hex']};
           padding: 4px 8px; border-radius: 5px; }}
QLabel {{ background: transparent; }}
QLabel[role="large"] {{ font-size: {SIZE['large']}px; font-weight: 700; }}
QLabel[role="title2"] {{ font-size: {SIZE['title2']}px; font-weight: 600; }}
QLabel[role="title3"] {{ font-size: {SIZE['title3']}px; font-weight: 600; }}
QLabel[role="headline"] {{ font-weight: 600; }}
QLabel[role="secondary"] {{ color: {t['secondary']}; }}
QLabel[role="caption"] {{ color: {t['secondary']}; font-size: {SIZE['caption']}px; }}
QLabel[role="section"] {{ color: {t['secondary']}; font-size: {SIZE['caption']}px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: {mono}; }}
QLabel[role="danger"] {{ color: {t['red']}; }}

/* ---------- 按鈕：主要（實心強調色）、次要（灰底）、無框、危險 ---------- */
QPushButton, QToolButton {{
    background: {t['fill']}; border: none; border-radius: 7px;
    padding: 5px 12px; min-height: 20px; max-height: 20px; color: {t['label']};
}}
QPushButton:hover, QToolButton:hover {{ background: {t['fill_strong']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t['fill_strong']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['tertiary']}; background: {t['fill']}; }}
QPushButton:focus {{ outline: none; }}
QPushButton[kind="primary"] {{ background: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t['accent_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {t['fill']}; color: {t['tertiary']}; }}
QPushButton[kind="destructive"] {{ background: {t['red']}; color: white; font-weight: 600; }}
QPushButton[kind="plain"], QToolButton[kind="plain"] {{ background: transparent; color: {t['accent']}; }}
QPushButton[kind="plain"]:hover, QToolButton[kind="plain"]:hover {{ background: {t['fill']}; }}
QPushButton[kind="danger-plain"] {{ background: transparent; color: {t['red']}; }}
QPushButton[kind="danger-plain"]:hover {{ background: {t['fill']}; }}
QPushButton[kind="large"] {{ padding: 8px 16px; font-weight: 600; border-radius: 9px; }}
QToolButton[kind="icon"] {{ background: transparent; padding: 4px; border-radius: 5px;
    min-height: 22px; max-height: 22px; min-width: 22px; }}
QToolButton[kind="icon"]:hover {{ background: {t['fill']}; }}
QToolButton[kind="icon"]:checked {{ background: {t['accent_soft']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}

/* ---------- 輸入 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {t['control']}; border: 1px solid {t['separator_hex']}; border-radius: 7px;
    padding: 4px 8px; selection-background-color: {t['accent']}; selection-color: white;
}}
QLineEdit, QSpinBox, QDoubleSpinBox {{ min-height: 20px; max-height: 20px; }}
QLineEdit:disabled, QLineEdit:read-only[muted="true"] {{ color: {t['secondary']}; background: {t['fill']}; border-color: transparent; }}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 2px solid {t['accent']}; padding: 3px 7px; }}
QLineEdit[mono="true"] {{ font-family: {mono}; }}
QLineEdit[invalid="true"] {{ border: 2px solid {t['red']}; padding: 3px 7px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QComboBox {{
    background: {t['control']}; border: 1px solid {t['separator_hex']}; border-radius: 7px;
    padding: 4px 26px 4px 9px; min-height: 20px; max-height: 20px;
}}
QComboBox:hover {{ background: {t['control_hover']}; }}
QComboBox:disabled {{ color: {t['tertiary']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url(__CHEVRON__); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {t['elevated']}; border: 1px solid {t['separator_hex']}; border-radius: 8px;
    padding: 4px; outline: none; selection-background-color: {t['accent']}; selection-color: white;
}}
/* 滑桿照原本 photoManager：軌道 8px 高、圓角 2px；滑塊 10×22 的直立方塊、圓角 3px ——
   兩個都在自己短邊的 1/3 以內。 */
QSlider {{ min-height: 30px; max-height: 30px; }}
QSlider::groove:horizontal {{ height: 8px; border-radius: 2px; background: {t['fill_strong']}; }}
QSlider::sub-page:horizontal {{ height: 8px; border-radius: 2px; background: {t['accent']}; }}
QSlider::handle:horizontal {{ width: 10px; height: 22px; margin: -7px 0; border-radius: 3px;
    background: {t['slider_thumb']}; }}
QSlider::handle:horizontal:hover {{ background: {t['slider_thumb_hover']}; }}
QSlider:disabled {{ opacity: 0.5; }}

/* ---------- 捲軸：細、只有滑過才明顯（跟 macOS 的疊加式捲軸一樣） ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['fill_strong']}; border-radius: 1px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {t['tertiary']}; margin: 0; border-radius: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['fill_strong']}; border-radius: 1px; min-width: 36px; margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QListView, QTableView, QTreeView {{ background: transparent; border: none; outline: none; }}
QHeaderView::section {{ background: transparent; border: none; border-bottom: 1px solid {t['separator_hex']};
    color: {t['secondary']}; font-weight: 600; padding: 6px 8px; font-size: {SIZE['caption']}px; }}
QTableView {{ gridline-color: transparent; selection-background-color: {t['accent_soft']}; selection-color: {t['label']}; }}

QMenu {{ background: {t['elevated']}; border: 1px solid {t['separator_hex']}; border-radius: 9px; padding: 5px; }}
QMenu::item {{ padding: 5px 22px 5px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['accent']}; color: white; }}
QMenu::item:disabled {{ color: {t['tertiary']}; }}
QMenu::separator {{ height: 1px; background: {t['separator_hex']}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 6px; }}

QSplitter::handle {{ background: transparent; }}
QDialog {{ background: {t['window']}; }}

/* ---------- 版面上的面 ---------- */
#Sidebar {{ background: {t['sidebar']}; border-right: 1px solid {t['separator_hex']}; }}
#Content {{ background: {t['window']}; }}
#StatusBar {{ background: {t['window']}; border-top: 1px solid {t['separator_hex']}; }}
#SourceBar {{ background: transparent; }}
QFrame[card="true"] {{ background: {t['content']}; border: 1px solid {t['separator_hex']}; border-radius: 12px; }}
QFrame[group="true"] {{ background: {t['content']}; border-radius: 10px; border: 1px solid {t['separator_hex']}; }}
QFrame[hairline="true"] {{ background: {t['separator_hex']}; max-height: 1px; min-height: 1px; border: none; }}
QFrame[vline="true"] {{ background: {t['separator_hex']}; max-width: 1px; min-width: 1px; border: none; }}
#Stage {{ background: {t['viewer_bg']}; border-radius: 12px; }}
#Stage[over="true"] {{ border: 2px dashed {t['accent']}; }}
#Banner {{ border-radius: 10px; padding: 10px 12px; }}
#Banner[tone="ok"] {{ background: rgba(52,199,89,0.14); }}
#Banner[tone="error"] {{ background: rgba(255,59,48,0.14); }}
"""


def apply(app: QApplication, dark: bool):
    global IS_DARK
    IS_DARK = dark
    T.clear()
    T.update(DARK if dark else LIGHT)

    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: "window", QPalette.ColorRole.Base: "control",
        QPalette.ColorRole.AlternateBase: "content", QPalette.ColorRole.Text: "label",
        QPalette.ColorRole.WindowText: "label", QPalette.ColorRole.Button: "control",
        QPalette.ColorRole.ButtonText: "label", QPalette.ColorRole.Highlight: "accent",
        QPalette.ColorRole.HighlightedText: "on_accent", QPalette.ColorRole.ToolTipBase: "elevated",
        QPalette.ColorRole.ToolTipText: "label", QPalette.ColorRole.PlaceholderText: "tertiary",
        QPalette.ColorRole.Link: "accent",
    }
    for role, key in roles.items():
        pal.setColor(role, c(key))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, c("tertiary"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, c("tertiary"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c("tertiary"))
    app.setPalette(pal)

    from . import icons
    css = stylesheet()
    css = css.replace("__CHEVRON__", icons.file_url("chevron-down", T["secondary"]))
    css = css.replace("__CHECK__", icons.file_url("check", "#ffffff"))
    app.setStyleSheet(css)
    app.setFont(font("body"))


def system_is_dark(app: QApplication) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return app.palette().window().color().lightness() < 128
