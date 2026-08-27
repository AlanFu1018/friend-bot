import time
from typing import List, Dict, Any, Optional
from src.friend_bot.memory.db import get_db_connection
from src.friend_bot.core.logger import get_logger

logger = get_logger("alarm_manager")

class AlarmManager:
    """定時鬧鐘提醒資料庫管理器（專門負責鬧鐘提醒資料表 CRUD）"""

    @staticmethod
    async def create_alarm(
        channel_id: str,
        user_id: str,
        user_name: str,
        target_timestamp: int,
        target_time_str: str,
        content: str
    ) -> int:
        """建立一筆定時提醒鬧鐘"""
        async with get_db_connection() as db:
            cursor = await db.execute("""
            INSERT INTO alarms (
                channel_id, user_id, user_name, target_timestamp, target_time_str, content, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (
                str(channel_id),
                str(user_id),
                str(user_name),
                int(target_timestamp),
                str(target_time_str),
                str(content).strip()
            ))
            await db.commit()
            alarm_id = cursor.lastrowid
            logger.info(f"⏰ [鬧鐘建立] ID:{alarm_id} 用戶:{user_name}({user_id}) 時間:{target_time_str} 內容:「{content}」")
            return alarm_id

    @staticmethod
    async def get_due_alarms(current_timestamp: Optional[int] = None) -> List[Dict[str, Any]]:
        """取得所有已到達觸發時間且未觸發的鬧鐘"""
        if current_timestamp is None:
            current_timestamp = int(time.time())

        async with get_db_connection() as db:
            async with db.execute("""
            SELECT id, channel_id, user_id, user_name, target_timestamp, target_time_str, content, status, created_at
            FROM alarms
            WHERE status = 'pending' AND target_timestamp <= ?
            ORDER BY target_timestamp ASC
            """, (current_timestamp,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @staticmethod
    async def mark_alarm_triggered(alarm_id: int) -> None:
        """將鬧鐘標記為已觸發 (triggered)"""
        async with get_db_connection() as db:
            await db.execute("""
            UPDATE alarms
            SET status = 'triggered'
            WHERE id = ?
            """, (alarm_id,))
            await db.commit()
            logger.debug(f"⏰ 鬧鐘 ID:{alarm_id} 狀態已更新為 triggered")

    @staticmethod
    async def get_pending_alarms(user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """查詢特定用戶或全部用戶待觸發的鬧鐘清單"""
        now_ts = int(time.time())
        async with get_db_connection() as db:
            if user_id:
                query = """
                SELECT id, channel_id, user_id, user_name, target_timestamp, target_time_str, content, status, created_at
                FROM alarms
                WHERE user_id = ? AND status = 'pending' AND target_timestamp >= ?
                ORDER BY target_timestamp ASC
                LIMIT ?
                """
                params = (str(user_id), now_ts - 3600, limit)
            else:
                query = """
                SELECT id, channel_id, user_id, user_name, target_timestamp, target_time_str, content, status, created_at
                FROM alarms
                WHERE status = 'pending' AND target_timestamp >= ?
                ORDER BY target_timestamp ASC
                LIMIT ?
                """
                params = (now_ts - 3600, limit)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @staticmethod
    async def cancel_alarm(alarm_id: int, user_id: Optional[str] = None) -> bool:
        """取消待觸發的鬧鐘"""
        async with get_db_connection() as db:
            if user_id:
                cursor = await db.execute("""
                UPDATE alarms
                SET status = 'canceled'
                WHERE id = ? AND user_id = ? AND status = 'pending'
                """, (alarm_id, str(user_id)))
            else:
                cursor = await db.execute("""
                UPDATE alarms
                SET status = 'canceled'
                WHERE id = ? AND status = 'pending'
                """, (alarm_id,))
            await db.commit()
            success = cursor.rowcount > 0
            if success:
                logger.info(f"⏰ 鬧鐘 ID:{alarm_id} 已成功取消 (用戶: {user_id or 'admin'})")
            return success
