import sys
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
import discord

from src.friend_bot.core.config import BOT_NAME
from src.friend_bot.memory.db import init_db, clear_all_memory
from src.friend_bot.memory.memory_manager import MemoryManager
from src.friend_bot.bot.utils.alarm import AlarmManager, parse_alarm_time
from src.friend_bot.bot.utils.calendar import CalendarManager, parse_calendar_time
from src.friend_bot.bot.utils.burst import BurstBufferManager
from src.friend_bot.ai.prompts import (
    format_memory_context,
    build_multi_entity_extraction_prompt,
    build_batch_dialogue_extraction_prompt,
    build_burst_dialogue_prompt,
    parse_burst_reply_response,
    TIER_ATTITUDE_MAP
)
from src.friend_bot.ai.memory_extractor import MemoryExtractor
from src.friend_bot.bot.client import FriendBotClient

class TestFriendBotFeatures(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await init_db()
        await clear_all_memory()

    # ==================== 1. 鬧鐘 (Alarm) 邏輯測試 ====================
    def test_parse_alarm_time(self):
        base_dt = datetime(2026, 8, 27, 12, 0, 0)
        dt, ts, date_str, time_str, formatted = parse_alarm_time("2026/8/27/15/30", base_now=base_dt)
        self.assertEqual(formatted, "2026/08/27 15:30")
        self.assertEqual(time_str, "15:30")

    async def test_alarm_manager_lifecycle(self):
        channel_id = "111222"
        user_id = "user_alarm_test"
        user_name = "KurisuFan"
        content = "搶特展限定週邊"
        
        future_dt = datetime.now() + timedelta(hours=2)
        target_ts = int(future_dt.timestamp())
        formatted_str = future_dt.strftime("%Y/%m/%d %H:%M")

        alarm_id = await AlarmManager.create_alarm(
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            target_timestamp=target_ts,
            target_time_str=formatted_str,
            content=content
        )
        self.assertIsInstance(alarm_id, int)

        pending = await AlarmManager.get_pending_alarms(user_id=user_id)
        self.assertTrue(any(a["id"] == alarm_id for a in pending))

        await AlarmManager.mark_alarm_triggered(alarm_id)

        alarm_id2 = await AlarmManager.create_alarm(
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            target_timestamp=target_ts + 3600,
            target_time_str=(future_dt + timedelta(hours=1)).strftime("%Y/%m/%d %H:%M"),
            content="另一個鬧鐘"
        )
        canceled = await AlarmManager.cancel_alarm(alarm_id=alarm_id2, user_id=user_id)
        self.assertTrue(canceled)

    # ==================== 2. 行事曆 (Calendar) 邏輯測試 ====================
    def test_parse_calendar_time(self):
        base_dt = datetime(2026, 8, 27, 12, 0, 0)
        dt, ts, date_str, time_str, formatted = parse_calendar_time("2026-08-27 18:30", base_now=base_dt)
        self.assertEqual(date_str, "2026-08-27")
        self.assertEqual(time_str, "18:30")
        self.assertEqual(formatted, "2026/08/27 18:30")

    async def test_calendar_manager_crud_and_query_by_date(self):
        channel_id = "333444"
        user_id = "user_okabe_lab"
        user_name = "Okabe"
        content = "時間機器理論研討會"
        
        future_dt = datetime.now() + timedelta(hours=2)
        target_ts = int(future_dt.timestamp())
        date_str = future_dt.strftime("%Y-%m-%d")
        time_str = future_dt.strftime("%H:%M")
        formatted_str = future_dt.strftime("%Y/%m/%d %H:%M")

        event_id = await CalendarManager.create_event(
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            target_timestamp=target_ts,
            target_date=date_str,
            target_time=time_str,
            target_time_str=formatted_str,
            content=content
        )
        self.assertIsInstance(event_id, int)

        events_on_date = await CalendarManager.get_user_events_by_date(user_id=user_id, date_str=date_str)
        self.assertTrue(any(e["id"] == event_id for e in events_on_date))

        summary = await CalendarManager.get_user_schedule_summary(user_id=user_id)
        self.assertIn("時間機器理論研討會", summary)
        self.assertIn(date_str, summary)

        canceled = await CalendarManager.cancel_event(event_id=event_id, user_id=user_id)
        self.assertTrue(canceled)

    # ==================== 3. 多人多維記憶檢索 (A + B + C 混合方案) 測試 ====================
    async def test_multi_user_profile_recall(self):
        await MemoryManager.update_user_profile(
            user_id="user_okabe",
            user_name="岡部",
            facts=["瘋狂科學家", "喜歡喝 Dr Pepper"],
            interaction_notes="說話中二，常發布作戰計畫"
        )
        await MemoryManager.update_user_profile(
            user_id="user_daru",
            user_name="桶子",
            facts=["超級駭客", "熱愛美少女遊戲"],
            interaction_notes="經常通宵，喜歡女僕咖啡廳"
        )
        await MemoryManager.update_user_profile(
            user_id="user_mayuri",
            user_name="真由理",
            facts=["喜歡做 Cosplay 服裝", "口頭禪是嘟嘟嚕"],
            interaction_notes="天真可愛，實驗室的吉祥物"
        )

        current_prof, other_profs = await MemoryManager.resolve_multi_user_profiles(
            current_user_id="user_okabe",
            content="桶子今天又在實驗室通宵玩遊戲了"
        )
        self.assertIsNotNone(current_prof)
        self.assertEqual(current_prof["user_name"], "岡部")
        self.assertEqual(len(other_profs), 1)
        self.assertEqual(other_profs[0]["user_name"], "桶子")
        self.assertIn("超級駭客", other_profs[0]["facts"])

        context_str = format_memory_context(
            current_user_name="岡部",
            user_profile=current_prof,
            deep_history=[],
            short_term_history=[],
            other_user_profiles=other_profs
        )
        self.assertIn("【主要發言者 岡部 的個人特徵記憶】:", context_str)
        self.assertIn("【對話中提及 / 近期在場的其他群友畫像】:", context_str)
        self.assertIn("用戶名稱: 桶子", context_str)

    # ==================== 4. 跨用戶特徵提煉與歸屬測試 ====================
    async def test_cross_user_memory_extraction(self):
        await MemoryManager.update_user_profile(
            user_id="user_speaker_a",
            user_name="真由理",
            facts=["喜歡香蕉"],
            interaction_notes="活潑開朗"
        )
        await MemoryManager.update_user_profile(
            user_id="user_target_b",
            user_name="桶子",
            facts=["超級駭客"],
            interaction_notes="技術宅"
        )

        mock_gemini_response = """```json
{
  "updates": [
    {
      "user_id": "user_speaker_a",
      "user_name": "真由理",
      "facts": ["正在縫製新服裝"],
      "remove_facts": [],
      "interaction_notes": "開心地面對分享近況",
      "favorability_delta": 1
    },
    {
      "user_id": "user_target_b",
      "user_name": "桶子",
      "facts": ["最近沉迷最新Galgame", "每天熬夜到早上"],
      "remove_facts": [],
      "interaction_notes": "被真由理提到經常通宵熬夜打電動",
      "favorability_delta": 0
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response)

        extractor = MemoryExtractor(gemini_client=mock_gemini_client)

        other_user = await MemoryManager.get_user_profile("user_target_b")
        await extractor.extract_and_update(
            user_id="user_speaker_a",
            user_name="真由理",
            recent_messages=["桶子最近都在沉迷最新Galgame，每天都熬夜到早上！我正在幫他縫新衣服呢。"],
            other_users=[other_user]
        )

        profile_a = await MemoryManager.get_user_profile("user_speaker_a")
        self.assertIn("喜歡香蕉", profile_a["facts"])
        self.assertIn("正在縫製新服裝", profile_a["facts"])

        profile_b = await MemoryManager.get_user_profile("user_target_b")
        self.assertIn("超級駭客", profile_b["facts"])
        self.assertIn("最近沉迷最新Galgame", profile_b["facts"])
        self.assertIn("每天熬夜到早上", profile_b["facts"])

    # ==================== 5. 事實防洗白與增量聯集保護測試 ====================
    async def test_facts_anti_overwrite_protection(self):
        initial_facts = [
            "對 FromSoftware 出品的遊戲有高度興趣",
            "有向他人索求遊戲作為禮物的傾向",
            "對他人職業或持有的執照表現出好奇與關注",
            "對 EMT（緊急醫療技術員）相關話題感興趣",
            "在 Discord 伺服器中擁有管理權限授予能力",
            "對特定物品（如梅酒）的去向有追蹤與確認的習慣",
            "具備線下物品交接的意願與行動力",
            "近期有涉及開車相關的線下互動與謝禮往來"
        ]
        await MemoryManager.update_user_profile(
            user_id="555738929584930868",
            user_name="感應與運動",
            facts=initial_facts,
            interaction_notes="發言風格帶有戲劇性與中二感"
        )

        mock_gemini_response = """```json
{
  "updates": [
    {
      "user_id": "555738929584930868",
      "user_name": "感應與運動",
      "facts": [],
      "remove_facts": [],
      "interaction_notes": "最新發言顯示其關注好感度機制，並擅長透過觀察他人互動來進行心理試探。",
      "favorability_delta": 0
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response)

        extractor = MemoryExtractor(gemini_client=mock_gemini_client)
        await extractor.extract_and_update(
            user_id="555738929584930868",
            user_name="感應與運動",
            recent_messages=["你今天好感度到底多少啊？"]
        )

        profile = await MemoryManager.get_user_profile("555738929584930868")
        self.assertEqual(len(profile["facts"]), 8)
        self.assertIn("對 FromSoftware 出品的遊戲有高度興趣", profile["facts"])
        self.assertIn("對 EMT（緊急醫療技術員）相關話題感興趣", profile["facts"])
        self.assertIn("對特定物品（如梅酒）的去向有追蹤與確認的習慣", profile["facts"])
        self.assertIn("心理試探", profile["interaction_notes"])

    # ==================== 6. 事實更正與移除 (remove_facts) 測試 ====================
    async def test_facts_correction_and_remove(self):
        await MemoryManager.update_user_profile(
            user_id="user_correct_test",
            user_name="測試群友",
            facts=["住在台中市", "喜歡喝咖啡", "職業是軟體工程師"],
            interaction_notes="平常話不多"
        )

        mock_gemini_response = """```json
{
  "updates": [
    {
      "user_id": "user_correct_test",
      "user_name": "測試群友",
      "facts": ["目前定居在台北市"],
      "remove_facts": ["住在台中市"],
      "interaction_notes": "主動更正了居住地資訊",
      "favorability_delta": 1
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response)

        extractor = MemoryExtractor(gemini_client=mock_gemini_client)
        await extractor.extract_and_update(
            user_id="user_correct_test",
            user_name="測試群友",
            recent_messages=["我上個月搬到台北了，不在台中了哦！"]
        )

        profile = await MemoryManager.get_user_profile("user_correct_test")
        self.assertNotIn("住在台中市", profile["facts"])
        self.assertIn("目前定居在台北市", profile["facts"])
        self.assertIn("喜歡喝咖啡", profile["facts"])
        self.assertIn("職業是軟體工程師", profile["facts"])
        self.assertEqual(len(profile["facts"]), 3)

    # ==================== 7. 方案 C：監聽頻道多輪批次提煉與狀態流轉測試 ====================
    async def test_plan_c_batch_extraction_and_extracted_flag(self):
        channel_listen = "listen_channel_999"
        msg_1 = "msg_batch_1"
        msg_2 = "msg_batch_2"
        
        await MemoryManager.save_message(
            message_id=msg_1,
            channel_id=channel_listen,
            user_id="user_batch_okabe",
            user_name="岡部",
            content="我買了新的科學實驗器材！",
            extracted=False
        )
        await MemoryManager.save_message(
            message_id=msg_2,
            channel_id=channel_listen,
            user_id="user_batch_daru",
            user_name="桶子",
            content="我剛入手了 Realforce 鍵盤，超好打！",
            extracted=False
        )

        unextracted = await MemoryManager.get_unextracted_messages(channel_id=channel_listen)
        self.assertEqual(len(unextracted), 2)

        mock_gemini_response = """```json
{
  "updates": [
    {
      "user_id": "user_batch_okabe",
      "user_name": "岡部",
      "facts": ["購買了新的科學實驗器材"],
      "remove_facts": [],
      "interaction_notes": "熱衷於實驗研究",
      "favorability_delta": 1
    },
    {
      "user_id": "user_batch_daru",
      "user_name": "桶子",
      "facts": ["入手了 Realforce 鍵盤"],
      "remove_facts": [],
      "interaction_notes": "熱衷於分享電腦週邊",
      "favorability_delta": 0
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response)

        extractor = MemoryExtractor(gemini_client=mock_gemini_client)
        await extractor.extract_from_dialogue_batch(unextracted)

        unextracted_after = await MemoryManager.get_unextracted_messages(channel_id=channel_listen)
        self.assertEqual(len(unextracted_after), 0)

        prof_okabe = await MemoryManager.get_user_profile("user_batch_okabe")
        self.assertIn("購買了新的科學實驗器材", prof_okabe["facts"])

        prof_daru = await MemoryManager.get_user_profile("user_batch_daru")
        self.assertIn("入手了 Realforce 鍵盤", prof_daru["facts"])

    # ==================== 8. 好感度進展與每日上限防刷保護測試 ====================
    async def test_favorability_progression_and_daily_cap(self):
        user_id = "user_fav_tester"
        user_name = "實驗助手"
        
        await MemoryManager.update_user_profile(
            user_id=user_id,
            user_name=user_name,
            facts=["認真做實驗"],
            interaction_notes="很有禮貌"
        )
        init_p = await MemoryManager.get_user_profile(user_id)
        self.assertEqual(init_p["favorability"], 30)
        self.assertEqual(init_p["relationship_tier"], "familiar")

        mock_gemini_response_1 = """```json
{
  "updates": [
    {
      "user_id": "user_fav_tester",
      "user_name": "實驗助手",
      "facts": ["請紅莉栖喝了一瓶Dr Pepper"],
      "remove_facts": [],
      "interaction_notes": "互動極佳",
      "favorability_delta": 3
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response_1)

        extractor = MemoryExtractor(gemini_client=mock_gemini_client)
        await extractor.extract_and_update(
            user_id=user_id,
            user_name=user_name,
            recent_messages=["送你一瓶冰涼的 Dr Pepper！"]
        )

        p1 = await MemoryManager.get_user_profile(user_id)
        self.assertEqual(p1["favorability"], 33)
        self.assertEqual(p1["daily_favorability_gain"], 3)
        self.assertEqual(p1["relationship_tier"], "familiar")

        mock_gemini_response_2 = """```json
{
  "updates": [
    {
      "user_id": "user_fav_tester",
      "user_name": "實驗助手",
      "facts": ["認真研讀神經科學論文"],
      "remove_facts": [],
      "interaction_notes": "認真討論學術",
      "favorability_delta": 4
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response_2)
        await extractor.extract_and_update(
            user_id=user_id,
            user_name=user_name,
            recent_messages=["這篇關於時間記憶的論文好精彩！"]
        )

        p2 = await MemoryManager.get_user_profile(user_id)
        self.assertEqual(p2["favorability"], 35)  # 33 + 2 (每日上限 5 分生效)
        self.assertEqual(p2["daily_favorability_gain"], 5)

        mock_gemini_response_3 = """```json
{
  "updates": [
    {
      "user_id": "user_fav_tester",
      "user_name": "實驗助手",
      "facts": [],
      "remove_facts": [],
      "interaction_notes": "繼續稱讚紅莉栖",
      "favorability_delta": 2
    }
  ]
}
```"""
        mock_gemini_client = MagicMock()
        mock_gemini_client.generate_response = AsyncMock(return_value=mock_gemini_response_3)
        await extractor.extract_and_update(
            user_id=user_id,
            user_name=user_name,
            recent_messages=["紅莉栖真是天才！"]
        )

        p3 = await MemoryManager.get_user_profile(user_id)
        self.assertEqual(p3["favorability"], 35)
        self.assertEqual(p3["daily_favorability_gain"], 5)

    # ==================== 9. 好感度階級轉換與 Prompt 動態注入測試 ====================
    def test_relationship_tier_computation_and_attitude_injection(self):
        self.assertEqual(MemoryManager.compute_relationship_tier(10), "stranger")
        self.assertEqual(MemoryManager.compute_relationship_tier(30), "familiar")
        self.assertEqual(MemoryManager.compute_relationship_tier(65), "trusted")
        self.assertEqual(MemoryManager.compute_relationship_tier(90), "cherished")

        profile_stranger = {"user_name": "陌生人", "relationship_tier": "stranger", "facts": [], "interaction_notes": ""}
        context_stranger = format_memory_context("陌生人", profile_stranger, [], [])
        self.assertIn("Tier 1 陌生警戒", context_stranger)

        profile_trusted = {"user_name": "助手", "relationship_tier": "trusted", "facts": ["實驗室夥伴"], "interaction_notes": ""}
        context_trusted = format_memory_context("助手", profile_trusted, [], [])
        self.assertIn("Tier 3 實驗室夥伴", context_trusted)
        self.assertIn("害羞破防", context_trusted)

    # ==================== 10. 多人群聊 Burst 提示詞與動態引用標籤解析測試 ====================
    def test_burst_dialogue_prompt_and_target_parser(self):
        messages = [
            {"message_id": "msg_okabe_1", "user_name": "岡部", "content": "時間機器理論成功了！"},
            {"message_id": "msg_daru_2", "user_name": "桶子", "content": "他又在發病了 www"}
        ]
        prompt = build_burst_dialogue_prompt(memory_context="Context", burst_messages=messages)
        self.assertIn("多人群聊即時熱烈討論", prompt)
        self.assertIn("[ID: msg_okabe_1] 岡部: 時間機器理論成功了！", prompt)
        self.assertIn("[ID: msg_daru_2] 桶子: 他又在發病了 www", prompt)

        # 模擬 AI 輸出帶有 [TARGET_ID: msg_daru_2]
        ai_response_with_tag = "[TARGET_ID: msg_daru_2]\n哈？桶子你閉嘴啦！雖然他確實很中二，但這理論可是有嚴謹公式推導的！"
        target_id, clean_reply = parse_burst_reply_response(ai_response_with_tag, default_target_id="msg_okabe_1")
        self.assertEqual(target_id, "msg_daru_2")
        self.assertNotIn("[TARGET_ID:", clean_reply)
        self.assertIn("哈？桶子你閉嘴啦！", clean_reply)

    # ==================== 11. Burst 緩衝管理器多用戶聚合判斷測試 ====================
    async def test_burst_buffer_manager_multi_user(self):
        burst_mgr = BurstBufferManager(window_seconds=0.1, min_user_count=2, max_burst_messages=5)
        
        # 建立假的 discord.Message 物件
        mock_channel = MagicMock()
        mock_channel.id = 999888

        msg_1 = MagicMock(spec=discord.Message)
        msg_1.id = 10001
        msg_1.channel = mock_channel
        msg_1.author = MagicMock()
        msg_1.author.id = 111
        msg_1.author.bot = False
        msg_1.author.display_name = "岡部"
        msg_1.clean_content = "第一句"
        msg_1.attachments = []

        msg_2 = MagicMock(spec=discord.Message)
        msg_2.id = 10002
        msg_2.channel = mock_channel
        msg_2.author = MagicMock()
        msg_2.author.id = 222
        msg_2.author.bot = False
        msg_2.author.display_name = "桶子"
        msg_2.clean_content = "第二句"
        msg_2.attachments = []

        flush_results = []
        async def on_flush(channel_id, messages, is_burst):
            flush_results.append((channel_id, messages, is_burst))

        await burst_mgr.add_message(msg_1, on_flush)
        await burst_mgr.add_message(msg_2, on_flush)

        # 等待窗口過期觸發 flush (0.25s)
        await asyncio.sleep(0.25)

        self.assertEqual(len(flush_results), 1)
        cid, msgs, is_b = flush_results[0]
        self.assertEqual(cid, "999888")
        self.assertEqual(len(msgs), 2)
        self.assertTrue(is_b)  # 2 位不同用戶，判定為 Burst!

    # ==================== 12. 機器人自身自我介紹與 /kurisu-profile @機器人 測試 ====================
    def test_bot_self_profile_embed(self):
        intents = discord.Intents.default()
        client = FriendBotClient(intents=intents)
        
        embed = client._create_bot_profile_embed()
        self.assertIsNotNone(embed)
        self.assertIn("牧瀨紅莉栖", embed.title)
        self.assertIn(BOT_NAME, embed.title)
        self.assertIn("維克多·孔多利亞大學", embed.description)
        
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("身份與背景" in name for name in field_names))
        self.assertTrue(any("專長領域與性格特質" in name for name in field_names))
        self.assertTrue(any("關係與好感機制" in name for name in field_names))
        self.assertTrue(any("常用指令指南" in name for name in field_names))
        
        field_values = "\n".join([f.value for f in embed.fields])
        self.assertIn("Labmem No.004", field_values)
        self.assertIn("Dr Pepper", field_values)
        self.assertIn("/kurisu-profile", field_values)

if __name__ == "__main__":
    unittest.main()
