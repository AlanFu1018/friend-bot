from src.friend_bot.core.mood import MoodTracker, MoodState
from src.friend_bot.core.logger import get_logger
from .db import get_db_connection

logger = get_logger("mood_store")


async def ensure_mood_loaded(channel_id: str) -> None:
    """若該頻道心情尚未載入記憶體快取，從資料庫讀入（每個頻道只讀一次）"""
    if not channel_id or MoodTracker.is_loaded(channel_id):
        return
    state = MoodState()
    try:
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT scores, updated_at, cause_user FROM channel_moods WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row:
            state = MoodState.from_row(row["scores"], row["updated_at"], row["cause_user"])
    except Exception as e:
        logger.warning(f"讀取頻道 {channel_id} 心情失敗，以平靜狀態開始: {e}")
    # 已被其他協程先載入並更新過時，不覆蓋
    if not MoodTracker.is_loaded(channel_id):
        MoodTracker.set_state(channel_id, state)


async def persist_mood(channel_id: str) -> None:
    """將該頻道目前的心情寫回資料庫"""
    state = MoodTracker.get_state(channel_id)
    if not channel_id or state is None:
        return
    try:
        async with get_db_connection() as db:
            await db.execute(
                """
                INSERT INTO channel_moods (channel_id, scores, cause_user, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    scores = excluded.scores,
                    cause_user = excluded.cause_user,
                    updated_at = excluded.updated_at
                """,
                (channel_id, state.to_json(), state.cause_user, state.updated_at)
            )
            await db.commit()
    except Exception as e:
        logger.warning(f"寫入頻道 {channel_id} 心情失敗: {e}")
