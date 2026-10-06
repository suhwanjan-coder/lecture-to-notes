# lecture-to-notes 可用性評估 + drpwchen / htlin222 可用 repo 清單（2026-10-06）

## 一、lecture-to-notes 可用性評估

來源：drpwchen/lecture-to-notes（107★，v0.7.1，2026-09-01），MIT。目前 fork 與上游同步。

| 面向 | 評估 |
|---|---|
| 成熟度 | 高。私下跑一年後才公開，發布前 4 人審計、約 130 個 findings 已修；CHANGELOG 到 0.7.1，有 SYNC-LEDGER 記錄每次同步與去識別化。 |
| 文件 | 很好。README 雙語、SKILL.md 當 agent 地圖、reference/ 五份規格、decisions.md 有踩雷紀錄。 |
| 程式碼 | 約 14,000 行 Python，全部 py_compile 通過。optional 套件「缺就大聲停用」，不會默默降級。 |
| 硬體 | 真正要跑順需 NVIDIA 8GB+（RTX 3070 Ti 實測）。CPU 可跑但 1 小時演講要數小時；2–4GB 卡視同沒 GPU。可用 Groq 免費 whisper 卸載（有 PHI 就不行）。 |
| 平台 | 只在 Windows 11 測過；Linux/macOS 作者說「應該可以、未測」。多個腳本有 Windows 路徑處理。 |
| Python | 要求 3.12。本機 3.11 可編譯，但未實跑。 |
| 外部依賴 | ffmpeg/ffprobe 必要；ollama + minicpm-v:8b（Stage D）、Surya 另開 venv、pandoc（PDF/HTML 匯出）皆選用。 |
| 最大限制 | Stage F（寫筆記）沒有腳本，是給 Claude Code agent 讀的 prompt 規格（reference/note-spec.md）。沒有 agent 的話，slides_grounded.json 就是終點，自己接 LLM。 |
| 預設偏向 | 復健科醫學演講、繁中章節標題、Zoom 繁中 UI 雜訊清單。都是 config，可改。 |
| 結論 | 以「Claude Code skill」方式使用最省事：把 repo 丟到 ~/.claude/skills/lecture-to-notes/。純 CLI 也能拿到逐字稿、去重投影片、OCR、對位 JSON，這已是 80% 價值。 |

對我們最有用的三個可拆件：
1. scripts/layout2/ + export_web.py：影片/逐字稿/摘要三欄同步的單檔 HTML viewer，網頁製作可直接借用。
2. media_capture_index.py + xcorr_media_offsets.py：多機錄影對時（宣稱 vs 量測，衝突就停）。
3. finalize_to_vault.py：筆記 + 引用圖直接進 Obsidian vault，支援資料夾直接當 vault 開。

## 二、drpwchen 其他 repo（共 20 個，全列可用者）

| Repo | ★ | 最後更新 | 用途 | 對我們 | 建議 |
|---|---|---|---|---|---|
| textbook-to-note | 104 | 08-26 | PDF/EPUB 教科書 → markdown + 圖 + 有引用的筆記；含 2 個 Claude skill | 作筆記 | Fork |
| paper-radar | 107 | 08-05 | RSS/PubMed 追蹤 + 興趣評分 + 私人網頁篩選，同步到筆記 | 研究 | Fork |
| paper-fetch | 65 | 09-08 | DOI → PDF（OA → 出版社 API → 機構 proxy） | 研究 | Fork |
| paper-review-and-digest | 42 | 08-22 | /paper-review（GRADE 評讀）+ /paper-digest（三層摘要）Claude skills | 研究 | Fork |
| vault-search | 6 | 09-06 | Obsidian vault 本機語意搜尋 + RAG，MCP server 給 Claude Code 用（Ollama bge-m3，不需 GPU） | 作筆記 | Fork |
| note-supplement | 6 | 08-08 | 新資料併入既有筆記，衝突偵測、強制引用；Claude slash command | 作筆記 | Fork |
| openevidence-tools | 8 | 09-02 | OpenEvidence MCP server + 引用驗證 | 研究 | 視需要 |
| ytscribe | 2 | 08-05 | 整個 YouTube 頻道抓字幕 | 研究/筆記 | 視需要 |
| asr-benchmark | 4 | 08-05 | 無標準答案下評分 ASR 模型（挑 Whisper 模型用） | 筆記（配 lecture-to-notes） | 視需要 |
| exam-practice | 7 | 08-05 | 自架考題練習平台，FSRS 間隔重複 | 學習 | 視需要 |
| chart-scrub | 7 | 08-19 | 繁中臨床文字去識別化 | 研究（PHI） | 視需要 |
| evernote-rescue | 3 | 08-02 | Evernote → Obsidian 遷移修復 | 作筆記 | 有需要才 |
| claude-pacer / codex-pacer | 8 / – | 09-28 | Claude Code / Codex 用量 statusline | 工具 | 可選 |
| loan-invest-sim | 9 | 08-09 | 房貸投資蒙地卡羅模擬（HTML） | 無關 | – |
| fbkit / fbpost-fork-archive / kimi-webbridge-lockdown | – | – | FB 自動化、瀏覽器擴充鎖定 | 無關 | – |

注意：drpwchen 的工具彼此成一條鏈：paper-radar（發現）→ paper-fetch（下載）→ paper-review-and-digest（評讀）→ textbook-to-note / lecture-to-notes（產筆記）→ note-supplement（併入）→ vault-search（檢索）。建議整條 fork。

## 三、htlin222 可用 repo（共 192 個，篩出研究/筆記/網頁相關）

「近期」= 2026-06 之後有更新或新建，幾個月前的清單可能沒有。

### 研究 / 文獻
| Repo | ★ | 最後更新 | 用途 | 近期 |
|---|---|---|---|---|
| meta-pipe | 134 | 09-23 | Claude Code 全自動 meta-analysis，9 階段到稿件（Academic/Non-commercial 授權） | ✔ |
| robust-lit-review | 60 | 09-09 | PRISMA 系統性回顧 pipeline（PubMed/Scopus），template | ✔ |
| openevidence-mcp | 78 | 09-14 | OpenEvidence MCP server | ✔ |
| audit-oe-skill | 14 | 04-20 | 用 PubMed 驗證 OpenEvidence 引用 | |
| ask-oe-with-ego-skill | – | 09-09 | 查 OE 並產出含 Crossref 書目的 HTML | ✔ |
| research-guardian-skill | 5 | 04-17 | 多關卡研究驗證（假設/引用/實驗） | |
| prisma-automation | 8 | 03-28 | PRISMA 流程自動化 | |
| flowdoc | 2 | 04-28 | PRISMA / CONSORT 流程圖產生器 | |
| academic-manuscript-workflow | 3 | 03-28 | Claude Code + Pandoc 引用工作流 | |
| quarto-doc / cookiecutter-quarto-research | – | 03-28 | Quarto 學術稿件模板 + R 引用驗證 | |
| submission-desk | 1 | 09-09 | 單檔投稿決策評分工具 | ✔ |
| lizard-on-zotero | 3 | 03-28 | Zotero 繁中完整指南 | |
| irb-in-hurry | 31 | 10-05 | IRB 文件自動備製（43 表單） | ✔ |
| agent-in-ebm | – | 09-16 | EBM agent，可版本化的研究決策 | ✔ |
| cps-skills | 19 | 10-05 | 貝氏診斷推理（likelihood ratio）skill | ✔ |
| society-calendar | 3 | 04-01 | 台灣醫學會活動爬蟲 → Google Calendar | |

### 作筆記 / 影音轉文字
| Repo | ★ | 最後更新 | 用途 | 近期 |
|---|---|---|---|---|
| sum-the-yt | 4 | 09-09 | YouTube → 繁中摘要（字幕優先，Whisper 備援，本機 claude CLI） | ✔ |
| polish-screen-record | – | 09-09 | 長螢幕錄影 → 可讀繁中 SRT（Colab + Faster Whisper + DeepSeek 標點） | ✔ |
| wiki-thread | – | 09-09 | Obsidian vault 渲染成 Threads 式社群 feed，互動寫回 .md | ✔ |
| pdf-to-tts-zh-skill | 4 | 09-09 | PDF → 繁中有聲書 MP3（edge-tts） | ✔ |
| hackmd-skill (fork) | – | 07-31 | HackMD 的 SKILL.md | ✔ |
| quartz | 1 | 03-28 | 數位花園靜態站產生器（筆記發布） | |
| mcq-bank / mcq-to-anki / qbank2anki / anki_batch / ankiweb-add-card / yanki-mcp-server | 12 / – | 09-21 等 | 題庫、Anki 整條工具鏈，含 Anki MCP | 部分 ✔ |
| zh-article-analyzer-skill | 22 | 04-04 | 繁中文章深度語言分析 skill | |
| clipboard-snap-shortcut | 2 | 09-09 | iOS 選字存到 Turso | ✔ |
| project-management-in-chat | – | 09-09 | 唯讀雲端環境下在對話裡做專案管理的 skill | ✔ |

### 網頁 / 簡報 / 課程製作
| Repo | ★ | 最後更新 | 用途 | 近期 |
|---|---|---|---|---|
| curate-course | 9 | 09-09 | 把 YouTube 影片策展成結構完整、連結全驗證的課程網站（template；gym-course、tarot-course 等都是它產的） | ✔ |
| lin-hsiehting | 3 | 10-05 | Astro 6 + MDX + Tailwind v4 臨床筆記網站，含 JSON-LD、審計腳本，Cloudflare Pages 自動部署 | ✔ |
| scenemd | 1 | 09-14 | Markdown → 響應式簡報場景，匯出 PPTX/PDF/DOCX/HTML（MVP） | ✔ |
| minimalism-slides | 5 | 05-20 | 輕量 HTML/CSS/JS 簡報框架，多螢幕同步（template） | |
| pdf-presenter | 9 | 04-18 | PDF 投影片瀏覽器簡報模式（講者備註、計時） | |
| my-slidev-template / lizard_marp | 1 / 2 | 04-08 / 03-28 | Slidev、Marp 簡報模板 | |
| lizard-gslide-module | 34 | 09-09 | Google Slides 格式自動化（Apps Script） | ✔ |
| open-google-slide (fork) | – | 07-12 | 給 agent 的簡報框架，可匯出 Google Slide | ✔ |
| live-google-slide | 1 | 09-22 | Cloudflare Worker 跨裝置同步 Google Slides | ✔ |
| slides-to-video | 3 | 03-28 | PDF 投影片 → 配音影片 | |
| lizard-design (fork) | 1 | 05-01 | HTML-native 設計 skill（原型/簡報/動畫） | |
| bestseller | 11 | 04-19 | 繁中非虛構 A5 書籍排版模板（Quarto + Typst） | |
| img-hosting | 12 | 09-09 | Cloudflare Workers + R2 自架圖床 | ✔ |
| og-img-gen | 1 | 09-09 | 產 GitHub 風 OG 卡片的 Chrome 擴充 | ✔ |
| excalidraw-cf-platform | 1 | 09-09 | 自架協作 Excalidraw | ✔ |
| image-step-by-step | 1 | 04-08 | 圖片分步標註 GUI | |
| claude-demo-studio-skill / claude-session-recorder | 1 / 1 | 09-09 | 把 Claude session 做成動畫 demo / 教學影片 | ✔ |

### 開發環境 / 其他可能有用
| Repo | ★ | 用途 |
|---|---|---|
| dotfiles | 78 | Neovim/Zsh/tmux/Hammerspoon |
| mini-claw / claude-telegram-bot | 93 / 13 | Telegram 操控 Claude |
| csession | 1 | fzf 瀏覽/恢復 Claude Code session |
| gh-repo-father-skill | 2 | AI 建 GitHub repo 骨架 |
| claude-with-webhook | 5 | GitHub webhook 自動跑 Claude Code |
| zerospec (fork) / ddd-workflow (fork) | – | 給 agent 看懂 repo 結構的 markdown 框架 / 文件驅動開發 |
| CCChange | 2 | Claude Code changelog 中文日報（Astro） |
| ttyd-tmux-cf / lumiterm | 16 / 6 | 網頁終端（簡報用） |

## 四、建議 fork 優先順序
1. drpwchen 整條鏈：textbook-to-note、paper-radar、paper-fetch、paper-review-and-digest、vault-search、note-supplement。
2. htlin222 研究：meta-pipe、robust-lit-review、openevidence-mcp、audit-oe-skill。
3. htlin222 網頁：curate-course、lin-hsiehting、scenemd、img-hosting。
4. htlin222 影音/筆記：sum-the-yt、polish-screen-record、wiki-thread。

## 五、與 suhwanjan-coder 既有 repo 對照（2026-10-06）

### drpwchen
| Repo | 狀態 |
|---|---|
| lecture-to-notes、claude-pacer | 已 fork |
| textbook-to-note、paper-radar、paper-fetch、paper-review-and-digest、vault-search、note-supplement、openevidence-tools、ytscribe、asr-benchmark、exam-practice、chart-scrub、evernote-rescue、codex-pacer | 未 fork，`docs/fork_drpwchen.sh` 一次補齊 |

注意：既有的 `textbook-notes` 不是 drpwchen 的 `textbook-to-note`，是另一個來源。

### htlin222 已 fork（11 個）
zh-ebn-report-skill、robust-lit-review、sum-the-yt、linebot-turso-relay、learn-r-with-ai、CCChange、pdf-to-tts-zh-skill、lumiterm、oe-extension、LizardType，以及 agent-skills / skills（htlin222 也是 fork，原始來源另有其人）。

### htlin222 尚未 fork、建議補的
| 類別 | Repo |
|---|---|
| 研究 | meta-pipe、openevidence-mcp、audit-oe-skill、ask-oe-with-ego-skill、irb-in-hurry、cps-skills、research-guardian-skill、flowdoc、academic-manuscript-workflow、submission-desk |
| 筆記 | wiki-thread、polish-screen-record、hackmd-skill、mcq-bank、yanki-mcp-server、zh-article-analyzer-skill、quartz |
| 網頁/簡報 | curate-course、lin-hsiehting、scenemd、lizard-gslide-module、img-hosting、minimalism-slides、pdf-presenter、bestseller、open-google-slide、live-google-slide |
| 開發 | mini-claw、csession、gh-repo-father-skill、claude-with-webhook |

## 六、本機 vs 雲端
這些工具絕大多數要在自己電腦上跑：lecture-to-notes、textbook-to-note、asr-benchmark 需要 GPU 或大量 CPU 時間與 ffmpeg；vault-search、note-supplement、wiki-thread 要讀本機 Obsidian vault；paper-fetch 要用你的機構 proxy 登入；sum-the-yt、polish-screen-record 依賴本機 claude CLI 或 Colab。純雲端可用的只有 paper-radar（Cloudflare + 24/7 主機）、curate-course / lin-hsiehting / scenemd（Cloudflare Pages）、openevidence-mcp 這類 server 型工具。Claude Code skill 類（paper-review-and-digest、cps-skills 等）只要放進 `~/.claude/skills/` 就能用，本機或 Claude Code web 皆可。

未能查到：drpwchen.com 與 lin.hsiehting.com 被本環境網路政策擋住，作者的 map/portfolio 頁沒讀到，以上以 GitHub 頁面為準。
