import asyncio
import time
import random
from typing import List, Optional
import discord
from discord import app_commands

from src.friend_bot.core.config import (
    BOT_NAME,
    REPLY_CHANNEL_IDS,
    LISTEN_CHANNEL_IDS,
    SHOW_TYPING,
    IGNORE_PREFIXES,
    ENABLE_BURST_REPLY,
    BURST_WINDOW_SECONDS,
    BURST_MIN_USER_COUNT,
    BURST_MAX_MESSAGES,
    TYPING_DELAY_RANGE,
    ENABLE_HISTORY_RECALL,
    FACTS_SPEAKER_MAX_TOTAL,
    FACTS_SPEAKER_HEAT_LIMIT,
    FACTS_SPEAKER_RECENT_LIMIT,
    FACTS_OTHERS_MAX_TOTAL,
    FACTS_OTHERS_HEAT_LIMIT,
    FACTS_OTHERS_RECENT_LIMIT,
    ENABLE_MUSIC_SUGGESTION,
    VOICE_MEMBERS_MAX,
)
from src.friend_bot.core.logger import get_logger
from src.friend_bot.ai.gemini_client import GeminiClient
from src.friend_bot.ai.memory_extractor import MemoryExtractor
from src.friend_bot.ai.facts_dedup import FactsDeduplicator
from src.friend_bot.ai.prompts import (
    format_memory_context,
    build_burst_dialogue_prompt,
    parse_burst_reply_response
)
from src.friend_bot.bot.handlers import download_image_attachments, split_message
from src.friend_bot.bot.utils import (
    AlarmManager,
    AlarmScheduler,
    CalendarManager,
    CalendarScheduler,
    BurstBufferManager
)
from src.friend_bot.memory import MemoryManager

# 導入所有 Mixin 指令模組
from src.friend_bot.bot.commands import (
    HelpCommandsMixin,
    SearchCommandsMixin,
    ProfileCommandsMixin,
    AliasCommandsMixin,
    AlarmCommandsMixin,
    CalendarCommandsMixin
)

logger = get_logger("client")


class FriendBotClient(
    HelpCommandsMixin,
    SearchCommandsMixin,
    ProfileCommandsMixin,
    AliasCommandsMixin,
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
        self.facts_deduplicator = FactsDeduplicator()
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
        self.register_alias_commands()
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
        # 背景撿漏：處理提煉失敗或重啟遺失佇列而殘留的未提煉訊息（取代原本的每則訊息 JIT）
        self.memory_extractor.start_sweeper()
        # 背景語意去重：定期合併「同一件事、不同講法」的重複事實，降低硬上限淘汰真實事實的頻率
        self.facts_deduplicator.start_sweeper()

    async def on_ready(self):
        logger.info(f"機器人已成功登入為: {self.user} (ID: {self.user.id})")
        logger.info(f"回覆頻道 (Reply Channels): {REPLY_CHANNEL_IDS}")
        logger.info(f"監聽頻道 (Listen Channels): {LISTEN_CHANNEL_IDS}")
        logger.info(f"忽略前綴 (Ignore Prefixes): {IGNORE_PREFIXES}")
        logger.info(f"Burst 聚合模式: {'已啟用' if ENABLE_BURST_REPLY else '未啟用'}")

    async def on_message(self, message: discord.Message):
        """訊息事件處理器：支援監聽頻道批次提煉與主頻道 Burst 聚合回覆緩衝區"""
        if message.author.bot or message.author == self.user:
            return

        clean_text = message.clean_content.strip()
        # 檢查是否為略過/旁白前綴（如 #, ＃, // 等，完全繞過監聽、記錄與回覆）
        if clean_text and any(clean_text.startswith(prefix) for prefix in IGNORE_PREFIXES):
            logger.debug(f"[繞過略過] 訊息以忽略前綴開頭，不予監聽與回覆: {clean_text}")
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
                    "timestamp": int(message.created_at.timestamp()),
                    "mentions": [
                        {"user_id": str(u.id), "user_name": u.display_name}
                        for u in message.mentions
                    ]
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

    async def _resolve_voice_context(self, author) -> Optional[dict]:
        """
        取得發言人所在語音頻道的在場者資訊。發言人不在任何語音頻道時回傳 None。

        **刻意只認發言人自己所在的頻道**，不去猜「人數最多的語音頻道」。這讓推薦對象
        的定義沒有歧義（發言人與同頻道的其他人必然重疊），也符合專案一貫的
        「寧可找不到也不猜」原則。代價是「在文字頻道幫語音裡的人點歌」不會觸發。

        成員來源用 `channel.voice_states` 而非 `channel.members`：後者的實作是
        `guild.get_member(uid)`，**快取沒有就靜默跳過**，而 members 是特權 intent
        且目前關閉——bot 重啟後若有人已在語音頻道中，`members` 可能回傳空清單。
        `voice_states` 直接來自語音狀態快取，不依賴成員快取。

        名稱解析兩段式：Discord 快取優先（權威且即時），退回我們自己的畫像記錄。
        """
        if not ENABLE_MUSIC_SUGGESTION:
            return None

        voice_state = getattr(author, "voice", None)
        channel = getattr(voice_state, "channel", None)
        if channel is None:
            return None

        member_ids = [str(uid) for uid in getattr(channel, "voice_states", {}).keys()]
        if not member_ids:
            return None

        # 名稱與別名：先查 Discord 快取，缺的再用自己的畫像補
        profiles = await MemoryManager.get_user_profiles_batch(member_ids)
        guild = getattr(channel, "guild", None)

        members = []
        for uid in member_ids[:VOICE_MEMBERS_MAX]:
            profile = profiles.get(uid) or {}
            cached = guild.get_member(int(uid)) if (guild and uid.isdigit()) else None
            name = (
                getattr(cached, "display_name", None)
                or profile.get("user_name")
                or "群友"
            )
            members.append({
                "user_id": uid,
                "user_name": name,
                "aliases": profile.get("aliases", [])
            })

        return {"channel_name": getattr(channel, "name", "語音頻道"), "members": members}

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

            # 提煉一律在回覆送出後由統一入口處理（見 MemoryExtractor.extract_dialogue）。
            # 先前這裡有一個「回覆前 JIT」迴圈，會與回覆後的收尾提煉重複處理同一批訊息，
            # 且對每位使用者都傳入最後發言者的名字，導致畫像被改成別人的名字。

            # 取出短期記憶與近期訊息 ID
            short_term = await MemoryManager.get_short_term_context(channel_id)
            recent_msg_ids = [str(m.get("message_id")) for m in short_term if m.get("message_id")]

            # 組合本批訊息文本供多用戶畫像檢索
            combined_content = " \n ".join([m.clean_content.strip() for m in messages if m.clean_content.strip()])

            # Discord 權威 @提及清單。訊息內容存的是 clean_content（<@123> 已被 Discord
            # 轉寫成 @顯示名稱），因此必須從 message.mentions 取得，不能對內容做正則。
            explicit_mentions = [
                {"user_id": str(u.id), "user_name": u.display_name}
                for m in messages for u in m.mentions
            ]

            # 語音頻道現況（發言人不在語音頻道時為 None，整塊不進 prompt）
            voice_context = await self._resolve_voice_context(latest_msg.author)
            voice_member_ids = (
                [m["user_id"] for m in voice_context["members"]] if voice_context else []
            )

            # 多人記憶檢索 (A + B + D + C 混合方案)
            current_user_profile, other_user_profiles = await MemoryManager.resolve_multi_user_profiles(
                current_user_id=latest_user_id,
                content=combined_content,
                short_term_history=short_term,
                max_others=4,
                explicit_mentions=explicit_mentions,
                voice_member_ids=voice_member_ids
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
                other_user_profiles=other_user_profiles,
                voice_context=voice_context
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
            image_bytes_list, image_mime_types = await download_image_attachments(all_attachments)

            # 打字中提示
            if SHOW_TYPING:
                async with channel.typing():
                    response_text = await self.gemini.generate_response(
                        prompt=prompt,
                        images=image_bytes_list if image_bytes_list else None,
                        image_mime_types=image_mime_types if image_mime_types else None
                    )
            else:
                response_text = await self.gemini.generate_response(
                    prompt=prompt,
                    images=image_bytes_list if image_bytes_list else None,
                    image_mime_types=image_mime_types if image_mime_types else None
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

                # 非同步背景提煉記憶與好感度（單人／多人皆走統一入口，由它決定引擎與白名單）
                dialogue_batch = [
                    {
                        "message_id": str(m.id),
                        "channel_id": str(channel_id),
                        "user_id": str(m.author.id),
                        "user_name": m.author.display_name,
                        "content": m.clean_content,
                        "has_image": bool(m.attachments),
                        "timestamp": int(m.created_at.timestamp()),
                        # 權威 @提及（含顯示名稱），供提煉端解析白名單與權威名稱
                        "mentions": [
                            {"user_id": str(u.id), "user_name": u.display_name}
                            for u in m.mentions
                        ]
                    }
                    for m in messages
                ]
                asyncio.create_task(
                    self.memory_extractor.extract_dialogue(
                        messages=dialogue_batch,
                        channel_id=channel_id
                    )
                )

        except Exception as e:
            logger.error(f"對話處理失敗: {e}", exc_info=True)
