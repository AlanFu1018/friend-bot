# 🤖 Friend-Bot 專案規格與開發計畫書

本文檔記錄「具備全記憶、多人多維畫像檢索、定時鬧鐘與 Webhook 行事曆排程的 Discord 聊天機器人 (`friend-bot`)」之完整系統規格、架構設計與開發計畫表。

---

## 📌 一、 專案核心規格 (Project Specifications)

| 項目 | 規格設定 | 說明 |
| :--- | :--- | :--- |
| **專案名稱** | `friend-bot` | Discord 全記憶型對話機器人（牧瀨紅莉栖人設） |
| **LLM 模型** | `gemini-2.5-flash` | 使用官方 Google GenAI SDK，可透過 `config/config.yaml` 彈性切換 |
| **角色定位 (Persona)** | 牧瀨紅莉栖 (Kurisu Makise) | 傲嬌、天才神經科學家、嘴硬心軟、說話偶爾帶有 @channeler 網絡用語與科學嚴謹風 |
| **多模態支援** | 支援圖片/附件解析 | 當使用者在 Discord 上傳圖片時，Gemini 能結合影像與歷史上下文理解並回應 |
| **雙軌頻道機制** | **回覆頻道** vs **純監聽記憶頻道** | • `reply_channel_ids`：機器人會主動聊天互動的專屬頻道<br>• `listen_channel_ids`：機器人默默旁聽並批次提煉用戶特徵，但**不主動回覆** |
| **原生指令集** | `/kurisu-` 系列 Slash Commands | • `/kurisu-help`：指令手冊<br>• `/kurisu-profile`：長期畫像查詢<br>• `/kurisu-search`：強制聯網搜尋<br>• `/kurisu-alarm-*`：定時鬧鐘管理<br>• `/kurisu-calendar-*`：Webhook 行事曆排程管理 |
| **設定管理** | `config/config.yaml` + `.env` | 機密金鑰放 `.env`，其餘模型參數、頻道清單、記憶深度、Persona 提示詞放 `config/config.yaml` |

---

## 🧠 二、 多維立體記憶系統架構 (Multi-Tier Memory Architecture)

```mermaid
flowchart TD
    subgraph 訊息輸入來源[Discord 訊息輸入]
        A1[回覆專屬頻道 Reply Channels]
        A2[純監聽記憶頻道 Listen Channels]
    end

    subgraph 永久歷史儲存庫[永久歷史訊息庫 (SQLite + FTS5)]
        DB[(永久儲存所有頻道歷史訊息<br>支援跨頻道全文索引檢索<br>含 extracted 狀態追蹤)]
    end

    subgraph 記憶檢索與調度組合拳[多維記憶檢索與調度組合拳]
        T1[第 1 層：短期滑動視窗<br>當前回覆頻道最近 15 則即時上下文]
        T2[第 2 層：多人多維長期畫像<br>A+B+C 組合拳：提取發言者 + 在場/提及群友畫像]
        T3[第 3 層：歷史跨頻道深度回憶<br>關鍵字/話題相關歷史片段檢索]
        T4[排程層：行事曆排程摘要<br>自動注入發言者已登記之日程]
    end

    subgraph 提煉與防洗白管線[背景提煉與防洗白安全管線 (方案 C)]
        E1[平日批次累積：滿 15 則或靜默 10 分鐘]
        E2[主對話 JIT 觸發：優先統合發言者未消化發言]
        E3[多實體歸屬提煉：精準區分自述 vs 他人轉述]
        E4[底層強制聯集保護：歷史事實 100% 保留 + remove_facts 顯式更正]
    end

    A1 -->|即時存庫 extracted=1| DB
    A2 -->|即時存庫 extracted=0| DB
    A2 -.->|加入防抖緩衝隊列| E1

    A1 --> T1
    DB --> T1
    DB --> T2
    DB -->|跨頻道話題匹配| T3
    DB --> T4

    A1 -.->|JIT 檢查發言者待處理發言| E2
    E1 & E2 --> E3 --> E4 -->|安全寫回| DB

    T1 & T2 & T3 & T4 --> Prompt[合成多層上下文 Prompt + 牧瀨紅莉栖 Persona]
    Prompt --> Gemini[Gemini 2.5 Flash API]
    Gemini --> Reply[發送回覆至【回覆專屬頻道】]
```

### 1. 雙軌頻道與記憶檢索 (A + B + C 組合拳)
* **方案 A (顯式 Mention / 回覆對象)**：解析訊息中的 `@群友` 或 Reply 引用訊息原作者。
* **方案 B (純文字暱稱/別名掃描)**：比對已知群友字典庫（如發言中提到「桶子」）。
* **方案 C (短期在場活躍群友)**：提取近期對話活躍者，掌握當前聊天室在場名單。

### 2. 監聽頻道批次累積與 JIT 按需統合提煉 (方案 C)
* **平日背景批次**：監聽頻道訊息累積滿 15 則或靜默 10 分鐘自動打包一次多輪對話給 Gemini 全局分析，節省 85% API 開銷。
* **主頻道 JIT 統合**：當發言者在回覆頻道發言時，優先非同步消化其在監聽頻道的未提煉發言，確保記憶即時。

### 3. 多實體跨用戶特徵歸屬與防洗白機制
* **精準實體歸屬**：他人轉述特徵精準寫入被提及者畫像，發言者自述特徵寫入發言者本人。
* **歷史事實增量聯集 (Incremental Union)**：日常對話即使 LLM 回傳空事實 `[]`，歷史事實永不被覆蓋洗白。
* **顯式自我更正 (`remove_facts`)**：用戶搬家或澄清時，精準剔除舊事實並寫入新事實。

---

## 📁 三、 專案檔案組織架構 (File Structure)

```text
friend-bot/
├── doc/
│   ├── project_plan.md        # 專案規格與實作計畫書 (本文檔)
│   ├── memory_sys_design.md   # 多維立體記憶系統架構設計詳解
│   ├── memory_improve.md      # 記憶系統改良演進記錄
│   └── discord_portal_setup_guide.md # Discord 開發者後台設置指南
├── config/
│   └── config.yaml            # 核心設定檔（回覆頻道、監聽頻道、模型參數、Persona 提示詞）
├── .env.example               # 環境變數範本（Token、API Key 等機密金鑰）
├── .gitignore                 # Git 忽略檔案清單
├── requirements.txt           # 專案依賴套件清單
├── README.md                  # 專案說明文件、安裝指南與指令手冊
├── src/
│   └── friend_bot/
│       ├── __init__.py
│       ├── main.py            # 機器人啟動入口點
│       ├── core/              # 核心配置與日誌
│       │   ├── __init__.py
│       │   ├── config.py      # 設定載入器（整合 config.yaml 與 .env）
│       │   └── logger.py      # 全域 Logging 設定
│       ├── bot/               # Discord 機器人相關邏輯
│       │   ├── __init__.py
│       │   ├── client.py      # Discord Client（斜線指令、雙軌監聽、多維記憶串接）
│       │   ├── handlers.py    # 多模態圖片下載、訊息分段發送
│       │   └── utils/         # 獨立業務模組
│       │       ├── alarm/     # 定時提醒鬧鐘模組（AlarmManager, AlarmScheduler）
│       │       └── calendar/  # Webhook 行事曆排程模組（CalendarManager, CalendarScheduler）
│       ├── ai/                # AI / LLM 生成與多模態模組
│       │   ├── __init__.py
│       │   ├── gemini_client.py    # Google GenAI SDK 封裝
│       │   ├── prompts.py          # 系統人設、三層記憶 Context、多實體/批次提煉 Prompt
│       │   └── memory_extractor.py # 多輪對話批次提煉、JIT 統合、事實增量聯集與自我更正
│       └── memory/            # 記憶持久化與檢索管理
│           ├── __init__.py
│           ├── db.py          # SQLite 連線、messages, user_profiles, calendar_events 等資料表
│           └── memory_manager.py # A+B+C 多人畫像檢索、FTS5 全文搜尋、未提煉訊息狀態流轉
├── test/
│   └── tests_verify.py        # 整合單元測試腳本（鬧鐘、行事曆、多人畫像、跨用戶歸屬、防洗白、批次提煉）
└── data/                      # 本地運行資料儲存目錄（自動建立）
    └── friend_bot.db          # SQLite 本地資料庫
```

---

## 📅 四、 實作進度與里程碑 (Roadmap Status)

| 階段 | 任務項目 | 具體工作與產出 | 狀態 |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **專案結構與環境配置** | • 建立 `requirements.txt`、`config/config.yaml`、`.env.example`<br>• 實作 `src/friend_bot/core/config.py` 與 `logger.py` | ✅ 已完成 |
| **Phase 2** | **永久資料庫與三層記憶模組** | • 建立 `db.py`（支援 `messages`、`messages_fts`、`user_profiles`、`extracted` 旗標）<br>• 實作 `memory_manager.py`（A+B+C 多維畫像檢索、FTS5 全文檢索） | ✅ 已完成 |
| **Phase 3** | **Gemini 引擎與記憶提煉器** | • 實作 `gemini_client.py`（多模態支援、Tools 支援）<br>• 實作 `prompts.py`（人設、多維 Context 注入、多實體提煉 Prompt）<br>• 實作 `memory_extractor.py`（跨用戶歸屬、事實增量聯集保護、`remove_facts` 更正） | ✅ 已完成 |
| **Phase 4** | **鬧鐘與 Webhook 行事曆解耦** | • 建立獨立 `src/friend_bot/bot/utils/alarm/`（定時鬧鐘管理與調度器）<br>• 建立獨立 `src/friend_bot/bot/utils/calendar/`（Webhook 行事曆管理與調度器） | ✅ 已完成 |
| **Phase 5** | **監聽頻道記憶改良 (方案 C)** | • 實作監聽頻道 15 則 / 10 分鐘防抖緩衝隊列<br>• 實作主頻道發言時 JIT 優先按需統合提煉<br>• 批次提煉後自動標記 `extracted = 1`，節省 85% API 開銷 | ✅ 已完成 |
| **Phase 6** | **Discord 原生 Slash 指令** | • 實作 `/kurisu-help`、`/kurisu-search`、`/kurisu-profile`<br>• 實作 `/kurisu-alarm-*` 與 `/kurisu-calendar-*` 全套指令 | ✅ 已完成 |
| **Phase 7** | **測試與全模組自動化驗證** | • 實作 `test/tests_verify.py`，涵蓋 9 大核心測試用例，100% 測試通過 | ✅ 已完成 |
