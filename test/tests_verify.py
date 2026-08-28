import unittest
import asyncio
import os
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
    parse_burst_reply_response
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

        # 測試標籤替換
        raw_text = "誰是助手啊！還有，我哪有開心？[emotion:tsundere]"
        replaced = EmotionReplacer.replace_emotion_tags(raw_text)
        self.assertNotIn("[emotion:tsundere]", replaced)
        self.assertIn("誰是助手啊！還有，我哪有開心？", replaced)

        # 測試別名映射 (如 [emotion:shy], [emotion:cry], [emotion:gloom])
        alias_text = "別看我啦……[emotion:shy] 嗚嗚……[emotion:cry] 好累喔[emotion:gloom]"
        replaced_alias = EmotionReplacer.replace_emotion_tags(alias_text)
        self.assertNotIn("[emotion:shy]", replaced_alias)
        self.assertNotIn("[emotion:cry]", replaced_alias)
        self.assertNotIn("[emotion:gloom]", replaced_alias)

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


if __name__ == "__main__":
    unittest.main()
