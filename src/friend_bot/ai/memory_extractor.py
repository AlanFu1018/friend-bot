import json
import re
from typing import List, Optional
from src.friend_bot.memory.memory_manager import MemoryManager
from src.friend_bot.core.logger import get_logger
from .prompts import build_extraction_prompt
from .gemini_client import GeminiClient
from src.friend_bot.core.config import ENABLE_AUTO_MEMORY_EXTRACTION

logger = get_logger("extractor")

class MemoryExtractor:
    """非同步背景記憶提取器：從用戶發言中分析並累積長期個人特徵"""

    def __init__(self, gemini_client: Optional[GeminiClient] = None):
        self.ai = gemini_client or GeminiClient()

    async def extract_and_update(
        self,
        user_id: str,
        user_name: str,
        recent_messages: List[str]
    ) -> None:
        """背景任務：分析用戶發言並更新 DB 中的畫像"""
        if not ENABLE_AUTO_MEMORY_EXTRACTION or not recent_messages:
            return

        try:
            # 1. 取得當前已記錄的個人畫像
            profile = await MemoryManager.get_user_profile(user_id)
            current_facts = profile.get("facts", []) if profile else []
            current_notes = profile.get("interaction_notes", "") if profile else ""

            # 2. 建構提煉 Prompt
            prompt = build_extraction_prompt(
                user_name=user_name,
                current_facts=current_facts,
                current_notes=current_notes,
                recent_user_messages=recent_messages
            )

            # 3. 呼叫 Gemini 進行結構化萃取（溫度設低以求精準）
            raw_result = await self.ai.generate_response(
                prompt=prompt,
                system_instruction="你是一個嚴謹的資料分析器，請以乾淨的 JSON 格式輸出提取結果，禁止任何無關廢話。",
                temperature=0.2,
                max_tokens=1024
            )

            # 4. 解析 JSON
            cleaned_json_str = raw_result.strip()
            if "```" in cleaned_json_str:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned_json_str)
                if match:
                    cleaned_json_str = match.group(1).strip()

            parsed = json.loads(cleaned_json_str)
            facts = parsed.get("facts", current_facts)
            notes = parsed.get("interaction_notes", current_notes)

            # 若萃取出有效內容，寫回資料庫
            if isinstance(facts, list):
                clean_facts = list(dict.fromkeys([str(f).strip() for f in facts if str(f).strip()]))
                if clean_facts != current_facts or notes != current_notes:
                    await MemoryManager.update_user_profile(
                        user_id=user_id,
                        user_name=user_name,
                        facts=clean_facts,
                        interaction_notes=notes
                    )
                    logger.info(f"🧠 [畫像更新] 用戶 [{user_name}] 新記憶事實: {clean_facts}")

        except Exception as e:
            # 背景任務錯誤不影響機器人主要聊天流程
            logger.debug(f"背景記憶萃取略過: {e}")
