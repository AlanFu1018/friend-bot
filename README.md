# 🤖 Friend-Bot (Discord 記憶型聊天機器人)

基於 **Google Gemini 3.1 Flash-Lite** 與 **三層全記憶系統 (Three-Tier Full Memory System)** 的 Discord 聊天機器人。

具備幽默風趣的群友風格，能永久保存所有頻道對話、自動累積群友個人特徵畫像、支援跨頻道深度歷史回憶，並支援 Discord 圖片多模態理解與擬真多氣泡發送！

---

## 🌟 核心特色

1. **三層全記憶架構**：
   * **第 1 層 (短期即時上下文)**：最近 15 則頻道即時對話，確保接話自然連貫。
   * **第 2 層 (個人長期畫像)**：背景自動分析提取每位群友的喜好、習慣與特徵，持續動態更新。
   * **第 3 層 (跨頻道歷史深度回憶)**：透過 SQLite FTS5 全文檢索，精準回憶很久以前聊過的話題。
2. **雙軌頻道機制**：
   * **回覆專屬頻道 (`reply_channel_ids`)**：機器人在此頻道參與聊天並幽默回覆。
   * **純監聽記憶頻道 (`listen_channel_ids`)**：機器人默默旁聽並更新群友記憶，不插話打擾。
3. **即時畫像查詢指令 (`/profile`)**：
   * 在頻道輸入 `/profile` 或 `/profile @群友`，立即以精美 Embed 卡片展示已記錄的特徵、喜好與互動印象。
4. **擬真多氣泡連續發送 (`chat_behavior`)**：
   * 智慧依據段落、句末標點（。！？）拆分為多則自然長度的訊息氣泡。
   * 發送後續段落時自動顯示「正在輸入...」並帶有真實人類鍵盤節奏（0.6 ~ 1.3 秒隨機延遲）。
   * 支援 Markdown Code Block 語法完整保護不破版。
5. **多模態視覺理解**：當群友上傳圖片時，Gemini 能結合影像與歷史上下文一同吐槽或互動。
6. **靈活設定**：透過 [config/config.yaml](config/config.yaml) 與 [config/persona.md](config/persona.md) 輕鬆微調人設、模型參數與記憶深度。

---

## 🚀 快速啟動指南

### 1. 填寫金鑰設定 (.env)
複製 `.env.example` 為 `.env` 並填入您的金鑰：
```env
DISCORD_TOKEN=您的_Discord_Bot_Token
GEMINI_API_KEY=您的_Google_Gemini_API_Key
```

### 2. 設定聊天與監聽頻道 (config/config.yaml)
開啟 [config/config.yaml](config/config.yaml)，填入您要互動或監聽的頻道 ID：
```yaml
bot:
  # 機器人會在此說話聊天的頻道 ID (可留空代表不限頻道)
  reply_channel_ids: [1542218293505429504]

  # 機器人只聽不說、默默記錄記憶的頻道 ID
  listen_channel_ids: [1542218343279239278]
```

### 3. 開啟 Discord Bot 權限
請至 [Discord Developer Portal](https://discord.com/developers/applications) 確認已在 Bot 頁面開啟：
* ✅ **Message Content Intent** (讀取訊息內容權限)
* ✅ **Server Members Intent**

### 4. 啟動機器人
```powershell
# 正常啟動
.\.venv\Scripts\python.exe main.py
```

---

## 📋 常用指令

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `/profile` | 查詢發言者本人的長期記憶畫像與特徵 | `/profile` |
| `/profile @用戶` | 查詢指定標註對象的個人特徵畫像 | `/profile @Trito_Nozan` |
| `/profile <User ID>` | 透過 Discord User ID 查詢畫像 | `/profile 555738929584930868` |

---

## 🧹 命令列參數 (CLI 記憶管理)

在啟動時，您可以透過額外參數來管理或清空機器人的記憶：

| 指令參數 | 說明 | 範例 |
| :--- | :--- | :--- |
| `--clear-memory` | **清空所有記憶**（重置全部對話歷史 + 全文索引 + 所有用戶長期畫像） | `.\.venv\Scripts\python.exe main.py --clear-memory` |
| `--clear-history` | **僅清空聊天歷史**（保留用戶特徵畫像） | `.\.venv\Scripts\python.exe main.py --clear-history` |
| `--clear-profiles` | **僅清空用戶個人特徵畫像**（保留歷史紀錄） | `.\.venv\Scripts\python.exe main.py --clear-profiles` |
| `--only-clear` | 清空後**直接結束程式**，不連線啟動 Discord 機器人 | `.\.venv\Scripts\python.exe main.py --clear-memory --only-clear` |

---

## 📁 專案檔案結構

* 📄 [main.py](file:///C:/ALL%20FILES/Code/friend-bot/main.py)：機器人啟動入口（支援 CLI 參數）
* ⚙️ [config/config.yaml](file:///C:/ALL%20FILES/Code/friend-bot/config/config.yaml)：核心設定檔（模型、頻道、氣泡分段行為）
* 🎭 [config/persona.md](file:///C:/ALL%20FILES/Code/friend-bot/config/persona.md)：角色性格人設 Markdown 檔案
* ⚙️ [src/friend_bot/core/](file:///C:/ALL%20FILES/Code/friend-bot/src/friend_bot/core/)：全域設定解析與工業級彩色日誌
* 🧠 [src/friend_bot/memory/](file:///C:/ALL%20FILES/Code/friend-bot/src/friend_bot/memory/)：SQLite 資料庫與三層記憶管理器
* 🤖 [src/friend_bot/ai/](file:///C:/ALL%20FILES/Code/friend-bot/src/friend_bot/ai/)：Gemini 3.1 Flash-Lite 與多模態生成、背景記憶提取器
* 💬 [src/friend_bot/bot/](file:///C:/ALL%20FILES/Code/friend-bot/src/friend_bot/bot/)：Discord 事件監聽、`/profile` 查詢指令、語意分段切分與擬真發送路由
* 🧪 [src/test/tests_verify.py](file:///C:/ALL%20FILES/Code/friend-bot/src/test/tests_verify.py)：三層記憶與檢索功能測試腳本
