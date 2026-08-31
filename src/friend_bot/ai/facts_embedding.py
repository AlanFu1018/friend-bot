import asyncio
from typing import List, Optional
from google import genai

from src.friend_bot.core.config import (
    GEMINI_API_KEY,
    FACTS_EMBEDDING_MODEL,
    FACTS_EMBEDDING_BATCH_SIZE,
    FACTS_EMBEDDING_BATCH_DELAY_SECONDS,
)
from src.friend_bot.core.logger import get_logger

logger = get_logger("facts_embedding")


class FactsEmbeddingClient:
    """封裝 Gemini Embedding API，將事實文字轉為向量，供語意去重分群（見 MemoryManager.group_facts）使用"""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        self.model = model or FACTS_EMBEDDING_MODEL
        self.client = genai.Client(api_key=self.api_key)

    async def embed_texts(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        批次計算多筆事實文字的 embedding，回傳順序與輸入一致。

        Gemini 的 BatchEmbedContents API 單次最多接受 `FACTS_EMBEDDING_BATCH_SIZE`
        （預設 100）筆請求，超過會整批被 400 INVALID_ARGUMENT 拒絕，所以這裡切成多個
        子批次依序呼叫，中間刻意間隔 `FACTS_EMBEDDING_BATCH_DELAY_SECONDS` 秒避免觸發
        速率限制。單一子批次失敗只影響該子批次（回傳 None），不拖累其他子批次；
        缺 embedding 的事實會在下次去重批次自然重試（沿用
        `MemoryExtractor.sweep_unextracted()` 的「失敗就留到下次」精神），不會因為
        embedding 暫時算不出來而中斷提煉或阻塞事實寫入。
        """
        if not texts:
            return []
        if not self.api_key:
            return [None] * len(texts)

        results: List[Optional[List[float]]] = []
        chunk_size = max(1, FACTS_EMBEDDING_BATCH_SIZE)
        for start in range(0, len(texts), chunk_size):
            if start > 0 and FACTS_EMBEDDING_BATCH_DELAY_SECONDS > 0:
                await asyncio.sleep(FACTS_EMBEDDING_BATCH_DELAY_SECONDS)
            chunk = texts[start:start + chunk_size]
            results.extend(await self._embed_chunk(chunk))

        return results

    async def _embed_chunk(self, chunk: List[str]) -> List[Optional[List[float]]]:
        """呼叫一次 BatchEmbedContents API（筆數已保證不超過上限），回傳與輸入等長的結果"""
        try:
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=chunk
            )
        except Exception as e:
            logger.warning(f"事實 embedding 子批次計算失敗（{len(chunk)} 筆），將於下次批次重試: {e}")
            return [None] * len(chunk)

        embeddings = response.embeddings or []
        if len(embeddings) != len(chunk):
            logger.warning(
                f"事實 embedding 回傳數量（{len(embeddings)}）與輸入數量（{len(chunk)}）不符，捨棄本子批次結果"
            )
            return [None] * len(chunk)

        return [e.values for e in embeddings]

    async def embed_text(self, text: str) -> Optional[List[float]]:
        """計算單一事實文字的 embedding"""
        results = await self.embed_texts([text])
        return results[0] if results else None
