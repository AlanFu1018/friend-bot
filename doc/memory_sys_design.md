# Friend-Bot 記憶系統架構設計（Memory System Design）

> **文件定位**：本文說明記憶系統的**架構與設計理由**——為什麼這樣設計、每個決定在防範什麼。
>
> 另外兩份文件的分工：
> - [`doc/prompt_pipeline.md`](prompt_pipeline.md)：訊息進來後 prompt 如何組成的**執行期細節**（含逐行行號）。
> - [`doc/mem_sys_bugs.md`](mem_sys_bugs.md)：語意正確性的**缺陷清單與修復記錄**。
>
> 最後更新：2026-08-30。本文所有描述皆已對照當時的程式碼核實。

---

## 1. 設計哲學

系統要解決三個痛點：

1. **單一發言者視角**：A 在群組中談到 B 時，機器人無法調閱 B 的記憶，甚至把 B 的特徵錯記到 A 身上。
2. **記憶被覆蓋洗白**：模型回傳空特徵時直接覆蓋舊記憶，累積已久的事實一次清空。
3. **監聽頻道提煉開銷巨大且缺乏脈絡**：單句提煉看不懂跨數分鐘的完整對話，且頻繁觸發 API。

貫穿整份設計的一條原則是：

> **系統負責縮小範圍與限制損害，模型負責在範圍內做語意判斷。**

凡是「誰是誰」「可以寫給誰」「名字是什麼」這類**身分問題**，一律由確定性的規則決定，且**寧可找不到也不猜**；凡是「這句話在講什麼、該歸給誰」這類**語意問題**，交給模型，但用白名單把錯誤的影響範圍框住。

---

## 2. 三層記憶

| 層 | 載體 | 內容 | 時效 |
| :--- | :--- | :--- | :--- |
| **第 1 層** 短期對話 | `messages` | 該頻道最近 15 則（`short_term_history_limit`） | 數分鐘 |
| **第 2 層** 長期畫像 | `user_profiles` | 事實、別名、互動印象、好感度 | 永久 |
| **第 3 層** 深度回憶 | `messages_fts` | 跨頻道全文檢索（n-gram 索引） | 永久 |

`messages` 是原始資料的**唯一真實來源**；`messages_fts` 只是它的衍生索引，任何時候都可以重建。這個關係讓索引格式的變更成為無損操作（見 §7 遷移 v1）。

---

## 3. 人物識別：四個來源

這是整個系統最核心的部分——**記憶要準確，先要認對人**。

| 來源 | 說明 | 進 prompt（讀） | 可被寫入事實 |
| :--- | :--- | :---: | :---: |
| **發言者** | Discord author id | ✅ | ✅ |
| **維度 A：@提及** | `discord.Message.mentions` | ✅ | ✅ |
| **維度 B：名稱比對** | 顯示名稱 ∪ **別名** | ✅ | ✅ |
| **維度 C：近期在場者** | 短期記憶中的發言者 | ✅ | ❌ |

回傳順序即優先序（A → B 依名稱長度 → C），配額截斷時保留較可信的人選。

### 讀寫不對稱是刻意的

維度 C **只讀不寫**。誤判代價不對稱：讀錯只是這次回覆變差，**寫錯是把事實永久記到別人頭上**。「某人剛好在頻道講過話」不足以構成「可以把事實寫給他」的理由。

### 維度 A 必須用 `message.mentions`

**不能對訊息內容做 `<@\d+>` 正則。** discord.py 的 `clean_content` 會把 `<@123>` 轉寫成 `@顯示名稱`，而系統全程使用 `clean_content`，因此正則永遠不會命中。此處曾經失效很長一段時間，導致名稱比對成為唯一能運作的識別方式。

### 同名一律排除，不猜

`get_known_users_map()` 遇到一個名稱對應多位使用者時，**整組排除**而非任選其一。理由是舊做法「後者覆蓋前者」搭配沒有 `ORDER BY` 的 SELECT，結果是**不確定的**——同一句話在不同時候可能命中不同的人。排除後行為變成確定性的：要嘛找對人，要嘛找不到。

被排除者仍可透過 @提及與別名被精準識別。

---

## 4. 別名系統

Discord 顯示名稱往往不是群友實際互稱的稱呼。別名讓慣用綽號也能識別。

**與 `user_name` 分開儲存**是關鍵：`user_name` 由 Discord 權威覆寫（手動改會被還原），`aliases` 不受提煉碰觸，設了就保留——因此它也是人工緊急修正的唯一有效管道。

### 自動學習的五道校驗

提煉可提議別名，須**全部通過**才生效：

1. 通過 `is_matchable_name()`（非停用詞、長度足夠、非過短英數）
2. 不與任何既有的顯示名稱或別名碰撞 — **這道擋掉冒名**
3. 歸屬對象必須在本次 `allowed_uids` 內
4. 未超過 `max_aliases_per_user`（達上限拒絕，不自動淘汰）
5. **記錄來源**：誰的哪則訊息、哪個頻道、何時

第 5 點是讓自動學習可被接受的關鍵：事實寫錯是靜默的，**別名寫錯會立刻表現為「機器人把 X 當成 Y」**，只要有來源記錄就查得到、也撤得掉。

### 由條件 3 導出的邊界

`allowed_uids` 由「發言者 + 已識別的提及對象」組成，因此**別名只能在當事人於該次對話中在場時學到**。學到後即永久有效，包含日後缺席的場合——機制從「在場」自然 bootstrap。

這不是缺陷而是安全性質：要讓「A 提到從不發言的 C 的綽號」也成立，就得讓模型猜「這個沒見過的綽號是誰」，正是本設計一路在移除的那種猜測。

### 別名必須寫進 prompt

僅用於「解析出該載入誰」是不夠的。`format_alias_hint()` 在**五個把人放進 prompt 的渲染點**都附上別名：

```
- 【daru_1024】（大家也叫他：桶子）(ID: 2002)
```

否則模型只看得到顯示名稱，得自己猜對話中的綽號指誰——人一多就不可靠。

---

## 5. 檢索端：一次回覆載入什麼

```
【發言者】        Tier 態度指令 + 8 條事實 + 互動印象 + 行事曆
【其他人 ≤4 位】  Tier 態度指令 + 3 條事實 + 互動印象
【與人無關】      近期對話 15 則 + 深度回憶 4 則
```

### 三軌事實檢索

事實數超過配額時才啟動，以三條軌道合併去重：

| 軌道 | 依據 | 作用 |
| :--- | :--- | :--- |
| 熱度 | `hits` 倒序 | 標誌性人設常駐，不因當下話題無關而消失 |
| 話題 | 關鍵字重疊計分 | 聊到特定話題時喚醒對應記憶 |
| 最新 | `created_at` 倒序 | 近期生活動態不被舊事實淹沒 |

### 深度回憶必須雙邊切詞

FTS5 預設的 `unicode61` 分詞器會把**整串連續中文視為單一 token**，因此索引側與查詢側都必須由應用層自行切詞：

- **索引側**：存入的是 `build_search_blob()` 產生的 n-gram 檢索字串，而非原文；顯示時 JOIN 回 `messages.content` 取原文。
- **查詢側**：以**同一個** `extract_keywords()` 切 query，確保兩邊規則永遠對稱。
- **相關性門檻**：FTS5 只做粗篩，最終以「最長共同詞彙的字數」判定（`history_recall_min_score`，預設 2 = 至少共享一個二字詞）。

刻意不用「命中詞彙數量」計分：單一個二字詞只得 1 分會被門檻擋掉，等於又讓中文最常見的詞長無法召回。

---

## 6. 提煉端：單一調度入口

**所有提煉都經由 `MemoryExtractor.extract_dialogue()`**，由它統一決定引擎、白名單與標記責任。

```
回覆送出後   ─┐
監聽防抖／滿載 ─┼─→ extract_dialogue()
背景撿漏      ─┘      1. 發言者 ≥2 人 → 多人對話引擎；否則單人主角引擎
                      2. 白名單 = 發言者 + @提及 + 名稱/別名命中
                      3. 權威名稱 ← Discord（模型說了不算）
                      4. 成功才 mark_extracted；失敗保留待重試
```

### 為什麼是單一入口

先前有四條路徑各自為政：收尾提煉、回覆前 JIT、監聽批次、Burst 批次。後果是同一批訊息被提煉兩次（好感度雙倍、熱度灌水、API 成本雙倍）、白名單建法不一致、`extracted` 標記責任分歧，而 Burst 那條的方法根本不存在。

**JIT 機制已完全廢除。** 它的設計意圖是「使用者出現時消化監聽頻道積壓」，但它是射後不理的背景任務，結果趕不上該次回覆——意圖從未實現。移除它沒有損失任何實際效果，卻消除了競態、砍半 API 成本，並讓提煉只剩一條路徑。

殘留的未提煉訊息（提煉失敗、或重啟導致監聽佇列遺失）由 `sweep_unextracted()` 每 10 分鐘撿漏，並以**正確的多人引擎**處理。

### 監聽頻道的兩個觸發條件

滿足其一即提煉：靜默滿 `listen_debounce_seconds`（4 秒），或佇列滿 `listen_max_queue_messages`（15 則）。

則數上限是防抖的保險：熱絡頻道若持續有人發言，計時器會被無限重設，佇列將無上限成長並在最後產生超大 prompt。

> **調參建議**：`listen_debounce_seconds` 是提升監聽頻道歸屬品質最有效的旋鈕——調大能讓模型一次看到更完整的多輪脈絡（「A 問鍵盤型號、B 隔兩分鐘回答」這種因果才接得起來）。有則數上限兜底後可以放心調整。

### 白名單是防注入的核心

`allowed_uids` 全部來自 Discord（發言者 + 解析出的提及對象）。模型輸出的 `user_id` 不在其中一律拒絕，因此使用者無法透過訊息內容注入指令操控第三方的畫像或好感度。

系統**不做姓名反查補全**——模型在 prompt 中本就拿得到每個人的 ID，靠名字反查等於讓一個可能已污染的索引決定資料寫給誰。

---

## 7. 記憶保護機制

### 事實（三層保護）

| 情境 | 處理 |
| :--- | :--- |
| 模型回傳空列表 | 舊事實 100% 保留（增量聯集，絕不覆寫） |
| 重複確認既有事實 | `hits += 3`，沉澱核心人設 |
| **新事實否定舊事實** | **以新事實取代舊事實**，熱度歸 1 |
| `remove_facts` 明確刪除 | 需達最低引述門檻才執行 |

**否定推翻**（`has_negation` + `topic_key`）是必要的第二道防線。`remove_facts` 要求模型主動且正確地填寫，一旦漏填，舊的比對邏輯會把「已經不喜歡台北了」判定為「喜歡台北」的重複確認 → 錯誤事實加權、更正被丟棄 → 使用者再否認、熱度再漲。那是一個**正回饋迴路**。

判斷刻意保守：只採用「不 / 沒」與英文否定詞，先剔除「不錯 / 差不多」等假否定，**不採用「未 / 無 / 非」**（「非常喜歡台北」若被判為否定就會誤刪「喜歡台北」）。偏誤方向是有意的——漏判只是退回原本行為，誤判會刪掉仍然成立的事實。

**刪除門檻**同理。`remove_facts=['台中']` 曾經會連帶清掉「以前在台中唸書」「喜歡台中的太陽餅」。現在「事實包含刪除詞」的方向需滿足 `len(term) >= max(2, ceil(0.4 × len(fact)))`。刪除不可逆，因此寧可留下過時事實（可見、可再更正）也不誤刪。

### 名稱（權威來源）

`user_name` **只從 Discord 的 `display_name` 寫入**，提煉路徑一律不得覆寫。曾經發生過畫像被改成別人名字、連帶讓暱稱索引塌縮的資料污染，根因就是允許模型輸出決定名字。

### 並發

所有「讀取畫像 → 計算變更 → 寫回」都經 `apply_profile_update()`，以每位使用者專屬的 `asyncio.Lock` 序列化，避免並行背景任務互相覆蓋。

---

## 8. 資料庫結構

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE, channel_id TEXT NOT NULL,
    user_id TEXT NOT NULL, user_name TEXT NOT NULL, content TEXT NOT NULL,
    has_image INTEGER DEFAULT 0, is_bot INTEGER DEFAULT 0,
    timestamp INTEGER NOT NULL,
    extracted INTEGER DEFAULT 0,          -- 0: 待提煉, 1: 已提煉
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_profiles (
    user_id TEXT PRIMARY KEY,             -- Discord 數字 ID
    user_name TEXT NOT NULL,              -- Discord 權威顯示名稱
    facts TEXT DEFAULT '[]',              -- [{text, hits, created_at, last_used_at}]
    aliases TEXT DEFAULT '[]',            -- [{alias, source, by, channel_id, message_id, at}]
    interaction_notes TEXT DEFAULT '',
    favorability INTEGER DEFAULT 30,
    relationship_tier TEXT DEFAULT 'familiar',
    daily_favorability_gain INTEGER DEFAULT 0,
    last_gain_date TEXT DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- content 存 n-gram 檢索字串而非原文（見 §5）
CREATE VIRTUAL TABLE messages_fts USING fts5(content, user_name);
```

### Schema 版本（`PRAGMA user_version`）

| 版本 | 內容 |
| :--- | :--- |
| 1 | `messages_fts` 改存 n-gram，移除從未寫入的 `channel_id` / `msg_id` 欄位 |
| 2 | 從 `messages` 回填被改錯的 `user_name`；重置被重複提煉灌水的 `hits` |
| 3 | 清除以「名字」而非數字 ID 為主鍵的幽靈畫像（事實併入同名真人後刪除） |

各階段皆為 idempotent，任一階段失敗不提升版本號，下次啟動重跑。`aliases` 欄位以既有的 `ALTER TABLE` 檢查新增，不需版本遷移。

---

## 9. 原始碼導覽

| 檔案 | 職責 |
| :--- | :--- |
| `memory/memory_manager.py` | `resolve_mentioned_user_ids()` 人物識別（A+B）<br>`resolve_multi_user_profiles()` 加上維度 C 的完整檢索<br>`get_known_users_map()` 名稱索引（含別名、碰撞排除）<br>`add_alias()` / `remove_alias()` 別名管理與五道校驗<br>`merge_facts()` / `has_negation()` / `should_remove_fact()` 事實保護<br>`filter_facts_three_tracks()` 三軌檢索<br>`build_search_blob()` / `recall_deep_history()` 第三層回憶 |
| `ai/memory_extractor.py` | `extract_dialogue()` **單一提煉入口**<br>`_run_batch_engine()` / `_run_single_engine()` 兩種引擎<br>`_safe_apply_updates()` 白名單校驗與安全合併<br>`sweep_unextracted()` 背景撿漏 |
| `ai/prompts.py` | `format_memory_context()` 記憶上下文組裝<br>`format_alias_hint()` 別名提示<br>兩份提煉 prompt |
| `bot/client.py` | `_handle_buffered_chat()` 對話主流程 |
| `bot/commands/alias.py` | `/kurisu-alias` 別名指令 |
| `memory/db.py` | Schema 與分階段遷移 |
| `test/tests_verify.py` | 62 項自動化測試 |

---

## 10. 已知限制

### 規則層做不到的（需分詞或 embedding）

- **中文暱稱子字串誤命中**：「小美」仍會命中「小美食」。任何基於相鄰字元的啟發式都會同時誤殺「桶子今天又通宵」這類正常命中，因此刻意不加。
- **字面不同的語意矛盾**：「沒有養寵物」與「養了一隻貓」會並存。
- **三軌檢索的話題匹配是字面的**：語意相反但字面相似的舊事實仍可能被選入。

### 設計取捨

- **維度 C 不篩相關性**：只要在最近 15 則裡講過話就算「在場」，與話題是否相關不論。屬產品取捨——「誰在場」對群聊 bot 確實有用，但 C 來源目前享有與被明確提及者完全相同的待遇（同樣配額、同樣態度指令）。

### 尚未處理的風險

- **`interaction_notes` 無保護**：模型每次提煉**整份取代**互動印象。事實有聯集、熱度、否定推翻三層保護，互動印象則一次壞的提煉就永久消失——而它承載的正是【核心性格】這種累積最久、最難重建的內容。prompt 有要求模型「參考歷史記錄適度保留」，正常情況會演進而非清空，但這是三種記憶中唯一沒有防護網的。
- **好感度扣分無跨日累積限制**：加分有 `daily_favorability_gain` 追蹤並受 15 分/日上限約束；扣分只有單次夾限 100，對 -2~+2 的範圍等於無限制。連續誤判可讓關係階級快速崩落，回升卻受每日上限拖慢。
- **`messages` 無保留策略**：表會無限增長，且 n-gram 索引使 FTS 體積約為原本的 4~5 倍。
