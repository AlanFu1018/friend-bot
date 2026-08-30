# 聯網搜尋（Web Search）

> 機器人如何取得即時資訊：兩段式檢索、模型自主調用、以及強制搜尋指令。
>
> 最後更新：2026-08-30，已對照程式碼核實。

---

## 1. 兩段式檢索

單靠搜尋引擎的摘要（snippet）通常太短，無法支撐一段有內容的回答。因此流程分兩段：

```
perform_web_search(query)
    │
    ├─ 1. search_duckduckgo()      取得前 top_k 個網址 + 摘要
    │      https://html.duckduckgo.com/html/?q=...
    │      解析 HTML 抽出 result__snippet / result__url
    │      還原 uddg= 轉址參數為真實網址
    │
    ├─ 2. fetch_page_with_jina()   逐一抓取網頁正文
    │      https://r.jina.ai/<url>
    │      回傳純文字 Markdown，截斷至 max_content_length_per_page
    │      失敗時退回該筆的搜尋摘要
    │
    └─ 3. 組裝為結構化 Context 回傳給 Gemini
```

用 DuckDuckGo 的 HTML 端點（而非 API）是因為它不需要金鑰；Jina AI Reader（`r.jina.ai`）則把任意網頁轉成乾淨的 Markdown，省去自行處理 HTML 與 JS 渲染。

### 回傳格式

```
【即時聯網搜尋結果：台北天氣】

--- [來源 1] ---
網址: https://...
摘要與內容:
（Jina 抓到的正文，或退回搜尋摘要）

--- [來源 2] ---
...
```

---

## 2. 兩種觸發方式

### 模型自主調用（Tool Calling）

`GeminiClient._get_tools()` 向模型宣告一個 `search_web` function：

```
description: 當用戶詢問最新即時新聞、天氣、特定日期事件、新科技動態或需要
             聯網查證最新資料時呼叫此工具。
parameters:  { query: string }
```

模型判斷需要時自行調用，`generate_response()` 執行搜尋並把結果回傳給模型續寫。**最多 3 輪**（`loop_count < 3`），避免無限來回。

只有在 `enable_tools=True` 時才走 `chats.create` 對話模式；背景提煉一律 `enable_tools=False`，因為那裡只需要 JSON 輸出、不該聯網。

系統指令第 6 條也明確要求：檢索後**必須認真閱讀搜尋內容並融合成實質回覆**，而不是只說「我查到了」。

### 強制搜尋指令

`/kurisu-search <query>` 走獨立路徑（`bot/commands/search.py`）：組裝與一般對話相同的記憶上下文，但把 prompt 換成「用戶聯網查詢請求」並強制 `enable_tools=True`。

兩者的差別只在**是否保證觸發**——指令保證模型看到一個明確的查詢請求，一般對話則由模型自行判斷。

---

## 3. 與記憶系統的關係

`/kurisu-search` 仍會載入完整的記憶上下文（發言者畫像、其他群友、行事曆、深度回憶、短期對話），所以搜尋結果會以認識你的口吻回答，而不是像搜尋引擎那樣乾巴巴。

指令的查詢會以 `/kurisu-search <query>` 的形式存進 `messages`（`extracted=0`），因此也會參與日後的記憶提煉與深度回憶。

> 注意：`/kurisu-search` 的 `max_others` 是 **3**，一般對話是 4。

---

## 4. 相關設定

```yaml
web_search:
  enable_web_search: true          # 關閉後不向模型宣告 search_web 工具
  search_top_k: 3                  # 抓取前 N 個網址
  max_content_length_per_page: 2500  # 單頁正文字元上限，避免 context 爆掉
```

`enable_web_search: false` 時 `_get_tools()` 回傳 `None`，模型完全不知道有這個工具；`/kurisu-search` 仍可呼叫但模型無工具可用。

---

## 5. 失敗處理

各層皆為**軟失敗**，不會中斷對話：

| 失敗點 | 行為 |
| :--- | :--- |
| DuckDuckGo 逾時（8 秒）或解析不到結果 | 回傳「未找到相關即時資訊」字串給模型 |
| Jina 逾時（10 秒）或非 200 | 該筆退回使用搜尋摘要 |
| 全部來源都抓不到正文 | 模型仍會收到摘要，可據以回答 |

所有失敗都會寫入 log（`web_search` logger）。

---

## 6. 已知問題

### 無網域或 scheme 驗證

`fetch_page_with_jina()` 會把 DuckDuckGo 結果中的**任意網址**轉發給 `r.jina.ai`，沒有對網域或 scheme 做過濾（例如排除內網位址、`file://`）。

目前網址來源限於 DDG 搜尋結果，風險較低。但若日後擴充查詢來源（例如允許使用者直接指定網址），這裡就會成為 SSRF 的入口。記錄於 [`improv.md`](improv.md) 3.4，尚未處理。

### 依賴 HTML 結構

`search_duckduckgo()` 以正則解析 DuckDuckGo 的 HTML（`result__snippet`、`result__url` class 名稱）。DDG 改版即會失效——症狀是搜尋「成功」但回傳 0 筆結果。

已有部分緩解：snippet 解析不足 `top_k` 時會退回通用連結解析。但若兩種 class 名稱都變了，就會完全抓不到。log 中的 `取得 0 個目標網址` 是這個問題的訊號。

### 序列抓取

多個來源是**逐一** `await` 抓取的，不是並行。`top_k=3` 且每頁逾時 10 秒的情況下，最壞要等 30 秒。目前 `top_k` 小、影響有限，若要調大應同時改為 `asyncio.gather()` 並行。
