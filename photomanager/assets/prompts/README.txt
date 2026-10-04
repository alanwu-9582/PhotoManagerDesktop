AI 風格提示詞資料夾
====================

每個提示詞一個資料夾：
  <資料夾>/prompt.txt          提示詞本文（UTF-8），程式裡按「複製提示詞」會一字不改地複製
  <資料夾>/<名稱>_原圖.jpg      範例的原圖
  <資料夾>/<名稱>_成果.jpg      範例的成果圖（用同一個 <名稱> 配成一組；也接受 _before / _after）
  沒有標示 _原圖 / _成果 的圖片，會當成只有成果圖的範例。

prompts.json 是全部提示詞的清單：
  id           唯一的名稱（英文、數字、連字號）
  folder       資料夾名稱
  file         提示詞檔名（通常是 prompt.txt）
  title        標題
  description  一兩句說明
  tags         標籤，例如 poster、collage、watercolor
  language     提示詞本身是什麼語言（"en" / "zh-Hant"）

加完圖片或改完 json，回到程式按「重新整理」。

在程式裡按「上傳提示詞」或「新增範例」的，存在設定資料夾（Windows：%APPDATA%\PhotoManager\prompts），
上傳的圖片會先壓縮（長邊 1600、JPEG 品質 85）。程式會把兩邊合在一起顯示。
這個資料夾是內建的提示詞，跟著程式一起打包。
