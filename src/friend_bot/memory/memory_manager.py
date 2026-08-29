import asyncio
import json
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
    FACTS_RAG_HIT_BONUS
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
                    normalized.append({
                        "text": str(item.get("text", "")).strip(),
                        "hits": int(item.get("hits", 1)),
                        "created_at": int(item.get("created_at", now)),
                        "last_used_at": int(item.get("last_used_at", now))
                    })
                elif isinstance(item, str) and item.strip():
                    normalized.append({
                        "text": item.strip(),
                        "hits": 1,
                        "created_at": now,
                        "last_used_at": now
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
                    "last_used_at": now
                })
        return normalized

    @staticmethod
    def to_fact_texts(facts_raw: Any) -> List[str]:
        """將任意格式（標準字典列表 / 舊格式純字串列表 / 混合格式）的 facts 統一轉為純文字字串列表"""
        if not facts_raw:
            return []
        return [f["text"] if isinstance(f, dict) else str(f) for f in facts_raw]

    @staticmethod
    def merge_facts(
        current_facts_raw: Any,
        incoming_facts_raw: Any,
        remove_facts_raw: Any,
        reaffirm_bonus: int = FACTS_EXTRACTION_REAFFIRM_BONUS
    ) -> List[Dict[str, Any]]:
        """
        合併與更新事實清單（含更正刪除與提煉重複確認加權）：
        1. 依 remove_facts_raw 剔除被推翻的舊事實。
        2. 若新提取的事實已存在，增加熱度權重 hits += reaffirm_bonus（提煉重複確認加權）。
        3. 若為全新事實，追加至末尾。
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
            filtered_cur_facts = [
                f for f in cur_facts
                if not any(rf in f["text"].lower() or f["text"].lower() in rf for rf in remove_clean)
            ]
        else:
            filtered_cur_facts = list(cur_facts)

        # 2. 處理新提取的事實
        incoming_clean = [
            str(f).strip() for f in incoming_facts_raw if str(f).strip()
        ] if isinstance(incoming_facts_raw, list) else []

        merged_facts = list(filtered_cur_facts)

        for new_f in incoming_clean:
            new_f_lower = new_f.lower()
            # 尋找是否已存在相同或高度相似的事實
            matched_fact = next(
                (f for f in merged_facts if f["text"].lower() == new_f_lower or new_f_lower in f["text"].lower() or f["text"].lower() in new_f_lower),
                None
            )
            if matched_fact:
                # 提煉重複確認加權 hits += reaffirm_bonus
                matched_fact["hits"] = matched_fact.get("hits", 1) + reaffirm_bonus
                matched_fact["last_used_at"] = now
            else:
                merged_facts.append({
                    "text": new_f,
                    "hits": 1,
                    "created_at": now,
                    "last_used_at": now
                })

        return merged_facts

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
            SELECT user_id, user_name, facts, interaction_notes, favorability, relationship_tier, daily_favorability_gain, last_gain_date, updated_at
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
            SELECT user_id, user_name, facts, interaction_notes, favorability, relationship_tier, daily_favorability_gain, last_gain_date, updated_at
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
                    res[str(data["user_id"])] = data
                return res

    @staticmethod
    async def get_known_users_map() -> Dict[str, str]:
        """取得所有已知用戶的名稱小寫與 user_id 映射表"""
        async with get_db_connection() as db:
            async with db.execute("SELECT user_id, user_name FROM user_profiles") as cursor:
                rows = await cursor.fetchall()
                name_map = {}
                for r in rows:
                    uname = str(r["user_name"]).strip().lower()
                    if uname:
                        name_map[uname] = str(r["user_id"])
                return name_map

    @staticmethod
    async def resolve_mentioned_user_ids(
        content: str,
        exclude_uids: Optional[Set[str]] = None
    ) -> List[str]:
        """
        從文本解析出「被談到的人」的 user_id：維度 A（Discord @提及）＋ 維度 B（暱稱文字比對）。

        回覆端的畫像檢索與提煉端的白名單共用此函式，確保兩邊對「誰算是參與了這段對話」
        的定義一致。先前兩處各自實作，導致監聽頻道的提煉白名單只認得有發言的人，
        被提及但沒發言者的特徵會被白名單拒絕寫入（跨使用者歸屬失效）。
        """
        excluded = {str(u) for u in (exclude_uids or set())}
        found: List[str] = []

        # 維度 A：Discord 原生 @提及
        for mid in re.findall(r'<@!?(\d+)>', content or ""):
            if mid not in excluded and mid not in found:
                found.append(mid)

        # 維度 B：名稱文字比對
        known_name_map = await MemoryManager.get_known_users_map()
        content_lower = (content or "").lower()
        for uname, uid in known_name_map.items():
            if uid not in excluded and uid not in found:
                if len(uname) >= 2 and uname in content_lower:
                    found.append(uid)

        return found

    @staticmethod
    async def resolve_multi_user_profiles(
        current_user_id: str,
        content: str,
        short_term_history: Optional[List[Dict[str, Any]]] = None,
        max_others: int = 4
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """多人多維畫像檢索解析器"""
        current_profile = await MemoryManager.get_user_profile(current_user_id)

        # 維度 A + B（與提煉端白名單共用同一份解析邏輯）
        target_other_uids: List[str] = await MemoryManager.resolve_mentioned_user_ids(
            content, exclude_uids={str(current_user_id)}
        )

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
        favorability: Optional[int] = None,
        relationship_tier: Optional[str] = None,
        daily_favorability_gain: Optional[int] = None,
        last_gain_date: Optional[str] = None
    ) -> None:
        """更新或建立特定用戶畫像設定檔"""
        current = await MemoryManager.get_user_profile(user_id)

        if current:
            new_facts = MemoryManager.normalize_facts(facts) if facts is not None else current["facts"]
            new_notes = interaction_notes if interaction_notes is not None else current["interaction_notes"]
            new_fav = favorability if favorability is not None else current["favorability"]
            new_tier = relationship_tier if relationship_tier is not None else current["relationship_tier"]
            new_daily_gain = daily_favorability_gain if daily_favorability_gain is not None else current["daily_favorability_gain"]
            new_gain_date = last_gain_date if last_gain_date is not None else current["last_gain_date"]
        else:
            new_facts = MemoryManager.normalize_facts(facts) if facts is not None else []
            new_notes = interaction_notes or ""
            new_fav = favorability if favorability is not None else DEFAULT_FAVORABILITY
            new_tier = relationship_tier or MemoryManager.compute_relationship_tier(new_fav)
            new_daily_gain = daily_favorability_gain if daily_favorability_gain is not None else 0
            new_gain_date = last_gain_date or datetime.now().strftime("%Y-%m-%d")

        facts_json = json.dumps(new_facts, ensure_ascii=False)

        async with get_db_connection() as db:
            await db.execute("""
            INSERT INTO user_profiles (
                user_id, user_name, facts, interaction_notes, favorability, relationship_tier,
                daily_favorability_gain, last_gain_date, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = excluded.user_name,
                facts = excluded.facts,
                interaction_notes = excluded.interaction_notes,
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
