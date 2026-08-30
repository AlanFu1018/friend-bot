import json
from pathlib import Path
from contextlib import asynccontextmanager
import aiosqlite
from src.friend_bot.core.config import DB_PATH, DEFAULT_FAVORABILITY
from src.friend_bot.core.logger import get_logger

logger = get_logger("database")

# 資料庫 schema 版本（記錄於 SQLite 原生的 PRAGMA user_version）
# 1 = messages_fts 改存 n-gram 切詞後的檢索字串，並移除從未寫入過的 channel_id / msg_id 欄位
# 2 = 修復重複提煉造成的資料污染：回填被改錯的 user_name、重置被灌水的事實熱度 hits
# 3 = 清除以「名字」而非 Discord 數字 ID 為主鍵的幽靈畫像（防注入白名單上線前的遺留）
SCHEMA_VERSION = 3
FTS_SCHEMA_VERSION = 1  # 保留舊名稱以相容既有引用

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
            aliases TEXT DEFAULT '[]',
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
            if "aliases" not in prof_cols:
                # 別名與 user_name 分開儲存：user_name 由 Discord 權威覆寫，
                # aliases 不受提煉碰觸，設了就保留。
                await db.execute("ALTER TABLE user_profiles ADD COLUMN aliases TEXT DEFAULT '[]';")
                logger.info("已為 user_profiles 表新增 aliases 欄位")
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

        # 5. 依 schema 版本執行必要的資料遷移
        await _run_migrations(db)

        logger.info(f"SQLite 資料庫結構就緒: {DB_PATH}")

async def _run_migrations(db) -> None:
    """
    依 PRAGMA user_version 逐階段執行資料遷移。

    每個階段都設計為可重複執行（idempotent），因此若中途失敗不會提升版本號，
    下次啟動會從頭重跑，不會留下半套狀態。
    """
    try:
        async with db.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
            current_version = int(row[0]) if row else 0
    except Exception as e:
        logger.warning(f"無法讀取資料庫 schema 版本，略過遷移: {e}")
        return

    if current_version >= SCHEMA_VERSION:
        return

    logger.info(f"偵測到舊版資料庫 (version={current_version} → {SCHEMA_VERSION})，開始執行遷移…")

    try:
        if current_version < 1:
            await _rebuild_fts_index(db)
        if current_version < 2:
            await _repair_extraction_pollution(db)
        if current_version < 3:
            await _cleanup_phantom_profiles(db)
    except Exception as e:
        logger.error(f"資料庫遷移失敗（版本維持 {current_version}，下次啟動將重試）: {e}", exc_info=True)
        return

    await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
    await db.commit()
    logger.info(f"✅ 資料庫遷移完成，schema 版本已更新為 {SCHEMA_VERSION}。")

async def _repair_extraction_pollution(db) -> None:
    """
    【遷移 v2】修復重複提煉造成的兩類資料污染。

    1. `user_name` 被改成別人的名字：舊版 `client.py` 的 JIT 迴圈對每位使用者都傳入
       「最後一則訊息發言者」的名字，該名字經提煉 prompt 流入模型輸出後被寫回畫像。
       `messages` 表保存了每則訊息當下由 Discord 提供的正確 `user_name`，取每位
       使用者最新一筆回填即可修復。

    2. 事實熱度 `hits` 被灌水：舊版每則訊息會被 JIT 與收尾提煉各提煉一次，第二次會被
       `merge_facts` 判定為「重複確認」而 hits += 3。污染程度依每條事實被提煉過幾次而
       異、無法精確回推，因此一律重置為 1，讓熱度從乾淨的基準重新累積。
    """
    from .memory_manager import MemoryManager

    # 1. 從 messages 回填權威 user_name
    await db.execute("""
    UPDATE user_profiles
    SET user_name = (
        SELECT m.user_name FROM messages m
        WHERE m.user_id = user_profiles.user_id AND m.is_bot = 0
        ORDER BY m.timestamp DESC, m.id DESC
        LIMIT 1
    )
    WHERE EXISTS (
        SELECT 1 FROM messages m
        WHERE m.user_id = user_profiles.user_id AND m.is_bot = 0
    )
    """)
    await db.commit()

    async with db.execute("SELECT changes();") as cursor:
        row = await cursor.fetchone()
        renamed = int(row[0]) if row else 0

    # 2. 重置被灌水的事實熱度
    async with db.execute("SELECT user_id, facts FROM user_profiles") as cursor:
        rows = await cursor.fetchall()

    reset_facts = 0
    for row in rows:
        facts = MemoryManager.normalize_facts(row["facts"])
        if not facts:
            continue
        for f in facts:
            f["hits"] = 1
        reset_facts += len(facts)
        await db.execute(
            "UPDATE user_profiles SET facts = ? WHERE user_id = ?",
            (json.dumps(facts, ensure_ascii=False), row["user_id"])
        )
    await db.commit()

    logger.info(
        f"🔧 [遷移 v2] 已從 messages 回填 {renamed} 筆畫像的 user_name，"
        f"並將 {reset_facts} 條事實的熱度重置為 1。"
    )

async def _cleanup_phantom_profiles(db) -> None:
    """
    【遷移 v3】清除以「名字」而非 Discord 數字 ID 為主鍵的幽靈畫像。

    成因：早期提煉時模型會把 `user_id` 欄位填成使用者名稱（而非 ID），舊版
    `_safe_apply_updates` 因為該值是非空字串就直接當成主鍵，於是建立了一筆
    `user_id = '代謝'` 這樣的畫像。這些資料早於防注入白名單（`allowed_uids`）上線，
    白名單啟用後就不再更新——特徵是 favorability 停在預設值、facts 遠少於同名真人。

    危害：`get_known_users_map()` 舊版遇到同名時是「後者覆蓋前者」，而 SELECT 沒有
    ORDER BY，因此該名字有時會解析到幾乎空白的幽靈畫像、有時解析到真人，造成
    「紅莉栖時而完全不記得某人」這種不定時的症狀。

    處理：若該名字恰好對應到唯一一筆真實（數字 ID）畫像，先把幽靈的 facts 併入
    該真人畫像（走 merge_facts，自動去重並套用否定推翻邏輯），再刪除幽靈列；
    對應不唯一時則直接刪除，不猜。

    此類資料已無法再產生：`allowed_uids` 全部來自 Discord（必為數字 ID），
    模型輸出的非數字 ID 不可能通過白名單校驗。
    """
    from .memory_manager import MemoryManager

    async with db.execute(
        "SELECT user_id, user_name, facts FROM user_profiles"
    ) as cursor:
        rows = [dict(r) for r in await cursor.fetchall()]

    phantoms = [r for r in rows if not str(r["user_id"]).strip().isdigit()]
    if not phantoms:
        return

    # 名字 -> 真實（數字 ID）畫像清單
    real_by_name: dict = {}
    for r in rows:
        uid = str(r["user_id"]).strip()
        if uid.isdigit():
            key = str(r["user_name"]).strip().lower()
            real_by_name.setdefault(key, []).append(r)

    merged_count = 0
    orphan_count = 0

    for ph in phantoms:
        ph_uid = str(ph["user_id"])
        name_key = str(ph["user_name"]).strip().lower()
        targets = real_by_name.get(name_key, [])

        if len(targets) == 1:
            target = targets[0]
            phantom_facts = MemoryManager.to_fact_texts(
                MemoryManager.normalize_facts(ph.get("facts"))
            )
            if phantom_facts:
                merged = MemoryManager.merge_facts(
                    current_facts_raw=target.get("facts"),
                    incoming_facts_raw=phantom_facts,
                    remove_facts_raw=[]
                )
                await db.execute(
                    "UPDATE user_profiles SET facts = ? WHERE user_id = ?",
                    (json.dumps(merged, ensure_ascii=False), str(target["user_id"]))
                )
                # 後續若有同名幽靈，需以合併後的結果為基礎
                target["facts"] = merged
            merged_count += 1
            logger.info(
                f"🔧 [遷移 v3] 幽靈畫像 user_id={ph_uid!r} 的 {len(phantom_facts)} 條事實"
                f"已併入真實使用者 [{target['user_name']} ({target['user_id']})]"
            )
        else:
            orphan_count += 1
            logger.warning(
                f"🔧 [遷移 v3] 幽靈畫像 user_id={ph_uid!r} 找不到唯一對應的真實使用者"
                f"（同名真人 {len(targets)} 位），直接刪除不做合併"
            )

        await db.execute("DELETE FROM user_profiles WHERE user_id = ?", (ph_uid,))

    await db.commit()
    logger.info(
        f"✅ [遷移 v3] 已清除 {len(phantoms)} 筆幽靈畫像"
        f"（{merged_count} 筆事實已併入真人，{orphan_count} 筆無對應直接刪除）。"
    )

async def _rebuild_fts_index(db) -> None:
    """
    【遷移 v1】從 messages 表全量重建 FTS 索引。

    舊版索引直接存訊息原文，但 FTS5 預設的 unicode61 分詞器會把整串連續中文視為
    單一 token，導致中文查詢幾乎永遠無法命中。新版改存 n-gram 切詞後的檢索字串。

    messages 表是原始資料的唯一真實來源，messages_fts 只是其衍生索引，因此重建為
    無損操作；user_profiles（事實、好感度、互動印象）完全不受影響。
    """
    # 延遲匯入以避免與 memory_manager 形成循環相依
    from .memory_manager import MemoryManager

    logger.info("開始從 messages 表重建全文索引…")

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

    logger.info(f"✅ FTS 全文索引重建完成，共重新索引 {total} 則訊息。")

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
