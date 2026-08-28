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
    BURST_MAX_MESSAGES,
    FACTS_SPEAKER_MAX_TOTAL,
    FACTS_SPEAKER_HEAT_LIMIT,
    FACTS_SPEAKER_RECENT_LIMIT,
    FACTS_OTHERS_MAX_TOTAL,
    FACTS_OTHERS_HEAT_LIMIT,
    FACTS_OTHERS_RECENT_LIMIT
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
        """Bot 啟動時自動掛載與註冊所有 Mixin 指令"""
        logger.info("正在註冊所有 Slash 指令至 CommandTree...")
        self.register_help_commands()
        self.register_search_commands()
        self.register_profile_commands()
        self.register_alarm_commands()
        self.register_calendar_commands()

        # 同步指令至 Discord 伺服器
        try:
            synced = await self.tree.sync()
            logger.info(f"Slash 指令同步成功，共註冊 {len(synced)} 個指令")
        except Exception as e:
            logger.error(f"Slash 指令同步失敗: {e}", exc_info=True)

        # 啟動背景定時排程器
        self.alarm_scheduler.start()
        self.calendar_scheduler.start()

    async def on_ready(self):
        logger.info(f"機器人已成功登入為: {self.user} (ID: {self.user.id})")
        logger.info(f"回覆頻道 (Reply Channels): {REPLY_CHANNEL_IDS}")
        logger.info(f"監聽頻道 (Listen Channels): {LISTEN_CHANNEL_IDS}")
        logger.info(f"Burst 聚合模式: {'已啟用' if ENABLE_BURST_REPLY else '未啟用'}")

    async def on_message(self, message: discord.Message):
        """訊息事件處理器：支援監聽頻道批次提煉與主頻道 Burst 聚合回覆緩衝區"""
        if message.author.bot or message.author == self.user:
            return

        channel_id = str(message.channel.id)

        # 1. 監聽頻道模式 (純記錄與非同步記憶提煉)
        if channel_id in LISTEN_CHANNEL_IDS:
            logger.debug(f"[監聽頻道] 收到來自 {message.author.display_name} 的訊息: {message.clean_content}")
            await MemoryManager.save_message(
                message_id=str(message.id),
                channel_id=channel_id,
                user_id=str(message.author.id),
                user_name=message.author.display_name,
                content=message.clean_content,
                has_image=bool(message.attachments),
                is_bot=False,
                timestamp=int(message.created_at.timestamp()),
                extracted=False
            )
            await self.memory_extractor.add_listen_message(
                channel_id=channel_id,
                message_data={
                    "message_id": str(message.id),
                    "channel_id": channel_id,
                    "user_id": str(message.author.id),
                    "user_name": message.author.display_name,
                    "content": message.clean_content,
                    "has_image": bool(message.attachments),
                    "timestamp": int(message.created_at.timestamp())
                }
            )
            return

        # 2. 主要對話頻道模式 (主動或被提及回覆)
        if channel_id in REPLY_CHANNEL_IDS:
            # 若啟用 Burst 模式，送入緩衝區由防抖視窗聚合處理
            if ENABLE_BURST_REPLY:
                await self.burst_manager.add_message(message=message, on_flush=self._on_burst_flush)
            else:
                # 傳統單則回覆模式
                await self._handle_buffered_chat(channel=message.channel, messages=[message], is_burst=False)

    async def _on_burst_flush(self, channel_id: str, messages: List[discord.Message], is_burst: bool):
        """Burst 緩衝區到期或滿載時的回呼"""
        if not messages:
            return
        channel = messages[0].channel
        await self._handle_buffered_chat(channel=channel, messages=messages, is_burst=is_burst)

    async def _handle_buffered_chat(self, channel: discord.abc.Messageable, messages: List[discord.Message], is_burst: bool):
        """核心對話處理器：整合多實體記憶解析、三軌 RAG 檢索、好感度評估與 Burst 聚合回覆"""
        if not messages:
            return

        channel_id = str(channel.id)
        latest_msg = messages[-1]
        latest_user_id = str(latest_msg.author.id)
        latest_user_name = latest_msg.author.display_name

        try:
            # 儲存本批所有訊息至 SQLite
            for m in messages:
                await MemoryManager.save_message(
                    message_id=str(m.id),
                    channel_id=channel_id,
                    user_id=str(m.author.id),
                    user_name=m.author.display_name,
                    content=m.clean_content,
                    has_image=bool(m.attachments),
                    is_bot=False,
                    timestamp=int(m.created_at.timestamp()),
                    extracted=False
                )

            all_user_ids = list(set(str(m.author.id) for m in messages))

            # JIT 按需整合：消化在場用戶在監聽頻道累積的訊息
            for uid in all_user_ids:
                asyncio.create_task(self.memory_extractor.process_unextracted_for_user(uid, latest_user_name))

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

            # 三軌混合事實檢索 (Heat + RAG + Recent)
            if current_user_profile:
                filtered_facts, hit_facts = MemoryManager.filter_facts_three_tracks(
                    facts_data=current_user_profile.get("facts", []),
                    query_text=combined_content,
                    max_total=FACTS_SPEAKER_MAX_TOTAL,
                    heat_limit=FACTS_SPEAKER_HEAT_LIMIT,
                    recent_limit=FACTS_SPEAKER_RECENT_LIMIT
                )
                current_user_profile["facts"] = filtered_facts
                if hit_facts:
                    asyncio.create_task(MemoryManager.record_fact_hits(latest_user_id, hit_facts))

            if other_user_profiles:
                for o_prof in other_user_profiles:
                    o_uid = str(o_prof.get("user_id", ""))
                    o_filtered, o_hits = MemoryManager.filter_facts_three_tracks(
                        facts_data=o_prof.get("facts", []),
                        query_text=combined_content,
                        max_total=FACTS_OTHERS_MAX_TOTAL,
                        heat_limit=FACTS_OTHERS_HEAT_LIMIT,
                        recent_limit=FACTS_OTHERS_RECENT_LIMIT
                    )
                    o_prof["facts"] = o_filtered
                    if o_hits and o_uid:
                        asyncio.create_task(MemoryManager.record_fact_hits(o_uid, o_hits))

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

            # 收集圖片附件
            all_attachments = []
            for m in messages:
                all_attachments.extend(m.attachments)
            image_bytes_list = await download_image_attachments(all_attachments)

            # 打字中提示
            if SHOW_TYPING:
                async with channel.typing():
                    response_text = await self.gemini.generate_response(
                        prompt=prompt,
                        images=image_bytes_list if image_bytes_list else None
                    )
            else:
                response_text = await self.gemini.generate_response(
                    prompt=prompt,
                    images=image_bytes_list if image_bytes_list else None
                )

            if response_text:
                # 若為 Burst 模式，解析模型回傳的 [TARGET_ID: xxx]
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
                            "content": m.clean_content,
                            "has_image": bool(m.attachments),
                            "timestamp": int(m.created_at.timestamp())
                        }
                        for m in messages
                    ]
                    asyncio.create_task(
                        self.memory_extractor._process_batch_extraction(channel_id, dialogue_batch)
                    )
                else:
                    recent_texts = [m.clean_content for m in messages if m.clean_content.strip()]
                    asyncio.create_task(
                        self.memory_extractor.extract_and_update(
                            user_id=latest_user_id,
                            user_name=latest_user_name,
                            recent_messages=recent_texts,
                            other_users=other_user_profiles
                        )
                    )

        except Exception as e:
            logger.error(f"對話處理失敗: {e}", exc_info=True)
            try:
                await channel.send("（糟糕……世界線似乎發生了未知的變動，我的神經迴路暫時打結了……）")
            except Exception:
                pass
