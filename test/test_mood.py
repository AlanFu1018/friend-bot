import unittest
import os
import sys
import tempfile
from unittest.mock import patch

# 加入根目錄至 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.friend_bot.core import emotion as emotion_module
from src.friend_bot.core.emotion import EmotionReplacer
from src.friend_bot.core.mood import MoodTracker, MoodState, INERTIA_MIN_INTENSITY
from src.friend_bot.core.config import MOOD_HALF_LIFE_MINUTES, MOOD_THRESHOLD
from src.friend_bot.memory import db as db_module
from src.friend_bot.memory.mood_store import ensure_mood_loaded, persist_mood
from src.friend_bot.ai.prompts import format_memory_context

CH = "test-channel"
T0 = 1_000_000.0


def reset_state():
    MoodTracker._states.clear()
    EmotionReplacer._recent_history.clear()
    EmotionReplacer._rare_last_used.clear()
    EmotionReplacer.load_kaomoji()


class TestEmotionTags(unittest.TestCase):
    """標籤解析（Phase 0 修正）"""

    def setUp(self):
        reset_state()

    def test_both_bracket_styles_and_case(self):
        for raw in ["嗯 [emotion:happy] 好", "哼 (emotion:angry)", "X [EMOTION:Sad] y"]:
            out = EmotionReplacer.replace_emotion_tags(raw)
            self.assertNotIn("emotion:", out.lower(), raw)
            self.assertIn("`", out, raw)

    def test_pipe_is_not_a_bracket(self):
        self.assertEqual(EmotionReplacer.replace_emotion_tags("a |emotion:sad| b"), "a |emotion:sad| b")

    def test_indentation_preserved(self):
        out = EmotionReplacer.replace_emotion_tags("def f():\n    return 1 [emotion:soft]")
        self.assertTrue(out.startswith("def f():\n    return 1 `"))

    def test_no_double_space_and_line_start(self):
        out = EmotionReplacer.replace_emotion_tags("好  [emotion:soft] 喔")
        self.assertNotIn("  ", out)
        out2 = EmotionReplacer.replace_emotion_tags("第一行\n[emotion:soft] 第二行")
        self.assertIn("\n`", out2)

    def test_unknown_category_removed(self):
        self.assertEqual(EmotionReplacer.replace_emotion_tags("哈 [emotion:nonsense]"), "哈")

    def test_alias_shares_history_with_canonical(self):
        self.assertEqual(EmotionReplacer.resolve_category("Happy"), "soft")
        EmotionReplacer.get_random_kaomoji("happy")
        EmotionReplacer.get_random_kaomoji("smile")
        self.assertEqual(set(EmotionReplacer._recent_history), {"soft"})


class TestMoodTracker(unittest.TestCase):
    """心情累積、衰減、慣性與描述"""

    def setUp(self):
        reset_state()

    def test_record_and_threshold(self):
        self.assertIsNone(MoodTracker.current(CH, now=T0))
        MoodTracker.record(CH, "angry", "桶子", now=T0)
        cat, intensity = MoodTracker.current(CH, now=T0)
        self.assertEqual(cat, "angry")
        self.assertGreaterEqual(intensity, MOOD_THRESHOLD)

    def test_thinking_is_not_a_mood(self):
        self.assertFalse(MoodTracker.record(CH, "thinking", now=T0))
        self.assertIsNone(MoodTracker.current(CH, now=T0))

    def test_decay_by_half_life(self):
        MoodTracker.record(CH, "angry", now=T0)
        MoodTracker.record(CH, "angry", now=T0)
        _, before = MoodTracker.current(CH, now=T0)
        _, after = MoodTracker.current(CH, now=T0 + MOOD_HALF_LIFE_MINUTES * 60)
        self.assertAlmostEqual(after, before / 2, places=5)
        # 很久之後回到平靜
        self.assertIsNone(MoodTracker.current(CH, now=T0 + MOOD_HALF_LIFE_MINUTES * 60 * 10))

    def test_new_emotion_dampens_others(self):
        for _ in range(3):
            MoodTracker.record(CH, "angry", now=T0)
        angry_before = MoodTracker.get_state(CH).scores["angry"]
        MoodTracker.record(CH, "soft", now=T0)
        self.assertLess(MoodTracker.get_state(CH).scores["angry"], angry_before)

    def test_inertia_only_when_strong(self):
        transitions = {"angry": {"soft": "tsundere"}}
        MoodTracker.record(CH, "angry", now=T0)
        # 只有一次，強度不足以觸發慣性
        self.assertLess(MoodTracker.current(CH, now=T0)[1], INERTIA_MIN_INTENSITY)
        self.assertEqual(MoodTracker.apply_inertia(CH, "soft", transitions, now=T0), "soft")
        MoodTracker.record(CH, "angry", now=T0)
        self.assertEqual(MoodTracker.apply_inertia(CH, "soft", transitions, now=T0), "tsundere")
        # 不在規則裡的類別不受影響
        self.assertEqual(MoodTracker.apply_inertia(CH, "sad", transitions, now=T0), "sad")

    def test_describe(self):
        self.assertEqual(MoodTracker.describe(CH, now=T0), "")
        MoodTracker.record(CH, "angry", "桶子", now=T0)
        desc = MoodTracker.describe(CH, now=T0)
        self.assertIn("不爽", desc)
        self.assertIn("桶子", desc)
        self.assertFalse(any(ch.isdigit() for ch in desc))

    def test_replace_records_mood_only_when_channel_given(self):
        EmotionReplacer.replace_emotion_tags("哼 [emotion:mad]")
        self.assertIsNone(MoodTracker.get_state(CH))
        EmotionReplacer.replace_emotion_tags("哼 [emotion:mad]", mood_channel_id=CH, cause_user="岡部")
        state = MoodTracker.get_state(CH)
        self.assertIn("angry", state.scores)  # 別名被正規化後才記錄
        self.assertEqual(state.cause_user, "岡部")

    def test_inertia_changes_pool_in_render(self):
        for _ in range(3):
            MoodTracker.record(CH, "angry")
        tsundere_pool = {k.replace("`", "´") for k in EmotionReplacer._kaomoji_map["tsundere"]}
        with patch.object(emotion_module, "ENABLE_RARE_KAOMOJI", False):
            out = EmotionReplacer.replace_emotion_tags("[emotion:soft]", mood_channel_id=CH)
        self.assertIn(out[1:-1], tsundere_pool)  # 去掉外層反引號


class TestRareKaomoji(unittest.TestCase):

    def setUp(self):
        reset_state()

    def test_rare_pool_loaded(self):
        self.assertIn("tsundere", EmotionReplacer._rare_map)
        self.assertIn("angry", EmotionReplacer._mood_transitions)

    def test_rare_hit_and_cooldown(self):
        rare_pool = EmotionReplacer._rare_map["angry"]
        with patch.object(emotion_module, "RARE_KAOMOJI_CHANCE", 1.0):
            picks = [EmotionReplacer.get_random_kaomoji("angry") for _ in range(len(rare_pool) + 2)]
        # 前 len(rare_pool) 次都是稀有款且互不重複；之後全在冷卻中，退回一般池
        self.assertEqual(sorted(picks[:len(rare_pool)]), sorted(rare_pool))
        self.assertTrue(all(p not in rare_pool for p in picks[len(rare_pool):]))

    def test_rare_disabled(self):
        with patch.object(emotion_module, "RARE_KAOMOJI_CHANCE", 1.0), \
             patch.object(emotion_module, "ENABLE_RARE_KAOMOJI", False):
            k = EmotionReplacer.get_random_kaomoji("angry")
        self.assertNotIn(k, EmotionReplacer._rare_map["angry"])


class TestMoodPersistence(unittest.IsolatedAsyncioTestCase):
    """心情存進資料庫、重啟（清空快取）後讀回"""

    async def asyncSetUp(self):
        reset_state()
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = patch.object(db_module, "DB_PATH", os.path.join(self._tmp.name, "mood_test.db"))
        self._patch.start()
        await db_module.init_db()

    async def asyncTearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    async def test_roundtrip(self):
        MoodTracker.record(CH, "sad", "助手")
        MoodTracker.record(CH, "sad", "助手")
        await persist_mood(CH)
        saved = MoodTracker.get_state(CH)

        MoodTracker._states.clear()  # 模擬重啟
        await ensure_mood_loaded(CH)
        loaded = MoodTracker.get_state(CH)
        self.assertEqual(loaded.cause_user, "助手")
        self.assertAlmostEqual(loaded.scores["sad"], saved.scores["sad"])
        self.assertAlmostEqual(loaded.updated_at, saved.updated_at)

    async def test_missing_channel_starts_calm(self):
        await ensure_mood_loaded("never-seen")
        self.assertIsNone(MoodTracker.current("never-seen"))


class TestMoodPrompt(unittest.TestCase):

    def test_mood_block_before_short_term(self):
        ctx = format_memory_context(
            current_user_name="岡部",
            user_profile=None,
            deep_history=[],
            short_term_history=[{"user_name": "岡部", "content": "嗨"}],
            mood_description="你現在明顯不爽。"
        )
        self.assertIn("【你目前的心情】", ctx)
        self.assertLess(ctx.index("【你目前的心情】"), ctx.index("【近期頻道對話紀錄】"))

    def test_no_block_when_calm(self):
        ctx = format_memory_context("岡部", None, [], [], mood_description="")
        self.assertNotIn("【你目前的心情】", ctx)


if __name__ == "__main__":
    unittest.main()
