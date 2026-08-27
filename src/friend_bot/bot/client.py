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
    REPLY_CHANNEL_IDS,
    LISTEN_CHANNEL_IDS,
)
from src.friend_bot.memory import MemoryManager
from src.friend_bot.ai import GeminiClient, MemoryExtractor
from src.friend_bot.ai.prompts import format_memory_context
from src.friend_bot.bot.handlers import download_image_attachments, split_message

logger = logging.getLogger("friend_bot.bot")

class FriendBotClient(discord.Client):
    """Discord Bot 客戶端（支援 /kurisu- 原生斜線指令）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.gemini = GeminiClient()
        self.memory_extractor = MemoryExtractor()

    async def setup_hook(self):
        """註冊 Discord 原生 /kurisu- 開頭的 Slash 指令（提供自動補全、參數提示與選單）"""
        
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

            # 檢索上下文與畫像
            short_term = await MemoryManager.get_short_term_context(channel_id) if channel_id else []
            user_profile = await MemoryManager.get_user_profile(user_id)
            deep_history = []
            if ENABLE_HISTORY_RECALL:
                deep_history = await MemoryManager.recall_deep_history(query_text=query)

            memory_context = format_memory_context(
                current_user_name=user_name,
                user_profile=user_profile,
                deep_history=deep_history,
                short_term_history=short_term
            )

            prompt = f"""{memory_context}

【當前用戶最新發言】:
{user_name}: 【用戶明確要求強制聯網搜尋最新資料】：{query}

【特別指示】：
請務必使用 search_web 搜尋即時資料，並在獲取搜尋結果後，以你的角色風格（傲嬌/理性的克莉絲）**詳細綜合整理並具體回覆搜尋到的最新資訊與實質內容**給 {user_name}："""

            response_text = await self.gemini.generate_response(prompt=prompt)
            chunks = split_message(response_text)

            # 首則訊息透過 interaction followup 回傳
            if chunks:
                first_msg = await interaction.followup.send(chunks[0])
                current_ts = int(time.time())
                
                # 儲存用戶的搜尋發言
                await MemoryManager.save_message(
                    message_id=str(interaction.id),
                    channel_id=channel_id,
                    user_id=user_id,
                    user_name=user_name,
                    content=query,
                    has_image=False,
                    is_bot=False,
                    timestamp=current_ts
                )

                # 若有多個氣泡，後續透過頻道發送
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

                # 儲存機器人回覆
                await MemoryManager.save_message(
                    message_id=str(first_msg.id if hasattr(first_msg, 'id') else interaction.id),
                    channel_id=channel_id,
                    user_id=str(self.user.id),
                    user_name=BOT_NAME,
                    content="\n".join(sent_chunks),
                    has_image=False,
                    is_bot=True,
                    timestamp=int(time.time())
                )

                # 背景非同步提煉特徵
                asyncio.create_task(
                    self.memory_extractor.extract_and_update(
                        user_id=user_id,
                        user_name=user_name,
                        recent_messages=[query]
                    )
                )

        # 3. /kurisu-profile 指令
        @self.tree.command(name="kurisu-profile", description="查看自己或指定群友的長期記憶特徵畫像與互動印象")
        @app_commands.describe(user="選擇要查詢畫像的群友（留空代表查詢自己）")
        async def kurisu_profile_command(interaction: discord.Interaction, user: Optional[discord.User] = None):
            target_user = user or interaction.user
            target_user_id = str(target_user.id)
            target_user_name = target_user.display_name

            profile = await MemoryManager.get_user_profile(target_user_id)
            embed = self._create_profile_embed(target_user_id, target_user_name, profile, target_user)
            await interaction.response.send_message(embed=embed)
            logger.info(f"已向 [{interaction.user.display_name}] 透過 /kurisu-profile 顯示 [{target_user_name}] 的畫像")

    async def on_ready(self):
        logger.info(f"✨ 機器人登入成功！身份: {self.user} (ID: {self.user.id})")
        logger.info(f"🎭 機器人人設名稱: {BOT_NAME}")
        logger.info(f"💬 回覆頻道列表 (REPLY_CHANNEL_IDS): {REPLY_CHANNEL_IDS or '全部頻道 (無限制)'}")
        logger.info(f"👂 純監聽頻道列表 (LISTEN_CHANNEL_IDS): {LISTEN_CHANNEL_IDS or '無'}")

        # 在 Bot 登入完成且獲取 application_id 後同步全域 Slash 指令樹
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
            name="🔍 `/kurisu-search <查詢內容>`",
            value="**【強制線上搜尋】**\n當你想獲取即時時事、最新新聞或查證最新資訊時使用。\n*範例：`/kurisu-search 台北現在天氣`、`/kurisu-search 2026年最新科技新聞`*",
            inline=False
        )
        embed.add_field(
            name="🧠 `/kurisu-profile [用戶]`",
            value="**【查詢用戶個人畫像】**\n查看機器人為你或指定群友建立的長期特徵、喜好與互動印象（支援選單直接選擇群友）。\n*範例：`/kurisu-profile`、`/kurisu-profile @群友`*",
            inline=False
        )
        embed.add_field(
            name="💬 `日常直接對話`",
            value="**【自然群友聊天】**\n直接在頻道內發送文字或上傳圖片，我會自動結合上下文、歷史回憶與個人畫像進行幽默互動。",
            inline=False
        )
        embed.add_field(
            name="❓ `/kurisu-help`",
            value="**【功能說明】**\n呼叫出這張指令指南卡片。",
            inline=False
        )
        if self.user and self.user.display_avatar:
            embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text=f"{BOT_NAME} • Three-Tier Memory & Tool Calling Enabled")
        return embed

    def _create_profile_embed(
        self,
        target_user_id: str,
        target_user_name: str,
        profile: Optional[dict],
        target_user: Optional[discord.User] = None
    ) -> discord.Embed:
        """產生 Profile 指令的 Embed 說明卡片"""
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
        updated_at = profile.get("updated_at", "未知時間")

        embed = discord.Embed(
            title=f"🧠 個人特徵畫像檔案：{user_name_in_db}",
            description=f"以下是 {BOT_NAME} 目前為你整理的長期記憶特徵與互動印象：",
            color=0x3498DB
        )

        facts_formatted = "\n".join([f"• {f}" for f in facts]) if facts else "尚無記錄明確的事實特徵"
        embed.add_field(name="📌 已知特徵 / 喜好 / 事實", value=facts_formatted, inline=False)
        embed.add_field(name="💬 互動印象與習慣", value=notes or "尚無特別印象", inline=False)
        embed.set_footer(text=f"User ID: {target_user_id} | 最後更新：{updated_at}")

        if target_user and hasattr(target_user, "display_avatar") and target_user.display_avatar:
            embed.set_thumbnail(url=target_user.display_avatar.url)

        return embed

    async def on_message(self, message: discord.Message):
        # 1. 忽略機器人自身與其他機器人的發言
        if message.author.bot or message.author == self.user:
            return

        channel_id = message.channel.id
        is_reply_channel = (not REPLY_CHANNEL_IDS) or (channel_id in REPLY_CHANNEL_IDS)
        is_listen_channel = channel_id in LISTEN_CHANNEL_IDS

        # 若不在回覆頻道，也不在監聽頻道，則直接略過
        if not is_reply_channel and not is_listen_channel:
            return

        content = message.clean_content.strip()

        # 2. 下載圖片附件（若有）
        images, mime_types = await download_image_attachments(message)
        has_image = bool(images)

        # 若無文字也無圖片，略過
        if not content and not has_image:
            return

        user_id = str(message.author.id)
        user_name = message.author.display_name
        msg_id = str(message.id)
        current_ts = int(message.created_at.timestamp() if message.created_at else time.time())

        # 3. 【永久儲存】將收到的對話訊息寫入資料庫與 FTS5
        await MemoryManager.save_message(
            message_id=msg_id,
            channel_id=str(channel_id),
            user_id=user_id,
            user_name=user_name,
            content=content or "[上傳了圖片]",
            has_image=has_image,
            is_bot=False,
            timestamp=current_ts
        )

        # 4. 【純監聽模式處理】若僅為監聽頻道且非回覆頻道：默默記錄記憶，不發言
        if is_listen_channel and not is_reply_channel:
            logger.debug(f"[監聽模式] 記錄頻道 #{message.channel.name} 訊息 - {user_name}")
            if content:
                asyncio.create_task(
                    self.memory_extractor.extract_and_update(
                        user_id=user_id,
                        user_name=user_name,
                        recent_messages=[content]
                    )
                )
            return

        # 5. 【回覆頻道處理】調用三層記憶並透過 Gemini 生成幽默回覆
        async def process_chat():
            logger.info(f"收到來自 [{user_name}] 的對話訊息 (頻道: #{message.channel.name if hasattr(message.channel, 'name') else channel_id})")
            
            # A. 取出短期記憶
            short_term = await MemoryManager.get_short_term_context(str(channel_id))
            recent_msg_ids = [str(m.get("message_id")) for m in short_term if m.get("message_id")]

            # B. 取出發言用戶長期畫像
            user_profile = await MemoryManager.get_user_profile(user_id)

            # C. 取出跨頻道深度回憶
            deep_history = []
            if ENABLE_HISTORY_RECALL and content:
                deep_history = await MemoryManager.recall_deep_history(
                    query_text=content,
                    exclude_message_ids=recent_msg_ids
                )

            # D. 組裝上下文 Prompt
            memory_context = format_memory_context(
                current_user_name=user_name,
                user_profile=user_profile,
                deep_history=deep_history,
                short_term_history=short_term
            )

            prompt = f"""{memory_context}

【當前用戶最新發言】:
{user_name}: {content or '[發送了一張圖片]'}

請以幽默風趣的群友風格回應 {user_name}："""

            # E. 呼叫 Gemini 生成回覆
            response_text = await self.gemini.generate_response(
                prompt=prompt,
                images=images if images else None,
                image_mime_types=mime_types if mime_types else None
            )

            # F. 自然語意多氣泡切分並逐段發送
            chunks = split_message(response_text)
            sent_messages = []
            for idx, chunk in enumerate(chunks):
                if idx > 0:
                    min_delay, max_delay = TYPING_DELAY_RANGE
                    calc_delay = min(max_delay, max(min_delay, len(chunk) * 0.015))
                    actual_delay = max(min_delay, calc_delay + random.uniform(-0.1, 0.2))

                    if SHOW_TYPING:
                        async with message.channel.typing():
                            await asyncio.sleep(actual_delay)
                    else:
                        await asyncio.sleep(actual_delay)

                sent_msg = await message.channel.send(chunk)
                sent_messages.append(sent_msg)

            # G. 儲存機器人回覆
            combined_reply = "\n".join([m.content for m in sent_messages])
            primary_msg = sent_messages[0] if sent_messages else None
            if primary_msg:
                await MemoryManager.save_message(
                    message_id=str(primary_msg.id),
                    channel_id=str(channel_id),
                    user_id=str(self.user.id),
                    user_name=BOT_NAME,
                    content=combined_reply,
                    has_image=False,
                    is_bot=True,
                    timestamp=int(primary_msg.created_at.timestamp() if primary_msg.created_at else time.time())
                )

            logger.info(f"已回覆 [{user_name}] 的訊息 (共 {len(chunks)} 則訊息氣泡)")

            # H. 背景非同步提煉畫像
            if content:
                asyncio.create_task(
                    self.memory_extractor.extract_and_update(
                        user_id=user_id,
                        user_name=user_name,
                        recent_messages=[content]
                    )
                )

        # 執行生成並維持 Typing 狀態
        try:
            if SHOW_TYPING:
                async with message.channel.typing():
                    await process_chat()
            else:
                await process_chat()
        except Exception as e:
            logger.error(f"處理訊息時發生異常: {e}", exc_info=True)
            try:
                await message.channel.send(f"（發生了一點小狀況：{e}）")
            except Exception:
                pass
