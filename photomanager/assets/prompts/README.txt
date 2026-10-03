AI 風格提示詞資料夾
====================

每個提示詞一個資料夾：
  <資料夾>/prompt.txt     提示詞本文（UTF-8），程式裡按「複製提示詞」會一字不改地複製
  <資料夾>/任何圖片        參考結果圖（jpg / png / webp / heic …），直接丟進來就會顯示成縮圖

prompts.json 是全部提示詞的清單：
  id           唯一的名稱（英文、數字、連字號）
  folder       資料夾名稱
  file         提示詞檔名（通常是 prompt.txt）
  title        標題
  description  一兩句說明
  tags         標籤，例如 poster、collage、watercolor
  language     提示詞本身是什麼語言（"en" / "zh-Hant"）
  thumbnails   參考圖檔名；留空 [] 就自動列出資料夾裡全部的圖片

加完圖片或改完 json，回到程式按「重新整理」。
