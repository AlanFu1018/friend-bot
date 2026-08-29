from pathlib import Path
from contextlib import asynccontextmanager
import aiosqlite
from src.friend_bot.core.config import DB_PATH, DEFAULT_FAVORABILITY
from src.friend_bot.core.logger import get_logger

logger = get_logger("database")

# FTS 索引 schema 版本（記錄於 SQLite 原生的 PRAGMA user_version）
# 1 = messages_fts 改存 n-gram 切詞後的檢索字串，並移除從未寫入過的 channel_id / msg_id 欄位
FTS_SCHEMA_VERSION = 1

# 重建索引時每批處理的訊息數量
_FTS_REBUILD_BATCH_SIZE = 500

@asynccontextmanager
async def get_db_connection():
    """非同步資料庫連線上線器 (Async Context Manager)"""
    db_file = Path(DB_PATH)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_file)) as db:
        db.row_factory = aiosqlite.Row
        # WAL 允許讀寫並行；busy_timeout 讓短暫鎖競爭自動重試而非直接拋出 "database is locked"
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
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
            extracted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 遷移檢查：若舊表沒有 extracted 欄位，動態補上
        try:
            cursor = await db.execute("PRAGMA table_info(messages);")
            columns = [row["name"] for row in await cursor.fetchall()]
            if "extracted" not in columns:
                await db.execute("ALTER TABLE messages ADD COLUMN extracted INTEGER DEFAULT 0;")
                logger.info("已為 messages 表新增 extracted 欄位")
        except Exception as e:
            logger.debug(f"messages 欄位檢查: {e}")

        # 索引優化
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, id DESC);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_unextracted ON messages(channel_id, extracted, id ASC);")

        # 2. 全文搜尋虛擬表 (FTS5)
        # content 欄位存的是經 n-gram 切詞後的檢索字串（見 MemoryManager.build_search_blob），
        # 而非訊息原文；顯示時一律 JOIN 回 messages 取原文。
        try:
            await db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                user_name
            );
            """)
        except Exception as e:
            logger.warning(f"FTS5 全文搜尋表建置提醒: {e}")

        # 3. 結構化用戶長期畫像、特徵與好感度表
        await db.execute(f"""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            user_name TEXT NOT NULL,
            facts TEXT DEFAULT '[]',
            interaction_notes TEXT DEFAULT '',
            favorability INTEGER DEFAULT {DEFAULT_FAVORABILITY},
            relationship_tier TEXT DEFAULT 'familiar',
            daily_favorability_gain INTEGER DEFAULT 0,
            last_gain_date TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 遷移檢查：若舊表沒有好感度相關欄位，動態補上
        try:
            cursor = await db.execute("PRAGMA table_info(user_profiles);")
            prof_cols = [row["name"] for row in await cursor.fetchall()]
            if "favorability" not in prof_cols:
                await db.execute(f"ALTER TABLE user_profiles ADD COLUMN favorability INTEGER DEFAULT {DEFAULT_FAVORABILITY};")
                logger.info("已為 user_profiles 表新增 favorability 欄位")
            if "relationship_tier" not in prof_cols:
                await db.execute("ALTER TABLE user_profiles ADD COLUMN relationship_tier TEXT DEFAULT 'familiar';")
                logger.info("已為 user_profiles 表新增 relationship_tier 欄位")
            if "daily_favorability_gain" not in prof_cols:
                await db.execute("ALTER TABLE user_profiles ADD COLUMN daily_favorability_gain INTEGER DEFAULT 0;")
                logger.info("已為 user_profiles 表新增 daily_favorability_gain 欄位")
            if "last_gain_date" not in prof_cols:
                await db.execute("ALTER TABLE user_profiles ADD COLUMN last_gain_date TEXT DEFAULT '';")
                logger.info("已為 user_profiles 表新增 last_gain_date 欄位")
        except Exception as e:
            logger.debug(f"user_profiles 欄位檢查: {e}")

        # 4. 紅莉栖行事曆排程與 Webhook 定時提醒表 (calendar_events)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            target_timestamp INTEGER NOT NULL,
            target_date TEXT NOT NULL,
            target_time TEXT NOT NULL,
            target_time_str TEXT NOT NULL,
            content TEXT NOT NULL,
            webhook_url TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cal_pending ON calendar_events(status, target_timestamp);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cal_user_date ON calendar_events(user_id, target_date);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cal_user_status ON calendar_events(user_id, status);")

        # 鬧鐘相容表
        await db.execute("""
        CREATE TABLE IF NOT EXISTS alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            target_timestamp INTEGER NOT NULL,
            target_time_str TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await db.commit()

        # 5. 檢查並在必要時重建 FTS 索引（見 _rebuild_fts_index 說明）
        await _rebuild_fts_index_if_needed(db)

        logger.info(f"SQLite 資料庫結構就緒: {DB_PATH}")

async def _rebuild_fts_index_if_needed(db) -> None:
    """
    偵測 FTS 索引是否為舊版格式，若是則從 messages 表全量重建。

    舊版索引直接存訊息原文，但 FTS5 預設的 unicode61 分詞器會把整串連續中文視為
    單一 token，導致中文查詢幾乎永遠無法命中。新版改存 n-gram 切詞後的檢索字串。

    messages 表是原始資料的唯一真實來源，messages_fts 只是其衍生索引，因此重建為
    無損操作；user_profiles（事實、好感度、互動印象）完全不受影響。
    以 PRAGMA user_version 作為版本標記，確保重建只會發生一次。
    """
    # 延遲匯入以避免與 memory_manager 形成循環相依
    from .memory_manager import MemoryManager

    try:
        async with db.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            current_version = int(row[0]) if row else 0
    except Exception as e:
        logger.warning(f"無法讀取 FTS schema 版本，略過索引重建: {e}")
        return

    if current_version >= FTS_SCHEMA_VERSION:
        return

    logger.info(
        f"偵測到舊版 FTS 索引格式 (version={current_version})，開始從 messages 表重建全文索引…"
    )

    try:
        await db.execute("DROP TABLE IF EXISTS messages_fts;")
        await db.execute("""
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content,
            user_name
        );
        """)

        total = 0
        last_rowid = 0
        while True:
            # 必須顯式取別名：messages 宣告了 id INTEGER PRIMARY KEY，SQLite 會把
            # SELECT rowid 的結果欄位命名為該別名 (id)，直接用 row["rowid"] 會取不到值。
            async with db.execute("""
            SELECT rowid AS rid, content, user_name
            FROM messages
            WHERE rowid > ?
            ORDER BY rowid ASC
            LIMIT ?
            """, (last_rowid, _FTS_REBUILD_BATCH_SIZE)) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                break

            await db.executemany(
                "INSERT INTO messages_fts (rowid, content, user_name) VALUES (?, ?, ?)",
                [
                    (
                        row["rid"],
                        MemoryManager.build_search_blob(str(row["content"] or "")),
                        str(row["user_name"] or "")
                    )
                    for row in rows
                ]
            )
            await db.commit()

            last_rowid = rows[-1]["rid"]
            total += len(rows)
            logger.info(f"  FTS 索引重建進度：已處理 {total} 則訊息…")

        await db.execute(f"PRAGMA user_version = {FTS_SCHEMA_VERSION};")
        await db.commit()
        logger.info(f"✅ FTS 全文索引重建完成，共重新索引 {total} 則訊息。")

    except Exception as e:
        logger.error(f"FTS 索引重建失敗: {e}", exc_info=True)

async def clear_all_memory() -> None:
    """清空所有記憶（包含所有頻道歷史對話、FTS5 全文索引、用戶畫像與行事曆）"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM messages;")
        try:
            await db.execute("DELETE FROM messages_fts;")
        except Exception:
            pass
        await db.execute("DELETE FROM user_profiles;")
        await db.execute("DELETE FROM calendar_events;")
        await db.execute("DELETE FROM alarms;")
        await db.commit()
    logger.info("🧹 已成功清空所有歷史訊息、用戶長期畫像與行事曆排程！")

async def clear_history_only() -> None:
    """僅清空所有頻道的對話歷史與全文索引，保留用戶長期特徵畫像與行事曆"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM messages;")
        try:
            await db.execute("DELETE FROM messages_fts;")
        except Exception:
            pass
        await db.commit()
    logger.info("🧹 已清空所有頻道對話歷史（用戶個人畫像與行事曆仍保留）。")

async def clear_profiles_only() -> None:
    """僅清空用戶個人特徵畫像，保留對話歷史與行事曆紀錄"""
    async with get_db_connection() as db:
        await db.execute("DELETE FROM user_profiles;")
        await db.commit()
    logger.info("🧹 已清空所有用戶長期特徵畫像（對話歷史與行事曆仍保留）。")
