# 🧪 Friend-Bot (克莉絲 / 牧瀨紅莉棲 Makise Kurisu)

<div align="center">

![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue?logo=python)
![Discord.py](https://img.shields.io/badge/Discord.py-v2.3%2B-5865F2?logo=discord)
![Gemini API](https://img.shields.io/badge/Google%20Gemini-3.1%20%2F%202.5-orange?logo=google)
![Storage](https://img.shields.io/badge/Storage-SQLite3%20FTS5-003B57?logo=sqlite)
![Tests](https://img.shields.io/badge/Tests-68%2F68%20Passed-brightgreen?logo=pytest)
![License](https://img.shields.io/badge/License-MIT-green)

**基於 Google Gemini 與三層記憶架構打造的擬真傲嬌 Discord 群友機器人**

*具備跨對話長期記憶、多人身分識別與綽號辨識、三軌事實檢索、情緒顏文字渲染、動態好感度階梯、多人群聊聚合引用回覆、即時聯網搜尋、定時鬧鐘與 Webhook 行事曆，以及語音頻道感知的音樂推薦。*

[📖 系統架構總覽](doc/architecture.md) ｜ [🧠 記憶系統設計](doc/memory_sys_design.md) ｜ [⚙️ Prompt 組裝管線](doc/prompt_pipeline.md) ｜ [📚 完整文件索引](doc/architecture.md#2-功能模組與規格書)

</div>

---

## 🌟 核心特色 (Key Features)

### 🧠 三層長期記憶

| 層 | 內容 |
| :--- | :--- |
| **第 1 層** 短期對話 | 該頻道最近 N 則訊息的滑動窗口 |
| **第 2 層** 長期畫像 | 事實、綽號、互動印象、好感度（永久保存） |
| **第 3 層** 深度回憶 | SQLite FTS5 跨頻道全文檢索，自動聯想過去話題 |

**三軌事實檢索**：事實累積超過配額時，以三條軌道併選——**熱度**（標誌性人設常駐）、**話題**（聊到什麼喚醒什麼）、**最新**（近況不被舊事實淹沒）。

**記憶保護**：事實採增量聯集，模型回傳空值時舊記憶 100% 保留；重複確認會加權熱度；**新事實否定舊事實時自動取代**（例如「我已經不喜歡台北了」會推翻舊的「喜歡台北」，而非誤判為重複確認）；`remove_facts` 需達最低引述門檻才執行，避免籠統的刪除詞誤刪無關事實。

**中文檢索**：FTS5 預設分詞器會把整串中文視為單一 token，因此索引與查詢兩側都由應用層以 n-gram 切詞，確保「拉麵」「通宵」這類二字詞能正常召回。

### 👥 多人身分識別

四個來源決定「這段對話牽涉到誰」：

| 來源 | 說明 | 進 Prompt | 可被寫入事實 |
| :--- | :--- | :---: | :---: |
| 發言者 | Discord author id | ✅ | ✅ |
| @提及 | `message.mentions`（權威來源） | ✅ | ✅ |
| 名稱／**綽號** | 顯示名稱 ∪ 別名 | ✅ | ✅ |
| 語音／文字在場者 | 同語音頻道、近期發言者 | ✅ | ❌ |

**讀寫不對稱是刻意的**：讀錯只是一次回覆變差，寫錯是把事實永久記到別人頭上。同名一律排除不猜；模型輸出的 `user_id` 不在對話上下文內一律拒絕（防提示詞注入）。

### 🏷️ 綽號辨識 (Alias)

Discord 顯示名稱往往不是群友實際互稱的稱呼。系統可從對話中**自動學習綽號**，須通過五道校驗才生效（格式合格、不與任何既有名稱碰撞、當事人須在場、數量上限、**記錄來源可稽核**）。也可用 `/kurisu-alias` 手動管理。

### 🎭 人格與好感度

- 人設讀取外部 [`config/persona.md`](config/persona.md)，改人格不需動程式碼。
- **4 階關係階級**：`Tier 1 陌生警戒 (0~19)` → `Tier 2 熟識群友 (20~49)` → `Tier 3 實驗室夥伴 (50~79)` → `Tier 4 靈魂共鳴 (80~100)`，逐人注入，同一次回覆可對不同人展現不同親密度。
- **隱密更新與每日防刷**：對話中絕無系統提示，好感度僅能透過 `/kurisu-profile` 查看。

### 🎨 情緒顏文字渲染

模型輸出 `[emotion:tsundere]` 這類標籤，程式從 [`config/kaomoji.yaml`](config/kaomoji.yaml) 的十大類顏文字庫隨機抽取。**防連續重複機制**確保一個顏文字要等該類別其他一半的選項用過才可能再出現。

### 💬 多人群聊聚合與動態引用

短時間內 2 人以上發言時自動聚合，模型挑選核心回應對象並以 **Discord 原生引用回覆**發送，兼顧其他在場群友的話題。

### 🎵 語音頻道感知與音樂推薦

發言人在語音頻道時，紅莉栖知道誰和他在一起，聊到音樂時會**結合在場者的已知喜好**推薦歌曲並附上可複製的指令。

> ⚠️ Discord API **不允許 bot 呼叫另一個 bot 的 Slash 指令**，因此這裡只做「建議」——實際播放需群友自行執行指令。詳見 [`doc/music_suggestion.md`](doc/music_suggestion.md)。

### 🔍 即時聯網搜尋

DuckDuckGo 檢索 + Jina AI Reader 抓取網頁正文的兩段式流程。模型可自主判斷需要時調用，或用 `/kurisu-search` 強制觸發。

### ⏰ 定時鬧鐘與 📅 Webhook 行事曆

兩套獨立系統。鬧鐘是一次性提醒；**行事曆會進入對話上下文**——可直接在聊天中問「我明天有什麼安排？」。時間格式支援相對時間（`10m`、`1h30m`）與各種絕對格式（`2026/8/27/15/30`、`15:30`、`2026年8月27日15點30分`）。

### 🛡️ 前綴繞過

以 `#`、`＃`、`//` 開頭的訊息**完全被忽略**：不監聽、不入庫、不提煉、不回覆，方便群友內部交流或旁白備註。

---

## ⚡ Slash 指令清單

| 指令 | 參數 | 說明 |
| :--- | :--- | :--- |
| `/kurisu-help` | 無 | 查看所有可用指令與功能介紹 |
| `/kurisu-profile` | `user`（選填） | 查看自己或群友的好感度、關係階級與長期特徵；指定 bot 自己則顯示人設卡 |
| `/kurisu-alias` | `action`（必填）、`alias`、`user` | 管理綽號：`add` / `remove` / `list`。代他人操作需管理伺服器權限 |
| `/kurisu-search` | `query`（必填） | 強制聯網搜尋並以紅莉栖口吻整理回覆 |
| `/kurisu-alarm-set` | `time`、`content` | 設定定時提醒鬧鐘 |
| `/kurisu-alarm-list` | 無 | 檢視個人所有待觸發鬧鐘 |
| `/kurisu-alarm-cancel` | `alarm_id` | 取消指定鬧鐘 |
| `/kurisu-calendar-set` | `time`、`content`、`webhook_url`（選填） | 登記行事曆排程，到期由 Webhook 推送 |
| `/kurisu-calendar-list` | 無 | 查看未來一個月的排程清單 |
| `/kurisu-calendar-cancel` | `event_id` | 取消指定排程 |

---

## 🛠️ 安裝與快速開始

### 1. 環境需求

* **Python 3.10 / 3.11 / 3.12**
* **Google Gemini API Key**
* **Discord Bot Token**——需於 Discord Developer Portal 開啟 `MESSAGE CONTENT INTENT`

> 語音頻道感知所需的 `voice_states` intent 已內含於 `Intents.default()`，**不需額外開啟**。

### 2. 下載與安裝

```bash
git clone https://github.com/AlanFu1018/friend-bot.git
cd friend-bot

python -m venv venv
.\venv\Scripts\activate          # Windows
source venv/bin/activate         # Linux / macOS

pip install -r requirements.txt
```

### 3. 環境變數 (`.env`)

```env
DISCORD_TOKEN=your_discord_bot_token_here
GEMINI_API_KEY=your_google_gemini_api_key_here

# 選填：頻道 ID 也可寫在這裡（與 config.yaml 取聯集，而非覆蓋）
# REPLY_CHANNEL_IDS=123456789012345678
# LISTEN_CHANNEL_IDS=112233445566778899
```

### 4. 主要設定 (`config/config.yaml`)

設定檔本身有完整的中文註解說明每一項的作用與調整取捨，這裡只列最常調整的幾項。完整參考見 [`doc/configuration.md`](doc/configuration.md)。

```yaml
bot:
  reply_channel_ids: []            # 會回覆的頻道（留空則不回覆任何頻道）
  listen_channel_ids: []           # 只記錄不回覆的頻道

memory:
  short_term_history_limit: 30     # 送入 Prompt 的近期訊息數
  listen_debounce_seconds: 4.0     # 監聽頻道靜默多久才打包提煉
  listen_max_queue_messages: 30    # 佇列滿此數量即提煉（防抖的保險）
  history_recall_min_score: 2      # 深度回憶門檻：至少共享一個二字詞

  alias:
    enable_alias_learning: true    # 是否讓提煉自動學習綽號
    max_aliases_per_user: 5

music:
  enable_music_suggestion: true
  play_command: "/play"            # 換音樂 bot 時只要改這裡

favorability:
  default_favorability: 30
  daily_gain_limit: 15             # 每日加分上限（防刷）

gemini:
  model: "gemini-3.1-flash-lite"
  temperature: 0.87
```

> **調參建議**：`listen_debounce_seconds` 是提升監聽頻道歸屬品質最有效的旋鈕——調大能讓模型一次看到更完整的多輪脈絡（「A 問鍵盤型號、B 隔兩分鐘回答」這種因果才接得起來）。已有則數上限兜底，可放心調整。

---

## 🚀 啟動與運行

```bash
python main.py
```

首次啟動會自動建立資料庫並執行必要的 schema 遷移（`PRAGMA user_version`），log 會顯示進度。

### 命令列參數

| 參數 | 說明 |
| :--- | :--- |
| `--clear-memory` | 清空**所有**記憶（對話歷史 + 群友畫像 + 行事曆 + 鬧鐘） |
| `--clear-history` | 僅清對話歷史，**保留畫像與好感度** |
| `--clear-profiles` | 僅清群友畫像，保留歷史 |
| `--only-clear` | 清理後直接結束，不啟動 Bot |

```bash
python main.py --help                          # 查看說明
python main.py --clear-memory --only-clear     # 完全重置後結束
python main.py --clear-history                 # 清歷史後啟動
```

> ⚠️ 這些操作**不可逆且無確認提示**。`--clear-profiles` 會抹掉所有累積的事實、綽號與好感度——那是重建成本最高的資料。

### 自動化測試

```bash
python test/tests_verify.py
```

68 項測試，涵蓋時間解析、記憶檢索、跨使用者歸屬、事實保護與否定推翻、綽號校驗、語音在場解析、Burst 聚合、指令註冊與顏文字渲染。

> 測試直接以指令稿執行——`test/` 沒有 `__init__.py`，不能用 `python -m unittest`。

### 雲端背景常駐 (systemd)

```bash
sudo nano /etc/systemd/system/friend-bot.service
```

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable friend-bot
sudo systemctl start friend-bot
sudo journalctl -u friend-bot -f      # 查看即時日誌
```

---

## 📁 專案結構

```
friend-bot/
├── config/
│   ├── config.yaml            # 核心設定檔（含完整中文註解）
│   ├── kaomoji.yaml           # 顏文字庫（十大情緒類別）
│   └── persona.md             # 人設與情緒標籤規範
├── data/
│   └── friend_bot.db          # SQLite（訊息、畫像、鬧鐘、行事曆）
├── doc/                       # 📚 規格書與問題記錄，見下方
├── src/friend_bot/
│   ├── core/                  # config.py、logger.py、emotion.py（顏文字渲染）
│   ├── memory/                # db.py（schema 與遷移）、memory_manager.py（三層記憶核心）
│   ├── ai/                    # gemini_client.py、prompts.py、memory_extractor.py、tools/
│   └── bot/                   # client.py（主流程）、handlers.py
│       ├── commands/          # Slash 指令 Mixin
│       └── utils/             # burst/、alarm/、calendar/
├── test/tests_verify.py       # 68 項自動化測試
├── main.py                    # 啟動入口（含 CLI 記憶管理參數）
└── requirements.txt
```

各層依賴方向是**嚴格單向**的 `bot → ai → memory → core`，任何一層都可單獨匯入。

---

## 📚 文件導覽

### 規格書（現在怎麼運作）

| 文件 | 內容 |
| :--- | :--- |
| [`architecture.md`](doc/architecture.md) | **入口**——全景架構與模組索引 |
| [`chat_and_reply.md`](doc/chat_and_reply.md) | 頻道模式、Burst 聚合、多氣泡分段、圖片理解 |
| [`memory_sys_design.md`](doc/memory_sys_design.md) | 記憶架構與每個設計決定的理由 |
| [`prompt_pipeline.md`](doc/prompt_pipeline.md) | Prompt 如何組成（執行期細節，含行號） |
| [`persona_and_favorability.md`](doc/persona_and_favorability.md) | 人格、四階關係、好感度計算 |
| [`music_suggestion.md`](doc/music_suggestion.md) | 語音頻道感知與音樂推薦 |
| [`web_search.md`](doc/web_search.md) | 兩段式檢索與 Tool Calling |
| [`calendar_and_alarm.md`](doc/calendar_and_alarm.md) | 兩套定時系統與時間解析 |
| [`emotion_kaomoji.md`](doc/emotion_kaomoji.md) | 標籤渲染與防重複 |
| [`commands.md`](doc/commands.md) | 指令、Mixin 架構、權限模型 |
| [`configuration.md`](doc/configuration.md) | 設定層級、日誌、資料庫遷移 |

### 問題記錄（為什麼會變成這樣）

| 文件 | 內容 |
| :--- | :--- |
| [`mem_sys_bugs.md`](doc/mem_sys_bugs.md) | 記憶系統缺陷清單、實測證據與修復記錄 |
| [`improv.md`](doc/improv.md) | 並發與安全面的程式碼審查記錄 |

---

## 📄 開源授權

本專案採用 [MIT License](LICENSE) 授權開源。
