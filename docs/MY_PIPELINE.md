# 阿志的演講筆記 pipeline（本機預設流程）

> 定稿 2026-10-07，依 IDSA 20261004 兩場實跑經驗設計。上游通用說明仍在 `SKILL.md` 與 `reference/`；這份只寫本機的預設與分工。

## 一句話

給一個影片、資料夾或 YouTube 網址，跑一個指令到「可以寫筆記」的狀態；請 Claude 寫筆記與查核；再跑一個指令收尾，得到 vault 筆記、單檔 HTML 和 Web viewer。

## 流程

| 步驟 | 做什麼 | 工具 | 誰做 |
|---|---|---|---|
| 0 取得素材 | 網址用 yt-dlp 下載 mp4，有字幕一併下載；本機檔直接用 | `scripts/fetch_media.py` | 腳本 |
| 1 轉錄（主） | 本機 CPU Whisper medium，資料不離開本機 | `scripts/transcribe_video.py --device cpu --model medium` | 腳本 |
| 2 轉錄稽核（外部） | Groq 再轉一次，只拿來比對，不取代主稿；列出崩壞段、漏段、數字不一致 | `scripts/asr_audit.py` | 腳本 |
| 3 投影片 | 擷取畫面、OCR、去重、VLM 語意、逐字稿對位 | 既有 Stage A–E | 腳本 |
| 4 分級 | 依 note-spec 計分規則決定每張投影片要不要放；投影片講座改用 all-slides 版面，全部放 | `scripts/tier_pass.py` | 腳本 |
| 5 寫筆記 | 寫筆記 → 獨立查核（文獻補齊、分段整理稿只在要求時做） | `reference/stage-f-prompts.md` | Claude |
| 6 收尾 | 寫入 vault、單檔 HTML；Web viewer 只在要求時做 | `scripts/run_lecture.py --finish [--viewer]` | 腳本 |

指令：

```
python scripts/run_lecture.py <影片或網址> --out <輸出資料夾> --lang zh [--no-groq]
python scripts/run_lecture.py <輸出資料夾> --tier
python scripts/run_lecture.py <輸出資料夾> --render
python scripts/run_lecture.py <輸出資料夾> --finish
```

| 指令 | 做完之後 |
|---|---|
| 第一行（準備） | 停在「READY FOR STAGE F」。確認講者後，把講者、主題、日期填進 `lecture.json` |
| `--tier` | 產出 `slides_final.json`，交給 Claude 寫筆記（第 1 輪） |
| `--render` | 產出正式筆記檔並稽核，交給 Claude 獨立查核（第 2 輪） |
| `--finish` | 寫入 vault、產出單檔 HTML。加 `--viewer` 才做 Web viewer |

實際上只要在這個 repo 開 Claude Code，說「跑演講筆記 <影片路徑或網址>」，Claude 會照順序執行這四步與四輪寫作。資料夾輸入只會列出裡面的檔案，不會替你挑影片。

端到端測試（2026-10-07）：CDC 公開演講的 2 分鐘 YouTube 片段，從下載到可寫筆記約 2.5 分鐘，四個指令都 exit 0。

一律用 repo 的 `.venv\Scripts\python.exe` 執行（系統 Python 沒有 OCR 套件，會在轉錄跑完一小時後才在 OCR 停住）。

## 筆記版面（2026-10-09 定案）

確認講者時，順便看畫面決定版面，寫進 `lecture.json` 的 `layout`，再跑 `--tier`：

| 講座類型 | `layout` | 總整理的圖 | 逐投影片筆記 |
|---|---|---|---|
| 投影片講座 | `all-slides` | 每張投影片都放在講到它的段落；不重要的一行帶過；重複圖與章節頁預設收合、縮小 | 純文字索引（時間 → 所在小節） |
| 螢幕分享、操作示範 | `tiered`（預設） | 依分級，T1 放總整理；畫面上的重點文字整理成表格 | 每張 T1／T2 畫面附說明 |

不依重要度分數篩掉講者的投影片，是阿志的裁示：分級擅長排序，但不該決定讀者看不看得到講者自己的投影片。兩種版面的總整理每一小節最後，都加一則 `〔整理者補充〕對主任秘書的用處`，做成獨立的提示框，明確標示不是講者說的。

## 講座資料夾整理

每場收尾後整理 `D:\lectures\<講座>\`：根目錄只留會打開的檔案（影片、HTML 筆記；有 Web viewer 時加上 viewer 與分享說明），其餘全部移進 `_archive\`。之後要補 Web viewer 或第 3 輪，對 `_archive\_out` 執行即可。筆記 md 的正本在 vault。

## 預設值與理由

- **CPU 為主，Groq 為稽核。** 實測 CPU 兩場都完整；Groq 快 30 倍，但 40 分鐘那場整段崩壞，且輸出簡體。Groq 只用來抓主稿的錯，`--no-groq` 可關閉（有病人資料或內部會議時必關，因為音檔會上傳）。
- **稽核看三件事。** 某段只有一邊有文字（漏段）、某段文字一直重複（崩壞）、同一段兩邊的數字不同。數字不同最重要，因為筆記的價值在數字。
- **講者身分以介紹段為準。** 論壇錄影開頭常是別人致詞；以主持人介紹與主講標題頁判定，不看第一張畫面。
- **寫筆記與查核分開。** 寫的人不驗自己的稿；查核者先讀逐字稿再讀筆記，數字逐一回查。
- **文獻不另外求證。** 筆記只列講者或投影片點名的來源，書目不完整就標 `⚠️ 待補`，不再逐篇查 PubMed。要深入時再下指令跑第 3 輪。
- **Web viewer 非必要。** 需要時說「做 Web viewer」，先跑第 4 輪寫分段整理稿，再 `--finish --viewer`。影片一律壓成 H.264，所有瀏覽器都能播。
- **Vault 位置。** 筆記進 `00-Inbox\`，圖片進 `99-Attachment\lecture_<slug>\`，在 `config.yaml` 的 `paths.vault_*` 設定。

## 這次實跑修掉的問題

| 問題 | 修法 |
|---|---|
| 中文投影片幾乎全被判成「講者沒提到」 | `ground_slides.py` 中文改用字元片段比對，並統一簡繁、濾掉 Zoom 介面字 |
| finalize 寫死 `00Inbox`／`99Attachment` | 改成 config 與參數可設定 |
| Web viewer 找不到資料夾外的影片 | `build_single_talk_web.py` manifest 記錄影片所在資料夾 |
| 圖說第一行太長 | `render_embeds.py` 自動把說明移到第二行 |
| md 不好讀 | `scripts/note_to_html.py` 產出圖片內嵌的單檔 HTML |
| PowerShell 紀錄檔變 UTF-16 亂碼 | 執行器一律用 Python 寫 UTF-8 紀錄 |
