# Friend-Bot 系統架構總覽

> **文件定位**：全系統的入口與索引。說明有哪些功能模組、彼此如何串接、以及各自的規格書在哪。
>
> 最後更新：2026-08-30。本文所有描述皆已對照當時的程式碼核實。

---

## 1. 這是什麼

一個以 Gemini 驅動的 Discord 聊天機器人，扮演固定人格（牧瀨紅莉栖）與群友長期互動。核心特色是**跨對話的長期記憶**：它會累積每位群友的事實、綽號、互動印象與好感度，並在對話中自然運用。

技術骨幹：`discord.py` + `google-genai` + SQLite（含 FTS5 全文檢索），全非同步。

---

## 2. 功能模組與規格書

| 模組 | 職責 | 規格書 |
| :--- | :--- | :--- |
| **對話與回覆** | 頻道模式、Burst 多人聚合、動態引用、多氣泡分段、圖片理解 | [`chat_and_reply.md`](chat_and_reply.md) |
| **記憶系統** | 三層記憶、人物識別、別名、背景提煉 | [`memory_sys_design.md`](memory_sys_design.md) |
| **Prompt 組裝** | 記憶如何組成送往 Gemini 的 prompt（執行期細節） | [`prompt_pipeline.md`](prompt_pipeline.md) |
| **人格與好感度** | persona、四階關係階級、好感度計算與防刷 | [`persona_and_favorability.md`](persona_and_favorability.md) |
| **聯網搜尋** | DuckDuckGo + Jina Reader、Tool Calling | [`web_search.md`](web_search.md) |
| **行事曆與鬧鐘** | 兩套定時系統、時間解析、Webhook 推送 | [`calendar_and_alarm.md`](calendar_and_alarm.md) |
| **情緒顏文字** | `[emotion:xxx]` 標籤渲染與防重複 | [`emotion_kaomoji.md`](emotion_kaomoji.md) |
| **Slash 指令** | 10 個指令、Mixin 註冊架構、權限模型 | [`commands.md`](commands.md) |
| **設定與基礎建設** | config 層級、logger、DB schema 與遷移 | [`configuration.md`](configuration.md) |

### 問題記錄（非規格）

| 文件 | 內容 |
| :--- | :--- |
| [`mem_sys_bugs.md`](mem_sys_bugs.md) | 記憶系統語意正確性的缺陷清單、實測證據與修復記錄 |
| [`improv.md`](improv.md) | 並發與安全面的程式碼審查記錄（部分項目仍未處理） |

規格書講「現在怎麼運作」；這兩份講「為什麼會變成這樣」，含大量實測證據與判斷理由。

---

## 3. 全景架構

```
                        Discord Gateway
                              │
                     ┌────────▼────────┐
                     │  on_message()   │  client.py
                     └────────┬────────┘
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   忽略前綴              監聽頻道               對話頻道
   (#／＃／//)          存庫 + 防抖佇列        Burst 緩衝
     直接 return              │                     │
                              │            ┌────────▼────────┐
                              │            │ 記憶檢索與組裝  │
                              │            │ ・短期對話      │
                              │            │ ・人物識別      │
                              │            │ ・三軌事實 RAG  │
                              │            │ ・深度回憶      │
                              │            │ ・行事曆        │
                              │            └────────┬────────┘
                              │                     │
                              │            ┌────────▼────────┐
                              │            │  Gemini 生成    │
                              │            │  (可調用搜尋)   │
                              │            └────────┬────────┘
                              │                     │
                              │            ┌────────▼────────┐
                              │            │ 顏文字渲染      │
                              │            │ 多氣泡切分      │
                              │            │ 引用回覆送出    │
                              │            └────────┬────────┘
                              │                     │
                              └──────────┬──────────┘
                                         ▼
                              統一提煉入口（背景）
                              extract_dialogue()
                                         │
                                    SQLite 畫像

           背景排程器（獨立於對話流程）
           ・鬧鐘掃描      每 5 秒
           ・行事曆掃描    每 5 秒
           ・提煉撿漏      每 600 秒
```

---

## 4. 目錄結構

```
src/friend_bot/
├── core/          config.py（設定層級）、logger.py（彩色 + 輪替檔案）
│                  emotion.py（顏文字渲染，純文字處理不依賴 Discord）
├── memory/        db.py（schema 與遷移）、memory_manager.py（三層記憶核心）
├── ai/            gemini_client.py（SDK 封裝 + Tool Calling）
│                  prompts.py（所有 prompt 組裝）
│                  memory_extractor.py（背景提煉單一入口）
│                  tools/web_search_tool.py
└── bot/           client.py（主流程）、handlers.py（圖片下載、訊息切分）
    ├── commands/  Slash 指令 Mixin（help／search／profile／alias／alarm／calendar）
    └── utils/     burst/（多人聚合）、alarm/、calendar/
```

各層的依賴方向是**嚴格單向**的 `bot → ai → memory → core`，沒有例外。任何一層都可以被單獨匯入。

> 這在 2026-08-30 之前並非如此：`ai/gemini_client.py` 曾匯入 `bot/utils/emotion.py`，形成
> `ai → bot → ai` 的環路，使匯入順序變得敏感。`emotion.py` 已移至 `core/`（見
> [`configuration.md`](configuration.md)）。新增跨層匯入時請維持這個方向。

---

## 5. 兩個貫穿全系統的設計原則

### 系統縮小範圍，模型做語意判斷

凡是「誰是誰」「可以寫給誰」「名字是什麼」這類**身分問題**，由確定性的規則決定，且**寧可找不到也不猜**；凡是「這句話在講什麼」這類**語意問題**交給模型，但用白名單框住錯誤的影響範圍。

這條原則在記憶系統最為明顯（見 [`memory_sys_design.md`](memory_sys_design.md) §1），但也適用於別處：Slash 指令的呼叫者身分取自 Discord 而非訊息內容、`user_name` 只由 Discord 權威寫入。

### 誤判代價不對稱時，偏向可回復的那一側

- 深度回憶的相關性門檻寧可漏召回，也不把無關舊話塞進 prompt
- 事實刪除寧可留下過時事實（可見、可再更正），也不誤刪仍成立的事實
- 否定偵測寧可漏判（退回原本行為），也不誤判（會刪資料）
- 維度 C 的「在場者」只讀不寫（讀錯只是一次回覆變差，寫錯是永久記錯人）

---

## 6. 啟動流程

```
main.py
  ├─ setup_logger()
  ├─ init_db()               建表 → 依 PRAGMA user_version 執行遷移
  ├─ 處理 --clear-* 參數     （可選：清空記憶／歷史／畫像）
  ├─ 檢查 DISCORD_TOKEN
  └─ FriendBotClient.start()
       └─ setup_hook()
            ├─ 註冊 6 組 Slash 指令 Mixin → tree.sync()
            └─ 啟動 3 個背景任務（鬧鐘／行事曆／提煉撿漏）
```

命令列參數見 [`configuration.md`](configuration.md#命令列參數)。

---

## 7. 測試

```bash
python test/tests_verify.py
```

62 項自動化測試，涵蓋時間解析、記憶檢索、跨使用者歸屬、事實保護、別名校驗、Burst 聚合、指令註冊、顏文字渲染等。測試直接以指令稿執行（`test/` 沒有 `__init__.py`，不能用 `python -m unittest`）。
