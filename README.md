# Photo Manager Desktop

參考 [wayne-1211/photoManager](https://github.com/wayne-1211/photoManager) 製作的桌面重製版。

以 PySide6 開發，直接讀取及整理本機照片；照片不會上傳，分類時會在原位置將檔案移至對應的子資料夾。支援 JPEG、TIFF、HEIC／HEIF。

## 功能

- 依影像相似度或連拍時間自動分組，方便從相近照片中挑選成品。
- 以分類設定檔保存不同情境的分類、顏色、動作與快捷鍵，並可一次套用整理結果。
- 編輯工具可無損接力：裁切旋轉、EXIF 相框、照片色卡與景深模糊的結果能直接傳到下一個工具，避免重複 JPEG 壓縮。
- 景深模糊可使用 Depth Anything V2 模型分析遠近，並用深度範圍選擇對焦區域；對焦範圍外的模糊量會隨深度差逐漸增加（前景比背景糊得快），接近真實鏡頭的效果。模型需另行下載。
- 設定 → 外觀與快取 裡有網頁版的連結：https://wayne-1211.github.io/photoManager/
- 縮圖採虛擬捲動，EXIF 與縮圖會快取至本機，適合瀏覽大量照片。

## 安裝與執行

需要 Python 3.10 以上。

```bash
pip install -r requirements.txt
python main.py
```

也可以指定資料夾或照片：

```bash
python main.py D:\Photos\2026-09
```

Windows 可直接執行 `run.bat`。

若要打包成應用程式（需要 PyInstaller 與 [Inno Setup 6](https://jrsoftware.org/isinfo.php)，後者可用 `winget install JRSoftware.InnoSetup` 安裝）：

```bash
packaginguild.bat
```

產出：

- `dist\PhotoManager\PhotoManager.exe`：免安裝的資料夾版，整個資料夾複製走就能用。
- `dist\installer\PhotoManager-Setup-<版本>.exe`：安裝檔。預設只裝給目前使用者（不需要系統管理員），也可以選擇安裝給所有使用者；解除安裝時會詢問是否一併刪除設定與標記。

版本號在 `photomanager/__init__.py` 的 `__version__`。

## 資料位置

| 路徑（Windows） | 內容 |
| --- | --- |
| `%APPDATA%\PhotoManager\settings.json` | 介面偏好與上次開啟的資料夾 |
| `%APPDATA%\PhotoManager\categories.json` | 自訂分類與分類設定檔 |
| `%APPDATA%\PhotoManager\marks\` | 各資料夾的照片分類標記 |
| `%LOCALAPPDATA%\PhotoManager\` | 縮圖、EXIF 與 AI 模型快取；刪除後會按需重建或重新下載 |

預設分類位於 [`photomanager/assets/categories.json`](photomanager/assets/categories.json)。

## 專案結構

```text
PhotoManagerDesktop/
├─ main.py                  # 程式進入點
├─ run.bat                  # Windows 啟動腳本
├─ requirements.txt         # Python 相依套件
├─ PhotoManager.spec        # PyInstaller 打包設定
├─ packaging/               # build.bat、安裝檔腳本（installer.iss）、繁中安裝精靈文字、圖示
└─ photomanager/
   ├─ app.py                # 主視窗、頁面路由、快捷鍵與主題
   ├─ config.py             # 使用者設定與資料路徑
   ├─ state.py              # 跨頁狀態
   ├─ filters.py            # 照片篩選條件
   ├─ grouping.py           # 照片分組邏輯
   ├─ i18n.py               # 多語系處理
   ├─ engine/               # EXIF、影像解碼、照片庫、分類與檔案操作
   ├─ pages/                # 照片、統計、整理、改名與設定頁面
   ├─ tools/                # 裁切、相框、色卡與景深模糊工具
   ├─ ui/                   # 共用介面元件、檢視器與主題
   └─ assets/               # 圖示與預設分類
```
