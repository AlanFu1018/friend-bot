import asyncio
import random
import time
from typing import Optional, TYPE_CHECKING
import aiohttp
import discord

from src.friend_bot.core.logger import get_logger
from src.friend_bot.core.config import BOT_NAME, CALENDAR_WEBHOOK_URL, CALENDAR_AVATAR_URL
from src.friend_bot.bot.utils.calendar.calendar_manager import CalendarManager
from src.friend_bot.memory.memory_manager import MemoryManager

if TYPE_CHECKING:
    from src.friend_bot.bot.client import FriendBotClient

logger = get_logger("calendar_scheduler")

FALLBACK_KURISU_CALENDAR_QUOTES = [
    "喂！{mention}，你之前在行事曆安排的這個時間「{content}」到了！別給我裝作沒看見，快去處理啦！",
    "哼，{mention}！行事曆時間已經到了。你設定的「{content}」，我可是分毫不差地提醒你了，別一副漫不經心的樣子！",
    "{mention}！可別誤會了，我才不是特別想幫你記著，只是身為科學家對行事曆一向嚴謹而已！總之「{content}」的時間到了！",
    "喂，{mention}，行事曆時間到了！你安排的「{content}」時間到了，趕緊去完成，真是拿你沒辦法……"
]

class CalendarScheduler:
    """牧瀨紅莉栖行事曆與 Webhook 定時後台調度器"""

    def __init__(self, client: "FriendBotClient", check_interval: float = 5.0):
        self.client = client
        self.check_interval = check_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        """啟動行事曆背景檢查服務"""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info("📅 [CalendarScheduler] 行事曆與 Webhook 定時服務已啟動。")

    def stop(self):
        """停止行事曆背景檢查服務"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("📅 [CalendarScheduler] 行事曆與 Webhook 定時服務已停止。")

    async def _run_loop(self):
        """後台輪詢迴圈"""
        await self.client.wait_until_ready()
        
        while self._running:
            try:
                current_ts = int(time.time())
                due_events = await CalendarManager.get_due_events(current_ts)
                for event in due_events:
                    await self._process_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"📅 [CalendarScheduler] 輪詢行事曆時發生異常: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval)

    async def _generate_kurisu_reminder(self, user_name: str, content: str, time_str: str) -> str:
        """透過 Gemini AI 以牧瀨紅莉栖的性格生成專屬提醒台詞"""
        prompt = f"""【任務：行事曆定時提醒對白生成】
你現在完全沉浸在牧瀨紅莉栖（Makise Kurisu）的人設中。
用戶【{user_name}】之前在行事曆中安排了這個時刻（{time_str}）的排程。
現在時間到了！

【用戶的排程事項】:
「{content}」

【生成指示】:
1. 請以紅莉栖經典的「傲嬌、口是心非但其實很重感情、嚴謹聰明」的風格，說出一段 1~3 句的提醒對白。
2. 語氣要像真人群友用通訊軟體提醒他一樣，催促他快去處理這件事。
3. 嚴禁任何旁白描寫（如 *雙手抱胸* 等），只輸出純對白。
4. 字數簡短有力（50 字以內），不要過於冗長。

紅莉栖的提醒對白:"""

        try:
            if hasattr(self.client, "gemini") and self.client.gemini:
                reply = await self.client.gemini.generate_response(
                    prompt=prompt,
                    temperature=0.85,
                    max_tokens=150,
                    enable_tools=False
                )
                clean_reply = reply.strip().strip('"').strip('「').strip('」')
                if clean_reply and len(clean_reply) > 5 and "發生了一點小狀況" not in clean_reply:
                    return clean_reply
        except Exception as e:
            logger.warning(f"📅 生成紅莉栖行事曆提醒台詞失敗: {e}，改用 Fallback 台詞")

        tmpl = random.choice(FALLBACK_KURISU_CALENDAR_QUOTES)
        return tmpl.format(mention=f"@{user_name}", content=content)

    async def _send_via_webhook(self, webhook_url: str, user_id: str, speech: str, content: str, time_str: str, event_id: int) -> bool:
        """透過 HTTP Webhook 發送自訂紅莉栖身份的提醒訊息"""
        avatar = CALENDAR_AVATAR_URL or (self.client.user.display_avatar.url if self.client.user and self.client.user.display_avatar else "")
        
        embed_dict = {
            "title": "⏰【牧瀨紅莉栖的行事曆排程提醒】",
            "description": f"🔔 <@{user_id}>\n\n**「{speech}」**",
            "color": 0xB22222,
            "fields": [
                {"name": "📌 排程內容", "value": f"```{content}```", "inline": False},
                {"name": "⏳ 排程時間", "value": f"`{time_str}`", "inline": True},
                {"name": "🔬 提醒者", "value": f"`{BOT_NAME} (Labmem No.004)`", "inline": True}
            ],
            "footer": {"text": f"排程編號: #{event_id} • Webhook 命運石之門行事曆系統"}
        }
        if avatar:
            embed_dict["thumbnail"] = {"url": avatar}

        payload = {
            "username": f"{BOT_NAME} (牧瀨紅莉栖)",
            "avatar_url": avatar,
            "content": f"🚨 **叮鈴鈴！行事曆時間到了！** <@{user_id}>",
            "embeds": [embed_dict]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in (200, 204):
                        logger.info(f"📅 [Webhook 發送成功] 排程 ID:{event_id} 已送至 Webhook")
                        return True
                    else:
                        text = await resp.text()
                        logger.warning(f"📅 Webhook 發送回傳狀態 {resp.status}: {text}")
                        return False
        except Exception as e:
            logger.error(f"📅 Webhook 發送異常: {e}")
            return False

    async def _process_event(self, event: dict):
        """觸發並發送單個行事曆事件"""
        event_id = event["id"]
        channel_id = event["channel_id"]
        user_id = event["user_id"]
        user_name = event["user_name"]
        content = event["content"]
        time_str = event.get("target_time_str", "現在")
        custom_webhook = event.get("webhook_url", "").strip()

        # 標記為已觸發
        await CalendarManager.mark_event_triggered(event_id)
        logger.info(f"📅 [行事曆觸發] ID:{event_id} 目標用戶:{user_name}({user_id}) 內容:「{content}」")

        # 生成紅莉栖台詞
        speech = await self._generate_kurisu_reminder(user_name, content, time_str)

        # 優先嘗試 Webhook 推送
        target_webhook = custom_webhook or CALENDAR_WEBHOOK_URL
        webhook_sent = False
        if target_webhook:
            webhook_sent = await self._send_via_webhook(
                webhook_url=target_webhook,
                user_id=user_id,
                speech=speech,
                content=content,
                time_str=time_str,
                event_id=event_id
            )

        # Fallback 透過頻道發送
        if not webhook_sent:
            channel = self.client.get_channel(int(channel_id)) if channel_id else None
            if not channel and channel_id:
                try:
                    channel = await self.client.fetch_channel(int(channel_id))
                except Exception as e:
                    logger.error(f"📅 無法獲取頻道 {channel_id} 發送行事曆 ID:{event_id}: {e}")
                    return

            if channel:
                embed = discord.Embed(
                    title="⏰【牧瀨紅莉栖的行事曆排程提醒】",
                    description=f"🔔 <@{user_id}>\n\n**「{speech}」**",
                    color=0xB22222
                )
                embed.add_field(name="📌 排程內容", value=f"```{content}```", inline=False)
                embed.add_field(name="⏳ 排程時間", value=f"`{time_str}`", inline=True)
                embed.add_field(name="🔬 提醒者", value=f"`{BOT_NAME} (Labmem No.004)`", inline=True)
                if self.client.user and self.client.user.display_avatar:
                    embed.set_thumbnail(url=self.client.user.display_avatar.url)
                embed.set_footer(text=f"排程編號: #{event_id} • 命運石之門行事曆系統")

                try:
                    msg = await channel.send(
                        content=f"🚨 **叮鈴鈴！行事曆時間到了！** <@{user_id}>",
                        embed=embed
                    )
                    full_record_text = f"【行事曆排程觸發】提醒 @{user_name} 事項：{content}。紅莉栖提醒台詞：{speech}"
                    await MemoryManager.save_message(
                        message_id=str(msg.id),
                        channel_id=str(channel_id),
                        user_id=str(self.client.user.id if self.client.user else "bot"),
                        user_name=BOT_NAME,
                        content=full_record_text,
                        has_image=False,
                        is_bot=True,
                        timestamp=int(time.time())
                    )
                except Exception as e:
                    logger.error(f"📅 發送行事曆訊息至頻道失敗: {e}", exc_info=True)
