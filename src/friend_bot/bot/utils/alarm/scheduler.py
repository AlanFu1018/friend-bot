import asyncio
import random
import time
from typing import Optional, TYPE_CHECKING
import discord

from src.friend_bot.core.logger import get_logger
from src.friend_bot.core.config import BOT_NAME
from src.friend_bot.bot.utils.alarm.alarm_manager import AlarmManager
from src.friend_bot.memory.memory_manager import MemoryManager

if TYPE_CHECKING:
    from src.friend_bot.bot.client import FriendBotClient

logger = get_logger("alarm_scheduler")

FALLBACK_KURISU_ALARM_QUOTES = [
    "喂！{mention}，你之前不是特地交代我在這個時間提醒你「{content}」嗎？別給我裝作沒看見，快去處理啦！",
    "哼，{mention}！時間已經到了。你交代我的「{content}」，我可是分毫不差地提醒你了，別一副漫不經心的樣子！",
    "{mention}！可別誤會了，我才不是特別想幫你記著，只是身為科學家對時間一向嚴謹而已！總之「{content}」的時間到了！",
    "喂，{mention}，定時提醒時間到了！你設定的「{content}」到了，趕緊去完成，真是拿你沒辦法……"
]

class AlarmScheduler:
    """牧瀨紅莉栖專屬定時鬧鐘後台調度器"""

    def __init__(self, client: "FriendBotClient", check_interval: float = 5.0):
        self.client = client
        self.check_interval = check_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        """啟動定時鬧鐘檢查任務"""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info("⏰ [AlarmScheduler] 定時鬧鐘檢查服務已啟動。")

    def stop(self):
        """停止定時鬧鐘檢查任務"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("⏰ [AlarmScheduler] 定時鬧鐘檢查服務已停止。")

    async def _run_loop(self):
        """後台輪詢迴圈"""
        await self.client.wait_until_ready()
        
        while self._running:
            try:
                current_ts = int(time.time())
                due_alarms = await AlarmManager.get_due_alarms(current_ts)
                for alarm in due_alarms:
                    await self._process_alarm(alarm)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"⏰ [AlarmScheduler] 輪詢鬧鐘時發生異常: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval)

    async def _generate_kurisu_speech(self, user_name: str, content: str, time_str: str) -> str:
        """透過 Gemini AI 以牧瀨紅莉栖的性格生成專屬提醒台詞"""
        prompt = f"""【任務：定時鬧鐘提醒對白生成】
你現在完全沉浸在牧瀨紅莉栖（Makise Kurisu）的人設中。
用戶【{user_name}】之前設定了這個時刻（{time_str}）的定時提醒。
現在時間到了！

【用戶的提醒事項】:
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
            logger.warning(f"⏰ 生成紅莉栖鬧鐘台詞失敗: {e}，改用 Fallback 台詞")

        tmpl = random.choice(FALLBACK_KURISU_ALARM_QUOTES)
        return tmpl.format(mention=f"@{user_name}", content=content)

    async def _process_alarm(self, alarm: dict):
        """觸發並發送單個鬧鐘"""
        alarm_id = alarm["id"]
        channel_id = alarm["channel_id"]
        user_id = alarm["user_id"]
        user_name = alarm["user_name"]
        content = alarm["content"]
        time_str = alarm.get("target_time_str", "現在")

        # 標記已觸發
        await AlarmManager.mark_alarm_triggered(alarm_id)
        logger.info(f"⏰ [鬧鐘觸發] ID:{alarm_id} 目標用戶:{user_name}({user_id}) 內容:「{content}」")

        # 生成紅莉栖台詞
        speech = await self._generate_kurisu_speech(user_name, content, time_str)

        channel = self.client.get_channel(int(channel_id)) if channel_id else None
        if not channel and channel_id:
            try:
                channel = await self.client.fetch_channel(int(channel_id))
            except Exception as e:
                logger.error(f"⏰ 無法獲取頻道 {channel_id} 發送鬧鐘 ID:{alarm_id}: {e}")
                return

        if channel:
            embed = discord.Embed(
                title="⏰【牧瀨紅莉栖的定時提醒】",
                description=f"🔔 <@{user_id}>\n\n**「{speech}」**",
                color=0xB22222
            )
            embed.add_field(name="📌 提醒事項", value=f"```{content}```", inline=False)
            embed.add_field(name="⏳ 預定時間", value=f"`{time_str}`", inline=True)
            embed.add_field(name="🔬 提醒者", value=f"`{BOT_NAME} (Labmem No.004)`", inline=True)
            if self.client.user and self.client.user.display_avatar:
                embed.set_thumbnail(url=self.client.user.display_avatar.url)
            embed.set_footer(text=f"鬧鐘編號: #{alarm_id} • 命運石之門定時提醒系統")

            try:
                msg = await channel.send(
                    content=f"🚨 **叮鈴鈴！時間到了！** <@{user_id}>",
                    embed=embed
                )
                full_record_text = f"【定時鬧鐘觸發】提醒 @{user_name} 事項：{content}。紅莉栖提醒台詞：{speech}"
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
                logger.error(f"⏰ 發送鬧鐘訊息至頻道失敗: {e}", exc_info=True)
