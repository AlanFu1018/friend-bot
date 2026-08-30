# 人格與好感度（Persona & Favorability）

> 機器人的人格從哪裡來、如何對不同群友展現不同態度、以及好感度如何變動。
>
> 最後更新：2026-08-30，已對照程式碼核實。

---

## 1. 人格來源

人格定義存於 [`config/persona.md`](../config/persona.md)，由 `config.py` 的 `_load_persona_prompt()` 在啟動時讀入為 `SYSTEM_PROMPT`。

檔案路徑可用 `persona.persona_file` 設定，會依序在 `config/` 與專案根目錄尋找。找不到或讀取失敗時退回 `persona.system_prompt` 設定，最後才用內建的通用群友人設。

**改人格不需要動程式碼**——編輯 `persona.md` 後重啟即可。

`SYSTEM_PROMPT` 會被組進 `build_system_instruction()`，透過 `GenerateContentConfig.system_instruction` 傳給 Gemini（**不在 prompt 字串裡**，見 [`prompt_pipeline.md`](prompt_pipeline.md) §3.1），後面接上基本資訊與 9 條回覆原則。

---

## 2. 四階關係階級

好感度（0~100）決定關係階級，階級決定 prompt 中注入哪一段態度指令。

| 階級 | 好感度 | 態度 |
| :--- | :--- | :--- |
| `stranger` | 0 ~ 19 | 冷淡、嚴肅、講究邏輯，不主動開玩笑，公事公辦 |
| `familiar` | 20 ~ 49 | 經典傲嬌，嘴硬心軟，適度吐槽與接梗 |
| `trusted` | 50 ~ 79 | 傲嬌防線大幅變薄，易害羞破防，主動關心作息與日常 |
| `cherished` | 80 ~ 100 | 真誠溫柔與高度信賴（嬌 70%／傲 30%），深層羈絆 |

對照表定義於 `ai/prompts.py` 的 `TIER_ATTITUDE_MAP`，級距計算在 `MemoryManager.compute_relationship_tier()`。

### 每個人各自的態度

態度指令是**逐人注入**的：發言者有自己的一段，prompt 中每位其他群友也各自帶著自己的階級指令。

```
【主要發言者 岡部 的個人特徵記憶】:
- 【對此用戶態度 (Tier 3 實驗室夥伴)】：…

【對話中提及 / 近期在場的其他群友畫像】:
- 用戶名稱: 桶子 (關係階級: familiar)
  • 【對此用戶態度 (Tier 2 熟識群友)】：…
```

這讓機器人能在同一次回覆中對不同人展現不同親密度。

---

## 3. 好感度變動

### 誰決定增減

背景提煉時，模型在 `favorability_delta` 欄位給出 **-2 ~ +2** 的整數。它是 `_safe_apply_updates()` 眾多輸出之一，與事實、互動印象一併處理，因此受**同一套 `allowed_uids` 白名單保護**——使用者無法透過訊息內容操控第三方的好感度。

### 評分基準

兩種提煉引擎的措辭略有不同，但共同原則是**只衡量「對紅莉栖本人的態度」**：

| delta | 情境 |
| :--- | :--- |
| +1 ~ +2 | 送禮、認真討論科學、由衷感謝、關心紅莉栖 |
| 0 | 一般問答、日常打招呼、陳述客觀事實 |
| -1 ~ -2 | 惡意挑釁、無理取鬧、人身攻擊 |

監聽頻道的批次 prompt 曾經沒有限定「對紅莉栖」，導致群友之間互嗆可能扣到對 bot 的好感度——bot 根本沒參與那場對話。已修正為明確限定，且說明「這段對話紅莉栖並未參與，多數情況應給 0」。

### 每日上限防刷

`calculate_favorability_update()`（`memory/memory_manager.py`）：

```python
today = 今天日期
if last_gain_date != today:
    daily_gain = 0                       # 跨日自動重置

if delta > 0:
    available = max(0, gain_limit - daily_gain)
    actual = min(delta, available)       # 受每日累積上限約束
    daily_gain += actual
elif delta < 0:
    actual = max(delta, -loss_limit)     # 只有單次夾限

new_score = clamp(current + actual, 0, 100)
```

`daily_favorability_gain` 與 `last_gain_date` 存於畫像，跨日自動歸零。

---

## 4. 隱密性

好感度**從不主動顯示在對話中**。模型只看到態度指令（「Tier 3 實驗室夥伴」），看不到分數。使用者若想查看要用 `/kurisu-profile`。

這是刻意的：讓關係進展表現為「口吻慢慢變了」，而不是一個可以刷的數值。

---

## 5. 相關設定

```yaml
favorability:
  enable_favorability: true    # 關閉後不注入態度指令、不計算好感度
  default_favorability: 30     # 新用戶初始值（落在 familiar）
  daily_gain_limit: 15         # 每日累積加分上限
  daily_loss_limit: 100        # 單次扣分夾限

persona:
  bot_name: 克莉絲
  persona_file: persona.md
```

---

## 6. 已知問題

### 扣分無跨日累積限制（不對稱）

加分有 `daily_favorability_gain` 追蹤，受 15 分／日約束；**扣分只有「單次夾限 100」**，而模型的 delta 範圍是 -2 ~ +2，等於**完全沒有限制**。

後果不對稱：連續幾次誤判可讓關係從 `cherished` 快速掉到 `stranger`，回升卻受每日上限拖慢，要好幾天。

記錄於 [`mem_sys_bugs.md`](mem_sys_bugs.md) P4-2 與 [`improv.md`](improv.md) 6.1，尚未處理——需先確認「一次惡意行為就能重置關係」是否為刻意設計。

### `interaction_notes` 無保護

互動印象（【核心性格】【社交關係】【近期動態】三維度）由模型每次提煉**整份取代**，沒有任何合併或保護機制。相較之下事實有四層保護。一次壞的提煉可永久抹掉累積最久的人格觀察。

記錄於 [`mem_sys_bugs.md`](mem_sys_bugs.md) P4-1。

### 好感度上限與階級門檻的互動

`daily_gain_limit: 15` 意味著從 `familiar`（20）爬到 `cherished`（80）在理想情況下最少要 4 天。這是刻意的節奏設計，但若日後調整級距門檻，需一併檢視這個爬升曲線是否仍合理。
