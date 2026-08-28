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
    ENABLE_BURST_REPLY,
    BURST_WINDOW_SECONDS,
    BURST_MIN_USER_COUNT,
    BURST_MAX_MESSAGES
)
from src.friend_bot.memory import MemoryManager
from src.friend_bot.bot.utils.alarm import AlarmScheduler
from src.friend_bot.bot.utils.calendar import CalendarManager, CalendarScheduler
from src.friend_bot.bot.utils.burst import BurstBufferManager
from src.friend_bot.ai import GeminiClient, MemoryExtractor
from src.friend_bot.ai.prompts import (
    format_memory_context,
    build_burst_dialogue_prompt,
    parse_burst_reply_response
)
from src.friend_bot.bot.handlers import download_image_attachments, split_message
from src.friend_bot.bot.commands import (
    HelpCommandsMixin,
    SearchCommandsMixin,
    ProfileCommandsMixin,
    AlarmCommandsMixin,
    CalendarCommandsMixin,
    TIER_NAME_MAP,
    render_favorability_bar
)

logger = logging.getLogger("friend_bot.bot")


class FriendBotClient(
    HelpCommandsMixin,
    SearchCommandsMixin,
    ProfileCommandsMixin,
    AlarmCommandsMixin,
    CalendarCommandsMixin,
    discord.Client
):
    """Discord Bot 客戶端（透過 Mixin 繼承各類別 Slash 指令實作，支援定時鬧鐘、行事曆、多人群聊短時熱絡聚合與動態引用回覆）"""

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
        """註冊各 Mixin 模組中 Discord 原生 /kurisu- 開頭的 Slash 指令"""
        self.register_help_commands()
        self.register_search_commands()
        self.register_profile_commands()
        self.register_alarm_commands()
        self.register_calendar_commands()
        logger.info("已成功註冊說明 (help)、搜尋 (search)、畫像 (profile)、鬧鐘 (alarm) 與行事曆 (calendar) Slash 指令實作")

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

        # 訊息註解過濾（若以 # 或 ＃ 開頭，則視為註解：在回覆頻道不回覆且不記憶，在監聽頻道亦排除不提煉）
        if content.startswith("#") or content.startswith("＃"):
            logger.debug(f"[註解過濾] 忽略以 '#' 開頭的訊息 - 頻道: {channel_id}, 發送者: {message.author.display_name}")
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
