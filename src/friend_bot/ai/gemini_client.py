from typing import List, Optional
from google import genai
from google.genai import types

from src.friend_bot.core.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    GEMINI_FREQUENCY_PENALTY,
    GEMINI_PRESENCE_PENALTY,
    GEMINI_MAX_OUTPUT_TOKENS,
    ENABLE_WEB_SEARCH,
    SEARCH_TOP_K,
)
from src.friend_bot.core.logger import get_logger
from .prompts import build_system_instruction
from src.friend_bot.ai.tools.web_search_tool import perform_web_search

logger = get_logger("gemini")

class GeminiClient:
    """封裝 Google GenAI SDK 的非同步客戶端，支援文字生成、多模態圖片分析與 Web Search Tool Calling"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = model or GEMINI_MODEL
        if not self.api_key:
            logger.warning("未設定 GEMINI_API_KEY，AI 對話功能將暫時無法使用。")
        self.client = genai.Client(api_key=self.api_key)

    def _get_tools(self) -> Optional[List[types.Tool]]:
        """建立 Tool 定義（包含 DuckDuckGo + Jina AI Reader 網路搜尋）"""
        if not ENABLE_WEB_SEARCH:
            return None

        search_func = types.FunctionDeclaration(
            name="search_web",
            description="當用戶詢問最新即時新聞、天氣、特定日期事件、新科技動態或需要聯網查證最新資料時呼叫此工具。",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="要搜尋的關鍵字或查詢語句"
                    )
                },
                required=["query"]
            )
        )
        return [types.Tool(function_declarations=[search_func])]

    async def generate_response(
        self,
        prompt: str,
        images: Optional[List[bytes]] = None,
        image_mime_types: Optional[List[str]] = None,
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        enable_tools: bool = True
    ) -> str:
        """
        發送對話請求至 Gemini 模型並取得回覆文字。
        支援 Tool Calling：若模型判斷需聯網，會暫停並要求搜尋，此處自動執行 DuckDuckGo + Jina AI Reader 後回傳結果給模型生成最終回應。
        """
        if not self.api_key:
            return "（目前未設定 GEMINI_API_KEY，請在 .env 中填入金鑰～）"

        sys_inst = system_instruction or build_system_instruction()
        temp = temperature if temperature is not None else GEMINI_TEMPERATURE
        max_tok = max_tokens if max_tokens is not None else GEMINI_MAX_OUTPUT_TOKENS
        freq_pen = frequency_penalty if frequency_penalty is not None else GEMINI_FREQUENCY_PENALTY
        pres_pen = presence_penalty if presence_penalty is not None else GEMINI_PRESENCE_PENALTY
        tools = self._get_tools() if enable_tools else None

        config = types.GenerateContentConfig(
            system_instruction=sys_inst,
            temperature=temp,
            max_output_tokens=max_tok,
            frequency_penalty=freq_pen,
            presence_penalty=pres_pen,
            tools=tools
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
            logger.debug(f"向模型 [{self.model}] 發送生成請求 (溫度: {temp}, 頻率懲罰: {freq_pen}, 聯網工具: {bool(tools)})...")

            # 若啟用了 tools，使用 aio.chats 進行多輪 Tool Calling 對話循環
            if tools:
                chat = self.client.aio.chats.create(model=self.model, config=config)
                # 發送初始對話內容（若有圖片以 contents 發送，否則以 prompt 發送）
                response = await chat.send_message(contents if len(contents) > 1 else prompt)

                # Tool Calling 循環處理（最多 3 輪以防無限循環）
                loop_count = 0
                while response.function_calls and loop_count < 3:
                    loop_count += 1
                    for call in response.function_calls:
                        if call.name == "search_web":
                            query = call.args.get("query", "")
                            logger.info(f"🔍 [Gemini Tool Call] 模型要求聯網搜尋: 「{query}」")
                            
                            # 執行 DuckDuckGo + Jina AI Reader
                            search_data = await perform_web_search(query, top_k=SEARCH_TOP_K)
                            
                            # 將搜尋結果作為 function_response 回傳給 Gemini
                            tool_res_part = types.Part.from_function_response(
                                name=call.name,
                                response={"result": search_data}
                            )
                            response = await chat.send_message(tool_res_part)

                return response.text.strip() if response and response.text else "（思考中斷了，請再說一次看看～）"

            else:
                # 一般直接生成
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config
                )
                return response.text.strip() if response and response.text else "（思考中斷了，請再說一次看看～）"

        except Exception as e:
            logger.error(f"Gemini 生成回應失敗: {e}", exc_info=True)
            return "（剛才走神了，能再跟我說一次嗎？）"
