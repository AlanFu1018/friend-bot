import asyncio
import time
import random
import re
import discord

from src.friend_bot.core.config import (
    REPLY_CHANNEL_IDS,
    LISTEN_CHANNEL_IDS,
    SHOW_TYPING,
    BOT_NAME,
    ENABLE_HISTORY_RECALL,
    TYPING_DELAY_RANGE,
)
from src.friend_bot.core.logger import get_logger
from src.friend_bot.memory.memory_manager import MemoryManager
from src.friend_bot.ai import GeminiClient
from src.friend_bot.ai.memory_extractor import MemoryExtractor
from src.friend_bot.ai.prompts import format_memory_context
from .handlers import download_image_attachments, split_message

logger = get_logger("bot")

class FriendBotClient(discord.Client):
    """Friend-Bot 核心客戶端，處理訊息路由、記憶提取、AI 回應與指令處理"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gemini = GeminiClient()
        self.memory_extractor = MemoryExtractor(gemini_client=self.gemini)

    async def on_ready(self):
        logger.info(f"✨ 機器人登入成功！身分: {self.user} (ID: {self.user.id})")
        logger.info(f"🎭 機器人人設名稱: {BOT_NAME}")
        logger.info(f"💬 回覆頻道列表 (REPLY_CHANNEL_IDS): {REPLY_CHANNEL_IDS or '全部頻道 (無限制)'}")
        logger.info(f"👂 純監聽頻道列表 (LISTEN_CHANNEL_IDS): {LISTEN_CHANNEL_IDS or '無'}")

    async def _handle_profile_command(self, message: discord.Message) -> bool:
        """
        處理 /profile 指令：
        支援格式：
        - `/profile` (查詢自己)
        - `/profile @user` (提及用戶)
        - `/profile <user_id>` (純數字 ID)
        """
        content = message.content.strip()
        if not content.startswith("/profile"):
            return False

        # 解析目標使用者
        target_user = None
        parts = content.split()

        if len(message.mentions) > 0:
            # 優先從 mention 獲取
            target_user = message.mentions[0]
            target_user_id = str(target_user.id)
            target_user_name = target_user.display_name
        elif len(parts) > 1:
            # 檢查是否為純數字 ID 或帶有標籤的字串
            arg = parts[1].strip("<@!>")
            if arg.isdigit():
                target_user_id = arg
                # 嘗試從伺服器快取抓取使用者名稱
                member = message.guild.get_member(int(arg)) if message.guild else None
                target_user_name = member.display_name if member else f"用戶({arg})"
            else:
                target_user_id = str(message.author.id)
                target_user_name = message.author.display_name
        else:
            # 預設查自己
            target_user_id = str(message.author.id)
            target_user_name = message.author.display_name

        # 查詢個人畫像
        profile = await MemoryManager.get_user_profile(target_user_id)

        if not profile:
            embed = discord.Embed(
                title=f"📋 個人畫像檔案：{target_user_name}",
                description="（目前資料庫中尚未建立該使用者的長期畫像，多跟我聊聊天就會自動累積囉～）",
                color=0x95A5A6
            )
            embed.set_footer(text=f"User ID: {target_user_id}")
            await message.channel.send(embed=embed)
            return True

        user_name_in_db = profile.get("user_name", target_user_name)
        facts = profile.get("facts", [])
        notes = profile.get("interaction_notes", "")
        updated_at = profile.get("updated_at", "未知時間")

        embed = discord.Embed(
            title=f"🧠 個人特徵畫像檔案：{user_name_in_db}",
            description=f"以下是 {BOT_NAME} 目前為你整理的長期記憶特徵與互動印象：",
            color=0x3498DB
        )

        # 格式化特徵清單
        if facts:
            facts_formatted = "\n".join([f"• {f}" for f in facts])
        else:
            facts_formatted = "尚無記錄明確的事實特徵"
        embed.add_field(name="📌 已知特徵 / 喜好 / 事實", value=facts_formatted, inline=False)

        # 格式化互動印象
        if notes:
            embed.add_field(name="💬 互動印象與習慣", value=notes, inline=False)
        else:
            embed.add_field(name="💬 互動印象與習慣", value="尚無特別印象", inline=False)

        embed.set_footer(text=f"User ID: {target_user_id} | 最後更新：{updated_at}")

        if target_user and hasattr(target_user, "display_avatar") and target_user.display_avatar:
            embed.set_thumbnail(url=target_user.display_avatar.url)

        await message.channel.send(embed=embed)
        logger.info(f"已向 [{message.author.display_name}] 顯示 [{user_name_in_db}] 的個人畫像")
        return True

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

        # 2. 優先檢查是否為 /profile 查詢指令
        if await self._handle_profile_command(message):
            return

        content = message.clean_content.strip()
        
        # 3. 下載圖片附件（若有）
        images, mime_types = await download_image_attachments(message)
        has_image = bool(images)

        # 若無文字也無圖片，略過
        if not content and not has_image:
            return

        user_id = str(message.author.id)
        user_name = message.author.display_name
        msg_id = str(message.id)
        current_ts = int(message.created_at.timestamp() if message.created_at else time.time())

        # 4. 【永久儲存】將收到的訊息寫入資料庫與 FTS5
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

        # 5. 【純監聽模式處理】若僅為監聽頻道且非回覆頻道：默默記錄記憶，不發言
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

        # 6. 【回覆頻道處理】調用三層記憶並透過 Gemini 生成幽默回覆
        async def process_chat():
            logger.info(f"收到來自 [{user_name}] 的對話訊息 (頻道: #{message.channel.name if hasattr(message.channel, 'name') else channel_id})")
            
            # A. 取出第 1 層：當前頻道短期滑動視窗
            short_term = await MemoryManager.get_short_term_context(str(channel_id))
            recent_msg_ids = [str(m.get("message_id")) for m in short_term if m.get("message_id")]

            # B. 取出第 2 層：發言用戶長期個人畫像
            user_profile = await MemoryManager.get_user_profile(user_id)

            # C. 取出第 3 層：跨頻道歷史深度回憶
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

            # F. 自然語意多氣泡切分並逐段發送至 Discord
            chunks = split_message(response_text)
            sent_messages = []
            for idx, chunk in enumerate(chunks):
                # 若有多段訊息，在發送後續段落時加入擬真打字間隔 (Typing)
                if idx > 0:
                    min_delay, max_delay = TYPING_DELAY_RANGE
                    calc_delay = min(max_delay, max(min_delay, len(chunk) * 0.015))
                    actual_delay = calc_delay + random.uniform(-0.1, 0.2)
                    actual_delay = max(min_delay, actual_delay)

                    if SHOW_TYPING:
                        async with message.channel.typing():
                            await asyncio.sleep(actual_delay)
                    else:
                        await asyncio.sleep(actual_delay)

                sent_msg = await message.channel.send(chunk)
                sent_messages.append(sent_msg)

            # G. 將機器人的完整回覆存入資料庫
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

            # H. 背景非同步提取該用戶的新特徵
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
