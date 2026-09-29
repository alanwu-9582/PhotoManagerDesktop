# PyInstaller 設定：pyinstaller PhotoManager.spec
block_cipher = None

a = Analysis(
    ["main.py"],
    datas=[("photomanager/assets", "photomanager/assets")],
    hiddenimports=["pillow_heif"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PhotoManager", console=False,
          icon=None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="PhotoManager")
