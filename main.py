import argparse
import asyncio
import sys
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
    clear_profiles_only
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
    return parser.parse_args()

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
