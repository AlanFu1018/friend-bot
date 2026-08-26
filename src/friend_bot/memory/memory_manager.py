import json
import re
from typing import List, Dict, Any, Optional
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
        timestamp: int = 0
    ) -> None:
        """永久儲存一筆訊息至 messages 主表與 FTS5 全文索引"""
        async with get_db_connection() as db:
            try:
                await db.execute("""
                INSERT OR IGNORE INTO messages (message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(message_id),
                    str(channel_id),
                    str(user_id),
                    str(user_name),
                    content,
                    1 if has_image else 0,
                    1 if is_bot else 0,
                    timestamp
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
            SELECT id, message_id, channel_id, user_id, user_name, content, has_image, is_bot, timestamp, created_at
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

        # 清理關鍵字，過濾過短字詞
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', query_text)
        keywords = [t for t in tokens if len(t) >= 2]
        if not keywords:
            return []

        # 組合 FTS5 OR 查詢
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
                # 若 FTS 語法查詢失敗，Fallback 至 LIKE 模糊搜尋
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
