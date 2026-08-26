# 🤖 Friend-Bot 專案規格與開發計畫書

本文件記錄「具備全記憶與畫像檢索功能的 Discord 聊天機器人 (`friend-bot`)」之完整系統規格、架構設計與開發計劃表。

---

## 📌 一、 專案核心規格 (Project Specifications)

| 項目 | 規格設定 | 說明 |
| :--- | :--- | :--- |
| **專案名稱** | `friend-bot` | Discord 全記憶型對話機器人 |
| **LLM 模型** | `gemini-3.1-flash-lite` | 使用官方 Google GenAI SDK，可透過 `../config/config.yaml` 彈性切換 |
| **角色定位 (Persona)** | 幽默吐槽 / 活潑趣味的群友風格 | 具備群友共鳴感、自然風趣，能根據話題開玩笑或吐槽，非死板助理 |
| **多模態支援** | 支援圖片/附件解析 | 當使用者在 Discord 上傳圖片時，Gemini 能結合影像與歷史上下文理解並回應 |
| **雙軌頻道機制** | **回覆頻道** vs **純監聽記憶頻道** | • `reply_channel_ids`：機器人會主動聊天互動的專屬頻道<br>• `listen_channel_ids`：機器人默默旁聽、記憶對話與用戶特徵，但**不主動回覆** |
| **設定管理** | `../config/config.yaml` + `.env` | 機密金鑰放 `.env`，其餘模型參數、頻道清單、記憶深度、Persona 提示詞放 `../config/config.yaml` |
| **對話處理** | 自動分段 + 打字狀態 (Typing) | 超過 Discord 2000 字元限制自動切分，生成期間維持 Typing 狀態 |

---

## 🧠 二、 雙軌頻道與三層記憶系統架構

系統將頻道分為「**主動回覆**」與「**純監聽記憶**」兩類，並建立於**「永久儲存所有歷史對話」**的基礎上：

```mermaid
flowchart TD
    subgraph 訊息輸入來源[Discord 訊息輸入]
        A1[回覆專屬頻道 Reply Channels]
        A2[純監聽記憶頻道 Listen Channels]
    end

    subgraph 永久歷史儲存庫[永久歷史訊息庫 (SQLite + FTS5)]
        DB[(永久儲存所有頻道歷史訊息<br>支援跨頻道全文索引檢索)]
    end

    subgraph 三層記憶檢索管道[三層記憶檢索管道]
        T1[第 1 層：短期滑動視窗<br>當前回覆頻道最近 15 則即時上下文]
        T2[第 2 層：結構化個人畫像<br>跨頻道累積之用戶喜好/事實/特徵]
        T3[第 3 層：歷史跨頻道深度回憶<br>關鍵字/話題相關歷史片段檢索]
    end

    A1 -->|儲存對話| DB
    A2 -->|純記錄不回覆| DB
    A2 -.->|背景自動萃取個人特徵| T2

    A1 --> T1
    DB --> T1
    DB --> T2
    DB -->|跨頻道話題匹配| T3

    T1 & T2 & T3 --> Prompt[合成多層上下文 Prompt + 幽默群友 Persona]
    Prompt --> Gemini[Gemini 3.1 Flash-Lite API]
    Gemini --> Reply[發送回覆至【回覆專屬頻道】]
    Gemini -.->|背景非同步提取新特徵| T2
```

### 1. 雙軌頻道機制運作
* **回覆頻道 (`reply_channel_ids`)**：
  * 使用者發言後，機器人會載入短期上下文、用戶畫像、歷史回憶並給予幽默回覆。
* **純監聽記憶頻道 (`listen_channel_ids`)**：
  * 機器人收到訊息後，**立即永久存入 SQLite 並非同步提取用戶特徵**，但**完全不發言打擾**。
  * 讓機器人在日常對話中「偷聽」並記住大家的興趣、習慣，日後在回覆頻道中能驚喜展現記憶。

### 2. 三層記憶管道
* **第 1 層：短期即時上下文 (Short-term Context)**：當前互動頻道最近 15 則訊息。
* **第 2 層：結構化用戶長期畫像 (User Profile)**：跨所有頻道自動萃取、整合的用戶事實清單。
* **第 3 層：歷史跨頻道深度回憶 (Deep History Recall)**：利用 FTS5 全文搜尋，能在回覆時精準調用過去在任何監聽頻道聊過的舊話題。

---

## 📁 三、 專案檔案組織架構 (File Structure)

```text
friend-bot/
├── doc/
│   └── project_plan.md        # 專案規格與實作計畫書 (本文件)
├── config.yaml                # 核心設定檔（回覆頻道、監聽頻道、模型參數、Persona 提示詞）
├── .env.example               # 環境變數範本（Token、API Key 等機密金鑰）
├── .gitignore                 # Git 忽略檔案清單（忽略 .env、資料庫、快取等）
├── requirements.txt           # 專案依賴套件清單
├── config.py                  # 設定載入器（整合 config.yaml 與 .env）
├── main.py                    # 機器人啟動入口點
│
├── bot/                       # Discord 機器人相關邏輯
│   ├── __init__.py
│   ├── client.py              # Discord Client 實例與事件監聽 (區分回覆頻道 vs 監聽頻道)
│   └── handlers.py            # 訊息過濾、附件圖片下載、長訊息分段發送輔助
│
├── ai/                        # AI / LLM 生成與多模態模組
│   ├── __init__.py
│   ├── gemini_client.py       # Google GenAI SDK (gemini-3.1-flash-lite) 封裝
│   ├── prompts.py             # Prompt 範本與記憶提取 Prompt
│   └── memory_extractor.py    # 非同步背景分析對話，萃取用戶長期特徵與事實
│
├── memory/                    # 記憶持久化與檢索管理
│   ├── __init__.py
│   ├── db.py                  # SQLite (aiosqlite) 連線、資料表建置與 FTS5 全文索引
│   └── memory_manager.py      # 三層記憶整合（短期視窗 + 跨頻道用戶畫像 + 全文檢索）
│
└── data/                      # 本地運行資料儲存目錄（自動建立）
    └── friend_bot.db          # SQLite 本地資料庫（永久儲存歷史、用戶記憶、FTS 索引）
```

---

## 📅 四、 實作計劃表 (Implementation Roadmap)

| 階段 | 任務項目 | 具體工作與產出 |
| :--- | :--- | :--- |
| **Phase 1** | **專案結構與環境配置** | • 建立 `requirements.txt` (`discord.py`, `google-genai`, `aiosqlite`, `PyYAML`, `python-dotenv`, `pillow`, `aiohttp`)<br>• 建立 `../config/config.yaml`（配置 `reply_channel_ids` 與 `listen_channel_ids`）、`.env.example`、`.gitignore`<br>• 實作 `../src/friend_bot/core/config.py` (整合載入 `../config/config.yaml` 與 `.env`) |
| **Phase 2** | **永久資料庫與三層記憶模組** | • 建立 `../src/friend_bot/memory/db.py`：設計永久訊息表 `messages`（含 channel_id 標記）、全文搜尋表 `messages_fts`、用戶畫像表 `user_profiles`<br>• 建立 `../src/friend_bot/memory/memory_manager.py`：實作短期查詢、跨頻道個人畫像維護與歷史跨頻道回憶檢索 |
| **Phase 3** | **Gemini 引擎與多模態模組** | • 建立 `../src/friend_bot/ai/prompts.py`：結合 `../config/config.yaml` 載入 Persona、多層記憶組合模板與特徵提取 Prompt<br>• 建立 `../src/friend_bot/ai/gemini_client.py`：封裝 `gemini-3.1-flash-lite` 文字與圖片多模態生成<br>• 建立 `../src/friend_bot/ai/memory_extractor.py`：背景非同步提取用戶畫像特徵並更新至 DB |
| **Phase 4** | **Discord Bot 事件與邏輯串接** | • 建立 `../src/friend_bot/bot/handlers.py`：多模態圖片下載轉碼、訊息過濾、超長文字切分<br>• 建立 `../src/friend_bot/bot/client.py` 與 `main.py`：實作雙軌訊息監聽（回覆頻道 vs 純監聽記憶頻道） |
| **Phase 5** | **測試與功能驗證** | • 測試在「監聽頻道」發言後，機器人不說話但成功存檔並提煉記憶<br>• 測試在「回覆頻道」發言時，機器人能自然提及該用戶在監聽頻道聊過的話題 |
