"""相框與色卡：EXIF 相框與照片色卡都是「把照片排成一張成品」，合在同一頁，右上角切換。"""
from __future__ import annotations


from .common import ToolGroup
from .exif_frame.page import ExifFramePage
from .palette_card.page import PaletteCardPage


class FramesPage(ToolGroup):
    title = "相框與色卡"
    members = [("frame", "EXIF 相框", ExifFramePage), ("palette", "照片色卡", PaletteCardPage)]
