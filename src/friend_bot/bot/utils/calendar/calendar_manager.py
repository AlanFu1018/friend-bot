import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from src.friend_bot.memory.db import get_db_connection
from src.friend_bot.core.logger import get_logger

logger = get_logger("calendar_manager")

class CalendarManager:
    """行事曆排程與 Webhook 日程管理資料庫模組"""

    @staticmethod
    async def create_event(
        channel_id: str,
        user_id: str,
        user_name: str,
        target_timestamp: int,
        target_date: str,
        target_time: str,
        target_time_str: str,
        content: str,
        webhook_url: str = ""
    ) -> int:
        """建立一筆新的行事曆排程事件"""
        async with get_db_connection() as db:
            cursor = await db.execute("""
            INSERT INTO calendar_events (
                channel_id, user_id, user_name, target_timestamp, 
                target_date, target_time, target_time_str, content, webhook_url, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                str(channel_id),
                str(user_id),
                str(user_name),
                int(target_timestamp),
                str(target_date),
                str(target_time),
                str(target_time_str),
                str(content).strip(),
                str(webhook_url or "").strip()
            ))
            await db.commit()
            event_id = cursor.lastrowid
            logger.info(f"📅 [行事曆建立] ID:{event_id} 用戶:{user_name}({user_id}) 日期:{target_date} 時間:{target_time} 內容:「{content}」 Webhook:{bool(webhook_url)}")
            return event_id

    @staticmethod
    async def get_due_events(current_timestamp: Optional[int] = None) -> List[Dict[str, Any]]:
        """取得所有已到達觸發時間且狀態為 pending 的行事曆事件"""
        if current_timestamp is None:
            current_timestamp = int(time.time())

        async with get_db_connection() as db:
            async with db.execute("""
            SELECT id, channel_id, user_id, user_name, target_timestamp, 
                   target_date, target_time, target_time_str, content, webhook_url, status, created_at
            FROM calendar_events
            WHERE status = 'pending' AND target_timestamp <= ?
            ORDER BY target_timestamp ASC
            """, (current_timestamp,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @staticmethod
    async def mark_event_triggered(event_id: int) -> None:
        """將行事曆事件標記為已觸發 (triggered)"""
        async with get_db_connection() as db:
            await db.execute("""
            UPDATE calendar_events
            SET status = 'triggered'
            WHERE id = ?
            """, (event_id,))
            await db.commit()
            logger.debug(f"📅 行事曆 ID:{event_id} 狀態已更新為 triggered")

    @staticmethod
    async def get_user_events_by_date(user_id: str, date_str: str) -> List[Dict[str, Any]]:
        """
        查詢特定使用者在某個日期的所有排程（包括 pending 與 triggered）
        date_str 格式例如 '2026-08-27'
        """
        async with get_db_connection() as db:
            async with db.execute("""
            SELECT id, channel_id, user_id, user_name, target_timestamp, 
                   target_date, target_time, target_time_str, content, webhook_url, status, created_at
            FROM calendar_events
            WHERE user_id = ? AND target_date = ? AND status != 'canceled'
            ORDER BY target_timestamp ASC
            """, (str(user_id), str(date_str))) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @staticmethod
    async def get_upcoming_events(user_id: Optional[str] = None, days: int = 7, limit: int = 15) -> List[Dict[str, Any]]:
        """查詢未來 N 天內的排程清單"""
        now_ts = int(time.time())
        max_ts = now_ts + days * 86400

        async with get_db_connection() as db:
            if user_id:
                query = """
                SELECT id, channel_id, user_id, user_name, target_timestamp, 
                       target_date, target_time, target_time_str, content, webhook_url, status, created_at
                FROM calendar_events
                WHERE user_id = ? AND status = 'pending' AND target_timestamp >= ? AND target_timestamp <= ?
                ORDER BY target_timestamp ASC
                LIMIT ?
                """
                params = (str(user_id), now_ts - 3600, max_ts, limit)
            else:
                query = """
                SELECT id, channel_id, user_id, user_name, target_timestamp, 
                       target_date, target_time, target_time_str, content, webhook_url, status, created_at
                FROM calendar_events
                WHERE status = 'pending' AND target_timestamp >= ? AND target_timestamp <= ?
                ORDER BY target_timestamp ASC
                LIMIT ?
                """
                params = (now_ts - 3600, max_ts, limit)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    @staticmethod
    async def cancel_event(event_id: int, user_id: Optional[str] = None) -> bool:
        """取消待觸發的行事曆排程"""
        async with get_db_connection() as db:
            if user_id:
                cursor = await db.execute("""
                UPDATE calendar_events
                SET status = 'canceled'
                WHERE id = ? AND user_id = ? AND status = 'pending'
                """, (event_id, str(user_id)))
            else:
                cursor = await db.execute("""
                UPDATE calendar_events
                SET status = 'canceled'
                WHERE id = ? AND status = 'pending'
                """, (event_id,))
            await db.commit()
            success = cursor.rowcount > 0
            if success:
                logger.info(f"📅 行事曆 ID:{event_id} 已成功取消 (用戶: {user_id or 'admin'})")
            return success

    @staticmethod
    async def get_user_schedule_summary(user_id: str) -> str:
        """將用戶近期所有排程整合為字串，供 Gemini 系統對話 Prompt 注入"""
        events = await CalendarManager.get_upcoming_events(user_id=user_id, days=14, limit=10)
        if not events:
            return ""

        lines = ["【用戶已登記的行事曆與排程 (Calendar Schedules)】:"]
        for e in events:
            date = e.get("target_date", "")
            time_part = e.get("target_time", "")
            content = e.get("content", "")
            status = "⏳待提醒" if e.get("status") == "pending" else "✅已觸發"
            lines.append(f"- [{date} {time_part}] {content} ({status})")

        return "\n".join(lines)
