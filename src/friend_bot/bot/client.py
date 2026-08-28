import discord
from discord import app_commands
import logging
import asyncio
import time
import random
from typing import Optional, List, Dict, Any
from src.friend_bot.core.config import (
    BOT_NAME,
    ENABLE_HISTORY_RECALL,
    SHOW_TYPING,
    TYPING_DELAY_RANGE,
    REPLY_CHANNEL_IDS,
    LISTEN_CHANNEL_IDS,
    CALENDAR_WEBHOOK_URL,
    ENABLE_FAVORABILITY,
    DEFAULT_FAVORABILITY,
    DAILY_GAIN_LIMIT,
    ENABLE_BURST_REPLY,
    BURST_WINDOW_SECONDS,
    BURST_MIN_USER_COUNT,
    BURST_MAX_MESSAGES
)
from src.friend_bot.memory import MemoryManager
from src.friend_bot.bot.utils.alarm import AlarmManager, AlarmScheduler, parse_alarm_time
from src.friend_bot.bot.utils.calendar import CalendarManager, CalendarScheduler, parse_calendar_time
from src.friend_bot.bot.utils.burst import BurstBufferManager
from src.friend_bot.ai import GeminiClient, MemoryExtractor
from src.friend_bot.ai.prompts import (
    format_memory_context,
    build_burst_dialogue_prompt,
    parse_burst_reply_response
)
from src.friend_bot.bot.handlers import download_image_attachments, split_message

logger = logging.getLogger("friend_bot.bot")

TIER_NAME_MAP = {
    "stranger": "Tier 1: 陌生警戒 (Stranger)",
    "familiar": "Tier 2: 熟識群友 (Familiar)",
    "trusted": "Tier 3: 實驗室夥伴 (Labmem Partner)",
    "cherished": "Tier 4: 靈魂共鳴 (Steins;Gate Bond)"
}

def render_favorability_bar(score: int, total: int = 100, bar_length: int = 10) -> str:
    """產生好感度視覺化進度條 [██████░░░░]"""
    clamped_score = max(0, min(total, score))
    filled = int(round((clamped_score / total) * bar_length))
    filled = max(0, min(bar_length, filled))
    return "█" * filled + "░" * (bar_length - filled)

class FriendBotClient(discord.Client):
    """Discord Bot 客戶端（支援 /kurisu- 原生斜線指令、定時鬧鐘、Webhook 行事曆、多人群聊短時熱絡聚合與動態引用回覆）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.gemini = GeminiClient()
        self.memory_extractor = MemoryExtractor()
        self.alarm_scheduler = AlarmScheduler(self)
        self.calendar_scheduler = CalendarScheduler(self)
        self.burst_manager = BurstBufferManager(
            window_seconds=BURST_WINDOW_SECONDS,
            min_user_count=BURST_MIN_USER_COUNT,
            max_burst_messages=BURST_MAX_MESSAGES
        )

    async def setup_hook(self):
        """註冊 Discord 原生 /kurisu- 開頭的 Slash 指令"""
        
        # 1. /kurisu-help 指令
        @self.tree.command(name="kurisu-help", description=f"查看 {BOT_NAME} 的所有指令功能說明指南")
        async def kurisu_help_command(interaction: discord.Interaction):
            embed = self._create_help_embed()
            await interaction.response.send_message(embed=embed)
            logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-help 展示說明手冊卡片")

        # 2. /kurisu-search 指令
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

        # 3. /kurisu-profile 指令
        @self.tree.command(name="kurisu-profile", description="查看自己、指定群友的記憶特徵畫像與好感度，或機器人自身簡介")
        @app_commands.describe(user="選擇要查詢畫像的群友或克莉絲的簡介（留空代表查詢自己）")
        async def kurisu_profile_command(interaction: discord.Interaction, user: Optional[discord.User] = None):
            target_user = user or interaction.user
            target_user_id = str(target_user.id)
            target_user_name = target_user.display_name

            # 檢查是否查詢機器人自身（包含 @機器人、自身 user_id 或 bot 自身）
            is_bot_self = False
            if self.user and target_user.id == self.user.id:
                is_bot_self = True
            elif target_user == self.user:
                is_bot_self = True
            elif getattr(target_user, "bot", False) and (
                target_user.name == BOT_NAME or target_user.display_name == BOT_NAME or (self.user and target_user.name == self.user.name)
            ):
                is_bot_self = True

            if is_bot_self:
                embed = self._create_bot_profile_embed()
                await interaction.response.send_message(embed=embed)
                logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-profile 顯示 {BOT_NAME} 自身的個人檔案簡介")
                return

            profile = await MemoryManager.get_user_profile(target_user_id)
            embed = self._create_profile_embed(target_user_id, target_user_name, profile, target_user)
            await interaction.response.send_message(embed=embed)
            logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-profile 顯示 [{target_user_name}] 的畫像與好感度")

        # 4. 【定時鬧鐘模組】/kurisu-alarm-set 指令
        @self.tree.command(name="kurisu-alarm-set", description="【設定定時提醒鬧鐘】紅莉栖會在指定時間以傲嬌風格發送醒目標題提醒")
        @app_commands.describe(
            time="提醒時間 (格式: y/m/d/h/m，例如 2026/8/27/15/30、8/27/15/30 或 15:30)",
            content="要提醒的具體事項內容 (例如: 搶特展門票、吃藥、開會)"
        )
        async def kurisu_alarm_set_command(interaction: discord.Interaction, time: str, content: str):
            content = content.strip()
            if not content:
                await interaction.response.send_message("（提醒內容不能是空的啦！請告訴我要提醒你什麼事。）", ephemeral=True)
                return

            try:
                target_dt, target_ts, date_str, time_str, formatted_time_str = parse_alarm_time(time)
            except ValueError as e:
                embed_err = discord.Embed(
                    title="⚠️【鬧鐘時間設定失敗】",
                    description=f"哼，連時間格式都弄錯了！\n\n{str(e)}",
                    color=0xE74C3C
                )
                embed_err.set_footer(text="格式範例：2026/8/27/15/30、8/27/15/30 或 15:30")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            channel_id = str(interaction.channel_id) if interaction.channel_id else ""
            user_id = str(interaction.user.id)
            user_name = interaction.user.display_name

            alarm_id = await AlarmManager.create_alarm(
                channel_id=channel_id,
                user_id=user_id,
                user_name=user_name,
                target_timestamp=target_ts,
                target_time_str=formatted_time_str,
                content=content
            )

            embed = discord.Embed(
                title="⏰【紅莉栖的鬧鐘已設定】",
                description=(
                    f"哼，既然你特地拜託我了，那我就幫你記下來吧！\n"
                    f"可別誤會了，我才不是特別關心你，只是實驗室助手對時間很嚴謹而已！"
                ),
                color=0x2ECC71
            )
            embed.add_field(name="📌 提醒事項", value=f"```{content}```", inline=False)
            embed.add_field(name="⏳ 預定提醒時間", value=f"`{formatted_time_str}`", inline=True)
            embed.add_field(name="🔢 鬧鐘編號", value=f"`#{alarm_id}`", inline=True)
            embed.set_footer(text="時間到了會在頻道發送提醒 • 可使用 /kurisu-alarm-list 查看")

            if self.user and self.user.display_avatar:
                embed.set_thumbnail(url=self.user.display_avatar.url)

            await interaction.response.send_message(embed=embed)
            logger.info(f"已為 [{user_name}] 設定鬧鐘 ID:{alarm_id}，時間:{formatted_time_str}，內容:「{content}」")

        # 5. 【定時鬧鐘模組】/kurisu-alarm-list 指令
        @self.tree.command(name="kurisu-alarm-list", description="查看自己名下所有待觸發的定時鬧鐘清單")
        async def kurisu_alarm_list_command(interaction: discord.Interaction):
            user_id = str(interaction.user.id)
            alarms = await AlarmManager.get_pending_alarms(user_id=user_id)

            if not alarms:
                embed = discord.Embed(
                    title="⏰【待觸發鬧鐘清單】",
                    description="（你目前沒有任何待觸發的定時鬧鐘哦！可以使用 `/kurisu-alarm-set` 設定一個。）",
                    color=0x95A5A6
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"⏰【{interaction.user.display_name} 的待觸發鬧鐘清單】",
                description=f"目前共有 **{len(alarms)}** 則等待觸發的定時鬧鐘：",
                color=0x3498DB
            )
            for a in alarms:
                aid = a["id"]
                t_str = a["target_time_str"]
                cnt = a["content"]
                embed.add_field(name=f"鬧鐘 #{aid} ｜ {t_str}", value=f"內容: {cnt}", inline=False)
            embed.set_footer(text="若要取消某個鬧鐘，請使用 /kurisu-alarm-cancel <編號>")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # 6. 【定時鬧鐘模組】/kurisu-alarm-cancel 指令
        @self.tree.command(name="kurisu-alarm-cancel", description="取消指定的定時提醒鬧鐘")
        @app_commands.describe(alarm_id="要取消的鬧鐘編號 (可從 /kurisu-alarm-list 查詢)")
        async def kurisu_alarm_cancel_command(interaction: discord.Interaction, alarm_id: int):
            user_id = str(interaction.user.id)
            success = await AlarmManager.cancel_alarm(alarm_id=alarm_id, user_id=user_id)
            if success:
                embed = discord.Embed(
                    title="🗑️【鬧鐘已取消】",
                    description=f"鬧鐘 **#{alarm_id}** 已經幫你取消囉！",
                    color=0xE67E22
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="⚠️【取消失敗】",
                    description=f"找不到編號 **#{alarm_id}** 的待觸發鬧鐘，或者該鬧鐘不是由你設定的！",
                    color=0xE74C3C
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        # 7. 【行事曆模組】/kurisu-calendar-set 指令
        @self.tree.command(name="kurisu-calendar-set", description="【設定 Webhook 行事曆排程】登記日程並支援 Webhook 推送，平時聊天可直接問行程")
        @app_commands.describe(
            time="排程時間 (格式: y/m/d/h/m，例如 2026/8/27/15/30、8/27/15/30 或 15:30)",
            content="具體排程日程事項 (例如: 實驗室進度匯報、客戶會議)",
            webhook_url="自訂 Webhook URL (選填，若留空則使用預設頻道通知)"
        )
        async def kurisu_calendar_set_command(
            interaction: discord.Interaction,
            time: str,
            content: str,
            webhook_url: Optional[str] = None
        ):
            content = content.strip()
            if not content:
                await interaction.response.send_message("（排程內容不能是空的啦！請輸入日程事項。）", ephemeral=True)
                return

            try:
                target_dt, target_ts, date_str, time_str, formatted_time_str = parse_calendar_time(time)
            except ValueError as e:
                embed_err = discord.Embed(
                    title="⚠️【行事曆時間設定失敗】",
                    description=f"哼，連時間格式都弄錯了！\n\n{str(e)}",
                    color=0xE74C3C
                )
                embed_err.set_footer(text="格式範例：2026/8/27/15/30、8/27/15/30 或 15:30")
                await interaction.response.send_message(embed=embed_err, ephemeral=True)
                return

            channel_id = str(interaction.channel_id) if interaction.channel_id else ""
            user_id = str(interaction.user.id)
            user_name = interaction.user.display_name
            wh_url = (webhook_url or "").strip()

            event_id = await CalendarManager.create_event(
                channel_id=channel_id,
                user_id=user_id,
                user_name=user_name,
                target_timestamp=target_ts,
                target_date=date_str,
                target_time=time_str,
                target_time_str=formatted_time_str,
                content=content,
                webhook_url=wh_url
            )

            embed = discord.Embed(
                title="📅【紅莉栖的行事曆已登記】",
                description=(
                    f"哼，既然你特地交代了，那我就幫你在行事曆記下來吧！\n"
                    f"平常聊天時直接問我「今天/某天有什麼行程」，我也會幫你查出來哦！"
                ),
                color=0x2ECC71
            )
            embed.add_field(name="📌 排程事項", value=f"```{content}```", inline=False)
            embed.add_field(name="⏳ 預定時間", value=f"`{formatted_time_str}`", inline=True)
            embed.add_field(name="🔢 排程編號", value=f"`#{event_id}`", inline=True)
            if wh_url or CALENDAR_WEBHOOK_URL:
                embed.add_field(name="🌐 Webhook 通知", value="`已啟用自訂 Webhook 推送`", inline=False)

            embed.set_footer(text="時間到達時推送提醒 • 平常聊天可隨時詢問當日排程")

            if self.user and self.user.display_avatar:
                embed.set_thumbnail(url=self.user.display_avatar.url)

            await interaction.response.send_message(embed=embed)
            logger.info(f"已為 [{user_name}] 設定行事曆 ID:{event_id}，時間:{formatted_time_str}，內容:「{content}」")

        # 8. 【行事曆模組】/kurisu-calendar-list 指令
        @self.tree.command(name="kurisu-calendar-list", description="查看自己未來一個月內的所有行事曆排程清單")
        async def kurisu_calendar_list_command(interaction: discord.Interaction):
            user_id = str(interaction.user.id)
            events = await CalendarManager.get_upcoming_events(user_id=user_id, days=30, limit=20)

            if not events:
                embed = discord.Embed(
                    title="📅【行事曆待辦清單】",
                    description="（你目前沒有任何排程哦！可以使用 `/kurisu-calendar-set` 設定一個。）",
                    color=0x95A5A6
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"📅【{interaction.user.display_name} 的行事曆排程清單】",
                description=f"目前共有 **{len(events)}** 則待辦排程：\n*（提示：平常對話直接問我「今天有什麼行程」我也能回答你哦！）*",
                color=0x3498DB
            )
            for e in events:
                eid = e["id"]
                t_str = e["target_time_str"]
                cnt = e["content"]
                wh_tag = " [🌐Webhook]" if e.get("webhook_url") else ""
                embed.add_field(name=f"排程 #{eid} ｜ {t_str}{wh_tag}", value=f"內容: {cnt}", inline=False)
            embed.set_footer(text="若要取消某個排程，請使用 /kurisu-calendar-cancel <編號>")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # 9. 【行事曆模組】/kurisu-calendar-cancel 指令
        @self.tree.command(name="kurisu-calendar-cancel", description="取消指定的行事曆排程")
        @app_commands.describe(event_id="要取消的排程編號 (可從 /kurisu-calendar-list 查詢)")
        async def kurisu_calendar_cancel_command(interaction: discord.Interaction, event_id: int):
            user_id = str(interaction.user.id)
            success = await CalendarManager.cancel_event(event_id=event_id, user_id=user_id)
            if success:
                embed = discord.Embed(
                    title="🗑️【排程已取消】",
                    description=f"行事曆排程 **#{event_id}** 已經幫你取消囉！",
                    color=0xE67E22
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="⚠️【取消失敗】",
                    description=f"找不到編號 **#{event_id}** 的待觸發排程，或者該排程不是由你設定的！",
                    color=0xE74C3C
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_ready(self):
        logger.info(f"✨ 機器人登入成功！身份: {self.user} (ID: {self.user.id})")
        logger.info(f"🎭 機器人人設名稱: {BOT_NAME}")
        logger.info(f"💬 回覆頻道列表 (REPLY_CHANNEL_IDS): {REPLY_CHANNEL_IDS or '全部頻道 (無限制)'}")
        logger.info(f"👂 純監聽頻道列表 (LISTEN_CHANNEL_IDS): {LISTEN_CHANNEL_IDS or '無'}")

        # 啟動獨立定時鬧鐘與行事曆後台調度器
        self.alarm_scheduler.start()
        self.calendar_scheduler.start()

        # 同步全域 Slash 指令樹
        try:
            synced = await self.tree.sync()
            logger.info(f"⚡ [Slash Commands] 已成功向 Discord 全域同步 {len(synced)} 個指令: {[cmd.name for cmd in synced]}")
        except Exception as e:
            logger.error(f"❌ 同步 Slash 指令至 Discord 時發生錯誤: {e}", exc_info=True)

    def _create_help_embed(self) -> discord.Embed:
        """產生 Help 指令的 Embed 說明卡片"""
        embed = discord.Embed(
            title=f"📖 {BOT_NAME} 機器人指令說明手冊",
            description=f"你好！我是 **{BOT_NAME}**，以下是目前支援的所有指令與功能（輸入 `/kurisu-` 即可自動跳出補全選單）：",
            color=0x2ECC71
        )
        embed.add_field(
            name="⏰ `/kurisu-alarm-set <時間> <提醒內容>`",
            value=(
                "**【定時鬧鐘提醒】**\n"
                "設定專屬提醒鬧鈴，在指定時刻以紅莉栖專屬傲嬌對白提醒你。\n"
                "*管理：`/kurisu-alarm-list` ｜ `/kurisu-alarm-cancel <編號>`*"
            ),
            inline=False
        )
        embed.add_field(
            name="📅 `/kurisu-calendar-set <時間> <排程內容> [webhook_url]`",
            value=(
                "**【Webhook 行事曆排程】**\n"
                "登記行事曆日程，支援 Webhook 推送，且**日常聊天問我『今天有什麼行程』也會自動回答你**！\n"
                "*管理：`/kurisu-calendar-list` ｜ `/kurisu-calendar-cancel <編號>`*"
            ),
            inline=False
        )
        embed.add_field(
            name="🔍 `/kurisu-search <查詢內容>`",
            value="**【強制線上搜尋】**\n當你想獲取即時時事、最新新聞或查證最新資訊時使用。\n*範例：`/kurisu-search 台北現在天氣`*",
            inline=False
        )
        embed.add_field(
            name="🧠 `/kurisu-profile [用戶或@機器人]`",
            value=(
                "**【查詢用戶個人畫像 / 機器人自身簡介】**\n"
                "查看機器人為你或指定群友建立的長期特徵與好感進展；**若指定 @機器人 則會展示紅莉栖自身的人物檔案與自我介紹**！\n"
                "*範例：`/kurisu-profile` 或 `/kurisu-profile user:@克莉絲`*"
            ),
            inline=False
        )
        embed.add_field(
            name="💬 `日常直接對話（支援多人短時熱絡引用回覆）`",
            value="**【自然群友聊天】**\n直接在頻道內聊天，短時間內**多人熱烈發言時會智慧選擇引用對象**，精準吐槽並兼顧在場群友！",
            inline=False
        )
        if self.user and self.user.display_avatar:
            embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text=f"{BOT_NAME} • Multi-User Memory & Webhook Calendar Enabled")
        return embed

    def _create_bot_profile_embed(self) -> discord.Embed:
        """產生機器人自身的角色自我介紹與背景檔案 Embed 卡片"""
        embed = discord.Embed(
            title=f"🧠 角色檔案：牧瀨紅莉栖（Makise Kurisu）｜ {BOT_NAME}",
            description=(
                "「才、才不是特地向你做自我介紹呢！只是身為維克多·孔多利亞大學的研究員，"
                "適度的身分公開是學術禮儀而已……可別會錯意了！」"
            ),
            color=0x9B59B6
        )

        embed.add_field(
            name="🧬 身份與背景 (Identity)",
            value=(
                "• **本名**：牧瀨紅莉栖（Makise Kurisu）\n"
                f"• **暱稱**：{BOT_NAME}、助手、克里斯蒂娜 (Christina)、The Zombie\n"
                "• **學術所屬**：維克多·孔多利亞大學 腦科學研究所研究員\n"
                "• **實驗室編號**：未來道具研究所 Labmem No.004"
            ),
            inline=False
        )

        embed.add_field(
            name="🔬 專長領域與性格特質 (Expertise & Traits)",
            value=(
                "• **專長領域**：腦科學、神經脈衝傳導、時間跳躍理論\n"
                "• **性格標籤**：天才少女、極度理性、傲嬌 (Tsundere)、重感情\n"
                "• **日常喜好**：Dr Pepper、閱讀學術論文、黑咖啡、匿名論壇討論 (@channel)"
            ),
            inline=False
        )

        embed.add_field(
            name="💖 關係與好感機制 (Relationship System)",
            value=(
                "• **好感階級**：支援動態 4 階好感進展 (`Stranger` ➔ `Familiar` ➔ `Labmem Partner` ➔ `Steins;Gate Bond`)\n"
                "• **關係定位**：默默守護每位群友的傲嬌助手\n"
                "• **每日好感**：平時在頻道聊天會自動累積好感度與記憶特徵"
            ),
            inline=False
        )

        embed.add_field(
            name="💡 常用指令指南",
            value=(
                "• `/kurisu-help`：查看所有指令說明\n"
                "• `/kurisu-profile`：查看你或群友的記憶特徵與好感進度\n"
                "• `/kurisu-search`：強制聯網檢索即時資料\n"
                "• `/kurisu-alarm-*` / `/kurisu-calendar-*`：設定定時提醒與行事曆"
            ),
            inline=False
        )

        if self.user and hasattr(self.user, "display_avatar") and self.user.display_avatar:
            embed.set_thumbnail(url=self.user.display_avatar.url)

        embed.set_footer(text=f"{BOT_NAME} • Labmem No.004 • Steins;Gate Worldline")
        return embed

    def _create_profile_embed(
        self,
        target_user_id: str,
        target_user_name: str,
        profile: Optional[dict],
        target_user: Optional[discord.User] = None
    ) -> discord.Embed:
        if not profile:
            embed = discord.Embed(
                title=f"📋 個人畫像檔案：{target_user_name}",
                description="（目前資料庫中尚未建立該使用者的長期畫像，多跟我聊聊天就會自動累積囉～）",
                color=0x95A5A6
            )
            embed.set_footer(text=f"User ID: {target_user_id}")
            return embed

        user_name_in_db = profile.get("user_name", target_user_name)
        facts = profile.get("facts", [])
        notes = profile.get("interaction_notes", "")
        fav_score = profile.get("favorability", DEFAULT_FAVORABILITY)
        tier = profile.get("relationship_tier", "familiar")
        daily_gain = profile.get("daily_favorability_gain", 0)
        updated_at = profile.get("updated_at", "未知時間")

        tier_title = TIER_NAME_MAP.get(tier, f"Tier: {tier}")
        progress_bar = render_favorability_bar(fav_score, total=100, bar_length=10)

        embed = discord.Embed(
            title=f"🧠 個人特徵畫像檔案：{user_name_in_db}",
            description=f"以下是 {BOT_NAME} 目前為你整理的長期記憶特徵與互動印象：",
            color=0x3498DB
        )

        if ENABLE_FAVORABILITY:
            embed.add_field(
                name="💖 關係進展 (Relationship Progression)",
                value=f"**【{tier_title}】**\n📊 信任進度條：`[{progress_bar}]` **{fav_score} / 100** *(今日累積: +{daily_gain}/{DAILY_GAIN_LIMIT})*",
                inline=False
            )

        facts_formatted = "\n".join([f"• {f}" for f in facts]) if facts else "尚無記錄明確的事實特徵"
        embed.add_field(name="📌 已知特徵 / 喜好 / 事實", value=facts_formatted, inline=False)
        embed.add_field(name="💬 互動印象與習慣", value=notes or "尚無特別印象", inline=False)
        embed.set_footer(text=f"User ID: {target_user_id} | 最後更新：{updated_at}")

        if target_user and hasattr(target_user, "display_avatar") and target_user.display_avatar:
            embed.set_thumbnail(url=target_user.display_avatar.url)

        return embed

    async def _handle_buffered_chat(
        self,
        channel_id: str,
        messages: List[discord.Message],
        is_burst: bool
    ) -> None:
        """
        處理從 BurstBufferManager 釋放的一批訊息（單人或多人群聊 Burst）
        """
        if not messages:
            return

        first_msg = messages[0]
        channel = first_msg.channel

        # 收集圖片附件
        all_images = []
        all_mime_types = []
        for m in messages:
            imgs, mimes = await download_image_attachments(m)
            if imgs:
                all_images.extend(imgs)
                all_mime_types.extend(mimes)

        async def _do_process():
            try:
                # 1. 取得這批訊息中所有出現過的使用者 ID 與提及對象
                all_user_ids = list(set(str(m.author.id) for m in messages if not m.author.bot))
                latest_msg = messages[-1]
                latest_user_id = str(latest_msg.author.id)
                latest_user_name = latest_msg.author.display_name

                # JIT 按需統合：消化在場用戶在監聽頻道累積的訊息
                for uid in all_user_ids:
                    asyncio.create_task(self.memory_extractor.process_user_unextracted_messages(uid))

                # 取出短期記憶與近期訊息 ID
                short_term = await MemoryManager.get_short_term_context(channel_id)
                recent_msg_ids = [str(m.get("message_id")) for m in short_term if m.get("message_id")]

                # 組合本批訊息文本供多用戶畫像檢索
                combined_content = " \n ".join([m.clean_content.strip() for m in messages if m.clean_content.strip()])

                # 多人記憶檢索 (A + B + C 混合方案)
                current_user_profile, other_user_profiles = await MemoryManager.resolve_multi_user_profiles(
                    current_user_id=latest_user_id,
                    content=combined_content,
                    short_term_history=short_term,
                    max_others=4
                )

                # 行事曆排程摘要
                calendar_summary = await CalendarManager.get_user_schedule_summary(latest_user_id)

                # 跨頻道深度回憶
                deep_history = []
                if ENABLE_HISTORY_RECALL and combined_content:
                    deep_history = await MemoryManager.recall_deep_history(
                        query_text=combined_content,
                        exclude_message_ids=recent_msg_ids
                    )

                # 組裝 Context
                memory_context = format_memory_context(
                    current_user_name=latest_user_name,
                    user_profile=current_user_profile,
                    deep_history=deep_history,
                    short_term_history=short_term,
                    calendar_summary=calendar_summary,
                    other_user_profiles=other_user_profiles
                )

                # 根據是否為 Burst 模式組裝 Prompt
                target_msg_obj = latest_msg
                if is_burst and len(messages) >= 2:
                    burst_meta_list = [
                        {
                            "message_id": str(m.id),
                            "user_id": str(m.author.id),
                            "user_name": m.author.display_name,
                            "content": m.clean_content.strip() or "[發送了圖片]",
                            "has_image": bool(m.attachments)
                        }
                        for m in messages
                    ]
                    prompt = build_burst_dialogue_prompt(
                        memory_context=memory_context,
                        burst_messages=burst_meta_list
                    )
                else:
                    # 單人對話模式
                    prompt = f"""{memory_context}

【當前用戶最新發言】:
{latest_user_name}: {combined_content or '[發送了一張圖片]'}

請以幽默風趣的群友風格回應 {latest_user_name}："""

                # 呼叫 Gemini
                response_text = await self.gemini.generate_response(
                    prompt=prompt,
                    images=all_images if all_images else None,
                    image_mime_types=all_mime_types if all_mime_types else None
                )

                # 解析 Burst 回覆標籤與引用目標
                if is_burst and len(messages) >= 2:
                    picked_target_id, clean_response_text = parse_burst_reply_response(
                        raw_text=response_text,
                        default_target_id=str(latest_msg.id)
                    )
                    # 匹配對應的 Discord message 物件
                    matched = next((m for m in messages if str(m.id) == picked_target_id), None)
                    if matched:
                        target_msg_obj = matched
                    response_text = clean_response_text

                # 切分多氣泡訊息
                chunks = split_message(response_text)
                sent_messages = []

                for idx, chunk in enumerate(chunks):
                    if idx == 0:
                        # 第一則訊息：使用 Discord 原生 Reply 引用回覆效果！
                        sent_msg = await target_msg_obj.reply(chunk, mention_author=False)
                        sent_messages.append(sent_msg)
                    else:
                        min_delay, max_delay = TYPING_DELAY_RANGE
                        calc_delay = min(max_delay, max(min_delay, len(chunk) * 0.015))
                        actual_delay = max(min_delay, calc_delay + random.uniform(-0.1, 0.2))

                        if SHOW_TYPING:
                            async with channel.typing():
                                await asyncio.sleep(actual_delay)
                        else:
                            await asyncio.sleep(actual_delay)

                        sent_msg = await channel.send(chunk)
                        sent_messages.append(sent_msg)

                # 儲存 Bot 發送的訊息至 SQLite
                combined_reply = "\n".join([m.content for m in sent_messages])
                primary_sent = sent_messages[0] if sent_messages else None
                if primary_sent:
                    await MemoryManager.save_message(
                        message_id=str(primary_sent.id),
                        channel_id=str(channel_id),
                        user_id=str(self.user.id),
                        user_name=BOT_NAME,
                        content=combined_reply,
                        has_image=False,
                        is_bot=True,
                        timestamp=int(primary_sent.created_at.timestamp() if primary_sent.created_at else time.time()),
                        extracted=True
                    )

                logger.info(
                    f"已回覆訊息 (Burst={is_burst}, 引用對象: [{target_msg_obj.author.display_name} - {target_msg_obj.id}], "
                    f"氣泡數: {len(chunks)})"
                )

                # 非同步背景提煉記憶與好感度
                if is_burst and len(messages) >= 2:
                    dialogue_batch = [
                        {
                            "message_id": str(m.id),
                            "channel_id": str(channel_id),
                            "user_id": str(m.author.id),
                            "user_name": m.author.display_name,
                            "content": m.clean_content.strip(),
                            "has_image": bool(m.attachments),
                            "timestamp": int(m.created_at.timestamp() if m.created_at else time.time())
                        }
                        for m in messages
                    ]
                    asyncio.create_task(self.memory_extractor.extract_from_dialogue_batch(dialogue_batch))
                else:
                    if combined_content:
                        asyncio.create_task(
                            self.memory_extractor.extract_and_update(
                                user_id=latest_user_id,
                                user_name=latest_user_name,
                                recent_messages=[combined_content],
                                other_users=other_user_profiles
                            )
                        )

            except Exception as e:
                logger.error(f"處理對話訊息時發生異常: {e}", exc_info=True)
                try:
                    await channel.send(f"（發生了一點小狀況：{e}）")
                except Exception:
                    pass

        if SHOW_TYPING:
            async with channel.typing():
                await _do_process()
        else:
            await _do_process()

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.author == self.user:
            return

        channel_id = message.channel.id
        is_reply_channel = (not REPLY_CHANNEL_IDS) or (channel_id in REPLY_CHANNEL_IDS)
        is_listen_channel = channel_id in LISTEN_CHANNEL_IDS

        if not is_reply_channel and not is_listen_channel:
            return

        content = message.clean_content.strip()
        has_image = bool(message.attachments)

        if not content and not has_image:
            return

        user_id = str(message.author.id)
        user_name = message.author.display_name
        msg_id = str(message.id)
        current_ts = int(message.created_at.timestamp() if message.created_at else time.time())

        # 1. 永久寫入資料庫（純監聽頻道 extracted=0，待批次處理；回覆頻道若為單人直接標記）
        await MemoryManager.save_message(
            message_id=msg_id,
            channel_id=str(channel_id),
            user_id=user_id,
            user_name=user_name,
            content=content or "[上傳了圖片]",
            has_image=has_image,
            is_bot=False,
            timestamp=current_ts,
            extracted=False
        )

        # 2. 純監聽頻道處理（方案 C：加入防抖隊列，累積滿或靜默定時批次提煉）
        if is_listen_channel and not is_reply_channel:
            logger.debug(f"[監聽模式] 記錄頻道 #{message.channel.name if hasattr(message.channel, 'name') else channel_id} 訊息 - {user_name}")
            self.memory_extractor.add_to_listen_queue(
                str(channel_id),
                {
                    "message_id": msg_id,
                    "channel_id": str(channel_id),
                    "user_id": user_id,
                    "user_name": user_name,
                    "content": content or "[上傳了圖片]",
                    "has_image": has_image,
                    "timestamp": current_ts
                }
            )
            return

        # 3. 主回覆頻道對話處理（若開啟 Burst 聚合，則加入滑動窗口緩衝隊列）
        if ENABLE_BURST_REPLY:
            await self.burst_manager.add_message(message, self._handle_buffered_chat)
        else:
            await self._handle_buffered_chat(str(channel_id), [message], is_burst=False)
