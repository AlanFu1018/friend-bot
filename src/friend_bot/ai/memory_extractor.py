import asyncio
import json
import re
from typing import List, Dict, Any, Optional
from src.friend_bot.memory.memory_manager import MemoryManager
from src.friend_bot.core.logger import get_logger
from .prompts import build_multi_entity_extraction_prompt, build_batch_dialogue_extraction_prompt
from .gemini_client import GeminiClient
from src.friend_bot.core.config import (
    ENABLE_AUTO_MEMORY_EXTRACTION,
    ENABLE_FAVORABILITY,
    DEFAULT_FAVORABILITY,
    DAILY_GAIN_LIMIT,
    DAILY_LOSS_LIMIT
)

logger = get_logger("extractor")

class MemoryExtractor:
    """非同步背景記憶提煉器：支援單則提煉、監聽頻道多輪批次提煉、JIT 按需整合、好感度評估與三軌事實加權"""

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        self.ai = gemini_client or GeminiClient()
        self._listen_queue: Dict[str, List[Dict[str, Any]]] = {}
        self._debounce_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def _safe_apply_updates(self, updates: List[Dict[str, Any]], default_user_name: str = "") -> None:
        """核心安全合併管線：歷史事實永久保護 + remove_facts 精準更正 + 提煉重複加權 + 隱密好感度計算與印象演進"""
        if not isinstance(updates, list) or not updates:
            return

        known_name_map = await MemoryManager.get_known_users_map()

        for update_item in updates:
            target_uid = str(update_item.get("user_id", "")).strip()
            target_name = str(update_item.get("user_name", "")).strip()
            incoming_facts_raw = update_item.get("facts", [])
            remove_facts_raw = update_item.get("remove_facts", [])
            notes = update_item.get("interaction_notes", "")
            raw_fav_delta = update_item.get("favorability_delta", 0)

            # 若 JSON 未給出明確 user_id，嘗試從 known_name_map 補全
            if not target_uid or target_uid == "None":
                if target_name.lower() in known_name_map:
                    target_uid = known_name_map[target_name.lower()]

            if not target_uid:
                continue

            # 取得目前已有的畫像
            current_p = await MemoryManager.get_user_profile(target_uid)
            cur_facts = current_p.get("facts", []) if current_p else []
            cur_notes = current_p.get("interaction_notes", "") if current_p else ""
            cur_fav = current_p.get("favorability", DEFAULT_FAVORABILITY) if current_p else DEFAULT_FAVORABILITY
            cur_tier = current_p.get("relationship_tier", "familiar") if current_p else "familiar"
            cur_daily_gain = current_p.get("daily_favorability_gain", 0) if current_p else 0
            cur_gain_date = current_p.get("last_gain_date", "") if current_p else ""

            # 【安全更正與增量合併】：透過 MemoryManager.merge_facts 處理更正、剔除與 hits 加權
            merged_facts = MemoryManager.merge_facts(
                current_facts_raw=cur_facts,
                incoming_facts_raw=incoming_facts_raw,
                remove_facts_raw=remove_facts_raw
            )

            # 互動印象保護
            merged_notes = str(notes).strip() if (notes and str(notes).strip()) else cur_notes

            # 【好感度與關係階級計算】
            try:
                delta_int = int(raw_fav_delta)
            except (ValueError, TypeError):
                delta_int = 0

            if ENABLE_FAVORABILITY and delta_int != 0:
                new_fav, new_tier, new_daily_gain, today_str = MemoryManager.calculate_favorability_update(
                    current_score=cur_fav,
                    current_daily_gain=cur_daily_gain,
                    last_gain_date=cur_gain_date,
                    delta=delta_int,
                    gain_limit=DAILY_GAIN_LIMIT,
                    loss_limit=DAILY_LOSS_LIMIT
                )
            else:
                new_fav = cur_fav
                new_tier = cur_tier
                new_daily_gain = cur_daily_gain
                today_str = cur_gain_date

            # 判斷是否有實質變更需寫回資料庫
            has_changes = (
                merged_facts != cur_facts or
                merged_notes != cur_notes or
                new_fav != cur_fav or
                new_tier != cur_tier or
                new_daily_gain != cur_daily_gain or
                current_p is None
            )

            if has_changes:
                final_user_name = target_name or (current_p.get("user_name") if current_p else default_user_name or target_name)
                await MemoryManager.update_user_profile(
                    user_id=target_uid,
                    user_name=final_user_name,
                    facts=merged_facts,
                    interaction_notes=merged_notes,
                    favorability=new_fav,
                    relationship_tier=new_tier,
                    daily_favorability_gain=new_daily_gain,
                    last_gain_date=today_str
                )
                logger.info(
                    f"🧠 [畫像/好感更新] 用戶 [{final_user_name} ({target_uid})] "
                    f"好感度: {cur_fav} -> {new_fav} ({new_tier}), 今日增量: {new_daily_gain}/{DAILY_GAIN_LIMIT}, "
                    f"facts={len(merged_facts)} 條"
                )

    async def extract_and_update(
        self,
        user_id: str,
        user_name: str,
        recent_messages: List[str],
        other_users: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """單次即時對話提煉（用於即時回覆頻道的非同步背景分析）"""
        if not ENABLE_AUTO_MEMORY_EXTRACTION or not recent_messages:
            return

        try:
            speaker_profile = await MemoryManager.get_user_profile(user_id)
            speaker_info = {
                "user_id": str(user_id),
                "user_name": user_name,
                "facts": [f["text"] for f in speaker_profile.get("facts", [])] if speaker_profile else [],
                "interaction_notes": speaker_profile.get("interaction_notes", "") if speaker_profile else "",
                "favorability": speaker_profile.get("favorability", DEFAULT_FAVORABILITY) if speaker_profile else DEFAULT_FAVORABILITY
            }

            other_users_info = []
            if other_users:
                for u in other_users:
                    if str(u.get("user_id")) != str(user_id):
                        u_facts = u.get("facts", [])
                        fact_strs = [f["text"] if isinstance(f, dict) else str(f) for f in u_facts]
                        o_copy = dict(u)
                        o_copy["facts"] = fact_strs
                        other_users_info.append(o_copy)

            prompt = build_multi_entity_extraction_prompt(
                speaker=speaker_info,
                other_users=other_users_info,
                recent_messages=recent_messages
            )

            raw_result = await self.ai.generate_response(
                prompt=prompt,
                system_instruction="你是一個嚴謹的資料分析器，請以乾淨的 JSON 格式輸出多實體記憶提取與好感度評估結果，禁止任何無關廢話。",
                temperature=0.2,
                max_tokens=1536,
                enable_tools=False
            )

            cleaned_json_str = raw_result.strip()
            if "```" in cleaned_json_str:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned_json_str)
                if match:
                    cleaned_json_str = match.group(1).strip()

            data = json.loads(cleaned_json_str)
            updates = data.get("updates", [])
            await self._safe_apply_updates(updates, default_user_name=user_name)

        except Exception as e:
            logger.warning(f"單次記憶提煉與好感度評估失敗 (User: {user_name}): {e}")

    async def add_listen_message(
        self,
        channel_id: str,
        message_data: Dict[str, Any],
        debounce_seconds: float = 4.0
    ) -> None:
        """監聽頻道訊息防抖收集器"""
        if not ENABLE_AUTO_MEMORY_EXTRACTION:
            return

        async with self._lock:
            if channel_id not in self._listen_queue:
                self._listen_queue[channel_id] = []
            self._listen_queue[channel_id].append(message_data)

            if channel_id in self._debounce_tasks and not self._debounce_tasks[channel_id].done():
                self._debounce_tasks[channel_id].cancel()

            self._debounce_tasks[channel_id] = asyncio.create_task(
                self._debounced_process_listen_channel(channel_id, debounce_seconds)
            )

    async def _debounced_process_listen_channel(self, channel_id: str, delay: float) -> None:
        """防抖倒數完成後執行批次提煉"""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        async with self._lock:
            messages_to_process = self._listen_queue.pop(channel_id, [])

        if not messages_to_process:
            return

        await self._process_batch_extraction(channel_id, messages_to_process)

    async def _process_batch_extraction(self, channel_id: str, messages: List[Dict[str, Any]]) -> None:
        """執行監聽頻道多輪交談批次提煉"""
        try:
            user_ids = list(set(str(m.get("user_id")) for m in messages if m.get("user_id") and not m.get("is_bot")))
            profiles_dict = await MemoryManager.get_user_profiles_batch(user_ids)
            known_profiles = []
            for p in profiles_dict.values():
                p_copy = dict(p)
                p_copy["facts"] = [f["text"] if isinstance(f, dict) else str(f) for f in p.get("facts", [])]
                known_profiles.append(p_copy)

            prompt = build_batch_dialogue_extraction_prompt(
                dialogue_messages=messages,
                known_profiles=known_profiles
            )

            raw_result = await self.ai.generate_response(
                prompt=prompt,
                system_instruction="你是一個專精 Discord 群聊分析的記憶提煉器，請以標準 JSON 輸出 updates 清單。",
                temperature=0.2,
                max_tokens=2048,
                enable_tools=False
            )

            cleaned_json_str = raw_result.strip()
            if "```" in cleaned_json_str:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned_json_str)
                if match:
                    cleaned_json_str = match.group(1).strip()

            data = json.loads(cleaned_json_str)
            updates = data.get("updates", [])
            await self._safe_apply_updates(updates)

            msg_ids = [str(m.get("message_id")) for m in messages if m.get("message_id")]
            await MemoryManager.mark_messages_extracted(msg_ids)
            logger.info(f"✅ [監聽頻道批次提煉完成] 頻道 [{channel_id}] 共處理 {len(messages)} 則訊息")

        except Exception as e:
            logger.warning(f"監聽頻道批次提煉失敗 (Channel: {channel_id}): {e}")

    async def extract_from_dialogue_batch(self, messages: List[Dict[str, Any]]) -> None:
        """直接對一批對話訊息進行批次提煉（供測試或手動排程）"""
        if not messages:
            return
        channel_id = str(messages[0].get("channel_id", "default_channel"))
        await self._process_batch_extraction(channel_id, messages)

    async def process_unextracted_for_user(self, user_id: str, user_name: str) -> None:
        """主頻道 JIT 按需即時提煉"""
        if not ENABLE_AUTO_MEMORY_EXTRACTION:
            return

        try:
            unextracted = await MemoryManager.get_unextracted_messages_by_user(user_id=str(user_id), limit=15)
            if not unextracted:
                return

            recent_texts = [str(m.get("content", "")) for m in unextracted if str(m.get("content", "")).strip()]
            if not recent_texts:
                msg_ids = [str(m.get("message_id")) for m in unextracted if m.get("message_id")]
                await MemoryManager.mark_messages_extracted(msg_ids)
                return

            await self.extract_and_update(
                user_id=user_id,
                user_name=user_name,
                recent_messages=recent_texts
            )

            msg_ids = [str(m.get("message_id")) for m in unextracted if m.get("message_id")]
            await MemoryManager.mark_messages_extracted(msg_ids)
            logger.debug(f"⚡ [JIT 提煉完成] 用戶 [{user_name}] 共補齊 {len(msg_ids)} 則訊息")

        except Exception as e:
            logger.warning(f"JIT 即時提煉失敗 (User: {user_name}): {e}")
