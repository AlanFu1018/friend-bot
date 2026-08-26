import asyncio
import time
import random
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
    """Friend-Bot 核心客戶端，處理訊息路由、記憶提取與 AI 回應"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gemini = GeminiClient()
        self.memory_extractor = MemoryExtractor(gemini_client=self.gemini)

    async def on_ready(self):
        logger.info(f"✨ 機器人登入成功！身分: {self.user} (ID: {self.user.id})")
        logger.info(f"🎭 機器人人設名稱: {BOT_NAME}")
        logger.info(f"💬 回覆頻道列表 (REPLY_CHANNEL_IDS): {REPLY_CHANNEL_IDS or '全部頻道 (無限制)'}")
        logger.info(f"👂 純監聽頻道列表 (LISTEN_CHANNEL_IDS): {LISTEN_CHANNEL_IDS or '無'}")

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

        # 3. 【永久儲存】將收到的訊息寫入資料庫與 FTS5
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
                    # 加上少許隨機擾動增加擬真感
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
