import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# 自動將專案根目錄加入 sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import discord
from src.friend_bot.core import DISCORD_TOKEN, setup_logger, get_logger
from src.friend_bot.memory import (
    init_db,
    clear_all_memory,
    clear_history_only,
    clear_profiles_only,
    MemoryManager
)
from src.friend_bot.bot.client import FriendBotClient

# 初始化專業日誌系統
setup_logger()
logger = get_logger("main")

def parse_args():
    parser = argparse.ArgumentParser(
        description="Friend-Bot: 具備三層全記憶系統的 Discord 聊天機器人"
    )
    parser.add_argument(
        "--clear-memory",
        action="store_true",
        help="啟動前清空所有記憶（包含所有頻道對話歷史與用戶長期畫像）"
    )
    parser.add_argument(
        "--clear-history",
        action="store_true",
        help="啟動前僅清空所有頻道的對話歷史，保留用戶長期個人畫像"
    )
    parser.add_argument(
        "--clear-profiles",
        action="store_true",
        help="啟動前僅清空所有用戶長期特徵畫像，保留對話歷史紀錄"
    )
    parser.add_argument(
        "--only-clear",
        action="store_true",
        help="執行完記憶清理後直接結束程式，不啟動 Discord 機器人"
    )
    parser.add_argument(
        "--view-notes",
        metavar="USER_ID",
        help="查看指定使用者目前的互動印象與上一版快照（唯讀，不修改資料），執行後直接結束"
    )
    parser.add_argument(
        "--restore-notes",
        metavar="USER_ID",
        help=(
            "將指定使用者的互動印象還原為上一版快照（與目前版本互換，可重複執行來回切換），"
            "執行後直接結束。僅此 CLI 提供，不開放 Discord 指令——這個操作直接覆寫他人的"
            "長期人設資料，安全邊界應落在能操作主機的人身上"
        )
    )
    return parser.parse_args()

async def _print_notes_status(user_id: str) -> None:
    """印出指定使用者目前的互動印象與上一版快照，供決定是否需要 --restore-notes"""
    profile = await MemoryManager.get_user_profile(user_id)
    if not profile:
        logger.info(f"⚠️ 找不到使用者 {user_id} 的畫像記錄")
        return

    current = profile.get("interaction_notes", "").strip() or "（無）"
    prev = profile.get("interaction_notes_prev", "").strip() or "（無快照）"
    prev_at = profile.get("interaction_notes_prev_at", 0)
    prev_at_str = (
        datetime.fromtimestamp(prev_at).strftime("%Y-%m-%d %H:%M:%S") if prev_at else "—"
    )

    logger.info(f"📋 使用者 [{profile.get('user_name', user_id)} ({user_id})] 的互動印象")
    logger.info(f"── 目前版本 ──\n{current}")
    logger.info(f"── 上一版快照（{prev_at_str}）──\n{prev}")

async def main():
    args = parse_args()

    # 1. 初始化資料庫與資料表架構
    logger.info("正在檢查並初始化 SQLite 資料庫與 FTS5 全文索引...")
    await init_db()

    # 2. 處理命令列記憶清理參數
    if args.clear_memory:
        await clear_all_memory()
    elif args.clear_history:
        await clear_history_only()
    elif args.clear_profiles:
        await clear_profiles_only()

    # 若指定了 --only-clear，清理後直接結束
    if args.only_clear:
        logger.info("記憶清理任務完成，程式順利結束。")
        return

    # 2.1 互動印象查看／還原（唯讀或還原後直接結束，不啟動 Discord 機器人）
    if args.view_notes:
        await _print_notes_status(args.view_notes)
        return

    if args.restore_notes:
        ok, reason = await MemoryManager.restore_interaction_notes(args.restore_notes)
        logger.info(f"{'✅' if ok else '⚠️'} [互動印象還原] 使用者 {args.restore_notes}：{reason}")
        return

    # 3. 檢查 Discord Token
    if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_bot_token_here":
        logger.error("尚未設定 DISCORD_TOKEN！請在 .env 檔案中填入有效的 Discord Bot Token。")
        sys.exit(1)

    # 4. 設定 Discord Intents
    intents = discord.Intents.default()
    intents.message_content = True  # 必須在 Discord Developer Portal 開啟 Message Content Intent
    intents.messages = True
    intents.guilds = True

    # 5. 實例化並啟動機器人
    client = FriendBotClient(intents=intents)
    logger.info("正在連線至 Discord 網關...")
    async with client:
        await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("接收到中斷訊號，機器人已安全關閉。")
