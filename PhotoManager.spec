# PyInstaller 設定：pyinstaller PhotoManager.spec（或直接跑 packaging\build.bat）
# 產出 dist\PhotoManager\PhotoManager.exe（資料夾版，啟動比單一 exe 快很多）
import re
from pathlib import Path

VERSION = re.search(r'"(.+)"', Path("photomanager/__init__.py").read_text()).group(1)
nums = [int(x) for x in VERSION.split(".")] + [0] * 4
nums = tuple(nums[:4])

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "PhotoManager"),
            StringStruct("FileDescription", "Photo Manager"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "PhotoManager"),
            StringStruct("OriginalFilename", "PhotoManager.exe"),
            StringStruct("ProductName", "Photo Manager"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    ["main.py"],
    datas=[("photomanager/assets", "photomanager/assets")],
    hiddenimports=["pillow_heif"],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtDBus"],
)
# 用不到的大檔：軟體 OpenGL（只畫 QWidget 不需要）、Qt 內建翻譯（介面文字是自己翻的）
DROP = ("opengl32sw.dll", "PySide6/translations/")
a.binaries = [b for b in a.binaries if not any(d in b[0].replace("\\", "/") for d in DROP)]
a.datas = [d for d in a.datas if not any(x in d[0].replace("\\", "/") for x in DROP)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PhotoManager", console=False,
          icon="packaging/PhotoManager.ico", version=version_info)
coll = COLLECT(exe, a.binaries, a.datas, name="PhotoManager")
