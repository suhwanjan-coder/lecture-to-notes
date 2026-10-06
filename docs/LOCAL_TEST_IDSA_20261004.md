# 本機測試：IDSA 流感疫苗接種指引更新發布 20261004

目標：用 `G:\我的雲端硬碟\IDSA 流感疫苗接種指引更新發布 20261004` 裡的兩個檔案，驗證 lecture-to-notes 在這台 Windows 機器上能跑完。

## 0. 一次性安裝（PowerShell）

```powershell
# 工具
winget install Gyan.FFmpeg          # ffmpeg + ffprobe
winget install GitHub.cli           # gh（fork 用）
winget install JohnMacFarlane.Pandoc  # 選用：PDF/HTML 匯出
python --version                    # 需要 3.12；沒有就 winget install Python.Python.3.12

# 取得 repo（含這份文件所在分支）
cd $HOME
git clone https://github.com/suhwanjan-coder/lecture-to-notes
cd lecture-to-notes
git checkout claude/zealous-ride-uw6734   # PR 合併後改用 main

# 依賴
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-optional.txt
copy config.example.yaml config.yaml
```

Stage D（投影片語意）選用：`winget install Ollama.Ollama` 然後 `ollama pull minicpm-v:8b`。沒有 8 GB GPU 就先跳過。

## 1. 當成 Claude Code skill 使用（建議）

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
New-Item -ItemType Junction -Path "$HOME\.claude\skills\lecture-to-notes" -Target "$HOME\lecture-to-notes"
```

然後在本機 Claude Code 裡貼：

> 用 lecture-to-notes skill 處理 `G:\我的雲端硬碟\IDSA 流感疫苗接種指引更新發布 20261004`。先跑 route_inputs.py 看它怎麼分類這兩個檔案並列出要問我的問題，再照它印出的順序執行。語言：先問我。輸出目錄放在 `G:\我的雲端硬碟\IDSA 流感疫苗接種指引更新發布 20261004\_out`。

## 2. 純 CLI 走法（不經 agent）

```powershell
$M = "G:\我的雲端硬碟\IDSA 流感疫苗接種指引更新發布 20261004"
$O = "$M\_out"
python scripts\route_inputs.py "$M" --out-dir "$O"      # 只印計畫，不動檔案
python scripts\gpu_check.py --out-dir "$O" --min-free-mb 6000
```

依 route_inputs 印出的命令跑。第一步會是轉錄，必帶 `--lang`：

```powershell
# 有 NVIDIA 8GB+：
python scripts\transcribe_video.py "<影片或音檔>" --output-dir "$O" --lang zh --batch-size 3 --beam-size 10
# 沒有 GPU 或卡太小：
python scripts\transcribe_video.py "<影片或音檔>" --output-dir "$O" --lang zh --device cpu --model small
# 想快又不介意音檔離開本機（這場是公開指引發布，沒有病人資料）：
$env:GROQ_API_KEY = "<key>"
python scripts\transcribe_video.py "<影片或音檔>" --output-dir "$O" --lang zh --engine groq
```

`--lang` 依講者實際語言選 `zh` / `en` / `bilingual`。

## 3. 預期結果與判讀

- 兩個檔案若是「影片 + PDF 投影片」會走 Path B，PDF 文字直接進對位，品質最好。
- 若是「影片 + 音檔」或兩段影片，route_inputs 會提示先跑 course_timeline / media_capture_index 對時。
- 跑完 Stage E 會有 `slides_grounded.json`；有 agent 才會寫出最後筆記與 HTML viewer。
- 卡住就把 `_out\*.log` 與 route_inputs 的輸出貼回來。
