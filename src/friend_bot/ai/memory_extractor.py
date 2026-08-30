import asyncio
import json
import re
from collections import defaultdict
from typing import List, Dict, Any, Optional, Set
from src.friend_bot.memory.memory_manager import MemoryManager
from src.friend_bot.core.logger import get_logger
from .prompts import build_multi_entity_extraction_prompt, build_batch_dialogue_extraction_prompt
from .gemini_client import GeminiClient
from src.friend_bot.core.config import (
    ENABLE_AUTO_MEMORY_EXTRACTION,
    ENABLE_FAVORABILITY,
    DEFAULT_FAVORABILITY,
    DAILY_GAIN_LIMIT,
    DAILY_LOSS_LIMIT,
    LISTEN_DEBOUNCE_SECONDS,
    EXTRACTION_SWEEP_INTERVAL_SECONDS,
    ENABLE_ALIAS_LEARNING
)

logger = get_logger("extractor")

class MemoryExtractor:
    """
    非同步背景記憶提煉器。

    【單一調度入口】所有提煉都經由 `extract_dialogue()`，由它統一決定三件事：
      1. 用哪個引擎（發言者 >= 2 人走多人對話引擎，否則走單人主角引擎）
      2. 白名單包含誰（發言者 + 訊息中 @提及／暱稱命中的人）
      3. 誰負責標記 extracted（一律由此入口在提煉成功後統一標記）

    先前「回覆前 JIT」與「回覆後收尾提煉」兩條路徑會對同一批訊息各處理一次，造成
    好感度雙重計算、事實熱度灌水與 API 成本雙倍；且收尾提煉不標記 extracted，處理過
    的訊息下次還會再被撈一次。統一入口後 JIT 已廢除，殘留的未提煉訊息（提煉失敗、
    或重啟導致監聽佇列遺失）改由低頻的 `sweep_unextracted()` 撿漏。
    """

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        self.ai = gemini_client or GeminiClient()
        self._listen_queue: Dict[str, List[Dict[str, Any]]] = {}
        self._debounce_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: Optional[asyncio.Task] = None
        self._sweeping = False

    # ==================== 統一提煉入口 ====================

    async def extract_dialogue(
        self,
        messages: List[Dict[str, Any]],
        channel_id: str = "",
        authoritative_names: Optional[Dict[str, str]] = None
    ) -> None:
        """
        統一提煉入口。messages 為訊息字典清單（需含 message_id / user_id / user_name / content）。

        提煉成功才標記 extracted；失敗則保留 extracted=0，交由 sweep_unextracted() 之後重試。
        """
        if not ENABLE_AUTO_MEMORY_EXTRACTION or not messages:
            return

        human_msgs = [
            m for m in messages
            if str(m.get("user_id", "")).strip() and not m.get("is_bot")
        ]
        all_msg_ids = [str(m.get("message_id")) for m in messages if m.get("message_id")]

        if not human_msgs:
            await MemoryManager.mark_messages_extracted(all_msg_ids)
            return

        # 發言者（保持出現順序）
        speaker_uids: List[str] = []
        for m in human_msgs:
            uid = str(m["user_id"])
            if uid not in speaker_uids:
                speaker_uids.append(uid)

        # Discord 權威 @提及清單（由呼叫端從 message.mentions 帶入）
        explicit_mentions: List[Dict[str, str]] = []
        seen_mention_uids: Set[str] = set()
        for m in human_msgs:
            for item in (m.get("mentions") or []):
                mid = str(item.get("user_id", "")).strip()
                if mid and mid not in seen_mention_uids:
                    seen_mention_uids.add(mid)
                    explicit_mentions.append(
                        {"user_id": mid, "user_name": str(item.get("user_name") or "").strip()}
                    )

        # 【權威名稱】一律取自 Discord（訊息作者 + 被 @提及者），絕不採用模型輸出的名字。
        # 被提及者也必須納入：否則首次被提及而尚無畫像的人，建檔時名字會退回模型輸出。
        names: Dict[str, str] = dict(authoritative_names or {})
        for m in human_msgs:
            uid = str(m["user_id"])
            msg_name = str(m.get("user_name") or "").strip()
            if msg_name:
                names[uid] = msg_name
        for item in explicit_mentions:
            if item["user_name"]:
                names.setdefault(item["user_id"], item["user_name"])

        combined_content = "\n".join(
            str(m.get("content", "")) for m in human_msgs if str(m.get("content", "")).strip()
        )

        # 【統一白名單】發言者 + 訊息中被提及的人（與回覆端共用同一份解析邏輯）
        mentioned_uids = await MemoryManager.resolve_mentioned_user_ids(
            combined_content,
            exclude_uids=set(speaker_uids),
            explicit_mentions=explicit_mentions
        )
        allowed_uids: Set[str] = set(speaker_uids) | set(mentioned_uids)

        # 別名學習的來源記錄：哪些人在這段對話中發言、位於哪個頻道、起始訊息為何。
        # 別名寫錯會直接表現為「機器人把 X 當成 Y」，必須可稽核、可撤銷。
        provenance = {
            "by": speaker_uids,
            "channel_id": str(channel_id),
            "message_id": all_msg_ids[0] if all_msg_ids else ""
        }

        try:
            if len(speaker_uids) >= 2:
                await self._run_batch_engine(human_msgs, allowed_uids, names, provenance)
            else:
                await self._run_single_engine(
                    speaker_uid=speaker_uids[0],
                    messages=human_msgs,
                    mentioned_uids=mentioned_uids,
                    allowed_uids=allowed_uids,
                    names=names,
                    provenance=provenance
                )
        except Exception as e:
            # 保留 extracted=0，交由背景撿漏重試
            logger.warning(f"記憶提煉失敗 (Channel: {channel_id})，訊息保留待重試: {e}")
            return

        await MemoryManager.mark_messages_extracted(all_msg_ids)
        logger.debug(
            f"✅ [提煉完成] 頻道 [{channel_id}] {len(human_msgs)} 則訊息、"
            f"{len(speaker_uids)} 位發言者（引擎: {'多人對話' if len(speaker_uids) >= 2 else '單人主角'}）"
        )

    async def _run_batch_engine(
        self,
        messages: List[Dict[str, Any]],
        allowed_uids: Set[str],
        names: Dict[str, str],
        provenance: Optional[Dict[str, Any]] = None
    ) -> None:
        """多人對話引擎：整段多輪對話全局分析，參與者平等歸屬"""
        profiles_dict = await MemoryManager.get_user_profiles_batch(list(allowed_uids))
        known_profiles = []
        for p in profiles_dict.values():
            p_copy = dict(p)
            p_copy["facts"] = MemoryManager.to_fact_texts(p.get("facts", []))
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
        updates = self._parse_updates(raw_result)
        await self._safe_apply_updates(
            updates, allowed_uids=allowed_uids, authoritative_names=names, provenance=provenance
        )

    async def _run_single_engine(
        self,
        speaker_uid: str,
        messages: List[Dict[str, Any]],
        mentioned_uids: List[str],
        allowed_uids: Set[str],
        names: Dict[str, str],
        provenance: Optional[Dict[str, Any]] = None
    ) -> None:
        """單人主角引擎：一位明確發言者，附上被提及者的畫像供跨使用者歸屬"""
        speaker_info = await self._build_speaker_info(
            speaker_uid, names.get(str(speaker_uid), "用戶")
        )

        other_users_info: List[Dict[str, Any]] = []
        if mentioned_uids:
            others_dict = await MemoryManager.get_user_profiles_batch(mentioned_uids)
            for uid in mentioned_uids:
                prof = others_dict.get(str(uid))
                if not prof:
                    continue
                o_copy = dict(prof)
                o_copy["facts"] = MemoryManager.to_fact_texts(prof.get("facts", []))
                other_users_info.append(o_copy)

        recent_messages = [
            str(m.get("content", "")) for m in messages if str(m.get("content", "")).strip()
        ]
        await self._call_single_engine(
            speaker_info=speaker_info,
            other_users_info=other_users_info,
            recent_messages=recent_messages,
            allowed_uids=allowed_uids,
            names=names,
            provenance=provenance
        )

    async def _call_single_engine(
        self,
        speaker_info: Dict[str, Any],
        other_users_info: List[Dict[str, Any]],
        recent_messages: List[str],
        allowed_uids: Set[str],
        names: Dict[str, str],
        provenance: Optional[Dict[str, Any]] = None
    ) -> None:
        """單人主角引擎的實際呼叫：組 prompt → 請求模型 → 解析 → 安全套用"""
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
        updates = self._parse_updates(raw_result)
        await self._safe_apply_updates(
            updates, allowed_uids=allowed_uids, authoritative_names=names, provenance=provenance
        )

    @staticmethod
    async def _build_speaker_info(user_id: str, user_name: str) -> Dict[str, Any]:
        """組裝單人引擎所需的發言者資訊（畫像不存在時使用預設值）"""
        profile = await MemoryManager.get_user_profile(user_id)
        return {
            "user_id": str(user_id),
            "user_name": user_name,
            "facts": MemoryManager.to_fact_texts(profile.get("facts", [])) if profile else [],
            "interaction_notes": profile.get("interaction_notes", "") if profile else "",
            "favorability": profile.get("favorability", DEFAULT_FAVORABILITY) if profile else DEFAULT_FAVORABILITY
        }

    @staticmethod
    def _parse_updates(raw_result: str) -> List[Dict[str, Any]]:
        """從模型輸出中解析 updates 清單（容忍 ``` 圍欄）"""
        cleaned = (raw_result or "").strip()
        if "```" in cleaned:
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
            if match:
                cleaned = match.group(1).strip()
        data = json.loads(cleaned)
        updates = data.get("updates", [])
        return updates if isinstance(updates, list) else []

    # ==================== 畫像安全合併 ====================

    async def _safe_apply_updates(
        self,
        updates: List[Dict[str, Any]],
        allowed_uids: Optional[Set[str]] = None,
        authoritative_names: Optional[Dict[str, str]] = None,
        provenance: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        核心安全合併管線：歷史事實永久保護 + remove_facts 精準更正 + 提煉重複加權 + 好感度計算。

        allowed_uids：本次提煉帶入模型上下文的使用者 ID 白名單（發言者 + 被提及者）。
        模型輸出的 user_id 不在白名單內一律拒絕，避免使用者透過訊息內容注入指令操控第三方畫像。

        authoritative_names：user_id -> Discord 顯示名稱。`user_name` **只採用此來源**，
        絕不使用模型輸出的名字——名字是 Discord 的權威資料，讓模型回填會造成畫像被改成
        別人的名字（並連帶讓暱稱索引表塌縮）。模型給的名字僅用於記錄與除錯。
        """
        if not isinstance(updates, list) or not updates:
            return

        names = authoritative_names or {}

        for update_item in updates:
            target_uid = str(update_item.get("user_id", "")).strip()
            model_name = str(update_item.get("user_name", "")).strip()
            incoming_facts_raw = update_item.get("facts", [])
            remove_facts_raw = update_item.get("remove_facts", [])
            notes = update_item.get("interaction_notes", "")
            raw_fav_delta = update_item.get("favorability_delta", 0)

            # 不再以姓名反查補全 user_id：模型在 prompt 中本就拿得到每個人的 ID，
            # 而反查依賴的暱稱索引表本身可能同名碰撞，猜錯的代價是把記憶寫到別人身上。
            if not target_uid or target_uid == "None":
                logger.warning(
                    f"⚠️ [畫像更新已略過] 模型輸出未提供有效 user_id（name={model_name!r}），"
                    f"不進行姓名反查猜測"
                )
                continue

            if allowed_uids is not None and target_uid not in allowed_uids:
                logger.warning(
                    f"⚠️ [畫像更新已拒絕] 模型輸出的 user_id [{target_uid} / {model_name}] "
                    f"不在本次對話上下文白名單內，可能為幻覺或提示詞注入，已略過此筆更新"
                )
                continue

            try:
                delta_int = int(raw_fav_delta)
            except (ValueError, TypeError):
                delta_int = 0

            def _mutator(current_p: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
                cur_facts = current_p.get("facts", []) if current_p else []
                cur_notes = current_p.get("interaction_notes", "") if current_p else ""
                cur_fav = current_p.get("favorability", DEFAULT_FAVORABILITY) if current_p else DEFAULT_FAVORABILITY
                cur_tier = current_p.get("relationship_tier", "familiar") if current_p else "familiar"
                cur_daily_gain = current_p.get("daily_favorability_gain", 0) if current_p else 0
                cur_gain_date = current_p.get("last_gain_date", "") if current_p else ""
                cur_name = current_p.get("user_name", "") if current_p else ""

                merged_facts = MemoryManager.merge_facts(
                    current_facts_raw=cur_facts,
                    incoming_facts_raw=incoming_facts_raw,
                    remove_facts_raw=remove_facts_raw
                )
                merged_notes = str(notes).strip() if (notes and str(notes).strip()) else cur_notes

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
                    new_fav, new_tier = cur_fav, cur_tier
                    new_daily_gain, today_str = cur_daily_gain, cur_gain_date

                # user_name 僅來自 Discord 權威名稱；退而求其次沿用既有畫像的名字
                final_user_name = names.get(target_uid) or cur_name or model_name or "用戶"

                has_changes = (
                    merged_facts != cur_facts or
                    merged_notes != cur_notes or
                    new_fav != cur_fav or
                    new_tier != cur_tier or
                    new_daily_gain != cur_daily_gain or
                    final_user_name != cur_name or
                    current_p is None
                )
                if not has_changes:
                    return None

                logger.info(
                    f"🧠 [畫像/好感更新] 用戶 [{final_user_name} ({target_uid})] "
                    f"好感度: {cur_fav} -> {new_fav} ({new_tier}), 今日增量: {new_daily_gain}/{DAILY_GAIN_LIMIT}, "
                    f"facts={len(merged_facts)} 條"
                )
                return {
                    "user_name": final_user_name,
                    "facts": merged_facts,
                    "interaction_notes": merged_notes,
                    "favorability": new_fav,
                    "relationship_tier": new_tier,
                    "daily_favorability_gain": new_daily_gain,
                    "last_gain_date": today_str
                }

            await MemoryManager.apply_profile_update(target_uid, _mutator)

            # 【別名學習】模型可提議綽號，但必須通過全部校驗才會生效。
            # 條件 3（歸屬對象須在本次對話上下文內）由上方的 allowed_uids 白名單保證：
            # 能走到這裡代表 target_uid 確實在這段對話中出現過（發言或被提及），
            # 因此系統不會替一個從未現身的人建立稱呼。
            if ENABLE_ALIAS_LEARNING:
                await self._apply_alias_proposals(
                    target_uid, update_item.get("aliases", []), provenance or {}
                )

    async def _apply_alias_proposals(
        self,
        target_uid: str,
        proposals: Any,
        provenance: Dict[str, Any]
    ) -> None:
        """
        套用模型提議的別名。校驗與寫入交由 MemoryManager.add_alias 統一處理
        （格式檢查、全站碰撞排除、數量上限），這裡只負責過濾格式與記錄來源。

        每一筆成功寫入都會留下來源（誰的哪則訊息、何時），因為別名寫錯會直接表現為
        「機器人把 X 當成 Y」，必須可稽核、可撤銷。
        """
        if not isinstance(proposals, list) or not proposals:
            return

        for raw in proposals[:5]:   # 單次提煉最多採納 5 筆提議，避免模型灌爆
            alias = str(raw).strip()
            if not alias:
                continue
            ok, reason = await MemoryManager.add_alias(
                user_id=target_uid,
                alias=alias,
                source="extraction",
                by=provenance.get("by", []),
                channel_id=str(provenance.get("channel_id", "")),
                message_id=str(provenance.get("message_id", ""))
            )
            if not ok:
                logger.debug(f"🏷️ [別名提議未採納] ID:{target_uid} 「{alias}」：{reason}")

    # ==================== 對外相容介面 ====================

    async def extract_and_update(
        self,
        user_id: str,
        user_name: str,
        recent_messages: List[str],
        other_users: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        單一發言者的即時提煉。

        注意：正式流程已全面改走 `extract_dialogue()` 統一入口，此方法目前僅供測試與
        手動呼叫使用。它與統一入口共用同一個單人引擎核心（`_call_single_engine`）與
        安全合併管線，因此測試涵蓋的仍是正式路徑實際執行的程式碼。
        """
        if not ENABLE_AUTO_MEMORY_EXTRACTION or not recent_messages:
            return

        other_users_info = [
            {**dict(u), "facts": MemoryManager.to_fact_texts(u.get("facts", []))}
            for u in (other_users or [])
            if str(u.get("user_id")) != str(user_id)
        ]

        names = {str(user_id): user_name}
        for u in other_users_info:
            uid = str(u.get("user_id", ""))
            if uid and u.get("user_name"):
                names[uid] = str(u["user_name"])

        allowed_uids = {str(user_id)} | {
            str(u.get("user_id")) for u in other_users_info if u.get("user_id")
        }

        try:
            speaker_info = await self._build_speaker_info(user_id, user_name)
            await self._call_single_engine(
                speaker_info=speaker_info,
                other_users_info=other_users_info,
                recent_messages=recent_messages,
                allowed_uids=allowed_uids,
                names=names
            )
        except Exception as e:
            logger.warning(f"單次記憶提煉與好感度評估失敗 (User: {user_name}): {e}")

    async def extract_from_dialogue_batch(self, messages: List[Dict[str, Any]]) -> None:
        """對一批對話訊息進行提煉（供測試或手動排程），統一走 extract_dialogue"""
        if not messages:
            return
        channel_id = str(messages[0].get("channel_id", "default_channel"))
        await self.extract_dialogue(messages, channel_id)

    # ==================== 監聽頻道防抖佇列 ====================

    async def add_listen_message(
        self,
        channel_id: str,
        message_data: Dict[str, Any],
        debounce_seconds: float = LISTEN_DEBOUNCE_SECONDS
    ) -> None:
        """監聽頻道訊息防抖收集器（防抖秒數見 config.yaml 的 listen_debounce_seconds）"""
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

        if messages_to_process:
            await self.extract_dialogue(messages_to_process, channel_id)

    # ==================== 背景撿漏（取代原本的 JIT） ====================

    async def sweep_unextracted(self, limit: int = 30) -> int:
        """
        撿漏：處理提煉失敗、或因重啟導致監聽佇列遺失而殘留的未提煉訊息。

        正常情況下各路徑提煉完就會標記 extracted=1，因此這裡通常無事可做；
        它取代了原本掛在「每則訊息」上的 JIT 觸發。
        """
        try:
            pending = await MemoryManager.get_unextracted_messages(limit=limit)
        except Exception as e:
            logger.warning(f"背景撿漏查詢未提煉訊息失敗: {e}")
            return 0

        if not pending:
            return 0

        by_channel: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in pending:
            by_channel[str(m.get("channel_id", ""))].append(m)

        logger.info(f"🧹 [背景撿漏] 發現 {len(pending)} 則未提煉訊息，橫跨 {len(by_channel)} 個頻道")
        for cid, msgs in by_channel.items():
            await self.extract_dialogue(msgs, cid)
        return len(pending)

    def start_sweeper(self, interval_seconds: float = EXTRACTION_SWEEP_INTERVAL_SECONDS) -> None:
        """啟動低頻背景撿漏任務（間隔見 config.yaml 的 extraction_sweep_interval_seconds）"""
        if self._sweeping:
            return
        self._sweeping = True
        self._sweep_task = asyncio.create_task(self._sweep_loop(interval_seconds))
        logger.info(f"🧹 [背景撿漏] 服務已啟動（每 {int(interval_seconds)} 秒檢查一次）。")

    def stop_sweeper(self) -> None:
        """停止背景撿漏任務"""
        self._sweeping = False
        if self._sweep_task and not self._sweep_task.done():
            self._sweep_task.cancel()

    async def _sweep_loop(self, interval_seconds: float) -> None:
        while self._sweeping:
            try:
                await asyncio.sleep(interval_seconds)
                await self.sweep_unextracted()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"🧹 [背景撿漏] 執行時發生異常: {e}", exc_info=True)
