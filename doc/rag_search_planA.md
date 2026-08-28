# 🔍 方案 A：三軌混合事實記憶檢索與雙重熱度加權架構 (rag_search_planA.md)

---

## ▍1. 核心設計理念

**三軌混合事實記憶架構（3-Track Fact Memory Architecture）** 模擬人類大腦對熟人的多層次工作記憶：
1. **軌道 1：核心高頻/熱門特徵 (Heat / Frequency)**：用戶最常被提及或最核心的人設特徵（常駐 2~3 條）。
2. **軌道 2：話題相關性檢索 (Topic RAG)**：與當前對話主題最匹配的客觀喜好或經驗（動態匹配 3~4 條）。
3. **軌道 3：最新近況事實 (Recent)**：最近幾天新記錄或更新的新鮮事（時間倒序 2~3 條）。

---

## ▍2. 檢索與配額架構圖

```
                                【當前對話 Context / Query】
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
         【軌道 1：核心高頻 (Heat)】     【軌道 2：話題相關 (RAG)】   【軌道 3：最新近況 (Recent)】
         (撈取 hits 最多 2~3 條)       (規則分詞匹配 3~4 條)       (時間倒序最新 2~3 條)
                    │                         │                         │
                    └─────────────────────────┼─────────────────────────┘
                                              ▼
                             【聯集合併去重 (Union & Deduplicate)】
                                              │
                                              ▼
                             【單人最終注入 Prompt：約 8~10 條事實】
```

---

## ▍3. 雙重熱度加權機制 (Hybrid Heat Mechanism)

為了讓事實的熱度（`hits`）既能反映**日常聊天的活躍度**，又能精準沉澱**用戶的核心人格**，系統採用「雙重加權」機制：

```
                    ┌────────────────────────┐
                    │ 使用者在 Discord 發言  │
                    └───────────┬────────────┘
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼ (即時線上對話)                                 ▼ (背景非同步提煉)
┌───────────────────────────────┐               ┌───────────────────────────────┐
│  【維度 1：檢索命中累加 (+1)】 │               │  【維度 2：提煉重複確認 (+3)】│
│                               │               │                               │
│ 當前話題觸發了 RAG 命中       │               │ AI 分析發言，發現用戶         │
│ ➔ 該事實被選入 Prompt         │               │ 「再次主動展現/提及該特徵」   │
│ ➔ `hits += 1`                 │               │ ➔ `hits += 3`                 │
│ (冷卻機制：同事實1小時限+1)    │               │ (權重更高，沉澱核心人設)      │
└───────────────────────────────┘               └───────────────────────────────┘
```

1. **維度 1（日常對話 RAG 命中加權）**：
   - 當某條事實因當前話題匹配被選入 Prompt 時，`hits += 1`。
   - **冷卻保護**：設置冷卻時間（如 1 小時內同條事實最多加 1 次），避免連續幾句同一話題導致計數暴增。
2. **維度 2（背景提煉重複確認加權）**：
   - 當背景提煉任務（單人 JIT / 監聽頻道批次）再次提取到既有事實時，賦予更高權重 `hits += 3`。
   - 代表該特徵是用戶反覆強調或展現的重要特質（如反覆自稱狂氣科學家、天天熬夜）。

---

## ▍4. 資料結構與相容性設計

### 1. 結構化事實資料格式（JSON）
```json
[
  {
    "text": "喜歡喝 Dr Pepper",
    "hits": 35,
    "created_at": 1724800000,
    "last_used_at": 1724830000
  },
  {
    "text": "最近剛入手 Realforce 靜音鍵盤",
    "hits": 3,
    "created_at": 1724825000,
    "last_used_at": 1724825000
  }
]
```

### 2. 向下相容保護
- 若資料庫中存在舊格式的純字串列表 `["喜歡喝 Dr Pepper"]`，讀取時自動平滑轉為結構化字典並預設 `hits = 1`。
- `/kurisu-profile` 指令與 Embed 渲染時，統一提取 `[f["text"] for f in facts]`，視覺體驗不受任何影響。

---

## ▍5. 程式碼演算法實作範例

```python
import re
import time
from typing import List, Dict, Any, Set, Tuple

STOPWORDS: Set[str] = {
    "今天", "明天", "昨天", "這個", "那個", "什麼", "我們", "你們", "他們",
    "一下", "的話", "可以", "覺得", "可能", "還是", "因為", "所以", "而且",
    "但是", "如果", "知道", "現在", "只是", "真的", "怎麼", "自己", "大家",
    "正在", "已經", "一直", "沒有", "不是", "不要", "就是"
}

def extract_keywords(text: str) -> Set[str]:
    """提取有語意的關鍵字集合（英文實詞 + 中文 2/3-gram）"""
    if not text:
        return set()
    clean = re.sub(r'<@!?\d+>|https?://\S+', '', text.lower())
    keywords = set()
    for word in re.findall(r'[a-z0-9_\-\+]{2,}', clean):
        if word not in STOPWORDS:
            keywords.add(word)
    chinese_text = "".join(re.findall(r'[\u4e00-\u9fa5]+', clean))
    n = len(chinese_text)
    for i in range(n):
        if i + 2 <= n:
            token = chinese_text[i:i+2]
            if token not in STOPWORDS:
                keywords.add(token)
        if i + 3 <= n:
            token = chinese_text[i:i+3]
            if token not in STOPWORDS:
                keywords.add(token)
    return keywords

def filter_facts_three_tracks(
    facts_data: List[Any],
    query_text: str,
    max_total: int = 8,
    heat_limit: int = 2,
    recent_limit: int = 2
) -> Tuple[List[str], List[str]]:
    """
    三軌混合檢索：
    1. 軌道 1 (Heat): 熱度最高事實 Top-heat_limit
    2. 軌道 3 (Recent): 最新事實 Top-recent_limit
    3. 軌道 2 (RAG): 關鍵字匹配最高事實 Top-(max_total - heat - recent)
    4. 合併去重並控制在 max_total 條以內
    回傳: (注入 Prompt 的事實列表, 本次被 RAG 命中需增加熱度的事實列表)
    """
    if not facts_data:
        return [], []

    # 正規化資料結構
    normalized_facts = []
    for item in facts_data:
        if isinstance(item, str):
            normalized_facts.append({"text": item, "hits": 1, "created_at": 0, "last_used_at": 0})
        elif isinstance(item, dict):
            normalized_facts.append(item)

    if len(normalized_facts) <= max_total:
        return [f["text"] for f in normalized_facts], []

    # 軌道 1：核心高頻 (按 hits 排序)
    sorted_by_hits = sorted(normalized_facts, key=lambda x: x.get("hits", 0), reverse=True)
    heat_facts = [f["text"] for f in sorted_by_hits[:heat_limit]]

    # 軌道 3：最新近況 (按順序或 created_at 取最後幾條)
    recent_facts = [f["text"] for f in normalized_facts[-recent_limit:]]

    # 軌道 2：話題 RAG 檢索
    query_keywords = extract_keywords(query_text)
    rag_scored = []
    rag_hit_texts = []
    for f in normalized_facts:
        f_text = f["text"]
        f_lower = f_text.lower()
        score = sum(1.0 + len(kw) * 0.2 for kw in query_keywords if kw in f_lower)
        if score > 0:
            rag_scored.append((f_text, score))
            rag_hit_texts.append(f_text)

    rag_scored.sort(key=lambda x: x[1], reverse=True)
    rag_facts = [f_text for f_text, _ in rag_scored]

    # 合併三軌（保留優先級並去重）
    merged = []
    for text in (heat_facts + rag_facts + recent_facts):
        if text not in merged:
            merged.append(text)
        if len(merged) >= max_total:
            break

    # 若未達 max_total，從最新事實向前補足
    if len(merged) < max_total:
        for f in reversed(normalized_facts):
            t = f["text"]
            if t not in merged:
                merged.append(t)
            if len(merged) >= max_total:
                break

    return merged, rag_hit_texts
```
