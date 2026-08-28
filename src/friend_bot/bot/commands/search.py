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
    TYPING_DELAY_RANGE
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

            calendar_summary = await CalendarManager.get_user_schedule_summary(user_id)
            deep_history = []
            if ENABLE_HISTORY_RECALL:
                deep_history = await MemoryManager.recall_deep_history(query_text=query)

            memory_context = format_memory_context(
                current_user_name=user_name,
                user_profile=current_user_profile,
                deep_history=deep_history,
                short_term_history=short_term,
                calendar_summary=calendar_summary,
                other_user_profiles=other_user_profiles
            )

            prompt = f"""{memory_context}

【當前用戶最新發言】:
{user_name}: 【用戶明確要求強制聯網搜尋最新資料】：{query}

【特別指示】：
請務必使用 search_web 搜尋即時資料，並在獲取搜尋結果後，以你的角色風格（傲嬌/理性的克莉絲）**詳細綜合整理並具體回覆搜尋到的最新資訊與實質內容**給 {user_name}："""

            response_text = await self.gemini.generate_response(prompt=prompt)
            chunks = split_message(response_text)

            if chunks:
                first_msg = await interaction.followup.send(chunks[0])
                current_ts = int(time.time())
                
                await MemoryManager.save_message(
                    message_id=str(interaction.id),
                    channel_id=channel_id,
                    user_id=user_id,
                    user_name=user_name,
                    content=query,
                    has_image=False,
                    is_bot=False,
                    timestamp=current_ts,
                    extracted=True
                )

                sent_chunks = [chunks[0]]
                if len(chunks) > 1 and interaction.channel:
                    for idx, chunk in enumerate(chunks[1:], start=1):
                        min_delay, max_delay = TYPING_DELAY_RANGE
                        calc_delay = min(max_delay, max(min_delay, len(chunk) * 0.015))
                        actual_delay = max(min_delay, calc_delay + random.uniform(-0.1, 0.2))
                        if SHOW_TYPING:
                            async with interaction.channel.typing():
                                await asyncio.sleep(actual_delay)
                        else:
                            await asyncio.sleep(actual_delay)
                        sent_msg = await interaction.channel.send(chunk)
                        sent_chunks.append(chunk)

                await MemoryManager.save_message(
                    message_id=str(first_msg.id if hasattr(first_msg, 'id') else interaction.id),
                    channel_id=channel_id,
                    user_id=str(self.user.id),
                    user_name=BOT_NAME,
                    content="\n".join(sent_chunks),
                    has_image=False,
                    is_bot=True,
                    timestamp=int(time.time()),
                    extracted=True
                )

                asyncio.create_task(
                    self.memory_extractor.extract_and_update(
                        user_id=user_id,
                        user_name=user_name,
                        recent_messages=[query],
                        other_users=other_user_profiles
                    )
                )
