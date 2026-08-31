# 事實容量控制與去重機制 — 功能規格

> 本文件狀態：**已實作**（2026-09-01）。目標：解決 `user_profiles.facts` 原本無上限成長的問題
> （`merge_facts()` 原本只會新增/取代/加權，沒有任何路徑會縮小清單）。
>
> **實作與本規格的一處差異**：`dedupe_facts()`／`combine_facts()` 等協調函式最終沒有放進
> `memory/memory_manager.py`，而是獨立成 `ai/facts_dedup.py` 的 `FactsDeduplicator` 類別
> （結構比照既有的 `ai/memory_extractor.py`／`MemoryExtractor`）。原因：`memory/` 目前完全不依賴
> `ai/`（純 DB／決定性邏輯層），把持有 `GeminiClient`／`FactsEmbeddingClient` 的協調邏輯放進
> `memory_manager.py` 會打破這個既有分層。`memory_manager.py` 本身只新增了純函式：
> `evict_stale_facts()`、`merge_fact_metadata()`、`resolve_fact_conflict()`、`group_facts()`、
> `cosine_similarity()`、`get_facts_missing_embedding()`、`get_user_ids_with_min_facts()`，
> 都不涉及任何 I/O。詳見 §2 各檔案實際落點。

---

## 0. 兩個獨立機制（先釐清分工，避免混在一起設計）

| 機制 | 觸發時機 | 性質 | 目的 |
| :--- | :--- | :--- | :--- |
| **A. 硬上限淘汰** | `merge_facts()` 每次寫入後，**同步**執行 | 決定性規則（hits + 時間） | **保證**任何時刻 facts 數量不超過上限——唯一真正解決「無限增長」的機制 |
| **B. 語意去重** | 背景週期批次（如 `sweep_unextracted()` 的模式），**非同步** | Embedding 分群 + LLM 判斷是否為同一事實 | 減少「同一件事不同講法」造成的假性膨脹，降低機制 A 被觸發、犧牲真實事實的頻率 |

機制 B 不保證有界（去重比率不可控），機制 A 才是邊界保證來源，兩者必須分開實作，不能共用同一個候選篩選函式。

---

## 1. Config 新增項目

| 設定 | 說明 |
| :--- | :--- |
| `facts_max_stored` | 硬上限，超過即觸發機制 A 淘汰 |
| `facts_similarity_threshold` | 機制 B 分群時的 cosine 相似度門檻 |
| `facts_dedup_cluster_max_size` | 單一群組送進 LLM 判斷的事實數上限（避免單次呼叫過大） |
| `facts_embedding_model` | 目前用於計算 embedding 的模型代號（如 Gemini 的 embedding 模型） |
| `facts_embedding_model_version` | 模型版本標記，供偵測舊向量是否需要重算 |
| `facts_dedup_sweep_interval_seconds` | 機制 B 背景批次的執行間隔 |

---

## 2. 新增／修改函式

### `memory/memory_manager.py`

| 函式 | 類型 | 功能說明 |
| :--- | :--- | :--- |
| `normalize_facts()` | 修改 | fact 字典結構新增 `embedding: Optional[List[float]]`、`embedding_model_version: Optional[str]` 欄位；既有資料預設為 `None`（尚無 embedding），由機制 B 首次掃描時 lazy backfill。 |
| `evict_stale_facts(facts, max_total)` | **新增** | 機制 A 的決定性淘汰規則：依 `hits` 由低到高、`last_used_at` 由舊到新排序，超過 `max_total` 的部分裁掉。永遠優先保留熱度最高的一批（延續三軌檢索既有的「熱度＝標誌性人設常駐」價值觀）。 |
| `merge_facts()` | 修改 | 現有邏輯跑完後，尾端呼叫一次 `evict_stale_facts()`，確保回傳結果永遠 ≤ 上限。這是保證有界性的唯一入口。 |
| `dedupe_facts(user_id)` | **新增** | 機制 B 的協調函式（由背景排程呼叫）：<br>1. 讀取該使用者完整 profile<br>2. 找出缺 embedding／embedding 版本過舊的 facts，呼叫 `facts_embedding.embed_texts()` 補算並寫回<br>3. 呼叫 `group_facts()` 分群<br>4. 對每個 size ≥ 2 的群組呼叫 `gemini_client.facts_similar_check()`<br>5. 依回傳結果分流：重複 → 直接合併；矛盾 → 交給 `resolve_fact_conflict()`<br>6. 透過 `apply_profile_update()` 的 per-user 鎖寫回 |
| `group_facts(facts_with_embeddings, threshold)` | **新增** | 決定性分群：對全部帶 embedding 的 facts 做 pairwise cosine similarity，相似度 > threshold 的兩兩相連，取連通分量成群（union-find）。單一元素的群組（無相似對象）不送 LLM。**範圍是全部 facts，不限熱門或冷門**——這是你在對話中發現的「熱門事實也可能重複」問題的修正點。 |
| `resolve_fact_conflict(fact_a, fact_b)` | **新增** | 當 `facts_similar_check()` 回報「同主題但語意矛盾」（而非單純重複）時，**不採用模型的保留/捨棄結果**，改用既有 `has_negation()` 極性判斷 + 「保留 `last_used_at`/`created_at` 較新者」規則決定去留，與 `merge_facts()` 既有的否定推翻邏輯共用同一套決定性規則。 |
| `merge_fact_metadata(kept_fact, discarded_facts)` | **新增** | 多條事實判定為單純重複、合併為一條時的算術規則（不可信任模型計算）：`created_at` 取最早值、`last_used_at` 取最新值；`hits` 的計算方式取最大值。 |
| `_get_facts_missing_embedding(facts)` | **新增**（內部輔助） | 篩出尚無 embedding、或 `embedding_model_version` 與目前設定不符的 facts，供 `dedupe_facts()` 呼叫補算。 |

### `ai/facts_embedding.py`（新檔案）

| 函式 | 功能說明 |
| :--- | :--- |
| `embed_text(text) -> List[float]` | 呼叫 Gemini embedding API，將單一事實文字轉為向量。 |
| `embed_texts(texts) -> List[List[float]]` | 批次版本，減少 API 往返次數。 |
| `cosine_similarity(vec_a, vec_b) -> float` | 純數學工具函式，供 `group_facts()` 呼叫。 |

失敗處理：任一筆 embedding 失敗不中斷整批，該筆事實維持「無 embedding」狀態，下次批次自然重試（沿用 `sweep_unextracted()` 的「失敗就留到下次」精神）。

### `ai/gemini_client.py`

| 函式 | 功能說明 |
| :--- | :--- |
| `facts_similar_check(cluster: List[str]) -> dict` | 送一群 embedding 相似的事實原文給 Gemini，要求回傳分類：<br>- **duplicate**（同一件事不同講法）→ 回傳保留哪一句，**必須是輸入原句之一，不可生成新句子**（集合選擇，不是摘要生成）<br>- **conflict**（同主題但語意矛盾）→ 只需標記，不需模型自己選留哪句（由 `resolve_fact_conflict()` 決定）<br>- **none**（其實不是同一件事）→ 都保留<br>- 呼叫失敗或格式錯誤 → 預設整群保留（沿用 `should_remove_fact()` 一貫的保守原則：寧可漏判也不誤刪） |

### 排程整合

| 函式 | 功能說明 |
| :--- | :--- |
| `sweep_dedupe_facts()` | 類似現有 `sweep_unextracted()` 的週期任務，掃描 `user_profiles`（可只挑 facts 數接近/超過上限的使用者以減少無謂掃描），呼叫 `dedupe_facts(user_id)`。掛載位置待定（`memory_extractor.py` 或獨立排程模組）。 |

---

## 3. 目前設計中被指出／發現的缺口

1. **embedding 何時計算、由誰觸發沒講清楚**：不能塞進 `merge_facts()`（純函式，不該做 API I/O）。改為 lazy backfill——平時只存文字，缺 embedding 的事實留到下次 `dedupe_facts()` 批次才補算。

2. **機制 A 與機制 B 原本被混在一起**：原始設計用同一個候選篩選函式（低 hits + 久未用）同時決定「該淘汰誰」與「該去重誰」。兩者呼叫時機（同步 vs 背景批次）與目的（保證有界 vs 品質優化）都不同，已拆成 `evict_stale_facts()` 與 `dedupe_facts()`/`group_facts()` 兩條獨立路徑。

3. **「熱門事實也可能重複」未被涵蓋**：若去重範圍限定在冷門尾巴，兩條各自被獨立提煉、各自累積 hits 的熱門重複事實永遠不會被抓到。修正為 `group_facts()` 對全部 facts 分群，不分熱門冷門。

4. **矛盾 vs 重複沒有分開處理**：`facts_similar_check()` 若只回傳「保留/捨棄」，遇到「同主題但立場相反」的兩條事實時，等於讓模型自己決定該信哪一句——這正是 `has_negation()` 機制原本要避免的猜測。已拆出獨立分類與 `resolve_fact_conflict()`。

5. **合併後的 `hits`/`created_at`/`last_used_at` 算術規則未定義**：這些數值不能讓模型計算，必須是程式碼固定規則。`hits` 該加總還是取最大值尚待決定，會直接影響機制 A 判斷「熱門」的準確度。

6. **併發鎖未提及**：`dedupe_facts()` 寫回 profile 必須走 `apply_profile_update()` 的 per-user 鎖，否則會與監聽頻道／收尾提煉的並發寫入互相覆蓋（`memory_sys_design.md` §7 已踩過的坑）。

7. **embedding 模型版本失效問題未考慮**：換 embedding 模型或模型升版會讓舊向量與新向量空間不相容，分群結果會悄悄失準且不會被發現。需在 fact 結構存 `embedding_model_version`，換版時整批判定失效並重算，比照 `db.py` 現有的 schema 版本遷移模式。

8. **模型判斷失敗時的預設行為未定義**：`facts_similar_check()` 呼叫失敗或回傳格式錯誤時應整群保留不動，不能因判斷失敗而誤刪，需在實作中明確寫死。

9. **缺少對應測試規劃**：`group_facts()`、`evict_stale_facts()`、`resolve_fact_conflict()` 都是可脫離 API 的決定性邏輯，應比照 `test/tests_verify.py` 現有 62 項測試的模式補上案例，尤其是「熱門重複」「矛盾 vs 重複」這兩個邊界情況。

10. **首次遷移的一次性尖峰未考慮**：現有 profiles 的 facts 都沒有 `embedding` 欄位，第一次跑 `dedupe_facts()` 時所有使用者會同時觸發補算，可能造成一次性 API 呼叫尖峰，需要節流或分批處理。
