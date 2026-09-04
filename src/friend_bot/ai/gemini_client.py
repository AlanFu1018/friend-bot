import json
import re
from typing import List, Optional, Dict, Any
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
from .prompts import build_system_instruction, build_facts_dedup_prompt, build_receipt_extraction_prompt
from src.friend_bot.ai.tools.web_search_tool import perform_web_search
from src.friend_bot.core.emotion import EmotionReplacer

logger = get_logger("gemini")

class GeminiClient:
    """封裝 Google GenAI SDK 的非同步客戶端，支援文字生成、多模態圖片分析、Web Search 與情緒標籤動態替換"""

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
        自動渲染 [emotion:xxx] 標籤為生動不重複的日系 2ch 顏文字。
        """
        if not self.api_key:
            return "（目前未設定 GEMINI_API_KEY，請在 .env 中填入金鑰～）"

        sys_inst = system_instruction or build_system_instruction()
        temp = temperature if temperature is not None else GEMINI_TEMPERATURE
        max_tok = max_tokens if max_tokens is not None else GEMINI_MAX_OUTPUT_TOKENS
        tools = self._get_tools() if enable_tools else None

        # 構建基礎 GenerateContentConfig
        config_kwargs = {
            "system_instruction": sys_inst,
            "temperature": temp,
            "max_output_tokens": max_tok,
            "tools": tools
        }

        freq_pen = frequency_penalty if frequency_penalty is not None else GEMINI_FREQUENCY_PENALTY
        pres_pen = presence_penalty if presence_penalty is not None else GEMINI_PRESENCE_PENALTY
        if freq_pen and freq_pen != 0.0:
            config_kwargs["frequency_penalty"] = freq_pen
        if pres_pen and pres_pen != 0.0:
            config_kwargs["presence_penalty"] = pres_pen

        config = types.GenerateContentConfig(**config_kwargs)

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

        async def _execute_generate(curr_config: types.GenerateContentConfig) -> str:
            if tools:
                chat = self.client.aio.chats.create(model=self.model, config=curr_config)
                response = await chat.send_message(contents if len(contents) > 1 else prompt)

                loop_count = 0
                while response.function_calls and loop_count < 3:
                    loop_count += 1
                    for call in response.function_calls:
                        if call.name == "search_web":
                            query = call.args.get("query", "")
                            logger.info(f"🔍 [Gemini Tool Call] 模型要求聯網搜尋: 「{query}」")
                            search_data = await perform_web_search(query, top_k=SEARCH_TOP_K)
                            tool_res_part = types.Part.from_function_response(
                                name=call.name,
                                response={"result": search_data}
                            )
                            response = await chat.send_message(tool_res_part)

                return response.text.strip() if response and response.text else "（思考中斷了，請再說一次看看～）"
            else:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=curr_config
                )
                return response.text.strip() if response and response.text else "（思考中斷了，請再說一次看看～）"

        try:
            logger.debug(f"向模型 [{self.model}] 發送生成請求 (溫度: {temp}, 聯網工具: {bool(tools)})...")
            raw_response = await _execute_generate(config)
            return EmotionReplacer.replace_emotion_tags(raw_response)
        except Exception as e:
            err_msg = str(e)
            # 若為 Penalty 不支援之 400 錯誤，立即自動移除 penalty 重試，確保對話不中斷！
            if "Penalty is not enabled for this model" in err_msg or "penalty" in err_msg.lower():
                logger.warning(f"⚠️ 模型 [{self.model}] 不支援 Penalty 參數，自動移除 Penalty 並重試請求...")
                safe_config = types.GenerateContentConfig(
                    system_instruction=sys_inst,
                    temperature=temp,
                    max_output_tokens=max_tok,
                    tools=tools
                )
                try:
                    raw_response = await _execute_generate(safe_config)
                    return EmotionReplacer.replace_emotion_tags(raw_response)
                except Exception as retry_err:
                    logger.error(f"Gemini 重試回應失敗: {retry_err}", exc_info=True)
                    return "（剛才走神了，能再跟我說一次嗎？）"

            logger.error(f"Gemini 生成回應失敗: {e}", exc_info=True)
            return "（剛才走神了，能再跟我說一次嗎？）"

    async def extract_receipt_items(self, image_bytes: bytes, mime_type: str) -> List[Dict[str, Any]]:
        """
        多模態辨識收據圖片中的品項與金額，回傳 [{"name": str, "price": float}, ...]。

        刻意不透過 generate_response()：那條路徑是聊天導向的（emotion 標籤替換、工具呼叫迴圈），
        對「取得乾淨 JSON」是額外負擔且較不穩定。改用 Gemini 結構化輸出
        (response_mime_type="application/json" + response_schema) 直接約束輸出格式，
        比 facts_similar_check() 那種手動拆 ```json fence 的方式更可靠——金額辨識錯誤直接影響
        使用者會送出的欠款金額，值得多花一點成本換穩定性。

        辨識失敗、圖片非收據、或解析不出任何合法品項時一律回傳空列表，交由呼叫端顯示對應提示，
        不拋出例外中斷 /kurisu-money 指令流程。
        """
        if not self.api_key:
            return []

        schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "items": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "name": types.Schema(type=types.Type.STRING),
                            "price": types.Schema(type=types.Type.NUMBER),
                        },
                        required=["name", "price"],
                    ),
                )
            },
            required=["items"],
        )
        config = types.GenerateContentConfig(
            system_instruction=build_receipt_extraction_prompt(),
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=schema,
        )

        try:
            part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            response = await self.client.aio.models.generate_content(
                model=self.model, contents=[part], config=config
            )
            data = json.loads(response.text) if response and response.text else {}
            raw_items = data.get("items", [])
        except Exception as e:
            logger.error(f"收據品項辨識失敗: {e}", exc_info=True)
            return []

        items: List[Dict[str, Any]] = []
        for raw in raw_items:
            try:
                name = str(raw.get("name", "")).strip()
                price = float(raw.get("price"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not name or price <= 0:
                continue
            items.append({"name": name, "price": price})
        return items

    async def facts_similar_check(self, cluster: List[str]) -> Dict[str, Any]:
        """
        判斷一群因 embedding 相似度而被歸為候選群組的事實原句彼此關係：
        "duplicate"（同一件事不同講法）／"conflict"（同主題但語意矛盾）／"none"（不相關）。

        呼叫失敗或輸出格式錯誤一律視為 "none"（整群保留不動）——沿用
        `MemoryManager.should_remove_fact()` 一貫的保守原則：寧可漏判也不誤刪。
        """
        fallback = {"relation": "none", "keep": ""}
        if not self.api_key or len(cluster) < 2:
            return fallback

        prompt = build_facts_dedup_prompt(cluster)

        try:
            raw_result = await self.generate_response(
                prompt=prompt,
                system_instruction="你是一個嚴謹的語意比對器，只依照指示輸出乾淨的 JSON，不得輸出任何無關文字。",
                temperature=0.1,
                max_tokens=256,
                enable_tools=False
            )
            cleaned = (raw_result or "").strip()
            if "```" in cleaned:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
                if match:
                    cleaned = match.group(1).strip()

            data = json.loads(cleaned)
            relation = str(data.get("relation", "none")).strip().lower()
            if relation not in ("duplicate", "conflict", "none"):
                relation = "none"
            keep = str(data.get("keep", "")).strip()
            return {"relation": relation, "keep": keep}
        except Exception as e:
            logger.warning(f"事實相似性判斷失敗，整群保留不動: {e}")
            return fallback
