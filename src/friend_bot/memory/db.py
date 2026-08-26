from pathlib import Path
from contextlib import asynccontextmanager
import aiosqlite
from src.friend_bot.core.config import DB_PATH
from src.friend_bot.core.logger import get_logger

logger = get_logger("database")

@asynccontextmanager
async def get_db_connection():
    """非同步資料庫連線上線器 (Async Context Manager)"""
    db_file = Path(DB_PATH)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_file)) as db:
        db.row_factory = aiosqlite.Row
        yield db

async def init_db():
    """初始化資料庫表架構與 FTS5 全文索引"""
    async with get_db_connection() as db:
        # 1. 永久儲存所有對話訊息的主表
        await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            content TEXT NOT NULL,
            has_image INTEGER DEFAULT 0,
            is_bot INTEGER DEFAULT 0,
            timestamp INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 索引優化：依頻道快速查詢近期訊息，依用戶快速查詢發言
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, id DESC);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);")

        # 2. 全文搜尋虛擬表 (FTS5) - 用於跨歷史深度話題回憶
        try:
            await db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                user_name,
                channel_id UNINDEXED,
                msg_id UNINDEXED
            );
            """)
        except Exception as e:
            logger.warning(f"FTS5 全文搜尋表建置提醒: {e}")

        # 3. 結構化用戶長期畫像與特徵表
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            user_name TEXT NOT NULL,
            facts TEXT DEFAULT '[]',
            interaction_notes TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await db.commit()
        logger.info(f"SQLite 資料庫結構就緒: {DB_PATH}")

async def clear_all_memory() -> None:
    """清空所有記憶（包含所有頻道歷史對話、FTS5 全文索引與所有用戶長期畫像）"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM messages;")
        try:
            await db.execute("DELETE FROM messages_fts;")
        except Exception:
            pass
        await db.execute("DELETE FROM user_profiles;")
        await db.commit()
    logger.info("🧹 已成功清空所有歷史訊息與用戶長期畫像！")

async def clear_history_only() -> None:
    """僅清空所有頻道的對話歷史與全文索引，保留用戶長期特徵畫像"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM messages;")
        try:
            await db.execute("DELETE FROM messages_fts;")
        except Exception:
            pass
        await db.commit()
    logger.info("🧹 已清空所有頻道對話歷史（用戶個人畫像仍保留）。")

async def clear_profiles_only() -> None:
    """僅清空用戶個人特徵畫像，保留對話歷史紀錄"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM user_profiles;")
        await db.commit()
    logger.info("🧹 已清空所有用戶長期特徵畫像（對話歷史仍保留）。")
