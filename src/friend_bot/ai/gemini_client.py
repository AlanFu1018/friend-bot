from typing import List, Optional
from google import genai
from google.genai import types

from src.friend_bot.core.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    GEMINI_MAX_OUTPUT_TOKENS,
)
from src.friend_bot.core.logger import get_logger
from .prompts import build_system_instruction

logger = get_logger("gemini")

class GeminiClient:
    """封裝 Google GenAI SDK 的非同步客戶端，支援文字生成與多模態圖片分析"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = model or GEMINI_MODEL
        if not self.api_key:
            logger.warning("未設定 GEMINI_API_KEY，AI 對話功能將暫時無法使用。")
        self.client = genai.Client(api_key=self.api_key)

    async def generate_response(
        self,
        prompt: str,
        images: Optional[List[bytes]] = None,
        image_mime_types: Optional[List[str]] = None,
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        發送對話請求至 Gemini 模型並取得回覆文字
        """
        if not self.api_key:
            return "（目前未設定 GEMINI_API_KEY，請在 .env 中填入金鑰～）"

        sys_inst = system_instruction or build_system_instruction()
        temp = temperature if temperature is not None else GEMINI_TEMPERATURE
        max_tok = max_tokens if max_tokens is not None else GEMINI_MAX_OUTPUT_TOKENS

        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            temperature=temp,
            max_output_tokens=max_tok
        )

        contents = []

        # 處理多模態圖片
        if images:
            mime_types = image_mime_types or ["image/jpeg"] * len(images)
            for img_bytes, mime_type in zip(images, mime_types):
                try:
                    part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
                    contents.append(part)
                except Exception as e:
                    logger.error(f"多模態圖片轉換失敗: {e}")

        # 加入文字 Prompt
        contents.append(prompt)

        try:
            logger.debug(f"向模型 [{self.model}] 發送生成請求 (溫度: {temp})...")
            # 使用非同步 API 生成
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )
            return response.text.strip() if response and response.text else "（思考中斷了，請再說一次看看～）"
        except Exception as e:
            logger.error(f"Gemini API 生成失敗: {e}", exc_info=True)
            return f"（遇到了一點小故障：{e}）"
