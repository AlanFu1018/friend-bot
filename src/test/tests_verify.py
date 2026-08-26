import asyncio
import sys
from pathlib import Path

# 將專案根目錄加入 sys.path 以免執行測試時找不到模組
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

async def test_memory_system():
    from src.friend_bot.memory import init_db
    from src.friend_bot.memory import MemoryManager
    from src.friend_bot.ai.prompts import format_memory_context

    print("[測試] 1. 初始化資料庫...")
    await init_db()

    channel_id = "999888777"
    listen_channel_id = "111222333"
    user_id = "12345"
    user_name = "小明"

    print("[測試] 2. 模擬監聽頻道中用戶發言（偷聽記憶）...")
    await MemoryManager.save_message(
        message_id="msg_001",
        channel_id=listen_channel_id,
        user_id=user_id,
        user_name=user_name,
        content="我最近買了一台 PS5 Pro，正在打黑神話悟空！",
        has_image=False,
        is_bot=False,
        timestamp=1700000000
    )

    print("[測試] 3. 模擬更新用戶長期畫像...")
    await MemoryManager.update_user_profile(
        user_id=user_id,
        user_name=user_name,
        facts=["喜歡打電玩", "買了 PS5 Pro", "愛喝無糖烏龍"],
        interaction_notes="常在遊戲群出沒，喜歡幽默互動"
    )

    profile = await MemoryManager.get_user_profile(user_id)
    print(f"-> 取得用戶畫像: {profile}")
    assert profile is not None
    assert "PS5 Pro" in profile["facts"][1]

    print("[測試] 4. 模擬回覆頻道中的短期對話...")
    await MemoryManager.save_message(
        message_id="msg_002",
        channel_id=channel_id,
        user_id="67890",
        user_name="小華",
        content="週末大家都打算幹嘛？",
        has_image=False,
        is_bot=False,
        timestamp=1700000100
    )
    await MemoryManager.save_message(
        message_id="msg_003",
        channel_id=channel_id,
        user_id=user_id,
        user_name=user_name,
        content="我打算在家通關黑神話",
        has_image=False,
        is_bot=False,
        timestamp=1700000120
    )

    short_term = await MemoryManager.get_short_term_context(channel_id, limit=5)
    print(f"-> 短期記憶筆數: {len(short_term)}")
    assert len(short_term) == 2

    print("[測試] 5. 測試 FTS5 歷史回憶跨頻道檢索 (搜尋 'PS5')...")
    deep_recall = await MemoryManager.recall_deep_history("PS5 有什麼好玩的？", exclude_message_ids=["msg_002", "msg_003"])
    print(f"-> 深度回憶檢索結果: {deep_recall}")
    assert len(deep_recall) >= 1
    assert "PS5" in deep_recall[0]["content"]

    print("[測試] 6. 測試上下文 Prompt 格式化合成...")
    context_text = format_memory_context(
        current_user_name=user_name,
        user_profile=profile,
        deep_history=deep_recall,
        short_term_history=short_term
    )
    print("=" * 50)
    print(context_text)
    print("=" * 50)

    print("\n[SUCCESS] 所有三層記憶與資料庫功能測試全部通過！")

if __name__ == "__main__":
    asyncio.run(test_memory_system())
