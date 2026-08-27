import asyncio
import logging
from typing import Dict, List, Any, Callable, Optional, Set
import discord

logger = logging.getLogger("friend_bot.burst")

class BurstBufferManager:
    """
    多人群聊短時熱絡 (Burst) 緩衝管理器
    - 監聽指定頻道的發言流，並以滑動時間窗口收集多位群友發言。
    - 當窗口內發言人數 >= min_user_count（預設 2 人）時判定為 Burst，彙整後調用回呼處理並執行動態引用。
    - 若僅有單人發言，則在極短時間內快速釋放，避免單人對話產生不必要之延遲。
    """

    def __init__(
        self,
        window_seconds: float = 4.5,
        min_user_count: int = 2,
        max_burst_messages: int = 5
    ):
        self.window_seconds = window_seconds
        self.min_user_count = min_user_count
        self.max_burst_messages = max_burst_messages

        # channel_id -> List of discord.Message
        self._buffers: Dict[str, List[discord.Message]] = {}
        # channel_id -> asyncio.Task
        self._tasks: Dict[str, asyncio.Task] = {}
        # 鎖保護
        self._lock = asyncio.Lock()

    async def add_message(
        self,
        message: discord.Message,
        on_flush: Callable[[str, List[discord.Message], bool], Any]
    ) -> None:
        """
        將新訊息加入頻道的熱絡緩衝池，並重新排程或提早觸發 Flush
        """
        channel_id = str(message.channel.id)

        async with self._lock:
            if channel_id not in self._buffers:
                self._buffers[channel_id] = []

            self._buffers[channel_id].append(message)
            current_msgs = self._buffers[channel_id]
            distinct_users: Set[int] = set(m.author.id for m in current_msgs)
            user_count = len(distinct_users)
            msg_count = len(current_msgs)

            is_burst = user_count >= self.min_user_count

            # 若達到訊息上限（例如 5 則），立即提早觸發
            if msg_count >= self.max_burst_messages:
                logger.info(f"⚡ [Burst 滿載] 頻道 #{channel_id} 累積滿 {msg_count} 則訊息 (人數: {user_count})，立即觸發回覆")
                if channel_id in self._tasks and not self._tasks[channel_id].done():
                    self._tasks[channel_id].cancel()
                
                msgs_to_flush = list(self._buffers.pop(channel_id, []))
                asyncio.create_task(self._safe_execute_flush(on_flush, channel_id, msgs_to_flush, is_burst))
                return

            # 如果目前只有單人發言，等待時間縮短為 1.2 秒（讓同一人連續兩句話稍微聚合，若無其他人即時響應）
            # 若已有 2 人以上發言，則等待完整的 window_seconds (例如 4.5 秒)
            delay = self.window_seconds if is_burst else 1.2

            # 取消既有的定時任務並重設
            if channel_id in self._tasks and not self._tasks[channel_id].done():
                self._tasks[channel_id].cancel()

            async def _wait_and_flush():
                try:
                    await asyncio.sleep(delay)
                    async with self._lock:
                        pending_msgs = self._buffers.pop(channel_id, [])
                    if pending_msgs:
                        p_distinct = len(set(m.author.id for m in pending_msgs))
                        p_is_burst = p_distinct >= self.min_user_count
                        logger.info(
                            f"⏱️ [窗口到期] 頻道 #{channel_id} 釋放 {len(pending_msgs)} 則訊息 "
                            f"(人數: {p_distinct}, Burst={p_is_burst})"
                        )
                        await self._safe_execute_flush(on_flush, channel_id, pending_msgs, p_is_burst)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Burst 緩衝調度異常: {e}", exc_info=True)

            self._tasks[channel_id] = asyncio.create_task(_wait_and_flush())

    async def _safe_execute_flush(
        self,
        callback: Callable[[str, List[discord.Message], bool], Any],
        channel_id: str,
        messages: List[discord.Message],
        is_burst: bool
    ) -> None:
        try:
            res = callback(channel_id, messages, is_burst)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            logger.error(f"執行 Burst 回呼處理時發生異常: {e}", exc_info=True)
