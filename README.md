# Photo Manager Desktop

參考 [wayne-1211/photoManager](https://github.com/wayne-1211/photoManager) 製作的桌面重製版。

以 PySide6 開發，直接讀取及整理本機照片；照片不會上傳，分類時會在原位置將檔案移至對應的子資料夾。支援 JPEG、TIFF、HEIC／HEIF。

## 功能

- 依影像相似度或連拍時間自動分組，方便從相近照片中挑選成品。
- 以分類設定檔保存不同情境的分類、顏色、動作與快捷鍵，並可一次套用整理結果。
- 照片編輯有四頁：裁切旋轉、調整、景深模糊、相框與色卡（EXIF 相框與照片色卡合在同一頁，右上角切換）。
- 編輯工具可無損接力：每個工具都有「暫存」（Ctrl+Shift+S，只留在記憶體、不寫檔）與「儲存」（Ctrl+S）；切到其他編輯工具會接著編輯暫存或儲存的結果，避免重複 JPEG 壓縮。
- 調整（參考 Lightroom）：白平衡、曝光、對比、亮部、陰影、白色、黑色、紋理、清晰度、去朦朧、細節飽和度、飽和度、自動色調（實際套用後量測再修正，只補足不壓平）；色彩混合（八個色相的色相 / 飽和度 / 明亮度）、顏色分級、暈影、顆粒；聚光燈可以在照片上畫出橢圓、矩形或橫跨整張的光帶，裡面提亮、外面壓暗，可以拖曳旋轉。
- 攝影風格（參考 iPhone 16 之後的相機）：標準、琥珀色、金色、玫瑰金色、中性、冷玫瑰色等膚色基調，與鮮明、自然、明亮、戲劇效果、寧靜、溫馨、空靈、柔和 / 強烈黑白等氛圍；用方形控制板調色調與色彩、用色盤調強度，其他調整會疊在風格上面。
- 調整與景深模糊的照片左下角有「照片參數」：直方圖（含死白 / 死黑提示）、波形、向量示波器與代表色、曝光數字與拍攝參數；按住空白鍵看原圖對照。
- 景深模糊照鏡頭的方式算：在線性光裡用光圈形狀（圓形、9 / 7 / 6 / 5 葉、圓環）的核心模糊，用 f 值決定最大模糊圈，模糊量隨離對焦面的深度差增加；點光源會散成明亮的散景。可使用 Depth Anything V2 模型分析遠近（需另行下載），沒有模型時用快速估計，遠近比較不準。
- AI 風格提示詞：把照片轉成各種風格的提示詞，一鍵複製到 AI 生圖工具。提示詞放在 `photomanager/assets/prompts/`：每個提示詞一個資料夾（`prompt.txt` + 參考結果圖，圖片丟進去就會顯示），`prompts.json` 記錄全部的提示詞；格式說明在該資料夾的 `README.txt`。
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
├─ docs/介面設計規範.md     # 介面設計規範（顏色、字體、元件、版面；不含功能，可套用到其他工具）
├─ packaging/               # build.bat、安裝檔腳本（installer.iss）、繁中安裝精靈文字、圖示
└─ photomanager/
   ├─ app.py                # 主視窗、頁面路由、快捷鍵與主題
   ├─ config.py             # 使用者設定與資料路徑
   ├─ state.py              # 跨頁狀態
   ├─ filters.py            # 照片篩選條件
   ├─ grouping.py           # 照片分組邏輯
   ├─ engine/               # EXIF、影像解碼、照片庫、分類與檔案操作
   ├─ pages/                # 照片、統計、整理、改名與設定頁面
   ├─ tools/                # 編輯工具：common.py（共用外框、暫存、分頁、ToolGroup）、photo_edit、adjust、depth_blur、frames（exif_frame + palette_card）
   ├─ ui/                   # 共用介面元件、檢視器與主題
   └─ assets/               # 圖示、預設分類、AI 風格提示詞（prompts/）
```
