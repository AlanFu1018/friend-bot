import asyncio
import json
import math
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set, Callable
from .db import get_db_connection
from src.friend_bot.core.config import (
    SHORT_TERM_HISTORY_LIMIT,
    HISTORY_RECALL_LIMIT,
    HISTORY_RECALL_MIN_SCORE,
    HISTORY_RECALL_MAX_QUERY_TOKENS,
    DEFAULT_FAVORABILITY,
    DAILY_GAIN_LIMIT,
    DAILY_LOSS_LIMIT,
    FACTS_SPEAKER_MAX_TOTAL,
    FACTS_SPEAKER_HEAT_LIMIT,
    FACTS_SPEAKER_RECENT_LIMIT,
    FACTS_OTHERS_MAX_TOTAL,
    FACTS_OTHERS_HEAT_LIMIT,
    FACTS_OTHERS_RECENT_LIMIT,
    FACTS_RAG_HIT_COOLDOWN_SECONDS,
    FACTS_EXTRACTION_REAFFIRM_BONUS,
    FACTS_RAG_HIT_BONUS,
    MAX_ALIASES_PER_USER,
    FACTS_MAX_STORED_PER_USER,
    FACTS_DEDUP_SIMILARITY_THRESHOLD,
    INTERACTION_NOTES_SHRINK_RATIO
)
from src.friend_bot.core.logger import get_logger

logger = get_logger("memory")

# 高頻日常無語意停用詞清單
STOPWORDS: Set[str] = {
    "今天", "明天", "昨天", "這個", "那個", "什麼", "我們", "你們", "他們",
    "一下", "的話", "可以", "覺得", "可能", "還是", "因為", "所以", "而且",
    "但是", "如果", "知道", "現在", "只是", "真的", "怎麼", "自己", "大家",
    "正在", "已經", "一直", "沒有", "不是", "不要", "就是", "最近", "比較",
    "應該", "然後", "其實", "這樣", "那樣", "一起", "還有", "一個", "一些"
}

# 含「不 / 沒」但語意上並非否定的常見詞。偵測否定前先剔除，避免把「覺得不錯」
# 這類正面敘述誤判為否定。
NON_NEGATION_WORDS: Tuple[str, ...] = (
    "不錯", "不過", "不但", "不僅", "不只", "不外乎", "差不多",
    "對不起", "了不起", "不好意思", "不得了", "要不然", "捨不得"
)

# remove_facts 的最低引述門檻：當「事實包含刪除詞」時，刪除詞必須佔該事實一定比例
# 才准刪除。刪除是不可逆操作，因此門檻刻意偏保守——寧可留下過時事實（它是可見的，
# 日後仍可再更正），也不要誤刪一條仍然成立的事實（無法復原）。
FACT_REMOVAL_MIN_RATIO = 0.4
FACT_REMOVAL_MIN_LENGTH = 2

# 英文否定標記
NEGATION_PATTERN_EN = re.compile(
    r"\b(?:not|never|no longer|isn'?t|aren'?t|wasn'?t|weren'?t|don'?t|doesn'?t|"
    r"didn'?t|won'?t|can'?t|cannot|stopped|quit|dislikes?|disliked)\b"
)

class MemoryManager:
    """三層記憶管理器：負責儲存與檢索短期對話、長期畫像、好感度與跨頻道歷史回憶"""

    # 每位使用者專屬的非同步鎖，序列化「讀取 -> 合併 -> 寫回」流程，避免背景任務併發覆寫 (lost update)
    _user_locks: Dict[str, asyncio.Lock] = {}
    _locks_guard: asyncio.Lock = asyncio.Lock()

    @staticmethod
    async def _get_user_lock(user_id: str) -> asyncio.Lock:
        """取得（必要時建立）特定使用者專屬的鎖"""
        uid = str(user_id)
        lock = MemoryManager._user_locks.get(uid)
        if lock is None:
            async with MemoryManager._locks_guard:
                lock = MemoryManager._user_locks.get(uid)
                if lock is None:
                    lock = asyncio.Lock()
                    MemoryManager._user_locks[uid] = lock
        return lock

    @staticmethod
    async def apply_profile_update(
        user_id: str,
        mutator: Callable[[Optional[Dict[str, Any]]], Optional[Dict[str, Any]]]
    ) -> None:
        """
        以每位使用者專屬鎖包住「讀取現有畫像 -> 計算變更 -> 寫回」的完整流程。

        mutator 接收目前的 profile（可能為 None，代表尚無記錄），並回傳要傳給
        update_user_profile 的關鍵字參數字典；若判斷無需變更，回傳 None 即可跳過寫入。
        所有需要「讀後寫」使用者畫像的呼叫方都應透過此方法，避免多個並行背景任務
        （JIT 提煉、RAG 命中加權、對話提煉等）同時讀到舊資料而互相覆蓋彼此的更新。
        """
        uid = str(user_id)
        lock = await MemoryManager._get_user_lock(uid)
        async with lock:
            current = await MemoryManager.get_user_profile(uid)
            update_kwargs = mutator(current)
            if update_kwargs is None:
                return
            await MemoryManager.update_user_profile(user_id=uid, **update_kwargs)

    @staticmethod
    def compute_relationship_tier(score: int) -> str:
        """根據好感度數值計算關係階級 (Progression Tier)"""
        if score < 20:
            return "stranger"
        elif score < 50:
            return "familiar"
        elif score < 80:
            return "trusted"
        else:
            return "cherished"

    # 4 階關係階級與 Tier N 標號的對照（1-indexed，對齊 prompts.py 的 TIER_ATTITUDE_MAP 用語）
    _TIER_RANK = {"stranger": 1, "familiar": 2, "trusted": 3, "cherished": 4}

    @staticmethod
    def tier_rank(tier: str) -> int:
        """將關係階級名稱轉為 Tier 數字（1~4），無法辨識時視為最低的 Tier 1"""
        return MemoryManager._TIER_RANK.get(tier, 1)

    @staticmethod
    def calculate_favorability_update(
        current_score: int,
        current_daily_gain: int,
        last_gain_date: str,
        delta: int,
        gain_limit: int = DAILY_GAIN_LIMIT,
        loss_limit: int = DAILY_LOSS_LIMIT
    ) -> Tuple[int, str, int, str]:
        """
        計算好感度更新（含跨日重設與每日上下限防刷保護）
        回傳: (new_score, new_tier, new_daily_gain, today_str)
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 若跨日，重置今日累積增量
        if last_gain_date != today_str:
            daily_gain = 0
        else:
            daily_gain = current_daily_gain

        if delta > 0:
            # 加分：受到每日上限保護
            available_gain = max(0, gain_limit - daily_gain)
            actual_delta = min(delta, available_gain)
            daily_gain += actual_delta
        elif delta < 0:
            # 扣分：受到每日下限保護
            actual_delta = max(delta, -loss_limit)
        else:
            actual_delta = 0

        # 計算新分數並夾在 0 ~ 100 之間
        new_score = max(0, min(100, current_score + actual_delta))
        new_tier = MemoryManager.compute_relationship_tier(new_score)

        return new_score, new_tier, daily_gain, today_str

    @staticmethod
    def normalize_facts(facts_raw: Any) -> List[Dict[str, Any]]:
        """將舊格式純字串列表或混合格式統一平滑轉為標準字典列表"""
        if not facts_raw:
            return []
        
        now = int(time.time())
        normalized = []
        if isinstance(facts_raw, list):
            for item in facts_raw:
                if isinstance(item, dict) and "text" in item:
                    embedding = item.get("embedding")
                    normalized.append({
                        "text": str(item.get("text", "")).strip(),
                        "hits": int(item.get("hits", 1)),
                        "created_at": int(item.get("created_at", now)),
                        "last_used_at": int(item.get("last_used_at", now)),
                        "embedding": list(embedding) if isinstance(embedding, list) else None,
                        "embedding_model": str(item.get("embedding_model", ""))
                    })
                elif isinstance(item, str) and item.strip():
                    normalized.append({
                        "text": item.strip(),
                        "hits": 1,
                        "created_at": now,
                        "last_used_at": now,
                        "embedding": None,
                        "embedding_model": ""
                    })
        elif isinstance(facts_raw, str) and facts_raw.strip():
            try:
                parsed = json.loads(facts_raw)
                return MemoryManager.normalize_facts(parsed)
            except Exception:
                normalized.append({
                    "text": facts_raw.strip(),
                    "hits": 1,
                    "created_at": now,
                    "last_used_at": now,
                    "embedding": None,
                    "embedding_model": ""
                })
        return normalized

    @staticmethod
    def to_fact_texts(facts_raw: Any) -> List[str]:
        """將任意格式（標準字典列表 / 舊格式純字串列表 / 混合格式）的 facts 統一轉為純文字字串列表"""
        if not facts_raw:
            return []
        return [f["text"] if isinstance(f, dict) else str(f) for f in facts_raw]

    @staticmethod
    def has_negation(text: str) -> bool:
        """
        判斷一段事實文字是否帶有否定語意。

        用途是辨識「新事實推翻舊事實」的情況：舊事實「喜歡台北」與新事實
        「已經不喜歡台北了」在字面上是包含關係，若不看否定詞會被誤判為「重複確認」，
        導致錯誤事實被加權、更正被丟棄。

        判斷刻意保守——**寧可漏判也不要誤判**。漏判只會退回原本的「重複確認」行為，
        誤判卻會刪掉一條仍然成立的事實。因此：
        - 只採用「不 / 沒」這兩個相對可靠的中文否定標記；
        - 先剔除「不錯 / 不過 / 差不多」等含「不」卻非否定的常見詞；
        - 不採用「未 / 無 / 非」，因為「未來」「無聊」「非常」等常見詞會造成大量誤判
          （「非常喜歡台北」若被判為否定，就會把「喜歡台北」誤刪）。
        """
        cleaned = (text or "").lower()
        for word in NON_NEGATION_WORDS:
            cleaned = cleaned.replace(word, "")

        if "不" in cleaned or "沒" in cleaned:
            return True
        return bool(NEGATION_PATTERN_EN.search(cleaned))

    @staticmethod
    def should_remove_fact(fact_text: str, remove_term: str) -> bool:
        """
        判斷模型給的 remove_facts 詞是否足以刪除某一條既有事實。

        原本是無門檻的雙向子字串比對，導致籠統的刪除詞會連帶清掉無關事實：
        `remove_facts=['台中']` 會把「以前在台中唸書」「喜歡台中的太陽餅」一起刪光，
        即使它們與這次的搬家更正毫無關係。這是**不可逆**的資料損失。

        三種情況分開處理：
        - 完全相同 → 刪除（無疑義）
        - 刪除詞**包含**整條事實 → 刪除（模型引述得比事實更完整，識別明確）
        - 事實**包含**刪除詞 → 危險方向，刪除詞需達最低引述比例才准刪

        不能改成「只接受完整引述」：`prompts.py` 的範例本身就教模型給部分引述
        （事實「住在台中市」對應 `remove_facts: ["住在台中"]`），改嚴會讓正常更正失效。
        """
        fact = (fact_text or "").strip().lower()
        term = (remove_term or "").strip().lower()
        if not fact or not term:
            return False

        if fact == term or fact in term:
            return True

        if term in fact:
            required = max(
                FACT_REMOVAL_MIN_LENGTH,
                math.ceil(FACT_REMOVAL_MIN_RATIO * len(fact))
            )
            return len(term) >= required

        return False

    @staticmethod
    def topic_key(text: str) -> str:
        """
        取出一段事實「去掉否定詞後的主題」，用於判斷兩條事實是否在談同一件事。

        否定極性的比對必須先能配對到同一主題才有意義。若直接比對原文，
        「不喜歡吃辣」與「現在喜歡吃辣了」不成子字串關係（「不」破壞了包含性），
        兩條互相矛盾的事實會被當成不相干而同時保留。去掉否定詞後兩者的主題
        都是「喜歡吃辣」，才能配對成功並辨識出極性相反。

        「不錯」「不過」這類含「不」卻非否定的詞會先被保護起來，不參與剝除。
        """
        cleaned = (text or "").lower()

        placeholders: Dict[str, str] = {}
        for idx, word in enumerate(NON_NEGATION_WORDS):
            if word in cleaned:
                key = f"\x01{idx}\x01"
                placeholders[key] = word
                cleaned = cleaned.replace(word, key)

        cleaned = cleaned.replace("不", "").replace("沒", "")
        cleaned = NEGATION_PATTERN_EN.sub(" ", cleaned)

        for key, word in placeholders.items():
            cleaned = cleaned.replace(key, word)

        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _is_same_topic(key_a: str, key_b: str) -> bool:
        """
        判斷兩個主題字串是否指向同一件事。

        極短的主題（少於 2 字）只接受完全相同，避免剝除否定詞後殘留的單字
        （如「不去」→「去」）到處誤配。
        """
        if not key_a or not key_b:
            return False
        if key_a == key_b:
            return True
        if min(len(key_a), len(key_b)) < 2:
            return False
        return key_a in key_b or key_b in key_a

    @staticmethod
    def evict_stale_facts(
        facts_raw: Any,
        max_total: int = FACTS_MAX_STORED_PER_USER
    ) -> List[Dict[str, Any]]:
        """
        機制 A：硬上限淘汰（決定性規則，保證有界）。

        依 `hits` 由低到高、`last_used_at` 由舊到新排序，超過 `max_total` 的部分裁掉，
        永遠優先保留熱度最高、最近仍在使用的一批（延續三軌檢索既有的「熱度＝標誌性
        人設常駐」價值觀）。這是唯一保證 facts 數量不會無限增長的機制——語意去重
        （見 `group_facts()` / `merge_fact_metadata()`）只是背景品質優化，降低這裡
        需要淘汰真實事實的頻率，本身不保證有界。
        """
        normalized = MemoryManager.normalize_facts(facts_raw)
        if len(normalized) <= max_total:
            return normalized

        ordered = sorted(
            normalized,
            key=lambda f: (f.get("hits", 1), f.get("last_used_at", 0)),
            reverse=True
        )
        kept = ordered[:max_total]
        dropped = ordered[max_total:]
        logger.info(
            f"🗑️ [事實容量淘汰] 超過上限 {max_total} 條，淘汰 {len(dropped)} 條"
            f"低熱度／久未使用事實：{[f['text'] for f in dropped]}"
        )
        return kept

    @staticmethod
    def merge_fact_metadata(
        kept_fact: Dict[str, Any],
        discarded_facts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        語意去重判定「單純重複」、多條事實合併為一條時的算術規則。

        這些數值不可信任模型計算，必須是程式碼固定規則：`hits` 取合併前各條的
        最大值（延續原本最受肯定的熱度，不重複計算造成灌水）、`created_at` 取最早值
        （這件事實際上存在得多久）、`last_used_at` 取最新值。文字與 embedding 沿用
        `kept_fact`（去重呼叫方已依「資訊最完整」原則選出）。
        """
        all_facts = [kept_fact] + list(discarded_facts)
        now = int(time.time())
        return {
            "text": kept_fact["text"],
            "hits": max(f.get("hits", 1) for f in all_facts),
            "created_at": min((f.get("created_at", now) for f in all_facts), default=now),
            "last_used_at": max((f.get("last_used_at", 0) for f in all_facts), default=now),
            "embedding": kept_fact.get("embedding"),
            "embedding_model": kept_fact.get("embedding_model", "")
        }

    @staticmethod
    def resolve_fact_conflict(facts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        當語意去重判定一組事實「同主題但語意矛盾」（而非單純重複）時，
        **不採用模型的保留/捨棄結果**，改用決定性規則保留「最後被使用／建立的一條」，
        與 `merge_facts()` 既有的否定推翻邏輯（以新事實推翻舊事實）精神一致——
        矛盾情境下不能讓模型自己決定該信哪一句，那正是本系統一路在移除的猜測。
        """
        return max(facts, key=lambda f: (f.get("last_used_at", 0), f.get("created_at", 0)))

    @staticmethod
    def cosine_similarity(vec_a: Optional[List[float]], vec_b: Optional[List[float]]) -> float:
        """計算兩向量的餘弦相似度，任一向量缺失、長度不符或為零向量一律回傳 0（視為不相似）"""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (norm_a * norm_b)

    @staticmethod
    def group_facts(
        facts: List[Dict[str, Any]],
        threshold: float = FACTS_DEDUP_SIMILARITY_THRESHOLD
    ) -> List[List[Dict[str, Any]]]:
        """
        機制 B 的決定性分群：對全部帶 embedding 的 facts 做 pairwise cosine similarity，
        相似度 >= threshold 的兩兩相連，取連通分量成群（union-find）。

        範圍涵蓋全部 facts，刻意不限定冷門尾巴——同一件事因措辭不同各自累積 hits、
        兩邊都沒被判定為重複的情況，同樣可能發生在熱門事實身上。只回傳 size >= 2
        的群組；沒有相似對象的事實不需要去重，不會出現在回傳結果中。
        """
        candidates = [f for f in facts if f.get("embedding")]
        n = len(candidates)
        if n < 2:
            return []

        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[ry] = rx

        for i in range(n):
            for j in range(i + 1, n):
                sim = MemoryManager.cosine_similarity(candidates[i]["embedding"], candidates[j]["embedding"])
                if sim >= threshold:
                    union(i, j)

        groups: Dict[int, List[Dict[str, Any]]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(candidates[i])

        return [g for g in groups.values() if len(g) >= 2]

    @staticmethod
    def get_facts_missing_embedding(
        facts: List[Dict[str, Any]],
        current_model: str
    ) -> List[Dict[str, Any]]:
        """
        篩出尚無 embedding、或 embedding 是用舊模型算的 facts，供語意去重批次補算。
        換 embedding 模型後，舊向量與新向量空間不相容，需整批判定過期並重算。
        """
        return [f for f in facts if not f.get("embedding") or f.get("embedding_model") != current_model]

    @staticmethod
    def merge_facts(
        current_facts_raw: Any,
        incoming_facts_raw: Any,
        remove_facts_raw: Any,
        reaffirm_bonus: int = FACTS_EXTRACTION_REAFFIRM_BONUS,
        max_total: int = FACTS_MAX_STORED_PER_USER
    ) -> List[Dict[str, Any]]:
        """
        合併與更新事實清單（含更正刪除、否定推翻與提煉重複確認加權）：
        1. 依 remove_facts_raw 剔除被推翻的舊事實。
        2. 新事實與既有事實字面高度相似時，再比對兩者的否定極性：
           - 極性相同 → 視為重複確認，hits += reaffirm_bonus。
           - 極性相反 → 視為矛盾，以新事實**取代**舊事實（熱度歸 1，不加權）。
        3. 若為全新事實，追加至末尾。
        4. 最後套用 `evict_stale_facts()`，確保回傳結果數量永遠不超過 max_total——
           這是保證 facts 有界的唯一入口（見該函式說明）。
        """
        cur_facts = MemoryManager.normalize_facts(current_facts_raw)
        now = int(time.time())

        # 1. 處理需要精準剔除的舊事實
        remove_clean = [
            str(rf).strip().lower()
            for rf in remove_facts_raw
            if str(rf).strip()
        ] if isinstance(remove_facts_raw, list) else []

        if remove_clean:
            filtered_cur_facts = []
            applied_terms: Set[str] = set()
            for f in cur_facts:
                hit = next(
                    (rf for rf in remove_clean if MemoryManager.should_remove_fact(f["text"], rf)),
                    None
                )
                if hit is None:
                    filtered_cur_facts.append(f)
                else:
                    applied_terms.add(hit)
                    logger.info(f"🗑️ [事實移除] 依刪除詞「{hit}」移除事實：{f['text']!r}")

            # 未命中任何事實的刪除詞多半是因為引述過於籠統而被門檻擋下，
            # 記錄下來以便追查「為什麼這次更正沒有生效」。
            for rf in remove_clean:
                if rf not in applied_terms:
                    logger.debug(
                        f"🗑️ [刪除詞未套用]「{rf}」未達最低引述門檻或找不到對應事實，已略過"
                    )
        else:
            filtered_cur_facts = list(cur_facts)

        # 2. 處理新提取的事實
        incoming_clean = [
            str(f).strip() for f in incoming_facts_raw if str(f).strip()
        ] if isinstance(incoming_facts_raw, list) else []

        merged_facts = list(filtered_cur_facts)

        for new_f in incoming_clean:
            # 以「去掉否定詞後的主題」配對，讓極性相反的同主題事實也能互相認得
            new_key = MemoryManager.topic_key(new_f)
            matched_fact = next(
                (
                    f for f in merged_facts
                    if MemoryManager._is_same_topic(MemoryManager.topic_key(f["text"]), new_key)
                ),
                None
            )

            if matched_fact:
                # 字面相似時，再比對否定極性以區分「重複確認」與「推翻更正」
                if MemoryManager.has_negation(matched_fact["text"]) != MemoryManager.has_negation(new_f):
                    # 極性相反 → 新事實推翻舊事實，就地取代（保留原本的排序位置）
                    logger.info(
                        f"✏️ [事實更正] 偵測到否定推翻，以新事實取代舊事實："
                        f"{matched_fact['text']!r} -> {new_f!r}"
                    )
                    merged_facts[merged_facts.index(matched_fact)] = {
                        "text": new_f,
                        "hits": 1,
                        "created_at": now,
                        "last_used_at": now
                    }
                else:
                    # 極性相同 → 提煉重複確認加權 hits += reaffirm_bonus
                    matched_fact["hits"] = matched_fact.get("hits", 1) + reaffirm_bonus
                    matched_fact["last_used_at"] = now
            else:
                merged_facts.append({
                    "text": new_f,
                    "hits": 1,
                    "created_at": now,
                    "last_used_at": now,
                    "embedding": None,
                    "embedding_model": ""
                })

        return MemoryManager.evict_stale_facts(merged_facts, max_total=max_total)

    # 互動印象的三個結構化標籤（見 ai/prompts.py 的提煉 prompt 範例）
    _NOTES_SECTION_LABELS: Tuple[Tuple[str, str], ...] = (
        ("core", "核心性格"),
        ("social", "社交關係"),
        ("recent", "近期動態")
    )

    @staticmethod
    def parse_interaction_notes(text: str) -> Dict[str, str]:
        """
        解析互動印象的三個結構化標籤（【核心性格】【社交關係】【近期動態】），
        缺少的標籤回傳空字串。供 `merge_interaction_notes()` 逐段比對用。
        """
        text = text or ""
        sections: Dict[str, str] = {key: "" for key, _ in MemoryManager._NOTES_SECTION_LABELS}
        all_labels = "|".join(re.escape(label) for _, label in MemoryManager._NOTES_SECTION_LABELS)

        for key, label in MemoryManager._NOTES_SECTION_LABELS:
            pattern = rf'【{re.escape(label)}】(.*?)(?=【(?:{all_labels})】|$)'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                sections[key] = match.group(1).strip()

        return sections

    @staticmethod
    def merge_interaction_notes(
        current_notes: str,
        incoming_notes: str,
        shrink_ratio: float = INTERACTION_NOTES_SHRINK_RATIO
    ) -> str:
        """
        互動印象的保護合併：三個標籤各自獨立判斷是否接受覆蓋，而非整包全有全無。

        模型每次提煉整段輸出 interaction_notes，先前沒有任何保護——一次輸出格式跑掉
        或忘記帶入舊人設，累積最久、最難重建的【核心性格】就會永久消失（facts 有聯集/
        熱度/否定推翻三層保護，interaction_notes 原本完全沒有）。這裡加兩道防線：

        1. 【格式完整性】：新輸出若缺任一標籤，視為異常輸出（截斷/格式跑掉），
           整份拒絕，保留舊 notes。
        2. 【疑似遺失偵測】：【核心性格】【社交關係】屬於「累積演進」性質，不該大幅
           萎縮；若新段落字數低於舊版的 shrink_ratio，視為疑似遺失，該段落保留舊版，
           但其他段落（尤其【近期動態】——本來就該每次滾動更新）仍正常採用新內容。

        是否要保留一版快照（供人工查看/還原）由呼叫端自行比對回傳值與 current_notes
        是否不同來決定，這裡只負責合併判斷本身。
        """
        incoming = (incoming_notes or "").strip()
        current = (current_notes or "").strip()

        if not incoming:
            return current

        new_sections = MemoryManager.parse_interaction_notes(incoming)
        if not all(new_sections.values()):
            logger.warning(
                f"⚠️ [互動印象格式異常] 新輸出缺少必要標籤，本次覆蓋已拒絕：{incoming[:80]}…"
            )
            return current

        if not current:
            return incoming

        cur_sections = MemoryManager.parse_interaction_notes(current)
        if not all(cur_sections.values()):
            # 舊資料本身格式不完整（例如尚未結構化的舊版資料），沒有比對基準，直接採用新輸出
            return incoming

        final_sections = dict(new_sections)
        for key, label in MemoryManager._NOTES_SECTION_LABELS:
            if key == "recent":
                continue  # 近期動態本來就該每次滾動更新，不設萎縮保護

            old_len = len(cur_sections[key])
            new_len = len(new_sections[key])
            if old_len > 0 and new_len < old_len * shrink_ratio:
                final_sections[key] = cur_sections[key]
                logger.warning(
                    f"⚠️ [互動印象疑似遺失] 【{label}】新段落字數（{new_len}）低於舊版"
                    f"（{old_len}）的 {int(shrink_ratio * 100)}%，已保留舊版內容"
                )

        return "\n".join(
            f"【{label}】{final_sections[key]}" for key, label in MemoryManager._NOTES_SECTION_LABELS
        )

    @staticmethod
    async def restore_interaction_notes(user_id: str) -> Tuple[bool, str]:
        """
        將指定使用者的互動印象還原為上一版快照，與目前版本**互換**（而非單向覆蓋覆寫）。

        設計成互換而非單向還原，是刻意讓這個操作天然可逆——重複執行會在兩版之間
        來回切換，誤操作也能立刻復原，不需要額外的「復原復原」機制。只保留一版快照
        （非完整歷史），因此只能挽救最近一次覆蓋，這是刻意的取捨（見 §「互動印象保護」
        設計說明）。

        僅供 CLI（`main.py`）呼叫，刻意不開放成 Discord 指令：這個操作直接覆寫另一位
        使用者的長期人設資料，安全邊界應該落在能操作主機／啟動程式的人，而非任何
        Discord 伺服器管理員。
        """
        uid = str(user_id)
        result: Dict[str, Any] = {"ok": False, "reason": ""}

        def _mutator(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not profile:
                result["reason"] = "找不到該使用者的畫像記錄"
                return None

            prev = str(profile.get("interaction_notes_prev", "") or "")
            if not prev.strip():
                result["reason"] = "沒有可還原的上一版快照"
                return None

            result["ok"] = True
            result["reason"] = "已還原至上一版互動印象"
            return {
                "user_name": profile.get("user_name", "用戶"),
                "facts": profile.get("facts", []),
                "interaction_notes": prev,
                "interaction_notes_prev": profile.get("interaction_notes", ""),
                "interaction_notes_prev_at": int(time.time()),
                "favorability": profile.get("favorability"),
                "relationship_tier": profile.get("relationship_tier"),
                "daily_favorability_gain": profile.get("daily_favorability_gain"),
                "last_gain_date": profile.get("last_gain_date")
            }

        await MemoryManager.apply_profile_update(uid, _mutator)
        return result["ok"], result["reason"]

    @staticmethod
    def extract_keywords(text: str) -> Set[str]:
        """從文本中提取有語意的關鍵字集合（英文實詞 + 中文 2/3-gram 滑動切片）"""
        if not text:
            return set()
        clean = re.sub(r'<@!?\d+>|https?://\S+', '', text.lower())
        keywords: Set[str] = set()

        # 提取英文/數字實詞
        for word in re.findall(r'[a-z0-9_\-\+]{2,}', clean):
            if word not in STOPWORDS:
                keywords.add(word)

        # 提取純中文字串並進行滑動切片
        chinese_text = "".join(re.findall(r'[\u4e00-\u9fa5]+', clean))
        n = len(chinese_text)
        for i in range(n):
            if i + 2 <= n:
                token2 = chinese_text[i:i+2]
                if token2 not in STOPWORDS:
                    keywords.add(token2)
            if i + 3 <= n:
                token3 = chinese_text[i:i+3]
                if token3 not in STOPWORDS:
                    keywords.add(token3)

        return keywords

    @staticmethod
    def build_search_blob(text: str) -> str:
        """
        將訊息內容轉為 FTS5 可正確切分的 n-gram 檢索字串（供寫入 messages_fts 索引）。

        SQLite FTS5 預設的 unicode61 分詞器會把一整串連續中文視為「單一 token」，
        導致「通宵」這類詞永遠無法命中（只有查詢完整原句才會匹配）。因此改由應用層
        先切好詞、以空白分隔後再交給 FTS5 索引。

        索引側與查詢側共用同一個 extract_keywords()，確保兩邊切詞規則永遠對稱，
        這是本機制能成立的根本前提。
        """
        return " ".join(sorted(MemoryManager.extract_keywords(text)))

    @staticmethod
    def filter_facts_three_tracks(
        facts_data: List[Any],
        query_text: str = "",
        max_total: int = FACTS_SPEAKER_MAX_TOTAL,
        heat_limit: int = FACTS_SPEAKER_HEAT_LIMIT,
        recent_limit: int = FACTS_SPEAKER_RECENT_LIMIT
    ) -> Tuple[List[str], List[str]]:
        """
        三軌混合事實檢索：
        1. 軌道 1 (Heat): 熱度最高事實 Top-heat_limit
        2. 軌道 3 (Recent): 最新近況事實 Top-recent_limit
        3. 軌道 2 (Topic RAG): 關鍵字匹配最高事實 Top-(max_total - heat - recent)
        4. 聯集合併去重並控制在 max_total 條以內
        回傳: (注入 Prompt 的事實字串列表, 本次被 RAG 命中需增加熱度的事實列表)
        """
        if not facts_data:
            return [], []

        normalized = MemoryManager.normalize_facts(facts_data)
        if not normalized:
            return [], []

        if len(normalized) <= max_total:
            return [f["text"] for f in normalized], []

        # 軌道 1：核心高頻 (按 hits 倒序)
        by_heat = sorted(normalized, key=lambda x: x.get("hits", 1), reverse=True)
        heat_facts = [f["text"] for f in by_heat[:heat_limit]]

        # 軌道 3：最新近況 (按 created_at 倒序)
        by_recent = sorted(normalized, key=lambda x: x.get("created_at", 0), reverse=True)
        recent_facts = [f["text"] for f in by_recent[:recent_limit]]

        # 軌道 2：話題關聯 RAG 檢索
        rag_hit_texts: List[str] = []
        rag_scored: List[Tuple[str, int]] = []
        keywords = MemoryManager.extract_keywords(query_text) if query_text else set()

        if keywords:
            for f in normalized:
                f_text = f["text"]
                f_keywords = MemoryManager.extract_keywords(f_text)
                overlap = keywords.intersection(f_keywords)
                score = len(overlap)
                # 若包含完整關鍵字子字串額外加分
                for kw in keywords:
                    if kw in f_text.lower():
                        score += 2
                if score > 0:
                    rag_scored.append((f_text, score))
                    rag_hit_texts.append(f_text)

            rag_scored.sort(key=lambda x: x[1], reverse=True)

        target_rag_count = max(0, max_total - len(heat_facts) - len(recent_facts))
        rag_facts = [f_text for f_text, _ in rag_scored[:target_rag_count]]

        # 合併三軌（保留優先級並去重）
        merged: List[str] = []
        for text in (heat_facts + rag_facts + recent_facts):
            if text not in merged:
                merged.append(text)
            if len(merged) >= max_total:
                break

        # 若未達到 max_total，從最新事實往前補足
        if len(merged) < max_total:
            for f in reversed(normalized):
                t = f["text"]
                if t not in merged:
                    merged.append(t)
                if len(merged) >= max_total:
                    break

        return merged, rag_hit_texts

    @staticmethod
    async def record_fact_hits(
        user_id: str,
        hit_texts: List[str],
        cooldown_seconds: int = FACTS_RAG_HIT_COOLDOWN_SECONDS,
        hit_bonus: int = FACTS_RAG_HIT_BONUS
    ) -> None:
        """非同步記錄事實被 RAG 命中（帶冷卻時間防刷，透過每使用者鎖序列化避免併發覆寫）"""
        if not user_id or not hit_texts:
            return

        hit_set = {t.strip().lower() for t in hit_texts if t.strip()}
        if not hit_set:
            return

        changed_flag = {"changed": False}

        def _mutator(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not profile:
                return None

            normalized = MemoryManager.normalize_facts(profile.get("facts", []))
            now = int(time.time())

            for f in normalized:
                if f["text"].strip().lower() in hit_set:
                    last_used = f.get("last_used_at", 0)
                    if now - last_used >= cooldown_seconds or f.get("hits", 0) == 0:
                        f["hits"] = f.get("hits", 0) + hit_bonus
                        f["last_used_at"] = now
                        changed_flag["changed"] = True

            if not changed_flag["changed"]:
                return None

            return {
                "user_name": profile.get("user_name", "用戶"),
                "facts": normalized,
                "interaction_notes": profile.get("interaction_notes", ""),
                "favorability": profile.get("favorability"),
                "relationship_tier": profile.get("relationship_tier"),
                "daily_favorability_gain": profile.get("daily_favorability_gain"),
                "last_gain_date": profile.get("last_gain_date")
            }

        try:
            await MemoryManager.apply_profile_update(str(user_id), _mutator)
            if changed_flag["changed"]:
                logger.debug(f"🔥 [事實 RAG 命中加權] 用戶 ID:{user_id} 命中事實: {hit_texts}")
        except Exception as e:
            logger.warning(f"記錄事實命中熱度失敗 (User: {user_id}): {e}")

    @staticmethod
    async def save_message(
        message_id: str,
        channel_id: str,
        user_id: str,
        user_name: str,
        content: str,
        has_image: bool = False,
        is_bot: bool = False,
        timestamp: Optional[int] = None,
        extracted: bool = False
    ) -> None:
        """儲存單則訊息至 messages 與全文檢索表 messages_fts"""
        if timestamp is None:
            timestamp = int(time.time())

        async with get_db_connection() as db:
            await db.execute("""
            INSERT OR REPLACE INTO messages (
                message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, extracted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(message_id),
                str(channel_id),
                str(user_id),
                str(user_name),
                str(content).strip(),
                1 if has_image else 0,
                1 if is_bot else 0,
                int(timestamp),
                1 if extracted else 0
            ))

            # FTS 索引存的是經 n-gram 切詞後的檢索字串而非原文；顯示時一律 JOIN 回
            # messages.content 取原文，因此不影響可讀性（見 build_search_blob 說明）。
            await db.execute("""
            INSERT OR REPLACE INTO messages_fts (rowid, content, user_name)
            SELECT rowid, ?, user_name FROM messages WHERE message_id = ?
            """, (MemoryManager.build_search_blob(str(content)), str(message_id)))

            await db.commit()

    @staticmethod
    async def get_short_term_context(channel_id: str, limit: int = SHORT_TERM_HISTORY_LIMIT) -> List[Dict[str, Any]]:
        """取得指定頻道最近 N 則即時上下文訊息（按時間正序排列）"""
        async with get_db_connection() as db:
            async with db.execute("""
            SELECT message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp
            FROM messages
            WHERE channel_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """, (str(channel_id), limit)) as cursor:
                rows = await cursor.fetchall()
                results = [dict(row) for row in rows]
                results.reverse()
                return results

    @staticmethod
    async def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
        """取得單一用戶的畫像設定檔（包含結構化 facts）"""
        async with get_db_connection() as db:
            async with db.execute("""
            SELECT user_id, user_name, facts, aliases, interaction_notes, interaction_notes_prev,
                   interaction_notes_prev_at, favorability, relationship_tier, daily_favorability_gain,
                   last_gain_date, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """, (str(user_id),)) as cursor:
                row = await cursor.fetchone()
                if row:
                    data = dict(row)
                    raw_facts = data.get("facts", "[]")
                    try:
                        parsed = json.loads(raw_facts) if isinstance(raw_facts, str) else raw_facts
                    except Exception:
                        parsed = []
                    data["facts"] = MemoryManager.normalize_facts(parsed)
                    data["aliases"] = MemoryManager.normalize_aliases(data.get("aliases"))
                    return data
                return None

    @staticmethod
    async def get_user_profiles_batch(user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批次取得多名用戶的長期畫像字典 {user_id: profile_dict}"""
        if not user_ids:
            return {}

        clean_uids = [str(uid) for uid in set(user_ids) if uid]
        placeholders = ",".join(["?"] * len(clean_uids))

        async with get_db_connection() as db:
            async with db.execute(f"""
            SELECT user_id, user_name, facts, aliases, interaction_notes, interaction_notes_prev,
                   interaction_notes_prev_at, favorability, relationship_tier, daily_favorability_gain,
                   last_gain_date, updated_at
            FROM user_profiles
            WHERE user_id IN ({placeholders})
            """, tuple(clean_uids)) as cursor:
                rows = await cursor.fetchall()
                res = {}
                for row in rows:
                    data = dict(row)
                    raw_facts = data.get("facts", "[]")
                    try:
                        parsed = json.loads(raw_facts) if isinstance(raw_facts, str) else raw_facts
                    except Exception:
                        parsed = []
                    data["facts"] = MemoryManager.normalize_facts(parsed)
                    data["aliases"] = MemoryManager.normalize_aliases(data.get("aliases"))
                    res[str(data["user_id"])] = data
                return res

    @staticmethod
    async def get_user_ids_with_min_facts(min_count: int) -> List[str]:
        """
        取得事實數達到門檻的使用者 ID 清單，供語意去重批次篩選掃描範圍，
        避免對事實數過少（不可能形成任何分群）的使用者也全量處理。
        """
        async with get_db_connection() as db:
            async with db.execute("SELECT user_id, facts FROM user_profiles") as cursor:
                rows = await cursor.fetchall()

        result = []
        for row in rows:
            facts = MemoryManager.normalize_facts(row["facts"])
            if len(facts) >= min_count:
                result.append(str(row["user_id"]))
        return result

    # 已回報過的同名暱稱，避免每則訊息都重複警告（僅在碰撞名單變動時才輸出）
    _reported_ambiguous_names: Set[str] = set()

    @staticmethod
    def normalize_aliases(aliases_raw: Any) -> List[Dict[str, Any]]:
        """將任意格式的別名資料統一為標準字典列表（含來源記錄）"""
        if not aliases_raw:
            return []

        if isinstance(aliases_raw, str):
            try:
                aliases_raw = json.loads(aliases_raw)
            except Exception:
                return []

        if not isinstance(aliases_raw, list):
            return []

        now = int(time.time())
        normalized: List[Dict[str, Any]] = []
        for item in aliases_raw:
            if isinstance(item, dict) and str(item.get("alias", "")).strip():
                normalized.append({
                    "alias": str(item["alias"]).strip(),
                    "source": str(item.get("source", "unknown")),
                    "by": item.get("by") or [],
                    "channel_id": str(item.get("channel_id", "")),
                    "message_id": str(item.get("message_id", "")),
                    "at": int(item.get("at", now))
                })
            elif isinstance(item, str) and item.strip():
                normalized.append({
                    "alias": item.strip(), "source": "unknown",
                    "by": [], "channel_id": "", "message_id": "", "at": now
                })
        return normalized

    @staticmethod
    def alias_texts(aliases_raw: Any) -> List[str]:
        """取出別名的純文字清單"""
        return [a["alias"] for a in MemoryManager.normalize_aliases(aliases_raw)]

    @staticmethod
    async def _load_name_index() -> Tuple[Dict[str, Set[str]], Dict[str, List[Dict[str, Any]]]]:
        """
        載入「名稱 -> user_id 集合」索引，同時涵蓋 Discord 顯示名稱與別名。

        回傳 (name_to_uids, aliases_by_uid)。兩者共用同一次查詢，避免重複讀庫。
        """
        async with get_db_connection() as db:
            async with db.execute("SELECT user_id, user_name, aliases FROM user_profiles") as cursor:
                rows = await cursor.fetchall()

        name_to_uids: Dict[str, Set[str]] = {}
        aliases_by_uid: Dict[str, List[Dict[str, Any]]] = {}

        for r in rows:
            uid = str(r["user_id"])
            uname = str(r["user_name"]).strip().lower()
            if uname:
                name_to_uids.setdefault(uname, set()).add(uid)

            alias_list = MemoryManager.normalize_aliases(r["aliases"])
            aliases_by_uid[uid] = alias_list
            for a in alias_list:
                key = a["alias"].strip().lower()
                if key:
                    name_to_uids.setdefault(key, set()).add(uid)

        return name_to_uids, aliases_by_uid

    @staticmethod
    async def get_user_aliases(user_id: str) -> List[Dict[str, Any]]:
        """取得某位使用者的別名清單（含來源記錄）"""
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT aliases FROM user_profiles WHERE user_id = ?", (str(user_id),)
            ) as cursor:
                row = await cursor.fetchone()
        return MemoryManager.normalize_aliases(row["aliases"]) if row else []

    @staticmethod
    async def add_alias(
        user_id: str,
        alias: str,
        source: str = "command",
        by: Optional[List[str]] = None,
        channel_id: str = "",
        message_id: str = "",
        max_aliases: int = MAX_ALIASES_PER_USER
    ) -> Tuple[bool, str]:
        """
        為使用者新增一個別名。回傳 (是否成功, 原因說明)。

        會執行以下校驗，任一不通過即拒絕：
        1. 通過 `is_matchable_name()`（非停用詞、長度足夠、非過短英數）
        2. 不與任何既有的 user_name 或別名碰撞——這道擋掉冒名
        3. 不與該使用者自己的顯示名稱重複（多餘）
        4. 未超過每人數量上限（達上限時拒絕新增，不自動淘汰既有別名）

        **不檢查「歸屬對象是否在本次對話上下文中」**——那是呼叫端的責任：
        提煉路徑以 `allowed_uids` 白名單把關，指令路徑以 Discord 的呼叫者身分把關。
        """
        clean = str(alias or "").strip()
        alias_key = clean.lower()
        uid = str(user_id)

        if not MemoryManager.is_matchable_name(alias_key):
            return False, f"「{clean}」不適合作為別名（太短、是常見詞、或英數過短）"

        lock = await MemoryManager._get_user_lock(uid)
        async with lock:
            profile = await MemoryManager.get_user_profile(uid)
            if not profile:
                return False, "該使用者尚無畫像記錄，無法設定別名"

            if alias_key == str(profile.get("user_name", "")).strip().lower():
                return False, f"「{clean}」與該使用者目前的顯示名稱相同，不需要另設別名"

            name_to_uids, _ = await MemoryManager._load_name_index()
            owners = name_to_uids.get(alias_key, set())
            if owners - {uid}:
                return False, f"「{clean}」已被其他使用者使用（顯示名稱或別名），無法重複設定"

            current = MemoryManager.normalize_aliases(profile.get("aliases"))
            if any(a["alias"].strip().lower() == alias_key for a in current):
                return False, f"「{clean}」已經是這位使用者的別名了"

            if len(current) >= max_aliases:
                return False, f"已達別名數量上限（{max_aliases} 個），請先移除不用的別名"

            current.append({
                "alias": clean,
                "source": source,
                "by": [str(b) for b in (by or [])],
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "at": int(time.time())
            })
            await MemoryManager._write_aliases(uid, current)

        logger.info(
            f"🏷️ [別名新增] 使用者 [{profile.get('user_name')} ({uid})] 新增別名「{clean}」"
            f"（來源: {source}, 提出者: {by or '—'}, 訊息: {message_id or '—'}）"
        )
        return True, f"已將「{clean}」設定為別名"

    @staticmethod
    async def remove_alias(user_id: str, alias: str) -> Tuple[bool, str]:
        """移除使用者的某個別名。回傳 (是否成功, 原因說明)。"""
        alias_key = str(alias or "").strip().lower()
        uid = str(user_id)

        lock = await MemoryManager._get_user_lock(uid)
        async with lock:
            current = await MemoryManager.get_user_aliases(uid)
            kept = [a for a in current if a["alias"].strip().lower() != alias_key]
            if len(kept) == len(current):
                return False, f"找不到別名「{alias}」"
            await MemoryManager._write_aliases(uid, kept)

        logger.info(f"🏷️ [別名移除] 使用者 ID:{uid} 移除別名「{alias}」")
        return True, f"已移除別名「{alias}」"

    @staticmethod
    async def _write_aliases(user_id: str, aliases: List[Dict[str, Any]]) -> None:
        """寫回別名清單（僅更新 aliases 欄位，不碰畫像的其他部分）"""
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE user_profiles SET aliases = ? WHERE user_id = ?",
                (json.dumps(aliases, ensure_ascii=False), str(user_id))
            )
            await db.commit()

    @staticmethod
    async def get_known_users_map() -> Dict[str, str]:
        """
        取得「暱稱小寫 → user_id」映射表，**僅包含唯一對應的暱稱**。

        若同一個暱稱對應到多位使用者（Discord 允許重複顯示名稱，或有人改名撞到別人
        的名字），整組排除而非任選一個保留。原本的寫法是後者覆蓋前者，而 SELECT 沒有
        ORDER BY，留下哪一筆其實是不確定的——同一句話在不同時候可能命中不同的人。

        排除後行為變成確定性的：要嘛找對人，要嘛找不到，不會找錯人。同名者仍可透過
        Discord 原生 @提及被精準識別（見 resolve_mentioned_user_ids 的維度 A）。

        涵蓋範圍同時包含 Discord 顯示名稱與**別名**，兩者適用完全相同的碰撞規則——
        別名撞到他人的顯示名稱（或別名）時一樣會被整組排除。
        """
        name_to_uids, _ = await MemoryManager._load_name_index()

        unique_map = {
            uname: next(iter(uids))
            for uname, uids in name_to_uids.items()
            if len(uids) == 1
        }

        ambiguous = {uname for uname, uids in name_to_uids.items() if len(uids) > 1}
        if ambiguous:
            newly_seen = ambiguous - MemoryManager._reported_ambiguous_names
            if newly_seen:
                MemoryManager._reported_ambiguous_names |= newly_seen
                logger.warning(
                    f"⚠️ [暱稱同名碰撞] 以下暱稱對應到多位使用者，已停用其名稱比對以免認錯人："
                    f"{sorted(newly_seen)}。這些使用者仍可透過 @提及被正確識別。"
                )

        return unique_map

    @staticmethod
    def is_matchable_name(uname: str) -> bool:
        """
        判斷一個暱稱是否適合拿來做「文字比對找人」。

        排除掉誤命中率過高、不具鑑別度的名稱：
        - 少於 2 個字：幾乎必然出現在任意訊息中。
        - 落在停用詞清單內（如「今天」「可以」）：每句話都可能命中。
        - 純 ASCII 且少於 3 字（如 "ab"）：極易成為其他英文單字的一部分。

        被排除的名稱仍可透過 Discord 原生 @提及被精準識別，不會完全找不到人。
        """
        if not uname or len(uname) < 2:
            return False
        if uname in STOPWORDS:
            return False
        if uname.isascii() and len(uname) < 3:
            return False
        return True

    @staticmethod
    def name_appears_in(uname: str, content_lower: str) -> bool:
        """
        判斷暱稱是否出現在文本中。

        英數暱稱有明確的詞邊界可用，因此以邊界比對避免 "test" 命中 "latest"、
        "contest" 這類子字串誤判。

        中文暱稱沒有詞邊界可依循，只能維持子字串比對——「小美」仍會命中「小美食」。
        這是中文分詞的固有限制，在不引入分詞詞典的前提下無法可靠解決，因此刻意
        不加啟發式規則硬猜（誤殺「桶子今天…」這類正常命中的代價更高）。
        """
        if not uname:
            return False
        if uname.isascii():
            pattern = rf'(?<![a-z0-9_]){re.escape(uname)}(?![a-z0-9_])'
            return re.search(pattern, content_lower) is not None
        return uname in content_lower

    @staticmethod
    async def resolve_mentioned_user_ids(
        content: str,
        exclude_uids: Optional[Set[str]] = None,
        explicit_mentions: Optional[List[Dict[str, str]]] = None
    ) -> List[str]:
        """
        從文本解析出「被談到的人」的 user_id：維度 A（Discord @提及）＋ 維度 B（暱稱文字比對）。

        回覆端的畫像檢索與提煉端的白名單共用此函式，確保兩邊對「誰算是參與了這段對話」
        的定義一致。先前兩處各自實作，導致監聽頻道的提煉白名單只認得有發言的人，
        被提及但沒發言者的特徵會被白名單拒絕寫入（跨使用者歸屬失效）。

        explicit_mentions：由呼叫端從 `discord.Message.mentions` 取得的權威提及清單，
        格式為 [{"user_id": ..., "user_name": ...}]。**這是維度 A 的正確來源**——訊息
        內容存的是 `clean_content`，Discord 已把 `<@123>` 轉寫成 `@顯示名稱`，因此對內容
        做 `<@!?(\\d+)>` 正則永遠不會命中。正則僅保留為 fallback，處理少數確實含原始
        標記的輸入（例如使用者在 Slash 指令參數中自行打出的提及語法）。

        回傳順序即優先序：@提及最精準排最前，名稱命中則以較長（較具鑑別度）者優先，
        讓上層截斷配額時保留較可信的人選。
        """
        excluded = {str(u) for u in (exclude_uids or set())}
        found: List[str] = []

        # 維度 A-1：Discord 權威提及清單（正確來源）
        for item in (explicit_mentions or []):
            mid = str(item.get("user_id", "")).strip()
            if mid and mid not in excluded and mid not in found:
                found.append(mid)

        # 維度 A-2：原始 <@id> 標記（fallback，僅少數輸入會帶有）
        for mid in re.findall(r'<@!?(\d+)>', content or ""):
            if mid not in excluded and mid not in found:
                found.append(mid)

        # 維度 B：名稱文字比對
        known_name_map = await MemoryManager.get_known_users_map()
        content_lower = (content or "").lower()

        matched: List[Tuple[str, str]] = []  # (暱稱, user_id)
        for uname, uid in known_name_map.items():
            if uid in excluded or uid in found:
                continue
            if MemoryManager.is_matchable_name(uname) and MemoryManager.name_appears_in(uname, content_lower):
                matched.append((uname, uid))

        # 若某個命中的暱稱是另一個命中暱稱的子字串（例如「小美」與「小美美」同時命中
        # 「小美美」），只保留較長者，避免把短名稱的主人一起誤拉進來。
        filtered = [
            (uname, uid) for uname, uid in matched
            if not any(other != uname and uname in other for other, _ in matched)
        ]

        # 較長的暱稱鑑別度較高，排在前面
        for uname, uid in sorted(filtered, key=lambda x: len(x[0]), reverse=True):
            if uid not in found:
                found.append(uid)

        return found

    @staticmethod
    async def resolve_multi_user_profiles(
        current_user_id: str,
        content: str,
        short_term_history: Optional[List[Dict[str, Any]]] = None,
        max_others: int = 4,
        explicit_mentions: Optional[List[Dict[str, str]]] = None,
        voice_member_ids: Optional[List[str]] = None
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        多人多維畫像檢索解析器。四個來源依優先序合併後截斷至 max_others：

            A. @提及        ← resolve_mentioned_user_ids
            B. 名稱／別名   ← resolve_mentioned_user_ids
            D. 語音頻道在場者（voice_member_ids）
            C. 近期文字發言者（short_term_history）

        維度 D 排在 C 之前，是因為「此刻和發言人同在一個語音頻道」比「15 則訊息前
        在文字頻道講過話」更能代表在場。

        ⚠️ **本函式只影響「讀取」——它決定哪些人的畫像會被放進 prompt，
        絕不決定「事實可以被寫給誰」。** 提煉端的白名單走的是
        `resolve_mentioned_user_ids()`（只含 A+B），刻意不經過這裡：
        「某人剛好在語音頻道／剛好講過話」不構成「可以把事實永久記到他頭上」的理由。
        若日後有人想把兩邊「統一」成同一個函式，會讓維度 C 與 D 取得寫入權限，
        使誤判從「一次回覆變差」升級為「永久記錯人」。
        """
        current_profile = await MemoryManager.get_user_profile(current_user_id)

        # 維度 A + B（與提煉端白名單共用同一份解析邏輯）
        target_other_uids: List[str] = await MemoryManager.resolve_mentioned_user_ids(
            content,
            exclude_uids={str(current_user_id)},
            explicit_mentions=explicit_mentions
        )

        # 維度 D：語音頻道在場者（唯讀，見上方 docstring）
        for uid in (voice_member_ids or []):
            uid = str(uid)
            if uid and uid != str(current_user_id) and uid not in target_other_uids:
                target_other_uids.append(uid)

        # 維度 C：近期頻道發言者
        if short_term_history:
            for msg in reversed(short_term_history):
                uid = str(msg.get("user_id", ""))
                if uid and uid != str(current_user_id) and not msg.get("is_bot", False):
                    if uid not in target_other_uids:
                        target_other_uids.append(uid)
                if len(target_other_uids) >= max_others:
                    break

        target_other_uids = target_other_uids[:max_others]

        other_profiles_dict = await MemoryManager.get_user_profiles_batch(target_other_uids)
        other_profiles = [
            other_profiles_dict[uid] for uid in target_other_uids if uid in other_profiles_dict
        ]

        return current_profile, other_profiles

    @staticmethod
    async def update_user_profile(
        user_id: str,
        user_name: str,
        facts: Optional[List[Any]] = None,
        interaction_notes: Optional[str] = None,
        interaction_notes_prev: Optional[str] = None,
        interaction_notes_prev_at: Optional[int] = None,
        favorability: Optional[int] = None,
        relationship_tier: Optional[str] = None,
        daily_favorability_gain: Optional[int] = None,
        last_gain_date: Optional[str] = None
    ) -> None:
        """
        更新或建立特定用戶畫像設定檔。

        interaction_notes_prev / interaction_notes_prev_at：互動印象保護機制的一版快照
        （被 interaction_notes 取代前的完整內容 + 時間戳），由呼叫端（見
        MemoryManager.merge_interaction_notes）決定何時更新，這裡只負責原樣寫入。
        """
        current = await MemoryManager.get_user_profile(user_id)

        if current:
            new_facts = MemoryManager.normalize_facts(facts) if facts is not None else current["facts"]
            new_notes = interaction_notes if interaction_notes is not None else current["interaction_notes"]
            new_notes_prev = interaction_notes_prev if interaction_notes_prev is not None else current.get("interaction_notes_prev", "")
            new_notes_prev_at = interaction_notes_prev_at if interaction_notes_prev_at is not None else current.get("interaction_notes_prev_at", 0)
            new_fav = favorability if favorability is not None else current["favorability"]
            new_tier = relationship_tier if relationship_tier is not None else current["relationship_tier"]
            new_daily_gain = daily_favorability_gain if daily_favorability_gain is not None else current["daily_favorability_gain"]
            new_gain_date = last_gain_date if last_gain_date is not None else current["last_gain_date"]
        else:
            new_facts = MemoryManager.normalize_facts(facts) if facts is not None else []
            new_notes = interaction_notes or ""
            new_notes_prev = interaction_notes_prev or ""
            new_notes_prev_at = interaction_notes_prev_at or 0
            new_fav = favorability if favorability is not None else DEFAULT_FAVORABILITY
            new_tier = relationship_tier or MemoryManager.compute_relationship_tier(new_fav)
            new_daily_gain = daily_favorability_gain if daily_favorability_gain is not None else 0
            new_gain_date = last_gain_date or datetime.now().strftime("%Y-%m-%d")

        facts_json = json.dumps(new_facts, ensure_ascii=False)

        async with get_db_connection() as db:
            await db.execute("""
            INSERT INTO user_profiles (
                user_id, user_name, facts, interaction_notes, interaction_notes_prev,
                interaction_notes_prev_at, favorability, relationship_tier,
                daily_favorability_gain, last_gain_date, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = excluded.user_name,
                facts = excluded.facts,
                interaction_notes = excluded.interaction_notes,
                interaction_notes_prev = excluded.interaction_notes_prev,
                interaction_notes_prev_at = excluded.interaction_notes_prev_at,
                favorability = excluded.favorability,
                relationship_tier = excluded.relationship_tier,
                daily_favorability_gain = excluded.daily_favorability_gain,
                last_gain_date = excluded.last_gain_date,
                updated_at = CURRENT_TIMESTAMP
            """, (
                str(user_id),
                str(user_name),
                facts_json,
                str(new_notes).strip(),
                str(new_notes_prev).strip(),
                int(new_notes_prev_at),
                int(new_fav),
                str(new_tier),
                int(new_daily_gain),
                str(new_gain_date)
            ))
            await db.commit()

    @staticmethod
    async def get_unextracted_messages(channel_id: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        """取得尚未提煉特徵的歷史訊息清單"""
        async with get_db_connection() as db:
            if channel_id:
                query = """
                SELECT message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp
                FROM messages
                WHERE extracted = 0 AND is_bot = 0 AND channel_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """
                params = (str(channel_id), limit)
            else:
                query = """
                SELECT message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp
                FROM messages
                WHERE extracted = 0 AND is_bot = 0
                ORDER BY timestamp ASC
                LIMIT ?
                """
                params = (limit,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # 註：原有的 get_unextracted_messages_by_user() 已隨 JIT 提煉機制一併移除。
    # 它缺少頻道過濾，會把當前對話頻道剛存入的訊息也一起撈走，導致同一批訊息被
    # JIT 與收尾提煉各處理一次（好感度雙倍、事實熱度灌水、API 成本雙倍）。
    # 現在殘留的未提煉訊息一律由 MemoryExtractor.sweep_unextracted() 依頻道分組處理。

    @staticmethod
    async def mark_messages_extracted(message_ids: List[str]) -> None:
        """批次將訊息標記為已提煉 (extracted = 1)"""
        if not message_ids:
            return

        clean_ids = [str(mid) for mid in message_ids if mid]
        placeholders = ",".join(["?"] * len(clean_ids))

        async with get_db_connection() as db:
            await db.execute(f"""
            UPDATE messages
            SET extracted = 1
            WHERE message_id IN ({placeholders})
            """, tuple(clean_ids))
            await db.commit()

    @staticmethod
    async def recall_deep_history(
        query_text: str,
        exclude_message_ids: Optional[List[str]] = None,
        limit: int = HISTORY_RECALL_LIMIT,
        min_score: int = HISTORY_RECALL_MIN_SCORE,
        max_query_tokens: int = HISTORY_RECALL_MAX_QUERY_TOKENS
    ) -> List[Dict[str, Any]]:
        """
        跨頻道深度歷史回憶（FTS5 全文檢索 + 相關性門檻過濾）。

        查詢側與索引側共用 extract_keywords() 切詞，確保中文二字詞（通宵、拉麵、鍵盤）
        能正常命中。FTS5 只負責粗篩候選，最終相關性由「命中的不同關鍵字數」在應用層
        判定，語意明確且不依賴 bm25 的黑箱分數。
        """
        keywords = MemoryManager.extract_keywords(query_text)
        if not keywords:
            return []

        # 依長度排序取前 N 個（3-gram 較具鑑別度），超過上限的短關鍵字直接捨棄。
        # 多人群聊 (Burst) 的合併查詢串可能產生數百個關鍵字，未設限會撞上 SQLite
        # 運算式深度上限。
        tokens = sorted(keywords, key=len, reverse=True)[:max_query_tokens]
        fts_match_query = " OR ".join(f'"{token}"' for token in tokens)
        exclude_ids = set(exclude_message_ids or [])

        async with get_db_connection() as db:
            async with db.execute("""
            SELECT m.message_id, m.channel_id, m.user_id, m.user_name, m.content, m.has_image, m.timestamp
            FROM messages_fts fts
            JOIN messages m ON fts.rowid = m.rowid
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """, (fts_match_query, max(limit * 5, limit))) as cursor:
                rows = await cursor.fetchall()

        # 以原文重新切詞取交集，並依「最長共同詞彙的字數」判定相關性。
        # 用最長詞長而非命中數量作為門檻，是為了讓門檻值有直觀語意：min_score=2 即
        # 「至少共享一個二字詞」。若改用命中數量，單一個二字詞只會得 1 分而被門檻 2
        # 擋掉，等於又讓中文最常見的二字詞無法召回。
        scored: List[Tuple[int, int, int, Dict[str, Any]]] = []
        for row in rows:
            r_dict = dict(row)
            if str(r_dict["message_id"]) in exclude_ids:
                continue

            matched = keywords & MemoryManager.extract_keywords(str(r_dict.get("content", "")))
            if not matched:
                continue

            longest_match = max(len(t) for t in matched)
            if longest_match < min_score:
                continue

            scored.append((longest_match, len(matched), int(r_dict.get("timestamp", 0)), r_dict))

        # 先比最長共同詞彙，再比命中詞彙數量，最後取較新的訊息
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return [item[3] for item in scored[:limit]]
