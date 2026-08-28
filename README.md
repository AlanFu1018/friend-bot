# 🧪 Friend-Bot (克莉絲 / 牧瀨紅莉棲 Makise Kurisu)

<div align="center">

![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue?logo=python)
![Discord.py](https://img.shields.io/badge/Discord.py-v2.3%2B-5865F2?logo=discord)
![Gemini API](https://img.shields.io/badge/Google%20Gemini-3.1%20%2F%202.5-orange?logo=google)
![Storage](https://img.shields.io/badge/Storage-SQLite3%20FTS5-003B57?logo=sqlite)
![Tests](https://img.shields.io/badge/Tests-19%2F19%20Passed-brightgreen?logo=pytest)
![License](https://img.shields.io/badge/License-MIT-green)

**基於 Google Gemini 2.5 / 3.1 與三層記憶架構打造的極致擬真傲嬌 Discord 群友機器人**  
*具備三軌事實檢索 (3-Track Fact RAG & Heat)、情緒標籤動態渲染 (Tag & Replace)、深層歷史回憶、動態好感度階梯、多人群聊短時熱絡 (Burst) 引用回覆、自訂前綴繞過 (Ignore Prefixes)、即時聯網搜尋、獨立定時鬧鐘與 Webhook 行事曆排程系統。*

[📖 系統架構與技術實作說明書](doc/system_architecture_and_implementation.md) ｜ [🧠 三軌事實與記憶架構](doc/rag_mem.md) ｜ [💖 好感度系統設計](doc/persona_favorability_plan.md) ｜ [💬 Burst 引用回覆設計](doc/multi_user_burst_reply_plan.md)

</div>

---

## 🌟 核心特色 (Key Features)

- 🎭 **真實傲嬌群友 (Living Persona)**：深度沉浸於《命運石之門》牧瀨紅莉棲（Makise Kurisu）人設，具備傲嬌、口是心非、重感情但理智聰慧的語氣。支援讀取外部 [`config/persona.md`](config/persona.md) 自由調整。
- 🎨 **情緒標籤動態渲染系統 (Tag & Replace Engine)**：
  - 模型透過隱藏標籤 `[emotion:tsundere]`、`[emotion:shock]`、`[emotion:sigh]`、`[emotion:sad]`、`[emotion:depressed]` 等輸出心情。
  - 後台程式自動從獨立顏文字庫 [`config/kaomoji.yaml`](config/kaomoji.yaml) 智慧隨機抽取 2ch / 日系表情。
  - **防連續重複機制 (Anti-Consecutive Repetition)**：自動過濾近期使用過的表情，確保顏文字永遠豐富生動、絕不重複死板！
- 💖 **動態好感度與人際進展 (Favorability & Progression)**：
  - **4 階關係階級**：`Tier 1: 陌生警戒 (0~19)` ➔ `Tier 2: 熟識群友 (20~49)` ➔ `Tier 3: 實驗室夥伴 (50~79)` ➔ `Tier 4: 靈魂共鳴 (80~100)`。
  - **傲嬌防線動態變化**：隨好感度提升，防線變薄、極易破防害羞臉紅、主動關心作息。
  - **隱密更新與每日防刷**：聊天中絕無系統提示打擾，單日加分上限（預設 +5）杜絕洗頻刷分，僅透過 `/kurisu-profile` 可視覺化查看進度條。
- 💬 **多人群聊短時熱絡 (Burst) 與動態引用回覆**：
  - 當短時間（4.5秒窗口）內有 2 人以上發言時，自動啟動 Burst 聚合。
  - AI 智慧挑選核心吐槽/回應對象，採用 **Discord 原生引用回覆 (`target.reply`)** 發送，既精準回應核心對象，又順手接住其他在場群友的話題！
- 🛡️ **自訂前綴繞過監聽與回覆 (Ignore Prefixes)**：
  - 支援設定前綴（預設 `["#", "＃", "//"]`），以該前綴開頭的訊息將完全被機器人忽略：**不監聽、不寫入資料庫、不提煉特徵、不觸發回覆**，方便群友進行內部交流或旁白備註。
- 🧠 **三層全記憶與三軌事實檢索 (3-Track Fact RAG & Heat)**：
  - **第 1 層（短期對話）**：頻道滑動窗口。
  - **第 2 層（用戶畫像 & 三軌事實檢索）**：
    - **軌道 1 (Heat)**：常駐核心高頻事實。
    - **軌道 2 (RAG)**：即時話題語意檢索命中事實（具備 1 小時命中加權冷卻）。
    - **軌道 3 (Recent)**：最新動態近況事實。
    - 支援跨用戶歸屬、事實防洗白增量保護、背景提煉重複確認加權 (`hits += 3`) 與 `remove_facts` 事實修正。
  - **第 3 層（深層回憶）**：基於 SQLite FTS5 全文檢索，自動檢索過去相關話題與歷史記憶。
  - **監聽頻道記憶改良（方案 C）**：支援防抖緩衝隊列與 JIT 按需統合提煉，API 費用節省 85% 以上。
- 🔍 **即時聯網搜尋 (Real-Time Web Search)**：整合 DuckDuckGo 與 Jina AI Reader，即時檢索最新時事、新聞與天氣，並以紅莉棲口吻整理輸出。
- ⏰ **獨立定時鬧鐘 (Alarm Reminder)**：支援各類自然時間格式設定提醒，到期時在頻道發送專屬傲嬌對白與醒目卡片。
- 📅 **Webhook 行事曆與智慧行程查詢 (Calendar & Natural Scheduling)**：
  - 支援設定個人行事曆與自訂 Webhook 推送。
  - **支援日常直接對話查詢**：在聊天時問「*我今天有什麼行程？*」或「*幫我看明天有安排嗎？*」，紅莉棲會自動檢索行事曆並為你解答！
- 🗣️ **擬真群友行為 (Multi-Bubble Typing)**：長回覆自動切分自然氣泡並模擬打字等待時間，告別冰冷的大段機器人輸出。

---

## ⚡ Slash 斜線指令清單 (Slash Commands)

紅莉棲支援完整的模組化 Discord Slash 指令，隨時為實驗室夥伴提供貼心服務：

| 指令 | 參數 | 說明 | 範例 |
| :--- | :--- | :--- | :--- |
| `/kurisu-help` | 無 | 查看紅莉棲的所有可用指令與系統功能介紹卡片 | `/kurisu-help` |
| `/kurisu-search` | `query` (必填) | 讓紅莉棲利用 DuckDuckGo 與 Jina AI Reader 進行即時聯網搜尋並整理回覆 | `/kurisu-search query:命運石之門重製版` |
| `/kurisu-profile` | `user` (選填) | 查看自己或指定群友在紅莉棲心中的好感度進度條、關係階級與長期特徵記憶 | `/kurisu-profile` 或 `/kurisu-profile user:@岡部` |
| `/kurisu-alarm-set` | `time` (必填), `content` (必填) | 設定定時提醒鬧鐘（支援相對時間 `10m`、`2h` 或絕對時間 `14:30`、`2026-08-29 18:00`） | `/kurisu-alarm-set time:30m content:召開作戰會議` |
| `/kurisu-alarm-list` | 無 | 檢視個人所有尚未觸發的進行中鬧鐘清單 | `/kurisu-alarm-list` |
| `/kurisu-alarm-cancel` | `alarm_id` (必填) | 取消指定的定時鬧鐘提醒 | `/kurisu-alarm-cancel alarm_id:1` |
| `/kurisu-calendar-set` | `time` (必填), `content` (必填) | 新增個人行事曆事件，到期自動由 Webhook 推送提醒卡片 | `/kurisu-calendar-set time:2026-08-29 15:00 content:研討會報告` |
| `/kurisu-calendar-list` | `date` (選填) | 查看個人排程清單（可指定日期如 `2026-08-29` 查詢當日行程） | `/kurisu-calendar-list date:2026-08-29` |
| `/kurisu-calendar-cancel` | `event_id` (必填) | 取消指定的行事曆排程事件 | `/kurisu-calendar-cancel event_id:2` |

---

## 🛠️ 安裝與快速開始 (Quick Start)

### 1. 環境需求
* **Python 3.10 / 3.11 / 3.12**
* **Google Gemini API Key**（推薦使用 Gemini 3.1 Flash-Lite 或 2.5 Flash）
* **Discord Bot Token**（需於 Discord Developer Portal 開啟 `MESSAGE CONTENT INTENT`）

### 2. 下載與安裝依賴

```bash
# 複製專案
git clone https://github.com/AlanFu1018/friend-bot.git
cd friend-bot

# 建立並啟用虛擬環境 (可選但推薦)
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# 安裝所需 Python 套件
pip install -r requirements.txt
```

---

### 3. 環境變數設定 (`.env`)

在專案根目錄下建立或修改 `.env` 檔案：

```env
# Discord 機器人 Token
DISCORD_TOKEN=your_discord_bot_token_here

# Google Gemini API 金鑰
GEMINI_API_KEY=your_google_gemini_api_key_here
```

---

### 4. 設定檔詳細說明 (`config/config.yaml`)

主要設定檔位於 [`config/config.yaml`](config/config.yaml)，各項參數功能如下：

```yaml
# Discord 機器人頻道與行為設定
bot:
  # 【互動與回覆頻道】機器人會在此發言聊天的專屬頻道 ID 列表（留空 [] 代表全部頻道）
  reply_channel_ids: [1542526624979878019]

  # 【純監聽與記憶頻道】機器人會默默旁聽記錄、提取用戶畫像，但「不會主動回覆」的頻道 ID 列表
  listen_channel_ids: [935055001062088724, 1455531125589151899]
  
  # 是否在生成回覆時顯示「正在輸入... (Typing)」狀態
  show_typing: true
  
  # 單則 Discord 訊息最大字元數 (Discord 上限為 2000)
  max_message_length: 2000

# 訊息發送、自然氣泡與多人 Burst 聚合行為
chat_behavior:
  # 略過前綴（以這些前綴開頭的訊息不監聽、不記錄、不回覆）
  ignore_prefixes: ["#", "＃", "//"]
  enable_multi_bubble: true        # 是否開啟多氣泡分段發送
  bubble_target_length: 47         # 單則氣泡目標字數（到達時尋找標點符號切分）
  typing_delay_range: [0.6, 1.3]   # 多氣泡之間的打字停頓時間範圍（秒）
  
  # 多人群聊短時熱絡 (Burst) 聚合與動態引用回覆
  burst_reply:
    enable_burst_reply: true       # 是否啟用短時多人發言聚合與動態引用
    window_seconds: 4.5            # 短時收集窗口（秒）
    min_user_count: 2              # 觸發門檻（>= 2 人連續發言）
    max_burst_messages: 5          # 單次最多累積訊息上限

# 動態好感度與人際進展 (Favorability & Progression)
favorability:
  enable_favorability: true        # 是否啟用好感度系統
  default_favorability: 30         # 新用戶預設起始分數 (0~100)
  daily_gain_limit: 5              # 單日好感度增加上限（防刷保護）
  daily_loss_limit: 10             # 單日好感度扣分上限

# 聯網即時搜尋 (Web Search Tool)
web_search:
  enable_web_search: true          # 是否啟用即時聯網檢索
  search_top_k: 3                  # 搜尋抓取前 N 個網頁
  max_content_length_per_page: 2500 # 單一網頁抓取最大字元數

# 行事曆與 Webhook 定時提醒
calendar:
  webhook_url: ""                  # 全域預設 Webhook URL (選填)
  avatar_url: ""                   # Webhook 顯示的紅莉棲頭像 URL (選填)

# Gemini AI 模型設定
gemini:
  model: "gemini-3.1-flash-lite"   # 預設使用的 Gemini 模型
  temperature: 0.88                # 溫度值 (0.0 ~ 2.0)
  frequency_penalty: 0.0           # 懲罰設定 (預設 0.0)
  presence_penalty: 0.0
  max_output_tokens: 2048

# 三層全記憶系統
memory:
  short_term_history_limit: 15     # 第 1 層短期記憶訊息數量
  enable_auto_memory_extraction: true # 第 2 層是否自動提取群友畫像
  
  # 三軌事實檢索配額與加權設定
  facts_rag:
    speaker_max_total: 8           # 主要發言者帶入事實上限
    speaker_heat_limit: 2          # 軌道 1：核心高頻事實數量
    speaker_recent_limit: 2        # 軌道 3：最新近況事實數量
    others_max_total: 3            # 其他在場群友帶入事實上限
    others_heat_limit: 1           # 軌道 1：核心高頻事實數量
    others_recent_limit: 1         # 軌道 3：最新近況事實數量
    rag_hit_cooldown_seconds: 3600 # RAG 命中加權冷卻（秒）
    extraction_reaffirm_bonus: 3   # 提煉重複確認加權 (hits += 3)
    rag_hit_bonus: 1               # 即時話題命中加權 (hits += 1)

  enable_history_recall: true      # 第 3 層是否啟用 FTS5 深度歷史回憶
  history_recall_limit: 4          # 深度回憶檢索數量上限
  db_path: "data/friend_bot.db"    # 本地 SQLite 資料庫路徑
```

---

## 🚀 啟動與運行方式 (Running the Bot)

### 1. 本地標準啟動

```bash
python main.py
```

---

### 2. 命令列 (CLI / CMD) 參數說明

`main.py` 內建命令列參數，方便管理員在啟動時或維護期間進行記憶體維護與清理操作：

| 參數 | 說明 | 適用情境 |
| :--- | :--- | :--- |
| `--clear-memory` | **清空所有記憶**（包含所有頻道的對話歷史與所有群友的長期特徵畫像） | 完全重置機器人記憶狀態 |
| `--clear-history` | **僅清空對話歷史**（保留用戶長期特徵畫像與好感度） | 頻道發言紀錄過於冗長時進行瘦身 |
| `--clear-profiles` | **僅清空用戶特徵畫像**（保留歷史對話紀錄） | 重新提煉並建立群友長期畫像 |
| `--only-clear` | **僅執行清理**（執行完指定的清理動作後直接結束程式，不連線啟動 Bot） | 配合 Cron 或維護腳本進行批次記憶維護 |

#### 常用命令範例：

```bash
# 查看所有命令列參數說明
python main.py --help

# 僅清空所有對話歷史與畫像，不啟動機器人
python main.py --clear-memory --only-clear

# 清空對話歷史後直接連線啟動機器人
python main.py --clear-history

# 僅清空群友畫像（重新開始建立好感度與特徵），不啟動機器人
python main.py --clear-profiles --only-clear
```

---

### 3. 執行全自動化測試套件

專案包含覆蓋全功能模組的自動化驗證測試（鬧鐘、行事曆、三軌 RAG、好感度、Burst 聚合、情緒標籤渲染與前綴略過）：

```bash
python test/tests_verify.py
```

---

### 4. 雲端主機背景常駐部署 (Oracle Cloud / Ubuntu / Debian)

在雲端伺服器（如 OCI、AWS、GCP）上，建議使用 **`systemd`** 實現背景守護與重開機自動啟動：

#### ① 建立 systemd 服務檔
```bash
sudo nano /etc/systemd/system/friend-bot.service
```

#### ② 寫入以下設定（請依實際路徑與使用者修改）：
```ini
[Unit]
Description=Friend-Bot Discord Service (Makise Kurisu)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/friend-bot
ExecStart=/home/ubuntu/friend-bot/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

#### ③ 啟用並啟動服務
```bash
# 重新載入設定
sudo systemctl daemon-reload

# 設定開機自動啟動
sudo systemctl enable friend-bot

# 立即啟動
sudo systemctl start friend-bot

# 查看即時日誌
sudo journalctl -u friend-bot -f
```

---

## 📁 專案結構與文件導覽 (Directory Structure & Docs)

```
friend-bot/
├── config/
│   ├── config.yaml                        # 核心設定檔 (頻道、模型、記憶、好感度、行為、ignore_prefixes)
│   ├── kaomoji.yaml                       # 🎨 獨立 2ch / 日系顏文字庫 (傲嬌、破防、死魚眼、難過、沮喪等 10 大類)
│   └── persona.md                         # 🎭 牧瀨紅莉棲人設與情緒標籤規範指南
├── data/
│   └── friend_bot.db                      # 本地 SQLite 資料庫 (訊息、畫像、鬧鐘、行事曆)
├── doc/                                   # 📚 系統架構與技術實作專題文檔
│   ├── system_architecture_and_implementation.md # 🏗️ 系統架構與全技術實作說明書
│   ├── rag_mem.md                         # 🧠 三軌事實檢索 (3-Track Fact RAG & Heat) 設計書
│   ├── memory_sys_design.md               # 🧠 三層記憶架構與方案 C 監聽提煉設計
│   ├── persona_favorability_plan.md       # 💖 4 階好感度進展與防刷設計
│   ├── multi_user_burst_reply_plan.md     # 💬 多人群聊 Burst 與動態引用回覆設計
│   └── project_plan.md                    # 🗺️ 專案開發進度與里程碑 Roadmap
├── src/
│   └── friend_bot/
│       ├── ai/                            # Gemini Client、Prompt 模組、特徵提煉、搜尋工具
│       │   ├── prompts.py                 # System Instruction、Context 組裝與 Burst 提示詞
│       │   ├── gemini_client.py           # Google GenAI SDK 封裝與情緒標籤自動渲染
│       │   ├── memory_extractor.py        # 背景自動分析提煉用戶特徵與好感度微調
│       │   └── tools/                     # Web Search 聯網搜尋工具
│       ├── bot/                           # Discord 客戶端與指令處理
│       │   ├── client.py                  # FriendBotClient (Slash 指令、Burst 緩衝、忽略前綴與對話處理)
│       │   ├── handlers.py                # 訊息多氣泡切分與圖片附件處理
│       │   ├── commands/                  # 模組化 Slash Commands Mixins (Help, Search, Profile, Alarm, Calendar)
│       │   └── utils/                     # 鬧鐘、行事曆、Burst 與情緒渲染獨立工具庫
│       │       ├── alarm/                 # ⏰ 定時鬧鐘模組 (Manager, Scheduler, Parser)
│       │       ├── calendar/              # 📅 行事曆與 Webhook 排程模組 (Manager, Scheduler, Parser)
│       │       ├── burst/                 # 💬 多人短時熱絡緩衝模組 (BurstBufferManager)
│       │       └── emotion.py             # 🎨 情緒標籤動態渲染與防重複抽取器 (EmotionReplacer)
│       ├── memory/                        # 資料庫管理 (db.py, memory_manager.py)
│       └── core/                          # 設定載入與 Logger 工具
├── test/
│   └── tests_verify.py                    # 🧪 19 項全自動化單元測試腳本 (100% Passed)
├── .env                                   # 敏感 Token 與 API Key
├── requirements.txt                       # Python 相依套件列表
├── main.py                                # 程式啟動入口 (含 CLI / CMD 記憶管理參數)
└── README.md                              # 專案說明文件
```

---

## 📄 開源授權 (License)

本專案採用 [MIT License](LICENSE) 授權開源。
