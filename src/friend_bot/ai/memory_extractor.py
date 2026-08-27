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
    """非同步背景記憶提煉器：支援單則提煉、監聽頻道多輪批次提煉、JIT 按需統合、好感度評估與事實增量保護"""

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        self.ai = gemini_client or GeminiClient()
        self._listen_queue: Dict[str, List[Dict[str, Any]]] = {}
        self._debounce_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def _safe_apply_updates(self, updates: List[Dict[str, Any]], default_user_name: str = "") -> None:
        """核心安全合併管線：歷史事實永久保護 + remove_facts 精準更正 + 隱密好感度計算與印象演進"""
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

            # 【安全更正與移除機制】：處理需要被精準剔除的舊事實
            remove_clean = [
                str(rf).strip().lower()
                for rf in remove_facts_raw
                if str(rf).strip()
            ] if isinstance(remove_facts_raw, list) else []

            if remove_clean:
                filtered_cur_facts = [
                    f for f in cur_facts
                    if not any(rf in f.lower() or f.lower() in rf for rf in remove_clean)
                ]
            else:
                filtered_cur_facts = list(cur_facts)

            # 【增量聯集合併】：新事實 + 過濾後的歷史事實（確保歷史事實永不被覆蓋洗白）
            incoming_clean_facts = (
                [str(f).strip() for f in incoming_facts_raw if str(f).strip()]
                if isinstance(incoming_facts_raw, list) else []
            )
            merged_facts = list(dict.fromkeys(filtered_cur_facts + incoming_clean_facts))

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
                "facts": speaker_profile.get("facts", []) if speaker_profile else [],
                "interaction_notes": speaker_profile.get("interaction_notes", "") if speaker_profile else "",
                "favorability": speaker_profile.get("favorability", DEFAULT_FAVORABILITY) if speaker_profile else DEFAULT_FAVORABILITY
            }

            other_users_info = []
            if other_users:
                for u in other_users:
                    if str(u.get("user_id")) != str(user_id):
                        other_users_info.append(u)

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

            parsed = json.loads(cleaned_json_str)
            updates = parsed.get("updates", [])
            await self._safe_apply_updates(updates, default_user_name=user_name)

        except Exception as e:
            logger.debug(f"單次記憶提煉略過: {e}")

    async def extract_from_dialogue_batch(
        self,
        messages: List[Dict[str, Any]]
    ) -> None:
        """
        【批次多輪對話提煉】：一次性消化多條累積訊息，全局提煉多個群友特徵與好感度並標記 extracted=1
        """
        if not ENABLE_AUTO_MEMORY_EXTRACTION or not messages:
            return

        try:
            # 1. 蒐集這段對話中出現過的所有用戶 ID
            user_ids = list(set(str(m.get("user_id")).strip() for m in messages if m.get("user_id")))
            if not user_ids:
                return

            # 2. 批次查詢這些用戶的既有畫像
            known_profiles_dict = await MemoryManager.get_user_profiles_batch(user_ids)
            known_profiles = list(known_profiles_dict.values())

            # 3. 建立多輪對話批次提煉 Prompt
            prompt = build_batch_dialogue_extraction_prompt(
                dialogue_messages=messages,
                known_profiles=known_profiles
            )

            # 4. 呼叫 Gemini 進行全局分析
            raw_result = await self.ai.generate_response(
                prompt=prompt,
                system_instruction="你是一個嚴謹的資料分析器，請以乾淨的 JSON 格式輸出批次對話記憶提煉與好感度評估結果，禁止任何無關廢話。",
                temperature=0.2,
                max_tokens=2048,
                enable_tools=False
            )

            cleaned_json_str = raw_result.strip()
            if "```" in cleaned_json_str:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned_json_str)
                if match:
                    cleaned_json_str = match.group(1).strip()

            parsed = json.loads(cleaned_json_str)
            updates = parsed.get("updates", [])

            # 5. 走統一的安全合併管線
            await self._safe_apply_updates(updates)

            # 6. 批次標記訊息為 extracted = 1
            msg_ids = [str(m["message_id"]) for m in messages if m.get("message_id")]
            if msg_ids:
                await MemoryManager.mark_messages_extracted(msg_ids)
                logger.info(f"📊 [批次記憶提煉完成] 已消化 {len(msg_ids)} 則累積訊息")

        except Exception as e:
            logger.error(f"批次對話提煉失敗: {e}")

    async def process_user_unextracted_messages(self, user_id: str) -> None:
        """
        【JIT 按需統合提煉】：當某用戶在主頻道發言時，優先消化其在監聽頻道累積的未提煉發言
        """
        if not ENABLE_AUTO_MEMORY_EXTRACTION:
            return
        try:
            unextracted = await MemoryManager.get_unextracted_messages_by_user(user_id, limit=20)
            if unextracted:
                logger.info(f"⚡ [JIT 按需提煉] 觸發用戶 [{user_id}] 的 {len(unextracted)} 則待處理訊息提煉")
                await self.extract_from_dialogue_batch(unextracted)
        except Exception as e:
            logger.debug(f"JIT 按需提煉略過: {e}")

    async def process_unextracted_channel_messages(self, channel_id: Optional[str] = None, limit: int = 30) -> None:
        """消化指定頻道或全域累積的未提煉訊息"""
        if not ENABLE_AUTO_MEMORY_EXTRACTION:
            return
        try:
            unextracted = await MemoryManager.get_unextracted_messages(channel_id=channel_id, limit=limit)
            if unextracted:
                await self.extract_from_dialogue_batch(unextracted)
        except Exception as e:
            logger.debug(f"頻道批次提煉略過: {e}")

    # ==================== 監聽頻道隊列與防抖調度 ====================
    def add_to_listen_queue(self, channel_id: str, message_dict: Dict[str, Any]) -> None:
        """將監聽頻道訊息加入防抖緩衝隊列（累積滿 15 則或靜默 10 分鐘自動消化）"""
        cid = str(channel_id)
        if cid not in self._listen_queue:
            self._listen_queue[cid] = []
        self._listen_queue[cid].append(message_dict)

        # 若已累積滿 15 則，立即排程非同步處理
        if len(self._listen_queue[cid]) >= 15:
            batch = list(self._listen_queue[cid])
            self._listen_queue[cid] = []
            asyncio.create_task(self.extract_from_dialogue_batch(batch))
            return

        # 否則重新設置 10 分鐘防抖計時器
        if cid in self._debounce_tasks and not self._debounce_tasks[cid].done():
            self._debounce_tasks[cid].cancel()

        async def _delayed_flush():
            try:
                await asyncio.sleep(600)  # 10 分鐘靜默後自動批次提煉
                async with self._lock:
                    pending = self._listen_queue.pop(cid, [])
                if pending:
                    await self.extract_from_dialogue_batch(pending)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"防抖消化略過: {e}")

        self._debounce_tasks[cid] = asyncio.create_task(_delayed_flush())
