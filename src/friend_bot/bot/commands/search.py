import discord
from discord import app_commands
import logging
import asyncio
import time
import random
from typing import Optional

from src.friend_bot.core.config import (
    BOT_NAME,
    ENABLE_HISTORY_RECALL,
    SHOW_TYPING,
    TYPING_DELAY_RANGE,
    FACTS_SPEAKER_MAX_TOTAL,
    FACTS_SPEAKER_HEAT_LIMIT,
    FACTS_SPEAKER_RECENT_LIMIT,
    FACTS_OTHERS_MAX_TOTAL,
    FACTS_OTHERS_HEAT_LIMIT,
    FACTS_OTHERS_RECENT_LIMIT
)
from src.friend_bot.memory import MemoryManager
from src.friend_bot.bot.utils.calendar import CalendarManager
from src.friend_bot.ai.prompts import format_memory_context
from src.friend_bot.bot.handlers import split_message

logger = logging.getLogger("friend_bot.commands.search")


class SearchCommandsMixin:
    """強制線上聯網搜尋指令 Mixin（包含 /kurisu-search）"""

    def register_search_commands(self):
        """註冊 /kurisu-search 指令至 self.tree"""

        @self.tree.command(name="kurisu-search", description="【強制線上搜尋】聯網檢索即時新聞、時事、天氣與最新資料")
        @app_commands.describe(query="你想搜尋的內容或問題 (例如：台北現在天氣、2026最新科技新聞)")
        async def kurisu_search_command(interaction: discord.Interaction, query: str):
            query = query.strip()
            if not query:
                await interaction.response.send_message("（請輸入要搜尋的內容喔！）", ephemeral=True)
                return

            await interaction.response.defer(thinking=True)
            logger.info(f"收到來自 [{interaction.user.display_name}] 的 /kurisu-search 指令: 「{query}」")

            channel_id = str(interaction.channel_id) if interaction.channel_id else ""
            user_id = str(interaction.user.id)
            user_name = interaction.user.display_name

            short_term = await MemoryManager.get_short_term_context(channel_id) if channel_id else []
            
            # 多人記憶解析 (A + B + C 混合方案)
            current_user_profile, other_user_profiles = await MemoryManager.resolve_multi_user_profiles(
                current_user_id=user_id,
                content=query,
                short_term_history=short_term,
                max_others=3
            )

            # 三軌混合事實檢索 (Heat + RAG + Recent)
            if current_user_profile:
                filtered_facts, hit_facts = MemoryManager.filter_facts_three_tracks(
                    facts_data=current_user_profile.get("facts", []),
                    query_text=query,
                    max_total=FACTS_SPEAKER_MAX_TOTAL,
                    heat_limit=FACTS_SPEAKER_HEAT_LIMIT,
                    recent_limit=FACTS_SPEAKER_RECENT_LIMIT
                )
                current_user_profile["facts"] = filtered_facts
                if hit_facts:
                    asyncio.create_task(MemoryManager.record_fact_hits(user_id, hit_facts))

            if other_user_profiles:
                for o_prof in other_user_profiles:
                    o_uid = str(o_prof.get("user_id", ""))
                    o_filtered, o_hits = MemoryManager.filter_facts_three_tracks(
                        facts_data=o_prof.get("facts", []),
                        query_text=query,
                        max_total=FACTS_OTHERS_MAX_TOTAL,
                        heat_limit=FACTS_OTHERS_HEAT_LIMIT,
                        recent_limit=FACTS_OTHERS_RECENT_LIMIT
                    )
                    o_prof["facts"] = o_filtered
                    if o_hits and o_uid:
                        asyncio.create_task(MemoryManager.record_fact_hits(o_uid, o_hits))

            # 行事曆排程摘要
            calendar_summary = await CalendarManager.get_user_schedule_summary(user_id)

            # 跨頻道深度回憶
            recent_msg_ids = [str(m.get("message_id")) for m in short_term if m.get("message_id")]
            deep_history = []
            if ENABLE_HISTORY_RECALL:
                deep_history = await MemoryManager.recall_deep_history(
                    query_text=query,
                    exclude_message_ids=recent_msg_ids
                )

            # 組裝上下文與 Prompt
            memory_context = format_memory_context(
                current_user_name=user_name,
                user_profile=current_user_profile,
                deep_history=deep_history,
                short_term_history=short_term,
                calendar_summary=calendar_summary,
                other_user_profiles=other_user_profiles
            )

            prompt = f"""{memory_context}

【用戶聯網查詢請求】:
{user_name}: {query}

請以傲嬌幽默的天才科學家風格，結合聯網檢索工具為 {user_name} 查證並給出清晰有趣的解答："""

            try:
                # 調用 Gemini 聯網搜尋（強制 enable_tools=True）
                response_text = await self.gemini.generate_response(
                    prompt=prompt,
                    enable_tools=True
                )

                chunks = split_message(response_text)
                
                # 第一則使用 Followup 回覆 Interaction
                await interaction.followup.send(chunks[0])

                # 後續氣泡依序透過頻道發送
                for chunk in chunks[1:]:
                    min_delay, max_delay = TYPING_DELAY_RANGE
                    calc_delay = min(max_delay, max(min_delay, len(chunk) * 0.015))
                    actual_delay = max(min_delay, calc_delay + random.uniform(-0.1, 0.2))

                    if SHOW_TYPING and interaction.channel:
                        async with interaction.channel.typing():
                            await asyncio.sleep(actual_delay)
                    else:
                        await asyncio.sleep(actual_delay)

                    if interaction.channel:
                        await interaction.channel.send(chunk)

                # 儲存對話紀錄
                if channel_id:
                    await MemoryManager.save_message(
                        message_id=str(interaction.id),
                        channel_id=channel_id,
                        user_id=user_id,
                        user_name=user_name,
                        content=f"/kurisu-search {query}",
                        has_image=False,
                        is_bot=False,
                        timestamp=int(time.time()),
                        extracted=False
                    )

                logger.info(f"成功完成 /kurisu-search 回覆 (字數: {len(response_text)}, 氣泡數: {len(chunks)})")

            except Exception as e:
                logger.error(f"/kurisu-search 指令處理失敗: {e}", exc_info=True)
                await interaction.followup.send("（糟糕……聯網模組在接收外界訊號時發生干擾，請稍後再試一次吧！）")
