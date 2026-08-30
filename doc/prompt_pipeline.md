# Friend-Bot 記憶系統 → Gemini Prompt 組裝管線 (Prompt Pipeline)

> 本文件是**照著 `src/` 程式碼逐行核對**的執行期真實流程說明，記錄「一則 Discord 訊息進來後，各層記憶如何被組合成送往 Gemini 的 prompt」。
>
> **與其他文件的定位差異**：
> - [`doc/architecture.md`](architecture.md)：全系統入口與模組索引。
> - [`doc/memory_sys_design.md`](memory_sys_design.md)：記憶系統的**架構與設計理由**。
> - [`doc/mem_sys_bugs.md`](mem_sys_bugs.md)：缺陷清單、實測證據與修復記錄。
> - **本文件**：執行期實際跑起來是什麼樣子（含逐行行號）。與設計文件有出入時以本文件為準；已知落差整理於 [§5](#5-附錄與現有設計文件的落差)。

---

## 1. 端到端時序（`on_message` → `chat.send_message`）

```
Discord 訊息
     │
     ▼
on_message  (bot/client.py:107-155)
     │
     ├─ 訊息以忽略前綴開頭 (#／＃／//)  → return，完全不記錄、不回覆
     │
     ├─ channel_id ∈ LISTEN_CHANNEL_IDS （純監聽頻道）
     │     ├─ save_message(extracted=0)          # 只存庫
     │     └─ add_listen_message(...)            # 丟進 4 秒防抖佇列，等待背景批次提煉
     │     └─ return  ◀── 【監聽頻道永遠不會產生回覆 prompt】
     │
     └─ channel_id ∈ REPLY_CHANNEL_IDS （對話頻道）
           │
           ├─ ENABLE_BURST_REPLY = true
           │     └─ BurstBufferManager.add_message  (bot/utils/burst/burst_manager.py:33-91)
           │           • 緩衝視窗：單人 1.2 秒／2 人以上 4.5 秒 (window_seconds)
           │           • 滿 5 則 (max_burst_messages) 立即提早 flush
           │           • is_burst = 視窗內不同發言者數 >= min_user_count (2)
           │
           └─ ENABLE_BURST_REPLY = false → 直接以單則訊息呼叫
                 │
                 ▼
        _handle_buffered_chat  (bot/client.py:164-393)
```

### `_handle_buffered_chat` 內部取數順序

這個順序決定了「這次回覆時，誰能看到誰的什麼資料」，是除錯的關鍵：

| # | 位置 | 動作 | 備註 |
| :-- | :--- | :--- | :--- |
| 1 | `client.py:176-187` | 本批訊息全部 `save_message(extracted=0)` | 先落地，後面的短期記憶才讀得到 |
| 2 | — | （原本此處有「回覆前 JIT 提煉」迴圈，已廢除，見 §4） | — |
| 3 | `client.py:196` | `get_short_term_context(channel_id)` 取最近 N 則 | 含步驟 1 剛寫入的訊息 |
| 4 | `client.py:200` | 本批訊息合併為 `combined_content` | 後續**所有**檢索共用的 query 串 |
| 5 | `client.py:203-208` | `resolve_multi_user_profiles(max_others=4)` | A+B+C 解析發言者 + 關係人 |
| 6 | `client.py:212-235` | `filter_facts_three_tracks` 砍事實數量 | 發言者 8 條／每位他人 3 條 |
| 7 | `client.py:238` | `get_user_schedule_summary(latest_user_id)` | **只撈發言者本人**的行事曆 |
| 8 | `client.py:242-246` | `recall_deep_history` FTS5 跨頻道回憶 | 排除短期記憶已含的 message_id |
| 9 | `client.py:249-256` | `format_memory_context(...)` | 組裝記憶上下文層 |
| 10 | `client.py:260-282` | 依 `is_burst` 選擇觸發層樣板 | 場景 A／B |
| 11 | `client.py:288-303` | `gemini.generate_response(prompt, images)` | 送出 |

> ⚠️ **提煉的時序特性**：提煉一律在回覆送出**後**於背景進行（`asyncio.create_task`），因此步驟 5 讀到的必然是**尚未包含本次發言**的畫像。使用者這句話帶來的新事實，最快要到**下一次發言**才會出現在 prompt 裡。這是刻意的取捨——同步等待提煉會在每次回覆前多壓一次 Gemini 呼叫的延遲。

### 送往 Gemini 的兩個通道

`ai/gemini_client.py:73-113` 顯示 prompt 並非單一字串，而是拆成兩路：

```python
sys_inst = system_instruction or build_system_instruction()   # gemini_client.py:73
config = types.GenerateContentConfig(
    system_instruction=sys_inst,   # ◀── 通道 A：人設與規則
    temperature=..., max_output_tokens=..., tools=...
)

contents = []
if images:
    contents.append(types.Part.from_bytes(...))   # 多模態圖片 Part
contents.append(prompt)                            # ◀── 通道 B：記憶 + 觸發
```

- **通道 A（`system_instruction`）**：`build_system_instruction()`，每次呼叫都重新產生（因為含當前時間）。
- **通道 B（`contents`）**：圖片 Part 在前、文字 prompt 在後。
- 若 `enable_tools=True`（對話預設），走 `chats.create` + `send_message`，並允許最多 3 輪 `search_web` tool call 迴圈（`gemini_client.py:110-129`）。

---

## 2. 五個記憶區塊的來源與篩選規則

| 區塊 | 來源函式 | 資料表 | 上限／配額 | 篩選演算法 |
| :--- | :--- | :--- | :--- | :--- |
| 發言者畫像 | `resolve_multi_user_profiles` (`memory/memory_manager.py:509`) | `user_profiles` | 事實 8 條 | 三軌：熱度 2 + RAG 4 + 最新 2 |
| 其他群友畫像 | 同上（A/B/C 三維度） | `user_profiles` | 最多 4 人，每人 3 條事實 | 三軌：熱度 1 + RAG 1 + 最新 1 |
| 行事曆 | `get_user_schedule_summary` (`bot/utils/calendar/calendar_manager.py:149`) | `calendar_events` | 未來 14 天、最多 10 筆 | 依時間排序 |
| 深度回憶 | `recall_deep_history` (`memory/memory_manager.py`) | `messages_fts` | `history_recall_limit` = 4 | FTS5 粗篩 + 最長共同詞彙門檻（見 §2.3） |
| 短期對話 | `get_short_term_context` (`memory/memory_manager.py:431`) | `messages` | `short_term_history_limit`（目前 30）| 依時間倒序取出後反轉為正序 |

### 2.1 其他群友的 A + B + C 三維度解析

`memory_manager.py:518-544`，**依序**加入候選、去重、最後 `[:max_others]` 硬截斷：

- **維度 A**：取自呼叫端傳入的 `explicit_mentions`（來源是 `discord.Message.mentions`）。**不能對內容做 `<@\d+>` 正則**——`clean_content` 已把提及轉寫成 `@顯示名稱`，正則永遠不會命中。原正則僅保留為 fallback，處理 Slash 指令參數中的原始標記。
- **維度 B**：`get_known_users_map()` 涵蓋**顯示名稱與別名**，且同名對應多人時整組排除（不猜）。ASCII 暱稱以詞邊界比對，停用詞與過短英數暱稱不參與。
- **維度 C**：掃描短期記憶（由新到舊），把非 bot、非發言者本人的發言者視為「在場」。**維度 C 只影響讀取，不進提煉白名單。**

> 回傳順序即優先序：@提及 → 較長暱稱 → 較短暱稱 → 在場者，因此截斷配額時 @提及必定優先保留。

### 2.2 三軌事實檢索

`filter_facts_three_tracks` (`memory_manager.py:261-335`)：

- **提前返回**：若該用戶事實總數 `<= max_total`，直接全部回傳，且**不產生 RAG 命中記錄**（`:283-284`）。三軌只在事實累積超過配額後才真正生效。
- **軌道 1 熱度**：依 `hits` 倒序取 `heat_limit` 條。
- **軌道 3 最新**：依 `created_at` 倒序取 `recent_limit` 條。
- **軌道 2 話題 RAG**：`extract_keywords`（英文實詞 + 中文 2/3-gram 滑動切片，扣掉 `STOPWORDS`）算交集數，另對完整子字串命中額外 +2 分，取剩餘配額。
- **合併**：以「熱度 → RAG → 最新」順序去重串接，不足則從最新事實往前補足。
- 回傳的第二個值 `rag_hit_texts` 會觸發 `record_fact_hits`（`client.py:221/235`）非同步加熱度，有 1 小時冷卻保護。

### 2.3 深度回憶的 n-gram 檢索機制

SQLite FTS5 的預設 `unicode61` 分詞器會把一整串連續中文視為**單一 token**，因此索引側與查詢側都必須由應用層自行切詞：

- **索引側**：`save_message` 寫入 `messages_fts.content` 的是 `build_search_blob()` 產生的 n-gram 檢索字串（空白分隔的 2/3-gram + 英文實詞），而非訊息原文。顯示時一律 JOIN 回 `messages.content` 取原文，可讀性不受影響。
- **查詢側**：`recall_deep_history` 以**同一個** `extract_keywords()` 切 query，確保兩邊切詞規則永遠對稱——這是整個機制能成立的前提。查詢關鍵字數受 `history_recall_max_query_tokens`（預設 30）限制，超過時優先捨棄較短、鑑別度低的詞，避免 Burst 長查詢串產生過大的 MATCH 運算式。
- **相關性門檻**：FTS5 只負責粗篩候選，最終由應用層以「**最長共同詞彙的字數**」判定，門檻為 `history_recall_min_score`（預設 2 = 至少共享一個二字詞）。刻意不用「命中詞彙數量」計分——單一個二字詞只會得 1 分而被門檻擋掉，等於又讓中文最常見的二字詞無法召回。
- **排序**：最長共同詞彙 → 命中詞彙數量 → 時間新舊。

索引版本由 `PRAGMA user_version` 標記（見 `memory/db.py` 的 `FTS_SCHEMA_VERSION`）；偵測到舊版格式時 `init_db()` 會自動從 `messages` 表全量重建，只執行一次。

### 2.4 好感度態度注入

`format_memory_context` 依 profile 的 `relationship_tier` 從 `TIER_ATTITUDE_MAP`（`ai/prompts.py:8-13`）取一段文字，發言者與每位其他群友**各自**注入自己的 Tier 指令：

| Tier | 好感度區間 | 態度 |
| :--- | :--- | :--- |
| `stranger` | < 20 | 冷淡、公事公辦 |
| `familiar` | 20 ~ 49 | 經典傲嬌，嘴硬心軟 |
| `trusted` | 50 ~ 79 | 傲嬌防線變薄，主動關心 |
| `cherished` | >= 80 | 真誠溫柔（嬌 70%／傲 30%） |

---

## 3. 完整 Prompt 骨架

### 3.1 第 1 層：System Instruction

`build_system_instruction()` (`ai/prompts.py:22-52`) — **走 `GenerateContentConfig.system_instruction`，不在 prompt 字串內**：

```
{config/persona.md 全文 (SYSTEM_PROMPT)}

[基本資訊]
- 你的名字: {BOT_NAME}
- 目前平台: Discord
- 當前系統真實時間 (Current Time): 2026年08月29日 14:32:05 (星期五)
- 使用者位置: 台灣台北市、新北市

[回覆原則與群友社交/工具指引]
1. 像真實群友一樣自然回覆…
2. 回覆適度簡潔、幽默…
3. 【群友社交與多人群聊認知】…
4. 【動態人際關係與態度指引】…
5. 【行事曆與排程查詢】…
6. 【聯網搜尋工具 (search_web)】…
7. 【當前時間日期】…
8. 若參考了長期記憶請自然融入，切勿生硬複誦…
9. 不需要每次回覆都把對方的名字掛在嘴邊…
```

### 3.2 第 2 層：記憶上下文層

`format_memory_context()` (`ai/prompts.py:54-129`) — 五個區塊以 `"\n\n"` 串接，**順序固定**，任一區塊資料為空則整塊略過：

```
【主要發言者 {current_user_name} 的個人特徵記憶】:
- 【對此用戶態度 (Tier N …)】：…                      ← 僅 ENABLE_FAVORABILITY 時
- 已知特徵/喜好: {事實1}、{事實2}、…                   ← 三軌篩選後，「、」串接
- 互動印象與習慣:
{interaction_notes 全文，含【核心性格】【社交關係】【近期動態】}

【對話中提及 / 近期在場的其他群友畫像】:               ← 每人 4 行，最多 4 人
- 用戶名稱: {名稱} (關係階級: {tier})
  • 【對此用戶態度 (Tier N …)】：…
  • 已知特徵: {…}                                      ← 空則「尚無特定記錄」
  • 互動印象: {…}                                      ← 空則「尚無特別印象」

【用戶已登記的行事曆與排程 (Calendar Schedules)】:
- [{日期} {時間}] {內容} (⏳待提醒 | ✅已觸發)

【過去的歷史話題回憶 (供參考，若相關可自然提及)】:
- [{發言時間}] {發言者}: {內容}                        ← 格式 %Y-%m-%d %H:%M

【近期頻道對話紀錄】:
{發言者 或 "{BOT_NAME} (你)"}: {內容}{ [附帶圖片] }
```

### 3.3 第 3 層：觸發層（三選一）

**場景 A｜單人日常對話**（`bot/client.py:277-282`）：

```
{memory_context}

【當前用戶最新發言】:
{latest_user_name}: {combined_content 或 '[發送了一張圖片]'}

請以幽默風趣的群友風格回應 {latest_user_name}：
```

**場景 B｜Burst 多人群聊**（`build_burst_dialogue_prompt`, `ai/prompts.py:131-161`）：

```
{memory_context}

【💬 多人群聊即時熱烈討論 (短時間內有多位群友連續發言)】:
1. [ID: {message_id}] {發言者}: {內容}{ [附圖] }
2. [ID: {message_id}] {發言者}: {內容}

【回覆指導原則】：
1. 第一行必須標記 `[TARGET_ID: <message_id>]`，緊接下一行輸出回覆文字。
2. 主要聚焦被引用者，但可自然兼顧其他群友。
3. 保持牧瀨紅莉栖的傲嬌/理性/幽默群友性格。

請生成回覆（開頭務必包含 [TARGET_ID: 訊息ID] 標籤）：
```

回覆後由 `parse_burst_reply_response` (`ai/prompts.py:163-176`) 抽出 `TARGET_ID`，比對出對應的 `discord.Message` 做原生 Reply 引用（`client.py:307-316`）；若模型沒給或給錯 ID，退回引用本批最後一則訊息。

**場景 C｜`/kurisu-search` 強制聯網**（`bot/commands/search.py:109-114`）：

```
{memory_context}

【用戶聯網查詢請求】:
{user_name}: {query}

請以傲嬌幽默的天才科學家風格，結合聯網檢索工具為 {user_name} 查證並給出清晰有趣的解答：
```

> 場景 C 的 `max_others` 是 **3**（`search.py:57`），與對話路徑的 4 不同。

### 3.4 完整渲染範例（單人場景）

假設岡部在對話頻道說「桶子今天又在通宵打遊戲，我受不了了」：

```
【主要發言者 岡部 的個人特徵記憶】:
- 【對此用戶態度 (Tier 3 實驗室夥伴)】：對方為深受信賴的實驗室夥伴。傲嬌防線大幅變薄，極易害羞破防臉紅，會主動在傲嬌口吻中流露對其健康、作息與日常的關心。
- 已知特徵/喜好: 自稱狂氣科學家鳳凰院凶真、常喝 Dr Pepper、正在研究時間機器理論、最近連夜做實驗
- 互動印象與習慣:
【核心性格】極度理性中帶著傲嬌，對未知科學充滿狂熱。
【社交關係】對桶子愛吐槽但非常信任，面對紅莉栖時嘴硬卻常被科學論點破防。
【近期動態】最近因為連夜做實驗而顯得疲憊，多次向群友抱怨程式 bug。

【對話中提及 / 近期在場的其他群友畫像】:
- 用戶名稱: 桶子 (關係階級: familiar)
  • 【對此用戶態度 (Tier 2 熟識群友)】：對方為熟悉群友。展現經典傲嬌風格，嘴硬心軟，適度吐槽與接梗…
  • 已知特徵: 超級駭客、最近常熬夜通宵、買了新靜音機械鍵盤
  • 互動印象: 【核心性格】幽默隨和、專精技術。【社交關係】常被岡部吐槽作息。【近期動態】沉迷新出的 Galgame。

【用戶已登記的行事曆與排程 (Calendar Schedules)】:
- [2026-08-30 14:00] 跟教授 meeting (⏳待提醒)

【過去的歷史話題回憶 (供參考，若相關可自然提及)】:
- [2026-08-20 14:30] 桶子: 我上次那把鍵盤軸體換成靜音紅了

【近期頻道對話紀錄】:
桶子: 我昨天又通宵了
牧瀨紅莉栖 (你): 哈？你那是自作自受吧
岡部: 桶子今天又在通宵打遊戲，我受不了了

【當前用戶最新發言】:
岡部: 桶子今天又在通宵打遊戲，我受不了了

請以幽默風趣的群友風格回應 岡部：
```

---

## 4. 回覆之後：背景寫回路徑

**這是另一次完全獨立的 Gemini 呼叫，使用的 prompt 與上面的回覆 prompt 沒有任何共用樣板。**

所有提煉都經由 `MemoryExtractor.extract_dialogue()` 這個**單一調度入口**：

```
回覆送出後 ─────┐
監聽防抖到期 ───┼──→ extract_dialogue(messages, channel_id)
背景撿漏掃描 ───┘         │
                          ├─ 1. 解析發言者（保持出現順序）
                          ├─ 2. 權威名稱 ← 訊息本身的 Discord display_name
                          ├─ 3. 白名單 = 發言者 + resolve_mentioned_user_ids(內容)
                          │      （@提及 + 暱稱比對，與回覆端共用同一函式）
                          ├─ 4. 選引擎：
                          │      發言者 >= 2 人 → _run_batch_engine   (多人對話 prompt)
                          │      否則           → _run_single_engine  (單人主角 prompt)
                          │        temperature=0.2, enable_tools=False, 要求輸出純 JSON
                          ├─ 5. _parse_updates：去 ``` 圍欄 → json.loads → updates
                          ├─ 6. _safe_apply_updates(updates, allowed_uids, authoritative_names)
                          │      ├─ 白名單校驗：user_id 不在上下文內一律拒絕（防注入）
                          │      ├─ user_name 只採用權威名稱，絕不使用模型輸出
                          │      ├─ merge_facts：remove_facts 剔除 / 重複 hits += 3 / 新事實追加
                          │      ├─ calculate_favorability_update：每日 ±上限保護
                          │      └─ apply_profile_update：每使用者 Lock 序列化讀-改-寫
                          └─ 7. 成功才 mark_messages_extracted；失敗保留 extracted=0
```

**為什麼是單一入口**：先前「回覆前 JIT」與「回覆後收尾提煉」兩條路徑會對同一批訊息各處理一次，造成好感度雙重計算、事實熱度灌水、API 成本雙倍；且兩條路徑的白名單建法不一致（監聽路徑只認得有發言的人，被提及者的特徵會被拒絕寫入）。統一後 JIT 已廢除，殘留的未提煉訊息（提煉失敗、或重啟導致監聽佇列遺失）由 `sweep_unextracted()` 每 10 分鐘撿漏一次。詳見 [`doc/mem_sys_bugs.md`](mem_sys_bugs.md)。

---

## 5. 附錄：與現有設計文件的落差

以下皆為**核對程式碼後確認的實際狀況**，本文件僅記錄，不在此次改動：

1. ~~**`doc/rag_mem.md` §8 的架構圖與實際渲染有出入**~~ — 該文件已於 2026-08-30 刪除（內容已併入 [`memory_sys_design.md`](memory_sys_design.md)）。其架構圖曾把 System Instruction 畫成 prompt 的第一區塊，實際上它走的是 `GenerateContentConfig.system_instruction` 參數，不在 prompt 字串內。

2. ~~**深度回憶的日期恆為空字串**~~ — **已修復**。原因是 dict key 不存在：`prompts.py` 讀 `item.get("created_at", "")`，但 `recall_deep_history` 的 SELECT 只回傳 `m.timestamp`，`.get()` 找不到 key 便靜靜回傳預設空字串（不報錯，故長期未被發現）。現改為格式化 `timestamp`（Discord 上實際發言時間，而非 `messages.created_at` 這個寫入資料庫的時間）。

3. ~~**中文深度回憶幾乎不會命中**~~ — **已修復**，詳見 §2.3。原因是索引側（`unicode61` 把整串中文當單一 token）與查詢側（`clean_query.split()` 切不開中文）雙邊都壞，只修一邊無效。

4. **最新訊息在 prompt 中出現兩次**：因為 `get_short_term_context`（步驟 3）在 `save_message`（步驟 1）之後才呼叫，本批訊息同時出現在【近期頻道對話紀錄】與【當前用戶最新發言】／Burst 清單中。

5. ~~**`process_burst_dialogue` 方法不存在**~~ — **已修復**。Burst 場景的背景提煉原本每次都拋 `AttributeError`，現已改由統一入口 `extract_dialogue()` 依發言者人數自動選用多人對話引擎。詳見 [`doc/mem_sys_bugs.md`](mem_sys_bugs.md) P0-2。

6. **監聽頻道的批次觸發條件與設計文件不符**：`doc/memory_sys_design.md` §4 描述為「每累積滿 15 則 **或** 靜默 10 分鐘」才觸發批次提煉，但 `add_listen_message`（`ai/memory_extractor.py:198-218`）實際上只有一個 `debounce_seconds=4.0` 的防抖計時器，沒有任何則數門檻——只要靜默滿 4 秒就打包送出。因此實際的 API 節省幅度低於設計文件宣稱的約 85%，且單則零星發言仍會各自觸發一次呼叫。
