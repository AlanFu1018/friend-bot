import unittest
import asyncio
import os
import re
import sys
import time
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# 加入根目錄至 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from src.friend_bot.memory import MemoryManager, init_db, get_db_connection
from src.friend_bot.bot.utils.alarm import AlarmManager, parse_alarm_time
from src.friend_bot.bot.utils.calendar import CalendarManager, parse_calendar_time
from src.friend_bot.bot.utils.burst import BurstBufferManager
from src.friend_bot.bot.utils.emotion import EmotionReplacer
from src.friend_bot.ai.memory_extractor import MemoryExtractor
from src.friend_bot.ai.prompts import (
    format_memory_context,
    build_burst_dialogue_prompt,
    parse_burst_reply_response,
    build_multi_entity_extraction_prompt,
    build_batch_dialogue_extraction_prompt
)
from src.friend_bot.bot.client import FriendBotClient
from src.friend_bot.bot.commands import (
    HelpCommandsMixin,
    SearchCommandsMixin,
    ProfileCommandsMixin,
    AlarmCommandsMixin,
    CalendarCommandsMixin,
    GeneralCommandsMixin
)


def get_fact_texts(facts: list) -> list:
    """測試輔助：提取事實清單中的純文字字串"""
    return [f["text"] if isinstance(f, dict) else str(f) for f in facts]


class TestFriendBotFeatures(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        """每個測試執行前初始化記憶體資料庫"""
        await init_db()
        async with get_db_connection() as db:
            await db.execute("DELETE FROM alarms")
            await db.execute("DELETE FROM calendar_events")
            await db.execute("DELETE FROM user_profiles")
            await db.execute("DELETE FROM messages")
            await db.execute("DELETE FROM messages_fts")
            await db.commit()

    # ==================== 1. 鬧鐘 (Alarm) 邏輯測試 ====================
    def test_parse_alarm_time_relative_and_absolute(self):
        base_dt = datetime(2026, 8, 27, 12, 0, 0)

        # 相對時間
        dt, ts, date_str, time_str, formatted = parse_alarm_time("10m", base_now=base_dt)
        self.assertEqual(ts, int((base_dt + timedelta(minutes=10)).timestamp()))

        dt2, ts2, date_str2, time_str2, formatted2 = parse_alarm_time("2h", base_now=base_dt)
        self.assertEqual(ts2, int((base_dt + timedelta(hours=2)).timestamp()))

        # 絕對時間
        dt3, ts3, date_str3, time_str3, formatted3 = parse_alarm_time("14:30", base_now=base_dt)
        self.assertEqual(formatted3, "2026/08/27 14:30")

    async def test_alarm_manager_crud(self):
        channel_id = "111222"
        user_id = "user_okabe"
        user_name = "岡部倫太郎"
        content = "召開 Lab 核心作戰會議"
        
        future_dt = datetime.now() + timedelta(hours=1)
        target_ts = int(future_dt.timestamp())
        target_str = future_dt.strftime("%Y/%m/%d %H:%M")

        alarm_id = await AlarmManager.create_alarm(
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            target_timestamp=target_ts,
            target_time_str=target_str,
            content=content
        )
        self.assertIsInstance(alarm_id, int)

        active = await AlarmManager.get_pending_alarms(user_id=user_id)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["content"], content)

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
        self.assertIn("超級駭客", get_fact_texts(other_profs[0]["facts"]))

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
        self.assertIn("喜歡香蕉", get_fact_texts(profile_a["facts"]))
        self.assertIn("正在縫製新服裝", get_fact_texts(profile_a["facts"]))

        profile_b = await MemoryManager.get_user_profile("user_target_b")
        self.assertIn("超級駭客", get_fact_texts(profile_b["facts"]))
        self.assertIn("最近沉迷最新Galgame", get_fact_texts(profile_b["facts"]))
        self.assertIn("每天熬夜到早上", get_fact_texts(profile_b["facts"]))

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
            recent_messages=["我只是在測試好感度系統而已～"]
        )

        profile = await MemoryManager.get_user_profile("555738929584930868")
        self.assertIsNotNone(profile)
        self.assertEqual(len(profile["facts"]), 8)
        self.assertIn("對 FromSoftware 出品的遊戲有高度興趣", get_fact_texts(profile["facts"]))

    # ==================== 6. 事實修正與 remove_facts 機制測試 ====================
    async def test_facts_correction_and_remove(self):
        initial_facts = ["住在台中", "喜歡吃拉麵", "職業是工程師"]
        await MemoryManager.update_user_profile(
            user_id="user_correct_test",
            user_name="測試群友",
            facts=initial_facts,
            interaction_notes="普通群友"
        )

        mock_gemini_response = """```json
{
  "updates": [
    {
      "user_id": "user_correct_test",
      "user_name": "測試群友",
      "facts": ["目前定居在台北市"],
      "remove_facts": ["住在台中"],
      "interaction_notes": "提到自己已經搬遷到台北生活",
      "favorability_delta": 0
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
            recent_messages=["我其實上個月就搬家到台北了，已經不住台中囉！"]
        )

        profile = await MemoryManager.get_user_profile("user_correct_test")
        self.assertNotIn("住在台中", get_fact_texts(profile["facts"]))
        self.assertIn("喜歡吃拉麵", get_fact_texts(profile["facts"]))
        self.assertIn("目前定居在台北市", get_fact_texts(profile["facts"]))

    # ==================== 7. 監聽頻道批次提煉 (Batch Extraction) 測試 ====================
    async def test_plan_c_batch_extraction_and_extracted_flag(self):
        channel_listen = "999888"
        
        await MemoryManager.save_message(
            message_id="msg_batch_1",
            channel_id=channel_listen,
            user_id="user_batch_okabe",
            user_name="岡部",
            content="我剛剛在秋葉原買了新的實驗器材！",
            is_bot=False,
            extracted=False
        )
        await MemoryManager.save_message(
            message_id="msg_batch_2",
            channel_id=channel_listen,
            user_id="user_batch_daru",
            user_name="桶子",
            content="我也入了新的 Realforce 鍵盤常駐在實驗室了。",
            is_bot=False,
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
      "facts": ["入了 Realforce 鍵盤"],
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
        self.assertIn("購買了新的科學實驗器材", get_fact_texts(prof_okabe["facts"]))

        prof_daru = await MemoryManager.get_user_profile("user_batch_daru")
        self.assertIn("入了 Realforce 鍵盤", get_fact_texts(prof_daru["facts"]))

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
      "facts": ["請紅莉棲喝了一瓶Dr Pepper"],
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
      "interaction_notes": "繼續稱讚紅莉棲",
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
            recent_messages=["紅莉棲真是天才！"]
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
        msg_2.clean_content = "第二句搶話"
        msg_2.attachments = []

        callback_mock = AsyncMock()
        await burst_mgr.add_message(msg_1, on_flush=callback_mock)
        await burst_mgr.add_message(msg_2, on_flush=callback_mock)

        await asyncio.sleep(0.2)
        self.assertNotIn("999888", burst_mgr._buffers)

    # ==================== 12. Mixin 繼承鏈與全 Slash 指令註冊測試 ====================
    def test_mixin_inheritance_and_command_registration(self):
        client = FriendBotClient(intents=discord.Intents.default())
        
        self.assertIsInstance(client, HelpCommandsMixin)
        self.assertIsInstance(client, SearchCommandsMixin)
        self.assertIsInstance(client, ProfileCommandsMixin)
        self.assertIsInstance(client, AlarmCommandsMixin)
        self.assertIsInstance(client, CalendarCommandsMixin)

        client.register_help_commands()
        client.register_search_commands()
        client.register_profile_commands()
        client.register_alarm_commands()
        client.register_calendar_commands()

        registered_command_names = [cmd.name for cmd in client.tree.get_commands()]
        expected_commands = [
            "kurisu-help",
            "kurisu-search",
            "kurisu-profile",
            "kurisu-alarm-set",
            "kurisu-alarm-list",
            "kurisu-alarm-cancel",
            "kurisu-calendar-set",
            "kurisu-calendar-list",
            "kurisu-calendar-cancel"
        ]
        for cmd_name in expected_commands:
            self.assertIn(cmd_name, registered_command_names, f"指令 /{cmd_name} 未正確註冊至 CommandTree！")

    # ==================== 13. 三軌混合事實檢索 (Three-track RAG) 測試 ====================
    def test_three_track_rag_filtering(self):
        facts = [
            {"text": "最愛喝 Dr Pepper", "hits": 30, "created_at": 100},
            {"text": "自稱狂氣科學家鳳凰院凶真", "hits": 25, "created_at": 110},
            {"text": "不喜歡吃青椒", "hits": 2, "created_at": 120},
            {"text": "喜歡在實驗室煮拉麵當宵夜", "hits": 5, "created_at": 130},
            {"text": "上週新買了二手顯卡 RTX 4090", "hits": 1, "created_at": 140},
            {"text": "昨天剛修理好了微波爐時間機器", "hits": 1, "created_at": 150}
        ]

        # 當對話提及「拉麵、宵夜」時
        query = "今晚要去吃拉麵當宵夜嗎？"
        filtered, hits = MemoryManager.filter_facts_three_tracks(
            facts_data=facts,
            query_text=query,
            max_total=5,
            heat_limit=2,
            recent_limit=2
        )

        self.assertLessEqual(len(filtered), 5)
        # 軌道 1 (Heat): 核心高頻 (Dr Pepper, 鳳凰院凶真)
        self.assertIn("最愛喝 Dr Pepper", filtered)
        self.assertIn("自稱狂氣科學家鳳凰院凶真", filtered)
        # 軌道 2 (RAG): 話題命中 (拉麵宵夜)
        self.assertIn("喜歡在實驗室煮拉麵當宵夜", filtered)
        self.assertIn("喜歡在實驗室煮拉麵當宵夜", hits)
        # 軌道 3 (Recent): 最新事實 (微波爐, RTX 4090)
        self.assertIn("昨天剛修理好了微波爐時間機器", filtered)

    # ==================== 14. 提煉重複確認加權 (Re-affirmation hits+=3) 測試 ====================
    def test_merge_facts_reaffirmation(self):
        current_facts = [
            {"text": "喜歡喝 Dr Pepper", "hits": 2, "created_at": 100, "last_used_at": 100}
        ]
        incoming_facts = ["喜歡喝 Dr Pepper", "入手了新顯卡"]
        remove_facts = []

        merged = MemoryManager.merge_facts(current_facts, incoming_facts, remove_facts)
        self.assertEqual(len(merged), 2)
        
        dr_pepper_fact = next(f for f in merged if f["text"] == "喜歡喝 Dr Pepper")
        self.assertEqual(dr_pepper_fact["hits"], 5)  # 2 + 3 提煉加權

        gpu_fact = next(f for f in merged if f["text"] == "入手了新顯卡")
        self.assertEqual(gpu_fact["hits"], 1)

    # ==================== 15. RAG 命中加權與冷卻保護測試 ====================
    async def test_record_fact_hits_cooldown(self):
        user_id = "user_hit_test"
        await MemoryManager.update_user_profile(
            user_id=user_id,
            user_name="熱度測試員",
            facts=[{"text": "喜歡吃壽喜燒", "hits": 1, "created_at": 100, "last_used_at": 0}]
        )

        # 第一次命中 -> hits 應該由 1 變 2
        await MemoryManager.record_fact_hits(user_id=user_id, hit_texts=["喜歡吃壽喜燒"], cooldown_seconds=3600)
        p1 = await MemoryManager.get_user_profile(user_id)
        f1 = next(f for f in p1["facts"] if f["text"] == "喜歡吃壽喜燒")
        self.assertEqual(f1["hits"], 2)

        # 立即再次命中 -> 因在 3600 秒冷卻期內，hits 應維持 2
        await MemoryManager.record_fact_hits(user_id=user_id, hit_texts=["喜歡吃壽喜燒"], cooldown_seconds=3600)
        p2 = await MemoryManager.get_user_profile(user_id)
        f2 = next(f for f in p2["facts"] if f["text"] == "喜歡吃壽喜燒")
        self.assertEqual(f2["hits"], 2)

    # ==================== 16. 情緒標籤渲染器 (Emotion Tag & Replace) 測試 ====================
    def test_emotion_replacer_tags_and_anti_repeat(self):
        # 測試載入
        EmotionReplacer.load_kaomoji()
        self.assertTrue("tsundere" in EmotionReplacer._kaomoji_map)
        self.assertTrue("shock" in EmotionReplacer._kaomoji_map)
        self.assertTrue("sigh" in EmotionReplacer._kaomoji_map)
        self.assertTrue("sad" in EmotionReplacer._kaomoji_map)
        self.assertTrue("depressed" in EmotionReplacer._kaomoji_map)

        # 測試標籤替換（應以程式碼區塊 `...` 包裹）
        raw_text = "誰是助手啊！還有，我哪有開心？[emotion:tsundere]"
        replaced = EmotionReplacer.replace_emotion_tags(raw_text)
        self.assertNotIn("[emotion:tsundere]", replaced)
        self.assertIn("誰是助手啊！還有，我哪有開心？", replaced)
        self.assertTrue("`" in replaced)

        # 測試別名映射 (如 [emotion:shy], [emotion:cry], [emotion:gloom])
        alias_text = "別看我啦……[emotion:shy] 嗚嗚……[emotion:cry] 好累喔[emotion:gloom]"
        replaced_alias = EmotionReplacer.replace_emotion_tags(alias_text)
        self.assertNotIn("[emotion:shy]", replaced_alias)
        self.assertNotIn("[emotion:cry]", replaced_alias)
        self.assertNotIn("[emotion:gloom]", replaced_alias)
        self.assertTrue("`" in replaced_alias)

        # 測試多標籤替換
        multi_text = "真是中二。[emotion:sigh] 不過理論確實嚴謹。[emotion:proud]"
        replaced_multi = EmotionReplacer.replace_emotion_tags(multi_text)
        self.assertNotIn("[emotion:sigh]", replaced_multi)
        self.assertNotIn("[emotion:proud]", replaced_multi)

        # 測試難過與沮喪標籤替換
        sad_depressed_text = "怎麼會這樣……[emotion:sad] 實驗又失敗了……[emotion:depressed]"
        replaced_sd = EmotionReplacer.replace_emotion_tags(sad_depressed_text)
        self.assertNotIn("[emotion:sad]", replaced_sd)
        self.assertNotIn("[emotion:depressed]", replaced_sd)
        self.assertIn("怎麼會這樣……", replaced_sd)
        self.assertIn("實驗又失敗了……", replaced_sd)
        self.assertTrue("`" in replaced_sd)

        # 測試防連續重複 (連續抽取 tsundere 兩次)
        k1 = EmotionReplacer.get_random_kaomoji("tsundere")
        k2 = EmotionReplacer.get_random_kaomoji("tsundere")
        self.assertNotEqual(k1, k2)

    # ==================== 17. 忽略前綴繞過監聽與回覆測試 ====================
    async def test_ignore_prefixes_bypass_listening_and_reply(self):
        client = FriendBotClient(intents=discord.Intents.default())
        
        # 建立模擬 Message
        mock_channel = MagicMock()
        mock_channel.id = 1542526624979878019  # 回覆頻道

        # 測試 1: 帶有 '#' 前綴的訊息
        msg_hash = MagicMock(spec=discord.Message)
        msg_hash.id = 99901
        msg_hash.channel = mock_channel
        msg_hash.author = MagicMock()
        msg_hash.author.id = 12345
        msg_hash.author.bot = False
        msg_hash.author.display_name = "測試員"
        msg_hash.clean_content = "# 這是一條測試用的旁白備註，不應被記錄或回覆"
        msg_hash.attachments = []

        with patch.object(MemoryManager, "save_message", new_callable=AsyncMock) as mock_save:
            with patch.object(client.burst_manager, "add_message", new_callable=AsyncMock) as mock_burst:
                await client.on_message(msg_hash)
                mock_save.assert_not_called()
                mock_burst.assert_not_called()

        # 測試 2: 帶有 '//' 前綴的訊息在監聽頻道中
        mock_listen_channel = MagicMock()
        mock_listen_channel.id = 935055001062088724  # 監聽頻道

        msg_slash = MagicMock(spec=discord.Message)
        msg_slash.id = 99902
        msg_slash.channel = mock_listen_channel
        msg_slash.author = MagicMock()
        msg_slash.author.id = 12345
        msg_slash.author.bot = False
        msg_slash.author.display_name = "測試員"
        msg_slash.clean_content = "// 這是另一條內部備註"
        msg_slash.attachments = []

        with patch.object(MemoryManager, "save_message", new_callable=AsyncMock) as mock_save:
            with patch.object(client.memory_extractor, "add_listen_message", new_callable=AsyncMock) as mock_listen:
                await client.on_message(msg_slash)
                mock_save.assert_not_called()
                mock_listen.assert_not_called()

    # ==================== 18. 第三層深度歷史回憶 (中文 n-gram 召回) 測試 ====================
    def test_build_search_blob_chinese_ngrams(self):
        """索引側切詞：中文訊息應被展開為空白分隔的 2/3-gram，FTS5 才切得開"""
        blob = MemoryManager.build_search_blob("昨天去吃拉麵")
        tokens = set(blob.split())

        # 二字詞與三字詞都應存在
        self.assertIn("拉麵", tokens)
        self.assertIn("吃拉麵", tokens)
        # 停用詞應被濾除
        self.assertNotIn("昨天", tokens)
        # 必須是空白分隔（否則 FTS5 仍會視為單一 token）
        self.assertGreater(len(tokens), 3)

        # 英文實詞應保留並轉小寫
        en_tokens = set(MemoryManager.build_search_blob("我在玩 Elden Ring").split())
        self.assertIn("elden", en_tokens)
        self.assertIn("ring", en_tokens)

    async def _seed_history_message(self, message_id: str, content: str, user_name: str = "岡部",
                                    channel_id: str = "777", timestamp: int = None):
        """測試輔助：寫入一則歷史訊息（會同時建立 FTS 索引）"""
        await MemoryManager.save_message(
            message_id=message_id,
            channel_id=channel_id,
            user_id="1001",
            user_name=user_name,
            content=content,
            timestamp=timestamp if timestamp is not None else int(time.time())
        )

    async def test_deep_history_recall_chinese_two_char_word(self):
        """核心迴歸測試：中文二字詞（拉麵）必須能召回，修復前此測試會失敗"""
        await self._seed_history_message("m-ramen", "上禮拜去吃的那家拉麵店湯頭超讚")
        await self._seed_history_message("m-keyboard", "我買了新的靜音機械鍵盤")

        results = await MemoryManager.recall_deep_history(query_text="今晚要不要再去吃拉麵？")

        self.assertEqual(len(results), 1)
        self.assertEqual(str(results[0]["message_id"]), "m-ramen")
        # 回傳的必須是原文，而非索引中的 n-gram 檢索字串
        self.assertEqual(results[0]["content"], "上禮拜去吃的那家拉麵店湯頭超讚")
        self.assertNotIn(" ", results[0]["content"][:6])

    async def test_deep_history_recall_min_score_threshold(self):
        """相關性門檻：完全無關的話題不應召回任何歷史"""
        await self._seed_history_message("m-ramen", "上禮拜去吃的那家拉麵店湯頭超讚")

        results = await MemoryManager.recall_deep_history(query_text="幫我查一下明天的氣象預報")
        self.assertEqual(results, [])

        # 門檻調高後，原本命中的查詢也應被濾掉
        strict = await MemoryManager.recall_deep_history(
            query_text="今晚要不要再去吃拉麵？",
            min_score=99
        )
        self.assertEqual(strict, [])

    async def test_deep_history_recall_excludes_recent_messages(self):
        """exclude_message_ids 應正確排除短期記憶中已存在的訊息"""
        await self._seed_history_message("m-ramen-1", "上禮拜去吃的那家拉麵店湯頭超讚")
        await self._seed_history_message("m-ramen-2", "那家拉麵店的叉燒也很不錯")

        all_results = await MemoryManager.recall_deep_history(query_text="想吃拉麵")
        self.assertEqual(len(all_results), 2)

        filtered = await MemoryManager.recall_deep_history(
            query_text="想吃拉麵",
            exclude_message_ids=["m-ramen-1"]
        )
        self.assertEqual([str(r["message_id"]) for r in filtered], ["m-ramen-2"])

    async def test_deep_history_recall_respects_limit_and_ordering(self):
        """回傳則數受 limit 控制，且相關性高者優先"""
        await self._seed_history_message("m-low", "拉麵", timestamp=1000)
        await self._seed_history_message("m-high", "那家拉麵店的叉燒拉麵最好吃", timestamp=900)
        await self._seed_history_message("m-mid", "我也想吃拉麵店", timestamp=800)

        results = await MemoryManager.recall_deep_history(query_text="叉燒拉麵店", limit=2)

        self.assertEqual(len(results), 2)
        # 命中關鍵字最多的應排在最前面
        self.assertEqual(str(results[0]["message_id"]), "m-high")

    def test_history_timestamp_rendering_in_prompt(self):
        """歷史回憶應顯示發言時間；修復前因讀取不存在的 created_at key 而永遠為空"""
        ts = int(datetime(2026, 8, 20, 14, 30).timestamp())
        context_str = format_memory_context(
            current_user_name="岡部",
            user_profile=None,
            deep_history=[{"user_name": "桶子", "content": "我換了靜音紅軸", "timestamp": ts}],
            short_term_history=[]
        )

        self.assertIn("2026-08-20 14:30", context_str)
        self.assertIn("桶子: 我換了靜音紅軸", context_str)
        # 不應再出現空的方括號
        self.assertNotIn("- [] ", context_str)

        # timestamp 缺失或格式異常時應優雅退化為不顯示日期，而非拋出例外
        degraded = format_memory_context(
            current_user_name="岡部",
            user_profile=None,
            deep_history=[{"user_name": "桶子", "content": "沒有時間戳", "timestamp": None}],
            short_term_history=[]
        )
        self.assertIn("- 桶子: 沒有時間戳", degraded)

    # ==================== 19. 統一提煉入口（引擎選擇／權威名稱／白名單／標記） ====================
    def _make_extractor(self, updates, capture=None):
        """測試輔助：建立 MemoryExtractor 並以固定 updates 回應取代 Gemini 呼叫"""
        extractor = MemoryExtractor()

        async def fake_generate(prompt, **kwargs):
            if capture is not None:
                capture.append(prompt)
            return json.dumps({"updates": updates}, ensure_ascii=False)

        extractor.ai.generate_response = AsyncMock(side_effect=fake_generate)
        return extractor

    async def test_unified_entry_engine_selection(self):
        """發言者 >= 2 人走多人對話引擎，單人走單人主角引擎"""
        multi = [
            {"message_id": "e1", "channel_id": "777", "user_id": "1001", "user_name": "岡部",
             "content": "桶子昨天又通宵"},
            {"message_id": "e2", "channel_id": "777", "user_id": "2002", "user_name": "桶子",
             "content": "哪有，我在編譯程式"},
        ]
        prompts = []
        ex = self._make_extractor([], capture=prompts)
        await ex.extract_dialogue(multi, "777")
        self.assertIn("待分析的多輪交談對話記錄", prompts[0])

        single = [{"message_id": "e3", "channel_id": "777", "user_id": "1001",
                   "user_name": "岡部", "content": "我今天買了新鍵盤"}]
        prompts2 = []
        ex2 = self._make_extractor([], capture=prompts2)
        await ex2.extract_dialogue(single, "777")
        self.assertIn("當前發言者 (Speaker)", prompts2[0])

    async def test_user_name_never_taken_from_model_output(self):
        """權威名稱防線：模型回傳錯誤的 user_name 不得污染畫像"""
        await MemoryManager.update_user_profile("1001", "岡部", facts=[])
        await MemoryManager.update_user_profile("2002", "桶子", facts=[])

        messages = [
            {"message_id": "n1", "channel_id": "777", "user_id": "1001", "user_name": "岡部",
             "content": "桶子昨天又通宵"},
            {"message_id": "n2", "channel_id": "777", "user_id": "2002", "user_name": "桶子",
             "content": "哪有"},
        ]
        for m in messages:
            await MemoryManager.save_message(m["message_id"], m["channel_id"], m["user_id"],
                                             m["user_name"], m["content"])

        # 模型把兩筆的 user_name 都回成「桶子」
        ex = self._make_extractor([
            {"user_id": "1001", "user_name": "桶子", "facts": ["愛吐槽"], "remove_facts": [],
             "interaction_notes": "【核心性格】中二。", "favorability_delta": 0},
            {"user_id": "2002", "user_name": "桶子", "facts": ["技術宅"], "remove_facts": [],
             "interaction_notes": "【核心性格】駭客。", "favorability_delta": 0},
        ])
        await ex.extract_dialogue(messages, "777")

        p1 = await MemoryManager.get_user_profile("1001")
        p2 = await MemoryManager.get_user_profile("2002")
        self.assertEqual(p1["user_name"], "岡部")   # 未被改成「桶子」
        self.assertEqual(p2["user_name"], "桶子")
        # 暱稱索引未塌縮
        self.assertEqual(len(await MemoryManager.get_known_users_map()), 2)

    async def test_single_extraction_no_double_counting(self):
        """同一批訊息只提煉一次：好感度不加倍、事實熱度不灌水"""
        await MemoryManager.update_user_profile("1001", "岡部", facts=[])
        await MemoryManager.save_message("d1", "777", "1001", "岡部", "我帶了 Dr Pepper 給你")

        prompts = []
        ex = self._make_extractor([
            {"user_id": "1001", "user_name": "岡部", "facts": ["喜歡喝 Dr Pepper"],
             "remove_facts": [], "interaction_notes": "【核心性格】中二。", "favorability_delta": 2},
        ], capture=prompts)
        await ex.extract_dialogue(
            [{"message_id": "d1", "channel_id": "777", "user_id": "1001",
              "user_name": "岡部", "content": "我帶了 Dr Pepper 給你"}], "777")

        p = await MemoryManager.get_user_profile("1001")
        self.assertEqual(len(prompts), 1)                 # 只呼叫一次模型
        self.assertEqual(p["favorability"], 32)           # 30 + 2，而非 +4
        self.assertEqual(p["facts"][0]["hits"], 1)        # 未被誤判為重複確認而 +3

        # 提煉成功後必須標記 extracted
        self.assertEqual(await MemoryManager.get_unextracted_messages(), [])

    async def test_mentioned_but_silent_user_is_whitelisted(self):
        """被提及但未發言者也應在白名單內，跨使用者歸屬才成立"""
        await MemoryManager.update_user_profile("1001", "岡部", facts=[])
        await MemoryManager.update_user_profile("3003", "真由理", facts=[])

        msgs = [{"message_id": "w1", "channel_id": "999", "user_id": "1001",
                 "user_name": "岡部", "content": "真由理最近都在做 cosplay 服裝"}]
        await MemoryManager.save_message("w1", "999", "1001", "岡部", msgs[0]["content"])

        ex = self._make_extractor([
            {"user_id": "3003", "user_name": "真由理", "facts": ["在做 cosplay 服裝"],
             "remove_facts": [], "interaction_notes": "", "favorability_delta": 0},
        ])
        await ex.extract_dialogue(msgs, "999")

        p = await MemoryManager.get_user_profile("3003")
        self.assertIn("在做 cosplay 服裝", get_fact_texts(p["facts"]))

    async def test_out_of_context_user_still_rejected(self):
        """白名單放寬後，完全不在對話中的使用者仍必須被拒絕（防提示詞注入）"""
        await MemoryManager.update_user_profile("1001", "岡部", facts=[])
        await MemoryManager.update_user_profile("9999", "路人", facts=[])

        msgs = [{"message_id": "x1", "channel_id": "777", "user_id": "1001",
                 "user_name": "岡部", "content": "今天天氣真好"}]
        await MemoryManager.save_message("x1", "777", "1001", "岡部", msgs[0]["content"])

        ex = self._make_extractor([
            {"user_id": "9999", "user_name": "路人", "facts": ["是笨蛋"], "remove_facts": [],
             "interaction_notes": "", "favorability_delta": -2},
        ])
        await ex.extract_dialogue(msgs, "777")

        p = await MemoryManager.get_user_profile("9999")
        self.assertEqual(get_fact_texts(p["facts"]), [])
        self.assertEqual(p["favorability"], 30)

    async def test_failed_extraction_retried_by_sweeper(self):
        """提煉失敗不標記 extracted，交由背景撿漏重試"""
        await MemoryManager.update_user_profile("1001", "岡部", facts=[])
        await MemoryManager.save_message("s1", "777", "1001", "岡部", "這則會失敗")

        failing = MemoryExtractor()
        failing.ai.generate_response = AsyncMock(side_effect=RuntimeError("模擬 API 失敗"))
        await failing.extract_dialogue(
            [{"message_id": "s1", "channel_id": "777", "user_id": "1001",
              "user_name": "岡部", "content": "這則會失敗"}], "777")

        pending = await MemoryManager.get_unextracted_messages()
        self.assertEqual([m["message_id"] for m in pending], ["s1"])

        ex = self._make_extractor([
            {"user_id": "1001", "user_name": "岡部", "facts": ["撿漏成功"], "remove_facts": [],
             "interaction_notes": "", "favorability_delta": 0},
        ])
        self.assertEqual(await ex.sweep_unextracted(), 1)
        self.assertEqual(await MemoryManager.get_unextracted_messages(), [])

        p = await MemoryManager.get_user_profile("1001")
        self.assertIn("撿漏成功", get_fact_texts(p["facts"]))

    # ==================== 20. 暱稱文字比對的誤命中防護 (P2-1) ====================
    def test_is_matchable_name_filters_low_signal_names(self):
        """過短、停用詞、過短英數暱稱不應拿來做文字比對"""
        self.assertTrue(MemoryManager.is_matchable_name("桶子"))
        self.assertTrue(MemoryManager.is_matchable_name("真由理"))
        self.assertTrue(MemoryManager.is_matchable_name("daru"))

        self.assertFalse(MemoryManager.is_matchable_name("桶"))      # 少於 2 字
        self.assertFalse(MemoryManager.is_matchable_name("今天"))    # 停用詞
        self.assertFalse(MemoryManager.is_matchable_name("可以"))    # 停用詞
        self.assertFalse(MemoryManager.is_matchable_name("ab"))      # 純 ASCII 過短

    def test_ascii_name_matching_respects_word_boundary(self):
        """英數暱稱需以詞邊界比對，避免成為其他單字的一部分"""
        # 誤命中案例（修復前會全部命中）
        self.assertFalse(MemoryManager.name_appears_in("test", "the latest news"))
        self.assertFalse(MemoryManager.name_appears_in("test", "a contest today"))
        self.assertFalse(MemoryManager.name_appears_in("daru", "darush is here"))

        # 正常命中仍須成立
        self.assertTrue(MemoryManager.name_appears_in("test", "test 你好"))
        self.assertTrue(MemoryManager.name_appears_in("test", "hi test!"))
        self.assertTrue(MemoryManager.name_appears_in("daru", "問一下 daru 好了"))

    def test_chinese_name_matching_unchanged(self):
        """中文暱稱的命中範圍不得因本次收緊而縮小"""
        self.assertTrue(MemoryManager.name_appears_in("桶子", "桶子今天又通宵"))
        self.assertTrue(MemoryManager.name_appears_in("桶子", "我覺得桶子很誇張"))
        self.assertTrue(MemoryManager.name_appears_in("真由理", "剛剛遇到真由理"))

        # 已知限制：中文無詞邊界，短暱稱仍會命中較長的詞。
        # 此處刻意斷言現況，若日後引入分詞而改變行為，這個測試會提醒需一併更新文件。
        self.assertTrue(MemoryManager.name_appears_in("小美", "今天的小美食好吃"))

    async def test_longest_matching_name_wins(self):
        """暱稱互為子字串時只保留較長者，短名稱的主人不應被一起拉進來"""
        await MemoryManager.update_user_profile("5001", "小美", facts=[])
        await MemoryManager.update_user_profile("5002", "小美美", facts=[])

        found = await MemoryManager.resolve_mentioned_user_ids("小美美今天心情很好")
        self.assertEqual(found, ["5002"])

    async def test_stopword_named_user_not_matched_by_every_message(self):
        """暱稱恰為停用詞的使用者，不應因任意訊息含該詞就被拉進上下文"""
        await MemoryManager.update_user_profile("5003", "今天", facts=[])
        await MemoryManager.update_user_profile("5004", "桶子", facts=[])

        found = await MemoryManager.resolve_mentioned_user_ids("今天桶子有來嗎")
        self.assertEqual(found, ["5004"])

    async def test_mention_priority_ordering(self):
        """@提及排在名稱命中之前，名稱命中則長者優先"""
        await MemoryManager.update_user_profile("5005", "桶子", facts=[])
        await MemoryManager.update_user_profile("5006", "真由理", facts=[])
        await MemoryManager.update_user_profile("5007", "岡部", facts=[])

        found = await MemoryManager.resolve_mentioned_user_ids(
            "<@5007> 你看桶子跟真由理又在鬧了"
        )
        self.assertEqual(found[0], "5007")            # @提及最優先
        self.assertEqual(found[1], "5006")            # 「真由理」比「桶子」長

    # ==================== 21. 暱稱同名碰撞與權威 @提及 (P1-2) ====================
    async def test_ambiguous_nickname_is_excluded_not_guessed(self):
        """同名暱稱對應多人時整組排除，不可任選一個保留"""
        await MemoryManager.update_user_profile("6001", "小明", facts=[])
        await MemoryManager.update_user_profile("6002", "小明", facts=[])   # 同名
        await MemoryManager.update_user_profile("6003", "桶子", facts=[])

        name_map = await MemoryManager.get_known_users_map()
        self.assertNotIn("小明", name_map)      # 同名者整組排除
        self.assertEqual(name_map.get("桶子"), "6003")

        # 名稱比對不得認錯人：兩位小明都不應被拉進來
        found = await MemoryManager.resolve_mentioned_user_ids("小明今天有來嗎")
        self.assertEqual(found, [])

    async def test_ambiguous_nickname_still_reachable_via_mention(self):
        """同名者仍可透過 Discord 權威 @提及被精準識別（降級才算安全）"""
        await MemoryManager.update_user_profile("6001", "小明", facts=[])
        await MemoryManager.update_user_profile("6002", "小明", facts=[])

        found = await MemoryManager.resolve_mentioned_user_ids(
            "@小明 今天有來嗎",
            explicit_mentions=[{"user_id": "6002", "user_name": "小明"}]
        )
        self.assertEqual(found, ["6002"])       # 精準命中那一位，不是猜的

    async def test_explicit_mentions_are_the_working_path(self):
        """
        維度 A 的正確來源是 message.mentions，而非對內容做正則。
        discord.py 的 clean_content 會把 <@id> 轉寫成 @顯示名稱，正則永遠不會命中。
        """
        await MemoryManager.update_user_profile("6004", "真由理", facts=[])

        # 模擬 clean_content：原始標記已被 Discord 轉寫掉
        clean = "@真由理 你在嗎"
        self.assertEqual(re.findall(r'<@!?(\d+)>', clean), [])   # 正則確實抓不到

        found = await MemoryManager.resolve_mentioned_user_ids(
            clean, explicit_mentions=[{"user_id": "6004", "user_name": "真由理"}]
        )
        self.assertEqual(found, ["6004"])

        # fallback 仍需可用：確實含原始標記的輸入（如 Slash 指令參數）
        found_raw = await MemoryManager.resolve_mentioned_user_ids("<@6004> 你在嗎")
        self.assertEqual(found_raw, ["6004"])

    async def test_mentioned_user_without_profile_gets_authoritative_name(self):
        """首次被 @提及而尚無畫像者，建檔名稱須來自 Discord 而非模型輸出"""
        await MemoryManager.update_user_profile("6005", "岡部", facts=[])
        # 6006 尚無畫像
        await MemoryManager.save_message("p1", "777", "6005", "岡部", "@新人 你好啊")

        ex = self._make_extractor([
            {"user_id": "6006", "user_name": "模型亂取的名字", "facts": ["剛加入群組"],
             "remove_facts": [], "interaction_notes": "", "favorability_delta": 0},
        ])
        await ex.extract_dialogue([{
            "message_id": "p1", "channel_id": "777", "user_id": "6005",
            "user_name": "岡部", "content": "@新人 你好啊",
            "mentions": [{"user_id": "6006", "user_name": "新人"}]
        }], "777")

        p = await MemoryManager.get_user_profile("6006")
        self.assertIsNotNone(p)
        self.assertEqual(p["user_name"], "新人")            # 不是「模型亂取的名字」
        self.assertIn("剛加入群組", get_fact_texts(p["facts"]))

    # ==================== 22. 事實否定推翻與更正 (P1-1) ====================
    def test_has_negation_is_conservative(self):
        """否定偵測需寧可漏判也不誤判：誤判會刪掉仍然成立的事實"""
        self.assertTrue(MemoryManager.has_negation("已經不喜歡台北了"))
        self.assertTrue(MemoryManager.has_negation("沒有養寵物"))
        self.assertTrue(MemoryManager.has_negation("does not like coffee"))

        # 含「不」卻非否定的常見詞不得誤判
        self.assertFalse(MemoryManager.has_negation("覺得台北不錯"))
        self.assertFalse(MemoryManager.has_negation("差不多每天熬夜"))
        # 「非常」「未來」「無聊」刻意不列入否定標記，避免大量誤判
        self.assertFalse(MemoryManager.has_negation("非常喜歡台北"))
        self.assertFalse(MemoryManager.has_negation("很期待未來的旅行"))
        self.assertFalse(MemoryManager.has_negation("覺得很無聊"))

    def test_negated_fact_replaces_instead_of_reaffirming(self):
        """核心迴歸：否定句不得被誤判為「重複確認」而替錯誤事實加權"""
        old = [{"text": "喜歡台北", "hits": 1, "created_at": 0, "last_used_at": 0}]
        merged = MemoryManager.merge_facts(old, ["已經不喜歡台北了"], [])

        texts = get_fact_texts(merged)
        self.assertEqual(texts, ["已經不喜歡台北了"])   # 舊事實被取代
        self.assertEqual(merged[0]["hits"], 1)          # 更正不加權（修復前會變成 4）

    def test_correction_works_in_both_directions(self):
        """負 -> 正的更正同樣要生效（否定詞會破壞子字串關係，需靠主題比對）"""
        old = [{"text": "不喜歡吃辣", "hits": 5, "created_at": 0, "last_used_at": 0}]
        merged = MemoryManager.merge_facts(old, ["現在喜歡吃辣了"], [])

        self.assertEqual(get_fact_texts(merged), ["現在喜歡吃辣了"])
        self.assertEqual(merged[0]["hits"], 1)

    def test_same_polarity_still_reaffirms(self):
        """極性相同仍應維持原本的重複確認加權行為"""
        old = [{"text": "不熬夜", "hits": 1, "created_at": 0, "last_used_at": 0}]
        merged = MemoryManager.merge_facts(old, ["已經不熬夜了"], [])
        self.assertEqual(get_fact_texts(merged), ["不熬夜"])
        self.assertEqual(merged[0]["hits"], 4)

        # 「非常喜歡」是加強語氣而非否定，必須加權而不是取代
        old2 = [{"text": "喜歡台北", "hits": 1, "created_at": 0, "last_used_at": 0}]
        merged2 = MemoryManager.merge_facts(old2, ["非常喜歡台北"], [])
        self.assertEqual(get_fact_texts(merged2), ["喜歡台北"])
        self.assertEqual(merged2[0]["hits"], 4)

    def test_unrelated_facts_are_not_merged(self):
        """不同主題的事實各自保留；剝除否定詞後的短字串不得到處誤配"""
        old = [{"text": "喜歡台北", "hits": 1, "created_at": 0, "last_used_at": 0}]
        merged = MemoryManager.merge_facts(old, ["喜歡吃拉麵"], [])
        self.assertEqual(sorted(get_fact_texts(merged)), sorted(["喜歡台北", "喜歡吃拉麵"]))

        # 「不去」剝除後只剩「去」，不可誤配到「去日本玩」
        short = [{"text": "不去", "hits": 1, "created_at": 0, "last_used_at": 0}]
        merged_short = MemoryManager.merge_facts(short, ["去日本玩"], [])
        self.assertEqual(len(merged_short), 2)

    def test_no_self_reinforcing_loop_on_repeated_denial(self):
        """使用者反覆否認同一件事，不得讓錯誤事實的熱度持續累積"""
        facts = [{"text": "喜歡台北", "hits": 1, "created_at": 0, "last_used_at": 0}]
        for _ in range(3):
            facts = MemoryManager.merge_facts(facts, ["其實不喜歡台北"], [])

        self.assertEqual(get_fact_texts(facts), ["其實不喜歡台北"])
        # 第一次取代後，後兩次屬同極性的重複確認：1 -> 4 -> 7
        self.assertEqual(facts[0]["hits"], 7)

    # ==================== 23. 幽靈畫像清理（遷移 v3） ====================
    async def _insert_profile(self, user_id, user_name, fact_pairs, favorability=30):
        """測試輔助：直接寫入一筆畫像（可指定非數字 user_id 以模擬幽靈）"""
        facts_json = json.dumps(
            [{"text": t, "hits": h, "created_at": 0, "last_used_at": 0} for t, h in fact_pairs],
            ensure_ascii=False
        )
        async with get_db_connection() as db:
            await db.execute(
                "INSERT OR REPLACE INTO user_profiles "
                "(user_id, user_name, facts, interaction_notes, favorability) VALUES (?,?,?,?,?)",
                (user_id, user_name, facts_json, "", favorability)
            )
            await db.commit()

    async def test_phantom_profile_merged_into_real_user(self):
        """user_id 為名字的幽靈畫像應併入同名真人後刪除"""
        from src.friend_bot.memory.db import _cleanup_phantom_profiles

        await self._insert_profile("844811392979697675", "代謝", [("熱愛生物學", 3)], favorability=49)
        await self._insert_profile("代謝", "代謝", [("喜歡打排球", 1)])   # 幽靈

        async with get_db_connection() as db:
            await _cleanup_phantom_profiles(db)

        self.assertIsNone(await MemoryManager.get_user_profile("代謝"))   # 幽靈已刪除

        real = await MemoryManager.get_user_profile("844811392979697675")
        self.assertEqual(real["favorability"], 49)                        # 真人資料未受影響
        self.assertIn("熱愛生物學", get_fact_texts(real["facts"]))
        self.assertIn("喜歡打排球", get_fact_texts(real["facts"]))        # 幽靈事實已併入

        # 同名碰撞消失，該名字可正常解析到真人
        self.assertEqual((await MemoryManager.get_known_users_map()).get("代謝"),
                         "844811392979697675")

    async def test_phantom_without_unique_match_is_deleted_not_guessed(self):
        """找不到唯一對應真人的幽靈直接刪除，不可猜測歸屬"""
        from src.friend_bot.memory.db import _cleanup_phantom_profiles

        await self._insert_profile("111111111111111111", "重複名", [("甲的事實", 1)])
        await self._insert_profile("222222222222222222", "重複名", [("乙的事實", 1)])
        await self._insert_profile("重複名", "重複名", [("來歷不明的事實", 1)])   # 幽靈

        async with get_db_connection() as db:
            await _cleanup_phantom_profiles(db)

        self.assertIsNone(await MemoryManager.get_user_profile("重複名"))
        # 兩位真人都不該被塞入來歷不明的事實
        for uid in ("111111111111111111", "222222222222222222"):
            texts = get_fact_texts((await MemoryManager.get_user_profile(uid))["facts"])
            self.assertNotIn("來歷不明的事實", texts)

    async def test_real_profiles_are_never_touched_by_cleanup(self):
        """沒有幽靈時清理不得動到任何資料"""
        from src.friend_bot.memory.db import _cleanup_phantom_profiles

        await self._insert_profile("555738929584930868", "感應與運動", [("愛打球", 5)], favorability=48)

        async with get_db_connection() as db:
            await _cleanup_phantom_profiles(db)

        p = await MemoryManager.get_user_profile("555738929584930868")
        self.assertEqual(p["favorability"], 48)
        self.assertEqual(get_fact_texts(p["facts"]), ["愛打球"])
        self.assertEqual(p["facts"][0]["hits"], 5)

    # ==================== 24. 別名系統 (Alias) ====================
    async def test_alias_add_list_remove(self):
        """別名基本流程：新增後可被名稱比對命中，移除後失效"""
        await MemoryManager.update_user_profile("7001", "daru_1024", facts=[])

        ok, _ = await MemoryManager.add_alias("7001", "桶子", source="command", by=["7001"])
        self.assertTrue(ok)

        # 別名進入名稱索引，可解析出該使用者
        self.assertEqual((await MemoryManager.get_known_users_map()).get("桶子"), "7001")
        self.assertEqual(await MemoryManager.resolve_mentioned_user_ids("桶子今天又通宵"), ["7001"])

        # 來源記錄可稽核
        aliases = await MemoryManager.get_user_aliases("7001")
        self.assertEqual(aliases[0]["alias"], "桶子")
        self.assertEqual(aliases[0]["source"], "command")
        self.assertEqual(aliases[0]["by"], ["7001"])

        ok, _ = await MemoryManager.remove_alias("7001", "桶子")
        self.assertTrue(ok)
        self.assertEqual(await MemoryManager.resolve_mentioned_user_ids("桶子今天又通宵"), [])

    async def test_alias_rejects_impersonation_and_bad_names(self):
        """別名的四道校驗：碰撞、重複、低鑑別度、自身顯示名稱"""
        await MemoryManager.update_user_profile("7001", "岡部", facts=[])
        await MemoryManager.update_user_profile("7002", "桶子", facts=[])

        # 1. 不得使用他人的顯示名稱（這道擋掉冒名）
        ok, reason = await MemoryManager.add_alias("7001", "桶子")
        self.assertFalse(ok)
        self.assertIn("已被其他使用者使用", reason)

        # 2. 不得使用他人的既有別名
        await MemoryManager.add_alias("7002", "阿桶")
        ok, _ = await MemoryManager.add_alias("7001", "阿桶")
        self.assertFalse(ok)

        # 3. 低鑑別度的名稱一律拒絕
        for bad in ("今天", "可", "ab"):
            ok, _ = await MemoryManager.add_alias("7001", bad)
            self.assertFalse(ok, f"「{bad}」不應被接受")

        # 4. 與自己的顯示名稱相同屬多餘
        ok, reason = await MemoryManager.add_alias("7001", "岡部")
        self.assertFalse(ok)
        self.assertIn("顯示名稱相同", reason)

    async def test_alias_respects_configured_cap(self):
        """達數量上限時拒絕新增，且不得自動淘汰既有別名"""
        await MemoryManager.update_user_profile("7001", "岡部", facts=[])

        for name in ("鳳凰院", "凶真", "中二病"):
            ok, _ = await MemoryManager.add_alias("7001", name, max_aliases=3)
            self.assertTrue(ok)

        ok, reason = await MemoryManager.add_alias("7001", "狂氣科學家", max_aliases=3)
        self.assertFalse(ok)
        self.assertIn("上限", reason)

        # 既有別名必須完好保留
        self.assertEqual(
            sorted(a["alias"] for a in await MemoryManager.get_user_aliases("7001")),
            sorted(["鳳凰院", "凶真", "中二病"])
        )

    async def test_alias_collision_disables_matching_for_both(self):
        """別名與他人顯示名稱撞名時，該名稱一律停用比對（沿用既有碰撞規則）"""
        await MemoryManager.update_user_profile("7001", "岡部", facts=[])
        await MemoryManager.update_user_profile("7002", "桶子", facts=[])

        # 繞過 add_alias 的校驗直接寫入，模擬「先設別名、之後有人改成同名」的競態
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE user_profiles SET aliases = ? WHERE user_id = ?",
                (json.dumps([{"alias": "桶子", "source": "command", "by": [],
                              "channel_id": "", "message_id": "", "at": 0}],
                            ensure_ascii=False), "7001")
            )
            await db.commit()

        # 「桶子」同時指向兩人 -> 整組排除，不猜
        self.assertNotIn("桶子", await MemoryManager.get_known_users_map())
        self.assertEqual(await MemoryManager.resolve_mentioned_user_ids("桶子在嗎"), [])

    async def test_alias_learning_only_for_users_in_context(self):
        """提煉學到的別名只能歸給本次對話上下文內的人（沿用 allowed_uids 白名單）"""
        await MemoryManager.update_user_profile("7001", "岡部", facts=[])
        await MemoryManager.update_user_profile("7002", "daru_1024", facts=[])
        await MemoryManager.update_user_profile("7003", "路人", facts=[])

        msgs = [
            {"message_id": "a1", "channel_id": "777", "user_id": "7001",
             "user_name": "岡部", "content": "桶子你昨天又通宵喔"},
            {"message_id": "a2", "channel_id": "777", "user_id": "7002",
             "user_name": "daru_1024", "content": "哪有"},
        ]
        for m in msgs:
            await MemoryManager.save_message(m["message_id"], m["channel_id"],
                                             m["user_id"], m["user_name"], m["content"])

        # 模型同時替「在場的 7002」與「不在場的 7003」提議別名
        ex = self._make_extractor([
            {"user_id": "7002", "user_name": "daru_1024", "facts": [], "remove_facts": [],
             "interaction_notes": "", "favorability_delta": 0, "aliases": ["桶子"]},
            {"user_id": "7003", "user_name": "路人", "facts": [], "remove_facts": [],
             "interaction_notes": "", "favorability_delta": 0, "aliases": ["小路"]},
        ])
        await ex.extract_dialogue(msgs, "777")

        # 在場者學到別名，並記錄來源
        learned = await MemoryManager.get_user_aliases("7002")
        self.assertEqual([a["alias"] for a in learned], ["桶子"])
        self.assertEqual(learned[0]["source"], "extraction")
        self.assertEqual(learned[0]["channel_id"], "777")
        self.assertIn("7001", learned[0]["by"])

        # 不在場者的整筆更新（含別名）被白名單拒絕
        self.assertEqual(await MemoryManager.get_user_aliases("7003"), [])

    def test_alias_mapping_is_visible_to_the_model(self):
        """
        別名必須寫進 prompt。

        別名只用於「解析出該載入誰的畫像」是不夠的——若 prompt 只顯示 Discord 顯示名稱，
        模型得自己猜對話中的綽號指的是誰，猜錯時事實會被歸給錯的人或整個漏掉。
        """
        alias_rec = [{"alias": "桶子", "source": "command", "by": [],
                      "channel_id": "", "message_id": "", "at": 0}]
        others = [{
            "user_id": "2002", "user_name": "daru_1024", "facts": ["常熬夜"],
            "interaction_notes": "技術宅", "aliases": alias_rec,
            "relationship_tier": "familiar"
        }]
        speaker = {"user_id": "1001", "user_name": "岡部", "facts": [],
                   "interaction_notes": "", "favorability": 30, "aliases": []}

        # 提煉端：單人引擎與多人引擎
        single = build_multi_entity_extraction_prompt(speaker, others, ["桶子昨天買了新鍵盤"])
        self.assertIn("daru_1024】（大家也叫他：桶子）", single)

        batch = build_batch_dialogue_extraction_prompt(
            [{"user_name": "岡部", "user_id": "1001", "content": "桶子買鍵盤"}], others)
        self.assertIn("daru_1024】（大家也叫他：桶子）", batch)

        # 回覆端：發言者本人與其他群友
        context = format_memory_context(
            current_user_name="岡部",
            user_profile={"user_name": "岡部", "facts": [], "interaction_notes": "",
                          "relationship_tier": "familiar",
                          "aliases": [{"alias": "鳳凰院", "source": "command", "by": [],
                                       "channel_id": "", "message_id": "", "at": 0}]},
            deep_history=[], short_term_history=[], other_user_profiles=others
        )
        self.assertIn("岡部（大家也叫他：鳳凰院）", context)
        self.assertIn("daru_1024（大家也叫他：桶子）", context)

        # 沒有別名的人不應出現多餘的括號
        no_alias = format_memory_context(
            current_user_name="岡部",
            user_profile={"user_name": "岡部", "facts": [], "interaction_notes": "",
                          "relationship_tier": "familiar", "aliases": []},
            deep_history=[], short_term_history=[]
        )
        self.assertNotIn("大家也叫他", no_alias)


if __name__ == "__main__":
    unittest.main()
