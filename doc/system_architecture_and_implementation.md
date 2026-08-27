# 🏗️ Friend-Bot 系統架構與技術實作說明書
*(System Architecture and Implementation Manual)*

本文檔完整整理 **Friend-Bot（牧瀨紅莉栖 Makise Kurisu）** 的技術核心、模組架構與演算法實作細節，供開發者、維護者與進階使用者深入了解系統內部運作原理。

---

## 📑 目錄 (Table of Contents)
- [一、系統全景架構圖 (Architecture Overview)](#一系統全景架構圖-architecture-overview)
- [二、三層全記憶與多人畫像模組 (Three-Tier Memory Architecture)](#二三層全記憶與多人畫像模組-three-tier-memory-architecture)
- [三、動態好感度與人際進展系統 (Favorability & Progression System)](#三動態好感度與人際進展系統-favorability--progression-system)
- [四、多人群聊短時熱絡 (Burst) 與動態引用回覆 (Burst & Dynamic Reply)](#四多人群聊短時熱絡-burst-與動態引用回覆-burst--dynamic-reply)
- [五、獨立定時鬧鐘與 Webhook 行事曆系統 (Alarm & Calendar Scheduling)](#五獨立定時鬧鐘與-webhook-行事曆系統-alarm--calendar-scheduling)
- [六、即時聯網搜尋與擬真多氣泡發送 (Web Search & Natural Chat Behavior)](#六即時聯網搜尋與擬真多氣泡發送-web-search--natural-chat-behavior)
- [七、自動化測試與驗證體系 (Verification & Quality Assurance)](#七自動化測試與驗證體系-verification--quality-assurance)

---

## 一、系統全景架構圖 (Architecture Overview)

```mermaid
graph TD
    User([Discord 群友發言 / 交互]) --> DiscordAPI[Discord Gateway API]
    DiscordAPI --> Client[FriendBotClient]

    subgraph "事件分流與緩衝調度"
        Client --> |純監聽頻道| ListenQueue[監聽頻道防抖隊列 (方案 C)]
        Client --> |主回覆頻道| BurstMgr[BurstBufferManager (4.5s 窗口)]
        Client --> |Slash 指令| SlashHandler[CommandTree 指令分派]
    end

    subgraph "三層記憶與檢索模組 (Memory & RAG)"
        MemoryMgr[MemoryManager]
        SQLite[(SQLite3 永久資料庫)]
        FTS5[(SQLite FTS5 全文索引)]
        MemoryMgr <--> SQLite
        MemoryMgr <--> FTS5
    end

    subgraph "AI 核心與提煉引擎 (Gemini 2.5)"
        Gemini[GeminiClient]
        Extractor[MemoryExtractor]
        Prompts[Prompt 模組 & Persona]
        WebTool[Web Search (DuckDuckGo + Jina AI)]
        Gemini <--> WebTool
    end

    subgraph "背景非同步調度器 (Schedulers)"
        AlarmSched[AlarmScheduler]
        CalSched[CalendarScheduler]
        WebhookPush[Discord Webhook 推送]
    end

    BurstMgr --> |收集對話| MemoryMgr
    BurstMgr --> |組裝 Context| Prompts
    Prompts --> Gemini
    Gemini --> |動態引用回覆 / 多氣泡| Client
    Client --> |對話記錄 & JIT 觸發| Extractor
    Extractor --> |更新事實/好感度| MemoryMgr

    CalSched --> WebhookPush
```

---

## 二、三層全記憶與多人畫像模組 (Three-Tier Memory Architecture)

### 1. 三層記憶分層設計
| 記憶層級 | 儲存載體 | 作用範圍 | 生命週期 |
| :--- | :--- | :--- | :--- |
| **第 1 層：短期對話上下文** | SQLite `messages` 表 | 最近 15 則頻道對話滑動窗口 | 會話級 (即時) |
| **第 2 層：長期用戶畫像** | SQLite `user_profiles` 表 | 每個群友的客觀事實 (`facts`)、社交印象 (`interaction_notes`)、好感度 (`favorability`) | 永久保存 |
| **第 3 層：深層歷史回憶** | SQLite `messages_fts` 全文索引 | 當前話題觸發的跨頻道、跨月份歷史回憶片段 | 永久全文檢索 |

### 2. 多人群友畫像識別 (A+B+C 混合解析方案)
在群聊中，紅莉栖能同時理解多位群友：
- **途徑 A (顯式關聯)**：發言中的 `@提及` 或 Discord 引用回覆對象。
- **途徑 B (近期在場群友)**：從第 1 層短期對話中提取最近發言過的其他活躍群友。
- **途徑 C (全域畫像關鍵字掃描)**：若當前訊息文本提及任何已建檔群友的暱稱或特徵，自動載入其畫像。

### 3. 事實防洗白與自我更正機制 (`MemoryExtractor`)
- **防洗白增量聯集保護**：提煉新記憶時，新舊事實採用聯集合併（`set(old_facts) | set(new_facts)`），避免 AI 單次未提及舊事實導致記憶遺失。
- **自我更正與事實移除 (`remove_facts`)**：當用戶明確澄清舊事實（例如「我搬去台北不住台中了」），Prompt 引導 AI 輸出 `remove_facts: ["住在台中"]`，精準剔除過期事實。

### 4. 監聽頻道批次提煉 (方案 C)
- 監聽頻道訊息暫存於記憶體防抖隊列（滿 15 則或靜默 10 分鐘觸發批次提煉）。
- 若發言者進入主頻道發言，系統會觸發 **JIT (Just-In-Time) 按需即時統合**，優先消化該發言者在監聽頻道積壓的發言，API 開銷降低 85% 以上。

---

## 三、動態好感度與人際進展系統 (Favorability & Progression System)

### 1. 傲嬌四階關係階級 (4-Tier Progression)
好感度數值區間為 `0 ~ 100`（新用戶預設為 `30`）：
```
[ 0 ──────── 19 ]  Tier 1: 陌生警戒 (Stranger)       - 冷淡、講究邏輯、公事公辦
[ 20 ─────── 49 ]  Tier 2: 熟識群友 (Familiar)       - 經典傲嬌、嘴硬心軟、互相吐槽 (預設)
[ 50 ─────── 79 ]  Tier 3: 實驗室夥伴 (Labmem)       - 防線變薄、容易害羞臉紅、關心作息
[ 80 ────── 100 ]  Tier 4: 靈魂共鳴 (Steins;Gate)   - 嬌 70% / 傲 30%、深層羈絆堅定不移
```

### 2. 隱密更新與防刷保護機制
- **完全捨棄系統提示**：對話中絕不發送「好感度+1」等系統訊息，維持完全擬真沉浸感。
- **每日增加上限 (`daily_gain_limit: 5`)**：單一用戶當日累積加分滿 5 點後自動封頂，杜絕洗頻刷分。
- **可視化查詢**：僅當使用者主動使用 `/kurisu-profile` 時，才展示 `💖 關係進展` 與 `[████████░░]` 信任進度條。

---

## 四、多人群聊短時熱絡 (Burst) 與動態引用回覆 (Burst & Dynamic Reply)

### 1. 滑動時間窗口模型 (`BurstBufferManager`)
```
  群友 A 發言 (t = 0s) ───► 啟動 4.5s 窗口
  群友 B 發言 (t = 1.5s) ──► 檢測人數 >= 2 人 (鎖定 Burst 模式)
  群友 C 發言 (t = 3.0s) ──► 累積滿或窗口截止
                           │
                           ▼
          【送入 Gemini 進行多對話打包理解】
```

### 2. AI 語意自選引用目標與 Discord 原生 Reply
- Prompt 指引 Gemini 輸出 `[TARGET_ID: <message_id>]` 標明最想吐槽或回答的核心發言者。
- Bot 發送的第一則氣泡調用 `target_message.reply(...)`，在 Discord 上呈現**專屬引用線與原文預覽**，同時在內容中自然接住其他在場群友的話題。

---

## 五、獨立定時鬧鐘與 Webhook 行事曆系統 (Alarm & Calendar Scheduling)

### 1. 獨立定時鬧鐘 (`alarm/`)
- 支援多種自然時間格式（如 `2026/8/27/15/30`、`8/27 15:30`、`15:30`、`30m`）。
- 獨立非同步調度器 `AlarmScheduler` 每 5 秒輪詢 SQLite，到期在指定頻道發送紅莉栖醒目傲嬌提醒。

### 2. Webhook 行事曆與自然對話查詢 (`calendar/`)
- 支援登記日程、依日期查詢、支援指定 Webhook URL 推送。
- **日常對話自然理解**：群友在聊天中隨意詢問「*我今天有什麼行程？*」或「*幫我看 8/27 有排程嗎？*」，紅莉栖會自動檢索行事曆並以科學家傲嬌口吻具體回答！

---

## 六、即時聯網搜尋與擬真多氣泡發送 (Web Search & Natural Chat Behavior)

### 1. 即時聯網工具鏈
- **檢索層**：DuckDuckGo 搜尋引擎。
- **閱讀層**：Jina AI Reader (`https://r.jina.ai/`)，精準抓取網頁 Markdown 內容。
- **指令與自動觸發**：支援 `/kurisu-search` 強制搜尋，或由 AI 依提問自動調用。

### 2. 擬真打字與多氣泡演算法
- 長篇回覆自動依句號、換行等標點切分為多則獨立氣泡（預設每段約 35 字）。
- 氣泡之間計算擬真打字延遲時間（`TYPING_DELAY_RANGE: [0.6, 1.3]` 秒），並同步觸發 Discord `typing()` 狀態。

---

## 七、自動化測試與驗證體系 (Verification & Quality Assurance)

本專案具備完善的自動化測試套件 [`test/tests_verify.py`](file:///C:/ALL%20FILES/Code/friend-bot/test/tests_verify.py)，共涵蓋 13 項端到端與單元測試用例：

```bash
# 執行全套測試
python test/tests_verify.py
```

| 測試用例名稱 | 驗證項目 |
| :--- | :--- |
| `test_parse_alarm_time` | 鬧鐘各類時間格式解析相容性 |
| `test_alarm_manager_lifecycle` | 鬧鐘建立、查詢、觸發與取消生命週期 |
| `test_parse_calendar_time` | 行事曆日期時間格式解析 |
| `test_calendar_manager_crud_and_query_by_date` | 行事曆 CRUD、依日期查詢與摘要產生 |
| `test_multi_user_profile_recall` | 多人畫像檢索 (A+B+C) 與 Prompt 注入 |
| `test_cross_user_memory_extraction` | 跨用戶自述與被提及他人特徵之精準歸屬 |
| `test_facts_anti_overwrite_protection` | 事實防洗白與增量聯集保護 |
| `test_facts_correction_and_remove` | `remove_facts` 事實否定與更正機制 |
| `test_plan_c_batch_extraction_and_extracted_flag` | 監聽頻道批次提煉與 `extracted` 狀態流轉 |
| `test_favorability_progression_and_daily_cap` | 好感度增長、每日上限 (+5) 防刷保護 |
| `test_relationship_tier_computation_and_attitude_injection` | 4 階 Tier 計算與 Prompt 傲嬌態度動態注入 |
| `test_burst_dialogue_prompt_and_target_parser` | Burst 提示詞生成與 `[TARGET_ID]` 標籤解析 |
| `test_burst_buffer_manager_multi_user` | 多用戶滑動窗口防抖收集與 Burst 判定 |
