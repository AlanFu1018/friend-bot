import json
import re
from typing import List, Dict, Any, Optional, Tuple, Set
from .db import get_db_connection
from src.friend_bot.core.config import SHORT_TERM_HISTORY_LIMIT, HISTORY_RECALL_LIMIT
from src.friend_bot.core.logger import get_logger

logger = get_logger("memory")

class MemoryManager:
    """三層記憶管理器：負責儲存與檢索短期對話、長期畫像與跨頻道歷史回憶"""

    @staticmethod
    async def save_message(
        message_id: str,
        channel_id: str,
        user_id: str,
        user_name: str,
        content: str,
        has_image: bool = False,
        is_bot: bool = False,
        timestamp: int = 0,
        extracted: bool = False
    ) -> None:
        """永久儲存一筆訊息至 messages 主表與 FTS5 全文索引"""
        async with get_db_connection() as db:
            try:
                await db.execute("""
                INSERT OR IGNORE INTO messages (message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, extracted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(message_id),
                    str(channel_id),
                    str(user_id),
                    str(user_name),
                    content,
                    1 if has_image else 0,
                    1 if is_bot else 0,
                    timestamp,
                    1 if extracted else 0
                ))

                # 同步寫入 FTS5 全文搜尋表
                if content.strip():
                    await db.execute("""
                    INSERT INTO messages_fts (content, user_name, channel_id, msg_id)
                    VALUES (?, ?, ?, ?)
                    """, (
                        content,
                        str(user_name),
                        str(channel_id),
                        str(message_id)
                    ))

                await db.commit()
            except Exception as e:
                logger.error(f"儲存訊息失敗 (ID: {message_id}): {e}")

    @staticmethod
    async def get_short_term_context(channel_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """獲取指定頻道最近 N 筆對話（按時間先後由舊到新排列）"""
        if limit is None:
            limit = SHORT_TERM_HISTORY_LIMIT

        async with get_db_connection() as db:
            async with db.execute("""
            SELECT id, message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, extracted, created_at
            FROM messages
            WHERE channel_id = ?
            ORDER BY id DESC
            LIMIT ?
            """, (str(channel_id), limit)) as cursor:
                rows = await cursor.fetchall()
                # 轉為由舊到新排序
                result = [dict(row) for row in reversed(rows)]
                return result

    @staticmethod
    async def get_unextracted_messages(
        channel_id: Optional[str] = None,
        limit: int = 30
    ) -> List[Dict[str, Any]]:
        """獲取尚未進行畫像提煉的訊息清單（按時間順序由舊到新）"""
        async with get_db_connection() as db:
            if channel_id:
                query = """
                SELECT id, message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, created_at
                FROM messages
                WHERE channel_id = ? AND extracted = 0 AND is_bot = 0
                ORDER BY id ASC
                LIMIT ?
                """
                params = (str(channel_id), limit)
            else:
                query = """
                SELECT id, message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, created_at
                FROM messages
                WHERE extracted = 0 AND is_bot = 0
                ORDER BY id ASC
                LIMIT ?
                """
                params = (limit,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def get_unextracted_messages_by_user(
        user_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """獲取指定用戶在所有頻道中尚未被提煉的訊息（用於主頻道 JIT 按需即時提煉）"""
        async with get_db_connection() as db:
            async with db.execute("""
            SELECT id, message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, created_at
            FROM messages
            WHERE user_id = ? AND extracted = 0 AND is_bot = 0
            ORDER BY id ASC
            LIMIT ?
            """, (str(user_id), limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def mark_messages_extracted(message_ids: List[str]) -> None:
        """批次將訊息標記為已提煉 (extracted = 1)"""
        if not message_ids:
            return
        clean_ids = [str(mid).strip() for mid in message_ids if str(mid).strip()]
        if not clean_ids:
            return

        placeholders = ",".join("?" for _ in clean_ids)
        async with get_db_connection() as db:
            await db.execute(f"""
            UPDATE messages
            SET extracted = 1
            WHERE message_id IN ({placeholders})
            """, clean_ids)
            await db.commit()
            logger.debug(f"已標記 {len(clean_ids)} 則訊息為 extracted=1")

    @staticmethod
    async def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
        """讀取指定用戶的長期畫像（包含事實清單與互動印象）"""
        async with get_db_connection() as db:
            async with db.execute("""
            SELECT user_id, user_name, facts, interaction_notes, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """, (str(user_id),)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                data = dict(row)
                try:
                    data["facts"] = json.loads(data["facts"]) if data["facts"] else []
                except Exception:
                    data["facts"] = []
                return data

    @staticmethod
    async def get_user_profiles_batch(user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批次讀取多位用戶的長期畫像字典 {user_id: profile_dict}"""
        if not user_ids:
            return {}

        clean_ids = list(set(str(uid).strip() for uid in user_ids if uid and str(uid).strip()))
        if not clean_ids:
            return {}

        placeholders = ",".join("?" for _ in clean_ids)
        result = {}
        async with get_db_connection() as db:
            async with db.execute(f"""
            SELECT user_id, user_name, facts, interaction_notes, updated_at
            FROM user_profiles
            WHERE user_id IN ({placeholders})
            """, clean_ids) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    data = dict(row)
                    try:
                        data["facts"] = json.loads(data["facts"]) if data["facts"] else []
                    except Exception:
                        data["facts"] = []
                    result[data["user_id"]] = data
        return result

    @staticmethod
    async def get_known_users_map() -> Dict[str, str]:
        """獲取已知群友暱稱與 ID 的對照表 {user_name_lower: user_id}"""
        result = {}
        async with get_db_connection() as db:
            async with db.execute("""
            SELECT user_id, user_name
            FROM user_profiles
            """) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    name = str(row["user_name"]).strip().lower()
                    if name:
                        result[name] = str(row["user_id"])
        return result

    @staticmethod
    async def resolve_multi_user_profiles(
        current_user_id: str,
        content: str = "",
        explicit_mentioned_user_ids: Optional[List[str]] = None,
        short_term_history: Optional[List[Dict[str, Any]]] = None,
        max_others: int = 3
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        【混合式多維記憶載入機制 (A + B + C)】:
        - A: Discord 顯式提及 (@某人 或 回覆對象)
        - B: 純文字中的已知群友暱稱掃描
        - C: 近期短期對話中的活躍參與者
        
        回傳:
        (current_user_profile, other_user_profiles)
        """
        candidate_user_ids: List[str] = []
        seen_ids: Set[str] = {str(current_user_id)}

        # 1. 方案 A: 顯式提及的群友 ID
        if explicit_mentioned_user_ids:
            for uid in explicit_mentioned_user_ids:
                uid_str = str(uid).strip()
                if uid_str and uid_str not in seen_ids:
                    candidate_user_ids.append(uid_str)
                    seen_ids.add(uid_str)

        # 2. 方案 B: 純文字暱稱快速掃描
        if content:
            # 同步檢查字串中的 <@123456> 格式
            mention_regex_ids = re.findall(r'<@!?(\d+)>', content)
            for uid_str in mention_regex_ids:
                if uid_str not in seen_ids:
                    candidate_user_ids.append(uid_str)
                    seen_ids.add(uid_str)

            # 比對已知用戶庫中的名字 (長度 >= 2，避免單字誤判)
            known_map = await MemoryManager.get_known_users_map()
            content_lower = content.lower()
            for known_name, uid in known_map.items():
                if len(known_name) >= 2 and known_name in content_lower:
                    if uid not in seen_ids:
                        candidate_user_ids.append(uid)
                        seen_ids.add(uid)

        # 3. 方案 C: 近期短期對話中的活躍參與者
        if short_term_history:
            for msg in reversed(short_term_history):
                uid = str(msg.get("user_id", "")).strip()
                is_bot = bool(msg.get("is_bot", False))
                if uid and not is_bot and uid not in seen_ids:
                    candidate_user_ids.append(uid)
                    seen_ids.add(uid)
                    if len(candidate_user_ids) >= max_others + 2:
                        break

        # 截取最多 max_others 位其他群友
        target_other_ids = candidate_user_ids[:max_others]

        # 4. 批次向資料庫查詢畫像
        all_ids_to_fetch = [str(current_user_id)] + target_other_ids
        profiles_dict = await MemoryManager.get_user_profiles_batch(all_ids_to_fetch)

        current_user_profile = profiles_dict.get(str(current_user_id))
        other_user_profiles = [
            profiles_dict[uid] for uid in target_other_ids if uid in profiles_dict
        ]

        return current_user_profile, other_user_profiles

    @staticmethod
    async def update_user_profile(
        user_id: str,
        user_name: str,
        facts: List[str],
        interaction_notes: str = ""
    ) -> None:
        """更新或建立用戶長期畫像"""
        async with get_db_connection() as db:
            facts_json = json.dumps(facts, ensure_ascii=False)
            await db.execute("""
            INSERT INTO user_profiles (user_id, user_name, facts, interaction_notes, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = excluded.user_name,
                facts = excluded.facts,
                interaction_notes = CASE 
                    WHEN excluded.interaction_notes != '' THEN excluded.interaction_notes 
                    ELSE user_profiles.interaction_notes 
                END,
                updated_at = CURRENT_TIMESTAMP
            """, (
                str(user_id),
                str(user_name),
                facts_json,
                interaction_notes
            ))
            await db.commit()
            logger.debug(f"已更新用戶 [{user_name}] 的個人畫像 (事實數: {len(facts)})")

    @staticmethod
    async def recall_deep_history(
        query_text: str,
        exclude_message_ids: Optional[List[str]] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        利用 FTS5 全文搜尋從過去所有對話（跨頻道）檢索與目前話題最相關的歷史對話片段
        """
        if limit is None:
            limit = HISTORY_RECALL_LIMIT

        if not query_text or len(query_text.strip()) < 2:
            return []

        if exclude_message_ids is None:
            exclude_message_ids = []

        exclude_set = set(str(mid) for mid in exclude_message_ids)

        tokens = re.findall(r'[\w\u4e00-\u9fff]+', query_text)
        keywords = [t for t in tokens if len(t) >= 2]
        if not keywords:
            return []

        fts_query = " OR ".join(f'"{kw}"' for kw in keywords[:5])

        async with get_db_connection() as db:
            results = []
            try:
                async with db.execute("""
                SELECT m.id, m.message_id, m.channel_id, m.user_id, m.user_name, m.content, m.created_at
                FROM messages_fts f
                JOIN messages m ON f.msg_id = m.message_id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """, (fts_query, limit * 3)) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        row_dict = dict(row)
                        if row_dict["message_id"] not in exclude_set:
                            results.append(row_dict)
                            if len(results) >= limit:
                                break
            except Exception:
                for kw in keywords[:2]:
                    async with db.execute("""
                    SELECT id, message_id, channel_id, user_id, user_name, content, created_at
                    FROM messages
                    WHERE content LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """, (f"%{kw}%", limit * 2)) as cursor:
                        rows = await cursor.fetchall()
                        for row in rows:
                            row_dict = dict(row)
                            if row_dict["message_id"] not in exclude_set and row_dict not in results:
                                results.append(row_dict)
                                if len(results) >= limit:
                                    break
            return results
