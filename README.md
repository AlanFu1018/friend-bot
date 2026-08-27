# 🧬 Friend-Bot (克莉絲 / 牧瀨紅莉栖 Makise Kurisu)

<div align="center">

![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue?logo=python)
![Discord.py](https://img.shields.io/badge/Discord.py-v2.3%2B-5865F2?logo=discord)
![Gemini API](https://img.shields.io/badge/Google%20Gemini-2.5%20%2F%20Flash-orange?logo=google)
![SQLite FTS5](https://img.shields.io/badge/Storage-SQLite3%20FTS5-003B57?logo=sqlite)
![License](https://img.shields.io/badge/License-MIT-green)

**基於 Google Gemini 2.5 與三層記憶架構打造的擬真傲嬌 Discord 群友機器人**  
*具備長期人像記憶、深度回憶、即時聯網搜尋、獨立定時鬧鐘與 Webhook 行事曆排程系統。*

</div>

---

## 🌟 核心特色 (Key Features)

- 🎭 **真實群友人設 (Living Persona)**：深度沉浸於《命運石之門》牧瀨紅莉栖（Makise Kurisu）人設，具備傲嬌、口是心非、重感情但理智聰慧的語氣。支援讀取外部 [`persona.md`](file:///C:/ALL%20FILES/Code/friend-bot/persona.md) 自由調整。
- 🧠 **三層全記憶系統 (Three-Tier Memory Architecture)**：
  - **第 1 層（短期記憶）**：頻道最新對話滑動窗口。
  - **第 2 層（用戶畫像）**：背景自動提煉群友性格、喜好、習慣與事實特徵。
  - **第 3 層（跨頻道深度回憶）**：基於 SQLite FTS5 全文檢索，自動檢索過去相關話題與歷史記憶。
- 🔍 **即時聯網搜尋 (Real-Time Web Search)**：整合 DuckDuckGo 與 Jina AI Reader，即時檢索最新時事、新聞與天氣，並以紅莉栖口吻整理輸出。
- ⏰ **獨立定時鬧鐘 (Alarm Reminder)**：支援各類自然時間格式設定提醒，到期時在頻道發送專屬傲嬌對白與醒目卡片。
- 📅 **Webhook 行事曆與智慧行程查詢 (Calendar & Natural Scheduling)**：
  - 支援設定個人行事曆與自訂 Webhook 推送。
  - **支援日常直接對話查詢**：在聊天時問「*我今天有什麼行程？*」或「*幫我看明天有安排嗎？*」，紅莉栖會自動檢索行事曆並為你解答！
- 💬 **擬真群友行為 (Multi-Bubble Typing)**：長回覆自動切分自然氣泡並模擬打字等待時間，告別冰冷的大段機器人輸出。

---

## 📋 指令列表與功能 (Commands & Features)

在 Discord 聊天框輸入 `/kurisu-` 即可自動跳出完整的自動補全選單與參數提示：

| 指令 (Slash Command) | 功能說明 | 參數與範例 |
| :--- | :--- | :--- |
| **`/kurisu-help`** | **【功能手冊】** 展示完整指令說明 Embed 卡片。 | `/kurisu-help` |
| **`/kurisu-search`** | **【強制聯網搜尋】** 檢索即時新聞、天氣或時事資料並以紅莉栖風格回覆。 | `query:台北現在天氣`、`query:2026科技新知` |
| **`/kurisu-profile`** | **【查詢個人畫像】** 查看機器人為你或指定群友建立的長期記憶特徵與印象。 | `/kurisu-profile` 或 `user:@群友` |
| **`/kurisu-alarm-set`** | **【設定定時鬧鐘】** 設定提醒時刻，到期時以紅莉栖專屬對白醒目提醒。 | `time:2026/8/27/15/30`、`time:15:30`、`time:30m`，`content:搶票` |
| **`/kurisu-alarm-list`** | **【查看鬧鐘清單】** 查看自己名下所有等待觸發的鬧鐘。 | `/kurisu-alarm-list` |
| **`/kurisu-alarm-cancel`** | **【取消定時鬧鐘】** 取消指定的待觸發鬧鐘。 | `alarm_id:1` |
| **`/kurisu-calendar-set`** | **【登記行事曆排程】** 登記日程事件，支援自訂 Webhook 與日常聊天查詢。 | `time:2026-08-27 15:30`，`content:實驗室報告`，`webhook_url:[選填]` |
| **`/kurisu-calendar-list`** | **【查看行事曆清單】** 查看未來一個月內的所有待辦日程。 | `/kurisu-calendar-list` |
| **`/kurisu-calendar-cancel`** | **【取消行事曆排程】** 取消指定的行事曆日程。 | `event_id:1` |
| **💬 日常直接對話** | **【智慧對話與排程查詢】** 在頻道聊天或直接問「*我今天有什麼行程？*」，紅莉栖會自動查詢行事曆並回答。 | 直接發送文字或圖片 |

---

## ⚙️ 環境需求與設置 (Setup & Installation)

### 1. 系統需求
- **Python**：`3.10` / `3.11` / `3.12`
- **作業系統**：Windows / Linux (Ubuntu / Debian / Oracle Linux) / macOS

---

### 2. 下載專案與建立虛擬環境

```bash
# 複製專案庫
git clone https://github.com/AlanFu1018/friend-bot.git
cd friend-bot

# 建立並啟用 Python 虛擬環境
python -m venv venv

# Windows 啟用方式:
venv\Scripts\activate

# Linux / macOS 啟用方式:
source venv/bin/activate

# 安裝相依套件
pip install -r requirements.txt
```

---

### 3. 設定環境變數 (`.env`)

在專案根目錄建立或複製 `.env` 檔案：

```ini
# Discord Bot Token (從 Discord Developer Portal 取得)
DISCORD_BOT_TOKEN="你的_DISCORD_BOT_TOKEN"

# Google Gemini API Key (從 Google AI Studio 取得)
GEMINI_API_KEY="你的_GEMINI_API_KEY"

# (選填) 覆寫設定檔路徑
CONFIG_PATH="config/config.yaml"
```

> [!IMPORTANT]
> **Discord Bot 權限設定**：
> 請務必在 [Discord Developer Portal](https://discord.com/developers/applications) -> **Bot** 頁面中開啟 **Privileged Gateway Intents**：
> - ✅ **MESSAGE CONTENT INTENT**（必要，讀取對話文字）
> - ✅ **SERVER MEMBERS INTENT**
> - ✅ **PRESENCE INTENT**

---

### 4. 設定檔詳細說明 (`config/config.yaml`)

主要設定檔位於 [`config/config.yaml`](file:///C:/ALL%20FILES/Code/friend-bot/config/config.yaml)，各項參數功能如下：

```yaml
# Discord 機器人頻道與行為設定
bot:
  # 【互動與回覆頻道】機器人會在此發言聊天的專屬頻道 ID 列表（留空 [] 代表允許全部頻道）
  reply_channel_ids: [1542526624979878019]

  # 【純監聽與記憶頻道】機器人會默默旁聽記錄、提取用戶畫像，但「不會主動回覆」的頻道 ID 列表
  listen_channel_ids: [935055001062088724, 1455531125589151899]
  
  # 是否在生成回覆時顯示「正在輸入... (Typing)」狀態
  show_typing: true
  
  # 單則 Discord 訊息最大字元數 (Discord 上限為 2000)
  max_message_length: 2000

# 訊息發送與自然氣泡行為
chat_behavior:
  enable_multi_bubble: true        # 是否開啟多氣泡分段發送
  bubble_target_length: 35         # 單則氣泡目標字數（到達時尋找標點符號切分）
  typing_delay_range: [0.6, 1.3]   # 多氣泡之間的打字停頓時間範圍（秒）

# 聯網即時搜尋 (Web Search Tool)
web_search:
  enable_web_search: true          # 是否啟用即時聯網檢索
  search_top_k: 3                  # 搜尋抓取前 N 個網頁
  max_content_length_per_page: 2500 # 單一網頁抓取最大字元數

# 行事曆與 Webhook 定時提醒
calendar:
  webhook_url: ""                  # 全域預設 Webhook URL (選填)
  avatar_url: ""                   # Webhook 顯示的紅莉栖頭像 URL (選填)

# Gemini AI 模型設定
gemini:
  model: "gemini-3.1-flash-lite"   # 推薦使用 gemini-2.5-flash 或 gemini-3.1-flash-lite
  temperature: 0.85                # 溫度值 (0.0 ~ 2.0)：數值越高越幽默隨機
  max_output_tokens: 2048

# 三層全記憶系統
memory:
  short_term_history_limit: 15     # 第 1 層短期記憶訊息數量
  enable_auto_memory_extraction: true # 第 2 層是否自動提取群友畫像
  enable_history_recall: true      # 第 3 層是否啟用 FTS5 深度歷史回憶
  history_recall_limit: 4          # 深度回憶檢索數量上限
  db_path: "data/friend_bot.db"    # 本地 SQLite 資料庫路徑

# 機器人人設設定
persona:
  bot_name: "克莉絲"               # 機器人暱稱
  persona_file: "persona.md"       # 人設 Markdown 檔案路徑
```

---

## 🚀 啟動與運行方式 (Running the Bot)

### 1. 本地啟動 (開發 / 測試)

```bash
python main.py
```

---

### 2. 雲端主機背景常駐部署 (Oracle Cloud / Ubuntu / Debian)

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

## 📁 專案結構 (Directory Structure)

```
friend-bot/
├── config/
│   └── config.yaml               # 核心設定檔 (頻道、模型、記憶、行為設定)
├── data/
│   └── friend_bot.db             # 本地 SQLite 資料庫 (訊息、畫像、鬧鐘、行事曆)
├── src/
│   └── friend_bot/
│       ├── ai/                   # Gemini Client、Prompt 模組、特徵提煉、搜尋工具
│       │   ├── prompts.py        # System Instruction 與三層記憶 Context 組裝
│       │   ├── gemini_client.py  # Google Gemini SDK API 呼叫封裝
│       │   ├── memory_extractor.py # 背景自動分析提煉用戶特徵
│       │   └── tools/            # Web Search 聯網搜尋工具
│       ├── bot/                  # Discord 客戶端與指令處理
│       │   ├── client.py         # FriendBotClient (Slash 指令、on_message 監聽)
│       │   ├── handlers.py       # 訊息多氣泡切分與圖片附件處理
│       │   └── utils/            # 鬧鐘與行事曆獨立工具庫
│       │       ├── alarm/        # ⏰ 定時鬧鐘模組 (Manager, Scheduler, Parser)
│       │       └── calendar/     # 📅 行事曆與 Webhook 排程模組 (Manager, Scheduler, Parser)
│       ├── memory/               # 資料庫管理 (db.py, memory_manager.py)
│       └── core/                 # 設定載入與 Logger 工具
├── test/
│   └── tests_verify.py           # 單元測試驗證腳本
├── .env                          # 敏感 Token 與 API Key
├── persona.md                    # 牧瀨紅莉栖人設詳細設定檔
├── requirements.txt              # Python 相依套件列表
├── main.py                       # 程式啟動入口
└── README.md                     # 專案說明文件
```

---

## 📄 開源授權 (License)

本專案採用 [MIT License](LICENSE) 授權開源。
