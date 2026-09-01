import asyncio
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from src.friend_bot.memory.memory_manager import MemoryManager
from src.friend_bot.core.logger import get_logger
from .gemini_client import GeminiClient
from .facts_embedding import FactsEmbeddingClient
from src.friend_bot.core.config import (
    ENABLE_FACTS_DEDUP,
    FACTS_DEDUP_SIMILARITY_THRESHOLD,
    FACTS_DEDUP_CLUSTER_MAX_SIZE,
    FACTS_DEDUP_MAX_FACTS_PER_USER_PER_DAY,
    FACTS_DEDUP_SWEEP_INTERVAL_SECONDS,
)

logger = get_logger("facts_dedup")


class FactsDeduplicator:
    """
    背景語意去重批次器（機制 B）。

    與硬上限淘汰（機制 A，`MemoryManager.evict_stale_facts()`，在 `merge_facts()`
    寫入時同步執行）是兩個獨立機制：機制 A 保證 facts 數量任何時刻都有界；機制 B
    只是背景品質優化，透過 embedding 分群 + Gemini 判斷「是否為同一事實」，減少
    因措辭不同造成的假性膨脹，降低機制 A 需要淘汰真實事實的頻率——機制 B 本身
    不保證有界，去重比率不可控。

    分群範圍涵蓋全部事實（不分熱門冷門）：同一件事因措辭不同各自累積 hits、
    兩邊都沒被判定為重複的情況，同樣可能發生在熱門事實身上（若只挑冷門尾巴，
    這種熱門重複永遠不會被抓到）。
    """

    def __init__(
        self,
        gemini_client: Optional[GeminiClient] = None,
        embedding_client: Optional[FactsEmbeddingClient] = None
    ):
        self.gemini = gemini_client or GeminiClient()
        self.embedder = embedding_client or FactsEmbeddingClient()
        self._sweep_task: Optional[asyncio.Task] = None
        self._sweeping = False
        # 每人每日去重配額（process 內記憶體計數，重啟歸零）：{user_id: (日期, 今日已處理筆數)}
        self._daily_dedup_usage: Dict[str, Tuple[str, int]] = {}

    # ==================== 每日去重配額 ====================

    def _remaining_daily_quota(self, user_id: str) -> int:
        """回傳該使用者今天還能送進去重流程的事實筆數上限"""
        today = date.today().isoformat()
        last_date, used = self._daily_dedup_usage.get(user_id, (today, 0))
        if last_date != today:
            used = 0
        return max(0, FACTS_DEDUP_MAX_FACTS_PER_USER_PER_DAY - used)

    def _consume_daily_quota(self, user_id: str, count: int) -> None:
        """記錄該使用者今天已處理的去重筆數（跨日自動歸零重算）"""
        if count <= 0:
            return
        today = date.today().isoformat()
        last_date, used = self._daily_dedup_usage.get(user_id, (today, 0))
        if last_date != today:
            used = 0
        self._daily_dedup_usage[user_id] = (today, used + count)

    # ==================== 單一使用者去重 ====================

    async def dedupe_user(self, user_id: str) -> None:
        """對單一使用者的事實清單執行一輪語意去重，受每日去重配額限制"""
        if not ENABLE_FACTS_DEDUP:
            return

        profile = await MemoryManager.get_user_profile(user_id)
        if not profile:
            return

        facts = MemoryManager.normalize_facts(profile.get("facts", []))
        if len(facts) < 2:
            return

        quota = self._remaining_daily_quota(user_id)
        if quota <= 0:
            logger.debug(f"🧬 [去重跳過] 用戶 ID:{user_id} 今日去重配額已用盡，留到明天再處理")
            return
        if len(facts) > quota:
            facts = facts[:quota]
            if len(facts) < 2:
                return

        await self._backfill_embeddings(user_id, facts)

        clusters = MemoryManager.group_facts(facts, threshold=FACTS_DEDUP_SIMILARITY_THRESHOLD)
        self._consume_daily_quota(user_id, len(facts))
        if not clusters:
            return

        for cluster in clusters:
            await self._resolve_cluster(user_id, cluster[:FACTS_DEDUP_CLUSTER_MAX_SIZE])

    async def _backfill_embeddings(self, user_id: str, facts: List[Dict[str, Any]]) -> None:
        """
        補算缺 embedding（或模型版本過舊）的事實向量，並立即寫回快取供下次批次重複利用。

        平時只存文字，embedding 一律 lazy backfill——不能塞進 `merge_facts()`
        （純函式，不該做 API I/O），而是由這個背景批次補上。
        """
        missing = MemoryManager.get_facts_missing_embedding(facts, self.embedder.model)
        if not missing:
            return

        vectors = await self.embedder.embed_texts([f["text"] for f in missing])
        updated: Dict[str, List[float]] = {}
        for f, vec in zip(missing, vectors):
            if vec is not None:
                f["embedding"] = vec
                f["embedding_model"] = self.embedder.model
                updated[f["text"]] = vec

        if not updated:
            return

        def _mutator(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not profile:
                return None
            current = MemoryManager.normalize_facts(profile.get("facts", []))
            changed = False
            for f in current:
                if f["text"] in updated:
                    f["embedding"] = updated[f["text"]]
                    f["embedding_model"] = self.embedder.model
                    changed = True
            if not changed:
                return None
            return self._as_update_kwargs(profile, current)

        await MemoryManager.apply_profile_update(user_id, _mutator)

    async def _resolve_cluster(self, user_id: str, cluster: List[Dict[str, Any]]) -> None:
        """對單一候選群組呼叫 Gemini 判斷關係，並依結果套用重複合併或矛盾解決"""
        if len(cluster) < 2:
            return

        try:
            result = await self.gemini.facts_similar_check([f["text"] for f in cluster])
        except Exception as e:
            logger.warning(f"事實去重比對呼叫失敗，略過此群組: {e}")
            return

        relation = result.get("relation", "none")

        if relation == "duplicate":
            keep_text = result.get("keep", "").strip()
            kept = next((f for f in cluster if f["text"] == keep_text), None)
            if kept is None:
                # 模型沒有照抄候選原句，格式不符預期，整群保留不動（保守優先）
                logger.debug(f"🧬 [去重跳過] 模型回傳的 keep 不在候選原句中，略過此群組: {result}")
                return
            discarded = [f for f in cluster if f["text"] != kept["text"]]
            merged = MemoryManager.merge_fact_metadata(kept, discarded)
            await self._apply_resolution(user_id, [f["text"] for f in cluster], merged)
            logger.info(
                f"🧬 [事實去重] 用戶 ID:{user_id} 合併 {len(cluster)} 條重複事實 -> "
                f"「{merged['text']}」(hits={merged['hits']})"
            )

        elif relation == "conflict":
            # 矛盾：不採用模型的保留/捨棄結果，改用決定性規則（保留最後使用/建立者）
            kept = MemoryManager.resolve_fact_conflict(cluster)
            discarded_count = len(cluster) - 1
            await self._apply_resolution(user_id, [f["text"] for f in cluster], kept)
            logger.info(
                f"🧬 [事實矛盾解決] 用戶 ID:{user_id} 保留較新事實「{kept['text']}」，"
                f"捨棄 {discarded_count} 條矛盾舊事實"
            )
        # relation == "none"：不動作，群組內事實原樣保留

    async def _apply_resolution(
        self,
        user_id: str,
        cluster_texts: List[str],
        surviving_fact: Dict[str, Any]
    ) -> None:
        """
        以 cluster 內原句文字為鍵，將這群事實從清單中移除，換成單一存活的事實。

        寫回前透過 `apply_profile_update()` 的 per-user 鎖重新讀取最新 profile
        （而非沿用分析當下的快照），避免覆蓋掉分析期間並發提煉寫入的更新；
        若 cluster 中的事實已被並發更新動過而找不到完整比對，本次去重直接放棄，
        留到下次批次重試——最壞情況是暫緩生效，不會遺失資料。
        """
        remove_set = set(cluster_texts)

        def _mutator(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not profile:
                return None
            current = MemoryManager.normalize_facts(profile.get("facts", []))
            current_texts = {f["text"] for f in current}
            if not remove_set.issubset(current_texts):
                # cluster 中至少一條事實已被其他並發更新動過，這次不處理，留待下次重試
                return None
            remaining = [f for f in current if f["text"] not in remove_set]
            remaining.append(surviving_fact)
            return self._as_update_kwargs(profile, remaining)

        await MemoryManager.apply_profile_update(user_id, _mutator)

    @staticmethod
    def _as_update_kwargs(
        profile: Dict[str, Any],
        facts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """組裝 `update_user_profile()` 所需的關鍵字參數，僅變更 facts，其餘欄位原樣沿用"""
        return {
            "user_name": profile.get("user_name", "用戶"),
            "facts": facts,
            "interaction_notes": profile.get("interaction_notes", ""),
            "favorability": profile.get("favorability"),
            "relationship_tier": profile.get("relationship_tier"),
            "daily_favorability_gain": profile.get("daily_favorability_gain"),
            "last_gain_date": profile.get("last_gain_date")
        }

    # ==================== 背景週期批次 ====================

    async def sweep_dedupe_facts(self) -> int:
        """掃描事實數 >= 2 的使用者並逐一執行去重，回傳處理的使用者數"""
        if not ENABLE_FACTS_DEDUP:
            return 0

        try:
            user_ids = await MemoryManager.get_user_ids_with_min_facts(2)
        except Exception as e:
            logger.warning(f"去重批次查詢候選使用者失敗: {e}")
            return 0

        if not user_ids:
            return 0

        logger.info(f"🧬 [事實去重批次] 開始掃描 {len(user_ids)} 位候選使用者")
        for uid in user_ids:
            try:
                await self.dedupe_user(uid)
            except Exception as e:
                logger.warning(f"用戶 ID:{uid} 的事實去重執行失敗，略過此人待下次重試: {e}")
        return len(user_ids)

    def start_sweeper(self, interval_seconds: float = FACTS_DEDUP_SWEEP_INTERVAL_SECONDS) -> None:
        """啟動背景語意去重批次任務（間隔見 config.yaml 的 dedup_sweep_interval_seconds）"""
        if not ENABLE_FACTS_DEDUP or self._sweeping:
            return
        self._sweeping = True
        self._sweep_task = asyncio.create_task(self._sweep_loop(interval_seconds))
        logger.info(f"🧬 [事實去重批次] 服務已啟動（每 {int(interval_seconds)} 秒檢查一次）。")

    def stop_sweeper(self) -> None:
        """停止背景語意去重批次任務"""
        self._sweeping = False
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()

    async def _sweep_loop(self, interval_seconds: float) -> None:
        while self._sweeping:
            try:
                await asyncio.sleep(interval_seconds)
                await self.sweep_dedupe_facts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"🧬 [事實去重批次] 執行時發生異常: {e}", exc_info=True)
